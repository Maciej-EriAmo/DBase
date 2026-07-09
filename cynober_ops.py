#!/usr/bin/env python3
"""
cynober_ops.py — operacje serwera v7.4 (metryki, zdrowie, kopie światów)
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cynober_worlds import (
    WorldRegistry,
    _kafd_path,
    _meta_path,
    load_runtime_from_kafd,
    save_runtime_to_kafd,
    validate_world_name,
)

SERVER_VERSION = "7.6"

_BACKUP_WORLD_RE = re.compile(
    r'^KOPIA\s+ZAPASOWA\s+ŚWIATA\s+"([^"]+)"$',
    re.IGNORECASE,
)
_LIST_BACKUPS_RE = re.compile(
    r'^LISTA\s+KOPII\s+ŚWIATA\s+"([^"]+)"$',
    re.IGNORECASE,
)
_RESTORE_RE = re.compile(
    r'^PRZYWRÓĆ\s+ŚWIAT\s+"([^"]+)"\s+Z\s+KOPII\s+"([^"]+)"$',
    re.IGNORECASE,
)


class ServerMetrics:
    """Licznik zapytań i sesji — thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self.queries_total = 0
        self.queries_ok = 0
        self.queries_error = 0
        self.rate_limited = 0
        self.connections_total = 0
        self._action_counts: Dict[str, int] = {}

    def record_connection(self) -> None:
        with self._lock:
            self.connections_total += 1

    def record_rate_limit(self) -> None:
        with self._lock:
            self.rate_limited += 1

    def record_query(self, results: list) -> None:
        with self._lock:
            self.queries_total += 1
            ok = results and all(r.get("status") != "error" for r in results)
            if ok:
                self.queries_ok += 1
            else:
                self.queries_error += 1
            if results:
                action = results[0].get("action", "QUERY")
                self._action_counts[action] = self._action_counts.get(action, 0) + 1

    def snapshot(
        self,
        *,
        active_sessions: int,
        loaded_worlds: int,
        persistent_worlds: int,
        worlds_dir: str,
        auth_enabled: bool,
    ) -> dict:
        with self._lock:
            uptime = time.time() - self._started_at
            return {
                "server_version": SERVER_VERSION,
                "uptime_sec": round(uptime, 2),
                "queries_total": self.queries_total,
                "queries_ok": self.queries_ok,
                "queries_error": self.queries_error,
                "rate_limited": self.rate_limited,
                "connections_total": self.connections_total,
                "active_sessions": active_sessions,
                "loaded_worlds": loaded_worlds,
                "persistent_worlds": persistent_worlds,
                "worlds_dir": worlds_dir,
                "auth_enabled": auth_enabled,
                "top_actions": dict(
                    sorted(self._action_counts.items(), key=lambda x: -x[1])[:12]
                ),
            }

    def health(self) -> dict:
        with self._lock:
            return {
                "status": "ok",
                "server_version": SERVER_VERSION,
                "uptime_sec": round(time.time() - self._started_at, 2),
            }


_metrics: Optional[ServerMetrics] = None
_metrics_lock = threading.Lock()


def get_server_metrics() -> ServerMetrics:
    global _metrics
    with _metrics_lock:
        if _metrics is None:
            _metrics = ServerMetrics()
        return _metrics


def reset_server_metrics_for_tests() -> ServerMetrics:
    global _metrics
    with _metrics_lock:
        _metrics = ServerMetrics()
        return _metrics


