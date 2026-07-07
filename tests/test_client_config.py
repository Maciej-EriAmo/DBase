"""Testy cynober_client_config."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cynober_client_config as cfg


class TestClientConfig(unittest.TestCase):
    def test_resolve_argv_overrides_profile(self):
        host, port, label = cfg.resolve_client_target(["192.168.0.5", "9090"])
        self.assertEqual(host, "192.168.0.5")
        self.assertEqual(port, 9090)
        self.assertEqual(label, "(argv)")

    def test_upsert_and_load_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            with patch.object(cfg, "CONFIG_PATH", path):
                cfg.upsert_profile("test", "10.0.0.2", 8081, note="lan")
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["active"], "test")
                self.assertEqual(data["profiles"]["test"]["host"], "10.0.0.2")

                host, port, name = cfg.resolve_client_target([])
                self.assertEqual((host, port, name), ("10.0.0.2", 8081, "test"))

    @patch.dict(os.environ, {"CYNOBER_HOST": "10.1.1.1", "CYNOBER_PORT": "7777"})
    def test_env_fallback(self):
        with patch.object(cfg, "CONFIG_PATH", Path("/nonexistent/karmazyn_client.json")):
            host, port, label = cfg.resolve_client_target([])
        self.assertEqual(host, "10.1.1.1")
        self.assertEqual(port, 7777)
        self.assertEqual(label, "(env)")

    def test_server_config_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            with patch.object(cfg, "CONFIG_PATH", path):
                data = cfg.load_config()
                self.assertIn("server", data)
                self.assertEqual(data["server"]["bind_host"], "0.0.0.0")

    def test_resolve_server_bind_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            with patch.object(cfg, "CONFIG_PATH", path):
                cfg.upsert_server_config("0.0.0.0", 9099)
                host, port, label = cfg.resolve_server_bind([])
                self.assertEqual((host, port, label), ("0.0.0.0", 9099, "(config)"))

    def test_list_local_ips_returns_list(self):
        ips = cfg.list_local_ips()
        self.assertIsInstance(ips, list)


if __name__ == "__main__":
    unittest.main()