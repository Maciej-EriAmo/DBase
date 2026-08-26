"""
karmazyn_substrate_native.py — Python drop-in Store over Rust core.

Backends (preferred → fallback):
  1. PyO3  `karmazyn_substrate_rs.CoreStore`  (phase 4)
  2. ctypes C ABI DLL  `karmazyn_substrate.dll`  (phase 0–3)

Surface for boot + Lua + mini-Lisp:
  atoms (metadata["v"]/["k"]), bubbles (bindings, parent), roots,
  tick/settle, events, reach hooks, stats, atoms(), HRR resonance.

GC law lives in Rust; language payloads + EventBus + HRR stay in Python.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    byref,
    c_char_p,
    c_double,
    c_int,
    c_int64,
    c_uint32,
    c_uint64,
    c_void_p,
)
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from karmazyn_substrate import Bubble, EventBus  # noqa: E402

# Optional HRR (same soft-degrade as Python Store)
try:
    import karmazyn_hrr as _hrr

    _HAS_HRR = True
except Exception:
    _hrr = None
    _HAS_HRR = False

VEC_DIM = 2048

T_INIT = 50.0
T_MAX = 100.0
T_HOT = 70.0
T_WARM = 30.0
T_TOMB = 2.0


def state_for_t(t: float) -> str:
    if t >= T_HOT:
        return "HOT"
    if t >= T_WARM:
        return "WARM"
    if t >= T_TOMB:
        return "COLD"
    return "TOMB"


# ── Core backend protocol ────────────────────────────────────────────────────


class _Core(Protocol):
    backend_name: str

    def atom_new(self, s: str, e: str, t: float) -> int: ...
    def atom_set_value(self, aid: int, token: int) -> bool: ...
    def atom_value(self, aid: int) -> int: ...
    def has_atom(self, aid: int) -> bool: ...
    def delete_atom(self, aid: int) -> bool: ...
    def heat(self, aid: int) -> bool: ...
    def atom_t(self, aid: int) -> float: ...
    def atom_set_t(self, aid: int, t: float) -> bool: ...
    def atom_is_dead(self, aid: int) -> int: ...
    def atom_ids(self) -> List[int]: ...
    def atom_upsert(self, aid: int, s: str, e: str, t: float, token: int) -> bool: ...
    def restore_atoms(
        self, entries: List[Tuple[int, str, str, float, int]]
    ) -> None: ...
    def bubble_new(self, label: str, parent: Optional[int]) -> int: ...
    def bind(self, bid: int, name: str, aid: int) -> bool: ...
    def unbind(self, bid: int, name: str) -> Optional[int]: ...
    def lookup(self, bid: int, name: str) -> Optional[int]: ...
    def set_root(self, bid: int) -> None: ...
    def unset_root(self, bid: int) -> None: ...
    def tick(self) -> None: ...
    def settle(self, n: int) -> None: ...
    def stats(self) -> dict: ...
    def register_env_of(self, cb: Callable[[int], int]) -> None: ...
    def unregister_env_of(self) -> None: ...
    def register_extra_reach(self, cb: Callable[[], List[int]]) -> None: ...
    def unregister_extra_reach(self) -> None: ...
    def close(self) -> None: ...
    def version(self) -> str: ...


# ── PyO3 core ────────────────────────────────────────────────────────────────


class _PyO3Core:
    backend_name = "pyo3"

    def __init__(self, thermal: bool = True):
        import karmazyn_substrate_rs as rs

        self._rs = rs
        self._s = rs.CoreStore(bool(thermal))
        self._env_cb = None
        self._extra_cb = None

    def version(self) -> str:
        return self._rs.CoreStore.version()

    def atom_new(self, s: str, e: str, t: float) -> int:
        return int(self._s.atom_new(s, e, float(t)))

    def atom_set_value(self, aid: int, token: int) -> bool:
        return bool(self._s.atom_set_value(int(aid), int(token)))

    def atom_value(self, aid: int) -> int:
        return int(self._s.atom_value(int(aid)) or 0)

    def has_atom(self, aid: int) -> bool:
        return bool(self._s.has_atom(int(aid)))

    def delete_atom(self, aid: int) -> bool:
        return bool(self._s.delete_atom(int(aid)))

    def heat(self, aid: int) -> bool:
        return bool(self._s.heat(int(aid)))

    def atom_t(self, aid: int) -> float:
        return float(self._s.atom_t(int(aid)))

    def atom_set_t(self, aid: int, t: float) -> bool:
        return bool(self._s.atom_set_t(int(aid), float(t)))

    def atom_is_dead(self, aid: int) -> int:
        return int(self._s.atom_is_dead(int(aid)))

    def atom_ids(self) -> List[int]:
        return [int(x) for x in self._s.atom_ids()]

    def atom_upsert(self, aid: int, s: str, e: str, t: float, token: int) -> bool:
        return bool(
            self._s.atom_upsert(int(aid), str(s), str(e), float(t), int(token))
        )

    def restore_atoms(self, entries: List[Tuple[int, str, str, float, int]]) -> None:
        self._s.restore_atoms(
            [(int(i), str(s), str(e), float(t), int(tok)) for i, s, e, t, tok in entries]
        )

    def bubble_new(self, label: str, parent: Optional[int]) -> int:
        p = None if parent is None else int(parent)
        return int(self._s.bubble_new(label, p))

    def bind(self, bid: int, name: str, aid: int) -> bool:
        try:
            self._s.bind(int(bid), name, int(aid))
            return True
        except Exception:
            return False

    def unbind(self, bid: int, name: str) -> Optional[int]:
        r = self._s.unbind(int(bid), name)
        return int(r) if r is not None else None

    def lookup(self, bid: int, name: str) -> Optional[int]:
        r = self._s.lookup(int(bid), name)
        return int(r) if r is not None else None

    def set_root(self, bid: int) -> None:
        self._s.set_root(int(bid))

    def unset_root(self, bid: int) -> None:
        self._s.unset_root(int(bid))

    def tick(self) -> None:
        self._s.tick()

    def settle(self, n: int) -> None:
        self._s.settle(int(n))

    def stats(self) -> dict:
        return dict(self._s.stats())

    def register_env_of(self, cb: Callable[[int], int]) -> None:
        # CoreStore expects callback(token) -> bubble_id | None
        def _wrap(token: int):
            bid = cb(int(token))
            return int(bid) if bid else None

        self._env_cb = _wrap
        self._s.register_env_of(_wrap)

    def unregister_env_of(self) -> None:
        self._env_cb = None
        self._s.unregister_env_of()

    def register_extra_reach(self, cb: Callable[[], List[int]]) -> None:
        def _wrap():
            try:
                return [int(x) for x in (cb() or [])]
            except Exception:
                return []

        self._extra_cb = _wrap
        self._s.register_extra_reach(_wrap)

    def unregister_extra_reach(self) -> None:
        self._extra_cb = None
        self._s.unregister_extra_reach()

    def close(self) -> None:
        # CoreStore dropped by GC; clear hooks first
        try:
            self.unregister_env_of()
            self.unregister_extra_reach()
        except Exception:
            pass
        self._s = None


# ── ctypes C ABI core ────────────────────────────────────────────────────────


class _KSubStats(Structure):
    _fields_ = [
        ("total", c_uint64),
        ("hot", c_uint64),
        ("warm", c_uint64),
        ("cold", c_uint64),
        ("tomb", c_uint64),
        ("alive", c_uint64),
        ("dead", c_uint64),
        ("reaped", c_uint64),
        ("retained_tomb", c_uint64),
        ("bubbles", c_uint64),
    ]


_EnvOfC = CFUNCTYPE(c_uint32, c_uint64, c_void_p)
_ExtraC = CFUNCTYPE(c_uint32, POINTER(c_uint32), c_uint32, c_void_p)
_ctypes_lib = None


def _candidate_libs():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, "karmazyn_substrate", "target")
    if sys.platform == "win32":
        names = ["karmazyn_substrate.dll"]
    elif sys.platform == "darwin":
        names = ["libkarmazyn_substrate.dylib"]
    else:
        names = ["libkarmazyn_substrate.so"]
    for profile in ("release", "debug"):
        for n in names:
            yield os.path.join(root, profile, n)
    for n in names:
        yield n


def _load_ctypes_lib():
    global _ctypes_lib
    if _ctypes_lib is not None:
        return _ctypes_lib
    last = None
    for path in _candidate_libs():
        if not os.path.isfile(path) and not os.path.isabs(path):
            try:
                lib = ctypes.CDLL(path)
                break
            except OSError as e:
                last = e
                continue
        if os.path.isfile(path):
            try:
                lib = ctypes.CDLL(path)
                break
            except OSError as e:
                last = e
                continue
    else:
        raise ImportError(
            "native karmazyn_substrate DLL not found — build with: "
            "cargo build --release  (last error: %s)" % last
        )
    lib.ksub_version.restype = c_char_p
    lib.ksub_store_new.argtypes = [c_int]
    lib.ksub_store_new.restype = c_uint64
    lib.ksub_store_free.argtypes = [c_uint64]
    lib.ksub_atom_new.argtypes = [c_uint64, c_char_p, c_char_p, c_double]
    lib.ksub_atom_new.restype = c_uint32
    lib.ksub_atom_set_value.argtypes = [c_uint64, c_uint32, c_uint64]
    lib.ksub_atom_set_value.restype = c_int
    lib.ksub_atom_value.argtypes = [c_uint64, c_uint32]
    lib.ksub_atom_value.restype = c_uint64
    lib.ksub_has_atom.argtypes = [c_uint64, c_uint32]
    lib.ksub_has_atom.restype = c_int
    lib.ksub_delete_atom.argtypes = [c_uint64, c_uint32]
    lib.ksub_delete_atom.restype = c_int
    lib.ksub_heat.argtypes = [c_uint64, c_uint32]
    lib.ksub_heat.restype = c_int
    lib.ksub_atom_t.argtypes = [c_uint64, c_uint32]
    lib.ksub_atom_t.restype = c_double
    lib.ksub_atom_set_t.argtypes = [c_uint64, c_uint32, c_double]
    lib.ksub_atom_set_t.restype = c_int
    lib.ksub_atom_is_dead.argtypes = [c_uint64, c_uint32]
    lib.ksub_atom_is_dead.restype = c_int
    lib.ksub_atom_upsert.argtypes = [
        c_uint64,
        c_uint32,
        c_char_p,
        c_char_p,
        c_double,
        c_uint64,
    ]
    lib.ksub_atom_upsert.restype = c_int
    lib.ksub_atom_ids.argtypes = [c_uint64, POINTER(c_uint32), c_uint32]
    lib.ksub_atom_ids.restype = c_int
    lib.ksub_bubble_new.argtypes = [c_uint64, c_char_p, c_int64]
    lib.ksub_bubble_new.restype = c_uint32
    lib.ksub_bind.argtypes = [c_uint64, c_uint32, c_char_p, c_uint32]
    lib.ksub_bind.restype = c_int
    lib.ksub_lookup.argtypes = [c_uint64, c_uint32, c_char_p]
    lib.ksub_lookup.restype = c_int64
    lib.ksub_unbind.argtypes = [c_uint64, c_uint32, c_char_p]
    lib.ksub_unbind.restype = c_int64
    lib.ksub_set_root.argtypes = [c_uint64, c_uint32]
    lib.ksub_unset_root.argtypes = [c_uint64, c_uint32]
    lib.ksub_tick.argtypes = [c_uint64]
    lib.ksub_settle.argtypes = [c_uint64, c_uint32]
    lib.ksub_stats.argtypes = [c_uint64, POINTER(_KSubStats)]
    lib.ksub_stats.restype = c_int
    lib.ksub_register_env_of.argtypes = [c_uint64, c_void_p, c_void_p]
    lib.ksub_register_env_of.restype = c_int
    lib.ksub_register_extra_reach.argtypes = [c_uint64, c_void_p, c_void_p]
    lib.ksub_register_extra_reach.restype = c_int
    _ctypes_lib = lib
    return lib


class _CtypesCore:
    backend_name = "ctypes"

    def __init__(self, thermal: bool = True):
        self._lib = _load_ctypes_lib()
        self._h = self._lib.ksub_store_new(1 if thermal else 0)
        if not self._h:
            raise RuntimeError("ksub_store_new failed")
        self._c_env = None
        self._c_extra = None
        self._py_env = None
        self._py_extra = None

    def version(self) -> str:
        v = self._lib.ksub_version()
        return v.decode("utf-8", errors="replace") if v else ""

    def atom_new(self, s: str, e: str, t: float) -> int:
        aid = self._lib.ksub_atom_new(
            self._h, str(s).encode("utf-8"), str(e).encode("utf-8"), float(t)
        )
        if aid == 0xFFFFFFFF:
            raise RuntimeError("atom_new failed")
        return int(aid)

    def atom_set_value(self, aid: int, token: int) -> bool:
        return bool(self._lib.ksub_atom_set_value(self._h, int(aid), int(token)))

    def atom_value(self, aid: int) -> int:
        return int(self._lib.ksub_atom_value(self._h, int(aid)) or 0)

    def has_atom(self, aid: int) -> bool:
        return bool(self._lib.ksub_has_atom(self._h, int(aid)))

    def delete_atom(self, aid: int) -> bool:
        return bool(self._lib.ksub_delete_atom(self._h, int(aid)))

    def heat(self, aid: int) -> bool:
        return bool(self._lib.ksub_heat(self._h, int(aid)))

    def atom_t(self, aid: int) -> float:
        return float(self._lib.ksub_atom_t(self._h, int(aid)))

    def atom_set_t(self, aid: int, t: float) -> bool:
        return bool(self._lib.ksub_atom_set_t(self._h, int(aid), float(t)))

    def atom_is_dead(self, aid: int) -> int:
        return int(self._lib.ksub_atom_is_dead(self._h, int(aid)))

    def atom_ids(self) -> List[int]:
        # first call with null/0 may not return total; grow buffer
        buf = (c_uint32 * 64)()
        n = int(self._lib.ksub_atom_ids(self._h, buf, 64))
        if n < 0:
            return []
        if n > 64:
            buf = (c_uint32 * n)()
            n = int(self._lib.ksub_atom_ids(self._h, buf, n))
        return [int(buf[i]) for i in range(max(0, n))]

    def atom_upsert(self, aid: int, s: str, e: str, t: float, token: int) -> bool:
        return bool(
            self._lib.ksub_atom_upsert(
                self._h,
                int(aid),
                str(s).encode("utf-8"),
                str(e).encode("utf-8"),
                float(t),
                int(token),
            )
        )

    def restore_atoms(self, entries: List[Tuple[int, str, str, float, int]]) -> None:
        keep = {int(i) for i, *_ in entries}
        for aid in self.atom_ids():
            if aid not in keep:
                self.delete_atom(aid)
        for aid, s, e, t, tok in entries:
            self.atom_upsert(int(aid), str(s), str(e), float(t), int(tok))

    def bubble_new(self, label: str, parent: Optional[int]) -> int:
        p = -1 if parent is None else int(parent)
        bid = self._lib.ksub_bubble_new(self._h, str(label).encode("utf-8"), p)
        if bid == 0xFFFFFFFF:
            raise RuntimeError("bubble_new failed")
        return int(bid)

    def bind(self, bid: int, name: str, aid: int) -> bool:
        return bool(
            self._lib.ksub_bind(
                self._h, int(bid), str(name).encode("utf-8"), int(aid)
            )
        )

    def unbind(self, bid: int, name: str) -> Optional[int]:
        r = self._lib.ksub_unbind(self._h, int(bid), str(name).encode("utf-8"))
        return None if r < 0 else int(r)

    def lookup(self, bid: int, name: str) -> Optional[int]:
        r = self._lib.ksub_lookup(self._h, int(bid), str(name).encode("utf-8"))
        return None if r < 0 else int(r)

    def set_root(self, bid: int) -> None:
        self._lib.ksub_set_root(self._h, int(bid))

    def unset_root(self, bid: int) -> None:
        self._lib.ksub_unset_root(self._h, int(bid))

    def tick(self) -> None:
        self._lib.ksub_tick(self._h)

    def settle(self, n: int) -> None:
        self._lib.ksub_settle(self._h, int(n))

    def stats(self) -> dict:
        st = _KSubStats()
        if not self._lib.ksub_stats(self._h, byref(st)):
            raise RuntimeError("stats failed")
        return {
            "total": int(st.total),
            "HOT": int(st.hot),
            "WARM": int(st.warm),
            "COLD": int(st.cold),
            "TOMB": int(st.tomb),
            "alive": int(st.alive),
            "dead": int(st.dead),
            "hot": int(st.alive),
            "cold": int(st.dead),
            "reaped": int(st.reaped),
            "retained_tomb": int(st.retained_tomb),
            "archived": int(st.retained_tomb),
            "bubbles": int(st.bubbles),
        }

    def register_env_of(self, cb: Callable[[int], int]) -> None:
        self._py_env = cb

        def _c(token, _ud):
            try:
                return int(cb(int(token)) or 0)
            except Exception:
                return 0

        self._c_env = _EnvOfC(_c)
        self._lib.ksub_register_env_of(self._h, self._c_env, None)

    def unregister_env_of(self) -> None:
        self._py_env = None
        self._c_env = None
        self._lib.ksub_register_env_of(self._h, None, None)

    def register_extra_reach(self, cb: Callable[[], List[int]]) -> None:
        self._py_extra = cb

        def _c(out, max_out, _ud):
            try:
                raw = cb() or []
            except Exception:
                return 0
            n = 0
            for x in raw:
                if n >= max_out:
                    break
                out[n] = int(x)
                n += 1
            return n

        self._c_extra = _ExtraC(_c)
        self._lib.ksub_register_extra_reach(self._h, self._c_extra, None)

    def unregister_extra_reach(self) -> None:
        self._py_extra = None
        self._c_extra = None
        self._lib.ksub_register_extra_reach(self._h, None, None)

    def close(self) -> None:
        if getattr(self, "_h", 0):
            self._lib.ksub_store_free(self._h)
            self._h = 0


def _open_core(thermal: bool = True) -> _Core:
    """Prefer PyO3; fall back to ctypes C ABI."""
    # Force ctypes for debugging: KARMAZYN_NATIVE_BRIDGE=ctypes
    force = os.environ.get("KARMAZYN_NATIVE_BRIDGE", "").strip().lower()
    if force not in ("ctypes", "c", "dll"):
        try:
            return _PyO3Core(thermal=thermal)
        except Exception:
            pass
    return _CtypesCore(thermal=thermal)


def native_bridge() -> str:
    """Which Rust bridge is currently preferred/available: pyo3 | ctypes | none."""
    force = os.environ.get("KARMAZYN_NATIVE_BRIDGE", "").strip().lower()
    if force in ("ctypes", "c", "dll"):
        try:
            _load_ctypes_lib()
            return "ctypes"
        except Exception:
            return "none"
    try:
        import karmazyn_substrate_rs  # noqa: F401

        return "pyo3"
    except Exception:
        try:
            _load_ctypes_lib()
            return "ctypes"
        except Exception:
            return "none"


def native_available() -> bool:
    return native_bridge() != "none"


def native_version() -> str:
    try:
        c = _open_core(True)
        try:
            return c.version()
        finally:
            c.close()
    except Exception as e:
        return f"error: {e}"


# ── Drop-in Atom / Bubble / Store ────────────────────────────────────────────


class _ValueMeta(dict):
    __slots__ = ("_store", "_aid")

    def __init__(self, store: "NativeStore", aid: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._store = store
        self._aid = int(aid)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "v":
            self._store._set_value_payload(self._aid, value)


class NativeAtom:
    """Atom z publicznym string id (DB_karmin / KAFD / KarminQL) i int _aid w Rust."""

    __slots__ = ("id", "_aid", "_store", "S", "E", "metadata", "vector")

    def __init__(
        self,
        store: "NativeStore",
        aid: int,
        s: str = "",
        e: str = "",
        metadata: Optional[dict] = None,
        *,
        public_id: Optional[str] = None,
    ):
        self._aid = int(aid)
        # Publiczne id: string jak w Python Store (a0, custom, cell_…)
        self.id = str(public_id) if public_id is not None else str(int(aid))
        self._store = store
        self.S = s
        self.E = e
        self.vector = None
        self.metadata = _ValueMeta(store, self._aid, metadata or {})

    @property
    def T(self) -> float:
        return float(self._store._core.atom_t(self._aid))

    @T.setter
    def T(self, value: float) -> None:
        self._store._core.atom_set_t(self._aid, float(value))

    @property
    def state(self) -> str:
        return state_for_t(self.T)

    def is_dead(self) -> bool:
        return self._store._core.atom_is_dead(self._aid) == 1

    def touch(self):
        self._store.heat(self)

    def heat(self, amount: float = 10.0):
        n = max(1, int(round(float(amount) / 10.0))) if amount else 1
        for _ in range(n):
            self._store.heat(self)

    def _update_state(self):
        """Kompatybilność z Python Store (FSM po zmianie T)."""
        return self.state

    def on_state_change(self, callback):
        """No-op kompatybilny z Python Atom — eventy idą przez Store.tick/EventBus."""
        return None

    def __repr__(self):
        return f"<NativeAtom {self.id!r} core={self._aid} T={self.T:.1f} state={self.state}>"


class NativeBubble(Bubble):
    # bid (str) — stabilny id bąbla dla Lunety (__dom_seq), osobno od id (int) w Rust
    __slots__ = ("id", "bid")

    def __init__(
        self,
        store: "NativeStore",
        bid: int,
        label: str = "",
        parent: Optional["NativeBubble"] = None,
    ):
        self.store = store
        self.label = label
        self.parent = parent
        self.bindings = {}  # name -> public string atom id (jak Python Store)
        self.id = int(bid)
        self.bid = f"b{self.id}"

    def bind(self, name, atom):
        if atom is None or getattr(atom, "id", None) is None:
            raise TypeError("bind wymaga Atom z polem id")
        with self.store.lock:
            if not self.store.has_atom(atom):
                raise ValueError(f"bind: atom {atom.id!r} nie należy do tego Store")
            core_aid = self.store._aid(atom)
            public = str(atom.id)
            self.bindings[name] = public
            if not self.store._core.bind(self.id, str(name), int(core_aid)):
                self.bindings.pop(name, None)
                raise ValueError(f"bind failed: {name!r} -> {public}")

    def unbind(self, name):
        with self.store.lock:
            sid = self.bindings.pop(name, None)
            self.store._core.unbind(self.id, str(name))
            return sid

    def lookup(self, name):
        with self.store.lock:
            b = self
            while b is not None:
                sid = b.bindings.get(name)
                if sid is not None:
                    atom = self.store.get_atom(sid)
                    if atom is None:
                        b.bindings.pop(name, None)
                        b = b.parent
                        continue
                    if self.store.thermal:
                        self.store.heat(atom)
                    return atom
                b = b.parent
            return None


class _NativeRegView:
    """Kompatybilność wsteczna store.reg dla klientów (lore-editor, skrypty).

    Nie omija GC — delete idzie przez Store.delete_atom. Nowy kod: atoms()/get_atom.
    """

    __slots__ = ("_store",)

    def __init__(self, store: "NativeStore"):
        self._store = store

    def atoms(self):
        return self._store.atoms()

    def get(self, aid):
        return self._store.get_atom(aid)

    def has(self, aid):
        return self._store.has_atom(aid)

    def delete(self, aid):
        return self._store.delete_atom(aid)

    def __len__(self):
        return len(self._store.atoms())


class NativeStore:
    """Drop-in Store: Rust GC core + Python language surface (+ HRR).

    Publiczne id atomów są **stringami** (kontrakt DB_karmin / KAFD / KarminQL).
    Rdzeń Rust trzyma int; mapowanie sid↔aid jest w tej warstwie.
    """

    _reg_warned = False

    def __init__(
        self,
        thermal: bool = True,
        env_of=None,
        extra_reach=None,
        decay=None,
        tick_event_mode: str = "both",
        **_kw,
    ):
        self._core = _open_core(thermal=thermal)
        self.thermal = thermal
        self.lock = threading.RLock()
        self.events = EventBus()
        self.bubbles: List[NativeBubble] = []
        self.roots: List[NativeBubble] = []
        self._atoms: Dict[int, NativeAtom] = {}  # core aid → atom
        self._sid_to_aid: Dict[str, int] = {}
        self._aid_to_sid: Dict[int, str] = {}
        self._n = 0  # generator id a0, a1, … (jak Python Store)
        self._bubbles_by_id: Dict[int, NativeBubble] = {}
        self._pin: Dict[int, Any] = {}
        self._env_hooks: List[Tuple[str, Callable]] = []
        self._extra_hooks: List[Tuple[str, Callable]] = []
        self.reaped = 0
        self._tick_count = 0
        self.tick_event_mode = tick_event_mode
        self.native_backend = self._core.backend_name  # pyo3 | ctypes
        self.substrate = "native"
        self._reg_view = _NativeRegView(self)
        if env_of is not None:
            self.register_env_of(env_of, name="init")
        if extra_reach is not None:
            self.register_extra_reach(extra_reach, name="init")

    @property
    def reg(self):
        """Alias wsteczny (lore / stare skrypty). Preferuj atoms()/delete_atom."""
        import warnings

        if not NativeStore._reg_warned:
            warnings.warn(
                "Store.reg jest wewnętrzny — użyj atoms()/get_atom/has_atom/"
                "delete_atom/heat. (NativeStore: widok kompatybilny z DB_karmin)",
                UserWarning,
                stacklevel=2,
            )
            NativeStore._reg_warned = True
        return self._reg_view

    def close(self):
        if getattr(self, "_core", None) is not None:
            self._core.close()
            self._core = None  # type: ignore

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _aid(self, aid) -> int:
        """Rozwiąż publiczne id / atom / int → core aid (Rust)."""
        if isinstance(aid, NativeAtom):
            return int(aid._aid)
        raw = getattr(aid, "id", aid)
        if isinstance(raw, NativeAtom):
            return int(raw._aid)
        if hasattr(aid, "_aid") and not isinstance(aid, (str, bytes, int)):
            try:
                return int(getattr(aid, "_aid"))
            except Exception:
                pass
        if isinstance(raw, str):
            if raw in self._sid_to_aid:
                return int(self._sid_to_aid[raw])
            # rzadki fallback: czysty int jako string
            if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
                return int(raw)
            raise KeyError(f"nieznany atom id {raw!r}")
        return int(raw)

    def _register_sid(self, sid: str, core_aid: int) -> None:
        self._sid_to_aid[str(sid)] = int(core_aid)
        self._aid_to_sid[int(core_aid)] = str(sid)

    def _forget_core(self, core_aid: int) -> None:
        sid = self._aid_to_sid.pop(int(core_aid), None)
        if sid is not None:
            self._sid_to_aid.pop(sid, None)
        self._atoms.pop(int(core_aid), None)

    def _alloc_sid(self) -> str:
        sid = f"a{self._n}"
        self._n += 1
        return sid

    def _note_manual_sid(self, sid: str) -> None:
        """Utrzymaj generator aN powyżej ręcznych id (jak Python Store)."""
        if isinstance(sid, str) and len(sid) > 1 and sid[0] == "a" and sid[1:].isdigit():
            n = int(sid[1:])
            if n >= self._n:
                self._n = n + 1

    def _token_for(self, value) -> int:
        if value is None:
            return 0
        if isinstance(value, NativeBubble):
            return int(value.id)
        if isinstance(value, Bubble) and hasattr(value, "id"):
            return int(value.id)
        tok = id(value) & 0xFFFFFFFFFFFFFFFF
        self._pin[tok] = value
        return tok

    def _set_value_payload(self, aid: int, value) -> None:
        token = self._token_for(value)
        self._core.atom_set_value(int(aid), int(token))

    def atom_new(self, S: str, E: str = "", T: float = T_INIT, value=None) -> NativeAtom:
        t = float(T)
        if t != t:
            t = T_INIT
        with self.lock:
            core_aid = self._core.atom_new(str(S), str(E), t)
            sid = self._alloc_sid()
            self._register_sid(sid, core_aid)
            atom = NativeAtom(self, core_aid, str(S), str(E), public_id=sid)
            atom.metadata["v"] = value
            self._atoms[core_aid] = atom
        self.events.emit("atom_created", atom)
        return atom

    def get_atom(self, aid) -> Optional[NativeAtom]:
        with self.lock:
            try:
                a = self._aid(aid)
            except (KeyError, TypeError, ValueError):
                return None
            if not self._core.has_atom(a):
                self._forget_core(a)
                return None
            atom = self._atoms.get(a)
            if atom is None:
                sid = self._aid_to_sid.get(a, str(a))
                atom = NativeAtom(self, a, public_id=sid)
                self._atoms[a] = atom
                self._register_sid(sid, a)
            return atom

    def has_atom(self, aid) -> bool:
        try:
            return bool(self._core.has_atom(self._aid(aid)))
        except (KeyError, TypeError, ValueError):
            return False

    def atoms(self, state=None):
        with self.lock:
            live = []
            dead_keys = []
            for aid, atom in self._atoms.items():
                if self._core.has_atom(aid):
                    live.append(atom)
                else:
                    dead_keys.append(aid)
            for k in dead_keys:
                self._forget_core(k)
        if state is None:
            return live
        s = str(state).upper()
        return [a for a in live if a.state == s]

    def heat(self, atom):
        self._core.heat(self._aid(atom))

    def delete_atom(self, aid) -> bool:
        with self.lock:
            try:
                a = self._aid(aid)
            except (KeyError, TypeError, ValueError):
                return False
            atom = self.get_atom(a)
            if atom is None:
                return False
            public = atom.id
            ok = bool(self._core.delete_atom(a))
            if ok:
                self._forget_core(a)
                self._purge_py_bindings(public)
        if ok:
            self.events.emit("atom_deleted", atom)
        return ok

    def _purge_py_bindings(self, public_or_core):
        """Usuń name→id w bąblach (public string id lub core aid)."""
        targets = {public_or_core, str(public_or_core)}
        if isinstance(public_or_core, int):
            sid = self._aid_to_sid.get(public_or_core)
            if sid is not None:
                targets.add(sid)
        elif isinstance(public_or_core, str) and public_or_core in self._sid_to_aid:
            targets.add(self._sid_to_aid[public_or_core])
        for b in self.bubbles:
            if not b.bindings:
                continue
            dead = [n for n, i in b.bindings.items() if i in targets or str(i) in targets]
            for n in dead:
                del b.bindings[n]

    def _sync_after_gc(self, reaped_before: int):
        with self.lock:
            st = self.stats()
            self.reaped = int(st.get("reaped", self.reaped))
            dead = [
                aid for aid in list(self._atoms) if not self._core.has_atom(aid)
            ]
            for aid in dead:
                sid = self._aid_to_sid.get(aid, str(aid))
                self._forget_core(aid)
                self._purge_py_bindings(sid)
                if self.reaped > reaped_before:
                    self.events.emit("vacuum_decay", None)
            for b in self.bubbles:
                if not b.bindings:
                    continue
                gone = []
                for n, sid in b.bindings.items():
                    try:
                        core = self._aid(sid)
                    except (KeyError, TypeError, ValueError):
                        gone.append(n)
                        continue
                    if not self._core.has_atom(core):
                        gone.append(n)
                for n in gone:
                    del b.bindings[n]

    def bubble_new(
        self, label: str = "", parent: Optional[NativeBubble] = None
    ) -> NativeBubble:
        p = None if parent is None else int(parent.id)
        with self.lock:
            bid = self._core.bubble_new(str(label), p)
            b = NativeBubble(self, bid, label, parent)
            self.bubbles.append(b)
            self._bubbles_by_id[bid] = b
            return b

    def get_bubble(self, label):
        with self.lock:
            for b in self.bubbles:
                if b.label == label:
                    return b
        return None

    def set_root(self, b: NativeBubble):
        with self.lock:
            self._core.set_root(int(b.id))
            if b not in self.roots:
                self.roots.append(b)

    def unset_root(self, b: NativeBubble):
        with self.lock:
            self._core.unset_root(int(b.id))
            try:
                self.roots.remove(b)
            except ValueError:
                pass

    @property
    def tick_count(self):
        return self._tick_count

    def tick(self):
        """Tick: GC w Rust + eventy S12 (per_atom / batch / both) jak Python Store."""
        before = self.reaped
        mode = self.tick_event_mode
        emit_per = mode in ("per_atom", "both")
        emit_batch = mode in ("batch", "both")
        with self.lock:
            self._tick_count += 1
            # snapshot atomów przed GC (do dual-emit "tick")
            pre_atoms = list(self.atoms()) if emit_per else []
            self._core.tick()
            self._sync_after_gc(before)
            reaped_now = max(0, self.reaped - before)
            retained = int(self.stats().get("retained_tomb", 0))
            n_atoms = len([a for a in self._atoms if self._core.has_atom(a)])
        if emit_per:
            for atom in pre_atoms:
                self.events.emit("tick", atom)
        if emit_batch:
            self.events.emit(
                "tick_batch",
                {
                    "atoms": n_atoms,
                    "reaped": reaped_now,
                    "retained": retained,
                },
            )

    def settle(self, n: int):
        for _ in range(int(n)):
            self.tick()

    def stats(self) -> dict:
        return self._core.stats()

    # ── HRR (phase 4) — same law as Python Store ─────────────────────────────
    def atom_vector(self, atom):
        if not _HAS_HRR or not getattr(atom, "E", None):
            return None
        with self.lock:
            if atom.vector is None:
                atom.vector = _hrr.name_to_vector(atom.E, VEC_DIM)
                atom.metadata["_vec_E"] = atom.E
            elif "_vec_E" in atom.metadata and atom.metadata["_vec_E"] != atom.E:
                atom.vector = _hrr.name_to_vector(atom.E, VEC_DIM)
                atom.metadata["_vec_E"] = atom.E
            return atom.vector

    def resonance(self, query, k=5, threshold=0.1):
        if not _HAS_HRR:
            return []
        use_surface = not hasattr(query, "shape")
        qv = None if use_surface else query
        with self.lock:
            atoms = list(self.atoms())
        hits = []
        for atom in atoms:
            e = getattr(atom, "E", None) or ""
            sname = getattr(atom, "S", None) or ""
            if not e and not sname:
                continue
            if use_surface:
                s = _hrr.surface_similarity(query, e)
                if sname:
                    s = max(s, _hrr.surface_similarity(query, sname))
            else:
                v = self.atom_vector(atom)
                if v is None:
                    continue
                s = _hrr.similarity(qv, v)
            if s >= threshold:
                hits.append((s, atom.id))
        hits.sort(key=lambda x: -x[0])
        return hits[:k]

    def create_atom(self, id, S, E, T=T_INIT, **kwargs):
        """AtomStore: create_atom(id, S, E, T, **kw) -> public string id.

        Zachowuje jawne id (KAFD / gossip / DB_karmin). GC w Rust po int.
        """
        sid = str(id)
        value = kwargs.pop("value", None)
        meta = kwargs.pop("metadata", None) or {}
        vector = kwargs.pop("vector", None)
        t = float(T)
        if t != t:
            t = T_INIT
        with self.lock:
            if sid in self._sid_to_aid:
                raise ValueError(f"atom o id {sid!r} już istnieje")
            # value: jawny kwargs > meta["v"] > None
            if value is None and "v" in meta:
                value = meta.get("v")
            core_aid = self._core.atom_new(str(S), str(E), t)
            self._register_sid(sid, core_aid)
            self._note_manual_sid(sid)
            atom = NativeAtom(
                self, core_aid, str(S), str(E), metadata=dict(meta), public_id=sid
            )
            if vector is not None:
                atom.vector = vector
            if value is not None or "v" not in atom.metadata:
                atom.metadata["v"] = value
            if kwargs:
                atom.metadata.update(kwargs)
            self._atoms[core_aid] = atom
        self.events.emit("atom_created", atom)
        return sid

    def create_bubble(self, label, atom_ids=None, root=False):
        with self.lock:
            b = self.get_bubble(label)
            if b is None:
                b = self.bubble_new(label)
            if root:
                self.set_root(b)
            if atom_ids:
                for aid in atom_ids:
                    atom = self.get_atom(aid)
                    if atom is not None:
                        name = atom.E if atom.E else str(aid)
                        b.bind(name, atom)
        return label

    def import_to_bubble(self, bubble_label, atom_id):
        with self.lock:
            b = self.get_bubble(bubble_label)
            atom = self.get_atom(atom_id)
            if b is None or atom is None:
                return False
            name = atom.E if atom.E else str(atom_id)
            b.bind(name, atom)
            return True

    def snapshot_atoms(self):
        """Mapa public_id(str) → NativeAtom (jak Python Store / transakcje KAFD)."""
        with self.lock:
            out = {}
            for core_aid, atom in list(self._atoms.items()):
                if self._core.has_atom(core_aid):
                    out[atom.id] = atom
            return out

    def restore_atoms(self, atoms, temperatures=None):
        """Przywróć rejestr atomów ze snapshotu (rollback transakcji).

        atoms: dict public_id→Atom (string keys, jak z snapshot_atoms / Python).
        temperatures: opcjonalnie {id: T}.
        """
        with self.lock:
            entries: List[Tuple[int, str, str, float, int]] = []
            new_map: Dict[int, NativeAtom] = {}
            new_sid: Dict[str, int] = {}
            new_aid_sid: Dict[int, str] = {}
            for raw_id, atom in dict(atoms).items():
                sid = str(getattr(atom, "id", raw_id))
                # Preferuj istniejący core aid z mapy / _aid atomu
                if isinstance(atom, NativeAtom):
                    core_aid = int(atom._aid)
                elif str(raw_id) in self._sid_to_aid:
                    core_aid = int(self._sid_to_aid[str(raw_id)])
                elif hasattr(atom, "_aid"):
                    core_aid = int(atom._aid)
                else:
                    # nowy slot w rdzeniu
                    core_aid = self._core.atom_new(
                        str(getattr(atom, "S", "") or ""),
                        str(getattr(atom, "E", "") or ""),
                        T_INIT,
                    )
                s = str(getattr(atom, "S", "") or "")
                e = str(getattr(atom, "E", "") or "")
                if temperatures is not None and raw_id in temperatures:
                    t = float(temperatures[raw_id])
                elif temperatures is not None and sid in temperatures:
                    t = float(temperatures[sid])
                else:
                    try:
                        t = float(getattr(atom, "T"))
                    except Exception:
                        t = T_INIT
                meta = getattr(atom, "metadata", None) or {}
                value = meta.get("v") if hasattr(meta, "get") else None
                token = self._token_for(value) if value is not None else 0
                if token == 0 and self._core.has_atom(core_aid):
                    try:
                        token = int(self._core.atom_value(core_aid) or 0)
                    except Exception:
                        token = 0
                entries.append((core_aid, s, e, t, int(token)))
                if isinstance(atom, NativeAtom):
                    atom._store = self
                    atom._aid = core_aid
                    atom.id = sid
                    atom.S = s
                    atom.E = e
                    if not isinstance(atom.metadata, _ValueMeta):
                        atom.metadata = _ValueMeta(self, core_aid, dict(meta))
                    else:
                        atom.metadata._store = self
                        atom.metadata._aid = core_aid
                    new_map[core_aid] = atom
                else:
                    na = NativeAtom(
                        self, core_aid, s, e, metadata=dict(meta), public_id=sid
                    )
                    new_map[core_aid] = na
                new_sid[sid] = core_aid
                new_aid_sid[core_aid] = sid
                self._note_manual_sid(sid)
            self._core.restore_atoms(entries)
            self._atoms = new_map
            self._sid_to_aid = new_sid
            self._aid_to_sid = new_aid_sid

    def sync_id_counter(self) -> int:
        """Po wczytaniu .kafd podnieś generator aN (string id)."""
        with self.lock:
            max_n = -1
            for sid in self._sid_to_aid:
                if len(sid) > 1 and sid[0] == "a" and sid[1:].isdigit():
                    max_n = max(max_n, int(sid[1:]))
            if max_n >= 0:
                self._n = max(self._n, max_n + 1)
            return self._n

    def register_env_of(self, fn: Callable[[Any], Any], *, name: str = "guest"):
        self._env_hooks = [(n, f) for n, f in self._env_hooks if n != name]
        self._env_hooks.insert(0, (name, fn))
        self._install_env_cb()
        return self

    def unregister_env_of(self, name: str = "guest"):
        self._env_hooks = [(n, f) for n, f in self._env_hooks if n != name]
        self._install_env_cb()
        return self

    def register_extra_reach(self, fn: Callable[[], Any], *, name: str = "guest"):
        self._extra_hooks = [(n, f) for n, f in self._extra_hooks if n != name]
        self._extra_hooks.insert(0, (name, fn))
        self._install_extra_cb()
        return self

    def unregister_extra_reach(self, name: str = "guest"):
        self._extra_hooks = [(n, f) for n, f in self._extra_hooks if n != name]
        self._install_extra_cb()
        return self

    def hook_names(self):
        return {
            "env_of": [n for n, _ in self._env_hooks],
            "extra_reach": [n for n, _ in self._extra_hooks],
        }

    def _dispatch_env_of(self, obj):
        for _name, fn in self._env_hooks:
            try:
                env = fn(obj)
            except Exception:
                continue
            if env is not None:
                return env
        return None

    def _install_env_cb(self):
        store = self

        def _cb(token: int) -> int:
            if token < 0x100000:
                b = store._bubbles_by_id.get(int(token))
                if b is not None:
                    return int(b.id)
            obj = store._pin.get(token)
            if obj is None and token < 0x100000:
                return int(token)
            if obj is None:
                return 0
            try:
                env = store._dispatch_env_of(obj)
            except Exception:
                return 0
            if env is None:
                return 0
            bid = getattr(env, "id", None)
            return int(bid) if bid is not None else 0

        if self._env_hooks:
            self._core.register_env_of(_cb)
        else:
            self._core.unregister_env_of()

    def _install_extra_cb(self):
        store = self

        def _cb() -> List[int]:
            ids: List[int] = []
            for _name, fn in store._extra_hooks:
                try:
                    raw = fn() if fn else ()
                except Exception:
                    continue
                for x in raw or ():
                    ids.append(int(getattr(x, "id", x)))
            return ids

        if self._extra_hooks:
            self._core.register_extra_reach(_cb)
        else:
            self._core.unregister_extra_reach()


def smoke() -> str:
    s = NativeStore(thermal=True)
    try:
        bridge = s.native_backend
        orphan = s.atom_new("var", "orphan", value=42)
        assert orphan.metadata["v"] == 42
        s.settle(80)
        assert not s.has_atom(orphan.id), "orphan should be reaped"
        root = s.bubble_new("root")
        s.set_root(root)
        keep = s.atom_new("var", "keep", value="x")
        root.bind("keep", keep)
        assert isinstance(root, Bubble)
        s.settle(80)
        assert s.has_atom(keep.id)
        assert keep.is_dead()
        keep.metadata["v"] = "y"
        assert s.get_atom(keep.id).metadata["v"] == "y"
        # snapshot / restore rollback
        s2 = NativeStore(thermal=False)
        try:
            a0 = s2.atom_new("var", "snap0", value=1)
            snap = s2.snapshot_atoms()
            temps = {aid: a.T for aid, a in snap.items()}
            a1 = s2.atom_new("var", "snap1", value=2)
            assert s2.has_atom(a1.id)
            s2.restore_atoms(snap, temps)
            assert s2.has_atom(a0.id)
            assert not s2.has_atom(a1.id)
        finally:
            s2.close()
        # HRR soft path
        named = s.atom_new("var", "hello_world")
        root.bind("n", named)
        hits = s.resonance("hello_world", k=3)
        st = s.stats()
        hrr = "hrr_on" if _HAS_HRR else "hrr_off"
        return (
            f"OK native bridge={bridge} ver={native_version()!r} "
            f"{hrr} restore_ok hits={len(hits)} stats={st}"
        )
    finally:
        s.close()


if __name__ == "__main__":
    if not native_available():
        print(
            "native missing — build:\n"
            "  cd native/karmazyn_substrate && cargo build --release\n"
            "  cd native/karmazyn_substrate_rs && python -m maturin build --release\n"
            "  pip install target/wheels/*.whl"
        )
        sys.exit(1)
    print(smoke())
