"""Testy Cynober Server v7.3: metryki, zdrowie, kopie zapasowe światów."""

import os
import tempfile
import time
import unittest

from cynober_ops import SERVER_VERSION, reset_server_metrics_for_tests
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
            "ops_world": {
                "admin": "admin",
                "writer": "writer",
                "reader": "reader",
            },
        },
    )
    auth.reload()


class TestServerOps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp.name
        reset_world_registry_for_tests()
        reset_server_metrics_for_tests()
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

    def test_health(self):
        c = self._client()
        r = c.query("ZDROWIE")
        row = r["results"][0]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["action"], "HEALTH")
        self.assertEqual(row["data"]["status"], "ok")
        self.assertEqual(row["data"]["server_version"], SERVER_VERSION)
        self.assertGreater(row["data"]["uptime_sec"], 0)

    def test_server_metrics(self):
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query("ZDROWIE")
        m = c.query("METRYKI SERWERA")
        data = m["results"][0]["data"]
        self.assertEqual(m["results"][0]["action"], "SERVER_METRICS")
        self.assertEqual(data["server_version"], SERVER_VERSION)
        self.assertGreaterEqual(data["queries_total"], 2)
        self.assertIn("HEALTH", data["top_actions"])

    def test_backup_mutate_restore_e2e(self):
        world = "ops_world"
        tag_before = f"Hero_{time.time_ns()}"
        tag_after = f"Mutant_{time.time_ns()}"

        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query(f'UTWÓRZ ŚWIAT "{world}"')
        c.query(f'UTRWAL "{tag_before}"')

        backup = c.query(f'KOPIA ZAPASOWA ŚWIATA "{world}"')
        self.assertEqual(backup["results"][0]["action"], "BACKUP_WORLD")
        backup_id = backup["results"][0]["backup_id"]
        self.assertTrue(backup_id)

        c.query(f'UTRWAL "{tag_after}"')
        find_after = c.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag_after}"')
        self.assertEqual(find_after["results"][0]["matches"], [tag_after])

        restore = c.query(f'PRZYWRÓĆ ŚWIAT "{world}" Z KOPII "{backup_id}"')
        self.assertEqual(restore["results"][0]["action"], "RESTORE_WORLD")
        self.assertTrue(restore["results"][0]["restored"])

        find_before = c.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag_before}"')
        self.assertEqual(find_before["results"][0]["matches"], [tag_before])
        find_gone = c.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag_after}"')
        self.assertEqual(find_gone["results"][0]["matches"], [])

        lst = c.query(f'LISTA KOPII ŚWIATA "{world}"')
        ids = [b["backup_id"] for b in lst["results"][0]["backups"]]
        self.assertIn(backup_id, ids)

    def test_reader_cannot_backup(self):
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query('UTWÓRZ ŚWIAT "ops_world"')
        c.query("ODŁĄCZ ŚWIAT")
        c.query('ZALOGUJ "reader" TOKEN "r-secret"')
        r = c.query('KOPIA ZAPASOWA ŚWIATA "ops_world"')
        self.assertEqual(r["results"][0]["status"], "error")

    def test_writer_can_backup_reader_can_list(self):
        world = f"bkp_{time.time_ns()}"
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query(f'UTWÓRZ ŚWIAT "{world}"')
        c.query(f'NADAJ "writer" ROLĘ "writer" W ŚWIECIE "{world}"')
        c.query(f'NADAJ "reader" ROLĘ "reader" W ŚWIECIE "{world}"')
        c.query("ODŁĄCZ ŚWIAT")
        c.query('ZALOGUJ "writer" TOKEN "w-secret"')
        c.query(f'WYBIERZ ŚWIAT "{world}"')
        r = c.query(f'KOPIA ZAPASOWA ŚWIATA "{world}"')
        self.assertEqual(r["results"][0]["status"], "ok", r["results"][0].get("message"))

        c.query("WYLOGUJ")
        c.query('ZALOGUJ "reader" TOKEN "r-secret"')
        lst = c.query(f'LISTA KOPII ŚWIATA "{world}"')
        self.assertEqual(lst["results"][0]["status"], "ok")
        self.assertGreaterEqual(len(lst["results"][0]["backups"]), 1)