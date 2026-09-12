"""Testy KarminQL v6.2: CTE, widoki, constraints, IMPORT CSV SCAL."""

import csv
import os
import tempfile
import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser
from cynober_query_engine import CreateViewNode


def _last(results):
    return results[-1] if results else {}


class TestCte(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nINSERT "Typ" = "Serwer" INTO "A"\n'
            'UTRWAL "B"\nINSERT "Typ" = "Plik" INTO "B"'
        )

    def test_z_jako_cte(self):
        r = _last(self.engine.execute(
            'Z $s JAKO (FIND WHERE "Typ" = "Serwer")\n'
            'FIND WHERE "BĄBEL" IN $s'
        ))
        self.assertEqual(r["matches"], ["A"])

    def test_with_english_alias(self):
        r = _last(self.engine.execute(
            'WITH $x AS (FIND WHERE "Typ" = "Plik")\n'
            'FIND WHERE "BĄBEL" IN $x'
        ))
        self.assertEqual(r["matches"], ["B"])


class TestViews(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "RAM" = 8 DO "A"')

    def test_create_and_query_view(self):
        self.engine.execute('UTRWAL WIDOK "Aktywne" JAKO (WYPISZ "BĄBEL", "RAM" GDZIE "RAM" > 0)')
        r = _last(self.engine.execute('WYPISZ Z WIDOKU "Aktywne"'))
        self.assertEqual(r["action"], "QUERY_VIEW")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["rows"][0]["RAM"], 8)

    def test_create_view_english_alias(self):
        node = KarminParser().parse('CREATE VIEW "V" AS (FIND WHERE "RAM" > 0)')[0][2]
        self.assertIsInstance(node, CreateViewNode)


class TestConstraints(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_unique_constraint(self):
        self.engine.execute('WYMAGAJ UNIKALNE "Sku"')
        self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "Sku" = "X1" DO "A"')
        r = self.engine.execute('UTRWAL "B"\nWSTRZYKNIJ "Sku" = "X1" DO "B"', strict=False)
        self.assertEqual(_last(r)["status"], "error")
        self.assertIn("UNIKALNE", _last(r)["message"])

    def test_not_null_constraint(self):
        self.engine.execute('WYMAGAJ NIE NULL "Sku"')
        r = self.engine.execute('UTRWAL "A"\nWSTRZYKNIJ "Sku" = NIC DO "A"', strict=False)
        self.assertEqual(_last(r)["status"], "error")
        self.assertIn("NIE NULL", _last(r)["message"])

    def test_opisz_shows_constraints(self):
        self.engine.execute('WYMAGAJ UNIKALNE "Sku"')
        r = _last(self.engine.execute("OPISZ BAZĘ"))
        self.assertIn("Sku", r["constraints"]["unique"])


class TestImportCsvUpsert(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.tmp = tempfile.mkdtemp()

    def test_import_csv_scal_po(self):
        path = os.path.join(self.tmp, "data.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Sku", "RAM"])
            writer.writerow(["A1", "8"])
        self.engine.execute(f'IMPORT CSV "{path}" SCAL PO "Sku"')
        self.engine.execute(f'IMPORT CSV "{path}" SCAL PO "Sku"')
        r = _last(self.engine.execute('ZNAJDŹ GDZIE "Sku" = "A1"'))
        self.assertEqual(len(r["matches"]), 1)
        show = _last(self.engine.execute(f'POKAŻ "{r["matches"][0]}"'))
        self.assertEqual(show["data"]["properties"]["RAM"], 8)

    def test_parser_import_upsert(self):
        node = KarminParser().parse('IMPORT CSV "f.csv" UPSERT ON "Sku"')[0][2]
        self.assertEqual(node.upsert_key, "Sku")


if __name__ == "__main__":
    unittest.main()