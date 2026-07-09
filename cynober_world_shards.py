"""
cynober_world_shards.py — shardy KAFD per region grafu (v8.0, faza 3)
=====================================================================
Każdy spójny składnik grafu relacji (rel:*) = region → osobny plik .kafd.
Główny {świat}.kafd to warstwa manifestu (nagłówki + bąble + HOT).
"""

from __future__ import annotations

import json
import os
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from karmazyn_atom import T_HOT

SHARD_INDEX_VERSION = 1
REGION_PREFIX = "region_"


def sharding_enabled() -> bool:
    raw = os.environ.get("CYNOBER_SHARDED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def shards_dir(base: Path, world: str) -> Path:
    path = base / "shards" / world
    path.mkdir(parents=True, exist_ok=True)
    return path


def shard_index_path(base: Path, world: str) -> Path:
    return shards_dir(base, world) / "index.json"


def region_id(index: int) -> str:
    return f"{REGION_PREFIX}{index}"


def region_filename(rid: str) -> str:
    return f"{rid}.kafd"


def compute_bubble_regions(api) -> List[Set[str]]:
    """Spójne składniki grafu bąbli po krawędziach rel:*."""
    bubbles = set(api._bubble_index.keys())
    adj: Dict[str, Set[str]] = {b: set() for b in bubbles}
    for name, bubble in api._bubble_index.items():
        for key in bubble.bindings:
            if not key.startswith("rel:"):
                continue
            parts = key.split(":", 2)
            if len(parts) >= 3:
                target = parts[2]
                if target in bubbles:
                    adj[name].add(target)
                    adj[target].add(name)

    visited: Set[str] = set()
    regions: List[Set[str]] = []
    for seed in sorted(bubbles):
        if seed in visited:
            continue
        component: Set[str] = set()
        stack = [seed]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            component.add(cur)
            for n in adj[cur]:
                if n not in visited:
                    stack.append(n)
        regions.append(component)
    return regions


def assign_atoms_to_regions(
    api,
    regions: List[Set[str]],
) -> Tuple[Dict[str, Set[str]], Dict[str, str]]:
    """
    Mapuj atomy na regiony.
    Zwraca (region_id -> atom_ids, atom_id -> region_id).
    Atomy w wielu regionach nie trafiają do shardów (zostają w manifeście).
    """
    bubble_to_region: Dict[str, str] = {}
    for i, comp in enumerate(regions):
        rid = region_id(i)
        for b in comp:
            bubble_to_region[b] = rid

    atom_region_ids: Dict[str, Set[str]] = defaultdict(set)
    for bname, bubble in api._bubble_index.items():
        rid = bubble_to_region.get(bname)
        if rid is None:
            continue
        for key, aid in bubble.bindings.items():
            if aid and not key.startswith("hist:"):
                atom_region_ids[aid].add(rid)

    per_region: Dict[str, Set[str]] = {region_id(i): set() for i in range(len(regions))}
    atom_to_region: Dict[str, str] = {}
    for aid, rids in atom_region_ids.items():
        if len(rids) == 1:
            rid = next(iter(rids))
            per_region[rid].add(aid)
            atom_to_region[aid] = rid

    return per_region, atom_to_region


def build_shard_plan(api) -> Tuple[List[Set[str]], Dict[str, Set[str]], Dict[str, str]]:
    regions = compute_bubble_regions(api)
    per_region, atom_to_region = assign_atoms_to_regions(api, regions)
    return regions, per_region, atom_to_region


def _load_shard_index_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_shard_index(
    base: Path,
    world: str,
    regions: List[Set[str]],
    per_region_atoms: Dict[str, Set[str]],
    *,
    shard_sizes: Optional[Dict[str, int]] = None,
) -> dict:
    entries = []
    for i, comp in enumerate(regions):
        rid = region_id(i)
        fname = region_filename(rid)
        entries.append({
            "id": rid,
            "file": fname,
            "bubbles": sorted(comp),
            "atoms": sorted(per_region_atoms.get(rid, set())),
            "bytes": (shard_sizes or {}).get(rid, 0),
        })
    index = {
        "version": SHARD_INDEX_VERSION,
        "world": world,
        "sharded": True,
        "regions": entries,
    }
    path = shard_index_path(base, world)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return index


def load_shard_index(base: Path, world: str) -> dict:
    return _load_shard_index_file(shard_index_path(base, world))


def atom_shard_paths(base: Path, world: str) -> Dict[str, Path]:
    """atom_id -> absolutna ścieżka pliku sharda."""
    index = load_shard_index(base, world)
    out: Dict[str, Path] = {}
    shard_root = shards_dir(base, world)
    for entry in index.get("regions") or []:
        if not isinstance(entry, dict):
            continue
        fname = entry.get("file", "")
        if not fname:
            continue
        shard_path = shard_root / fname
        for aid in entry.get("atoms") or []:
            if isinstance(aid, str):
                out[aid] = shard_path
    return out


def clear_shards(base: Path, world: str) -> None:
    root = base / "shards" / world
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def save_sharded_runtime(
    bridge,
    kafd_path: Path | str,
    base: Path,
    world: str,
    *,
    proca_dir: Path | str | None = None,
    proca_cold: bool = True,
) -> dict:
    """Zapisz manifest + shardy regionów. Zwraca statystyki zapisu."""
    import karmazyn_store
    from cynober_worlds import _proca_index_for

    api = bridge.engine.api
    store = bridge.store
    regions, per_region, atom_to_region = build_shard_plan(api)

    proca_index = _proca_index_for(proca_dir) if proca_cold else None
    shard_root = shards_dir(base, world)
    clear_shards(base, world)
    shard_root.mkdir(parents=True, exist_ok=True)

    syn_ids: List[str] = []
    try:
        for nazwa, b in api._bubble_index.items():
            syn = store.atom_new(S="__bubble__", E=nazwa, value=nazwa)
            syn.metadata["bindings"] = b.bindings
            syn_ids.append(syn.id)
        kinds = list({a.S for a in store.reg.atoms() if a.S})
        if "__bubble__" not in kinds:
            kinds.append("__bubble__")

        sharded_atom_ids = set(atom_to_region.keys())

        def _manifest_payload(atom) -> bool:
            if atom.S == "__bubble__":
                return True
            if float(atom.T) >= T_HOT:
                return True
            return atom.id not in sharded_atom_ids

        manifest_count = karmazyn_store.save_documents_filtered(
            store,
            str(kafd_path),
            kinds=kinds,
            proca_index=proca_index,
            proca_cold_only=bool(proca_cold and proca_index is not None),
            include_payload=_manifest_payload,
        )

        shard_sizes: Dict[str, int] = {}
        for rid, atom_ids in per_region.items():
            if not atom_ids:
                continue
            shard_path = shard_root / region_filename(rid)
            allow = set(atom_ids)

            def _shard_payload(atom, _allow=allow) -> bool:
                return atom.id in _allow

            n = karmazyn_store.save_documents_filtered(
                store,
                str(shard_path),
                kinds=kinds,
                proca_index=proca_index,
                proca_cold_only=bool(proca_cold and proca_index is not None),
                atom_filter=lambda a, _allow=allow: a.id in _allow,
                include_payload=_shard_payload,
            )
            shard_sizes[rid] = shard_path.stat().st_size if shard_path.is_file() else 0
            if n == 0 and shard_path.is_file():
                shard_path.unlink(missing_ok=True)

        index = save_shard_index(
            base, world, regions, per_region, shard_sizes=shard_sizes
        )
        return {
            "manifest_atoms": manifest_count,
            "regions": len(regions),
            "shards": len([e for e in index.get("regions", []) if e.get("bytes", 0) > 0]),
            "atom_to_region": atom_to_region,
        }
    finally:
        for sid in syn_ids:
            store.reg.delete(sid)


def write_shard_bytes(base: Path, world: str, rid: str, data: bytes) -> Path:
    dest = shards_dir(base, world) / region_filename(rid)
    dest.write_bytes(data)
    return dest


def read_shard_bytes(base: Path, world: str, rid: str) -> bytes:
    path = shards_dir(base, world) / region_filename(rid)
    if not path.is_file():
        raise ValueError(f"Shard '{rid}' nie istnieje dla świata '{world}'.")
    return path.read_bytes()


def list_shard_regions(base: Path, world: str) -> List[dict]:
    index = load_shard_index(base, world)
    out: List[dict] = []
    for entry in index.get("regions") or []:
        if isinstance(entry, dict):
            out.append(dict(entry))
    return out


def import_shard_index(base: Path, world: str, index: dict) -> None:
    if not index:
        return
    path = shard_index_path(base, world)
    shards_dir(base, world).mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)