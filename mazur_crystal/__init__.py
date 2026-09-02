"""Most Lorentza + Kryształ Mazura dla DBase / Cynober (standard jak KarmazynOs)."""

from .tracer import Tracer, d_E, get_tracer, set_tracer, tracer_energy
from .lorentz import R, content_similarity, resonance_score
from .crystal import MazurCrystal, ResonanceTrace
from .bridge import LorentzBridge, attach_lorentz_bridge, mazur_enabled

__all__ = [
    "Tracer",
    "get_tracer",
    "set_tracer",
    "tracer_energy",
    "d_E",
    "R",
    "content_similarity",
    "resonance_score",
    "MazurCrystal",
    "ResonanceTrace",
    "LorentzBridge",
    "attach_lorentz_bridge",
    "mazur_enabled",
    "open_mazur_store",
]


def open_mazur_store(thermal: bool = True, **kwargs):
    """
    Preferowany szew DBase: open_store → LorentzBridge (+ MRC domyślnie).
    KARMAZYN_MAZUR=0 → sam Store bez mostu.
    """
    try:
        from karmazyn_backend import open_store

        raw = open_store(thermal=thermal, **kwargs)
    except Exception:
        from karmazyn_substrate import Store

        raw = Store(thermal=thermal, **kwargs)
    return attach_lorentz_bridge(raw, mrc=True)
