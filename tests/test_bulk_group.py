"""Testy GROUP BY wielu kolumn i bulk INSERT (KarminQL v5.3)."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine, KarminParser, BulkInjectNode, BulkCreateNode


def _last(results):
    return results[-1] if results else {}


class TestMultiGroupBy(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "S" DO "A"\nWSTRZYKNIJ "Region" = "EU" DO "A"\nWSTRZYKNIJ "RAM" = 100 DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "S" DO "B"\nWSTRZYKNIJ "Region" = "US" DO "B"\nWSTRZYKNIJ "RAM" = 200 DO "B"\n'
            'UTRWAL "C"\nWSTRZYKNIJ "Typ" = "P" DO "C"\nWSTRZYKNIJ "Region" = "EU" DO "C"\nWSTRZYKNIJ "RAM" = 50 DO "C"'
        )

    def test_group_by_two_columns(self):
        r = self.engine.execute('SUMA "RAM" GDZIE "RAM" > 0 POGRUPUJ "Typ", "Region"')
        res = _last(r)["result"]
        self.assertEqual(res["S | EU"], 100)
        self.assertEqual(res["S | US"], 200)
        self.assertEqual(res["P | EU"], 50)

    def test_parser_multi_group(self):
        node = KarminParser().parse('SUMA "RAM" GDZIE "RAM" > 0 POGRUPUJ "Typ", "Region"')[0][2]
        self.assertEqual(node.group_by, ["Typ", "Region"])


class TestBulkInject(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))
        self.engine.execute('UTRWAL "Serwer"')

    def test_wstrzyknij_wiele(self):
        r = self.engine.execute('WSTRZYKNIJ WIELE "RAM" = 8192, "Typ" = "Prod", "CPU" = 8 DO "Serwer"')
        res = _last(r)
        self.assertEqual(res["action"], "BULK_INJECT")
        self.assertEqual(res["count"], 3)
        show = _last(self.engine.execute('POKAŻ "Serwer"'))
        props = show["data"]["properties"]
        self.assertEqual(props["RAM"], 8192)
        self.assertEqual(props["Typ"], "Prod")
        self.assertEqual(props["CPU"], 8)

    def test_parser_bulk_inject(self):
        node = KarminParser().parse('WSTRZYKNIJ WIELE "A" = 1, "B" = 2 DO "X"')[0][2]
        self.assertIsInstance(node, BulkInjectNode)
        self.assertEqual(node.target, "X")
        self.assertEqual(len(node.properties), 2)


class TestBulkCreate(unittest.TestCase):
    def setUp(self):
        self.engine = KarminEngine(kernel.Store(thermal=True))

    def test_utrwal_wiele_names_only(self):
        r = self.engine.execute('UTRWAL WIELE "A", "B", "C"')
        res = _last(r)
        self.assertEqual(res["count"], 3)
        self.assertEqual(sorted(res["created"]), ["A", "B", "C"])

    def test_utrwal_wiele_with_props(self):
        r = self.engine.execute(
            'UTRWAL WIELE "X" Z "RAM" = 100, "Typ" = "S" ORAZ "Y" Z "RAM" = 200, "Typ" = "P"'
        )
        res = _last(r)
        self.assertEqual(res["count"], 2)
        x = _last(self.engine.execute('POKAŻ "X"'))["data"]["properties"]
        y = _last(self.engine.execute('POKAŻ "Y"'))["data"]["properties"]
        self.assertEqual(x["RAM"], 100)
        self.assertEqual(y["Typ"], "P")

    def test_parser_bulk_create(self):
        node = KarminParser().parse('UTRWAL WIELE "A", "B"')[0][2]
        self.assertIsInstance(node, BulkCreateNode)
        self.assertEqual([e[0] for e in node.entries], ["A", "B"])


if __name__ == "__main__":
    unittest.main()