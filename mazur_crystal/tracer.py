"""Sonda energetyczna — metadata[\"tracer\"] (bez zmiany layoutu Atom w Rust)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping, Union

TRACER_KEY = "tracer"


@dataclass
class Tracer:
    energy: float = 0.0
    priority: float = 0.0
    level: int = 0
    group: str = ""
    pid: int = 0

    def normalized_energy(self) -> float:
        return float(self.energy)


def _coerce(value: Union[Tracer, Mapping[str, Any], float, int, None]) -> Tracer:
    if value is None:
        return Tracer()
    if isinstance(value, Tracer):
        return Tracer(
            energy=float(value.energy),
            priority=float(value.priority),
            level=int(value.level),
            group=str(value.group or ""),
            pid=int(value.pid),
        )
    if isinstance(value, (int, float)):
        return Tracer(energy=float(value))
    if isinstance(value, Mapping):
        allowed = {f.name for f in fields(Tracer)}
        kwargs = {k: value[k] for k in value if k in allowed}
        if "energy" in kwargs:
            kwargs["energy"] = float(kwargs["energy"])
        if "priority" in kwargs:
            kwargs["priority"] = float(kwargs["priority"])
        if "level" in kwargs:
            kwargs["level"] = int(kwargs["level"])
        if "pid" in kwargs:
            kwargs["pid"] = int(kwargs["pid"])
        if "group" in kwargs:
            kwargs["group"] = str(kwargs["group"])
        return Tracer(**kwargs)
    raise TypeError(f"nieobsługiwany tracer: {type(value)!r}")


def get_tracer(atom: Any) -> Tracer:
    md = getattr(atom, "metadata", None) or {}
    return _coerce(md.get(TRACER_KEY))


def set_tracer(atom: Any, tracer: Union[Tracer, Mapping[str, Any], float, int, None]) -> Tracer:
    t = _coerce(tracer)
    if getattr(atom, "metadata", None) is None:
        atom.metadata = {}
    atom.metadata[TRACER_KEY] = asdict(t)
    return t


def tracer_energy(atom: Any) -> float:
    return get_tracer(atom).normalized_energy()


def d_E(a: Any, b: Any) -> float:
    return abs(tracer_energy(a) - tracer_energy(b))


def has_tracer(atom: Any) -> bool:
    md = getattr(atom, "metadata", None) or {}
    return TRACER_KEY in md
