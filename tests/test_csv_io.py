"""Testy EKSPORT CSV / IMPORT CSV — KarminQL v5.5."""

import csv
import os
import tempfile
import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, ExportCsvNode, ImportCsvNode


def _last(results):
    return results[-1] if results else {}


class TestCsvExportImport(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.tmp = tempfile.mkdtemp()

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_export_import_roundtrip(self):
        self.engine.execute(
            'UTRWAL "T1"\nWSTRZYKNIJ "Sku" = "Item1" DO "T1"\nWSTRZYKNIJ "RAM" = 8 DO "T1"\n'
            'UTRWAL "T2"\nWSTRZYKNIJ "Sku" = "Item2" DO "T2"\nWSTRZYKNIJ "RAM" = 16 DO "T2"'
        )
        out = self._path("out.csv")
        r = self.engine.execute(
            f'EKSPORT CSV "{out}" Z (WYPISZ "Sku", "RAM" GDZIE "RAM" >= 8)'
        )
        res = _last(r)
        self.assertEqual(res["action"], "EXPORT_CSV")
        self.assertEqual(res["rows_written"], 2)
        self.assertTrue(os.path.isfile(out))

        self.engine.execute('USUŃ BĄBLE GDZIE "BĄBEL" != "NIC"')
        imp = self.engine.execute(f'IMPORT CSV "{out}" KOLUMNA "Sku"')
        self.assertEqual(_last(imp)["count"], 2)
        show = _last(self.engine.execute('POKAŻ "Item1"'))
        self.assertEqual(show["data"]["properties"]["RAM"], 8)

    def test_import_csv_default_name_column(self):
        path = self._path("simple.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["BĄBEL", "Typ", "RAM"])
            writer.writerow(["S1", "Serwer", "32"])
            writer.writerow(["S2", "Serwer", "64"])
        r = self.engine.execute(f'IMPORT CSV "{path}"')
        self.assertEqual(_last(r)["created"], ["S1", "S2"])

    def test_parser_csv_nodes(self):
        exp = KarminParser().parse('EKSPORT CSV "x.csv" Z (ZNAJDŹ GDZIE "Typ" = "S")')[0][2]
        self.assertIsInstance(exp, ExportCsvNode)
        imp = KarminParser().parse('IMPORT CSV "dane.csv" KOLUMNA "Sku"')[0][2]
        self.assertIsInstance(imp, ImportCsvNode)
        self.assertEqual(imp.name_column, "Sku")


if __name__ == "__main__":
    unittest.main()