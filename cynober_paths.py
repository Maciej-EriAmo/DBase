# -*- coding: utf-8 -*-
"""Dane Cynober / Karmin_DB — poza drzewem kodu (LOCALAPPDATA\\Cynober)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple


def int_env(name: str, default: int) -> int:
    """Odczytaj dodatnią liczbę całkowitą ze zmiennej środowiskowej (albo `default`)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    """Zapisz `data` jako JSON atomowo (tmp + replace), żeby nie zostawić uciętego pliku."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def data_home() -> Path:
    raw = (os.environ.get("CYNOBER_DATA_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local) / "Cynober"
    return Path.home() / ".local" / "share" / "cynober"


def worlds_home() -> Path:
    return data_home() / "worlds"


def client_config_path() -> Path:
    return data_home() / "client.json"


def node_id_path() -> Path:
    return data_home() / "node_id"


def phi2_path() -> Path:
    return data_home() / "phi2"


def _move_file(src: Path, dst: Path, moved: List[str]) -> None:
    if not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.is_file():
        shutil.copy2(src, dst)
        moved.append(src.name)
    try:
        src.unlink()
    except OSError:
        pass


def _move_dir(src: Path, dst: Path, moved: List[str]) -> None:
    if not src.is_dir():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copytree(src, dst)
        moved.append(str(src))
        shutil.rmtree(src, ignore_errors=True)
        return
    # dest już jest — nie mieszaj; zostaw legacy
    moved.append(f"keep-dest {src.name}")


def relocate_legacy() -> Dict[str, Any]:
    """Idempotentnie: ~/.cynober_worlds i ~/.karmazyn_* → LOCALAPPDATA\\Cynober."""
    home = Path.home()
    dest = data_home()
    dest.mkdir(parents=True, exist_ok=True)
    moved: List[str] = []
    _move_dir(home / ".cynober_worlds", worlds_home(), moved)
    _move_file(home / ".karmazyn_client.json", client_config_path(), moved)
    _move_file(home / ".karmazyn_node_id", node_id_path(), moved)
    _move_file(home / ".karmazyn_phi2", phi2_path(), moved)
    readme = dest / "README.txt"
    if not readme.is_file():
        readme.write_text(
            "Cynober / Karmin_DB data home — nie katalog DBase.\n"
            "Światy: worlds\\  klient: client.json\n",
            encoding="utf-8",
        )
    return {"home": str(dest), "moved": moved}


def legacy_pairs() -> Tuple[Tuple[Path, Path], ...]:
    h = Path.home()
    return (
        (h / ".cynober_worlds", worlds_home()),
        (h / ".karmazyn_client.json", client_config_path()),
        (h / ".karmazyn_node_id", node_id_path()),
        (h / ".karmazyn_phi2", phi2_path()),
    )
