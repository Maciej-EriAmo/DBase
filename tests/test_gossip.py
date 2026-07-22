"""Testy gossip phi-space (v7.7) i SOUL BubbleVFS-lite (v8.1)."""

import unittest

import karmazyn_kernel as kernel
from cynober_gossip import (
    export_phi_payload,
    export_soul_payload,
    import_phi_payload,
    import_soul_payload,
    merge_phi_atoms,
    merge_soul,
    serialize_phi_atoms,
    serialize_soul,
    try_gossip_command,
)
from cynober_query_engine import KarminEngine


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
        # id musi być zachowane (create_atom, nie atom_new)
        src_id = self.store.atoms()[0].id
        self.assertIsNotNone(other.get_atom(src_id))

    def test_merge_preserves_remote_id(self):
        s = kernel.Store(thermal=False)
        s.atom_new(S="Local", E="x")  # a0
        remote = [{
            "id": "a99",
            "S": "Remote",
            "E": "y",
            "T": 80.0,
            "state": "HOT",
            "age": 0,
        }]
        added, updated, skipped = merge_phi_atoms(s, remote)
        self.assertEqual(added, 1)
        self.assertIsNotNone(s.get_atom("a99"))
        self.assertEqual(s.get_atom("a99").S, "Remote")

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


class TestGossipSoul(unittest.TestCase):
    def setUp(self):
        self.store = kernel.Store(thermal=False)
        self.engine = KarminEngine(self.store)
        self.api = self.engine.api
        self.api.create_bubble("Hero")
        self.api.add_property("Hero", "Name", '"Gandalf"')
        self.api.add_property("Hero", "Role", '"Wizard"')

    def test_serialize_soul_has_bubbles_and_atoms(self):
        doc = serialize_soul(self.store, api=self.api)
        self.assertEqual(doc["kind"], "soul")
        self.assertGreaterEqual(len(doc["atoms"]), 2)
        labels = {b["label"] for b in doc["bubbles"]}
        self.assertIn("Hero", labels)
        hero = next(b for b in doc["bubbles"] if b["label"] == "Hero")
        self.assertIn("Name", hero["bindings"])
        self.assertIn("Role", hero["bindings"])

    def test_soul_roundtrip_preserves_bindings(self):
        payload = export_soul_payload(self.store, api=self.api)
        other = kernel.Store(thermal=False)
        eng2 = KarminEngine(other)
        stats = import_soul_payload(other, payload, api=eng2.api)
        self.assertEqual(stats["status"], "ok")
        self.assertGreaterEqual(stats["atoms_added"], 2)
        self.assertGreaterEqual(stats["bubbles_added"], 1)
        self.assertIn("Hero", eng2.api._bubble_index)
        b = eng2.api._bubble_index["Hero"]
        name_aid = b.bindings.get("Name")
        self.assertIsNotNone(name_aid)
        atom = other.get_atom(name_aid)
        self.assertIsNotNone(atom)
        # value z meta
        self.assertEqual(atom.metadata.get("v"), "Gandalf")

    def test_soul_merge_hotter_binding_wins(self):
        # lokalnie zimniejsza wartość
        payload = export_soul_payload(self.store, api=self.api)
        other = kernel.Store(thermal=False)
        eng2 = KarminEngine(other)
        import_soul_payload(other, payload, api=eng2.api)
        b = eng2.api._bubble_index["Hero"]
        name_aid = b.bindings["Name"]
        cold = other.get_atom(name_aid)
        cold.T = 10.0

        # zdalny gorętszy ten sam id
        remote_doc = serialize_soul(self.store, api=self.api)
        for a in remote_doc["atoms"]:
            if a["id"] == name_aid:
                a["T"] = 90.0
                a["meta"] = {"v": "Mithrandir"}
        stats = merge_soul(other, remote_doc, api=eng2.api)
        self.assertGreaterEqual(stats["atoms_updated"], 1)
        self.assertEqual(other.get_atom(name_aid).metadata.get("v"), "Mithrandir")

    def test_command_export_soul(self):
        rows = try_gossip_command(
            "GOSSIP EKSPORT SOUL",
            store=self.store,
            node_id="n1",
            api=self.api,
        )
        self.assertIsNotNone(rows)
        self.assertEqual(rows[0]["action"], "GOSSIP_EXPORT_SOUL")
        self.assertIn("data", rows[0])
        self.assertGreaterEqual(rows[0]["bubble_count"], 1)


if __name__ == "__main__":
    unittest.main()
