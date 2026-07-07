"""Testy end-to-end silnika KarminQL (KarminEngine)."""

import unittest

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine


def _last(results):
    return results[-1] if results else {}


class TestKarminEngine(unittest.TestCase):
    def setUp(self):
        self.store = kernel.Store(thermal=True)
        self.engine = KarminEngine(self.store)

    def test_crud_bubble_and_property(self):
        r = self.engine.execute(
            'UTRWAL "Encja"\n'
            'WSTRZYKNIJ "RAM" = 1024 DO "Encja"\n'
            'POKAŻ "Encja"'
        )
        show = _last(r)
        self.assertEqual(show["action"], "SHOW")
        self.assertEqual(show["data"]["properties"]["RAM"], 1024)

    def test_find_where(self):
        self.engine.execute(
            'UTRWAL "A"\n'
            'WSTRZYKNIJ "Typ" = "Serwer" DO "A"'
        )
        r = self.engine.execute('ZNAJDŹ GDZIE "Typ" = "Serwer"')
        self.assertEqual(_last(r)["matches"], ["A"])

    def test_aggregate_sum(self):
        self.engine.execute(
            'UTRWAL "A"\n'
            'WSTRZYKNIJ "RAM" = 100 DO "A"\n'
            'UTRWAL "B"\n'
            'WSTRZYKNIJ "RAM" = 200 DO "B"'
        )
        r = self.engine.execute('SUMA "RAM" GDZIE "RAM" > 0')
        self.assertEqual(_last(r)["result"], 300)

    def test_manual_transaction_rollback(self):
        self.engine.execute('UTRWAL "T"\nWSTRZYKNIJ "RAM" = 1024 DO "T"')
        self.engine.execute(
            'BEGIN\n'
            'WSTRZYKNIJ "RAM" = 9999 DO "T"\n'
            'ROLLBACK'
        )
        r = self.engine.execute('POKAŻ "T"')
        self.assertEqual(_last(r)["data"]["properties"]["RAM"], 1024)

    def test_auto_transaction_rolls_back_on_error(self):
        self.engine.execute('UTRWAL "T"\nWSTRZYKNIJ "X" = 1 DO "T"')
        r = self.engine.execute(
            'WSTRZYKNIJ "Y" = 2 DO "T"\n'
            'WSTRZYKNIJ "Z" = 3 DO "NieIstnieje"',
            strict=False,
        )
        self.assertEqual(_last(r)["status"], "error")
        show = self.engine.execute('POKAŻ "T"')
        props = _last(show)["data"]["properties"]
        self.assertEqual(props.get("X"), 1)
        self.assertNotIn("Y", props)

    def test_namespace_isolation(self):
        self.engine.execute(
            'UTRWAL PRZESTRZEŃ "ProjA"\n'
            'WYBIERZ PRZESTRZEŃ "ProjA"\n'
            'UTRWAL "BabelA"'
        )
        self.engine.execute('WYBIERZ PRZESTRZEŃ "DEFAULT"')
        with self.assertRaises(RuntimeError):
            self.engine.execute('POKAŻ "BabelA"', strict=True)

    def test_script_variable(self):
        self.engine.execute(
            'UTRWAL "A"\n'
            'WSTRZYKNIJ "Typ" = "X" DO "A"\n'
            'UTRWAL "B"\n'
            'WSTRZYKNIJ "Typ" = "Y" DO "B"'
        )
        r = self.engine.execute(
            'NIECH $tylko_x = ZNAJDŹ GDZIE "Typ" = "X"\n'
            'ZNAJDŹ GDZIE "BĄBEL" W $tylko_x'
        )
        self.assertEqual(_last(r)["matches"], ["A"])

    def test_graph_connect_and_find(self):
        self.engine.execute(
            'UTRWAL "Rodzic"\n'
            'UTRWAL "Dziecko"\n'
            'POŁĄCZ "Rodzic" Z "Dziecko" JAKO "syn"'
        )
        # Relacja jest zapisana na źródle: rel:syn:Dziecko na bąblu "Rodzic".
        r = self.engine.execute('ZNAJDŹ POŁĄCZONE JAKO "syn" Z "Dziecko"')
        self.assertEqual(_last(r)["matches"], ["Rodzic"])

    def test_relative_numeric_update(self):
        self.engine.execute(
            'UTRWAL "E"\n'
            'WSTRZYKNIJ "Liczba" = 100 DO "E"'
        )
        self.engine.execute('ZAKTUALIZUJ "Liczba" = +25 W "E"')
        r = self.engine.execute('POKAŻ "E"')
        self.assertEqual(_last(r)["data"]["properties"]["Liczba"], 125)

    def test_history_tracks_changes(self):
        self.engine.execute(
            'UTRWAL "E"\n'
            'WSTRZYKNIJ "Stan" = "A" DO "E"\n'
            'ZAKTUALIZUJ "Stan" = "B" W "E"'
        )
        r = self.engine.execute('HISTORIA "Stan" W "E"')
        statuses = [row["status"] for row in _last(r)["data"]]
        self.assertIn("CURRENT", statuses)
        self.assertIn("ARCHIVE", statuses)


if __name__ == "__main__":
    unittest.main()