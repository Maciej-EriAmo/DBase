"""Testy v7.8: auto-flush, utrwalony indeks, Proca COLD."""

import json
import os
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from cynober_auto_flush import (
    AutoFlushWorker,
    default_auto_flush_config,
    load_auto_flush_config,
)
from cynober_worlds import (
    _meta_path,
    _proca_dir,
    export_query_indexes,
    reset_world_registry_for_tests,
    restore_query_indexes,
)
from karmazyn_atom import T_WARM
from karmazyn_proca import ProcaCoordinate
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


class TestAutoFlushConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = default_auto_flush_config()
        self.assertEqual(cfg["interval_sec"], 60)

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"CYNOBER_AUTO_FLUSH_SEC": "30"}):
            cfg = load_auto_flush_config()
            self.assertEqual(cfg["interval_sec"], 30)

    def test_zero_disables(self):
        with mock.patch.dict(os.environ, {"CYNOBER_AUTO_FLUSH_SEC": "0"}):
            w = AutoFlushWorker(reset_world_registry_for_tests(), 0)
            self.assertFalse(w.enabled)


class TestFlushAllDirty(unittest.TestCase):
    def test_flush_all_dirty_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = reset_world_registry_for_tests(tmp)
            world = reg.create("flush_me")
            api = world.runtime.engine.api
            b = world.runtime.store.bubble_new(label="Item")
            world.runtime.store.set_root(b)
            api._bubble_index["Item"] = b
            api._update_index("Item", "name", "alpha", add=True)
            reg.mark_dirty("flush_me")
            flushed = reg.flush_all_dirty()
            self.assertEqual(len(flushed), 1)
            self.assertTrue(_meta_path(Path(tmp), "flush_me").is_file())


class TestQueryIndexPersistence(unittest.TestCase):
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
        cls._tmp.cleanup()

    def test_meta_contains_query_indexes(self):
        world = f"idx_{time.time_ns()}"
        tag = f"Npc_{time.time_ns()}"
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        c.query(f'UTWÓRZ ŚWIAT "{world}"')
        c.query(f'UTRWAL "{tag}"')
        c.query(f'WSTRZYKNIJ "name" = "hero" DO "{tag}"')
        c.query("ZAPISZ ŚWIAT")
        meta_path = _meta_path(Path(self._tmp.name), world)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        qi = meta.get("query_indexes", {})
        self.assertIn("inv_index", qi)
        self.assertIn("name", qi["inv_index"])

    def test_find_after_reload_uses_restored_index(self):
        world = f"reload_{time.time_ns()}"
        tag = f"Hero_{time.time_ns()}"
        c1 = CynoberRpcClient(port=self.port)
        c1.connect()
        c1.query(f'UTWÓRZ ŚWIAT "{world}"')
        c1.query(f'UTRWAL "{tag}"')
        c1.query(f'WSTRZYKNIJ "role" = "mage" DO "{tag}"')
        c1.query("ZAPISZ ŚWIAT")
        c1.close()

        reset_world_registry_for_tests(self._tmp.name)
        c2 = CynoberRpcClient(port=self.port)
        c2.connect()
        self.addCleanup(c2.close)
        c2.query(f'WYBIERZ ŚWIAT "{world}"')
        find = c2.query('ZNAJDŹ GDZIE "role" = "mage"')
        self.assertEqual(find["results"][0]["matches"], [tag])


class TestProcaColdPersistence(unittest.TestCase):
    def test_cold_payload_roundtrip(self):
        import karmazyn_kernel as kernel
        from cynober_lambda_bridge import KarminLambdaBridge
        from cynober_worlds import load_runtime_from_kafd, save_runtime_to_kafd

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            proca = _proca_dir(base, "lab")
            bridge = KarminLambdaBridge(kernel.Store(thermal=True))
            store = bridge.store
            big = b"x" * 256 + b"payload_unique_" + os.urandom(32)
            atom_a = store.atom_new(S="document", E="cold_a", value="a")
            atom_a.T = T_WARM - 5.0
            atom_a.metadata["data"] = big
            atom_b = store.atom_new(S="document", E="cold_b", value="b")
            atom_b.T = T_WARM - 5.0
            atom_b.metadata["data"] = big
            b = store.bubble_new(label="ColdItem")
            b.bindings["a"] = atom_a.id
            b.bindings["b"] = atom_b.id
            store.set_root(b)
            bridge.engine.api._bubble_index["ColdItem"] = b

            kafd = base / "lab.kafd"
            save_runtime_to_kafd(bridge, kafd, proca_dir=proca, proca_cold=True)
            self.assertGreaterEqual(len(list(proca.glob("*.pfld"))), 1)

            bridge2 = KarminLambdaBridge(kernel.Store(thermal=True))
            load_runtime_from_kafd(bridge2, kafd, proca_dir=proca, lazy=False)
            for aid in (atom_a.id, atom_b.id):
                loaded = bridge2.store.get_atom(aid)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.metadata.get("data"), big)
                self.assertFalse(
                    ProcaCoordinate.is_proca_json(loaded.metadata.get("data", b""))
                )