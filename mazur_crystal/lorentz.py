"""Jądro lorentzowskie: R = g·V²/(g²+d_E²). V domyślnie Jaccard po E."""

from __future__ import annotations

import re
from typing import Any, Optional

from .tracer import d_E

_TOKEN_RE = re.compile(r"[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def token_jaccard(a_text: str, b_text: str) -> float:
    a, b = _tokens(a_text), _tokens(b_text)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def content_similarity(
    atom: Any,
    probe: Any,
    store: Any = None,
    *,
    use_hrr: bool = False,
) -> float:
    if use_hrr and store is not None and hasattr(store, "atom_vector"):
        try:
            va = store.atom_vector(atom)
            vb = store.atom_vector(probe) if not hasattr(probe, "shape") else probe
            if va is not None and vb is not None and hasattr(vb, "shape"):
                import numpy as np

                na = float(np.linalg.norm(va))
                nb = float(np.linalg.norm(vb))
                if na > 1e-12 and nb > 1e-12:
                    sim = float(np.dot(va, vb) / (na * nb))
                    return max(0.0, min(1.0, sim))
        except Exception:
            pass

    a_e = getattr(atom, "E", "") or ""
    if hasattr(probe, "E"):
        b_e = probe.E or ""
    else:
        b_e = str(probe or "")
    return token_jaccard(a_e, b_e)


def R(V: float, dE: float, g: float = 0.3) -> float:
    g = max(float(g), 1e-9)
    return g * (float(V) ** 2) / (g * g + float(dE) ** 2)


def resonance_score(
    atom: Any,
    probe: Any,
    g: float = 0.3,
    store: Any = None,
    V: Optional[float] = None,
    *,
    use_hrr: bool = False,
) -> float:
    if V is None:
        V = content_similarity(atom, probe, store=store, use_hrr=use_hrr)
    # sonda tekstowa (recall query) — tylko treść, bez kary dE
    md = getattr(probe, "metadata", None) or {}
    if md.get("_ignore_dE"):
        return R(V, 0.0, g=g)
    return R(V, d_E(atom, probe), g=g)
