"""Testy Cynober Server v7.1: trwałe nazwane światy."""

import os
import tempfile
import time
import unittest

from cynober_worlds import reset_world_registry_for_tests
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


class TestPersistentWorlds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp.name
        reset_world_registry_for_tests()
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

    def test_create_and_list_world(self):
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        r = c.query('UTWÓRZ ŚWIAT "rivendell"')
        self.assertEqual(r["results"][0]["action"], "ATTACH_WORLD")
        lst = c.query("LISTA ŚWIATÓW")
        names = [w["name"] for w in lst["results"][0]["worlds"]]
        self.assertIn("rivendell", names)

    def test_reconnect_sees_persisted_bubble(self):
        tag = f"Persist_{time.time_ns()}"
        c1 = CynoberRpcClient(port=self.port)
        c1.connect()
        c1.query('UTWÓRZ ŚWIAT "save_test"')
        c1.query(f'UTRWAL "{tag}"')
        c1.close()

        c2 = CynoberRpcClient(port=self.port)
        c2.connect()
        self.addCleanup(c2.close)
        c2.query('WYBIERZ ŚWIAT "save_test"')
        show = c2.query(f'POKAŻ "{tag}"')
        self.assertEqual(show["results"][0]["status"], "ok")

    def test_two_clients_share_world(self):
        world = f"shared_{time.time_ns()}"
        ca = CynoberRpcClient(port=self.port)
        cb = CynoberRpcClient(port=self.port)
        ca.connect()
        cb.connect()
        self.addCleanup(ca.close)
        self.addCleanup(cb.close)

        ca.query(f'UTWÓRZ ŚWIAT "{world}"')
        tag = f"Npc_{time.time_ns()}"
        ca.query(f'UTRWAL "{tag}"')
        cb.query(f'WYBIERZ ŚWIAT "{world}"')
        find = cb.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag}"')
        self.assertEqual(find["results"][0]["matches"], [tag])

    def test_ephemeral_still_isolated_from_world(self):
        world = f"iso_{time.time_ns()}"
        tag = f"Secret_{time.time_ns()}"
        c1 = CynoberRpcClient(port=self.port)
        c1.connect()
        c1.query(f'UTWÓRZ ŚWIAT "{world}"')
        c1.query(f'UTRWAL "{tag}"')
        c1.close()

        c2 = CynoberRpcClient(port=self.port)
        c2.connect()
        self.addCleanup(c2.close)
        leak = c2.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag}"')
        self.assertEqual(leak["results"][0]["matches"], [])

    def test_stats_reports_world(self):
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        c.query('WYBIERZ ŚWIAT "stats_world"')
        row = c.query("STATYSTYKI")["results"][0]["data"]
        self.assertEqual(row.get("world"), "stats_world")
        self.assertFalse(row.get("session_isolated"))

    def test_detach_returns_to_ephemeral(self):
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        c.query('WYBIERZ ŚWIAT "detach_me"')
        c.query("ODŁĄCZ ŚWIAT")
        row = c.query("STATYSTYKI")["results"][0]["data"]
        self.assertIsNone(row.get("world"))
        self.assertTrue(row.get("session_isolated"))

    def test_delete_world(self):
        name = f"del_{time.time_ns()}"
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        c.query(f'UTWÓRZ ŚWIAT "{name}"')
        c.query("ODŁĄCZ ŚWIAT")
        r = c.query(f'USUŃ ŚWIAT "{name}"')
        self.assertEqual(r["results"][0]["action"], "DELETE_WORLD")
        lst = c.query("LISTA ŚWIATÓW")
        names = [w["name"] for w in lst["results"][0]["worlds"]]
        self.assertNotIn(name, names)


if __name__ == "__main__":
    unittest.main()