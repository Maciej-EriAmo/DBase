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
        self.assertEqual(info["version"], "1.1.0")
        self.assertIn("engine_native", info["surfaces"])

    def test_nested_tick_is_noop(self):
        """S15: vacuum_decay → tick() nie rekurencyjnie zawyża reaped."""
        store = kernel.Store(thermal=True)

        def boom(_atom):
            store.tick()

        store.events.on("vacuum_decay", boom)
        store.atom_new(S="orphan", E="x", value="x", T=1.0)
        store.tick()
        self.assertEqual(store.reaped, 1)
        self.assertIsNone(store.get_atom("a0"))

    def test_dual_emit_both_modes(self):
        """S12: domyślne 'both' emituje tick per atom i tick_batch."""
        ticks = []
        batches = []
        store = kernel.Store(thermal=True)  # default both
        store.events.on("tick", lambda a: ticks.append(a.id))
        store.events.on("tick_batch", lambda p: batches.append(p))
        store.atom_new(S="t", E="e", value=1)
        store.tick()
        self.assertEqual(len(ticks), 1)
        self.assertEqual(len(batches), 1)
        self.assertIn("reaped", batches[0])

    def test_batch_only_skips_per_atom_tick(self):
        ticks = []
        batches = []
        store = kernel.Store(thermal=True, tick_event_mode="batch")
        store.events.on("tick", lambda a: ticks.append(a.id))
        store.events.on("tick_batch", lambda p: batches.append(p))
        store.atom_new(S="t", E="e", value=1)
        store.tick()
        self.assertEqual(len(ticks), 0)
        self.assertEqual(len(batches), 1)

    def test_snapshot_restore_atoms(self):
        store = kernel.Store(thermal=False)
        store.create_atom("a0", "S", "E", value=1)
        snap = store.snapshot_atoms()
        temps = {aid: a.T for aid, a in snap.items()}
        store.create_atom("a1", "S", "E2", value=2)
        self.assertTrue(store.has_atom("a1"))
        store.restore_atoms(snap, temps)
        self.assertTrue(store.has_atom("a0"))
        self.assertFalse(store.has_atom("a1"))


if __name__ == "__main__":
    unittest.main()