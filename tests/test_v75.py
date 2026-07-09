"""Testy v7.5 pro: QKD adapter, capability tokens, rotacja epoki, NTT."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from cynober_rpc import build_rpc_request, decode_request
from karmazyn_hsl import CAP_RPC_QUERY
from karmazyn_handshake import _CryptoLayer, _compress
from karmazyn_hsl import (
    HSLLink,
    capability_token,
    current_epoch,
    hybrid_link_seed,
    link_commit,
    prism_target,
    verify_capability,
)
from karmazyn_hss import HSSDaemon, PROFILES, hss_use_ntt, resolve_hss_profile
from karmazyn_hss_ntt import kem_pubkey_ntt, ntt_compatible, ntt_poly_mul
from karmazyn_qkd import clear_qkd_cache, load_qkd_bytes, qkd_source_label


class TestQKDAdapter(unittest.TestCase):
    def tearDown(self):
        clear_qkd_cache()

    def test_env_hex(self):
        with patch.dict(os.environ, {"KARM_QKD_SEED": "ab" * 32}, clear=True):
            clear_qkd_cache()
            self.assertEqual(len(load_qkd_bytes()), 32)
            self.assertEqual(qkd_source_label(), "env")

    def test_file_path(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"\x01" * 48)
            path = f.name
        try:
            with patch.dict(
                os.environ,
                {"KARM_QKD_PATH": path, "KARM_QKD_SEED": ""},
                clear=True,
            ):
                clear_qkd_cache()
                self.assertIsNotNone(load_qkd_bytes())
                self.assertEqual(qkd_source_label(), "file")
        finally:
            os.unlink(path)


class TestCapabilityRPC(unittest.TestCase):
    def setUp(self):
        self.crypto = _CryptoLayer()
        self.crypto._key = b"\x01" * 32
        shared = b"\x02" * 32
        ca = link_commit(b"\x03" * 32, b"n")
        cb = link_commit(b"\x04" * 32, b"n")
        epoch = current_epoch()
        st = prism_target(shared, ca, cb, epoch)
        self.link = HSLLink(
            epoch=epoch,
            s_target=st,
            local_id="a",
            remote_id="b",
            task="cynober-rpc",
            prisms=("karminql",),
            _shared_key=shared,
            _commit_local=ca,
            _commit_remote=cb,
        )

    def test_build_and_verify_cap(self):
        req = build_rpc_request("ZDROWIE", self.link)
        self.assertIn("cap", req)
        self.assertTrue(verify_capability(self.link.s_target, CAP_RPC_QUERY, req["cap"]))

    def test_decode_rejects_missing_cap(self):
        blob = _compress(b'{"query":"X"}')
        enc = self.crypto.encrypt(blob, key=self.link.request_key(), aad=self.link.aad_request())
        with self.assertRaises(ValueError):
            decode_request(enc, self.crypto, self.link)


class TestEpochRotation(unittest.TestCase):
    def test_ensure_epoch_refreshes_target(self):
        shared = b"\x05" * 32
        ca = link_commit(b"\x06" * 32, b"n")
        cb = link_commit(b"\x07" * 32, b"n")
        old_epoch = current_epoch()
        link = HSLLink(
            epoch=old_epoch,
            s_target=prism_target(shared, ca, cb, old_epoch),
            local_id="x",
            remote_id="y",
            task="cynober-rpc",
            prisms=("karminql",),
            _shared_key=shared,
            _commit_local=ca,
            _commit_remote=cb,
        )
        before = link.s_target
        with patch("karmazyn_hsl.current_epoch", return_value=old_epoch + 1):
            self.assertTrue(link.ensure_epoch())
        self.assertNotEqual(link.s_target, before)
        tok = capability_token(link.s_target, CAP_RPC_QUERY)
        self.assertTrue(verify_capability(link.s_target, CAP_RPC_QUERY, tok))


class TestHSSNTT(unittest.TestCase):
    def test_ntt_compatible_profiles(self):
        self.assertFalse(ntt_compatible(PROFILES["proto"]))
        self.assertTrue(ntt_compatible(PROFILES["standard"]))
        self.assertTrue(ntt_compatible(PROFILES["production"]))

    def test_ntt_handshake_standard(self):
        with patch.dict(os.environ, {"KARM_HSS_PROFILE": "standard"}, clear=False):
            from importlib import reload
            import karmazyn_hss as hss_mod

            reload(hss_mod)
            self.assertTrue(hss_mod.hss_use_ntt(hss_mod.PROFILES["standard"]))
            alice = hss_mod.HSSDaemon(profile=hss_mod.PROFILES["standard"])
            bob = hss_mod.HSSDaemon(profile=hss_mod.PROFILES["standard"])
            token_a, pk_a = alice.init_session()
            _, shared_b, ack = bob.respond_handshake(pk_a)
            shared_a = alice.finalize(token_a, ack)
            self.assertEqual(shared_a, shared_b)

    def test_poly_mul_matches_dim(self):
        p = PROFILES["standard"]
        sk = __import__("numpy").random.default_rng(1).integers(0, 3, size=p.n)
        pk = kem_pubkey_ntt(sk, p)
        self.assertEqual(len(pk), p.n)