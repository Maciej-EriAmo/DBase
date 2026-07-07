"""Testy wymiany kluczy HSSDaemon (Ring-LWE tunnel bootstrap)."""

import unittest

from karmazyn_hss import HSSDaemon


class TestHSSDaemonHandshake(unittest.TestCase):
    def test_init_respond_finalize_same_key(self):
        alice = HSSDaemon()
        bob = HSSDaemon()

        token_a, pk_a = alice.init_session()
        _token_b, shared_b, ack = bob.respond_handshake(pk_a)
        shared_a = alice.finalize(token_a, ack)

        self.assertEqual(shared_a, shared_b)
        self.assertEqual(len(shared_a), 32)

    def test_finalize_rejects_bad_token(self):
        hss = HSSDaemon()
        with self.assertRaises(ValueError):
            hss.finalize("nieistniejacy", b"\x00" * 32)


if __name__ == "__main__":
    unittest.main()