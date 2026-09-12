"""Testy warstwy HSL (Φ², PrismMask, rezonans łącza)."""

import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

from cynober_rpc import (
    PROTO_VERSION,
    clear_replay_cache,
    decrypt_rpc_request,
    encrypt_rpc_request,
    hsl_enabled,
)
from karmazyn_hsl import (
    HSL_VERSION,
    HSLLink,
    build_aad,
    capability_token,
    current_epoch,
    derive_frame_key,
    hybrid_link_seed,
    link_commit,
    perform_hsl_link,
    prism_target,
    verify_capability,
)
from karmazyn_handshake import _CryptoLayer, _compress

from cynober_server import handle_client


class TestHSLPrimitives(unittest.TestCase):
    def test_prism_target_symmetric(self):
        shared = b"\xab" * 32
        ca = link_commit(b"\x01" * 32, b"nonce-a")
        cb = link_commit(b"\x02" * 32, b"nonce-b")
        epoch = 42
        s1 = prism_target(shared, ca, cb, epoch)
        s2 = prism_target(shared, cb, ca, epoch)
        self.assertEqual(s1, s2)

    def test_wrong_epoch_changes_prism(self):
        shared = b"\xab" * 32
        ca = link_commit(b"\x01" * 32, b"n")
        cb = link_commit(b"\x02" * 32, b"n")
        self.assertNotEqual(
            prism_target(shared, ca, cb, 1),
            prism_target(shared, ca, cb, 2),
        )

    def test_qkd_seed_changes_prism(self):
        shared = b"\xab" * 32
        ca = link_commit(b"\x01" * 32, b"n")
        cb = link_commit(b"\x02" * 32, b"n")
        epoch = 1
        without = prism_target(shared, ca, cb, epoch)
        with_qkd = prism_target(shared, ca, cb, epoch, qkd_seed=b"\x99" * 32)
        self.assertNotEqual(without, with_qkd)

    def test_hybrid_link_seed_differs_from_handshake_alone(self):
        shared = b"\xcc" * 32
        qkd = b"\xdd" * 32
        self.assertNotEqual(shared, hybrid_link_seed(shared, qkd))
        self.assertEqual(shared, hybrid_link_seed(shared, None))

    def test_capability_roundtrip(self):
        s = b"\xcd" * 32
        tok = capability_token(s, "establish")
        self.assertTrue(verify_capability(s, "establish", tok))
        self.assertFalse(verify_capability(s, "establish", "deadbeef" * 8))

    def test_frame_key_bound_to_aad(self):
        s = b"\xef" * 32
        aad1 = build_aad("node_a", "node_b", "cynober-rpc", 1, "req")
        aad2 = build_aad("node_a", "node_c", "cynober-rpc", 1, "req")
        self.assertNotEqual(derive_frame_key(s, aad1), derive_frame_key(s, aad2))


