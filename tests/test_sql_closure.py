"""Testy domknięcia SQL: OPISZ BAZĘ, SCAL, JOIN relacyjny, LIKE, CASE, read_karmin."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser

try:
    import pandas  # noqa: F401
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

if _HAS_PANDAS:
    from cynober_pandas_bridge import read_karmin


def _last(results):
    return results[-1] if results else {}


class TestDescribeDatabase(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_opisz_baze(self):
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "RAM" = 8 DO "A"')
        r = _last(self.engine.execute("OPISZ BAZĘ"))
        self.assertEqual(r["action"], "DESCRIBE_DB")
        self.assertEqual(r["bubble_count"], 1)
        self.assertEqual(r["bubbles"][0]["name"], "A")
        self.assertIn("RAM", r["bubbles"][0]["properties"])

    def test_describe_db_alias(self):
        self.engine.execute('CREATE "X"')
        r = _last(self.engine.execute('DESCRIBE DATABASE'))
        self.assertEqual(r["action"], "DESCRIBE_DB")
        self.assertGreaterEqual(r["bubble_count"], 1)


@unittest.skipUnless(_HAS_PANDAS, "pandas nie zainstalowany")
class TestReadKarmin(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_read_karmin_project(self):
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "RAM" = 8 DO "A"')
        df = read_karmin(self.engine, 'WYPISZ "BĄBEL", "RAM" GDZIE "RAM" > 0')
        self.assertEqual(len(df), 1)
        self.assertIn("RAM", df.columns)

    def test_read_karmin_describe(self):
        self.engine.execute('UTRWAL "A"')
        df = read_karmin(self.engine, "OPISZ BAZĘ")
        self.assertEqual(len(df), 1)
        self.assertIn("BĄBEL", df.columns)


class TestMergeUpsert(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_scal_by_bubble_create(self):
        r = _last(self.engine.execute('SCAL "Srv" Z "RAM" = 16'))
        self.assertEqual(r["action"], "MERGE")
        self.assertEqual(r["mode"], "created")
        show = _last(self.engine.execute('POKAŻ "Srv"'))
        self.assertEqual(show["data"]["properties"]["RAM"], 16)

    def test_scal_by_bubble_update(self):
        self.engine.execute('UTRWAL "Srv"\nWSTRZYKNIJ "RAM" = 8 DO "Srv"')
        r = _last(self.engine.execute('SCAL "Srv" Z "RAM" = 32'))
        self.assertEqual(r["mode"], "updated")
        show = _last(self.engine.execute('POKAŻ "Srv"'))
        self.assertEqual(show["data"]["properties"]["RAM"], 32)

    def test_scal_po_kluczu(self):
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "Sku" = "X1" DO "A"\nWSTRZYKNIJ "RAM" = 4 DO "A"')
        r = _last(self.engine.execute('SCAL PO "Sku" = "X1" Z "RAM" = 16'))
        self.assertEqual(r["mode"], "updated")
        show = _last(self.engine.execute('POKAŻ "A"'))
        self.assertEqual(show["data"]["properties"]["RAM"], 16)

    def test_scal_po_kluczu_create(self):
        r = _last(self.engine.execute('SCAL PO "Sku" = "NEW" Z "RAM" = 8, "Typ" = "S"'))
        self.assertEqual(r["mode"], "created")
        show = _last(self.engine.execute(f'POKAŻ "{r["target"]}"'))
        self.assertEqual(show["data"]["properties"]["Sku"], "NEW")


class TestV61JoinExtensions(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "O1"\nWSTRZYKNIJ "Sku" = "A" DO "O1"\nWSTRZYKNIJ "Qty" = 2 DO "O1"\n'
            'UTRWAL "O2"\nWSTRZYKNIJ "Sku" = "B" DO "O2"\nWSTRZYKNIJ "Qty" = 5 DO "O2"\n'
            'UTRWAL "K1"\nWSTRZYKNIJ "Sku" = "A" DO "K1"\nWSTRZYKNIJ "Cena" = 10 DO "K1"'
        )

    def test_inner_join_excludes_unmatched(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Qty" GDZIE "Qty" > 0 '
            'DOŁĄCZ Z "Katalog" GDZIE "Sku" = "Sku"'
        ))
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["rows"][0]["BĄBEL"], "O1")

    def test_left_join_keeps_unmatched(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Qty", Katalog.Cena GDZIE "Qty" > 0 '
            'LEWY DOŁĄCZ Z "Katalog" GDZIE "Sku" = "Sku"'
        ))
        self.assertEqual(r["count"], 2)
        unmatched = [row for row in r["rows"] if row["BĄBEL"] == "O2"][0]
        self.assertIsNone(unmatched.get("Cena"))

    def test_join_with_subquery_filter(self):
        self.engine.execute('WSTRZYKNIJ "Typ" = "Katalog" DO "K1"')
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", Katalog.Cena GDZIE "Qty" > 0 '
            'DOŁĄCZ Z (ZNAJDŹ GDZIE "Typ" = "Katalog") JAKO "Katalog" GDZIE "Sku" = "Sku"'
        ))
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["rows"][0]["Cena"], 10)


class TestSubstrateIndexPerf(unittest.TestCase):
    def test_inv_index_speeds_up_equality_where(self):
        engine = KarminEngine(kernel.Store(thermal=True))
        for i in range(200):
            engine.execute(f'UTRWAL "B{i}"\nWSTRZYKNIJ "Typ" = "X" DO "B{i}"')
        engine.execute('UTRWAL "Target"\nWSTRZYKNIJ "Typ" = "Hit" DO "Target"')
        r = _last(engine.execute('ZNAJDŹ GDZIE "Typ" = "Hit"'))
        self.assertEqual(r["matches"], ["Target"])


class TestRelationalJoin(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "O1"\nWSTRZYKNIJ "Sku" = "A" DO "O1"\nWSTRZYKNIJ "Qty" = 2 DO "O1"\n'
            'UTRWAL "K1"\nWSTRZYKNIJ "Sku" = "A" DO "K1"\nWSTRZYKNIJ "Cena" = 10 DO "K1"'
        )

    def test_dolacz_z_join(self):
        r = _last(self.engine.execute(
            'WYPISZ "BĄBEL", "Qty", "Katalog.Cena" GDZIE "Qty" > 0 '
            'DOŁĄCZ Z "Katalog" GDZIE "Sku" = "Sku"'
        ))
        self.assertEqual(r["action"], "PROJECT_WHERE")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["rows"][0]["Qty"], 2)
        self.assertEqual(r["rows"][0]["Cena"], 10)


class TestLikeAndCase(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Nazwa" = "Serwer_A" DO "A"\nWSTRZYKNIJ "RAM" = 32 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Nazwa" = "Plik_B" DO "B"'
        )

    def test_like_pattern(self):
        r = _last(self.engine.execute('ZNAJDŹ GDZIE "Nazwa" PODOBNE "Serwer%"'))
        self.assertEqual(r["matches"], ["A"])

    def test_like_english_alias(self):
        r = _last(self.engine.execute('FIND WHERE "Nazwa" LIKE "%Plik%"'))
        self.assertEqual(r["matches"], ["B"])

    def test_case_when_projection(self):
        r = _last(self.engine.execute(
            'WYPISZ CASE WHEN "RAM" > 16 THEN "High" ELSE "Low" END AS "Tier", "RAM" GDZIE "RAM" > 0'
        ))
        self.assertEqual(r["rows"][0]["Tier"], "High")

    def test_arith_projection(self):
        self.engine.execute('WSTRZYKNIJ "Cena" = 5 DO "A"\nWSTRZYKNIJ "Qty" = 3 DO "A"')
        r = _last(self.engine.execute(
            'WYPISZ "Cena" * "Qty" AS "Suma" GDZIE "BĄBEL" = "A"'
        ))
        self.assertEqual(r["rows"][0]["Suma"], 15)


class TestSqlClosureParser(unittest.TestCase):
    def test_parse_merge_and_describe(self):
        from cynober_query_engine import DescribeDatabaseNode, MergeNode
        p = KarminParser()
        self.assertIsInstance(p.parse("OPISZ BAZĘ")[0][2], DescribeDatabaseNode)
        self.assertIsInstance(p.parse('SCAL "X" Z "A" = 1')[0][2], MergeNode)


if __name__ == "__main__":
    unittest.main()