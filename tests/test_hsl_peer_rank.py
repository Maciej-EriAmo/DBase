"""Faza 6 — ranking peerów przez R; rozdział od materiału kryptograficznego."""

from __future__ import annotations

import unittest

from karmazyn_hsl import (
    HSLLocalProbe,
    HSLPeerCandidate,
    connect_plan,
    derive_frame_key,
    hybrid_link_seed,
    link_commit,
    peer_resonance,
    prism_target,
    rank_peers,
    select_peer,
)


class TestHSLPeerRank(unittest.TestCase):
    def setUp(self):
        self.local = HSLLocalProbe(
            label="cynober rpc karminql",
            energy=1.0,
            node_id="local",
        )
        self.peers = [
            HSLPeerCandidate(
                name="far",
                host="10.0.0.9",
                port=9009,
                label="orthogonal noise payload",
                energy=5.0,
            ),
            HSLPeerCandidate(
                name="near",
                host="10.0.0.2",
                port=9002,
                label="cynober rpc karminql mirror",
                energy=1.05,
            ),
            HSLPeerCandidate(
                name="mid",
                host="10.0.0.3",
                port=9003,
                label="cynober rpc service",
                energy=2.0,
            ),
        ]

    def test_rank_prefers_resonating_peer(self):
        ranked = rank_peers(self.local, self.peers, g=0.3)
        self.assertEqual(ranked[0][1].name, "near")
        self.assertGreater(ranked[0][0], ranked[-1][0])

    def test_select_respects_R_min(self):
        chosen = select_peer(self.local, self.peers, R_min=0.15)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.name, "near")
        none = select_peer(self.local, [self.peers[0]], R_min=0.5)
        self.assertIsNone(none)

    def test_connect_plan_pipeline_and_no_kdf_flag(self):
        plan = connect_plan(self.local, self.peers, R_min=0.15)
        self.assertFalse(plan["resonance_feeds_kdf"])
        self.assertEqual(plan["pipeline"][0], "resonance")
        self.assertEqual(plan["pipeline"][-1], "session_key")
        self.assertEqual(plan["selected"]["name"], "near")
        self.assertIn("handshake_crypto", plan["pipeline"])
        # ranking zawiera wszystkich; wybór dopiero po progu
        self.assertEqual(len(plan["ranking"]), 3)

    def test_resonance_does_not_change_crypto_material(self):
        """Inwariant bezpieczeństwa: R / etykiety peerów ∉ KDF."""
        shared = b"\xab" * 32
        ca = link_commit(b"\x01" * 32, b"nonce")
        cb = link_commit(b"\x02" * 32, b"nonce")
        epoch = 7
        s1 = prism_target(shared, ca, cb, epoch)
        # ranking „po drodze” — nie wolno mutować wejść KDF
        _ = rank_peers(self.local, self.peers)
        s2 = prism_target(shared, ca, cb, epoch)
        self.assertEqual(s1, s2)
        self.assertEqual(hybrid_link_seed(shared, None), shared)
        aad = b"aad-fixed"
        self.assertEqual(derive_frame_key(s1, aad), derive_frame_key(s2, aad))

    def test_registry_dict_shape(self):
        raw = [
            {
                "name": "alpha",
                "host": "127.0.0.1",
                "port": 7700,
                "label": "cynober rpc karminql",
                "energy": 1.0,
            },
            {
                "name": "beta",
                "host": "127.0.0.1",
                "port": 7701,
                "label": "other",
                "energy": 9.0,
            },
        ]
        chosen = select_peer(
            {"label": "cynober rpc karminql", "energy": 1.0},
            raw,
            R_min=0.1,
        )
        self.assertEqual(chosen.name, "alpha")
        self.assertGreater(
            peer_resonance(self.local, raw[0]),
            peer_resonance(self.local, raw[1]),
        )


if __name__ == "__main__":
    unittest.main()
