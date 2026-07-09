"""Testy oficjalnego klienta cynober_client.py (v7.6)."""

import time
import unittest

from cynober_client import CynoberClient, connect
from tests.test_server_rpc import TestServerHarness


class TestCynoberClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    def test_connect_and_health(self):
        with CynoberClient(port=self.port) as c:
            row = c.query_line("ZDROWIE")
            self.assertEqual(row["action"], "HEALTH")
            self.assertEqual(row["data"]["server_version"], "7.8")

    def test_context_manager(self):
        c = CynoberClient(port=self.port)
        with c:
            self.assertIsNotNone(c.crypto_mode)
        self.assertIsNone(c.sock)

    def test_connect_helper(self):
        c = connect(host="127.0.0.1", port=self.port)
        try:
            m = c.query_line("METRYKI SERWERA")
            self.assertEqual(m["action"], "SERVER_METRICS")
        finally:
            c.close()