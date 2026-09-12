"""Testy v8.0: shardy per region grafu i replikacja manifest-first (faza 3)."""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge
from cynober_replicate import (
    export_manifest_payload,
    export_shard_payload,
    export_world_payload,
    import_shard_payload,
    import_world_payload,
    reset_peer_registry_for_tests,
)
from cynober_world_shards import (
    atom_shard_paths,
    compute_bubble_regions,
    load_shard_index,
    save_sharded_runtime,
    shard_index_path,
    sharding_enabled,
)
from cynober_worlds import (
    World,
    WorldRuntime,
    _kafd_path,
    _proca_dir,
    load_runtime_from_kafd,
    reset_world_registry_for_tests,
    unfold_runtime,
)
from karmazyn_atom import T_HOT, T_WARM
from karmazyn_store import FOLDED_META_KEY
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestShardRegions(unittest.TestCase):
    def test_two_connected_components(self):
        bridge = KarminLambdaBridge(kernel.Store(thermal=True))
        store = bridge.store
        api = bridge.engine.api

        a = store.bubble_new(label="TownA")
        b = store.bubble_new(label="TownB")
        c = store.bubble_new(label="IsleC")
        a.bindings["rel:road:TownB"] = "x"
        api._bubble_index["TownA"] = a
        api._bubble_index["TownB"] = b
        api._bubble_index["IsleC"] = c

        regions = compute_bubble_regions(api)
        self.assertEqual(len(regions), 2)
        comps = sorted([sorted(r) for r in regions])
        self.assertEqual(comps[0], ["IsleC"])
        self.assertEqual(sorted(comps[1]), ["TownA", "TownB"])


class TestShardedPersist(unittest.TestCase):
    def setUp(self):
        os.environ["CYNOBER_SHARDED"] = "1"
        os.environ["CYNOBER_LAZY_LOAD"] = "1"

    def tearDown(self):
        os.environ.pop("CYNOBER_SHARDED", None)

    def test_cold_payload_in_shard_not_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bridge = KarminLambdaBridge(kernel.Store(thermal=True))
            store = bridge.store
            api = bridge.engine.api

            hot = store.atom_new(S="document", E="hot", value="h")
            hot.T = T_HOT + 5
            hot.metadata["data"] = b"hot-body"
            cold = store.atom_new(S="document", E="cold", value="c")
            cold.T = T_WARM - 5
            cold.metadata["data"] = b"cold-shard-" + b"x" * 400

            town = store.bubble_new(label="Town")
            town.bindings["hot"] = hot.id
            town.bindings["cold"] = cold.id
            store.set_root(town)
            api._bubble_index["Town"] = town

            kafd = base / "w.kafd"
            stats = save_sharded_runtime(
                bridge, kafd, base, "w", proca_dir=_proca_dir(base, "w")
            )
            self.assertGreaterEqual(stats["manifest_atoms"], 2)
            self.assertTrue(shard_index_path(base, "w").is_file())

            idx = load_shard_index(base, "w")
            self.assertTrue(idx.get("sharded"))
            self.assertGreaterEqual(len(idx.get("regions") or []), 1)

            bridge2 = KarminLambdaBridge(kernel.Store(thermal=True))
            shard_paths = atom_shard_paths(base, "w")
            _, folded = load_runtime_from_kafd(
                bridge2, kafd, lazy=True, shard_paths=shard_paths
            )
            self.assertIn(cold.id, folded)
            self.assertNotIn(hot.id, folded)

            rt = WorldRuntime(bridge2, kafd_path=kafd, shard_index=shard_paths)
            rt.folded_atoms = folded
            info = unfold_runtime(rt, ["Town"], radius=0)
            self.assertEqual(info["unfolded_atoms"], 1)
            atom = bridge2.store.get_atom(cold.id)
            self.assertEqual(atom.metadata.get("data"), cold.metadata["data"])
            self.assertNotIn(FOLDED_META_KEY, atom.metadata)


