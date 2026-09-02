#!/usr/bin/env python3
"""
cynober_lambda_bridge.py — most λ (karmazyn_exec) ↔ KarminQL (Cynober DB)
==========================================================================
Wspólny kernel.Store: ewaluator Lisp i KarminEngine widzą te same bąble.
Builtin (karmin "…") woła KarminQL z poziomu λ.
"""

from __future__ import annotations

from typing import Any, List, Optional

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine
from karmazyn_exec import Evaluator, _fmt


class KarminLambdaBridge:
    """Jeden Store, dwa wejścia: KarminQL (engine) i mini-Lisp (evaluator)."""

    def __init__(self, store: Optional[kernel.Store] = None):
        if store is None:
            try:
                from mazur_crystal import open_mazur_store

                store = open_mazur_store(thermal=True)
            except Exception:
                store = kernel.Store(thermal=True)
        self.store = store
        self.engine = KarminEngine(self.store)
        self.evaluator = Evaluator(self.store, env_label="__lambda__")
        self._last_karmin: List[dict] = []
        self.evaluator._builtins["karmin"] = self._builtin_karmin
        self.evaluator._builtins["karmin-raw"] = self._builtin_karmin_raw

    def is_lambda_line(self, line: str) -> bool:
        s = (line or "").strip()
        return bool(s) and s[0] == "("

    def eval_line(self, line: str) -> dict:
        out = self.evaluator.eval_line(line)
        if out.startswith("blad:"):
            return {"status": "error", "action": "LAMBDA", "message": out}
        return {"status": "ok", "action": "LAMBDA", "result": out}

    def execute_karmin(self, script: str, strict: bool = False) -> List[dict]:
        return self.engine.execute(script, strict=strict)

    def _builtin_karmin(self, script: Any) -> Any:
        if not isinstance(script, str):
            script = str(script)
        self._last_karmin = self.engine.execute(script, strict=False)
        return self._summarize_karmin(self._last_karmin)

    def _builtin_karmin_raw(self, script: Any) -> Any:
        if not isinstance(script, str):
            script = str(script)
        self._last_karmin = self.engine.execute(script, strict=False)
        return self._last_karmin

    @staticmethod
    def _summarize_karmin(results: List[dict]) -> Any:
        if not results:
            return []
        last = results[-1]
        if last.get("status") == "error":
            raise RuntimeError(last.get("message", "błąd KarminQL"))
        action = last.get("action", "")
        if action in ("FIND_WHERE", "FIND_REL", "SEARCH"):
            return list(last.get("matches", []))
        if action == "SET_OP" and last.get("matches") is not None:
            return list(last.get("matches", []))
        if action == "PROJECT_WHERE":
            return list(last.get("rows", []))
        if action == "COUNT_WHERE":
            return last.get("count", 0)
        if action == "INSERT_FROM":
            return list(last.get("created", []))
        if action == "EXPORT_CSV":
            return last.get("rows_written", 0)
        if action == "IMPORT_CSV":
            return list(last.get("created", []))
        if action.startswith("AGGREGATE_"):
            return last.get("result")
        if action == "SHOW":
            return last.get("data", {})
        return _fmt(last.get("result", last.get("count", last)))