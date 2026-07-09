#!/usr/bin/env python3
"""
cynober_server.py — Bezpieczny Serwer Bazy Danych Cynober DB (v7.4)
==========================================================================
Zastępuje serwer HTTP. Wykorzystuje protokół TCP oraz warstwę kryptograficzną
z karmazyn_handshake.py (Ring-LWE / ECDH / PBKDF2) do zabezpieczenia zapytań.

v7.0: każde połączenie RPC dostaje własny Store + KarminEngine (izolacja sesji).
v7.1: trwałe, nazwane światy — WYBIERZ ŚWIAT / UTWÓRZ ŚWIAT (współdzielony stan).
v7.2: auth na światach — ZALOGUJ, role reader/writer/admin, ACL w auth.json.
v7.3: operacje — ZDROWIE, METRYKI SERWERA, kopie zapasowe światów.
v7.4: replikacja — LISTA WĘZŁÓW, PULL/PUSH/SYNC światów między serwerami.
v7.5: profile HSS (proto/standard/production), konfiguracja KARM_HSS_PROFILE.
v7.6: cynober_client.py — oficjalny SDK klienta (jeden protokół).
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge
from cynober_ops import (
    get_server_metrics,
    is_ops_admin_query,
    is_ops_query,
    is_ops_write_query,
    try_ops_command,
    world_from_ops_query,
)
from cynober_replicate import (
    get_peer_registry,
    is_replicate_admin_query,
    is_replicate_query,
    is_replicate_read_query,
    try_replicate_command,
    world_from_replicate_query,
)
from cynober_world_auth import (
    ROLE_ADMIN,
    ROLE_READER,
    ROLE_WRITER,
    _GRANT_RE,
    _LIST_ACL_WORLD_RE,
    _LOGIN_RE,
    _REVOKE_RE,
    get_auth_store,
    is_read_only_query,
    is_world_admin_command,
)
from cynober_worlds import (
    World,
    WorldRuntime,
    get_world_registry,
    load_runtime_from_kafd,
    save_runtime_to_kafd,
    validate_world_name,
)

from cynober_rpc import (
    HS_TIMEOUT_SEC,
    SUPPORTED_VERSIONS,
    decode_request,
    error_result,
    perform_handshake,
    send_encrypted_response,
    send_handshake_error,
)
from cynober_rate_limit import (
    SessionQueryLimiter,
    ServerRateLimiter,
    load_rate_limit_config,
)
from karmazyn_handshake import _CryptoLayer, _recv_frame

_MUTATING_PREFIXES = (
    "UTRWAL", "WSTRZYKNIJ", "ZAKTUALIZUJ", "USUŃ", "POŁĄCZ", "ROZŁĄCZ",
    "UTWÓRZ", "PRZEMIANUJ", "IMPORT", "EKSPORT", "SCAL", "BEGIN", "COMMIT",
    "ROLLBACK", "TICK", "WCZYTAJ", "ZAPISZ", "UTWÓRZ INDEKS", "USUŃ INDEKS",
    "UTWÓRZ WIDOK", "USUŃ WIDOK", "WYMAGAJ", "USUŃ WYMAGANIE",
)
_WORLD_QUOTED = re.compile(
    r'^(?:WYBIERZ|UTWÓRZ|USUŃ)\s+ŚWIAT\s+"([^"]+)"$',
    re.IGNORECASE,
)


class CynoberFacade:
    """Executor zapytań jednej sesji RPC — efemeryczny lub podłączony do świata."""

    def __init__(self, session_label: str = "", registry=None):
        self.session_label = session_label or "anonymous"
        self._registry = registry or get_world_registry()
        self._auth = get_auth_store(self._registry.base_dir)
        self._auth_user: str | None = None
        self._ephemeral = WorldRuntime(KarminLambdaBridge(kernel.Store(thermal=True)))
        self._world: World | None = None
        self._lock = threading.Lock()

    @property
    def world_name(self) -> str | None:
        return self._world.name if self._world else None

    def _runtime(self):
        if self._world is not None:
            return self._world.runtime
        return self._ephemeral

    def execute(self, query: str) -> list:
        with self._lock:
            return self._execute_unlocked(query)

    def _execute_unlocked(self, query: str) -> list:
        stripped = query.strip()
        upper = stripped.upper()

        auth_resp = self._try_auth_command(stripped, upper)
        if auth_resp is not None:
            return auth_resp

        ops_resp = self._try_ops_command(stripped, upper)
        if ops_resp is not None:
            return ops_resp

        repl_resp = self._try_replicate_command(stripped, upper)
        if repl_resp is not None:
            return repl_resp

        deny = self._check_permission(stripped, upper)
        if deny is not None:
            self._auth.audit(
                user=self._auth_user,
                world=self.world_name,
                action="DENY",
                query=stripped,
                allowed=False,
            )
            return deny

        world_resp = self._try_world_command(stripped, upper)
        if world_resp is not None:
            if world_resp[0].get("status") == "ok":
                self._auth.audit(
                    user=self._auth_user,
                    world=self.world_name,
                    action=world_resp[0].get("action", "WORLD"),
                    query=stripped,
                    allowed=True,
                )
            return world_resp

        rt = self._runtime()
        with rt.lock:
            results = self._execute_on_runtime(rt, stripped, upper)
        if self._world is not None and self._should_mark_dirty(upper, results):
            self._registry.mark_dirty(self._world.name)
        if self._world is not None and results and results[0].get("status") == "ok":
            self._auth.audit(
                user=self._auth_user,
                world=self.world_name,
                action=results[0].get("action", "QUERY"),
                query=stripped,
                allowed=True,
            )
        return results

    def _try_auth_command(self, stripped: str, upper: str) -> list | None:
        m = _LOGIN_RE.match(stripped)
        if m:
            user, token = m.group(1), m.group(2)
            if self._auth.verify_login(user, token):
                self._auth_user = user
                return [{"status": "ok", "action": "LOGIN", "user": user}]
            return [{"status": "error", "message": "Nieprawidłowy użytkownik lub token."}]

        if upper == "WYLOGUJ":
            self._auth_user = None
            return [{"status": "ok", "action": "LOGOUT"}]

        if upper == "KTO JESTEM":
            return [{
                "status": "ok",
                "action": "WHOAMI",
                "user": self._auth_user,
                "world": self.world_name,
                "role": self._auth.role_for(self._auth_user, self.world_name),
                "auth_enabled": self._auth.enabled,
            }]

        m = _GRANT_RE.match(stripped)
        if m:
            target, role, world = m.group(1), m.group(2), m.group(3)
            try:
                world = validate_world_name(world)
                self._auth.grant(self._auth_user or "", target, role, world)
                return [{
                    "status": "ok",
                    "action": "GRANT_ROLE",
                    "user": target,
                    "role": role,
                    "world": world,
                }]
            except (ValueError, PermissionError) as e:
                return [{"status": "error", "message": str(e)}]

        m = _REVOKE_RE.match(stripped)
        if m:
            target, world = m.group(1), m.group(2)
            try:
                world = validate_world_name(world)
                self._auth.revoke(self._auth_user or "", target, world)
                return [{
                    "status": "ok",
                    "action": "REVOKE_ROLE",
                    "user": target,
                    "world": world,
                }]
            except (ValueError, PermissionError) as e:
                return [{"status": "error", "message": str(e)}]

        if upper == "LISTA UPRAWNIEŃ":
            if not self._auth.has_min_role(self._auth_user, "*", ROLE_ADMIN):
                return [{"status": "error", "message": "Wymagana rola admin (globalna)."}]
            return [{
                "status": "ok",
                "action": "LIST_ACL",
                "grants": self._auth.list_acl(),
            }]

        m = _LIST_ACL_WORLD_RE.match(stripped)
        if m:
            world = validate_world_name(m.group(1))
            if not self._auth.has_min_role(self._auth_user, world, ROLE_ADMIN):
                if not self._auth.has_min_role(self._auth_user, "*", ROLE_ADMIN):
                    return [{"status": "error", "message": f"Brak uprawnień admin w świecie '{world}'."}]
            return [{
                "status": "ok",
                "action": "LIST_ACL",
                "world": world,
                "grants": self._auth.list_acl(world),
            }]

        return None

    def _try_ops_command(self, stripped: str, upper: str) -> list | None:
        if not is_ops_query(stripped, upper):
            return None

        deny = self._check_ops_permission(stripped, upper)
        if deny is not None:
            self._auth.audit(
                user=self._auth_user,
                world=world_from_ops_query(stripped),
                action="DENY",
                query=stripped,
                allowed=False,
            )
            return deny

        resp = try_ops_command(
            stripped,
            upper,
            self._registry,
            active_sessions=_session_manager.active_count,
            auth_enabled=self._auth.enabled,
        )
        if resp and resp[0].get("status") == "ok":
            self._auth.audit(
                user=self._auth_user,
                world=world_from_ops_query(stripped),
                action=resp[0].get("action", "OPS"),
                query=stripped,
                allowed=True,
            )
        return resp

    def _try_replicate_command(self, stripped: str, upper: str) -> list | None:
        if not is_replicate_query(stripped, upper):
            return None

        deny = self._check_replicate_permission(stripped, upper)
        if deny is not None:
            self._auth.audit(
                user=self._auth_user,
                world=world_from_replicate_query(stripped),
                action="DENY",
                query=stripped,
                allowed=False,
            )
            return deny

        resp = try_replicate_command(
            stripped,
            upper,
            self._registry,
            get_peer_registry(self._registry.base_dir),
        )
        if resp and resp[0].get("status") == "ok":
            self._auth.audit(
                user=self._auth_user,
                world=world_from_replicate_query(stripped),
                action=resp[0].get("action", "REPLICATE"),
                query=stripped,
                allowed=True,
            )
        return resp

    def _check_replicate_permission(self, stripped: str, upper: str) -> list | None:
        if not self._auth.enabled:
            return None

        if upper == "LISTA WĘZŁÓW":
            if self._auth_user and self._auth.has_min_role(self._auth_user, "*", ROLE_READER):
                return None
            if not self._auth_user:
                return [{"status": "error", "message": "Wymagane logowanie: ZALOGUJ \"user\" TOKEN \"...\"."}]
            return [{"status": "error", "message": "LISTA WĘZŁÓW wymaga globalnej roli reader."}]

        if is_replicate_admin_query(stripped) and not world_from_replicate_query(stripped):
            if not self._auth_user:
                return [{"status": "error", "message": "Wymagane logowanie."}]
            if not self._auth.has_min_role(self._auth_user, "*", ROLE_ADMIN):
                return [{"status": "error", "message": "Zarządzanie węzłami wymaga globalnej roli admin."}]
            return None

        world = world_from_replicate_query(stripped)
        if world is None:
            return None

        if not self._auth_user:
            return [{"status": "error", "message": "Wymagane logowanie: ZALOGUJ \"user\" TOKEN \"...\"."}]

        if is_replicate_read_query(stripped, upper) and not is_replicate_admin_query(stripped):
            if self._auth.has_min_role(self._auth_user, world, ROLE_READER):
                return None
            return [{"status": "error", "message": f"EKSPORT wymaga roli reader w '{world}'."}]

        if self._auth.has_min_role(self._auth_user, world, ROLE_ADMIN):
            return None
        if is_replicate_admin_query(stripped):
            return [{"status": "error", "message": f"Replikacja wymaga roli admin w '{world}'."}]
        return None

    def _check_ops_permission(self, stripped: str, upper: str) -> list | None:
        if not self._auth.enabled:
            return None
        if upper in ("ZDROWIE", "METRYKI SERWERA"):
            return None

        world = world_from_ops_query(stripped)
        if world is None:
            return None

        if not self._auth_user:
            return [{"status": "error", "message": "Wymagane logowanie: ZALOGUJ \"user\" TOKEN \"...\"."}]

        if is_ops_admin_query(stripped):
            if not self._auth.has_min_role(self._auth_user, world, ROLE_ADMIN):
                return [{
                    "status": "error",
                    "message": f"PRZYWRÓĆ ŚWIAT wymaga roli admin w '{world}'.",
                }]
            return None

        if is_ops_write_query(stripped):
            if not self._auth.has_min_role(self._auth_user, world, ROLE_WRITER):
                return [{
                    "status": "error",
                    "message": f"KOPIA ZAPASOWA wymaga roli writer w '{world}'.",
                }]
            return None

        if not self._auth.has_min_role(self._auth_user, world, ROLE_READER):
            return [{"status": "error", "message": f"Brak dostępu do świata '{world}'."}]
        return None

    def _check_permission(self, stripped: str, upper: str) -> list | None:
        if is_ops_query(stripped, upper) or is_replicate_query(stripped, upper):
            return None
        if not self._auth.enabled:
            return None

        if upper.startswith("ZALOGUJ") or upper in ("WYLOGUJ", "KTO JESTEM"):
            return None

        if upper.startswith(("WYBIERZ ŚWIAT", "UTWÓRZ ŚWIAT", "USUŃ ŚWIAT")):
            return self._check_world_access_command(stripped, upper)

        if self._world is None:
            if is_world_admin_command(upper):
                return self._check_world_admin_permission(upper)
            return None

        if upper == "ODŁĄCZ ŚWIAT":
            if self._auth.has_min_role(self._auth_user, self._world.name, ROLE_READER):
                return None
            return [{"status": "error", "message": "Brak uprawnień do tego świata."}]

        if is_world_admin_command(upper):
            return self._check_world_admin_permission(upper)

        if is_read_only_query(upper):
            if not self._auth.has_min_role(self._auth_user, self._world.name, ROLE_READER):
                return [{"status": "error", "message": "Brak uprawnień do odczytu tego świata."}]
            return None

        if not self._auth.has_min_role(self._auth_user, self._world.name, ROLE_WRITER):
            return [{"status": "error", "message": "Brak uprawnień do zapisu w tym świecie (wymagana rola writer)."}]
        return None

    def _check_world_access_command(self, stripped: str, upper: str) -> list | None:
        if not self._auth_user:
            return [{"status": "error", "message": "Wymagane logowanie: ZALOGUJ \"user\" TOKEN \"...\"."}]

        if upper.startswith("UTWÓRZ ŚWIAT"):
            if not self._auth.has_min_role(self._auth_user, "*", ROLE_ADMIN):
                return [{"status": "error", "message": "UTWÓRZ ŚWIAT wymaga globalnej roli admin."}]
            return None

        if upper.startswith("USUŃ ŚWIAT"):
            m = _WORLD_QUOTED.match(stripped)
            if m:
                world = validate_world_name(m.group(1))
                if not self._auth.has_min_role(self._auth_user, world, ROLE_ADMIN):
                    return [{"status": "error", "message": f"USUŃ ŚWIAT wymaga roli admin w '{world}'."}]
            return None

        if upper.startswith("WYBIERZ ŚWIAT"):
            m = _WORLD_QUOTED.match(stripped)
            if m:
                world = validate_world_name(m.group(1))
                kafd = self._registry.base_dir / f"{world}.kafd"
                if not kafd.is_file():
                    if not self._auth.has_min_role(self._auth_user, "*", ROLE_ADMIN):
                        return [{
                            "status": "error",
                            "message": "Tworzenie nowego świata wymaga globalnej roli admin.",
                        }]
                elif not self._auth.has_min_role(self._auth_user, world, ROLE_READER):
                    return [{"status": "error", "message": f"Brak dostępu do świata '{world}'."}]
            return None

        return None

    def _check_world_admin_permission(self, upper: str) -> list | None:
        if not self._auth_user:
            return [{"status": "error", "message": "Wymagane logowanie."}]
        if self._world and self._auth.has_min_role(self._auth_user, self._world.name, ROLE_ADMIN):
            return None
        if self._auth.has_min_role(self._auth_user, "*", ROLE_ADMIN):
            return None
        return [{"status": "error", "message": "Wymagana rola admin."}]

    def _try_world_command(self, stripped: str, upper: str) -> list | None:
        if upper == "LISTA ŚWIATÓW":
            worlds = self._registry.list_worlds()
            if self._auth.enabled:
                if not self._auth_user:
                    worlds = []
                else:
                    allowed = self._auth.worlds_for(self._auth_user)
                    if allowed is not None:
                        worlds = [w for w in worlds if w["name"] in allowed]
            return [{
                "status": "ok",
                "action": "LIST_WORLDS",
                "worlds": worlds,
                "worlds_dir": str(self._registry.base_dir),
                "auth_enabled": self._auth.enabled,
            }]

        if upper == "ODŁĄCZ ŚWIAT":
            if self._world is None:
                return [{"status": "error", "message": "Sesja nie jest podłączona do świata."}]
            name = self._world.name
            self._registry.release(name)
            self._world = None
            self._ephemeral = WorldRuntime(KarminLambdaBridge(kernel.Store(thermal=True)))
            return [{"status": "ok", "action": "DETACH_WORLD", "world": name}]

        if upper == "ZAPISZ ŚWIAT":
            if self._world is None:
                return [{"status": "error", "message": "Brak aktywnego świata (użyj WYBIERZ ŚWIAT)."}]
            try:
                info = self._registry.flush(self._world.name)
                return [{"status": "ok", "action": "SAVE_WORLD", **info}]
            except ValueError as e:
                return [{"status": "error", "message": str(e)}]

        m = _WORLD_QUOTED.match(stripped)
        if m:
            name = validate_world_name(m.group(1))
            cmd = upper.split()[0]
            try:
                if cmd == "WYBIERZ":
                    return self._attach_world(name, create_if_missing=True)
                if cmd == "UTWÓRZ":
                    return self._attach_world(name, create_if_missing=False, force_create=True)
                if cmd == "USUŃ":
                    return self._delete_world(name)
            except ValueError as e:
                return [{"status": "error", "message": str(e)}]

        return None

    def _attach_world(
        self,
        name: str,
        *,
        create_if_missing: bool = False,
        force_create: bool = False,
    ) -> list:
        if self._world is not None:
            if self._world.name == name:
                return [{
                    "status": "ok",
                    "action": "ATTACH_WORLD",
                    "world": name,
                    "already_attached": True,
                }]
            self._registry.release(self._world.name)
            self._world = None

        if force_create:
            world = self._registry.create(name)
        else:
            kafd = self._registry.base_dir / f"{name}.kafd"
            if not kafd.is_file() and not create_if_missing:
                return [{
                    "status": "error",
                    "message": f"Świat '{name}' nie istnieje. Użyj UTWÓRZ ŚWIAT lub WYBIERZ z nową nazwą.",
                }]
            world = self._registry.attach(name)
            if not kafd.is_file():
                self._registry.mark_dirty(name)

        self._world = world
        bubbles = len(world.runtime.engine.api._bubble_index)
        return [{
            "status": "ok",
            "action": "ATTACH_WORLD",
            "world": name,
            "bubbles": bubbles,
            "created": force_create or bubbles == 0,
        }]

    def _delete_world(self, name: str) -> list:
        if self._world is not None and self._world.name == name:
            self._registry.release(name)
            self._world = None
            self._ephemeral = WorldRuntime(KarminLambdaBridge(kernel.Store(thermal=True)))
        try:
            self._registry.delete(name)
        except ValueError as e:
            return [{"status": "error", "message": str(e)}]
        return [{"status": "ok", "action": "DELETE_WORLD", "world": name}]

    def _execute_on_runtime(self, rt, query: str, upper: str) -> list:
        bridge = rt.bridge
        if bridge.is_lambda_line(query):
            return [bridge.eval_line(query)]

        if upper == "STATYSTYKI":
            stats = bridge.store.stats()
            return [{"status": "ok", "action": "STATS", "data": {
                "total_atoms": stats["total"],
                "hot": stats["hot"],
                "cold": stats["cold"],
                "reaped": stats["reaped"],
                "bubbles": len(bridge.engine.api._bubble_index),
                "session_label": self.session_label,
                "session_isolated": self._world is None,
                "world": self.world_name,
                "persistent_worlds": self._registry.persistent_count,
                "loaded_worlds": self._registry.loaded_count,
                "active_sessions": _session_manager.active_count,
                "worlds_dir": str(self._registry.base_dir),
                "auth_enabled": self._auth.enabled,
                "auth_user": self._auth_user,
                "auth_role": self._auth.role_for(self._auth_user, self.world_name),
            }}]

        if upper.startswith("TICK"):
            parts = upper.split()
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            bridge.store.settle(n)
            return [{"status": "ok", "action": "TICK", "cycles": n}]

        if upper.startswith("ZAPISZ"):
            parts = query.split(maxsplit=1)
            path = parts[1].strip() if len(parts) > 1 else "zrzut_cynober.kafd"
            if self._world is not None and path in ("", "zrzut_cynober.kafd"):
                info = self._registry.flush(self._world.name)
                return [{"status": "ok", "action": "SAVE_WORLD", **info}]
            try:
                saved = save_runtime_to_kafd(bridge, path)
                return [{"status": "ok", "action": "SAVE", "file": path, "saved": saved}]
            except Exception as e:
                return [{"status": "error", "message": f"Błąd zapisu: {e}"}]

        if upper.startswith("WCZYTAJ"):
            parts = query.split(maxsplit=1)
            path = parts[1].strip() if len(parts) > 1 else "zrzut_cynober.kafd"
            try:
                loaded = load_runtime_from_kafd(bridge, path)
                return [{"status": "ok", "action": "LOAD", "file": path, "loaded": loaded}]
            except Exception as e:
                return [{"status": "error", "message": f"Błąd odczytu: {e}"}]

        return bridge.engine.execute(query, strict=False)

    @staticmethod
    def _should_mark_dirty(upper: str, results: list) -> bool:
        if any(r.get("status") == "error" for r in results):
            return False
        if upper.startswith("WYJAŚNIJ") or upper.startswith("EXPLAIN"):
            return False
        if upper.startswith("POKAŻ") or upper.startswith("ZNAJDŹ") or upper.startswith("WYPISZ"):
            return False
        if upper.startswith("POLICZ") or upper == "STATYSTYKI" or upper == "OPISZ BAZĘ":
            return False
        if upper.startswith("SZUKAJ"):
            return False
        for prefix in _MUTATING_PREFIXES:
            if upper.startswith(prefix):
                return True
        for r in results:
            action = r.get("action", "")
            if action and action not in (
                "SHOW", "FIND_WHERE", "PROJECT_WHERE", "SEARCH", "DESCRIBE_DB",
                "EXPLAIN", "STATS", "AGGREGATE_COUNT", "AGGREGATE_SUM",
                "AGGREGATE_AVG", "AGGREGATE_MIN", "AGGREGATE_MAX",
            ):
                if not str(action).startswith("AGGREGATE_"):
                    return True
        return False

    def close(self) -> None:
        if self._world is not None:
            self._registry.release(self._world.name)
            self._world = None


class SessionManager:
    """Rejestr aktywnych sesji RPC — osobny CynoberFacade na połączenie."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = 0

    def create(self, session_label: str) -> CynoberFacade:
        with self._lock:
            self._active += 1
        return CynoberFacade(session_label=session_label)

    def release(self) -> None:
        with self._lock:
            if self._active > 0:
                self._active -= 1

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active


