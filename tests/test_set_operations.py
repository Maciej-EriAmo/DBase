"""Testy operacji zbiorów KarminQL v5.1 (ZŁĄCZ / PRZECIĘCIE / RÓŻNICA)."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, SetOpNode


def _last(results):
    return results[-1] if results else {}


class TestSetUnion(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Y" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "X" DO "C"'
        )

    def test_zlacz_two_queries(self):
        r = self.engine.execute('ZNAJDŹ GDZIE "Typ" = "X" ZŁĄCZ ZNAJDŹ GDZIE "Typ" = "Y"')
        self.assertEqual(set(_last(r)["matches"]), {"A", "B", "C"})

    def test_zlacz_dedupes_bubbles(self):
        r = self.engine.execute('ZNAJDŹ GDZIE "Typ" = "X" ZŁĄCZ ZNAJDŹ GDZIE "Typ" = "X"')
        self.assertEqual(sorted(_last(r)["matches"]), ["A", "C"])


class TestSetIntersect(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Grupa" = 1 DO "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Grupa" = 1 DO "B"\nWSTRZYKNIJ "Typ" = "Y" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Grupa" = 2 DO "C"\nWSTRZYKNIJ "Typ" = "X" DO "C"'
        )

    def test_przeciecie(self):
        r = self.engine.execute(
            'ZNAJDŹ GDZIE "Typ" = "X" PRZECIĘCIE ZNAJDŹ GDZIE "Grupa" = 1'
        )
        self.assertEqual(_last(r)["matches"], ["A"])


class TestSetExcept(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Y" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "X" DO "C"'
        )

    def test_roznica(self):
        r = self.engine.execute('ZNAJDŹ GDZIE "Typ" = "X" RÓŻNICA ZNAJDŹ GDZIE "BĄBEL" = "A"')
        self.assertEqual(_last(r)["matches"], ["C"])


class TestSetPrecedence(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "G" = 1 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "G" = 2 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "G" = 2 DO "C"'
        )

    def test_union_of_intersects(self):
        # A ZŁĄCZ (B PRZECIĘCIE C)  — PRZECIĘCIE wiąże mocniej
        r = self.engine.execute('ZNAJDŹ GDZIE "G" = 1 ZŁĄCZ ZNAJDŹ GDZIE "G" = 2 PRZECIĘCIE ZNAJDŹ GDZIE "BĄBEL" = "B"')
        self.assertEqual(set(_last(r)["matches"]), {"A", "B"})


class TestSetVariables(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Y" DO "B"'
        )

    def test_zlacz_variables(self):
        r = self.engine.execute(
            'NIECH $x = ZNAJDŹ GDZIE "Typ" = "X"\n'
            'NIECH $y = ZNAJDŹ GDZIE "Typ" = "Y"\n'
            'ZŁĄCZ $x $y'
        )
        self.assertEqual(set(_last(r)["matches"]), {"A", "B"})


class TestSetProjectRows(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\nWSTRZYKNIJ "Typ" = "S" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 100 DO "B"\nWSTRZYKNIJ "Typ" = "P" DO "B"'
        )

    def test_wypisz_zlacz_rows(self):
        r = self.engine.execute(
            'WYPISZ "RAM", "Typ" GDZIE "RAM" = 100 ZŁĄCZ WYPISZ "RAM", "Typ" GDZIE "Typ" = "P"'
        )
        res = _last(r)
        self.assertEqual(res["count"], 2)
        typs = {row["Typ"] for row in res["rows"]}
        self.assertEqual(typs, {"S", "P"})


class TestSetParser(unittest.TestCase):
    def setUp(self):
        self.parser = KarminParser()

    def test_parse_zlacz(self):
        node = self.parser.parse('ZNAJDŹ GDZIE "A"=1 ZŁĄCZ ZNAJDŹ GDZIE "B"=2')[0][2]
        self.assertIsInstance(node, SetOpNode)
        self.assertEqual(node.op, "ZŁĄCZ")

    def test_parse_with_limit(self):
        node = self.parser.parse('ZNAJDŹ GDZIE "A"=1 ZŁĄCZ ZNAJDŹ GDZIE "B"=2 LIMIT 1')[0][2]
        self.assertIsInstance(node, SetOpNode)
        self.assertEqual(node.limit, 1)


if __name__ == "__main__":
    unittest.main()