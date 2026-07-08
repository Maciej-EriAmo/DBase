"""Testy MIĘDZY (BETWEEN) i podzapytań KarminQL v5.2."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, CondCompare


def _last(results):
    return results[-1] if results else {}


class TestBetween(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 500 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "RAM" = 900 DO "C"'
        )

    def test_miedzy_numeric(self):
        r = self.engine.execute('ZNAJDŹ GDZIE "RAM" MIĘDZY 200 DO 800')
        self.assertEqual(_last(r)["matches"], ["B"])

    def test_miedzy_inclusive_bounds(self):
        r = self.engine.execute('ZNAJDŹ GDZIE "RAM" MIĘDZY 100 DO 500')
        self.assertEqual(sorted(_last(r)["matches"]), ["A", "B"])

    def test_miedzy_with_and_condition(self):
        r = self.engine.execute('ZNAJDŹ GDZIE "RAM" MIĘDZY 100 DO 900 ORAZ "BĄBEL" != "C"')
        self.assertEqual(sorted(_last(r)["matches"]), ["A", "B"])


class TestBetweenParser(unittest.TestCase):
    def test_parse_miedzy(self):
        node = KarminParser().parse('ZNAJDŹ GDZIE "RAM" MIĘDZY 10 DO 20')[0][2]
        self.assertIsInstance(node.cond, CondCompare)
        self.assertEqual(node.cond.op, "MIĘDZY")
        self.assertEqual(node.cond.val, "10")
        self.assertEqual(node.cond.val2, "20")


class TestSubquery(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "Serwer" DO "A"\nWSTRZYKNIJ "RAM" = 2048 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Plik" DO "B"\nWSTRZYKNIJ "RAM" = 512 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "Serwer" DO "C"\nWSTRZYKNIJ "RAM" = 128 DO "C"'
        )

    def test_babel_in_subquery_znajdz(self):
        r = self.engine.execute(
            'ZNAJDŹ GDZIE "BĄBEL" W (ZNAJDŹ GDZIE "Typ" = "Serwer") ORAZ "RAM" > 1000'
        )
        self.assertEqual(_last(r)["matches"], ["A"])

    def test_nie_w_subquery(self):
        r = self.engine.execute(
            'ZNAJDŹ GDZIE "BĄBEL" NIE W (ZNAJDŹ GDZIE "Typ" = "Serwer")'
        )
        self.assertEqual(_last(r)["matches"], ["B"])

    def test_wypisz_subquery_column(self):
        r = self.engine.execute(
            'ZNAJDŹ GDZIE "Typ" W (WYPISZ "Typ" GDZIE "RAM" > 1000)'
        )
        self.assertEqual(sorted(_last(r)["matches"]), ["A", "C"])

    def test_subquery_with_miedzy(self):
        r = self.engine.execute(
            'ZNAJDŹ GDZIE "BĄBEL" W (ZNAJDŹ GDZIE "RAM" MIĘDZY 100 DO 600)'
        )
        self.assertEqual(sorted(_last(r)["matches"]), ["B", "C"])


if __name__ == "__main__":
    unittest.main()