_session_manager = SessionManager()
_rate_limiter: ServerRateLimiter | None = None


def _get_rate_limiter() -> ServerRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        from cynober_client_config import get_server_config
        _rate_limiter = ServerRateLimiter(load_rate_limit_config(get_server_config()))
    return _rate_limiter


def handle_client(conn: socket.socket, addr, query_limit: SessionQueryLimiter | None = None):
    print(f"[Cynober] Połączenie przychodzące od {addr}")
    crypto = _CryptoLayer()
    tunnel_ready = False
    crypto_mode = "?"
    session_facade: CynoberFacade | None = None

    try:
        hs_deadline = time.monotonic() + HS_TIMEOUT_SEC
        crypto_mode, _, remote_caps, hsl_link = perform_handshake(
            conn, crypto, is_server=True, deadline=hs_deadline
        )
        tunnel_ready = True
        session_label = remote_caps.get("session_id") or f"{addr[0]}:{addr[1]}"
        session_facade = _session_manager.create(session_label)
        get_server_metrics().record_connection()
        psk_note = " [PSK]" if os.environ.get("KARM_PSK") else ""
        hsl_note = " + HSL" if hsl_link else ""
        qkd_note = " + QKD" if hsl_link and hsl_link.qkd_hybrid else ""
        print(f"[Cynober] Tunel zabezpieczony z {addr} ({crypto_mode.upper()}) "
              f"v{remote_caps.get('version')} sesja={session_label}{psk_note}{hsl_note}{qkd_note}")

        if query_limit is None:
            from cynober_client_config import get_server_config
            query_limit = SessionQueryLimiter(
                load_rate_limit_config(get_server_config())["max_queries_per_minute"]
            )

        while True:
            enc_req = _recv_frame(conn)
            if not enc_req:
                break

            ok, rl_msg = query_limit.allow()
            if not ok:
                print(f"[Cynober] Rate limit zapytań {addr}: {rl_msg}")
                get_server_metrics().record_rate_limit()
                send_encrypted_response(
                    conn, crypto, error_result(rl_msg, action="RATE_LIMIT"), hsl_link
                )
                break

            try:
                query = decode_request(enc_req, crypto, hsl_link)
                results = session_facade.execute(query)
            except ValueError as e:
                results = error_result(str(e))
            except Exception as e:
                results = error_result(f"Błąd wewnętrzny serwera: {e}", action="SERVER")

            get_server_metrics().record_query(results)
            send_encrypted_response(conn, crypto, results, hsl_link)

    except (ConnectionError, EOFError, TimeoutError):
        print(f"[Cynober] Klient {addr} rozłączył się.")
    except RuntimeError as e:
        if "Niezgodna wersja" in str(e):
            send_handshake_error(
                conn, "INCOMPATIBLE_VERSION", str(e),
                expected=list(SUPPORTED_VERSIONS),
            )
        print(f"[Cynober] Odrzucono handshake od {addr}: {e}")
    except Exception as e:
        if tunnel_ready:
            print(f"[Cynober] Błąd tunelu dla {addr}: {e}")
        else:
            print(f"[Cynober] Odrzucono handshake od {addr}: {e}")
    finally:
        if session_facade is not None:
            session_facade.close()
            _session_manager.release()
        conn.close()


