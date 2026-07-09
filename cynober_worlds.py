#!/usr/bin/env python3
"""
cynober_worlds.py — trwałe, nazwane światy na serwerze Cynober (v7.1)
=======================================================================
Każdy świat = współdzielony Store + KarminEngine, zapisany jako .kafd na dysku.
Wiele sesji RPC może dołączyć do tego samego świata (wspólny stan, lock na zapis).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge
from cynober_query_engine import KarminType

WORLD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

DEFAULT_WORLDS_DIR = Path.home() / ".cynober_worlds"


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


def save_runtime_to_kafd(bridge: KarminLambdaBridge, path: Path | str) -> int:
    import karmazyn_store

    store = bridge.store
    engine = bridge.engine
    syn_ids: List[str] = []
    try:
        for nazwa, b in engine.api._bubble_index.items():
            syn = store.atom_new(S="__bubble__", E=nazwa, value=nazwa)
            syn.metadata["bindings"] = b.bindings
            syn_ids.append(syn.id)
        kinds = list({a.S for a in store.reg.atoms() if a.S})
        if "__bubble__" not in kinds:
            kinds.append("__bubble__")
        return karmazyn_store.save_documents(store, str(path), kinds=kinds)
    finally:
        for sid in syn_ids:
            store.reg.delete(sid)


def load_runtime_from_kafd(bridge: KarminLambdaBridge, path: Path | str) -> int:
    import karmazyn_store

    store = bridge.store
    engine = bridge.engine
    loaded = karmazyn_store.load_documents(store, str(path))
    for a in list(store.reg.atoms()):
        if a.S != "__bubble__":
            continue
        nazwa = a.E
        if nazwa not in engine.api._bubble_index:
            b = store.bubble_new(label=nazwa)
            b.bindings = dict(a.metadata.get("bindings", {}))
            store.set_root(b)
            engine.api._bubble_index[nazwa] = b
        store.reg.delete(a.id)
    rebuild_all_indexes(engine.api)
    return loaded


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
        return {
            "name": name,
            "exists_on_disk": kafd.is_file(),
            "loaded": cached is not None,
            "refs": cached.refs if cached else 0,
            "bubbles": bubbles or meta.get("bubbles", 0),
            "created_at": meta.get("created_at", cached.created_at if cached else None),
            "modified_at": meta.get("modified_at", cached.modified_at if cached else None),
        }

    def _create_runtime(self) -> WorldRuntime:
        return WorldRuntime(KarminLambdaBridge(kernel.Store(thermal=True)))

    def _load_world(self, name: str) -> World:
        runtime = self._create_runtime()
        kafd = _kafd_path(self._base, name)
        meta = _load_meta(_meta_path(self._base, name))
        if kafd.is_file():
            load_runtime_from_kafd(runtime.bridge, kafd)
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

    def _persist(self, world: World) -> int:
        kafd = _kafd_path(self._base, world.name)
        saved = save_runtime_to_kafd(world.runtime.bridge, kafd)
        world.modified_at = time.time()
        bubbles = len(world.runtime.engine.api._bubble_index)
        meta = {
            "name": world.name,
            "created_at": world.created_at,
            "modified_at": world.modified_at,
            "bubbles": bubbles,
            "user_indexes": sorted(world.runtime.engine.api._user_indexes),
        }
        _save_meta(_meta_path(self._base, world.name), meta)
        world.user_indexes = set(meta["user_indexes"])
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