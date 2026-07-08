"""Testy angielskich aliasów KarminQL (v5.6)."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, normalize_karminql_aliases


def _last(results):
    return results[-1] if results else {}


class TestEnglishAliasesEngine(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_create_insert_show(self):
        r = self.engine.execute(
            'CREATE "Srv"\n'
            'INSERT "RAM" = 16 INTO "Srv"\n'
            'SHOW "Srv"'
        )
        self.assertEqual(_last(r)["action"], "SHOW")
        self.assertEqual(_last(r)["data"]["properties"]["RAM"], 16)

    def test_select_where(self):
        self.engine.execute(
            'CREATE "A"\nINSERT "Typ" = "Server" INTO "A"\n'
            'CREATE "B"\nINSERT "Typ" = "File" INTO "B"'
        )
        r = self.engine.execute('FIND WHERE "Typ" = "Server"')
        self.assertEqual(_last(r)["matches"], ["A"])

    def test_select_project_where(self):
        self.engine.execute('CREATE "X"\nINSERT "RAM" = 8 INTO "X"')
        r = self.engine.execute('SELECT "BĄBEL", "RAM" WHERE "RAM" > 0')
        self.assertEqual(_last(r)["action"], "PROJECT_WHERE")
        self.assertEqual(_last(r)["count"], 1)

    def test_sum_group_by_having(self):
        self.engine.execute(
            'CREATE "A"\nINSERT "Typ" = "S" INTO "A"\nINSERT "RAM" = 100 INTO "A"\n'
            'CREATE "B"\nINSERT "Typ" = "S" INTO "B"\nINSERT "RAM" = 200 INTO "B"'
        )
        r = self.engine.execute(
            'SUM "RAM" WHERE "Typ" = "S" GROUP BY "Typ" HAVING SUM > 150'
        )
        self.assertEqual(_last(r)["result"]["S"], 300)

    def test_connect_and_find_joined(self):
        self.engine.execute(
            'CREATE "P"\nCREATE "C"\nCONNECT "P" TO "C" AS "child"'
        )
        r = self.engine.execute('FIND JOINED AS "child" TO "C"')
        self.assertEqual(_last(r)["matches"], ["P"])

    def test_let_and_union(self):
        self.engine.execute(
            'CREATE "A"\nINSERT "Typ" = "X" INTO "A"\n'
            'CREATE "B"\nINSERT "Typ" = "Y" INTO "B"'
        )
        r = self.engine.execute(
            'LET $a = FIND WHERE "Typ" = "X"\n'
            'LET $b = FIND WHERE "Typ" = "Y"\n'
            'FIND WHERE "BĄBEL" IN $a UNION $b'
        )
        self.assertEqual(sorted(_last(r)["matches"]), ["A", "B"])

    def test_between_and_or(self):
        self.engine.execute(
            'CREATE "A"\nINSERT "RAM" = 100 INTO "A"\n'
            'CREATE "B"\nINSERT "RAM" = 500 INTO "B"'
        )
        r = self.engine.execute(
            'FIND WHERE "RAM" BETWEEN 100 AND 600 OR "BĄBEL" = "A"'
        )
        self.assertEqual(sorted(_last(r)["matches"]), ["A", "B"])

    def test_polish_still_works(self):
        r = self.engine.execute('UTRWAL "PL"\nWSTRZYKNIJ "V" = 1 DO "PL"')
        self.assertEqual(_last(r)["action"], "ADD_PROP")


class TestEnglishAliasesNormalize(unittest.TestCase):
    def test_normalize_find(self):
        self.assertIn("ZNAJDŹ", normalize_karminql_aliases('FIND WHERE "X" = 1'))

    def test_preserves_quoted_strings(self):
        out = normalize_karminql_aliases('FIND WHERE "FROM" = "SELECT"')
        self.assertIn('"FROM"', out)
        self.assertIn('"SELECT"', out)


class TestEnglishAliasesParser(unittest.TestCase):
    def test_parse_english_create(self):
        node = KarminParser().parse('CREATE "X"')[0][2]
        from cynober_query_engine import CreateBubbleNode
        self.assertIsInstance(node, CreateBubbleNode)


if __name__ == "__main__":
    unittest.main()