def run_server(host='0.0.0.0', port=8080):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    limiter = _get_rate_limiter()
    rl = limiter.cfg
    worlds_dir = get_world_registry().base_dir
    print("=" * 60)
    auth = get_auth_store(worlds_dir)
    peers = get_peer_registry(worlds_dir)
    hss_prof = os.environ.get("KARM_HSS_PROFILE", "proto")
    print(f"  Cynober DB SECURE Server v7.6 działa na porcie {port}")
    print(f"  Profil HSS: {hss_prof}")
    print("  Nasłuch w standardzie Karmazyn Handshake RPC.")
    print("  Izolacja sesji: osobny executor na każde połączenie TCP.")
    print(f"  Trwałe światy: {worlds_dir}")
    n_peers = len(peers.list_peers())
    if n_peers:
        print(f"  Węzły replikacji: {n_peers} (peers.json)")
    if auth.enabled:
        print(f"  Auth światów: WŁĄCZONE ({auth.path})")
    else:
        print("  Auth światów: wyłączone (brak auth.json lub enabled=false)")
    if any(rl.values()):
        print(f"  Rate limit: global={rl['max_concurrent_global']} "
              f"ip={rl['max_connections_per_ip']} "
              f"conn/min={rl['max_new_connections_per_ip_per_min']} "
              f"q/min={rl['max_queries_per_minute']}")
    print("=" * 60)

    try:
        while True:
            conn, addr = srv.accept()
            ip = addr[0]
            ok, msg = limiter.acquire_connection(ip)
            if not ok:
                print(f"[Cynober] Odrzucono połączenie {addr}: {msg}")
                conn.close()
                continue

            from cynober_client_config import get_server_config
            rl_cfg = load_rate_limit_config(get_server_config())
            query_limit = SessionQueryLimiter(rl_cfg["max_queries_per_minute"])

            def _serve(connection, address, q_limit):
                try:
                    handle_client(connection, address, q_limit)
                finally:
                    limiter.release_connection(address[0])

            t = threading.Thread(
                target=_serve, args=(conn, addr, query_limit), daemon=True
            )
            t.start()
    except KeyboardInterrupt:
        print("\nZamykanie serwera...")
        srv.close()


if __name__ == '__main__':
    import sys
    from cynober_client_config import resolve_server_bind

    from cynober_client_config import CONFIG_PATH
    bind_host, port, source = resolve_server_bind(sys.argv[1:])
    if source == "(config)":
        print(f"[Cynober] Konfiguracja serwera ({CONFIG_PATH}) → {bind_host}:{port}")
    run_server(host=bind_host, port=port)