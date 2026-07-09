"""Sprawdza, że wszystkie moduły źródłowe są w pyproject.toml (PyPI)."""

import re
import unittest
from pathlib import Path


class TestPyPiModules(unittest.TestCase):
    def test_all_root_modules_listed_in_pyproject(self):
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        listed = set(re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"', pyproject.split("py-modules")[1].split("]")[0]))

        expected = {
            p.stem
            for p in root.glob("*.py")
            if p.name not in ("setup.py",)
        }
        missing = sorted(expected - listed)
        self.assertEqual(
            missing,
            [],
            f"Moduły brakujące w [tool.setuptools].py-modules: {missing}",
        )