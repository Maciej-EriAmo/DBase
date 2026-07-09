#!/usr/bin/env python3
"""
cynober_server.py — Bezpieczny Serwer Bazy Danych Cynober DB (v7.1)
==========================================================================
Zastępuje serwer HTTP. Wykorzystuje protokół TCP oraz warstwę kryptograficzną
z karmazyn_handshake.py (Ring-LWE / ECDH / PBKDF2) do zabezpieczenia zapytań.

v7.0: każde połączenie RPC dostaje własny Store + KarminEngine (izolacja sesji).
v7.1: trwałe, nazwane światy — WYBIERZ ŚWIAT / UTWÓRZ ŚWIAT (współdzielony stan).
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time

import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge
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

        world_resp = self._try_world_command(stripped, upper)
        if world_resp is not None:
            return world_resp

        rt = self._runtime()
        with rt.lock:
            results = self._execute_on_runtime(rt, stripped, upper)
        if self._world is not None and self._should_mark_dirty(upper, results):
            self._registry.mark_dirty(self._world.name)
        return results

    def _try_world_command(self, stripped: str, upper: str) -> list | None:
        if upper == "LISTA ŚWIATÓW":
            worlds = self._registry.list_worlds()
            return [{
                "status": "ok",
                "action": "LIST_WORLDS",
                "worlds": worlds,
                "worlds_dir": str(self._registry.base_dir),
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
    print(f"  Cynober DB SECURE Server v7.1 działa na porcie {port}")
    print("  Nasłuch w standardzie Karmazyn Handshake RPC.")
    print("  Izolacja sesji: osobny executor na każde połączenie TCP.")
    print(f"  Trwałe światy: {worlds_dir}")
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