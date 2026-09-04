"""E2E: cache tuneli peer + GOSSIP SYNC Z \"@\" / AUTO (peer_server)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cynober_ops import reset_server_metrics_for_tests
from cynober_replicate import (
    PeerRegistry,
    _peer_cache_key,
    _peer_client,
    _peer_session_cache,
    reset_peer_registry_for_tests,
    reset_peer_sessions,
    resolve_peer,
)
from cynober_worlds import reset_world_registry_for_tests
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestPeerTunnelE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_a = tempfile.TemporaryDirectory()
        cls._tmp_b = tempfile.TemporaryDirectory()
        cls._peer_port = _free_port()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp_a.name
        reset_world_registry_for_tests(cls._tmp_a.name)
        reset_peer_registry_for_tests(Path(cls._tmp_a.name))
        reset_server_metrics_for_tests()
        reset_peer_sessions()
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        cls._peer_proc = subprocess.Popen(
            [sys.executable, "-m", "tests.peer_server", str(cls._peer_port), cls._tmp_b.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 10
        ready = False
        while time.time() < deadline:
            line = cls._peer_proc.stdout.readline() if cls._peer_proc.stdout else ""
            if line.startswith("READY"):
                ready = True
                break
        if not ready:
            cls._peer_proc.kill()
            raise RuntimeError("Peer server nie wystartował.")
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        reset_peer_sessions()
        cls.harness.stop()
        proc = cls._peer_proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        time.sleep(0.1)
        os.environ.pop("CYNOBER_WORLDS_DIR", None)
        for tmp in (cls._tmp_a, cls._tmp_b):
            try:
                tmp.cleanup()
            except OSError:
                pass

    def _client(self, port: int | None = None) -> CynoberRpcClient:
        c = CynoberRpcClient(port=port or self.port)
        c.connect()
        self.addCleanup(c.close)
        return c

    def test_peer_session_cache_reuses_socket(self):
        reset_peer_sessions()
        peers = PeerRegistry(Path(self._tmp_a.name))
        peers.add(
            "peer-b",
            "127.0.0.1",
            self._peer_port,
            label="cynober rpc karminql",
            energy=1.0,
        )
        peer = peers.get("peer-b")
        c1 = _peer_client(peer, reuse=True)
        c1.query("ZDROWIE")
        fd1 = c1._sock.fileno()
        c2 = _peer_client(peer, reuse=True)
        self.assertIs(c1, c2)
        self.assertEqual(c2._sock.fileno(), fd1)
        key = _peer_cache_key(peer)
        self.assertIn(key, _peer_session_cache)
        c2.query("STATYSTYKI")
        self.assertEqual(c2._sock.fileno(), fd1)
        reset_peer_sessions()

    def test_sync_world_and_auto_peer_alias(self):
        """Trwały świat na peer-b → SYNC ŚWIAT; resolve \"@\" po label/energy."""
        world = "phi_net"
        tag = f"PhiAuto_{time.time_ns()}"
        cb = self._client(self._peer_port)
        cb.query(f'UTWÓRZ ŚWIAT "{world}"')
        cb.query(f'UTRWAL "{tag}"')
        cb.query(f'WSTRZYKNIJ "Tag" = "rezonans" DO "{tag}"')
        cb.query("ZAPISZ ŚWIAT")
        cb.close()

        time.sleep(0.05)  # peer modified_at starszy niż lokalny create — unikaj push pustki
        ca = self._client()
        ca.query(
            f'DODAJ WĘZEŁ "peer-b" HOST "127.0.0.1" PORT {self._peer_port} '
            f'ETYKIETA "cynober rpc karminql" ENERGIA 1.0'
        )
        pull = ca.query(f'PULL ŚWIAT "{world}" Z "peer-b"')
        self.assertEqual(pull["results"][0]["status"], "ok", msg=pull)
        ca.query(f'WYBIERZ ŚWIAT "{world}"')
        find = ca.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag}"')
        self.assertEqual(find["results"][0]["matches"], [tag])

        with patch.dict(
            os.environ,
            {"KARM_PEER_LABEL": "cynober rpc karminql", "KARM_PEER_ENERGY": "1.0"},
            clear=False,
        ):
            peers = PeerRegistry(Path(self._tmp_a.name))
            chosen = resolve_peer(peers, "@")
            self.assertEqual(chosen["name"], "peer-b")
            sync_auto = ca.query(f'SYNC ŚWIAT "{world}" Z "@"')
        self.assertEqual(sync_auto["results"][0]["status"], "ok", msg=sync_auto)


if __name__ == "__main__":
    unittest.main()
