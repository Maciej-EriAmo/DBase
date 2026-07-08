#!/usr/bin/env python3
"""
cynober_server.py — Bezpieczny Serwer Bazy Danych Cynober DB (v7.0)
==========================================================================
Zastępuje serwer HTTP. Wykorzystuje protokół TCP oraz warstwę kryptograficzną
z karmazyn_handshake.py (Ring-LWE / ECDH / PBKDF2) do zabezpieczenia zapytań.

v7.0: każde połączenie RPC dostaje własny Store + KarminEngine (izolacja sesji).
"""

import os
import socket
import threading
import time
import karmazyn_kernel as kernel
from cynober_lambda_bridge import KarminLambdaBridge

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

class CynoberFacade:
    def __init__(self, session_label: str = ""):
        self.session_label = session_label or "anonymous"
        self.bridge = KarminLambdaBridge(kernel.Store(thermal=True))
        self.store = self.bridge.store
        self.engine = self.bridge.engine
        self._lock = threading.Lock()

    def execute(self, query: str) -> list:
        with self._lock:
            return self._execute_unlocked(query)

    def _execute_unlocked(self, query: str) -> list:
        if self.bridge.is_lambda_line(query):
            return [self.bridge.eval_line(query)]

        upper_query = query.strip().upper()
        if upper_query == "STATYSTYKI":
            stats = self.store.stats()
            return [{"status": "ok", "action": "STATS", "data": {
                "total_atoms": stats['total'], "hot": stats['hot'], "cold": stats['cold'],
                "reaped": stats['reaped'], "bubbles": len(self.engine.api._bubble_index),
                "session_label": self.session_label,
                "session_isolated": True,
                "active_sessions": _session_manager.active_count,
            }}]
        if upper_query.startswith("TICK"):
            parts = upper_query.split()
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            self.store.settle(n)
            return [{"status": "ok", "action": "TICK", "cycles": n}]
        if upper_query.startswith("ZAPISZ"):
            parts = query.split()
            path = parts[1] if len(parts) > 1 else "zrzut_cynober.kafd"
            import karmazyn_store
            syn_ids = []
            try:
                for nazwa, b in self.engine.api._bubble_index.items():
                    syn = self.store.atom_new(S="__bubble__", E=nazwa, value=nazwa)
                    syn.metadata["bindings"] = b.bindings
                    syn_ids.append(syn.id)
                aktywne_typy = list(set(a.S for a in self.store.reg.atoms() if a.S))
                if "__bubble__" not in aktywne_typy: aktywne_typy.append("__bubble__")
                zapisane = karmazyn_store.save_documents(self.store, path, kinds=aktywne_typy)
                return [{"status": "ok", "action": "SAVE", "file": path, "saved": zapisane}]
            except Exception as e:
                return [{"status": "error", "message": f"Błąd zapisu: {e}"}]
            finally:
                for sid in syn_ids:
                    self.store.reg.delete(sid)
        if upper_query.startswith("WCZYTAJ"):
            parts = query.split()
            path = parts[1] if len(parts) > 1 else "zrzut_cynober.kafd"
            try:
                import karmazyn_store
                wczytane = karmazyn_store.load_documents(self.store, path)
                for a in list(self.store.reg.atoms()):
                    if a.S == "__bubble__":
                        nazwa = a.E
                        if nazwa not in self.engine.api._bubble_index:
                            b = self.store.bubble_new(label=nazwa)
                            b.bindings = a.metadata.get("bindings", {})
                            self.store.set_root(b)
                            self.engine.api._bubble_index[nazwa] = b
                        self.store.reg.delete(a.id)
                return [{"status": "ok", "action": "LOAD", "file": path, "loaded": wczytane}]
            except Exception as e:
                return [{"status": "error", "message": f"Błąd odczytu: {e}"}]

        return self.engine.execute(query, strict=False)


class SessionManager:
    """Rejestr aktywnych sesji RPC — osobny CynoberFacade (Store) na połączenie."""

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
            _session_manager.release()
        conn.close()

def run_server(host='0.0.0.0', port=8080):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    limiter = _get_rate_limiter()
    rl = limiter.cfg
    print("=" * 60)
    print(f"  Cynober DB SECURE Server v7.0 działa na porcie {port}")
    print("  Nasłuch w standardzie Karmazyn Handshake RPC.")
    print("  Izolacja sesji: osobny Store na każde połączenie TCP.")
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