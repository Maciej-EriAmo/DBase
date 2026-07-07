"""Testy modułu cynober_rpc (kodeki protokołu)."""

import json
import unittest

import os
from unittest.mock import patch

from cynober_rpc import (
    LEGACY_VERSION,
    PROTO_VERSION,
    apply_psk,
    build_local_caps,
    clear_replay_cache,
    decode_request,
    error_result,
    encode_response,
    hsl_enabled,
    parse_response_payload,
    select_crypto_mode,
    validate_remote_caps,
)
from karmazyn_handshake import _CryptoLayer, _compress


class TestCynoberRpcCodec(unittest.TestCase):
    def setUp(self):
        clear_replay_cache()
        self.crypto = _CryptoLayer()
        self.crypto._key = b"\x01" * 32
        self.crypto._mode = "simple"

    def test_decode_valid_request(self):
        blob = json.dumps({"query": "STATYSTYKI"}).encode("utf-8")
        enc = self.crypto.encrypt(_compress(blob))
        self.assertEqual(decode_request(enc, self.crypto), "STATYSTYKI")

    def test_decode_rejects_missing_query(self):
        enc = self.crypto.encrypt(_compress(b"{}"))
        with self.assertRaises(ValueError):
            decode_request(enc, self.crypto)

    def test_parse_response_payload_results(self):
        results, err = parse_response_payload({"results": [{"status": "ok"}]})
        self.assertIsNone(err)
        self.assertEqual(len(results), 1)

    def test_parse_response_payload_transport_error(self):
        results, err = parse_response_payload({
            "error": {"code": "INCOMPATIBLE_VERSION", "message": "zła wersja"}
        })
        self.assertEqual(results, [])
        self.assertIn("zła wersja", err)

    def test_error_result_format(self):
        row = error_result("test")[0]
        self.assertEqual(row["action"], "PROTOCOL")
        self.assertEqual(row["status"], "error")

    def test_encode_response_roundtrip(self):
        data = json.loads(encode_response([{"status": "ok", "action": "STATS"}]))
        self.assertIn("results", data)

    def test_build_local_caps_includes_hss_when_available(self):
        caps = build_local_caps()
        self.assertEqual(caps["version"], PROTO_VERSION)
        self.assertIn("hsl", caps)
        self.assertIn("hss", caps["crypto"])
        self.assertIn("ecdh", caps["crypto"])

    def test_hsl_enabled_for_12(self):
        local = build_local_caps()
        remote = {**build_local_caps(), "session_id": "other_session_id"}
        self.assertTrue(hsl_enabled(local, remote))

    def test_select_crypto_prefers_hss(self):
        local = build_local_caps()
        remote = {"version": "Cynober-Secure-1.1", "crypto": ["hss", "ecdh", "simple"]}
        self.assertEqual(select_crypto_mode(local, remote), "hss")

    def test_legacy_forces_simple(self):
        local = build_local_caps()
        remote = {"version": LEGACY_VERSION, "crypto": ["simple"]}
        self.assertEqual(select_crypto_mode(local, remote), "simple")

    def test_validate_remote_caps_rejects_duplicate_session(self):
        local = build_local_caps()
        remote = build_local_caps()
        sid = "aabbccdd11223344"
        remote["session_id"] = sid
        validate_remote_caps(local, remote)
        with self.assertRaises(RuntimeError):
            validate_remote_caps(build_local_caps(), {**remote, "session_id": sid})

    def test_validate_remote_caps_rejects_clock_skew(self):
        local = build_local_caps()
        remote = build_local_caps()
        remote["ts"] = local["ts"] - 9999
        with self.assertRaises(RuntimeError):
            validate_remote_caps(local, remote)

    @patch.dict(os.environ, {"KARM_PSK": "tajne-haslo"})
    def test_apply_psk_mixes_key(self):
        c = _CryptoLayer()
        c._key = b"\x01" * 32
        c._mode = "ecdh"
        before = c._key
        self.assertTrue(apply_psk(c))
        self.assertNotEqual(c._key, before)


if __name__ == "__main__":
    unittest.main()