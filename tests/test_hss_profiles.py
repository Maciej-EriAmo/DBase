"""Testy profili HSS (proto / standard / production)."""

import unittest

from karmazyn_hss import HSSDaemon, PROFILES, resolve_hss_profile


class TestHSSProfiles(unittest.TestCase):
    def test_resolve_defaults_proto(self):
        p = resolve_hss_profile("proto")
        self.assertEqual(p.n, 15)
        self.assertEqual(p.q, 256)

    def test_standard_n128_q3329(self):
        p = resolve_hss_profile("standard")
        self.assertEqual(p.n, 128)
        self.assertEqual(p.q, 3329)

    def test_production_n512(self):
        p = resolve_hss_profile("production")
        self.assertEqual(p.n, 512)
        self.assertEqual(p.q, 12289)
        self.assertEqual(p.q % (2 * p.n), 1)

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            resolve_hss_profile("kyber")

    def test_handshake_all_profiles(self):
        for name in PROFILES:
            with self.subTest(profile=name):
                alice = HSSDaemon(profile=PROFILES[name])
                bob = HSSDaemon(profile=PROFILES[name])
                token_a, pk_a = alice.init_session()
                _, shared_b, ack = bob.respond_handshake(pk_a)
                shared_a = alice.finalize(token_a, ack)
                self.assertEqual(shared_a, shared_b)
                self.assertEqual(len(shared_a), 32)

    def test_mismatched_profile_fails(self):
        alice = HSSDaemon(profile=PROFILES["proto"])
        bob = HSSDaemon(profile=PROFILES["standard"])
        token_a, pk_a = alice.init_session()
        with self.assertRaises(ValueError):
            bob.respond_handshake(pk_a)