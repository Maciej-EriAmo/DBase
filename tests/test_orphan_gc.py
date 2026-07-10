"""Testy GC osieroconych atomów i licznika id po wczytaniu KAFD."""

import os
import tempfile
import unittest
from pathlib import Path

import karmazyn_kernel as kernel
from cynober_worlds import (
    _meta_path,
    load_runtime_from_kafd,
    reset_world_registry_for_tests,
    save_runtime_to_kafd,
)
from cynober_lambda_bridge import KarminLambdaBridge


class TestOrphanGC(unittest.TestCase):
    def setUp(self):
        self.bridge = KarminLambdaBridge(kernel.Store(thermal=True))
        self.api = self.bridge.engine.api

    def _bubble(self, name: str):
        self.api.create_bubble(name)
        return self.api._bubble_index[name]

    def test_hist_binding_blocks_thermal_reap(self):
        """hist:* utrzymuje atom osiągalnym — tick nie sprząta bez gc_orphan_atoms."""
        b = self._bubble("Doc")
        self.api.add_property("Doc", "Plik", '"a.txt"')
        self.api.update_property("Doc", "Plik", '"b.txt"')
        hist_keys = [k for k in b.bindings if k.startswith("hist:")]
        self.assertTrue(hist_keys)
        hist_aid = b.bindings[hist_keys[0]]
        for _ in range(80):
            self.bridge.store.tick()
        self.assertIsNotNone(self.bridge.store.get_atom(hist_aid))

    def test_gc_orphan_removes_hist_atoms(self):
        b = self._bubble("Doc")
        self.api.add_property("Doc", "Plik", '"a.txt"')
        self.api.update_property("Doc", "Plik", '"b.txt"')
        hist_aids = {b.bindings[k] for k in b.bindings if k.startswith("hist:")}
        self.assertTrue(hist_aids)
        reaped = self.api.gc_orphan_atoms(keep_hist=False)
        self.assertGreater(reaped, 0)
        for aid in hist_aids:
            self.assertIsNone(self.bridge.store.get_atom(aid))

    def test_delete_bubble_reaps_atoms_without_tick(self):
        self._bubble("Anna")
        self.api.add_property("Anna", "Typ", '"Postać"')
        self.api.add_property("Anna", "Notatka", '"bohaterka"')
        typ_aid = self.api._bubble_index["Anna"].bindings["Typ"]
        self.api.delete_bubble("Anna")
        self.assertIsNone(self.bridge.store.get_atom(typ_aid))
        self.assertNotIn("Anna", self.api._bubble_index)

    def test_sync_id_counter_after_kafd_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.kafd"
            self._bubble("Doc")
            self.api.add_property("Doc", "Typ", '"Dokument"')
            self.api.add_property("Doc", "Plik", '"rozdzial.txt"')
            save_runtime_to_kafd(self.bridge, path)

            bridge2 = KarminLambdaBridge(kernel.Store(thermal=True))
            load_runtime_from_kafd(bridge2, path, query_indexes=None)
            self.assertGreaterEqual(bridge2.store._n, 2)
            bridge2.engine.api.add_property("Doc", "Opis", '"opis"')
            opis_aid = bridge2.engine.api._bubble_index["Doc"].bindings["Opis"]
            self.assertNotEqual(opis_aid, "a0")
            typ_atom = bridge2.store.get_atom(
                bridge2.engine.api._bubble_index["Doc"].bindings["Typ"]
            )
            self.assertIsNotNone(typ_atom)
            self.assertEqual(typ_atom.metadata.get("v"), "Dokument")


class TestPersistGC(unittest.TestCase):
    def test_flush_drops_hist_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = reset_world_registry_for_tests(tmp)
            world = reg.create("gc_world")
            api = world.runtime.engine.api
            api.create_bubble("Doc")
            api.add_property("Doc", "Plik", '"v1.txt"')
            api.update_property("Doc", "Plik", '"v2.txt"')
            api.update_property("Doc", "Plik", '"v3.txt"')
            before = len(list(world.runtime.store.reg.atoms()))
            reg.flush("gc_world")
            reg2 = reset_world_registry_for_tests(tmp)
            world2 = reg2.attach("gc_world")
            api2 = world2.runtime.engine.api
            atoms = list(world2.runtime.store.reg.atoms())
            plik_atoms = [a for a in atoms if a.S == "Plik"]
            self.assertEqual(len(plik_atoms), 1)
            self.assertLess(len(atoms), before)
            doc = api2._bubble_index["Doc"]
            plik = doc.bindings.get("Plik")
            atom = world2.runtime.store.get_atom(plik)
            self.assertIsNotNone(atom)
            self.assertEqual(atom.metadata.get("v"), "v3.txt")
            reg2.release("gc_world")


if __name__ == "__main__":
    unittest.main()