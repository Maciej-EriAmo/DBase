"""Testy jądra KarmazynOS (Store, atomy, termodynamika)."""

import unittest

import karmazyn_kernel as kernel


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = kernel.Store(thermal=True)

    def test_atom_new_and_stats(self):
        self.store.atom_new(S="test", E="hello", value="hello")
        stats = self.store.stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["hot"], 1)
        self.assertEqual(stats["reaped"], 0)

    def test_bubble_bind_lookup(self):
        b = self.store.bubble_new(label="scope")
        self.store.set_root(b)
        atom = self.store.atom_new(S="key", E="42", value=42)
        b.bind("key", atom)
        found = b.lookup("key")
        self.assertIsNotNone(found)
        self.assertEqual(found.metadata["v"], 42)

    def test_settle_reaps_unreachable_cold_atoms(self):
        atom = self.store.atom_new(S="orphan", E="x", value="x")
        initial_id = atom.id
        for _ in range(80):
            self.store.tick()
        self.assertIsNone(self.store.get_atom(initial_id))
        self.assertGreater(self.store.reaped, 0)

    def test_reachable_cold_atom_survives(self):
        b = self.store.bubble_new(label="root")
        self.store.set_root(b)
        atom = self.store.atom_new(S="kept", E="y", value="y")
        b.bind("kept", atom)
        atom.T = 0.5
        atom._update_state()
        for _ in range(5):
            self.store.tick()
        self.assertIsNotNone(self.store.get_atom(atom.id))

    def test_kernel_info(self):
        info = kernel.kernel_info()
        self.assertEqual(info["version"], "1.0.0")
        self.assertIn("engine_native", info["surfaces"])


if __name__ == "__main__":
    unittest.main()