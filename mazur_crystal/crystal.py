"""MRC — historia R (ślady), nie kopie atomów."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ResonanceTrace:
    atom_id: str
    epoch: int
    R: float


class MazurCrystal:
    def __init__(
        self,
        beta: float = 0.005,
        lambda_R: float = 0.2,
        lambda_M: float = 0.8,
        max_len: int = 256,
        R_trace: float = 0.05,
    ):
        self.beta = float(beta)
        self.lambda_R = float(lambda_R)
        self.lambda_M = float(lambda_M)
        self.max_len = int(max_len)
        self.R_trace = float(R_trace)
        self.epoch = 0
        self._traces: DefaultDict[str, List[Tuple[int, float]]] = defaultdict(list)

    def advance(self) -> int:
        self.epoch += 1
        return self.epoch

    def record(
        self,
        atom_id: str,
        R: float,
        epoch: Optional[int] = None,
        *,
        force: bool = False,
    ) -> Optional[ResonanceTrace]:
        ep = self.epoch if epoch is None else int(epoch)
        r = float(R)
        if not force and r < self.R_trace:
            return None
        buf = self._traces[str(atom_id)]
        buf.append((ep, r))
        if len(buf) > self.max_len:
            del buf[: len(buf) - self.max_len]
        return ResonanceTrace(str(atom_id), ep, r)

    def record_many(
        self, scores: Iterable[Tuple[str, float]], epoch: Optional[int] = None
    ) -> None:
        for aid, r in scores:
            self.record(aid, r, epoch=epoch)

    def traces(self, atom_id: str) -> List[ResonanceTrace]:
        return [
            ResonanceTrace(str(atom_id), ep, r)
            for ep, r in self._traces.get(str(atom_id), ())
        ]

    def forget(self, atom_id: str) -> None:
        self._traces.pop(str(atom_id), None)

    def M(self, atom_id: str, t: Optional[int] = None) -> float:
        buf = self._traces.get(str(atom_id))
        if not buf:
            return 0.0
        t_now = self.epoch if t is None else int(t)
        beta = self.beta
        num = 0.0
        for ep, r in buf:
            age = max(0, t_now - ep)
            num += float(r) * math.exp(-beta * age)
        t0 = buf[0][0]
        if t_now < t0:
            return 0.0
        n = t_now - t0
        if beta <= 1e-15:
            den = float(n + 1)
        else:
            den = (1.0 - math.exp(-beta * (n + 1))) / (1.0 - math.exp(-beta))
        if den <= 1e-15:
            return 0.0
        return max(0.0, min(1.0, num / den))

    def Lambda(self, R_now: float, M_now: float) -> float:
        return self.lambda_R * float(R_now) + self.lambda_M * float(M_now)

    def Lambda_of(self, atom_id: str, R_now: float, t: Optional[int] = None) -> float:
        return self.Lambda(R_now, self.M(atom_id, t=t))

    def stats(self) -> Dict[str, float]:
        return {
            "epoch": float(self.epoch),
            "atoms_with_history": float(len(self._traces)),
            "traces": float(sum(len(v) for v in self._traces.values())),
            "beta": self.beta,
            "lambda_R": self.lambda_R,
            "lambda_M": self.lambda_M,
        }
