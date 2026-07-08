"""Testy KarminQL v6.4: EXISTS, CAST, CONCAT, DROP CONSTRAINT."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, CondExists
from cynober_query_engine import DeleteConstraintNode


def _last(results):
    return results[-1] if results else {}


class TestExists(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "Serwer" DO "A"\nWSTRZYKNIJ "RAM" = 2048 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Plik" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "Serwer" DO "C"\nWSTRZYKNIJ "RAM" = 128 DO "C"'
        )

    def test_exists_with_and(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "Typ" = "Serwer" ORAZ '
            'ISTNIEJE (ZNAJDŹ GDZIE "BĄBEL" W $BĄBEL ORAZ "RAM" > 1000)'
        ))
        self.assertEqual(r["matches"], ["A"])

    def test_not_exists(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "Typ" = "Serwer" ORAZ NIE ISTNIEJE (ZNAJDŹ GDZIE "RAM" > 5000)'
        ))
        self.assertEqual(sorted(r["matches"]), ["A", "C"])

    def test_exists_correlated_babel(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE ISTNIEJE (ZNAJDŹ GDZIE "BĄBEL" W $BĄBEL ORAZ "RAM" > 1000)'
        ))
        self.assertEqual(r["matches"], ["A"])

    def test_exists_parser_english(self):
        node = KarminParser().parse(
            'FIND WHERE EXISTS (FIND WHERE "Typ" = "Serwer")'
        )[0][2]
        self.assertIsInstance(node.cond, CondExists)


class TestCastConcat(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Qty" = "42" DO "A"\nWSTRZYKNIJ "Imie" = "Jan" DO "A"'
        )

    def test_cast_to_int(self):
        r = _last(self.engine.execute(
            'WYPISZ CAST("Qty" AS INT) JAKO "Qty_num" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["Qty_num"], 42)

    def test_concat(self):
        r = _last(self.engine.execute(
            'WYPISZ CONCAT("Imie", " ", "BĄBEL") JAKO "Etykieta" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["Etykieta"], "Jan A")


class TestDropConstraint(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_drop_unique_constraint(self):
        self.engine.execute('WYMAGAJ UNIKALNE "Sku"')
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "Sku" = "X1" DO "A"')
        self.engine.execute('USUŃ WYMAGANIE UNIKALNE "Sku"')
        self.engine.execute('UTRWAL "B"\nWSTRZYKNIJ "Sku" = "X1" DO "B"')
        show = _last(self.engine.execute('POKAŻ "B"'))
        self.assertEqual(show["data"]["properties"]["Sku"], "X1")

    def test_drop_check_constraint(self):
        self.engine.execute('WYMAGAJ SPRAWDŹ "RAM" > 0')
        self.engine.execute('USUŃ WYMAGANIE SPRAWDŹ "RAM"')
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "RAM" = -1 DO "A"')
        show = _last(self.engine.execute('POKAŻ "A"'))
        self.assertEqual(show["data"]["properties"]["RAM"], -1)

    def test_drop_constraint_parser(self):
        node = KarminParser().parse('DROP CONSTRAINT UNIQUE "Sku"')[0][2]
        self.assertIsInstance(node, DeleteConstraintNode)
        self.assertEqual(node.kind, "UNIKALNE")


if __name__ == "__main__":
    unittest.main()