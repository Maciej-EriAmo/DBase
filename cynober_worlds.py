#!/usr/bin/env python3
"""
cynober_worlds.py — trwałe, nazwane światy na serwerze Cynober (v7.1+)
=======================================================================
Każdy świat = współdzielony Store + KarminEngine, zapisany jako .kafd na dysku.
Wiele sesji RPC może dołączyć do tego samego świata (wspólny stan, lock na zapis).

v7.8: auto-flush dirty, utrwalony inv_index/atom_index w .meta.json,
      Proca dla payloadów COLD (katalog proca/<świat>/).
v7.9: lazy load manifestu (zwinięte nie-HOT), ROZWIJ / CEL + promień grafu.
v8.0: shardy KAFD per region grafu, replikacja manifest-first.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge
from cynober_query_engine import KarminType

WORLD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
META_INDEX_VERSION = 1

DEFAULT_WORLDS_DIR = Path.home() / ".cynober_worlds"
DEFAULT_UNFOLD_RADIUS = 2


def lazy_load_enabled() -> bool:
    raw = os.environ.get("CYNOBER_LAZY_LOAD", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def default_unfold_radius() -> int:
    raw = os.environ.get("CYNOBER_UNFOLD_RADIUS", "").strip()
    if not raw:
        return DEFAULT_UNFOLD_RADIUS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_UNFOLD_RADIUS


def worlds_dir() -> Path:
    raw = os.environ.get("CYNOBER_WORLDS_DIR", "").strip()
    path = Path(raw) if raw else DEFAULT_WORLDS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_world_name(name: str) -> str:
    name = name.strip()
    if not WORLD_NAME_RE.match(name):
        raise ValueError(
            "Nieprawidłowa nazwa świata (dozwolone: litery, cyfry, _, -, max 64 znaki)"
        )
    return name


def _meta_path(base: Path, name: str) -> Path:
    return base / f"{name}.meta.json"


def _kafd_path(base: Path, name: str) -> Path:
    return base / f"{name}.kafd"


def _proca_dir(base: Path, name: str) -> Path:
    path = base / "proca" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_query_indexes(api) -> dict[str, Any]:
    """Serializacja inv_index / atom_index do .meta.json."""
    return {
        "version": META_INDEX_VERSION,
        "inv_index": {
            key: {val: sorted(bubs) for val, bubs in vals.items()}
            for key, vals in api._inv_index.items()
        },
        "atom_index": {
            aid: sorted(bubs) for aid, bubs in api._atom_index.items()
        },
    }


def restore_query_indexes(api, data: dict[str, Any] | None) -> bool:
    """Przywróć indeksy z meta; False gdy brak danych."""
    if not data or not isinstance(data, dict):
        return False
    inv = data.get("inv_index")
    atom = data.get("atom_index")
    if not isinstance(inv, dict) or not isinstance(atom, dict):
        return False
    ns = api.active_ns
    shell = api.namespaces[ns]
    shell["inv_index"] = {
        key: {val: set(bubs) for val, bubs in vals.items()}
        for key, vals in inv.items()
        if isinstance(vals, dict)
    }
    shell["atom_index"] = {
        aid: set(bubs) for aid, bubs in atom.items()
        if isinstance(bubs, list)
    }
    return True


def rebuild_all_indexes(api) -> None:
    """Odtwarza inv_index i atom_index po wczytaniu z .kafd."""
    ns = api.active_ns
    shell = api.namespaces[ns]
    shell["inv_index"] = {}
    shell["atom_index"] = {}
    for bubble_name, bubble in api._bubble_index.items():
        for key, atom_id in bubble.bindings.items():
            if not atom_id:
                continue
            api._track_atom(bubble_name, atom_id, add=True)
            if key.startswith("hist:") or key.startswith("rel:"):
                continue
            atom = api.store.get_atom(atom_id)
            if atom is None:
                continue
            val = atom.metadata.get("v", KarminType.parse(atom.E))
            api._update_index(bubble_name, key, val, add=True)


def _proca_index_for(proca_dir: Path | str | None):
    if proca_dir is None:
        return None
    from karmazyn_proca import ProcaIndex

    idx = ProcaIndex(fields_dir=str(proca_dir))
    idx.load_sources_from_disk()
    return idx


def save_runtime_to_kafd(
    bridge: KarminLambdaBridge,
    path: Path | str,
    *,
    proca_dir: Path | str | None = None,
    proca_cold: bool = True,
) -> int:
    import karmazyn_store

    store = bridge.store
    engine = bridge.engine
    proca_index = _proca_index_for(proca_dir) if proca_cold else None
    syn_ids: List[str] = []
    try:
        for nazwa, b in engine.api._bubble_index.items():
            syn = store.atom_new(S="__bubble__", E=nazwa, value=nazwa)
            syn.metadata["bindings"] = b.bindings
            syn_ids.append(syn.id)
        kinds = list({a.S for a in store.atoms() if a.S})
        if "__bubble__" not in kinds:
            kinds.append("__bubble__")
        return karmazyn_store.save_documents(
            store,
            str(path),
            kinds=kinds,
            proca_index=proca_index,
            proca_cold_only=bool(proca_cold and proca_index is not None),
        )
    finally:
        for sid in syn_ids:
            store.delete_atom(sid)


def _finalize_kafd_load(
    bridge: KarminLambdaBridge,
    query_indexes: dict[str, Any] | None,
) -> None:
    store = bridge.store
    engine = bridge.engine
    for a in list(store.atoms()):
        if a.S != "__bubble__":
            continue
        nazwa = a.E
        if nazwa not in engine.api._bubble_index:
            b = store.bubble_new(label=nazwa)
            b.bindings = dict(a.metadata.get("bindings", {}))
            store.set_root(b)
            engine.api._bubble_index[nazwa] = b
        store.delete_atom(a.id)
    if hasattr(store, "sync_id_counter"):
        store.sync_id_counter()
    engine.api.prune_dead_bindings()
    rebuild_all_indexes(engine.api)


def load_runtime_from_kafd(
    bridge: KarminLambdaBridge,
    path: Path | str,
    *,
    proca_dir: Path | str | None = None,
    query_indexes: dict[str, Any] | None = None,
    lazy: bool = False,
    shard_paths: Optional[Dict[str, Path]] = None,
) -> tuple[int, set[str]]:
    import karmazyn_store
    from karmazyn_store import FOLDED_META_KEY, FOLD_SRC_KEY

    store = bridge.store
    proca_index = _proca_index_for(proca_dir)
    folded: set[str] = set()
    if lazy and lazy_load_enabled():
        loaded, folded = karmazyn_store.load_documents_lazy(
            store, str(path), proca_index=proca_index
        )
        if shard_paths:
            from karmazyn_atom import T_HOT

            for aid, spath in shard_paths.items():
                atom = store.get_atom(aid)
                if atom is None or atom.S == "__bubble__":
                    continue
                if float(atom.T) >= T_HOT:
                    continue
                folded.add(aid)
                atom.metadata[FOLDED_META_KEY] = True
                atom.metadata[FOLD_SRC_KEY] = str(spath)
                atom.metadata.pop("data", None)
    else:
        loaded = karmazyn_store.load_documents(store, str(path), proca_index=proca_index)
    _finalize_kafd_load(bridge, query_indexes)
    return loaded, folded


def bubble_graph_neighbors(api, bubble_name: str) -> Set[str]:
    """Sąsiedzi w grafie relacji (1-hop)."""
    neighbors: Set[str] = set()
    bubble = api._bubble_index.get(bubble_name)
    if bubble is None:
        return neighbors
    for key in bubble.bindings:
        if key.startswith("rel:"):
            parts = key.split(":", 2)
            if len(parts) >= 3:
                neighbors.add(parts[2])
    for name, other in api._bubble_index.items():
        if name == bubble_name:
            continue
        for key in other.bindings:
            if key.startswith("rel:") and key.endswith(f":{bubble_name}"):
                neighbors.add(name)
    return neighbors


def bfs_bubbles(api, seeds: List[str], radius: int) -> Set[str]:
    """Bąble w promieniu radius od seedów (graf relacji)."""
    valid_seeds = [s for s in seeds if s in api._bubble_index]
    if not valid_seeds:
        return set()
    visited: Set[str] = set(valid_seeds)
    frontier: Set[str] = set(valid_seeds)
    for _ in range(max(0, radius)):
        nxt: Set[str] = set()
        for b in frontier:
            for n in bubble_graph_neighbors(api, b):
                if n not in visited:
                    visited.add(n)
                    nxt.add(n)
        frontier = nxt
    return visited


def unfold_runtime(
    runtime: WorldRuntime,
    seeds: List[str],
    radius: int | None = None,
) -> dict[str, Any]:
    """Rozwiń payload zwiniętych atomów wokół seedów (+ promień grafu)."""
    import karmazyn_store

    if radius is None:
        radius = default_unfold_radius()
    api = runtime.engine.api
    bubbles = bfs_bubbles(api, seeds, radius)
    atom_ids: Set[str] = set()
    for bname in bubbles:
        bubble = api._bubble_index.get(bname)
        if bubble is None:
            continue
        for key, aid in bubble.bindings.items():
            if aid and not key.startswith("hist:"):
                atom_ids.add(aid)

    to_load = atom_ids & runtime.folded_atoms
    loaded = 0
    if to_load:
        proca = _proca_index_for(runtime.proca_dir)
        if runtime.shard_index:
            atom_paths: Dict[str, str] = {}
            for aid in to_load:
                spath = runtime.shard_index.get(aid)
                if spath and Path(spath).is_file():
                    atom_paths[aid] = str(spath)
                elif runtime.kafd_path and runtime.kafd_path.is_file():
                    atom_paths[aid] = str(runtime.kafd_path)
            loaded = karmazyn_store.load_folded_atoms_multi(
                runtime.store, atom_paths, proca_index=proca
            )
        elif runtime.kafd_path and runtime.kafd_path.is_file():
            loaded = karmazyn_store.load_folded_atoms(
                runtime.store,
                str(runtime.kafd_path),
                to_load,
                proca_index=proca,
            )
        for aid in to_load:
            runtime.folded_atoms.discard(aid)
            atom = runtime.store.get_atom(aid)
            if atom is not None:
                runtime.store.heat(atom)

    return {
        "unfolded_bubbles": sorted(bubbles),
        "unfolded_atoms": loaded,
        "radius": radius,
        "seeds": list(seeds),
        "folded_remaining": len(runtime.folded_atoms),
    }


def _load_meta(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_meta(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".meta.json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@dataclass
class WorldRuntime:
    bridge: KarminLambdaBridge
    lock: threading.RLock = field(default_factory=threading.RLock)
    kafd_path: Optional[Path] = None
    proca_dir: Optional[Path] = None
    folded_atoms: Set[str] = field(default_factory=set)
    shard_index: Dict[str, Path] = field(default_factory=dict)

    @property
    def store(self):
        return self.bridge.store

    @property
    def engine(self):
        return self.bridge.engine


@dataclass
class World:
    name: str
    runtime: WorldRuntime
    refs: int = 0
    dirty: bool = False
    created_at: float = field(default_factory=time.time)
    modified_at: float = field(default_factory=time.time)
    user_indexes: Set[str] = field(default_factory=set)


class WorldRegistry:
    """Rejestr trwałych światów — pamięć + dysk (.kafd + .meta.json)."""

    def __init__(self, base_dir: Optional[Path | str] = None):
        self._base = Path(base_dir) if base_dir else worlds_dir()
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._worlds: Dict[str, World] = {}

    @property
    def base_dir(self) -> Path:
        return self._base

    def list_worlds(self) -> List[dict]:
        names = set(self._worlds.keys())
        for path in self._base.glob("*.kafd"):
            names.add(path.stem)
        out: List[dict] = []
        for name in sorted(names):
            out.append(self._world_info(name))
        return out

    def _world_info(self, name: str) -> dict:
        kafd = _kafd_path(self._base, name)
        meta = _load_meta(_meta_path(self._base, name))
        cached = self._worlds.get(name)
        bubbles = 0
        if cached is not None:
            bubbles = len(cached.runtime.engine.api._bubble_index)
        qi = meta.get("query_indexes") or {}
        shard_meta = meta.get("shard_index") or {}
        return {
            "name": name,
            "exists_on_disk": kafd.is_file(),
            "loaded": cached is not None,
            "refs": cached.refs if cached else 0,
            "bubbles": bubbles or meta.get("bubbles", 0),
            "created_at": meta.get("created_at", cached.created_at if cached else None),
            "modified_at": meta.get("modified_at", cached.modified_at if cached else None),
            "indexed_keys": sorted((qi.get("inv_index") or {}).keys()),
            "folded_atoms": len(cached.runtime.folded_atoms) if cached else meta.get("folded_atoms", 0),
            "sharded": bool(shard_meta.get("sharded") or meta.get("sharded")),
            "shard_regions": shard_meta.get("regions", meta.get("shard_regions", 0)),
        }

    def _create_runtime(self) -> WorldRuntime:
        return WorldRuntime(KarminLambdaBridge(kernel.Store(thermal=True)))

    def _load_world(self, name: str) -> World:
        runtime = self._create_runtime()
        kafd = _kafd_path(self._base, name)
        meta = _load_meta(_meta_path(self._base, name))
        proca = _proca_dir(self._base, name)
        runtime.kafd_path = kafd
        runtime.proca_dir = proca
        from cynober_world_shards import atom_shard_paths

        runtime.shard_index = atom_shard_paths(self._base, name)
        if kafd.is_file():
            _, folded = load_runtime_from_kafd(
                runtime.bridge,
                kafd,
                proca_dir=proca,
                query_indexes=meta.get("query_indexes"),
                lazy=True,
                shard_paths=runtime.shard_index,
            )
            runtime.folded_atoms = folded
        user_indexes = set(meta.get("user_indexes", []))
        for key in user_indexes:
            runtime.engine.api._user_indexes.add(key)
        return World(
            name=name,
            runtime=runtime,
            created_at=float(meta.get("created_at", time.time())),
            modified_at=float(meta.get("modified_at", time.time())),
            user_indexes=user_indexes,
        )

    def attach(self, name: str) -> World:
        name = validate_world_name(name)
        with self._lock:
            if name not in self._worlds:
                self._worlds[name] = self._load_world(name)
            world = self._worlds[name]
            world.refs += 1
            return world

    def create(self, name: str) -> World:
        name = validate_world_name(name)
        kafd = _kafd_path(self._base, name)
        if kafd.exists():
            raise ValueError(f"Świat '{name}' już istnieje.")
        with self._lock:
            if name in self._worlds and self._worlds[name].refs > 0:
                raise ValueError(f"Świat '{name}' jest już załadowany.")
            world = World(name=name, runtime=self._create_runtime())
            self._worlds[name] = world
            world.refs = 1
            world.dirty = True
            self._persist(world)
            world.dirty = False
            return world

    def release(self, name: str) -> None:
        if not name:
            return
        with self._lock:
            world = self._worlds.get(name)
            if world is None:
                return
            world.refs = max(0, world.refs - 1)
            if world.dirty:
                self._persist(world)
                world.dirty = False

    def mark_dirty(self, name: Optional[str]) -> None:
        if not name:
            return
        with self._lock:
            world = self._worlds.get(name)
            if world is not None:
                world.dirty = True

    def flush(self, name: str) -> dict:
        name = validate_world_name(name)
        with self._lock:
            world = self._worlds.get(name)
            if world is None:
                raise ValueError(f"Świat '{name}' nie jest załadowany.")
            saved = self._persist(world)
            world.dirty = False
            return {"name": name, "saved": saved}

    def unfold(self, name: str, seeds: List[str], radius: int | None = None) -> dict:
        name = validate_world_name(name)
        with self._lock:
            world = self._worlds.get(name)
            if world is None:
                raise ValueError(f"Świat '{name}' nie jest załadowany.")
            with world.runtime.lock:
                return unfold_runtime(world.runtime, seeds, radius=radius)

    def flush_all_dirty(self) -> List[dict]:
        """Zapisuje wszystkie załadowane światy z flagą dirty (auto-flush)."""
        with self._lock:
            dirty_names = [n for n, w in self._worlds.items() if w.dirty]
        flushed: List[dict] = []
        for name in dirty_names:
            try:
                flushed.append(self.flush(name))
            except ValueError:
                pass
        return flushed

    def delete(self, name: str) -> None:
        name = validate_world_name(name)
        with self._lock:
            world = self._worlds.get(name)
            if world is not None and world.refs > 0:
                raise ValueError(f"Świat '{name}' ma aktywne sesje ({world.refs}).")
            self._worlds.pop(name, None)
        kafd = _kafd_path(self._base, name)
        meta = _meta_path(self._base, name)
        if kafd.is_file():
            kafd.unlink()
        if meta.is_file():
            meta.unlink()
        proca = self._base / "proca" / name
        if proca.is_dir():
            shutil.rmtree(proca, ignore_errors=True)
        from cynober_world_shards import clear_shards

        clear_shards(self._base, name)

    def _persist(self, world: World) -> int:
        with world.runtime.lock:
            kafd = _kafd_path(self._base, world.name)
            proca = _proca_dir(self._base, world.name)
            from cynober_world_shards import (
                atom_shard_paths,
                save_sharded_runtime,
                sharding_enabled,
            )

            api = world.runtime.engine.api
            api.prune_dead_bindings()
            api.gc_orphan_atoms(keep_hist=False)
            rebuild_all_indexes(api)

            if sharding_enabled():
                stats = save_sharded_runtime(
                    world.runtime.bridge,
                    kafd,
                    self._base,
                    world.name,
                    proca_dir=proca,
                    proca_cold=True,
                )
                saved = stats.get("manifest_atoms", 0)
                world.runtime.shard_index = atom_shard_paths(self._base, world.name)
            else:
                saved = save_runtime_to_kafd(
                    world.runtime.bridge,
                    kafd,
                    proca_dir=proca,
                    proca_cold=True,
                )
                world.runtime.shard_index = {}
            api = world.runtime.engine.api
            bubbles = len(api._bubble_index)
            meta = {
                "name": world.name,
                "created_at": world.created_at,
                "modified_at": time.time(),
                "bubbles": bubbles,
                "user_indexes": sorted(api._user_indexes),
                "query_indexes": export_query_indexes(api),
            }
            meta["folded_atoms"] = len(world.runtime.folded_atoms)
            meta["lazy_load"] = lazy_load_enabled()
            meta["sharded"] = sharding_enabled()
            if sharding_enabled():
                from cynober_world_shards import load_shard_index

                shard_idx = load_shard_index(self._base, world.name)
                meta["shard_index"] = shard_idx
                meta["shard_regions"] = len(shard_idx.get("regions") or [])
            _save_meta(_meta_path(self._base, world.name), meta)
            world.modified_at = meta["modified_at"]
            world.user_indexes = set(meta["user_indexes"])
            world.runtime.folded_atoms = set()
            return saved

    @property
    def loaded_count(self) -> int:
        with self._lock:
            return len(self._worlds)

    @property
    def persistent_count(self) -> int:
        return len(list(self._base.glob("*.kafd")))


_world_registry: Optional[WorldRegistry] = None
_registry_lock = threading.Lock()


def get_world_registry() -> WorldRegistry:
    global _world_registry
    with _registry_lock:
        if _world_registry is None:
            _world_registry = WorldRegistry()
        return _world_registry


def reset_world_registry_for_tests(base_dir: Optional[Path] = None) -> WorldRegistry:
    """Tylko testy — nowy rejestr na katalogu tymczasowym."""
    global _world_registry
    with _registry_lock:
        _world_registry = WorldRegistry(base_dir=base_dir)
        return _world_registry