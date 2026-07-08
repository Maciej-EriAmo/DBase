"""Testy KarminQL v6.5: funkcje stringowe i podzapytania skalarne."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser


def _last(results):
    return results[-1] if results else {}


class TestStringFunctions(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Nazwa" = "  Jan  " DO "A"\nWSTRZYKNIJ "Kod" = "AbCdE" DO "A"'
        )

    def test_trim(self):
        r = _last(self.engine.execute(
            'WYPISZ TRIM("Nazwa") JAKO "Czyste" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["Czyste"], "Jan")

    def test_upper_lower(self):
        r = _last(self.engine.execute(
            'WYPISZ UPPER("Kod") JAKO "Wielkie", LOWER("Kod") JAKO "Male" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["Wielkie"], "ABCDE")
        self.assertEqual(r["rows"][0]["Male"], "abcde")

    def test_length(self):
        r = _last(self.engine.execute(
            'WYPISZ LENGTH("Kod") JAKO "Len" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["Len"], 5)

    def test_substring(self):
        r = _last(self.engine.execute(
            'WYPISZ SUBSTRING("Kod", 2, 3) JAKO "Frag" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["Frag"], "bCd")

    def test_string_parser(self):
        node = KarminParser().parse(
            'SELECT TRIM("X") AS "T", SUBSTRING("X", 1, 2) AS "S" WHERE "BĄBEL" = "A"'
        )[0][2]
        self.assertEqual(node.columns[0].str_func, "trim")
        self.assertEqual(node.columns[1].str_func, "substring")


class TestScalarSubquery(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 2048 DO "A"\nWSTRZYKNIJ "Typ" = "Serwer" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 512 DO "B"\nWSTRZYKNIJ "Typ" = "Plik" DO "B"'
        )

    def test_scalar_aggregate(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", (SUMA "RAM" GDZIE "Typ" = "Serwer") JAKO "Suma_srv" '
            'GDZIE "Typ" = "Serwer"'
        ))
        self.assertEqual(r["rows"][0]["Suma_srv"], 2048)

    def test_scalar_correlated(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", (WYPISZ "RAM" GDZIE "BĄBEL" W $BĄBEL) JAKO "Self_ram" '
            'GDZIE "Typ" != "NIC"'
        ))
        by_name = {row["BĄBEL"]: row["Self_ram"] for row in r["rows"]}
        self.assertEqual(by_name["A"], 2048)
        self.assertEqual(by_name["B"], 512)


if __name__ == "__main__":
    unittest.main()