"""Zapis 100 komórek i odczyt każdej z nich, także po zapisie .kafd."""

import tempfile
import unittest
from pathlib import Path

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge
from cynober_worlds import export_query_indexes, load_runtime_from_kafd, save_runtime_to_kafd

N = 100


def _cell_name(i: int) -> str:
    return f"C{i:03d}"


def _write_cells(engine) -> None:
    lines = []
    for i in range(N):
        name = _cell_name(i)
        lines.append(f'UTRWAL "{name}"')
        lines.append(f'WSTRZYKNIJ "nr" = {i} DO "{name}"')
        lines.append(f'WSTRZYKNIJ "tresc" = "komorka-{i}" DO "{name}"')
    engine.execute("\n".join(lines))


def _read_cells(engine) -> dict[str, tuple[int, str]]:
    found: dict[str, tuple[int, str]] = {}
    for i in range(N):
        name = _cell_name(i)
        show = engine.execute(f'POKAŻ "{name}"')[-1]
        props = show["data"]["properties"]
        found[name] = (props["nr"], props["tresc"])
    return found


def _assert_cells(test: unittest.TestCase, found: dict[str, tuple[int, str]]) -> None:
    test.assertEqual(len(found), N)
    for i in range(N):
        name = _cell_name(i)
        test.assertEqual(found[name], (i, f"komorka-{i}"))


class TestStoKomorek(unittest.TestCase):
    def test_zapis_i_odczyt_stu_komorek(self):
        engine = KarminLambdaBridge(kernel.Store(thermal=True)).engine
        _write_cells(engine)

        table = engine.execute(
            'WYPISZ "BĄBEL", "nr", "tresc" GDZIE "nr" >= 0 SORTUJ WEDŁUG "nr"'
        )[-1]
        self.assertEqual(table["count"], N)
        rows = table["rows"]
        self.assertEqual([row["BĄBEL"] for row in rows], [_cell_name(i) for i in range(N)])
        self.assertEqual([row["nr"] for row in rows], list(range(N)))
        self.assertEqual([row["tresc"] for row in rows], [f"komorka-{i}" for i in range(N)])

        _assert_cells(self, _read_cells(engine))

    def test_sto_komorek_po_zapisie_kafd(self):
        bridge = KarminLambdaBridge(kernel.Store(thermal=True))
        _write_cells(bridge.engine)
        indexes = export_query_indexes(bridge.engine.api)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sto.kafd"
            save_runtime_to_kafd(bridge, path, proca_cold=False)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)

            loaded = KarminLambdaBridge(kernel.Store(thermal=True))
            n, _folded = load_runtime_from_kafd(
                loaded,
                path,
                query_indexes=indexes,
                lazy=False,
            )
            self.assertGreaterEqual(n, N)
            _assert_cells(self, _read_cells(loaded.engine))


if __name__ == "__main__":
    unittest.main()
