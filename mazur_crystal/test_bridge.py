"""Smoke: LorentzBridge na Python Store (+ native jeśli DLL jest)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (
    ROOT,
    ROOT / "archiwum" / "kernel_python",
    ROOT / "software",
    ROOT / "native",
):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

os.environ.setdefault("KARMAZYN_SUBSTRATE", "python")
os.environ["KARMAZYN_MAZUR"] = "1"

from mazur_crystal import (  # noqa: E402
    LorentzBridge,
    Tracer,
    attach_lorentz_bridge,
    get_tracer,
    resonance_score,
    set_tracer,
)


def _open_python():
    from karmazyn_substrate import Store

    return Store(thermal=True)


class TestLorentzBridgePython(unittest.TestCase):
    def test_attach_and_find(self):
        raw = _open_python()
        s = attach_lorentz_bridge(raw, mrc=True)
        self.assertIsInstance(s, LorentzBridge)
        s.create_atom(
            "ctx", "c", "system active context pulse", T=100.0, tracer=Tracer(energy=1.0)
        )
        root = s.bubble_new("root")
        s.set_root(root)
        root.bind("c", s.get_atom("ctx"))
        s.set_context("ctx")
        s.create_atom(
            "hot",
            "m",
            "system active context memory",
            T=50.0,
            tracer=Tracer(energy=1.0),
        )
        s.create_atom(
            "cold", "m", "orthogonal noise", T=50.0, tracer=Tracer(energy=9.0)
        )
        hits = s.find_resonating(limit=5)
        ids = [a.id for a in hits]
        self.assertIn("hot", ids)
        self.assertNotIn("cold", ids)

    def test_gc_retains_resonating(self):
        s = attach_lorentz_bridge(_open_python(), mrc=False)
        s.create_atom(
            "ctx", "c", "shared motif text", T=100.0, tracer=Tracer(energy=1.0)
        )
        root = s.bubble_new("root")
        s.set_root(root)
        root.bind("c", s.get_atom("ctx"))
        s.set_context("ctx")
        s.create_atom(
            "keep", "m", "shared motif text", T=1.5, tracer=Tracer(energy=1.0)
        )
        s.create_atom("drop", "m", "nope", T=1.5, tracer=Tracer(energy=8.0))
        s.tick()
        self.assertTrue(s.has_atom("keep"))
        self.assertFalse(s.has_atom("drop"))

    def test_host_similarity_path(self):
        s = attach_lorentz_bridge(_open_python(), mrc=True)
        s.create_atom("a", "x", "alpha beta gamma", tracer=1.0)
        s.create_atom("b", "x", "alpha beta gamma", tracer=1.0)
        a, b = s.get_atom("a"), s.get_atom("b")
        self.assertGreater(resonance_score(a, b, store=s), 1.0)

    def test_mrc_default_on(self):
        s = attach_lorentz_bridge(_open_python())
        self.assertIsNotNone(s.mrc)


class TestHostBindings(unittest.TestCase):
    def test_recall_uses_lorentz_layer(self):
        from karmazyn_host import KarmazynHost

        class _FakeEv:
            store = None

            def _tbl(self):
                return {}

            def _arr(self, xs):
                return xs

            def _set(self, t, k, v):
                t[k] = v

        s = attach_lorentz_bridge(_open_python(), mrc=True)
        s.create_atom("ctx", "c", "hello world pulse", T=100.0, tracer=1.0)
        root = s.bubble_new("root")
        s.set_root(root)
        root.bind("c", s.get_atom("ctx"))
        s.set_context("ctx")
        s.create_atom("m1", "m", "hello world memory", T=50.0, tracer=1.0)

        # minimal host without full Lua evaluator
        host = KarmazynHost.__new__(KarmazynHost)
        host.store = s
        host.ev = None
        host._id_alias = {}
        host._phi_ids = set()
        host._tbl = lambda: {}
        host._arr = lambda xs: xs
        host._set = lambda t, k, v: t.__setitem__(k, v)
        host._store_get = s.get_atom
        host._store_has = s.has_atom
        host._resolve_aid = lambda x: x
        host._public_id = lambda x: str(x)
        host._coerce_tracer = KarmazynHost._coerce_tracer.__get__(host, KarmazynHost)

        rows = host.recall("hello world", 5)
        self.assertTrue(rows)
        self.assertEqual(rows[0].get("layer"), "lorentz")


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    # native optional
    try:
        from karmazyn_backend import native_available, open_store

        if native_available():

            class TestNative(unittest.TestCase):
                def test_wrap_native(self):
                    os.environ["KARMAZYN_SUBSTRATE"] = "native"
                    raw = open_store(thermal=True)
                    s = attach_lorentz_bridge(raw, mrc=True)
                    self.assertTrue(s.stats().get("lorentz_bridge"))
                    s.create_atom("nctx", "c", "shared motif text", T=100.0, tracer=1.0)
                    # native id = u32; ustaw context po real id
                    atom = None
                    for a in s.atoms():
                        if (a.metadata or {}).get("_requested_id") == "nctx":
                            atom = a
                            break
                    self.assertIsNotNone(atom)
                    root = s.bubble_new("root")
                    s.set_root(root)
                    root.bind("c", atom)
                    s.set_context(str(atom.id))
                    set_tracer(atom, 1.0)
                    self.assertAlmostEqual(get_tracer(atom).energy, 1.0)

            suite.addTests(
                unittest.defaultTestLoader.loadTestsFromTestCase(TestNative)
            )
    except Exception:
        pass

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
