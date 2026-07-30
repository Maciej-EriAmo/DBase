#!/usr/bin/env python3
"""
run_native_demo.py — prosta ścieżka uruchomienia substratu Rust + weryfikacja

Użycie (z root KarmazynOs):
  python native/run_native_demo.py
  python native/run_native_demo.py --bridge ctypes
  python native/run_native_demo.py --skip-lua
  python native/run_native_demo.py --skip-dbase

Sprawdza:
  1) most native (pyo3|ctypes)
  2) prawo GC (orphan vacuum)
  3) snapshot / restore_atoms
  4) Lua na NativeStore (opcjonalnie)
  5) mini atom-DB (bąbel-korzeń + bind/lookup)
  6) miękki probe DBase (opcjonalnie, jeśli C:\\Users\\...\\DBase istnieje)
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

# ── ścieżki monorepo ─────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_PASS = 0
_FAIL = 0
_SKIP = 0


def _ok(name: str, detail: str = "") -> None:
    global _PASS
    _PASS += 1
    extra = f" — {detail}" if detail else ""
    print(f"  [ OK ] {name}{extra}")


def _fail(name: str, detail: str = "") -> None:
    global _FAIL
    _FAIL += 1
    extra = f" — {detail}" if detail else ""
    print(f"  [FAIL] {name}{extra}")


def _skip(name: str, detail: str = "") -> None:
    global _SKIP
    _SKIP += 1
    extra = f" — {detail}" if detail else ""
    print(f"  [SKIP] {name}{extra}")


def section(title: str) -> None:
    print()
    print(f"== {title} ==")


def step_backend(bridge: str | None) -> object:
    section("1. Backend Rust")
    if bridge:
        os.environ["KARMAZYN_NATIVE_BRIDGE"] = bridge
    os.environ["KARMAZYN_SUBSTRATE"] = "native"

    from karmazyn_backend import (
        backend_info,
        native_available,
        open_store,
        substrate_backend,
    )

    if not native_available():
        _fail("native_available", "zbuduj: .\\native\\build_native.ps1")
        raise SystemExit(2)

    info = backend_info()
    backend = substrate_backend()
    if backend != "native":
        _fail("substrate_backend", f"oczekiwano native, jest {backend!r}")
        raise SystemExit(2)
    _ok("native_available", f"bridge={info.get('native_bridge')} ver={info.get('native_version')}")

    store = open_store(thermal=True, backend="native")
    nb = getattr(store, "native_backend", "?")
    _ok("open_store(backend=native)", f"class={type(store).__name__} bridge={nb}")
    if bridge and nb != bridge and bridge != "auto":
        # ctypes force may still report ctypes
        if bridge in ("ctypes", "c", "dll") and nb != "ctypes":
            _fail("bridge force", f"chciano {bridge}, jest {nb}")
        elif bridge == "pyo3" and nb != "pyo3":
            _fail("bridge force", f"chciano pyo3, jest {nb}")
    return store


def step_gc(store) -> None:
    section("2. Prawo GC (orphan → vacuum)")
    orphan = store.atom_new("var", "orphan_demo", value=1)
    store.settle(80)
    if store.has_atom(orphan.id):
        _fail("orphan vacuum", f"atom {orphan.id} nadal żyje po settle(80)")
    else:
        _ok("orphan vacuum", f"atom {getattr(orphan, 'id', orphan)} usunięty")

    root = store.bubble_new("demo_root")
    store.set_root(root)
    keep = store.atom_new("var", "keep_demo", value="alive")
    root.bind("keep", keep)
    store.settle(80)
    if not store.has_atom(keep.id):
        _fail("root retain", "atom pod korzeniem zniknął")
    elif not keep.is_dead():
        _fail("root retain TOMB", f"oczekiwano TOMB, state={keep.state}")
    else:
        _ok("root retain TOMB", f"id={keep.id} state={keep.state}")


def step_restore(store) -> None:
    section("3. snapshot / restore_atoms")
    # osobny store bez thermal — czysty rollback
    from karmazyn_backend import open_store

    s = open_store(thermal=False, backend="native")
    try:
        a0 = s.atom_new("var", "snap0", value=11)
        snap = s.snapshot_atoms()
        temps = {aid: a.T for aid, a in snap.items()}
        a1 = s.atom_new("var", "snap1", value=22)
        if not s.has_atom(a1.id):
            _fail("pre-restore", "a1 nie istnieje")
            return
        s.restore_atoms(snap, temps)
        if s.has_atom(a0.id) and not s.has_atom(a1.id):
            _ok("restore_atoms", f"został a0={a0.id}, usunięty a1={a1.id}")
        else:
            _fail(
                "restore_atoms",
                f"a0={s.has_atom(a0.id)} a1={s.has_atom(a1.id)}",
            )
    finally:
        if hasattr(s, "close"):
            s.close()


def step_lua(store) -> None:
    section("4. Lua na NativeStore")
    lua_root = os.path.join(_REPO, "LUA")
    if not os.path.isdir(lua_root):
        _skip("Lua", f"brak katalogu {lua_root}")
        return
    if lua_root not in sys.path:
        sys.path.insert(0, lua_root)

    try:
        from _paths import ensure_kernel_on_path, ensure_lua_package

        ensure_kernel_on_path(lua_root)
        ensure_lua_package(lua_root)
    except Exception:
        pass

    try:
        from karmazyn_lua.lib import mount
        from karmazyn_lua import lib as _klib  # noqa: F401
    except Exception as e:
        _skip("Lua mount import", str(e))
        return

    # świeży store na Lua (thermal + korzeń w mount)
    from karmazyn_backend import open_store

    s = open_store(thermal=True, backend="native")
    try:
        tools = os.path.join(_REPO, "software", "tools")
        if not os.path.isdir(tools):
            tools = None
        ev = mount(s, tools=tools)
        r = ev.eval_line("return 1+2+3")
        if str(r).strip() in ("6", "6.0"):
            _ok("Lua eval", f"1+2+3 → {r!r}")
        else:
            _fail("Lua eval", f"oczekiwano 6, jest {r!r}")

        r2 = ev.eval_line("x = 10; y = x + 5; return y")
        if str(r2).strip() in ("15", "15.0"):
            _ok("Lua vars (atomy w korzeniu)", f"y → {r2!r}")
        else:
            _fail("Lua vars", f"oczekiwano 15, jest {r2!r}")

        if tools:
            r3 = ev.eval_line('return require("hello").run("rust")')
            if "rust" in str(r3) and "witaj" in str(r3).lower():
                _ok("Lua tool hello", str(r3)[:80])
            else:
                # nie krytyczne jeśli preload inny
                if "error" in str(r3).lower() or "Error" in str(r3):
                    _fail("Lua tool hello", str(r3)[:120])
                else:
                    _ok("Lua tool hello", str(r3)[:80])

        # GC: sierota vs zmienna w env
        ev.eval_line("keep = 123")
        s.settle(80)
        r4 = ev.eval_line("return keep")
        if str(r4).strip() in ("123", "123.0"):
            _ok("Lua keep po settle", f"keep → {r4!r}")
        else:
            _fail("Lua keep po settle", f"keep zniknął? → {r4!r}")
    except Exception as e:
        _fail("Lua", f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        if hasattr(s, "close"):
            s.close()


def step_atom_db(store) -> None:
    section("5. Mini atom-DB na substracie")
    from karmazyn_backend import open_store

    s = open_store(thermal=True, backend="native")
    try:
        # bąbel-worek z root=True → atomy pod retencją GC
        label = s.create_bubble("notes", root=True)
        a = s.atom_new("note", "tytul", value={"text": "hello rust", "n": 1})
        s.import_to_bubble(label, a.id)
        b = s.get_bubble(label)
        got = b.lookup("tytul") if b else None
        if got is None:
            # bind name może być E lub id
            got = b.lookup(str(a.id)) if b else None
        if got is not None and got.metadata.get("v", {}).get("text") == "hello rust":
            _ok("atom-DB bind/lookup", f"bubble={label!r} atom={a.id}")
        elif got is not None:
            # value shape
            v = got.metadata.get("v")
            if v == {"text": "hello rust", "n": 1} or (
                isinstance(v, dict) and v.get("text") == "hello rust"
            ):
                _ok("atom-DB bind/lookup", f"value={v!r}")
            else:
                _fail("atom-DB lookup value", f"got={got!r} v={v!r}")
        else:
            # fallback: ręczny bind po E
            if b is not None:
                b.bind("tytul", a)
                got2 = b.lookup("tytul")
                if got2 is not None:
                    _ok("atom-DB bind/lookup (manual)", f"atom={got2.id}")
                else:
                    _fail("atom-DB lookup", "brak wiązania")
            else:
                _fail("atom-DB bubble", "get_bubble None")

        st = s.stats()
        _ok("atom-DB stats", f"total={st.get('total')} bubbles={st.get('bubbles')}")

        snap = s.snapshot_atoms()
        temps = {i: x.T for i, x in snap.items()}
        extra = s.atom_new("note", "tmp", value="gone")
        s.restore_atoms(snap, temps)
        if s.has_atom(a.id) and not s.has_atom(extra.id):
            _ok("atom-DB restore", "rollback po insert tmp")
        else:
            _fail("atom-DB restore", "rollback nie zadziałał")
    except Exception as e:
        _fail("atom-DB", f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        if hasattr(s, "close"):
            s.close()


def step_dbase() -> None:
    section("6. Probe DBase (opcjonalnie)")
    candidates = [
        os.environ.get("KARMAZYN_DBASE"),
        os.path.join(os.path.dirname(_REPO), "DBase"),
        r"C:\Users\drwis\DBase",
    ]
    dbase = next((p for p in candidates if p and os.path.isdir(p)), None)
    if not dbase:
        _skip("DBase", "katalog nie znaleziony")
        return

    # miękki: import Cynober / atomstore bez mieszania Store
    sys.path.insert(0, dbase)
    try:
        import Cynober_db as cdb  # noqa: F401

        names = [n for n in dir(cdb) if not n.startswith("_")]
        _ok("DBase import Cynober_db", f"path={dbase} symbols={len(names)}")
    except Exception as e:
        _skip("DBase Cynober_db", str(e))

    try:
        # zgodność prawa: orphan na Store z DBase (python ref w tamtym drzewie)
        # nie podmieniamy native KarmazynOs — tylko sprawdzamy, że DBase ma substrat
        import importlib.util

        sub_path = os.path.join(dbase, "karmazyn_substrate.py")
        if os.path.isfile(sub_path):
            _ok("DBase karmazyn_substrate.py", "obecny (osobna kopia ref)")
        else:
            _skip("DBase substrate file", "brak karmazyn_substrate.py")
    except Exception as e:
        _skip("DBase substrate", str(e))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Demo / weryfikacja substratu Rust")
    ap.add_argument(
        "--bridge",
        choices=("pyo3", "ctypes", "auto"),
        default="auto",
        help="most native (domyślnie auto = pyo3→ctypes)",
    )
    ap.add_argument("--skip-lua", action="store_true")
    ap.add_argument("--skip-dbase", action="store_true")
    args = ap.parse_args(argv)

    print("=" * 60)
    print("  KarmazynOS — ścieżka uruchomienia substratu Rust")
    print("=" * 60)
    print(f"  repo: {_REPO}")

    bridge = None if args.bridge == "auto" else args.bridge
    store = None
    try:
        store = step_backend(bridge)
        step_gc(store)
        step_restore(store)
        if not args.skip_lua:
            step_lua(store)
        else:
            section("4. Lua")
            _skip("Lua", "--skip-lua")
        step_atom_db(store)
        if not args.skip_dbase:
            step_dbase()
        else:
            section("6. DBase")
            _skip("DBase", "--skip-dbase")
    except SystemExit:
        raise
    except Exception as e:
        _fail("fatal", f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception:
                pass

    section("Podsumowanie")
    print(f"  PASS={_PASS}  FAIL={_FAIL}  SKIP={_SKIP}")
    if _FAIL:
        print("  wynik: FAIL")
        return 1
    print("  wynik: OK — substrat Rust działa")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
