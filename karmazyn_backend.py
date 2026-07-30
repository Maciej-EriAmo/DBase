#!/usr/bin/env python3
"""
karmazyn_backend.py — przełącznik substratu (native Rust | Python reference)
===========================================================================
Maciej Mazur, Warsaw 2026

  KARMAZYN_SUBSTRATE=native   # domyślne gdy most Rust dostępny (PyO3 lub ctypes)
  KARMAZYN_SUBSTRATE=python   # referencyjna implementacja pure-Python
  KARMAZYN_SUBSTRATE=both     # tylko meta: test_compat uruchamia oba

Most native (opcjonalnie):
  KARMAZYN_NATIVE_BRIDGE=pyo3|ctypes   # wymuszenie mostu (domyślnie: pyo3→ctypes)

CLI (test_substrate_compat / boot):
  --substrate python|native
  --native / --python

API:
  substrate_backend() -> "python" | "native"
  open_store(thermal=True, **kw) -> Store-like
  native_available() -> bool
  resolve_default_backend() -> "python" | "native"
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional, Type


_VALID = frozenset({"python", "native", "both"})


def _candidate_roots():
    """Repo root candidates (flat layout + kernel/ mirror)."""
    here = os.path.dirname(os.path.abspath(__file__))
    yield here
    parent = os.path.dirname(here)
    if parent and parent != here:
        yield parent


def _native_dirs():
    for root in _candidate_roots():
        yield os.path.join(root, "native")
        yield root  # allow `import native.*` from repo root


def native_available() -> bool:
    for d in _native_dirs():
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        from native.karmazyn_substrate_native import native_available as nav
        return nav()
    except Exception:
        try:
            from karmazyn_substrate_native import native_available as nav
            return nav()
        except Exception:
            return False


def resolve_default_backend() -> str:
    """Domyślny backend: zawsze native (Rust), gdy most jest zbudowany.

    Pure-Python tylko gdy native niedostępny albo KARMAZYN_SUBSTRATE=python.
    """
    return "native" if native_available() else "python"


def substrate_backend(explicit: Optional[str] = None) -> str:
    """Aktualny backend: explicit > env > auto (native = default produkcyjny).

    'both' normalizowane do python przy open_store (both = tryb testów).
    Alias env: rust|c|dll|ksub → native.
    """
    if explicit is not None:
        raw = explicit
    else:
        env = os.environ.get("KARMAZYN_SUBSTRATE")
        if env is None or str(env).strip() == "":
            return resolve_default_backend()
        raw = env
    b = str(raw).strip().lower()
    if b in ("rust", "c", "dll", "ksub"):
        b = "native"
    if b not in _VALID:
        raise ValueError(
            f"KARMAZYN_SUBSTRATE={raw!r} — oczekiwano python|native|both"
        )
    return b


def apply_cli_substrate_flags(argv=None) -> Optional[str]:
    """Ustaw env z argv. Zwraca wybrany backend lub None."""
    argv = list(sys.argv if argv is None else argv)
    chosen = None
    if "--native" in argv:
        chosen = "native"
    if "--python" in argv:
        chosen = "python"
    if "--substrate" in argv:
        i = argv.index("--substrate")
        if i + 1 < len(argv):
            chosen = argv[i + 1]
    if chosen is not None:
        os.environ["KARMAZYN_SUBSTRATE"] = chosen
    return chosen


def _load_native_store_class() -> Type:
    for d in _native_dirs():
        if d not in sys.path:
            sys.path.insert(0, d)
    from karmazyn_substrate_native import NativeStore
    return NativeStore


def store_class(backend: Optional[str] = None) -> Type:
    """Klasa Store dla wybranego backendu (domyślnie: native jeśli dostępny).

    Używane przez karmazyn_kernel.Store — `from karmazyn_kernel import Store`
    daje Rust, gdy most jest zbudowany.
    """
    b = substrate_backend(backend)
    if b == "both":
        b = "python"
    if b == "native":
        if not native_available():
            # klasa: miękki fallback (konstruktor open_store nadal może rzucić
            # gdy wymuszono native i brak mostu)
            if backend is not None or (
                os.environ.get("KARMAZYN_SUBSTRATE", "").strip().lower()
                in ("native", "rust", "c", "dll", "ksub")
            ):
                # jawne native bez mostu — i tak zwróć NativeStore load attempt
                raise RuntimeError(
                    "substrat native niedostępny — zbuduj: .\\native\\build_native.ps1 "
                    "albo KARMAZYN_SUBSTRATE=python"
                )
            from karmazyn_substrate import Store as PythonStore
            return PythonStore
        return _load_native_store_class()
    from karmazyn_substrate import Store as PythonStore
    return PythonStore


def open_store(thermal: bool = True, backend: Optional[str] = None, **kwargs: Any):
    """Utwórz Store na wybranym substracie.

    backend=None → KARMAZYN_SUBSTRATE lub auto (**native = default**).
    both → python (tryb testów, nie runtime).
    kwargs przekazywane do konstruktora (env_of, extra_reach, …).
    """
    b = substrate_backend(backend)
    if b == "both":
        b = "python"
    if b == "native":
        if not native_available():
            # auto-default bez mostu → cichy fallback do referencji Python
            forced = (
                backend is not None
                or os.environ.get("KARMAZYN_SUBSTRATE", "").strip() != ""
            )
            if forced:
                raise RuntimeError(
                    "substrat native niedostępny — zbuduj:\n"
                    "  .\\native\\build_native.ps1\n"
                    "albo użyj: KARMAZYN_SUBSTRATE=python"
                )
            from karmazyn_substrate import Store as PythonStore
            return PythonStore(thermal=thermal, **kwargs)
        NativeStore = _load_native_store_class()
        return NativeStore(thermal=thermal, **kwargs)
    from karmazyn_substrate import Store as PythonStore
    return PythonStore(thermal=thermal, **kwargs)


def backend_info() -> dict:
    b = substrate_backend()
    nav = native_available()
    ver = None
    bridge = None
    if nav:
        try:
            for d in _native_dirs():
                if d not in sys.path:
                    sys.path.insert(0, d)
            from karmazyn_substrate_native import native_version, native_bridge
            ver = native_version()
            bridge = native_bridge()
        except Exception as e:
            ver = f"error: {e}"
    cls_name = None
    try:
        cls_name = store_class().__name__
    except Exception:
        cls_name = None
    return {
        "backend": b,
        "default": resolve_default_backend(),
        "native_available": nav,
        "native_version": ver,
        "native_bridge": bridge,  # pyo3 | ctypes | none
        "store_class": cls_name,  # NativeStore | Store
        "role": {
            "native": "production DEFAULT (Rust)",
            "python": "reference implementation + explicit fallback",
        },
        "env": os.environ.get("KARMAZYN_SUBSTRATE", ""),
    }


if __name__ == "__main__":
    apply_cli_substrate_flags()
    info = backend_info()
    print("substrate backend:", info)
    s = open_store(thermal=True)
    a = s.atom_new("probe", "x")
    print(
        "store",
        type(s).__name__,
        "atom",
        getattr(a, "id", a),
        "has",
        s.has_atom(getattr(a, "id", a)),
        "backend",
        getattr(s, "native_backend", "python"),
    )
    print("stats", s.stats())