class TestManifestFirstReplication(unittest.TestCase):
    def setUp(self):
        os.environ["CYNOBER_SHARDED"] = "1"

    def tearDown(self):
        os.environ.pop("CYNOBER_SHARDED", None)

    def test_export_manifest_and_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = reset_world_registry_for_tests(tmp)
            rt = reg._create_runtime()
            reg._worlds["alpha"] = World(name="alpha", runtime=rt, refs=0, dirty=True)
            with rt.lock:
                rt.engine.execute('UTRWAL "Node"')
                rt.engine.execute('WSTRZYKNIJ "k" = 1 DO "Node"')
            reg._persist(reg._worlds["alpha"])

            manifest = export_manifest_payload(reg, "alpha")
            self.assertIn("kafd_b64", manifest)
            self.assertTrue(manifest.get("sharded"))
            self.assertIn("shard_index", manifest)

            regions = manifest.get("shard_regions") or []
            if regions:
                rid = regions[0]["id"]
                shard = export_shard_payload(reg, "alpha", rid)
                self.assertIn("shard_b64", shard)

            full = export_world_payload(reg, "alpha")
            if full.get("sharded"):
                self.assertIn("shards", full)

    def test_import_manifest_then_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = reset_world_registry_for_tests(tmp)
            rt = reg._create_runtime()
            reg._worlds["beta"] = World(name="beta", runtime=rt, refs=0, dirty=True)
            with rt.lock:
                rt.engine.execute('UTRWAL "X"')
            reg._persist(reg._worlds["beta"])

            manifest = export_manifest_payload(reg, "beta")
            reg2 = reset_world_registry_for_tests(tmp + "_dst")
            import_world_payload(
                reg2,
                "beta",
                manifest["kafd_b64"],
                manifest.get("meta"),
                shard_index=manifest.get("shard_index"),
            )
            self.assertTrue(_kafd_path(reg2.base_dir, "beta").is_file())

            for entry in manifest.get("shard_regions") or []:
                rid = entry.get("id")
                if not rid or not entry.get("bytes"):
                    continue
                shard = export_shard_payload(reg, "beta", rid)
                import_shard_payload(reg2, "beta", rid, shard["shard_b64"])
                shard_path = reg2.base_dir / "shards" / "beta" / f"{rid}.kafd"
                self.assertTrue(shard_path.is_file())


class TestShardedReplicationE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_a = tempfile.TemporaryDirectory()
        cls._tmp_b = tempfile.TemporaryDirectory()
        cls._peer_port = _free_port()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp_a.name
        os.environ["CYNOBER_SHARDED"] = "1"
        reset_world_registry_for_tests(cls._tmp_a.name)
        reset_peer_registry_for_tests(Path(cls._tmp_a.name))
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        cls._peer_proc = subprocess.Popen(
            [sys.executable, "-m", "tests.peer_server", str(cls._peer_port), cls._tmp_b.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 10
        ready = False
        while time.time() < deadline:
            line = cls._peer_proc.stdout.readline() if cls._peer_proc.stdout else ""
            if line.startswith("READY"):
                ready = True
                break
        if not ready:
            cls._peer_proc.kill()
            raise RuntimeError("Peer server nie wystartował.")
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()
        proc = cls._peer_proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        time.sleep(0.1)
        os.environ.pop("CYNOBER_WORLDS_DIR", None)
        os.environ.pop("CYNOBER_SHARDED", None)
        for tmp in (cls._tmp_a, cls._tmp_b):
            try:
                tmp.cleanup()
            except OSError:
                pass

    def _client(self, port: int | None = None) -> CynoberRpcClient:
        c = CynoberRpcClient(port=port or self.port)
        c.connect()
        self.addCleanup(c.close)
        return c

    def test_manifest_export_rpc(self):
        world = f"shard_{time.time_ns()}"
        c = self._client()
        c.query(f'UTWÓRZ ŚWIAT "{world}"')
        c.query('UTRWAL "Npc"')
        c.query("ZAPISZ ŚWIAT")
        exp = c.query(f'EKSPORT MANIFEST ŚWIATA "{world}"')
        row = exp["results"][0]
        self.assertEqual(row["action"], "EXPORT_MANIFEST")
        self.assertIn("kafd_b64", row)

    def test_pull_manifest_first_with_shards(self):
        world = "shard_team"
        tag = f"ShardNpc_{time.time_ns()}"
        ca = self._client()
        ca.query(f'UTWÓRZ ŚWIAT "{world}"')
        ca.query(f'UTRWAL "{tag}"')
        ca.query(f'DODAJ WĘZEŁ "peer-b" HOST "127.0.0.1" PORT {self._peer_port}')
        push = ca.query(f'PUSH ŚWIAT "{world}" DO "peer-b"')
        self.assertEqual(push["results"][0]["action"], "PUSH_WORLD")

        cb = self._client(self._peer_port)
        cb.query(f'WYBIERZ ŚWIAT "{world}"')
        find = cb.query(f'ZNAJDŹ GDZIE "BĄBEL" = "{tag}"')
        self.assertEqual(find["results"][0]["matches"], [tag])


class TestShardingEnv(unittest.TestCase):
    def test_default_enabled(self):
        os.environ.pop("CYNOBER_SHARDED", None)
        self.assertTrue(sharding_enabled())
        os.environ["CYNOBER_SHARDED"] = "0"
        self.assertFalse(sharding_enabled())