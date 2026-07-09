"""Testy gossip phi-space (v7.7)."""

import unittest

import karmazyn_kernel as kernel
from cynober_gossip import export_phi_payload, import_phi_payload, merge_phi_atoms, serialize_phi_atoms


class TestGossipPhi(unittest.TestCase):
    def setUp(self):
        self.store = kernel.Store(thermal=True)
        self.store.atom_new(S="Hero", E="Gandalf", T=50.0)

    def test_serialize_nonempty(self):
        atoms = serialize_phi_atoms(self.store)
        self.assertEqual(len(atoms), 1)
        self.assertEqual(atoms[0]["S"], "Hero")

    def test_export_import_roundtrip(self):
        payload = export_phi_payload(self.store)
        other = kernel.Store(thermal=True)
        stats = import_phi_payload(other, payload)
        self.assertEqual(stats["added"], 1)
        self.assertEqual(len(serialize_phi_atoms(other)), 1)

    def test_merge_higher_temp_wins(self):
        remote = [{
            "id": "a0",
            "S": "Hero",
            "E": "Gandalf",
            "T": 99.0,
            "state": "HOT",
            "age": 0,
            "_node_id": "remote",
        }]
        atom = self.store.atoms()[0]
        aid = str(getattr(atom, "id", "a0"))
        remote[0]["id"] = aid
        added, updated, skipped = merge_phi_atoms(self.store, remote)
        self.assertEqual(updated, 1)
        self.assertGreater(float(self.store.get_atom(aid).T), 50.0)