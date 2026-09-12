"""Testy Cynober Server v7.4: replikacja światów między węzłami."""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from cynober_ops import SERVER_VERSION, reset_server_metrics_for_tests
from cynober_replicate import (
    PeerRegistry,
    export_world_payload,
    import_world_payload,
    reset_peer_registry_for_tests,
)
from cynober_worlds import World, _kafd_path, reset_world_registry_for_tests
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestReplicationPayload(unittest.TestCase):
    def test_export_import_roundtrip(self):
        tmp = tempfile.TemporaryDirectory()
        reg = reset_world_registry_for_tests(tmp.name)
        rt = reg._create_runtime()
        reg._worlds["alpha"] = World(name="alpha", runtime=rt, refs=0, dirty=True)
        with rt.lock:
            rt.engine.execute('UTRWAL "Seed"')
        reg._persist(reg._worlds["alpha"])

        payload = export_world_payload(reg, "alpha")
        self.assertIn("kafd_b64", payload)

        reg2 = reset_world_registry_for_tests(tmp.name + "_b")
        info = import_world_payload(reg2, "alpha", payload["kafd_b64"], payload.get("meta"))
        self.assertTrue(info["imported"])
        self.assertTrue(_kafd_path(reg2.base_dir, "alpha").is_file())


class TestPeerRegistry(unittest.TestCase):
    def test_add_list_remove(self):
        tmp = tempfile.TemporaryDirectory()
        peers = PeerRegistry(Path(tmp.name))
        peers.add("node-b", "10.0.0.2", 9090, user="repl", token="secret")
        names = [p["name"] for p in peers.list_peers()]
        self.assertIn("node-b", names)
        got = peers.get("node-b")
        self.assertEqual(got["host"], "10.0.0.2")
        peers.remove("node-b")
        self.assertEqual(peers.list_peers(), [])


class TestReplicationE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_a = tempfile.TemporaryDirectory()
        cls._tmp_b = tempfile.TemporaryDirectory()
        cls._peer_port = _free_port()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp_a.name
        reset_world_registry_for_tests(cls._tmp_a.name)
        reset_peer_registry_for_tests(Path(cls._tmp_a.name))
        reset_server_metrics_for_tests()
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

    def test_list_peers_and_health_version(self):
        c = self._client()
        h = c.query("ZDROWIE")
        self.assertEqual(h["results"][0]["data"]["server_version"], SERVER_VERSION)
        c.query(f'DODAJ WĘZEŁ "peer-b" HOST "127.0.0.1" PORT {self._peer_port}')
        lst = c.query("LISTA WĘZŁÓW")
        names = [p["name"] for p in lst["results"][0]["peers"]]
        self.assertIn("peer-b", names)

    def test_push_and_pull_between_nodes(self):
        tag = f"Repl_{time.time_ns()}"
        ca = self._client()
        ca.query('UTWÓRZ ŚWIAT "team_db"')
        ca.query(f'UTRWAL "{tag}"')
        ca.query(f'DODAJ WĘZEŁ "peer-b" HOST "127.0.0.1" PORT {self._peer_port}')
        push = ca.query('PUSH ŚWIAT "team_db" DO "peer-b"')
        self.assertEqual(push["results"][0]["action"], "PUSH_WORLD")

        cb = self._client(self._peer_port)
        cb.query('WYBIERZ ŚWIAT "team_db"')
        find = cb.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag}"')
        self.assertEqual(find["results"][0]["matches"], [tag])

    def test_sync_pull_when_remote_newer(self):
        world = "sync_test"
        ca = self._client()
        ca.query(f'UTWÓRZ ŚWIAT "{world}"')
        ca.query(f'DODAJ WĘZEŁ "peer-b" HOST "127.0.0.1" PORT {self._peer_port}')
        ca.query(f'PUSH ŚWIAT "{world}" DO "peer-b"')
        time.sleep(0.02)
        tag = f"Remote_{time.time_ns()}"
        cb = self._client(self._peer_port)
        cb.query(f'WYBIERZ ŚWIAT "{world}"')
        cb.query(f'UTRWAL "{tag}"')
        cb.query('ZAPISZ ŚWIAT')
        cb.close()

        sync = ca.query(f'SYNC ŚWIAT "{world}" Z "peer-b"')
        self.assertEqual(sync["results"][0]["action"], "SYNC_WORLD")
        self.assertEqual(sync["results"][0].get("direction"), "pull")
        find = ca.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag}"')
        self.assertEqual(find["results"][0]["matches"], [tag])