"""Testy KarminQL v6.3: COALESCE/NULLIF, DROP VIEW, CHECK, multi-SORT."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser
from cynober_query_engine import DeleteViewNode, RequireCheckNode


def _last(results):
    return results[-1] if results else {}


class TestCoalesceNullif(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = NIC DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 16 DO "B"'
        )

    def test_coalesce_fills_null(self):
        r = _last(self.engine.execute(
            'WYPISZ COALESCE("RAM", 0) JAKO "RAM_eff" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["RAM_eff"], 0)

    def test_coalesce_keeps_value(self):
        r = _last(self.engine.execute(
            'WYPISZ COALESCE("RAM", 0) JAKO "RAM_eff" GDZIE "BĄBEL" = "B"'
        ))
        self.assertEqual(r["rows"][0]["RAM_eff"], 16)

    def test_nullif(self):
        r = _last(self.engine.execute(
            'WYPISZ NULLIF("RAM", 16) JAKO "RAM_null" GDZIE "BĄBEL" = "B"'
        ))
        self.assertIsNone(r["rows"][0]["RAM_null"])
        r2 = _last(self.engine.execute(
            'WYPISZ NULLIF("RAM", 0) JAKO "RAM_null" GDZIE "BĄBEL" = "B"'
        ))
        self.assertEqual(r2["rows"][0]["RAM_null"], 16)


class TestDropView(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "RAM" = 8 DO "A"')

    def test_delete_view(self):
        self.engine.execute('UTRWAL WIDOK "V" JAKO (WYPISZ "BĄBEL", "RAM" GDZIE "RAM" > 0)')
        r = _last(self.engine.execute('USUŃ WIDOK "V"'))
        self.assertEqual(r["action"], "DELETE_VIEW")
        r2 = self.engine.execute('WYPISZ Z WIDOKU "V"', strict=False)
        self.assertEqual(_last(r2)["status"], "error")

    def test_drop_view_english_alias(self):
        node = KarminParser().parse('DROP VIEW "V"')[0][2]
        self.assertIsInstance(node, DeleteViewNode)


class TestCheckConstraint(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_check_constraint_violation(self):
        self.engine.execute('WYMAGAJ SPRAWDŹ "RAM" > 0')
        r = self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "RAM" = -1 DO "A"', strict=False)
        self.assertEqual(_last(r)["status"], "error")
        self.assertIn("SPRAWDŹ", _last(r)["message"])

    def test_check_constraint_ok(self):
        self.engine.execute('WYMAGAJ SPRAWDŹ "RAM" > 0')
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "RAM" = 8 DO "A"')
        show = _last(self.engine.execute('POKAŻ "A"'))
        self.assertEqual(show["data"]["properties"]["RAM"], 8)

    def test_require_check_parser(self):
        node = KarminParser().parse('REQUIRE CHECK "RAM" > 0')[0][2]
        self.assertIsInstance(node, RequireCheckNode)
        self.assertEqual(node.key, "RAM")

    def test_opisz_shows_check(self):
        self.engine.execute('WYMAGAJ SPRAWDŹ "RAM" > 0')
        r = _last(self.engine.execute("OPISZ BAZĘ"))
        self.assertIn("RAM", r["constraints"]["check"])


class TestMultiSort(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ WIELE "Grp" = "X", "Score" = 10 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ WIELE "Grp" = "X", "Score" = 20 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ WIELE "Grp" = "Y", "Score" = 5 DO "C"'
        )

    def test_sort_two_columns(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Grp", "Score" GDZIE "Grp" = "X" '
            'SORTUJ WEDŁUG "Grp", "Score" MALEJĄCO'
        ))
        scores = [row["Score"] for row in r["rows"]]
        self.assertEqual(scores, [20, 10])

    def test_sort_two_columns_on_find(self):
        r = _last(self.engine.execute(
            'ZNAJDŹ GDZIE "Grp" = "X" LUB "Grp" = "Y" SORTUJ WEDŁUG "Grp", "Score" MALEJĄCO'
        ))
        self.assertEqual(r["matches"], ["C", "B", "A"])


if __name__ == "__main__":
    unittest.main()