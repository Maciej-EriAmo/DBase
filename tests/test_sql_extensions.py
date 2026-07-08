"""Testy rozszerzeń KarminQL v4.9+ (zbliżenie do SQL)."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, CondAnd, CondCompare


def _last(results):
    return results[-1] if results else {}


class TestProjectWhere(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_project_where_tabular(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\nWSTRZYKNIJ "Typ" = "Serwer" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 200 DO "B"\nWSTRZYKNIJ "Typ" = "Serwer" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "RAM" = 50 DO "C"\nWSTRZYKNIJ "Typ" = "Plik" DO "C"'
        )
        r = self.engine.execute('WYPISZ "BĄBEL", "RAM", "Typ" GDZIE "Typ" = "Serwer" SORTUJ WEDŁUG "RAM" MALEJĄCO')
        res = _last(r)
        self.assertEqual(res["action"], "PROJECT_WHERE")
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["rows"][0]["RAM"], 200)
        self.assertEqual(res["rows"][1]["RAM"], 100)


class TestDistinct(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_wypisz_unikalne_prefix(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "Serwer" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Serwer" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "Plik" DO "C"'
        )
        r = self.engine.execute('WYPISZ UNIKALNE "Typ" GDZIE "Typ" != "NIC"')
        res = _last(r)
        self.assertTrue(res["distinct"])
        self.assertEqual(res["count"], 2)
        self.assertEqual(sorted(row["Typ"] for row in res["rows"]), ["Plik", "Serwer"])

    def test_wypisz_unikalne_modifier(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\nWSTRZYKNIJ "Typ" = "S" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 100 DO "B"\nWSTRZYKNIJ "Typ" = "S" DO "B"'
        )
        r = self.engine.execute('WYPISZ "RAM", "Typ" GDZIE "RAM" > 0 UNIKALNE')
        res = _last(r)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["rows"][0]["RAM"], 100)

    def test_unikalne_before_limit(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Grupa" = 1 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Grupa" = 1 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Grupa" = 2 DO "C"'
        )
        r = self.engine.execute('WYPISZ UNIKALNE "Grupa" GDZIE "Grupa" > 0 LIMIT 1')
        self.assertEqual(_last(r)["count"], 1)


class TestUpdateWhere(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_mass_update(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Stan" = "Off" DO "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Stan" = "Off" DO "B"\nWSTRZYKNIJ "Typ" = "X" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Stan" = "Off" DO "C"\nWSTRZYKNIJ "Typ" = "Y" DO "C"'
        )
        r = self.engine.execute('ZAKTUALIZUJ "Stan" = "On" GDZIE "Typ" = "X"')
        self.assertEqual(_last(r)["updated_count"], 2)
        show = _last(self.engine.execute('WYPISZ "Stan", "Typ" GDZIE "Typ" = "X"'))
        for row in show["rows"]:
            self.assertEqual(row["Stan"], "On")


class TestComplexConditions(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_three_and_chain(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "X" = 1 DO "A"\nWSTRZYKNIJ "Y" = 2 DO "A"\nWSTRZYKNIJ "Z" = 3 DO "A"'
        )
        r = self.engine.execute('ZNAJDŹ GDZIE "X" = 1 ORAZ "Y" = 2 ORAZ "Z" = 3')
        self.assertEqual(_last(r)["matches"], ["A"])

    def test_not_operator(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Y" DO "B"'
        )
        r = self.engine.execute('ZNAJDŹ GDZIE NIE "Typ" = "X"')
        self.assertEqual(_last(r)["matches"], ["B"])

    def test_parentheses_or(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\nWSTRZYKNIJ "RAM" = 10 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Y" DO "B"\nWSTRZYKNIJ "RAM" = 100 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "X" DO "C"\nWSTRZYKNIJ "RAM" = 100 DO "C"'
        )
        r = self.engine.execute('ZNAJDŹ GDZIE ("Typ" = "X" ORAZ "RAM" < 50) LUB ("Typ" = "Y" ORAZ "RAM" > 50)')
        self.assertEqual(sorted(_last(r)["matches"]), ["A", "B"])

    def test_is_null(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Opis" = "jest" DO "A"\n'
            'UTRWAL "B"'
        )
        r = self.engine.execute('ZNAJDŹ GDZIE "Opis" JEST NIC')
        self.assertEqual(_last(r)["matches"], ["B"])


class TestJoinRelation(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_find_connected_with_where(self):
        self.engine.execute(
            'UTRWAL "Rodzic"\nWSTRZYKNIJ "RAM" = 8192 DO "Rodzic"\n'
            'UTRWAL "Dziecko"\n'
            'POŁĄCZ "Rodzic" Z "Dziecko" JAKO "syn"'
        )
        r = self.engine.execute('ZNAJDŹ POŁĄCZONE JAKO "syn" Z "Dziecko" GDZIE "RAM" > 1000')
        self.assertEqual(_last(r)["matches"], ["Rodzic"])

    def test_project_with_join(self):
        self.engine.execute(
            'UTRWAL "Rodzic"\nWSTRZYKNIJ "RAM" = 8192 DO "Rodzic"\n'
            'UTRWAL "Dziecko"\n'
            'POŁĄCZ "Rodzic" Z "Dziecko" JAKO "syn"'
        )
        r = self.engine.execute('WYPISZ "BĄBEL", "RAM" POŁĄCZONE JAKO "syn" Z "Dziecko" GDZIE "RAM" > 0')
        self.assertEqual(_last(r)["rows"][0]["BĄBEL"], "Rodzic")

    def test_znajdz_with_join_modifier(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "X" = 1 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "X" = 2 DO "B"\n'
            'UTRWAL "C"\n'
            'POŁĄCZ "A" Z "C" JAKO "link"\n'
            'POŁĄCZ "B" Z "C" JAKO "link"'
        )
        r = self.engine.execute('ZNAJDŹ POŁĄCZONE JAKO "link" Z "C" GDZIE "X" = 1')
        self.assertEqual(_last(r)["matches"], ["A"])


class TestHavingAndCount(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_count_column(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 200 DO "B"\n'
            'UTRWAL "C"'
        )
        r = self.engine.execute('POLICZ "RAM" GDZIE "RAM" > 0')
        self.assertEqual(_last(r)["result"], 2)

    def test_count_distinct(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "X" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "Y" DO "C"'
        )
        r = self.engine.execute('POLICZ RÓŻNE "Typ" GDZIE "Typ" != "NIC"')
        self.assertEqual(_last(r)["result"], 2)

    def test_having_majace(self):
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\nWSTRZYKNIJ "Typ" = "S" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 200 DO "B"\nWSTRZYKNIJ "Typ" = "S" DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "RAM" = 50 DO "C"\nWSTRZYKNIJ "Typ" = "P" DO "C"'
        )
        r = self.engine.execute('SUMA "RAM" GDZIE "Typ" != "NIC" POGRUPUJ "Typ" MAJĄCE SUMA > 150')
        self.assertEqual(_last(r)["result"], {"S": 300})
        self.assertNotIn("P", _last(r)["result"])


class TestParserV49(unittest.TestCase):
    def setUp(self):
        self.parser = KarminParser()

    def test_parse_condition_and_chain(self):
        node = self.parser.parse('ZNAJDŹ GDZIE "A" = 1 ORAZ "B" = 2 ORAZ "C" = 3')[0][2]
        self.assertIsInstance(node.cond, CondAnd)
        self.assertEqual(len(node.cond.parts), 3)

    def test_parse_project_where(self):
        from cynober_query_engine import ProjectWhereNode
        node = self.parser.parse('WYPISZ "RAM", "Typ" GDZIE "RAM" > 0')[0][2]
        self.assertIsInstance(node, ProjectWhereNode)
        self.assertEqual(node.keys, ["RAM", "Typ"])


if __name__ == "__main__":
    unittest.main()