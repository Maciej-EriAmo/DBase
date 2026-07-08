"""Testy WSTAW Z (INSERT … SELECT) — KarminQL v5.4."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, InsertFromNode


def _last(results):
    return results[-1] if results else {}


class TestInsertFromWypisz(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_wstaw_z_wypisz_by_name_column(self):
        self.engine.execute(
            'UTRWAL "Szablon"\nWSTRZYKNIJ "Sku" = "Prod_A" DO "Szablon"\n'
            'WSTRZYKNIJ "RAM" = 8 DO "Szablon"\nWSTRZYKNIJ "Typ" = "S" DO "Szablon"'
        )
        r = self.engine.execute(
            'WSTAW Z (WYPISZ "Sku", "RAM", "Typ" GDZIE "Typ" = "S")'
        )
        res = _last(r)
        self.assertEqual(res["action"], "INSERT_FROM")
        self.assertEqual(res["created"], ["Prod_A"])
        self.assertEqual(res["count"], 1)
        show = _last(self.engine.execute('POKAŻ "Prod_A"'))
        props = show["data"]["properties"]
        self.assertEqual(props["RAM"], 8)
        self.assertEqual(props["Typ"], "S")

    def test_wstaw_z_wypisz_duplicate_fails(self):
        self.engine.execute('UTRWAL "X"\nWSTRZYKNIJ "Typ" = "T" DO "X"')
        with self.assertRaises(RuntimeError):
            self.engine.execute('WSTAW Z (WYPISZ "BĄBEL", "Typ" GDZIE "Typ" = "T")')

    def test_wstaw_z_wypisz_multi_rows(self):
        self.engine.execute(
            'UTRWAL "T1"\nWSTRZYKNIJ "Sku" = "Item1" DO "T1"\nWSTRZYKNIJ "RAM" = 8 DO "T1"\n'
            'UTRWAL "T2"\nWSTRZYKNIJ "Sku" = "Item2" DO "T2"\nWSTRZYKNIJ "RAM" = 16 DO "T2"'
        )
        r = self.engine.execute(
            'WSTAW Z (WYPISZ "Sku", "RAM" GDZIE "RAM" >= 8)'
        )
        res = _last(r)
        self.assertEqual(res["count"], 2)
        self.assertEqual(sorted(res["created"]), ["Item1", "Item2"])


class TestInsertFromZnajdz(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\nWSTRZYKNIJ "Typ" = "S" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "RAM" = 200 DO "B"\nWSTRZYKNIJ "Typ" = "S" DO "B"\n'
            'POŁĄCZ "A" Z "B" JAKO "link"'
        )

    def test_wstaw_z_znajdz_clones(self):
        r = self.engine.execute('WSTAW Z (ZNAJDŹ GDZIE "Typ" = "S")')
        res = _last(r)
        self.assertEqual(res["action"], "INSERT_FROM")
        self.assertEqual(res["count"], 2)
        self.assertEqual(sorted(res["created"]), ["A_kopia", "B_kopia"])
        a_copy = _last(self.engine.execute('POKAŻ "A_kopia"'))
        self.assertEqual(a_copy["data"]["properties"]["RAM"], 100)
        rels = a_copy["data"]["relations"]
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["relation"], "link")

    def test_wstaw_z_znajdz_second_clone_suffix(self):
        self.engine.execute('WSTAW Z (ZNAJDŹ GDZIE "BĄBEL" = "A")')
        r = self.engine.execute('WSTAW Z (ZNAJDŹ GDZIE "BĄBEL" = "A")')
        self.assertEqual(_last(r)["created"], ["A_kopia_2"])

    def test_parser_insert_from(self):
        node = KarminParser().parse('WSTAW Z (ZNAJDŹ GDZIE "Typ" = "S")')[0][2]
        self.assertIsInstance(node, InsertFromNode)


class TestInsertFromSubstrate(unittest.TestCase):
    def test_engine_uses_karmazyn_store(self):
        store = kernel.Store(thermal=True)
        engine = KarminEngine(store)
        engine.execute('UTRWAL "X"\nWSTRZYKNIJ "V" = 1 DO "X"')
        self.assertEqual(len(store.bubbles), 1)
        self.assertGreater(store.stats()["total"], 0)


if __name__ == "__main__":
    unittest.main()