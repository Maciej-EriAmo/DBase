"""Testy game_store.py — adapter pamięci gry (tryb lokalny + RPC)."""

import os
import tempfile
import time
import unittest

from cynober_worlds import reset_world_registry_for_tests
from game_store import connect_local, connect_rpc
from tests.test_server_rpc import TestServerHarness


class TestGameStoreLocal(unittest.TestCase):
    def setUp(self):
        self.store = connect_local()

    def tearDown(self):
        self.store.close()

    def test_seed_and_npcs(self):
        self.store.seed_demo_world()
        npcs = self.store.find_npcs()
        self.assertIn("Gandalf", npcs)
        players = self.store.find_players()
        self.assertIn("Aldric", players)

    def test_json_stats_projection(self):
        self.store.seed_demo_world()
        rows = self.store.npc_stats_table()
        gandalf = next(r for r in rows if r["BĄBEL"] == "Gandalf")
        self.assertEqual(gandalf["HP"], 80)
        self.assertEqual(gandalf["Mana"], 200)

    def test_quest_graph(self):
        self.store.seed_demo_world()
        self.assertEqual(self.store.quest_givers("Quest_Smok"), ["Gandalf"])

    def test_search_memory(self):
        self.store.seed_demo_world()
        hits = self.store.search_memory("smok")
        self.assertIn("Gandalf", hits)
        self.assertIn("Quest_Smok", hits)
        self.assertEqual(self.store.search_memory("mędrzec"), ["Gandalf"])

    def test_search_resonance_short_tag(self):
        self.store.seed_demo_world()
        hits = self.store.search_resonance("smok")
        # Klucz="smok" na Gandalfie; Tytuł/Pamięć też rezonują (powierzchnia, nie hash).
        self.assertIn("Gandalf", hits)

    def test_explain_plan(self):
        self.store.seed_demo_world()
        plan = self.store.explain_find("Rola", "NPC")
        self.assertEqual(plan.get("plan", {}).get("strategy"), "index_lookup")


class TestGameStoreRpc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp.name
        reset_world_registry_for_tests()
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()
        time.sleep(0.1)
        os.environ.pop("CYNOBER_WORLDS_DIR", None)
        try:
            cls._tmp.cleanup()
        except OSError:
            pass

    def test_rpc_session_isolated_world(self):
        store = connect_rpc(port=self.port)
        try:
            store.seed_demo_world()
            self.assertEqual(store.find_npcs(), ["Gandalf"])
            stats = store.stats()
            self.assertTrue(stats.get("session_isolated"))
        finally:
            store.close()

    def test_persistent_world_survives_reconnect(self):
        world = f"GsWorld_{time.time_ns()}"
        tag = f"GsTag_{time.time_ns()}"
        s1 = connect_rpc(port=self.port, world=world, create_world=True)
        try:
            s1.run_line(f'UTRWAL "{tag}"')
        finally:
            s1.close()

        s2 = connect_rpc(port=self.port, world=world)
        try:
            row = s2.run_line(f'POKAŻ "{tag}"', strict=False)
            self.assertEqual(row.get("status"), "ok")
        finally:
            s2.close()

    def test_other_client_does_not_see_world(self):
        tag = f"GsRpc_{time.time_ns()}"
        s1 = connect_rpc(port=self.port)
        try:
            s1.run_line(f'UTRWAL "{tag}"')
            s2 = connect_rpc(port=self.port)
            try:
                row = s2.run_line(f'POKAŻ "{tag}"', strict=False)
                self.assertEqual(row.get("status"), "error")
            finally:
                s2.close()
        finally:
            s1.close()


if __name__ == "__main__":
    unittest.main()