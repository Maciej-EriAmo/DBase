#!/usr/bin/env python3
"""
cynober_pandas_bridge.py — read_karmin() → pandas DataFrame
"""

from __future__ import annotations

from typing import Any


def read_karmin(source: Any, query: str, *, strict: bool = False):
    """Wykonuje zapytanie KarminQL i zwraca pandas.DataFrame (WYPISZ … GDZIE, OPISZ BAZĘ, agregacje)."""
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("read_karmin wymaga pandas (pip install pandas)") from e

    if hasattr(source, "execute_karmin"):
        results = source.execute_karmin(query, strict=strict)
    elif hasattr(source, "execute"):
        results = source.execute(query, strict=strict)
    else:
        raise TypeError("source musi być KarminEngine lub KarminLambdaBridge")

    for r in reversed(results):
        if r.get("status") == "error":
            raise RuntimeError(r.get("message", "błąd KarminQL"))
        action = r.get("action", "")
        if action == "PROJECT_WHERE":
            rows = r.get("rows", [])
            cols = r.get("columns", list(rows[0].keys()) if rows else [])
            return pd.DataFrame(rows, columns=cols)
        if action == "DESCRIBE_DB":
            return pd.DataFrame(r.get("catalog_rows", []))
        if action.startswith("AGGREGATE_"):
            res = r.get("result")
            if isinstance(res, dict):
                gb = r.get("group_by")
                if gb:
                    label = ", ".join(gb) if isinstance(gb, list) else str(gb)
                    return pd.DataFrame([(k, v) for k, v in res.items()], columns=[label, "Wynik"])
                return pd.DataFrame([{"Wynik": res}])
            return pd.DataFrame([{"Wynik": res}])

    raise ValueError(
        "Zapytanie nie zwróciło tabelarycznego wyniku "
        "(użyj WYPISZ … GDZIE, OPISZ BAZĘ lub agregacji)"
    )