def backups_root(worlds_dir: Path) -> Path:
    root = worlds_dir / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _backup_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class WorldBackupManager:
    def __init__(self, registry: WorldRegistry):
        self._registry = registry
        self._root = backups_root(registry.base_dir)

    def create(self, world_name: str) -> dict:
        name = validate_world_name(world_name)
        with self._registry._lock:
            world = self._registry._worlds.get(name)
            if world is not None:
                if world.dirty:
                    self._registry._persist(world)
                    world.dirty = False
        kafd = _kafd_path(self._registry.base_dir, name)
        if not kafd.is_file():
            raise ValueError(f"Świat '{name}' nie ma zapisu na dysku.")
        backup_id = _backup_id_now()
        dest = self._root / name / backup_id
        dest.mkdir(parents=True, exist_ok=False)
        shutil.copy2(kafd, dest / f"{name}.kafd")
        meta = _meta_path(self._registry.base_dir, name)
        if meta.is_file():
            shutil.copy2(meta, dest / f"{name}.meta.json")
        manifest = {
            "world": name,
            "backup_id": backup_id,
            "created_at": time.time(),
            "source_kafd": str(kafd),
        }
        (dest / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest

    def list_backups(self, world_name: str) -> List[dict]:
        name = validate_world_name(world_name)
        world_dir = self._root / name
        if not world_dir.is_dir():
            return []
        out: List[dict] = []
        for entry in sorted(world_dir.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.json"
            if manifest_path.is_file():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    out.append(data)
                    continue
                except (OSError, json.JSONDecodeError):
                    pass
            out.append({
                "world": name,
                "backup_id": entry.name,
                "created_at": entry.stat().st_mtime,
            })
        return out

    def restore(self, world_name: str, backup_id: str) -> dict:
        name = validate_world_name(world_name)
        bid = backup_id.strip()
        if not bid:
            raise ValueError("Brak identyfikatora kopii.")
        src = self._root / name / bid
        if not src.is_dir():
            raise ValueError(f"Kopia '{bid}' nie istnieje dla świata '{name}'.")
        src_kafd = src / f"{name}.kafd"
        if not src_kafd.is_file():
            raise ValueError(f"Uszkodzona kopia: brak pliku .kafd.")
        with self._registry._lock:
            world = self._registry._worlds.get(name)
            if world is not None and world.refs > 0:
                new_rt = self._registry._create_runtime()
                load_runtime_from_kafd(new_rt.bridge, src_kafd)
                with world.runtime.lock:
                    world.runtime = new_rt
                world.dirty = True
                self._registry._persist(world)
                world.dirty = False
            else:
                shutil.copy2(src_kafd, _kafd_path(self._registry.base_dir, name))
                src_meta = src / f"{name}.meta.json"
                if src_meta.is_file():
                    shutil.copy2(src_meta, _meta_path(self._registry.base_dir, name))
                if world is not None:
                    self._registry._worlds.pop(name, None)
        return {"world": name, "backup_id": bid, "restored": True}


def try_ops_command(
    stripped: str,
    upper: str,
    registry: WorldRegistry,
    *,
    active_sessions: int,
    auth_enabled: bool,
) -> Optional[list]:
    metrics = get_server_metrics()

    if upper == "ZDROWIE":
        return [{"status": "ok", "action": "HEALTH", "data": metrics.health()}]

    if upper == "METRYKI SERWERA":
        return [{
            "status": "ok",
            "action": "SERVER_METRICS",
            "data": metrics.snapshot(
                active_sessions=active_sessions,
                loaded_worlds=registry.loaded_count,
                persistent_worlds=registry.persistent_count,
                worlds_dir=str(registry.base_dir),
                auth_enabled=auth_enabled,
            ),
        }]

    mgr = WorldBackupManager(registry)

    m = _BACKUP_WORLD_RE.match(stripped)
    if m:
        try:
            info = mgr.create(m.group(1))
            return [{"status": "ok", "action": "BACKUP_WORLD", **info}]
        except (ValueError, OSError) as e:
            return [{"status": "error", "message": str(e)}]

    m = _LIST_BACKUPS_RE.match(stripped)
    if m:
        backups = mgr.list_backups(m.group(1))
        return [{
            "status": "ok",
            "action": "LIST_BACKUPS",
            "world": validate_world_name(m.group(1)),
            "backups": backups,
        }]

    m = _RESTORE_RE.match(stripped)
    if m:
        try:
            info = mgr.restore(m.group(1), m.group(2))
            return [{"status": "ok", "action": "RESTORE_WORLD", **info}]
        except (ValueError, OSError) as e:
            return [{"status": "error", "message": str(e)}]

    return None


def is_ops_query(stripped: str, upper: str) -> bool:
    return (
        upper in ("ZDROWIE", "METRYKI SERWERA")
        or bool(_BACKUP_WORLD_RE.match(stripped))
        or bool(_LIST_BACKUPS_RE.match(stripped))
        or bool(_RESTORE_RE.match(stripped))
    )


def is_ops_read_query(stripped: str, upper: str) -> bool:
    return upper in ("ZDROWIE", "METRYKI SERWERA") or bool(
        _LIST_BACKUPS_RE.match(stripped)
    )


def is_ops_admin_query(stripped: str) -> bool:
    return bool(_RESTORE_RE.match(stripped))


def is_ops_write_query(stripped: str) -> bool:
    return bool(_BACKUP_WORLD_RE.match(stripped))


def world_from_ops_query(stripped: str) -> Optional[str]:
    for pat in (_BACKUP_WORLD_RE, _LIST_BACKUPS_RE, _RESTORE_RE):
        m = pat.match(stripped)
        if m:
            return validate_world_name(m.group(1))
    return None