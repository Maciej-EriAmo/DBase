"""Regresje audytu 2026-09-29: rola, auth, kopie, media, id NativeStore."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cynober_media_rpc import _media_perm
from cynober_ops import WorldBackupManager
from cynober_world_auth import ROLE_ADMIN, ROLE_WRITER, WorldAuthStore


class _Facade:
    def __init__(self, auth, user, world):
        self._auth = auth
        self._auth_user = user
        self.world_name = world


class TestRoleFloor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.auth = WorldAuthStore(self._tmp.name)
        self.auth.write_config_for_tests(
            users={"admin": "a", "power": "p", "local": "l"},
            acl={
                "*": {"admin": "admin", "power": "writer"},
                "notes": {"admin": "reader", "power": "reader", "local": "reader"},
            },
            enabled=True,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_world_entry_does_not_lower_global_role(self):
        self.assertEqual(self.auth.role_for("admin", "notes"), ROLE_ADMIN)
        self.assertTrue(self.auth.has_min_role("admin", "notes", ROLE_ADMIN))
        self.assertEqual(self.auth.role_for("power", "notes"), ROLE_WRITER)
        self.assertEqual(self.auth.role_for("local", "notes"), "reader")
        self.assertFalse(self.auth.has_min_role("local", "notes", ROLE_WRITER))

    def test_star_grant_means_every_world(self):
        self.assertIsNone(self.auth.worlds_for("admin"))
        self.assertIsNone(self.auth.worlds_for("power"))
        self.assertEqual(self.auth.worlds_for("local"), {"notes"})
        self.assertTrue(self.auth.has_min_role("power", "other", "reader"))

    def test_media_follows_effective_role(self):
        local = _Facade(self.auth, "local", "notes")
        power = _Facade(self.auth, "power", "notes")
        denied = _media_perm(local, write=True)
        self.assertIsNotNone(denied)
        self.assertEqual(denied[0]["status"], "error")
        self.assertIsNone(_media_perm(power, write=True))
        self.assertIsNone(_media_perm(_Facade(self.auth, "admin", "notes"), write=True))
        self.assertTrue(self.auth.has_min_role("power", "notes", ROLE_WRITER))
        self.assertFalse(self.auth.has_min_role("local", "notes", ROLE_WRITER))


class TestCorruptAuth(unittest.TestCase):
    def test_broken_json_closes_login(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "auth.json").write_text("{", encoding="utf-8")
        auth = WorldAuthStore(base)
        self.assertTrue(auth.enabled)
        self.assertIsNotNone(auth.load_error)
        self.assertFalse(auth.verify_login("admin", "secret"))
        self.assertIsNone(auth.role_for("admin", "notes"))

    def test_missing_file_leaves_auth_off(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        auth = WorldAuthStore(tmp.name)
        self.assertFalse(auth.enabled)
        self.assertIsNone(auth.load_error)
        self.assertTrue(auth.verify_login("anyone", "x"))


class _Reg:
    def __init__(self, base: Path):
        self.base_dir = base
        self._worlds = {}


class TestBackupId(unittest.TestCase):
    def test_restore_rejects_path_escape(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mgr = WorldBackupManager(_Reg(Path(tmp.name)))
        for bid in ("../..", "..\\..", "C:/Windows", r"C:\Windows", "", ".."):
            with self.assertRaises(ValueError, msg=bid):
                mgr.restore("notes", bid)

    def test_plain_id_stays_in_backup_tree(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mgr = WorldBackupManager(_Reg(Path(tmp.name)))
        bid = "20260929T120000Z"
        (mgr._root / "notes" / bid).mkdir(parents=True)
        with self.assertRaises(ValueError) as ctx:
            mgr.restore("notes", bid)
        self.assertIn("kafd", str(ctx.exception).lower())


class TestNativePublicId(unittest.TestCase):
    def test_numeric_string_is_not_core_aid(self):
        try:
            from native.karmazyn_substrate_native import NativeStore
        except Exception as exc:
            self.skipTest(f"native store: {exc}")
        store = NativeStore(thermal=False)
        self.addCleanup(store.close)
        first = store.atom_new("var", "first", value=1)
        second = store.atom_new("var", "second", value=2)
        self.assertIsNone(store.get_atom("1"))
        self.assertIsNone(store.get_atom(str(second._aid)))
        self.assertFalse(store.has_atom("0"))
        self.assertFalse(store.delete_atom("1"))
        self.assertEqual(store.get_atom(second.id).E, "second")
        self.assertEqual(store.get_atom(first.id).E, "first")
        store.create_atom("7", "var", "named", value=3)
        self.assertEqual(store.get_atom("7").E, "named")
        self.assertEqual(store.get_atom(second.id).E, "second")


if __name__ == "__main__":
    unittest.main()
