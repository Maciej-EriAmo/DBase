"""Testy KarminQL v6.6: funkcje okienkowe OVER i ALL/ANY."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, CondCompare


def _last(results):
    return results[-1] if results else {}


class TestWindowFunctions(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ WIELE "Typ" = "X", "Score" = 30 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ WIELE "Typ" = "X", "Score" = 10 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ WIELE "Typ" = "Y", "Score" = 20 DO "C"'
        )

    def test_row_number_partition(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Typ", "Score", '
            'ROW_NUMBER() OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" MALEJĄCO) JAKO "Rn" '
            'GDZIE "Typ" != "NIC"'
        ))
        by_babel = {row["BĄBEL"]: row["Rn"] for row in r["rows"]}
        self.assertEqual(by_babel["A"], 1)
        self.assertEqual(by_babel["B"], 2)
        self.assertEqual(by_babel["C"], 1)

    def test_rank_ties(self):
        self.engine.execute(
            'UTRWAL "D"\nWSTRZYKNIJ WIELE "Typ" = "Z", "Score" = 10 DO "D"\n'
            'UTRWAL "E"\nWSTRZYKNIJ WIELE "Typ" = "Z", "Score" = 10 DO "E"'
        )
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Score", '
            'RANK() OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "R" '
            'GDZIE "Typ" = "Z"'
        ))
        ranks = sorted(row["R"] for row in r["rows"])
        self.assertEqual(ranks, [1, 1])

    def test_dense_rank(self):
        self.engine.execute(
            'UTRWAL "D"\nWSTRZYKNIJ WIELE "Typ" = "Z", "Score" = 10 DO "D"\n'
            'UTRWAL "E"\nWSTRZYKNIJ WIELE "Typ" = "Z", "Score" = 10 DO "E"'
        )
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Score", '
            'DENSE_RANK() OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Dr" '
            'GDZIE "Typ" = "Z"'
        ))
        ranks = sorted(row["Dr"] for row in r["rows"])
        self.assertEqual(ranks, [1, 1])

    def test_window_parser_english(self):
        node = KarminParser().parse(
            'SELECT "BĄBEL", ROW_NUMBER() OVER (PARTITION BY "Typ" ORDER BY "Score" DESC) AS "Rn" '
            'WHERE "Typ" != "NIC"'
        )[0][2]
        self.assertEqual(node.columns[-1].win_func, "row_number")
        self.assertEqual(node.columns[-1].win_partition, ("Typ",))


class TestAllAny(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 2048 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 512 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "RAM" = 128 DO "C"'
        )

    def test_greater_than_all(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "RAM" > WSZYSTKIE (WYPISZ "RAM" GDZIE "RAM" < 1000)'
        ))
        self.assertEqual(r["matches"], ["A"])
        r2 = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "RAM" > WSZYSTKIE (WYPISZ "RAM" GDZIE "RAM" >= 2000)'
        ))
        self.assertEqual(r2["matches"], [])

    def test_greater_than_any(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "RAM" > DOWOLNE (WYPISZ "RAM" GDZIE "RAM" < 600)'
        ))
        self.assertEqual(sorted(r["matches"]), ["A", "B"])

    def test_all_parser_english(self):
        node = KarminParser().parse(
            'FIND WHERE "RAM" = ALL (SELECT "RAM" WHERE "RAM" > 0)'
        )[0][2]
        self.assertIsInstance(node.cond, CondCompare)
        self.assertEqual(node.cond.quantifier, "ALL")


if __name__ == "__main__":
    unittest.main()