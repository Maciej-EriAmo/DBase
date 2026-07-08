"""Testy Cynober Server v7.0: izolacja sesji RPC."""

import time
import unittest

from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


class TestSessionIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    def test_parallel_sessions_do_not_leak_bubbles(self):
        tag_a = f"SessA_{time.time_ns()}"
        tag_b = f"SessB_{time.time_ns()}"

        ca = CynoberRpcClient(port=self.port)
        cb = CynoberRpcClient(port=self.port)
        ca.connect()
        cb.connect()
        self.addCleanup(ca.close)
        self.addCleanup(cb.close)

        ca.query(f'UTRWAL "{tag_a}"')
        cb.query(f'UTRWAL "{tag_b}"')

        find_a = ca.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag_a}"')
        find_b = cb.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag_b}"')
        self.assertEqual(find_a["results"][0]["matches"], [tag_a])
        self.assertEqual(find_b["results"][0]["matches"], [tag_b])

        leak_a = cb.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag_a}"')
        leak_b = ca.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag_b}"')
        self.assertEqual(leak_a["results"][0]["matches"], [])
        self.assertEqual(leak_b["results"][0]["matches"], [])

    def test_reconnect_gets_fresh_store(self):
        tag = f"Fresh_{time.time_ns()}"
        c1 = CynoberRpcClient(port=self.port)
        c1.connect()
        c1.query(f'UTRWAL "{tag}"')
        c1.close()

        c2 = CynoberRpcClient(port=self.port)
        c2.connect()
        self.addCleanup(c2.close)
        resp = c2.query(f'POKAŻ "{tag}"')
        self.assertEqual(resp["results"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()