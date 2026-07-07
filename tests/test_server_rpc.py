"""Testy integracyjne tunelu TCP Cynober-Secure-1.0 (klient ↔ serwer)."""

import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cynober_server import handle_client
from cynober_rpc import (
    LEGACY_VERSION,
    LEGACY_VERSION_11,
    PROTO_VERSION,
    SUPPORTED_VERSIONS,
    decrypt_rpc_response,
    encrypt_rpc_request,
)
from karmazyn_handshake import (
    _CryptoLayer,
    _compress,
    _recv_frame,
    _recv_json,
    _send_frame,
    _send_json,
)

from tests.rpc_client import CynoberRpcClient


class TestServerHarness:
    """Serwer testowy na losowym porcie 127.0.0.1."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]

    def start(self) -> int:
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.port

    def _accept_loop(self) -> None:
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, addr = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(
                target=handle_client, args=(conn, addr), daemon=True
            ).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)


class RpcTestBase(unittest.TestCase):
    def setUp(self):
        from cynober_rpc import clear_replay_cache
        clear_replay_cache()

    @classmethod
    def setUpClass(cls):
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    def _client(self) -> CynoberRpcClient:
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        return c


class TestCynoberRpcTunnel(RpcTestBase):
    def test_handshake_negotiates_strongest_mode(self):
        c = self._client()
        self.assertIn(c.crypto_mode, ("hss", "ecdh", "simple"))
        self.assertEqual(c.crypto.mode, c.crypto_mode)
        self.assertIsNotNone(c.crypto._key)

    def test_handshake_negotiates_hss_by_default(self):
        c = self._client()
        self.assertEqual(c.crypto_mode, "hss")

    def test_handshake_establishes_hsl_link(self):
        c = self._client()
        self.assertIsNotNone(c.hsl_link)
        self.assertGreater(c.hsl_link.epoch, 0)

    @patch.dict(os.environ, {"KARM_PSK": "test-psk-siatka"})
    def test_psk_tunnel_roundtrip(self):
        c = self._client()
        self.assertEqual(c.query("STATYSTYKI")["results"][0]["action"], "STATS")

    def test_legacy_client_gets_simple(self):
        c = CynoberRpcClient(port=self.port)
        c.connect(client_version=LEGACY_VERSION)
        self.addCleanup(c.close)
        self.assertEqual(c.crypto_mode, "simple")

    def test_stats_roundtrip(self):
        c = self._client()
        resp = c.query("STATYSTYKI")
        self.assertIn("results", resp)
        row = resp["results"][0]
        self.assertEqual(row["action"], "STATS")
        self.assertIn("total_atoms", row["data"])

    def test_crud_roundtrip(self):
        c = self._client()
        tag = f"RpcTest_{time.time_ns()}"
        c.query(f'UTRWAL "{tag}"')
        c.query(f'WSTRZYKNIJ "Opis" = "żółć" DO "{tag}"')
        resp = c.query(f'POKAŻ "{tag}"')
        props = resp["results"][0]["data"]["properties"]
        self.assertEqual(props["Opis"], "żółć")

    def test_syntax_error_keeps_tunnel_alive(self):
        c = self._client()
        bad = c.query("TO NIE JEST KOMENDA")
        self.assertEqual(bad["results"][0]["status"], "error")
        ok = c.query("STATYSTYKI")
        self.assertEqual(ok["results"][0]["action"], "STATS")

    def test_multiple_queries_one_session(self):
        c = self._client()
        tag = f"Multi_{time.time_ns()}"
        for i in range(5):
            if i == 0:
                r = c.query(f'UTRWAL "{tag}"')
            else:
                r = c.query(f'WSTRZYKNIJ "N" = {i} DO "{tag}"')
            self.assertEqual(r["results"][-1]["status"], "ok")
        show = c.query(f'POKAŻ "{tag}"')
        self.assertEqual(show["results"][0]["data"]["properties"]["N"], 4)

    def test_two_clients_share_server_state(self):
        tag = f"Shared_{time.time_ns()}"
        c1 = CynoberRpcClient(port=self.port)
        c1.connect()
        self.addCleanup(c1.close)
        c1.query(f'UTRWAL "{tag}"')
        c1.query(f'WSTRZYKNIJ "Flaga" = PRAWDA DO "{tag}"')

        c2 = CynoberRpcClient(port=self.port)
        c2.connect()
        self.addCleanup(c2.close)
        resp = c2.query(f'POKAŻ "{tag}"')
        self.assertTrue(resp["results"][0]["data"]["properties"]["Flaga"])

    def test_large_query_payload_roundtrip(self):
        c = self._client()
        tag = f"Big_{time.time_ns()}"
        long_val = "A" * 8000
        c.query(f'UTRWAL "{tag}"')
        c.query(f'WSTRZYKNIJ "Dane" = "{long_val}" DO "{tag}"')
        resp = c.query(f'POKAŻ "{tag}"')
        self.assertEqual(
            resp["results"][0]["data"]["properties"]["Dane"], long_val
        )

    def test_tick_server_command(self):
        c = self._client()
        resp = c.query("TICK 3")
        self.assertEqual(resp["results"][0]["action"], "TICK")
        self.assertEqual(resp["results"][0]["cycles"], 3)

    def test_empty_query_returns_empty_results(self):
        c = self._client()
        resp = c.query("")
        self.assertEqual(resp.get("results"), [])

    def test_zapisz_wczytaj_over_rpc(self):
        c = self._client()
        tag = f"Persist_{time.time_ns()}"
        c.query(f'UTRWAL "{tag}"')
        c.query(f'WSTRZYKNIJ "V" = 42 DO "{tag}"')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test_rpc.kafd")
            save = c.query(f"ZAPISZ {path}")
            self.assertEqual(save["results"][0]["action"], "SAVE")
            show = c.query(f'POKAŻ "{tag}"')
            self.assertEqual(show["results"][0]["data"]["properties"]["V"], 42)

    def test_client_disconnect_without_error_frame(self):
        c = CynoberRpcClient(port=self.port)
        c.connect()
        c.sock.close()
        c.sock = None
        # Serwer powinien przeżyć — kolejny klient łączy się normalnie
        c2 = self._client()
        self.assertEqual(c2.query("STATYSTYKI")["results"][0]["action"], "STATS")


class TestCynoberProtocolFailures(RpcTestBase):
    def test_server_rejects_wrong_client_protocol_version(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", self.port))
        try:
            hs_deadline = time.monotonic() + 5.0
            _recv_json(sock, hs_deadline)
            _send_json(sock, {"version": "Cynober-OLD-0.1", "crypto": ["simple"]})
            err = _recv_json(sock, hs_deadline)
            self.assertEqual(err["error"]["code"], "INCOMPATIBLE_VERSION")
            self.assertIn(err["error"]["expected"], (list(SUPPORTED_VERSIONS), PROTO_VERSION))
        finally:
            sock.close()

    def test_malformed_rpc_frame_returns_protocol_error(self):
        c = self._client()
        bad = encrypt_rpc_request(c.crypto, b"to-nie-jest-zlib", c.hsl_link)
        _send_frame(c.sock, bad)
        enc_resp = _recv_frame(c.sock)
        raw = decrypt_rpc_response(c.crypto, enc_resp, c.hsl_link)
        import zlib, json
        payload = json.loads(zlib.decompress(raw).decode("utf-8"))
        self.assertEqual(payload["results"][0]["action"], "PROTOCOL")
        ok = c.query("STATYSTYKI")
        self.assertEqual(ok["results"][0]["action"], "STATS")

    def test_concurrent_clients_no_crash(self):
        errors = []
        barrier = threading.Barrier(4)

        def worker(tid: int):
            c = CynoberRpcClient(port=self.port)
            try:
                c.connect()
                barrier.wait(timeout=5)
                for i in range(8):
                    name = f"Conc_{tid}_{i}_{time.time_ns()}"
                    r = c.query(f'UTRWAL "{name}"')
                    if r["results"][-1]["status"] != "ok":
                        errors.append((tid, i, r))
            except Exception as e:
                errors.append((tid, str(e)))
            finally:
                c.close()

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])

    def test_client_rejects_wrong_server_protocol_version(self):
        """Serwer z inną wersją PROTO_VERSION — klient odmawia."""
        rogue = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        rogue.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rogue.bind(("127.0.0.1", 0))
        rogue.listen(1)
        port = rogue.getsockname()[1]

        def accept_one():
            conn, _ = rogue.accept()
            try:
                _send_json(conn, {"version": "FAKE-9.9", "crypto": ["simple"]})
                time.sleep(0.5)
            finally:
                conn.close()

        threading.Thread(target=accept_one, daemon=True).start()
        try:
            c = CynoberRpcClient(port=port, timeout=3.0)
            with self.assertRaises(ConnectionError):
                c.connect()
            c.close()
        finally:
            rogue.close()


class TestCryptoRoundTrip(unittest.TestCase):
    """Warstwa szyfrowania używana przez tunel — poza gniazdem."""

    def test_simple_mode_encrypt_decrypt_symmetry(self):
        layer = _CryptoLayer()
        layer._key = b"\x42" * 32
        layer._mode = "simple"
        plain = _compress(b'{"query": "STATYSTYKI"}')
        enc = layer.encrypt(plain)
        dec = layer.decrypt(enc)
        self.assertEqual(dec, plain)
        self.assertNotEqual(enc, plain)

    def test_different_nonces_per_frame(self):
        layer = _CryptoLayer()
        layer._key = b"\xab" * 32
        layer._mode = "simple"
        data = b"identyczny payload"
        enc1 = layer.encrypt(data)
        enc2 = layer.encrypt(data)
        self.assertNotEqual(enc1, enc2)


if __name__ == "__main__":
    unittest.main()