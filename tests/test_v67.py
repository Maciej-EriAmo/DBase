"""Testy KarminQL v6.7: LAG/LEAD/FIRST_VALUE, NTILE, SUM OVER, ILIKE."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser


def _last(results):
    return results[-1] if results else {}


class TestWindowExtended(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ WIELE "Typ" = "X", "Score" = 30 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ WIELE "Typ" = "X", "Score" = 10 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ WIELE "Typ" = "Y", "Score" = 20 DO "C"'
        )

    def test_lag_lead(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Score", '
            'LAG("Score", 1) OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Prev", '
            'LEAD("Score", 1) OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Next" '
            'GDZIE "Typ" = "X"'
        ))
        by_babel = {row["BĄBEL"]: row for row in r["rows"]}
        self.assertIsNone(by_babel["B"]["Prev"])
        self.assertEqual(by_babel["B"]["Next"], 30)
        self.assertEqual(by_babel["A"]["Prev"], 10)
        self.assertIsNone(by_babel["A"]["Next"])

    def test_first_value(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", '
            'FIRST_VALUE("Score") OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "First" '
            'GDZIE "Typ" = "X"'
        ))
        for row in r["rows"]:
            self.assertEqual(row["First"], 10)

    def test_ntile(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", '
            'NTILE(2) OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Bucket" '
            'GDZIE "Typ" != "NIC"'
        ))
        buckets = sorted(row["Bucket"] for row in r["rows"])
        self.assertEqual(buckets, [1, 1, 2])

    def test_sum_over_running(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Score", '
            'SUM("Score") OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "RunSum" '
            'GDZIE "Typ" != "NIC"'
        ))
        run_sums = [row["RunSum"] for row in r["rows"] if row["BĄBEL"] == "B"]
        self.assertEqual(run_sums, [10])

    def test_sum_over_partition(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", '
            'SUM("Score") OVER (PODZIEL NA "Typ") JAKO "PartSum" '
            'GDZIE "Typ" != "NIC"'
        ))
        by_babel = {row["BĄBEL"]: row["PartSum"] for row in r["rows"]}
        self.assertEqual(by_babel["A"], 40)
        self.assertEqual(by_babel["B"], 40)
        self.assertEqual(by_babel["C"], 20)


class TestIlike(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Nazwa" = "Serwer_Alpha" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Nazwa" = "Plik_Beta" DO "B"'
        )

    def test_ilike_case_insensitive(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "Nazwa" ILIKE "serwer%"'
        ))
        self.assertEqual(r["matches"], ["A"])

    def test_not_ilike(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "Nazwa" NIE ILIKE "serwer%"'
        ))
        self.assertEqual(r["matches"], ["B"])

    def test_ilike_parser(self):
        node = KarminParser().parse('FIND WHERE "Nazwa" ILIKE "x%"')[0][2]
        self.assertEqual(node.cond.op, "PODOBNE")


if __name__ == "__main__":
    unittest.main()