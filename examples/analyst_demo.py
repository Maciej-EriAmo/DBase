#!/usr/bin/env python3
"""Przykład analityka: KarminQL + read_karmin() → pandas."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine
from cynober_pandas_bridge import read_karmin

SCRIPT = """
UTRWAL "Zam_A"
WSTRZYKNIJ "Sku" = "P1" DO "Zam_A"
WSTRZYKNIJ "Qty" = 3 DO "Zam_A"
UTRWAL "Kat_P1"
WSTRZYKNIJ "Sku" = "P1" DO "Kat_P1"
WSTRZYKNIJ "Cena" = 9.99 DO "Kat_P1"
WSTRZYKNIJ "Typ" = "Katalog" DO "Kat_P1"
"""

QUERY = (
    'WYPISZ "BĄBEL", "Qty", Katalog.Cena, Katalog.Cena * "Qty" AS "Wartość" '
    'GDZIE "Qty" > 0 '
    'DOŁĄCZ Z (ZNAJDŹ GDZIE "Typ" = "Katalog") JAKO "Katalog" GDZIE "Sku" = "Sku"'
)


def main():
    engine = KarminEngine(kernel.Store(thermal=True))
    engine.execute(SCRIPT)
    print("=== OPISZ BAZĘ ===")
    print(read_karmin(engine, "OPISZ BAZĘ").to_string(index=False))
    print("\n=== JOIN + wyrażenia ===")
    df = read_karmin(engine, QUERY)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()