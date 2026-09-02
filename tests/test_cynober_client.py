"""Testy oficjalnego klienta cynober_client.py (v7.7)."""

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
            from cynober_ops import SERVER_VERSION

            self.assertEqual(row["data"]["server_version"], SERVER_VERSION)
            self.assertEqual(row["data"].get("l0_carrier"), "tcp")
            self.assertTrue(row["data"].get("kpc"))

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

    def test_persistent_session_many_queries(self):
        """Stałe połączenie: wiele query na jednym TCP (bez reconnect)."""
        c = CynoberClient(port=self.port)
        c.connect()
        try:
            sock0 = c.sock
            fd0 = sock0.fileno()
            for _ in range(5):
                row = c.query_line("ZDROWIE")
                self.assertEqual(row["action"], "HEALTH")
                self.assertIs(c.sock, sock0)
                self.assertEqual(c.sock.fileno(), fd0)
            self.assertTrue(c.session_info()["connected"])
        finally:
            c.close()
            self.assertIsNone(c.sock)