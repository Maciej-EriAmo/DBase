"""Testy mostu λ ↔ KarminQL (v5.5)."""

import unittest

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge


class TestLambdaBridge(unittest.TestCase):
    def setUp(self):
        self.bridge = KarminLambdaBridge(kernel.Store(thermal=True))

    def test_lambda_arithmetic(self):
        r = self.bridge.eval_line("(+ 2 3)")
        self.assertEqual(r["action"], "LAMBDA")
        self.assertEqual(r["result"], "5")

    def test_karmin_builtin_from_lambda(self):
        self.bridge.engine.execute(
            'UTRWAL "A"\nWSTRZYKNIJ "Typ" = "Serwer" DO "A"\n'
            'UTRWAL "B"\nWSTRZYKNIJ "Typ" = "Plik" DO "B"'
        )
        r = self.bridge.eval_line('(karmin "ZNAJDŹ GDZIE \\"Typ\\" = \\"Serwer\\"")')
        self.assertIn("A", r["result"])

    def test_shared_store(self):
        self.bridge.engine.execute('UTRWAL "X"\nWSTRZYKNIJ "V" = 7 DO "X"')
        self.bridge.eval_line('(define n (karmin "POLICZ BĄBLE GDZIE \\"V\\" > 0"))')
        r = self.bridge.eval_line("n")
        self.assertEqual(r["result"], "1")

    def test_lambda_error(self):
        r = self.bridge.eval_line("(+ 1 nieznane)")
        self.assertEqual(r["status"], "error")


if __name__ == "__main__":
    unittest.main()