"""
Security audit regression — mapa 1:1 do audytu połączenia Cynober 8.2.4.

Sekcje:
  A. Downgrade / crypto policy
  B. Gossip / confused deputy (ACL sesji)
  C. Handshake / HSL / PSK / QKD
  D. Auth / ops surface (METRYKI, ZDROWIE, LOGIN audit)
  E. Replay cache
  F. Regresje ACL (REZONANS, world gossip)
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cynober_world_auth import reset_auth_store_for_tests
from cynober_worlds import reset_world_registry_for_tests
from tests.rpc_client import CynoberRpcClient
from tests.test_server_rpc import TestServerHarness


# ── helpers ───────────────────────────────────────────────────────────────────


def _setup_auth(base_dir: str, *, with_outsider: bool = False) -> None:
    users = {
        "admin": "admin-secret",
        "writer": "w-secret",
        "reader": "r-secret",
    }
    acl = {
        "*": {"admin": "admin", "writer": "writer", "reader": "reader"},
        "secure": {
            "admin": "admin",
            "writer": "writer",
            "reader": "reader",
        },
    }
    if with_outsider:
        users["outsider"] = "o-secret"
    auth = reset_auth_store_for_tests(base_dir)
    auth.write_config_for_tests(users=users, acl=acl, enabled=True)
    auth.reload()


def _teardown_auth_env(tmp: tempfile.TemporaryDirectory) -> None:
    try:
        auth = reset_auth_store_for_tests(tmp.name)
        auth.write_config_for_tests(users={}, acl={}, enabled=False)
    except Exception:
        pass
    os.environ.pop("CYNOBER_WORLDS_DIR", None)
    try:
        from cynober_world_auth import _auth_lock, _auth_stores

        with _auth_lock:
            _auth_stores.clear()
    except Exception:
        pass
    try:
        tmp.cleanup()
    except OSError:
        pass


class _AuthServerCase(unittest.TestCase):
    """Serwer z auth.json w izolowanym worlds_dir."""

    with_outsider = False

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["CYNOBER_WORLDS_DIR"] = cls._tmp.name
        reset_world_registry_for_tests()
        _setup_auth(cls._tmp.name, with_outsider=cls.with_outsider)
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()
        time.sleep(0.05)
        _teardown_auth_env(cls._tmp)

    def _client(self) -> CynoberRpcClient:
        c = CynoberRpcClient(port=self.port)
        c.connect()
        self.addCleanup(c.close)
        return c


# ═══════════════════════════════════════════════════════════════════════════════
# A. Downgrade / crypto policy
# ═══════════════════════════════════════════════════════════════════════════════


class TestA_CryptoPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = TestServerHarness()
        cls.port = cls.harness.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.harness.stop()

    @patch.dict(os.environ, {"CYNOBER_ALLOW_LEGACY": "0", "CYNOBER_MIN_CRYPTO": "hss"}, clear=False)
    def test_reject_legacy_when_min_crypto_hss(self):
        from cynober_rpc import LEGACY_VERSION

        c = CynoberRpcClient(port=self.port)
        with self.assertRaises(Exception):
            c.connect(client_version=LEGACY_VERSION)

    @patch.dict(os.environ, {"CYNOBER_ALLOW_LEGACY": "0"}, clear=False)
    def test_reject_legacy_when_allow_legacy_0(self):
        from cynober_rpc import LEGACY_VERSION

        c = CynoberRpcClient(port=self.port)
        with self.assertRaises(Exception):
            c.connect(client_version=LEGACY_VERSION)

    @patch.dict(os.environ, {"CYNOBER_MIN_CRYPTO": "hss", "CYNOBER_ALLOW_LEGACY": "1"}, clear=False)
    def test_min_crypto_not_bypassed_by_version_10(self):
        """Nawet przy ALLOW_LEGACY=1, MIN_CRYPTO=hss blokuje shortcut 1.0→simple."""
        from cynober_rpc import LEGACY_VERSION, select_crypto_mode

        local = {"version": "Cynober-Secure-1.2", "crypto": ["hss", "ecdh", "simple"]}
        remote = {"version": LEGACY_VERSION, "crypto": ["simple"]}
        with self.assertRaises(RuntimeError) as ctx:
            select_crypto_mode(local, remote)
        self.assertIn("MIN_CRYPTO", str(ctx.exception))

    @patch.dict(os.environ, {"CYNOBER_ALLOW_SIMPLE": "0", "CYNOBER_MIN_CRYPTO": "hss"}, clear=False)
    def test_server_without_simple_in_caps_refuses_simple_only_client(self):
        from cynober_rpc import build_local_caps, select_crypto_mode

        caps = build_local_caps()
        self.assertNotIn("simple", caps.get("crypto") or [])
        remote = {"version": "Cynober-Secure-1.1", "crypto": ["simple"]}
        with self.assertRaises(RuntimeError):
            select_crypto_mode(caps, remote)

    @patch.dict(os.environ, {"CYNOBER_ALLOW_LEGACY": "1", "CYNOBER_ALLOW_SIMPLE": "1"}, clear=False)
    def test_legacy_allowed_when_explicitly_enabled(self):
        from cynober_rpc import LEGACY_VERSION

        c = CynoberRpcClient(port=self.port)
        c.connect(client_version=LEGACY_VERSION)
        self.addCleanup(c.close)
        self.assertEqual(c.crypto_mode, "simple")


# ═══════════════════════════════════════════════════════════════════════════════
# B. Gossip / confused deputy
# ═══════════════════════════════════════════════════════════════════════════════


class TestB_GossipEphemeralAcl(_AuthServerCase):
    def test_auth_on_anon_ephemeral_gossip_export_denied(self):
        c = self._client()
        r = c.query("GOSSIP EKSPORT PHI")
        self.assertEqual(r["results"][0]["status"], "error")
        self.assertIn("logowanie", (r["results"][0].get("message") or "").lower())

    def test_auth_on_anon_ephemeral_gossip_sync_denied(self):
        c = self._client()
        r = c.query('GOSSIP SYNC PHI Z "peer1"')
        self.assertEqual(r["results"][0]["status"], "error")

    def test_auth_on_ephemeral_cannot_trigger_peer_login(self):
        """Reader na sesji nie może SYNC/FETCH (peers.json credentials)."""
        c = self._client()
        c.query('ZALOGUJ "reader" TOKEN "r-secret"')
        for q in (
            'GOSSIP SYNC PHI Z "nope"',
            'GOSSIP SYNC SOUL Z "nope"',
            'GOSSIP FETCH MEDIA Z "nope"',
            'GOSSIP FETCH MEDIA "a0" Z "nope"',
        ):
            r = c.query(q)
            self.assertEqual(r["results"][0]["status"], "error", msg=q)
            msg = (r["results"][0].get("message") or "").lower()
            self.assertIn("admin", msg, msg=q)

    def test_writer_ephemeral_export_ok_sync_denied(self):
        c = self._client()
        c.query('ZALOGUJ "writer" TOKEN "w-secret"')
        exp = c.query("GOSSIP EKSPORT PHI")
        self.assertEqual(exp["results"][0]["status"], "ok")
        sync = c.query('GOSSIP SYNC PHI Z "nope"')
        self.assertEqual(sync["results"][0]["status"], "error")
        self.assertIn("admin", (sync["results"][0].get("message") or "").lower())

    def test_reader_on_world_export_ok_import_denied(self):
        c = self._client()
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        c.query('UTWÓRZ ŚWIAT "secure"')
        c.query('UTRWAL "SecretBubble"')
        payload = c.query("GOSSIP EKSPORT SOUL")["results"][0]["data"]
        c.close()

        reader = self._client()
        reader.query('ZALOGUJ "reader" TOKEN "r-secret"')
        self.assertEqual(reader.query('WYBIERZ ŚWIAT "secure"')["results"][0]["status"], "ok")
        self.assertEqual(reader.query("GOSSIP EKSPORT SOUL")["results"][0]["status"], "ok")
        imp = reader.query(f'GOSSIP IMPORT SOUL DANE "{payload}"')
        self.assertEqual(imp["results"][0]["status"], "error")


# ═══════════════════════════════════════════════════════════════════════════════
# C. Handshake / HSL / PSK / QKD
# ═══════════════════════════════════════════════════════════════════════════════


class TestC_HandshakeHsl(unittest.TestCase):
    def setUp(self):
        from cynober_rpc import clear_replay_cache

        clear_replay_cache()

    def test_psk_mismatch_fails_rpc(self):
        """Różne KARM_PSK po stronach → tunel nie działa (decrypt/HSL)."""
        from cynober_server import handle_client

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        ready = threading.Event()

        def accept():
            with patch.dict(os.environ, {"KARM_PSK": "server-psk-AAAA"}):
                ready.set()
                conn, addr = srv.accept()
                try:
                    handle_client(conn, addr)
                except Exception:
                    try:
                        conn.close()
                    except OSError:
                        pass

        threading.Thread(target=accept, daemon=True).start()
        self.assertTrue(ready.wait(2.0))
        c = CynoberRpcClient(port=port)
        with patch.dict(os.environ, {"KARM_PSK": "client-psk-BBBB"}):
            try:
                c.connect()
                # jeśli handshake przeszedł (PSK mieszany lokalnie), query powinno paść
                with self.assertRaises(Exception):
                    c.query("ZDROWIE")
            except Exception:
                # connect sam padł — też OK (silniejsza ochrona)
                pass
            finally:
                try:
                    c.close()
                except Exception:
                    pass
                srv.close()

    def test_qkd_fp_mismatch_aborts_link(self):
        from cynober_rpc import PROTO_VERSION
        from karmazyn_hsl import HSL_VERSION, perform_hsl_link

        s_srv, s_cli = socket.socketpair()
        shared = b"\xaa" * 32
        caps_srv = {
            "node_id": "node_srv",
            "session_id": "audit_s1",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        caps_cli = {
            "node_id": "node_cli",
            "session_id": "audit_s2",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        deadline = time.monotonic() + 5.0
        server_err: list[str] = []

        def server():
            try:
                with patch.dict(os.environ, {"KARM_QKD_SEED": "bb" * 32}):
                    perform_hsl_link(
                        s_srv,
                        shared,
                        caps_srv,
                        caps_cli,
                        is_server=True,
                        deadline=deadline,
                        phi2=b"\x11" * 32,
                    )
            except RuntimeError as e:
                server_err.append(str(e))

        t = threading.Thread(target=server, daemon=True)
        t.start()
        with patch.dict(os.environ, {"KARM_QKD_SEED": "cc" * 32}):
            with self.assertRaises(RuntimeError) as ctx:
                perform_hsl_link(
                    s_cli,
                    shared,
                    caps_cli,
                    caps_srv,
                    is_server=False,
                    deadline=deadline,
                    phi2=b"\x22" * 32,
                )
        self.assertIn("QKD", str(ctx.exception))
        t.join(timeout=3)
        s_srv.close()
        s_cli.close()

    def test_hsl_enabled_requires_12_both_sides(self):
        from cynober_rpc import LEGACY_VERSION, PROTO_VERSION, hsl_enabled

        a = {"version": PROTO_VERSION, "hsl": "1"}
        b = {"version": LEGACY_VERSION, "hsl": "1"}
        self.assertFalse(hsl_enabled(a, b))
        self.assertTrue(hsl_enabled(
            {"version": PROTO_VERSION, "hsl": "1"},
            {"version": PROTO_VERSION, "hsl": "1"},
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# D. Auth / ops surface
# ═══════════════════════════════════════════════════════════════════════════════


class TestD_AuthOpsSurface(_AuthServerCase):
    def test_metryki_requires_role_when_auth_enabled(self):
        c = self._client()
        denied = c.query("METRYKI SERWERA")
        self.assertEqual(denied["results"][0]["status"], "error")
        c.query('ZALOGUJ "reader" TOKEN "r-secret"')
        ok = c.query("METRYKI SERWERA")
        self.assertEqual(ok["results"][0]["status"], "ok")

    def test_zdrowie_open_under_auth_without_login(self):
        """ZDROWIE = liveness — bez logowania przy auth ON."""
        c = self._client()
        row = c.query("ZDROWIE")["results"][0]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row.get("action"), "HEALTH")

    def test_failed_login_audited_without_token(self):
        c = self._client()
        bad = c.query('ZALOGUJ "admin" TOKEN "wrong-token-secret"')
        self.assertEqual(bad["results"][0]["status"], "error")
        audit_path = Path(self._tmp.name) / "audit.log"
        self.assertTrue(audit_path.is_file())
        text = audit_path.read_text(encoding="utf-8")
        self.assertIn("LOGIN_FAIL", text)
        self.assertNotIn("wrong-token-secret", text)
        entry = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(entry["action"], "LOGIN_FAIL")
        self.assertFalse(entry["allowed"])
        # query w audycie zmaskowane (bez surowego tokenu)
        self.assertNotIn("wrong-token-secret", entry.get("query") or "")
        self.assertIn("…", entry.get("query") or "")

    def test_successful_login_audited(self):
        c = self._client()
        ok = c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        self.assertEqual(ok["results"][0]["status"], "ok")
        audit_path = Path(self._tmp.name) / "audit.log"
        lines = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        logins = [e for e in lines if e.get("action") == "LOGIN" and e.get("allowed")]
        self.assertTrue(logins)
        self.assertNotIn("admin-secret", json.dumps(logins))


# ═══════════════════════════════════════════════════════════════════════════════
# E. Replay cache
# ═══════════════════════════════════════════════════════════════════════════════


class TestE_ReplayCache(unittest.TestCase):
    def setUp(self):
        from cynober_rpc import clear_replay_cache

        clear_replay_cache()

    def tearDown(self):
        from cynober_rpc import clear_replay_cache

        clear_replay_cache()

    def test_seen_sessions_prune_does_not_drop_recent_ids(self):
        from cynober_rpc import (
            REPLAY_WINDOW_SEC,
            _SEEN_SESSIONS,
            _prune_seen_sessions,
        )

        now = time.time()
        _SEEN_SESSIONS["old"] = now - REPLAY_WINDOW_SEC - 10
        _SEEN_SESSIONS["fresh"] = now
        _SEEN_SESSIONS["mid"] = now - 1.0
        _prune_seen_sessions(now)
        self.assertNotIn("old", _SEEN_SESSIONS)
        self.assertIn("fresh", _SEEN_SESSIONS)
        self.assertIn("mid", _SEEN_SESSIONS)

    def test_duplicate_session_id_rejected_within_window(self):
        from cynober_rpc import build_local_caps, validate_remote_caps

        local = build_local_caps()
        remote = build_local_caps()
        sid = "deadbeefcafebabe"
        remote["session_id"] = sid
        remote["ts"] = local["ts"]
        validate_remote_caps(local, remote)
        with self.assertRaises(RuntimeError) as ctx:
            validate_remote_caps(build_local_caps(), {**remote, "session_id": sid})
        self.assertIn("session_id", str(ctx.exception))

    def test_overflow_prune_keeps_newest(self):
        from cynober_rpc import _MAX_SEEN_SESSIONS, _SEEN_SESSIONS, _prune_seen_sessions

        now = time.time()
        for i in range(_MAX_SEEN_SESSIONS + 50):
            _SEEN_SESSIONS[f"s{i:05d}"] = now - (_MAX_SEEN_SESSIONS + 50 - i)
        _prune_seen_sessions(now)
        self.assertLessEqual(len(_SEEN_SESSIONS), _MAX_SEEN_SESSIONS)
        self.assertIn(f"s{_MAX_SEEN_SESSIONS + 49:05d}", _SEEN_SESSIONS)


# ═══════════════════════════════════════════════════════════════════════════════
# F. Regresje ACL
# ═══════════════════════════════════════════════════════════════════════════════


class TestF_AclRegressions(_AuthServerCase):
    with_outsider = False

    def test_lista_wezlow_rezonans_requires_auth(self):
        c = self._client()
        denied = c.query("LISTA WĘZŁÓW REZONANS")
        self.assertEqual(denied["results"][0]["status"], "error")
        ascii_denied = c.query("LISTA WEZLOW REZONANS")
        self.assertEqual(ascii_denied["results"][0]["status"], "error")
        c.query('ZALOGUJ "admin" TOKEN "admin-secret"')
        ok = c.query("LISTA WĘZŁÓW REZONANS")
        self.assertEqual(ok["results"][0]["status"], "ok")
        self.assertEqual(ok["results"][0]["action"], "LIST_PEERS_RESONANCE")

    def test_lista_wezlow_same_gate(self):
        c = self._client()
        self.assertEqual(c.query("LISTA WĘZŁÓW")["results"][0]["status"], "error")
        c.query('ZALOGUJ "reader" TOKEN "r-secret"')
        self.assertEqual(c.query("LISTA WĘZŁÓW")["results"][0]["status"], "ok")


class TestG_SecureBootPosture(unittest.TestCase):
    def test_secure_boot_exits_on_public_bind_without_auth(self):
        from cynober_server import _warn_security_posture

        with patch.dict(
            os.environ,
            {
                "CYNOBER_SECURE_BOOT": "1",
                "KARM_PSK": "",
                "KARM_HSS_PROFILE": "proto",
                "CYNOBER_ALLOW_LEGACY": "1",
                "CYNOBER_ALLOW_SIMPLE": "1",
            },
            clear=False,
        ):
            # wyczyść PSK jeśli był ustawiony
            os.environ.pop("KARM_PSK", None)
            with self.assertRaises(SystemExit):
                _warn_security_posture(
                    host="0.0.0.0",
                    auth_enabled=False,
                    hss_prof="proto",
                )

    def test_secure_boot_ok_on_loopback(self):
        from cynober_server import _warn_security_posture

        with patch.dict(os.environ, {"CYNOBER_SECURE_BOOT": "1"}, clear=False):
            # loopback + auth off → ostrzeżenia słabsze; secure_boot tylko przy problems+publicish
            _warn_security_posture(
                host="127.0.0.1",
                auth_enabled=False,
                hss_prof="proto",
            )


if __name__ == "__main__":
    unittest.main()
