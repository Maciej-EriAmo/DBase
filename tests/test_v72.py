"""Testy Cynober Server v7.2: auth na trwałych światach."""

import os
import tempfile
import time
import unittest

from cynober_world_auth import reset_auth_store_for_tests
from cynober_worlds import reset_world_registry_for_tests
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


def _setup_auth(base_dir: str) -> None:
    auth = reset_auth_store_for_tests(base_dir)
    auth.write_config_for_tests(
        users={
            "admin": "admin-secret",
            "writer": "w-secret",
            "reader": "r-secret",
        },
        acl={
            "*": {"admin": "admin"},
            "secure": {
                "admin": "admin",
                "writer": "writer",
                "reader": "reader",
            },
        },
    )
    auth.reload()


class TestWorldAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp.name
        reset_world_registry_for_tests()
        _setup_auth(cls._tmp.name)
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()
        time.sleep(0.1)
        os.environ.pop("CYNOBER_WORLDS_DIR", None)
        try:
            cls._tmp.cleanup()
        except OSError:
            pass

    def _client(self) -> CynoberRpcClient:
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        return c

    def test_login_and_whoami(self):
        c = self._client()
        r = c.query('ZALOGUJ "writer" TOKEN "w-secret"')
        self.assertEqual(r["results"][0]["action"], "LOGIN")
        who = c.query("KTO JESTEM")
        self.assertEqual(who["results"][0]["user"], "writer")
        self.assertTrue(who["results"][0]["auth_enabled"])

    def test_reader_cannot_write_in_world(self):
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query('UTWÓRZ ŚWIAT "secure"')
        c.query("ODŁĄCZ ŚWIAT")
        c.query('ZALOGUJ "reader" TOKEN "r-secret"')
        c.query('WYBIERZ ŚWIAT "secure"')
        r = c.query('UTRWAL "Blocked"')
        self.assertEqual(r["results"][0]["status"], "error")

    def test_writer_can_mutate(self):
        c = self._client()
        c.query('ZALOGUJ "writer" TOKEN "w-secret"')
        c.query('WYBIERZ ŚWIAT "secure"')
        tag = f"W_{time.time_ns()}"
        r = c.query(f'UTRWAL "{tag}"')
        self.assertEqual(r["results"][0]["status"], "ok")

    def test_unauthenticated_cannot_attach_world(self):
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query('UTWÓRZ ŚWIAT "secure"')
        c.close()

        c2 = self._client()
        r = c2.query('WYBIERZ ŚWIAT "secure"')
        self.assertEqual(r["results"][0]["status"], "error")

    def test_admin_can_grant_role(self):
        world = f"grant_{time.time_ns()}"
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query(f'UTWÓRZ ŚWIAT "{world}"')
        c.query('NADAJ "writer" ROLĘ "writer" W ŚWIECIE "' + world + '"')
        acl = c.query(f'LISTA UPRAWNIEŃ ŚWIATA "{world}"')
        users = [g["user"] for g in acl["results"][0]["grants"]]
        self.assertIn("writer", users)

    def test_list_worlds_filtered_for_reader(self):
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query('UTWÓRZ ŚWIAT "secure"')
        c.query("WYLOGUJ")
        c.query('ZALOGUJ "reader" TOKEN "r-secret"')
        lst = c.query("LISTA ŚWIATÓW")
        names = [w["name"] for w in lst["results"][0]["worlds"]]
        self.assertIn("secure", names)

    def test_stats_include_auth(self):
        c = self._client()
        c.query('ZALOGUJ "writer" TOKEN "w-secret"')
        c.query('WYBIERZ ŚWIAT "secure"')
        data = c.query("STATYSTYKI")["results"][0]["data"]
        self.assertEqual(data.get("auth_user"), "writer")
        self.assertEqual(data.get("auth_role"), "writer")


if __name__ == "__main__":
    unittest.main()