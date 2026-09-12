"""DBase = standard KarmazynOs: most Lorentza, SEARCH→R, wybór peera bez KDF."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("KARMAZYN_SUBSTRATE", "python")
os.environ["KARMAZYN_MAZUR"] = "1"


class TestMazurStore(unittest.TestCase):
    def test_open_mazur_store_has_bridge(self):
        from mazur_crystal import LorentzBridge, open_mazur_store

        s = open_mazur_store(thermal=True)
        self.assertIsInstance(s, LorentzBridge)
        self.assertTrue(s.stats().get("lorentz_bridge"))
        self.assertIsNotNone(s.mrc)

    def test_world_runtime_uses_bridge(self):
        from cynober_worlds import create_mazur_runtime

        rt = create_mazur_runtime()
        st = rt.bridge.store.stats()
        self.assertTrue(st.get("lorentz_bridge"))


class TestSearchResonance(unittest.TestCase):
    def test_search_uses_lorentz(self):
        from mazur_crystal import Tracer, open_mazur_store
        from cynober_query_engine import KarminEngine

        store = open_mazur_store(thermal=True)
        # minimal bubble index via engine API
        eng = KarminEngine(store)
        store.create_atom("a1", "row", "system active context memory", T=50.0, tracer=Tracer(1.0))
        # ręczne podpięcie indeksu jak po INSERT
        aid = "a1"
        atom = store.get_atom(aid)
        self.assertIsNotNone(atom)
        # resonance_R bezpośrednio
        hits = store.resonance_R("system active", k=5)
        self.assertTrue(hits)
        # search_resonance mapuje id→bubbles; bez indeksu zwróci []
        found = eng.api.search_resonance("system active")
        self.assertIsInstance(found, list)

    def test_wstrzyknij_szukaj_returns_bubble(self):
        """E2E: WSTRZYKNIJ → indeks → SZUKAJ zwraca nazwę bąbla (Lorentz)."""
        from cynober_worlds import create_mazur_runtime

        rt = create_mazur_runtime()
        eng = rt.bridge.engine
        eng.api.create_bubble("Sonda")
        eng.api.add_property("Sonda", "Tag", '"klucz rezonansowy mazur"')
        found = eng.api.search_resonance("klucz rezonansowy")
        self.assertIsInstance(found, list)
        self.assertIn("Sonda", found)


class TestPeerResonanceNet(unittest.TestCase):
    def test_resolve_auto_picks_near_peer(self):
        from cynober_replicate import (
            PeerRegistry,
            resolve_peer,
        )

        with tempfile.TemporaryDirectory() as td:
            peers = PeerRegistry(Path(td))
            peers.add("far", "10.0.0.9", 9009, label="orthogonal noise", energy=8.0)
            peers.add(
                "twin",
                "10.0.0.2",
                9002,
                label="cynober rpc karminql",
                energy=1.0,
            )
            peers.add("mid", "10.0.0.3", 9003, label="cynober rpc", energy=2.5)
            chosen = resolve_peer(
                peers,
                "@",
                local={"label": "cynober rpc karminql", "energy": 1.0},
            )
            self.assertEqual(chosen["name"], "twin")
            self.assertFalse(chosen["connect_plan"]["resonance_feeds_kdf"])
            self.assertEqual(chosen["connect_plan"]["pipeline"][0], "resonance")
            self.assertEqual(chosen["connect_plan"]["pipeline"][-1], "session_key")

    def test_named_peer_unchanged(self):
        from cynober_replicate import PeerRegistry, resolve_peer

        with tempfile.TemporaryDirectory() as td:
            peers = PeerRegistry(Path(td))
            peers.add("far", "10.0.0.9", 9009, label="noise", energy=9.0)
            p = resolve_peer(peers, "far")
            self.assertEqual(p["name"], "far")
            self.assertEqual(p["host"], "10.0.0.9")

    def test_dodaj_wezel_label_energy_grammar(self):
        from cynober_replicate import PeerRegistry, try_replicate_command

        with tempfile.TemporaryDirectory() as td:
            peers = PeerRegistry(Path(td))
            rows = try_replicate_command(
                'DODAJ WĘZEŁ "scout" HOST "10.0.0.5" PORT 9005 ETYKIETA "cynober rpc" ENERGIA 0.7',
                "DODAJ WĘZEŁ",
                registry=None,  # type: ignore[arg-type]
                peers=peers,
            )
            self.assertEqual(rows[0]["status"], "ok")
            self.assertEqual(rows[0]["label"], "cynober rpc")
            self.assertAlmostEqual(float(rows[0]["energy"]), 0.7)
            info = peers.get("scout")
            self.assertEqual(info["label"], "cynober rpc")
            self.assertAlmostEqual(float(info["energy"]), 0.7)


if __name__ == "__main__":
    unittest.main()
