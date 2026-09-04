"""
LorentzBridge — most na dowolny Store (Python lub Native).

Composition, nie dziedziczenie: NativeStore zostaje królem prawa GC;
most dokłada tracer, R, retencję extra_reach i domyślnie MRC (pamięć).
"""

from __future__ import annotations

import os
from typing import Any, List, Optional, Tuple, Union

from .lorentz import content_similarity, resonance_score
from .crystal import MazurCrystal
from .tracer import Tracer, get_tracer, set_tracer


def mazur_enabled() -> bool:
    """
    Domyślnie WŁĄCZONY.
    Wyłączenie: KARMAZYN_MAZUR=0|false|off|no
    """
    raw = os.environ.get("KARMAZYN_MAZUR", "1").strip().lower()
    return raw not in ("0", "false", "off", "no", "disable", "disabled")


class LorentzBridge:
    """
    Proxy Store-like:
      - create_atom(..., tracer=)
      - find_resonating / resonance_R / score / memory / relevance
      - tick: epoka MRC + retencja przez extra_reach('mazur_crystal_R')
      - resonance(): tor Lorentz (HRR: resonance_hrr)
    """

    __slots__ = (
        "_inner",
        "g",
        "R_min",
        "R_retain",
        "context_id",
        "use_hrr",
        "_resonance_retain",
        "mrc",
        "_bridge_on",
    )

    def __init__(
        self,
        inner: Any,
        *,
        g: float = 0.3,
        R_min: float = 0.15,
        R_retain: Optional[float] = None,
        context_id: Optional[str] = None,
        use_hrr: bool = False,
        mrc: bool = True,
        crystal: Optional[MazurCrystal] = None,
        beta: float = 0.005,
        lambda_R: float = 0.2,
        lambda_M: float = 0.8,
    ):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "g", float(g))
        object.__setattr__(self, "R_min", float(R_min))
        object.__setattr__(
            self, "R_retain", float(R_retain if R_retain is not None else R_min)
        )
        object.__setattr__(self, "context_id", context_id)
        object.__setattr__(self, "use_hrr", bool(use_hrr))
        object.__setattr__(self, "_resonance_retain", True)
        object.__setattr__(self, "_bridge_on", True)
        if mrc:
            cry = crystal or MazurCrystal(
                beta=beta, lambda_R=lambda_R, lambda_M=lambda_M
            )
            object.__setattr__(self, "mrc", cry)
        else:
            object.__setattr__(self, "mrc", None)

        # Retencja — osobna nazwa haka, nie koliduje z Lua/exec "guest"
        if hasattr(inner, "register_extra_reach"):
            try:
                inner.register_extra_reach(
                    self._resonant_reach_ids, name="mazur_crystal_R"
                )
            except TypeError:
                # starsze API bez name=
                inner.register_extra_reach(self._resonant_reach_ids)

    # ── delegacja ─────────────────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        # __slots__: znane pola mostu lokalnie; reszta (np. _env_of z exec) → inner
        if name in LorentzBridge.__slots__:
            object.__setattr__(self, name, value)
            return
        inner = object.__getattribute__(self, "_inner")
        setattr(inner, name, value)

    @property
    def inner(self) -> Any:
        return self._inner

    def unwrap(self) -> Any:
        return self._inner

    # ── API Mazur ─────────────────────────────────────────────────────────────

    def create_atom(self, id, S, E, T=None, **kwargs):
        tracer = kwargs.pop("tracer", None)
        if T is None:
            try:
                from karmazyn_atom import T_INIT

                T = T_INIT
            except Exception:
                T = 50.0
        ret = self._inner.create_atom(id, S, E, T, **kwargs)
        # native: ret = u32 / string sid; python: często string id
        aid = ret if ret is not None else id
        atom = self._inner.get_atom(aid)
        if atom is None and isinstance(id, str):
            atom = self._find_by_requested(id)
        if atom is not None:
            if tracer is not None:
                set_tracer(atom, tracer)
            elif self.context_id is not None:
                # sesja z kontekstem → domyślny tracer (MRC / d_E)
                set_tracer(atom, Tracer(energy=1.0))
        return ret

    def atom_new(self, *args, **kwargs):
        """KarminQL path (add_property) — doklej tracer gdy jest kontekst MRC."""
        tracer = kwargs.pop("tracer", None)
        atom = self._inner.atom_new(*args, **kwargs)
        if atom is None:
            return atom
        if tracer is not None:
            set_tracer(atom, tracer)
        elif self.context_id is not None:
            set_tracer(atom, Tracer(energy=1.0))
        return atom

    def _find_by_requested(self, logical: str):
        try:
            for a in self._inner.atoms():
                md = getattr(a, "metadata", None) or {}
                if md.get("_requested_id") == logical:
                    return a
        except Exception:
            pass
        return None

    def set_context(self, atom_or_id: Union[str, Any]) -> str:
        if hasattr(atom_or_id, "id"):
            cid = str(atom_or_id.id)
        else:
            cid = str(atom_or_id)
        object.__setattr__(self, "context_id", cid)
        return cid

    def enable_resonance_retention(self, enabled: bool = True) -> None:
        object.__setattr__(self, "_resonance_retain", bool(enabled))

    def enable_mrc(
        self,
        enabled: bool = True,
        *,
        beta: float = 0.005,
        lambda_R: float = 0.2,
        lambda_M: float = 0.8,
        crystal: Optional[MazurCrystal] = None,
    ) -> Optional[MazurCrystal]:
        if not enabled:
            object.__setattr__(self, "mrc", None)
            return None
        if crystal is not None:
            object.__setattr__(self, "mrc", crystal)
        elif self.mrc is None:
            object.__setattr__(
                self,
                "mrc",
                MazurCrystal(beta=beta, lambda_R=lambda_R, lambda_M=lambda_M),
            )
        return self.mrc

    def score(self, atom: Any, probe: Any = None) -> float:
        probe = probe if probe is not None else self._probe()
        if probe is None:
            return 0.0
        return resonance_score(
            atom, probe, g=self.g, store=self._inner, use_hrr=self.use_hrr
        )

    def memory(self, atom_or_id: Any) -> float:
        if self.mrc is None:
            return 0.0
        aid = atom_or_id.id if hasattr(atom_or_id, "id") else atom_or_id
        return self.mrc.M(str(aid))

    def relevance(self, atom: Any, probe: Any = None) -> float:
        r = self.score(atom, probe=probe)
        if self.mrc is None:
            return r
        return self.mrc.Lambda_of(str(atom.id), r)

    def _probe(self) -> Any:
        if not self.context_id:
            return None
        a = self._inner.get_atom(self.context_id)
        if a is None:
            a = self._find_by_requested(self.context_id)
        return a

    def _record_mrc_epoch(self) -> None:
        if self.mrc is None:
            return
        probe = self._probe()
        self.mrc.advance()
        if probe is None:
            return
        batch = []
        for atom in self._inner.atoms():
            if str(atom.id) == str(probe.id):
                continue
            batch.append(
                (
                    str(atom.id),
                    resonance_score(
                        atom,
                        probe,
                        g=self.g,
                        store=self._inner,
                        use_hrr=self.use_hrr,
                    ),
                )
            )
        self.mrc.record_many(batch)

    def tick(self):
        self._record_mrc_epoch()
        return self._inner.tick()

    def settle(self, n):
        for _ in range(int(n)):
            self.tick()

    def _resonant_reach_ids(self):
        if not self._resonance_retain:
            return []
        probe = self._probe()
        if probe is None:
            return []
        out = []
        for atom in self._inner.atoms():
            if str(atom.id) == str(probe.id):
                continue
            r = resonance_score(
                atom, probe, g=self.g, store=self._inner, use_hrr=self.use_hrr
            )
            score = (
                self.mrc.Lambda_of(str(atom.id), r) if self.mrc is not None else r
            )
            if score >= self.R_retain:
                out.append(atom.id)
        return out

    def find_resonating(
        self,
        concept: Any = None,
        T_min: float = 0.0,
        limit: int = 10,
        R_min: Optional[float] = None,
    ) -> List[Any]:
        probe = self._resolve_probe(concept)
        if probe is None:
            return []
        thr = self.R_min if R_min is None else float(R_min)
        hits: List[Tuple[float, Any]] = []
        for atom in self._inner.atoms():
            if str(atom.id) == str(getattr(probe, "id", None)):
                continue
            if float(getattr(atom, "T", 0.0)) < T_min:
                continue
            r = resonance_score(
                atom, probe, g=self.g, store=self._inner, use_hrr=self.use_hrr
            )
            if r >= thr:
                hits.append((r, atom))
        hits.sort(key=lambda x: -x[0])
        return [a for _, a in hits[:limit]]

    def _resolve_probe(self, concept: Any) -> Any:
        if concept is None:
            return self._probe()
        if hasattr(concept, "id") and hasattr(concept, "E"):
            return concept

        class _Probe:
            pass

        p = _Probe()
        p.id = "__probe__"
        p.E = str(concept)
        p.metadata = {"_ignore_dE": True}  # recall po tekście = tylko V, bez dE
        ctx = self._probe()
        if ctx is not None:
            set_tracer(p, get_tracer(ctx))
            # przy kontekście energetycznym włącz dE (sonda systemu)
            p.metadata.pop("_ignore_dE", None)
        else:
            set_tracer(p, Tracer(energy=0.0))
        return p

    def resonance(self, query=None, k=5, threshold=None):
        """Domyślny tor: Lorentz R. HRR: resonance_hrr()."""
        return self.resonance_R(query, k=k, threshold=threshold)

    def resonance_R(self, query=None, k=5, threshold=None) -> List[Tuple[float, Any]]:
        thr = self.R_min if threshold is None else float(threshold)
        atoms = self.find_resonating(query, T_min=0.0, limit=k, R_min=thr)
        probe = self._resolve_probe(query)
        out = []
        for a in atoms:
            out.append(
                (
                    resonance_score(
                        a, probe, g=self.g, store=self._inner, use_hrr=self.use_hrr
                    ),
                    a.id,
                )
            )
        return out

    def resonance_hrr(self, query, k=5, threshold=0.1):
        return self._inner.resonance(query, k=k, threshold=threshold)

    def content_V(self, atom: Any, probe: Any = None) -> float:
        probe = probe if probe is not None else self._probe()
        if probe is None:
            return 0.0
        return content_similarity(
            atom, probe, store=self._inner, use_hrr=self.use_hrr
        )

    def stats(self):
        st = self._inner.stats() if callable(getattr(self._inner, "stats", None)) else {}
        if not isinstance(st, dict):
            st = {}
        else:
            st = dict(st)
        st["lorentz_bridge"] = True
        st["mrc"] = self.mrc is not None
        if self.mrc is not None:
            st["mrc_stats"] = self.mrc.stats()
        return st


def attach_lorentz_bridge(
    store: Any,
    *,
    mrc: bool = True,
    force: bool = False,
    **kwargs,
) -> Any:
    """
    Owiń Store mostem Lorentza (+ MRC domyślnie).
    Jeśli KARMAZYN_MAZUR wyłączony i force=False → zwróć store bez zmian.
    """
    if isinstance(store, LorentzBridge):
        return store
    if not force and not mazur_enabled():
        return store
    return LorentzBridge(store, mrc=mrc, **kwargs)