class TestHSLHandshake(unittest.TestCase):
    def setUp(self):
        clear_replay_cache()
        self.phi_a = b"\x11" * 32
        self.phi_b = b"\x22" * 32

    def _pipe(self):
        s1, s2 = socket.socketpair()
        return s1, s2

    def test_link_establishment_resonates(self):
        s_srv, s_cli = self._pipe()
        shared = b"\xaa" * 32
        local_caps = {
            "node_id": "node_srv",
            "session_id": "sess_srv_01",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        remote_caps = {
            "node_id": "node_cli",
            "session_id": "sess_cli_01",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        deadline = time.monotonic() + 5.0
        err = []

        def server():
            try:
                link = perform_hsl_link(
                    s_srv, shared, local_caps, remote_caps,
                    is_server=True, deadline=deadline, phi2=self.phi_a,
                )
                err.append(("ok", link.epoch))
            except Exception as e:
                err.append(("fail", str(e)))

        t = threading.Thread(target=server, daemon=True)
        t.start()
        link = perform_hsl_link(
            s_cli, shared, remote_caps, local_caps,
            is_server=False, deadline=deadline, phi2=self.phi_b,
        )
        t.join(timeout=3)
        self.assertEqual(err, [("ok", link.epoch)])
        self.assertIsInstance(link, HSLLink)
        s_srv.close()
        s_cli.close()

    def test_tampered_cap_breaks_resonance(self):
        """Fałszywy link_cap → brak rezonansu (jak szum dla obserwatora)."""
        s_srv, s_cli = self._pipe()
        shared = b"\xaa" * 32
        local_caps = {
            "node_id": "node_srv",
            "session_id": "s1",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        remote_caps = {
            "node_id": "node_cli",
            "session_id": "s2",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        deadline = time.monotonic() + 5.0
        from karmazyn_hsl import (
            _HSL_AAD_CAP,
            _HSL_AAD_LINK,
            _hsl_crypto_from_shared,
            _recv_hsl_msg,
            _send_hsl_msg,
            link_nonce_from_caps,
        )

        crypto_c = _hsl_crypto_from_shared(shared, mode="hss")

        def client():
            nonce = link_nonce_from_caps(remote_caps, local_caps)
            _send_hsl_msg(
                s_cli,
                {
                    "type": "hsl_link",
                    "version": HSL_VERSION,
                    "epoch": current_epoch(),
                    "node_id": "node_cli",
                    "commit": link_commit(self.phi_b, nonce).hex(),
                },
                crypto_c,
                aad=_HSL_AAD_LINK,
            )
            _recv_hsl_msg(s_cli, deadline, crypto_c, aad=_HSL_AAD_LINK)
            _send_hsl_msg(
                s_cli,
                {"type": "hsl_cap", "cap": "0" * 64},
                crypto_c,
                aad=_HSL_AAD_CAP,
            )

        t = threading.Thread(target=client, daemon=True)
        t.start()
        with self.assertRaises(RuntimeError):
            perform_hsl_link(
                s_srv, shared, local_caps, remote_caps,
                is_server=True, deadline=deadline, phi2=self.phi_a,
            )
        t.join(timeout=3)
        s_srv.close()
        s_cli.close()


class TestHSLRpcFrames(unittest.TestCase):
    def setUp(self):
        self.crypto = _CryptoLayer()
        self.crypto._key = b"\x55" * 32
        self.crypto._mode = "ecdh"
        self.link = HSLLink(
            epoch=current_epoch(),
            s_target=b"\x66" * 32,
            local_id="node_cli",
            remote_id="node_srv",
            task="cynober-rpc",
            prisms=("karminql",),
        )

    def test_encrypt_decrypt_with_hsl(self):
        blob = _compress(b'{"query":"STATYSTYKI"}')
        enc = encrypt_rpc_request(self.crypto, blob, self.link)
        dec = decrypt_rpc_request(self.crypto, enc, self.link)
        self.assertEqual(dec, blob)

    def test_wrong_link_collapses_to_noise(self):
        blob = _compress(b'{"query":"X"}')
        enc = encrypt_rpc_request(self.crypto, blob, self.link)
        wrong = HSLLink(
            epoch=self.link.epoch + 99,
            s_target=b"\x99" * 32,
            local_id=self.link.local_id,
            remote_id=self.link.remote_id,
            task=self.link.task,
            prisms=self.link.prisms,
        )
        with self.assertRaises(Exception):
            decrypt_rpc_request(self.crypto, enc, wrong)


class TestHSLIntegration(unittest.TestCase):
    def setUp(self):
        clear_replay_cache()

    def test_hsl_enabled_only_for_12(self):
        local = {"version": PROTO_VERSION, "hsl": HSL_VERSION}
        remote = {"version": PROTO_VERSION, "hsl": HSL_VERSION}
        self.assertTrue(hsl_enabled(local, remote))
        self.assertFalse(hsl_enabled(local, {"version": "Cynober-Secure-1.1"}))

    @patch.dict(os.environ, {"KARM_QKD_SEED": "bb" * 32, "KARM_PHI2": "aa" * 32})
    def test_server_client_qkd_hybrid_tunnel(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def accept():
            conn, addr = srv.accept()
            handle_client(conn, addr)

        threading.Thread(target=accept, daemon=True).start()
        from tests.rpc_client import CynoberRpcClient

        c = CynoberRpcClient(port=port)
        c.connect()
        try:
            self.assertTrue(c.hsl_link.qkd_hybrid)
            self.assertEqual(c.query("STATYSTYKI")["results"][0]["action"], "STATS")
        finally:
            c.close()
            srv.close()

    @patch.dict(os.environ, {"KARM_QKD_SEED": "bb" * 32, "KARM_PHI2": "aa" * 32})
    def test_qkd_mismatch_breaks_link(self):
        s_srv, s_cli = self._pipe()
        shared = b"\xaa" * 32
        caps_srv = {
            "node_id": "node_srv", "session_id": "s1",
            "version": PROTO_VERSION, "hsl": HSL_VERSION,
        }
        caps_cli = {
            "node_id": "node_cli", "session_id": "s2",
            "version": PROTO_VERSION, "hsl": HSL_VERSION,
        }
        deadline = time.monotonic() + 5.0

        server_err = []

        def server():
            try:
                with patch.dict(os.environ, {"KARM_QKD_SEED": "bb" * 32}):
                    perform_hsl_link(
                        s_srv, shared, caps_srv, caps_cli,
                        is_server=True, deadline=deadline, phi2=b"\x11" * 32,
                    )
            except RuntimeError as e:
                server_err.append(str(e))

        t = threading.Thread(target=server, daemon=True)
        t.start()
        with patch.dict(os.environ, {"KARM_QKD_SEED": "cc" * 32}):
            with self.assertRaises(RuntimeError) as ctx:
                perform_hsl_link(
                    s_cli, shared, caps_cli, caps_srv,
                    is_server=False, deadline=deadline, phi2=b"\x22" * 32,
                )
        self.assertIn("QKD", str(ctx.exception))
        t.join(timeout=3)
        s_srv.close()
        s_cli.close()

    def _pipe(self):
        return socket.socketpair()

    @patch.dict(os.environ, {"KARM_PHI2": "aa" * 32})
    def test_server_client_hsl_tunnel(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def accept():
            conn, addr = srv.accept()
            handle_client(conn, addr)

        threading.Thread(target=accept, daemon=True).start()
        from tests.rpc_client import CynoberRpcClient

        c = CynoberRpcClient(port=port)
        c.connect()
        try:
            self.assertIsNotNone(c.hsl_link)
            resp = c.query("STATYSTYKI")
            self.assertEqual(resp["results"][0]["action"], "STATS")
        finally:
            c.close()
            srv.close()


if __name__ == "__main__":
    unittest.main()