"""Testy cynober_rate_limit."""

import unittest

from cynober_rate_limit import ServerRateLimiter, SessionQueryLimiter


class TestSessionQueryLimiter(unittest.TestCase):
    def test_blocks_over_limit(self):
        lim = SessionQueryLimiter(3)
        for _ in range(3):
            ok, _ = lim.allow()
            self.assertTrue(ok)
        ok, msg = lim.allow()
        self.assertFalse(ok)
        self.assertIn("RATE_LIMIT", msg)

    def test_zero_disables(self):
        lim = SessionQueryLimiter(0)
        for _ in range(100):
            self.assertTrue(lim.allow()[0])


class TestServerRateLimiter(unittest.TestCase):
    def test_per_ip_concurrent(self):
        lim = ServerRateLimiter({
            "max_concurrent_global": 0,
            "max_connections_per_ip": 2,
            "max_new_connections_per_ip_per_min": 0,
            "max_queries_per_minute": 0,
        })
        self.assertTrue(lim.acquire_connection("10.0.0.1")[0])
        self.assertTrue(lim.acquire_connection("10.0.0.1")[0])
        self.assertFalse(lim.acquire_connection("10.0.0.1")[0])
        lim.release_connection("10.0.0.1")
        self.assertTrue(lim.acquire_connection("10.0.0.1")[0])
        lim.release_connection("10.0.0.1")
        lim.release_connection("10.0.0.1")

    def test_connection_rate_per_minute(self):
        lim = ServerRateLimiter({
            "max_concurrent_global": 0,
            "max_connections_per_ip": 0,
            "max_new_connections_per_ip_per_min": 2,
            "max_queries_per_minute": 0,
        })
        self.assertTrue(lim.acquire_connection("1.2.3.4")[0])
        lim.release_connection("1.2.3.4")
        self.assertTrue(lim.acquire_connection("1.2.3.4")[0])
        lim.release_connection("1.2.3.4")
        self.assertFalse(lim.acquire_connection("1.2.3.4")[0])


if __name__ == "__main__":
    unittest.main()