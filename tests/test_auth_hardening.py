"""Auth hardening: scrypt KDF + lockout + legacy SHA256 upgrade."""

from __future__ import annotations

import tempfile
import time
import unittest

from cynober_world_auth import (
    _LOGIN_FAIL_MAX,
    _hash_token,
    _hash_token_sha256,
    _verify_token_hash,
    reset_auth_store_for_tests,
)


class TestScryptTokenHash(unittest.TestCase):
    def test_scrypt_roundtrip(self):
        salt = "aabb" * 8
        h = _hash_token(salt, "sekret")
        self.assertTrue(h.startswith("scrypt$"))
        ok, rehash = _verify_token_hash(salt, "sekret", h)
        self.assertTrue(ok)
        self.assertFalse(rehash)
        bad, _ = _verify_token_hash(salt, "zly", h)
        self.assertFalse(bad)

    def test_legacy_sha256_accepted_and_flags_rehash(self):
        salt = "deadbeef" * 4
        legacy = _hash_token_sha256(salt, "oldtok")
        ok, rehash = _verify_token_hash(salt, "oldtok", legacy)
        self.assertTrue(ok)
        self.assertTrue(rehash)

    def test_login_upgrades_legacy_hash(self):
        tmp = tempfile.TemporaryDirectory()
        auth = reset_auth_store_for_tests(tmp.name)
        salt = "cafebabe" * 4
        auth._data = {
            "enabled": True,
            "salt": salt,
            "users": {
                "u1": {"token_hash": _hash_token_sha256(salt, "tok1")},
            },
            "acl": {"*": {"u1": "admin"}},
        }
        auth._save()
        self.assertTrue(auth.verify_login("u1", "tok1"))
        stored = auth._data["users"]["u1"]["token_hash"]
        self.assertTrue(stored.startswith("scrypt$"))
        tmp.cleanup()


class TestLoginLockout(unittest.TestCase):
    def test_lockout_after_max_fails(self):
        tmp = tempfile.TemporaryDirectory()
        auth = reset_auth_store_for_tests(tmp.name)
        auth.write_config_for_tests(
            users={"admin": "good"},
            acl={"*": {"admin": "admin"}},
            enabled=True,
        )
        for _ in range(_LOGIN_FAIL_MAX):
            self.assertFalse(auth.verify_login("admin", "bad"))
            auth.record_login_failure("admin")
        self.assertTrue(auth.is_login_locked("admin"))
        self.assertFalse(auth.verify_login("admin", "good"))
        # odblokuj sztucznie
        auth._locked_until["admin"] = time.time() - 1
        auth.clear_login_failures("admin")
        self.assertTrue(auth.verify_login("admin", "good"))
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
