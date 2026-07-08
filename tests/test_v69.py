"""Testy KarminQL v6.9: CREATE/DROP INDEX, EXPLAIN, RANGE BETWEEN, JSON paths."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser


def _last(results):
    return results[-1] if results else {}


class TestCreateDropIndex(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ WIELE "Sku" = "X1", "Typ" = "Serwer" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ WIELE "Sku" = "X2", "Typ" = "Serwer" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ WIELE "Sku" = "Y1", "Typ" = "Plik" DO "C"'
        )

    def test_create_index_rebuilds(self):
        r = _last(self.engine.execute('UTWÓRZ INDEKS NA "Sku"'))
        self.assertEqual(r["action"], "CREATE_INDEX")
        self.assertEqual(r["key"], "Sku")
        self.assertEqual(r["indexed_rows"], 3)

    def test_drop_index(self):
        self.engine.execute('UTWÓRZ INDEKS NA "Sku"')
        r = _last(self.engine.execute('USUŃ INDEKS NA "Sku"'))
        self.assertEqual(r["action"], "DROP_INDEX")

    def test_drop_missing_index_raises(self):
        with self.assertRaises(RuntimeError):
            self.engine.execute('USUŃ INDEKS NA "Brak"')

    def test_create_index_english_alias(self):
        node = KarminParser().parse('CREATE INDEX ON "Typ"')[0][2]
        self.assertEqual(node.key, "Typ")


class TestExplain(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Sku" = "X1" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Sku" = "X2" DO "B"'
        )

    def test_explain_index_lookup(self):
        r = _last(self.engine.execute('WYJAŚNIJ ZNAJDŹ GDZIE "Sku" = "X1"'))
        self.assertEqual(r["action"], "EXPLAIN")
        self.assertEqual(r["plan"]["strategy"], "index_lookup")
        self.assertEqual(r["plan"]["estimated_rows"], 1)

    def test_explain_full_scan(self):
        r = _last(self.engine.execute('WYJAŚNIJ ZNAJDŹ GDZIE "Sku" > "A"'))
        self.assertEqual(r["plan"]["strategy"], "full_scan")

    def test_explain_pinned_index(self):
        self.engine.execute('UTWÓRZ INDEKS NA "Sku"')
        r = _last(self.engine.execute('WYJAŚNIJ ZNAJDŹ GDZIE "Sku" = "X2"'))
        self.assertTrue(r["plan"]["pinned_index"])

    def test_explain_english_alias(self):
        r = _last(self.engine.execute('EXPLAIN FIND WHERE "Sku" = "X1"'))
        self.assertEqual(r["action"], "EXPLAIN")
        self.assertEqual(r["plan"]["strategy"], "index_lookup")


class TestRangeBetween(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Score" = 10 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Score" = 20 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Score" = 20 DO "C"\n'
            'UTRWAL "D"\nWSTRZYKNIJ "Score" = 30 DO "D"'
        )

    def test_range_between_peers(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Score", '
            'COUNT(*) OVER (SORTUJ WEDŁUG "Score" ROSNĄCO '
            'ZAKRES MIĘDZY BIEŻĄCY WIERSZ A BIEŻĄCY WIERSZ) JAKO "Peers" '
            'GDZIE "Score" > 0'
        ))
        by_babel = {row["BĄBEL"]: row["Peers"] for row in r["rows"]}
        self.assertEqual(by_babel["B"], 2)
        self.assertEqual(by_babel["C"], 2)
        self.assertEqual(by_babel["A"], 1)

    def test_range_between_preceding(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Score", '
            'SUM("Score") OVER (SORTUJ WEDŁUG "Score" ROSNĄCO '
            'ZAKRES MIĘDZY 10 POPRZEDZAJĄCE A BIEŻĄCY WIERSZ) JAKO "WinSum" '
            'GDZIE "Score" > 0'
        ))
        by_babel = {row["BĄBEL"]: row["WinSum"] for row in r["rows"]}
        self.assertEqual(by_babel["D"], 70)
        self.assertEqual(by_babel["A"], 10)


class TestJsonPaths(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "Hero"\n'
            'WSTRZYKNIJ "Stats" = {"hp": 100, "mp": 50, "skills": ["fire", "ice"]} DO "Hero"'
        )

    def test_json_value_projection(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", JSON_WARTOŚĆ("Stats", "$.hp") JAKO "HP" '
            'GDZIE "BĄBEL" = "Hero"'
        ))
        self.assertEqual(r["rows"][0]["HP"], 100)

    def test_json_nested_path(self):
        r = _last(self.engine.execute(
            'WYPISZ JSON_WARTOŚĆ("Stats", "$.skills[0]") JAKO "Skill" '
            'GDZIE "BĄBEL" = "Hero"'
        ))
        self.assertEqual(r["rows"][0]["Skill"], "fire")

    def test_dotted_key_where(self):
        r = _last(self.engine.execute('ZNAJDŹ GDZIE "Stats.hp" = 100'))
        self.assertEqual(r["matches"], ["Hero"])

    def test_json_value_english_alias(self):
        r = _last(self.engine.execute(
            'WYPISZ JSON_VALUE("Stats", "$.mp") JAKO "MP" GDZIE "BĄBEL" = "Hero"'
        ))
        self.assertEqual(r["rows"][0]["MP"], 50)


if __name__ == "__main__":
    unittest.main()