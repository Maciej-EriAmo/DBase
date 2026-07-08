"""Testy KarminQL v6.8: COUNT OVER, ROWS BETWEEN, REGEXP, PRZEMIANUJ."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser


def _last(results):
    return results[-1] if results else {}


class TestCountOver(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ WIELE "Typ" = "X", "Score" = 10 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ WIELE "Typ" = "X", "Score" = 20 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ WIELE "Typ" = "Y", "Score" = 5 DO "C"'
        )

    def test_count_over_partition(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", COUNT(*) OVER (PODZIEL NA "Typ") JAKO "Cnt" '
            'GDZIE "Typ" != "NIC"'
        ))
        by_babel = {row["BĄBEL"]: row["Cnt"] for row in r["rows"]}
        self.assertEqual(by_babel["A"], 2)
        self.assertEqual(by_babel["C"], 1)

    def test_count_over_running(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", COUNT(*) OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Run" '
            'GDZIE "Typ" != "NIC"'
        ))
        by_babel = {row["BĄBEL"]: row["Run"] for row in r["rows"]}
        self.assertEqual(by_babel["C"], 1)
        self.assertEqual(by_babel["A"], 2)
        self.assertEqual(by_babel["B"], 3)


class TestRowsBetween(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Score" = 10 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Score" = 20 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Score" = 30 DO "C"'
        )

    def test_rows_between_preceding_following(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Score", '
            'SUM("Score") OVER (SORTUJ WEDŁUG "Score" ROSNĄCO '
            'WIERSZE MIĘDZY 1 POPRZEDZAJĄCE A 1 NASTĘPUJĄCE) JAKO "WinSum" '
            'GDZIE "Score" > 0'
        ))
        by_babel = {row["BĄBEL"]: row["WinSum"] for row in r["rows"]}
        self.assertEqual(by_babel["B"], 60)


class TestRegexp(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Kod" = "item_42" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Kod" = "other" DO "B"'
        )

    def test_regexp_match(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "Kod" PASUJE DO "^item_\\d+"'
        ))
        self.assertEqual(r["matches"], ["A"])

    def test_regexp_tilde(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "Kod" ~ "^other$"'
        ))
        self.assertEqual(r["matches"], ["B"])


class TestRename(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute('UTRWAL "Stary"\nWSTRZYKNIJ "Sku" = "X1" DO "Stary"')

    def test_rename_bubble(self):
        self.engine.execute('PRZEMIANUJ BĄBEL "Stary" NA "Nowy"')
        show = _last(self.engine.execute('POKAŻ "Nowy"'))
        self.assertEqual(show["data"]["properties"]["Sku"], "X1")

    def test_rename_property(self):
        self.engine.execute('PRZEMIANUJ CECHĘ "Sku" NA "SKU" W "Stary"')
        show = _last(self.engine.execute('POKAŻ "Stary"'))
        self.assertEqual(show["data"]["properties"]["SKU"], "X1")
        self.assertNotIn("Sku", show["data"]["properties"])

    def test_rename_parser_english(self):
        node = KarminParser().parse('RENAME BUBBLE "A" TO "B"')[0][2]
        self.assertEqual(node.old_name, "A")
        self.assertEqual(node.new_name, "B")


if __name__ == "__main__":
    unittest.main()