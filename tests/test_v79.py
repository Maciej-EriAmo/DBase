"""Testy v7.9: lazy load manifestu i ROZWIJ (faza 2)."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge
from cynober_worlds import (
    _meta_path,
    load_runtime_from_kafd,
    reset_world_registry_for_tests,
    save_runtime_to_kafd,
    unfold_runtime,
    _proca_dir,
)
from karmazyn_atom import T_HOT, T_WARM
from karmazyn_store import FOLDED_META_KEY
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


class TestLazyManifestLoad(unittest.TestCase):
    def test_cold_atom_folded_on_lazy_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = KarminLambdaBridge(kernel.Store(thermal=True))
            store = bridge.store
            hot = store.atom_new(S="document", E="hot", value="h")
            hot.T = T_HOT + 5
            hot.metadata["data"] = b"hot-payload"
            cold = store.atom_new(S="document", E="cold", value="c")
            cold.T = T_WARM - 5
            cold.metadata["data"] = b"cold-payload-" + b"x" * 200
            b = store.bubble_new(label="Chest")
            b.bindings["hot"] = hot.id
            b.bindings["cold"] = cold.id
            store.set_root(b)
            bridge.engine.api._bubble_index["Chest"] = b

            kafd = base / "w.kafd"
            save_runtime_to_kafd(bridge, kafd, proca_dir=_proca_dir(base, "w"))

            bridge2 = KarminLambdaBridge(kernel.Store(thermal=True))
            _, folded = load_runtime_from_kafd(
                bridge2, kafd, proca_dir=_proca_dir(base, "w"), lazy=True
            )
            self.assertIn(cold.id, folded)
            self.assertNotIn(hot.id, folded)
            cold_loaded = bridge2.store.get_atom(cold.id)
            self.assertTrue(cold_loaded.metadata.get(FOLDED_META_KEY))
            self.assertNotIn("data", cold_loaded.metadata)

    def test_unfold_restores_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = KarminLambdaBridge(kernel.Store(thermal=True))
            store = bridge.store
            cold = store.atom_new(S="document", E="cold", value="c")
            cold.T = T_WARM - 5
            payload = b"secret-" + b"z" * 300
            cold.metadata["data"] = payload
            b = store.bubble_new(label="Vault")
            b.bindings["loot"] = cold.id
            store.set_root(b)
            bridge.engine.api._bubble_index["Vault"] = b

            kafd = base / "w.kafd"
            save_runtime_to_kafd(bridge, kafd)

            bridge2 = KarminLambdaBridge(kernel.Store(thermal=True))
            from cynober_worlds import WorldRuntime

            rt = WorldRuntime(bridge2)
            rt.kafd_path = kafd
            _, folded = load_runtime_from_kafd(bridge2, kafd, lazy=True)
            rt.folded_atoms = folded

            info = unfold_runtime(rt, ["Vault"], radius=0)
            self.assertEqual(info["unfolded_atoms"], 1)
            atom = bridge2.store.get_atom(cold.id)
            self.assertEqual(atom.metadata.get("data"), payload)
            self.assertNotIn(FOLDED_META_KEY, atom.metadata)


class TestLazyUnfoldRpc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp.name
        os.environ["CYNOBER_LAZY_LOAD"] = "1"
        reset_world_registry_for_tests()
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()
        time.sleep(0.1)
        os.environ.pop("CYNOBER_WORLDS_DIR", None)
        os.environ.pop("CYNOBER_LAZY_LOAD", None)
        cls._tmp.cleanup()

    def test_wybierz_cel_unfolds(self):
        world = f"lazy_{time.time_ns()}"
        c1 = CynoberRpcClient(port=self.port)
        c1.connect()
        c1.query(f'UTWÓRZ ŚWIAT "{world}"')
        tag = f"ColdNpc_{time.time_ns()}"
        c1.query(f'UTRWAL "{tag}"')
        c1.query(f'WSTRZYKNIJ "note" = "folded_test" DO "{tag}"')
        c1.query("ZAPISZ ŚWIAT")
        c1.close()

        reset_world_registry_for_tests(self._tmp.name)
        c2 = CynoberRpcClient(port=self.port)
        c2.connect()
        self.addCleanup(c2.close)
        attach = c2.query(f'WYBIERZ ŚWIAT "{world}" CEL "{tag}" PROMIEŃ 0')
        self.assertEqual(attach["results"][0]["action"], "ATTACH_WORLD")
        self.assertGreaterEqual(attach["results"][0].get("unfolded_atoms", 0), 0)
        show = c2.query(f'POKAŻ "{tag}"')
        self.assertEqual(show["results"][0]["status"], "ok")

    def test_rozwij_command(self):
        world = f"roz_{time.time_ns()}"
        tag = f"Item_{time.time_ns()}"
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        c.query(f'UTWÓRZ ŚWIAT "{world}"')
        c.query(f'UTRWAL "{tag}"')
        c.query(f'WSTRZYKNIJ "k" = 1 DO "{tag}"')
        c.query("ZAPISZ ŚWIAT")
        c.query(f'WYBIERZ ŚWIAT "{world}"')
        unfold = c.query(f'ROZWIJ "{tag}" PROMIEŃ 1')
        self.assertEqual(unfold["results"][0]["action"], "UNFOLD")
        meta = json.loads(_meta_path(Path(self._tmp.name), world).read_text(encoding="utf-8"))
        self.assertIn("query_indexes", meta)