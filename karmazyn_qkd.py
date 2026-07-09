"""
karmazyn_qkd.py — adapter źródła k_QKD dla HSL (v7.5)
=====================================================
Slot na seed kwantowy / hybrydowy. Priorytet źródeł:

  1. KARM_QKD_SEED  — env (hex 64 zn. lub hasło → SHA-256)
  2. KARM_QKD_PATH  — plik binarny (32+ B) lub tekst hex
  3. KARM_QKD_PIPE  — nazwany pipe / ścieżka FIFO (jednorazowy odczyt)

Ten sam interfejs co symulacja env — HSL (hybrid_link_seed, qkd_fp) bez zmian.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

_QKD_CACHE: Optional[bytes] = None


def _parse_seed_text(raw: str) -> bytes:
    text = raw.strip()
    if not text:
        raise ValueError("pusty seed QKD")
    if len(text) >= 64 and all(c in "0123456789abcdefABCDEF" for c in text):
        return bytes.fromhex(text)
    return hashlib.sha256(text.encode("utf-8")).digest()


def _read_file(path: Path) -> bytes:
    data = path.read_bytes()
    if not data:
        raise ValueError("pusty plik QKD")
    try:
        text = data.decode("utf-8").strip()
        if len(text) >= 64 and all(c in "0123456789abcdefABCDEF \n\r\t" for c in text):
            hex_only = "".join(text.split())
            if len(hex_only) >= 64:
                return bytes.fromhex(hex_only[:64])
    except UnicodeDecodeError:
        pass
    if len(data) < 32:
        return hashlib.sha256(data).digest()
    return hashlib.sha256(data).digest()


def _read_pipe(path: Path) -> bytes:
    data = path.read_bytes()
    if not data:
        raise ValueError("pusty pipe QKD")
    return _read_file_bytes(data)


def _read_file_bytes(data: bytes) -> bytes:
    if len(data) < 32:
        return hashlib.sha256(data).digest()
    return hashlib.sha256(data).digest()


def clear_qkd_cache() -> None:
    """Do testów — wyczyść cache odczytu."""
    global _QKD_CACHE
    _QKD_CACHE = None


def load_qkd_bytes() -> bytes | None:
    """
    Załaduj k_QKD z pierwszego dostępnego źródła.
    Env: zawsze świeży odczyt (testy, rotacja). Plik/pipe: cache procesu.
    """
    global _QKD_CACHE

    env = os.environ.get("KARM_QKD_SEED", "").strip()
    if env:
        return _parse_seed_text(env)

    if _QKD_CACHE is not None:
        return _QKD_CACHE

    path_raw = os.environ.get("KARM_QKD_PATH", "").strip()
    if path_raw:
        _QKD_CACHE = _read_file(Path(path_raw).expanduser())
        return _QKD_CACHE

    pipe_raw = os.environ.get("KARM_QKD_PIPE", "").strip()
    if pipe_raw:
        _QKD_CACHE = _read_pipe(Path(pipe_raw).expanduser())
        return _QKD_CACHE

    return None


def qkd_source_label() -> str | None:
    """Etykieta aktywnego źródła (do logów / ZDROWIE)."""
    if os.environ.get("KARM_QKD_SEED", "").strip():
        return "env"
    if os.environ.get("KARM_QKD_PATH", "").strip():
        return "file"
    if os.environ.get("KARM_QKD_PIPE", "").strip():
        return "pipe"
    return None