#!/usr/bin/env python3
"""
cynober_client.py — oficjalny klient Cynober-Secure-1.2 (v7.7)
==============================================================
Jeden protokół: TCP + HSS + HSL + KarminQL-RPC.

  from cynober_client import connect, CynoberClient

  with connect() as c:
      print(c.query("ZDROWIE"))

  # lub z profilem ~/.karmazyn_client.json
  c = connect(profile="zespol")
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any, Optional

from cynober_rpc import (
    HS_TIMEOUT_SEC,
    PROTO_VERSION,
    RPC_TIMEOUT_SEC,
    SUPPORTED_VERSIONS,
    build_rpc_request,
    decrypt_rpc_response,
    encrypt_rpc_request,
    perform_handshake,
)
from karmazyn_handshake import (
    _CryptoLayer,
    _compress,
    _decompress,
    _recv_frame,
    _send_frame,
)


class CynoberClientError(ConnectionError):
    """Błąd połączenia lub protokołu Cynober."""


class CynoberClient:
    """Sesja RPC przez tunel HSS+HSL."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        timeout: float = RPC_TIMEOUT_SEC,
    ):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.crypto = _CryptoLayer()
        self.crypto_mode: Optional[str] = None
        self.hsl_link = None

    def connect(self, client_version: str = PROTO_VERSION) -> "CynoberClient":
        if client_version not in SUPPORTED_VERSIONS:
            raise CynoberClientError(f"Nieobsługiwana wersja klienta: {client_version!r}")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(HS_TIMEOUT_SEC)
        try:
            self.sock.connect((self.host, self.port))
        except OSError as e:
            raise CynoberClientError(f"Nie można połączyć z {self.host}:{self.port}: {e}") from e

        hs_deadline = time.monotonic() + HS_TIMEOUT_SEC
        try:
            self.crypto_mode, _, _, self.hsl_link = perform_handshake(
                self.sock,
                self.crypto,
                is_server=False,
                deadline=hs_deadline,
                client_version=client_version,
            )
        except (ConnectionError, ConnectionResetError, OSError) as e:
            raise CynoberClientError(
                "Serwer odrzucił połączenie — sprawdź wersję protokołu i profil HSS."
            ) from e
        except RuntimeError as e:
            raise CynoberClientError(str(e)) from e

        self.sock.settimeout(self.timeout)
        return self

    def query(self, text: str) -> dict:
        if not self.sock:
            raise CynoberClientError("Nie połączono — wywołaj connect()")
        req_blob = json.dumps(
            build_rpc_request(text, self.hsl_link), ensure_ascii=False
        ).encode("utf-8")
        enc_req = encrypt_rpc_request(self.crypto, _compress(req_blob), self.hsl_link)
        _send_frame(self.sock, enc_req)

        enc_resp = _recv_frame(self.sock)
        if not enc_resp:
            raise CynoberClientError("Pusta odpowiedź — serwer zamknął tunel")
        raw_resp = _decompress(
            decrypt_rpc_response(self.crypto, enc_resp, self.hsl_link)
        )
        return json.loads(raw_resp.decode("utf-8"))

    def query_line(self, text: str) -> dict:
        """Ostatni wiersz z results."""
        payload = self.query(text)
        results = payload.get("results") or []
        return results[-1] if results else {}

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> "CynoberClient":
        return self.connect()

    def __exit__(self, *args: Any) -> None:
        self.close()


# Alias kompatybilności z testami / starszym kodem
CynoberRpcClient = CynoberClient


def connect(
    host: Optional[str] = None,
    port: Optional[int] = None,
    *,
    profile: Optional[str] = None,
    timeout: float = RPC_TIMEOUT_SEC,
) -> CynoberClient:
    """
    Połącz z serwerem Cynober.

    Bez argumentów: aktywny profil z ~/.karmazyn_client.json.
    """
    from cynober_client_config import (
        apply_profile_secrets,
        apply_server_secrets,
        get_active_profile,
        get_server_config,
        load_config,
    )

    if host is not None and port is not None:
        apply_server_secrets()
        client = CynoberClient(host=host, port=port, timeout=timeout)
        return client.connect()

    cfg = load_config()
    if profile:
        if profile not in cfg.get("profiles", {}):
            raise CynoberClientError(f"Nieznany profil klienta: {profile}")
        cfg["active"] = profile
        from cynober_client_config import save_config
        save_config(cfg)

    _name, prof = get_active_profile(cfg)
    apply_profile_secrets(prof)

    h = host or str(prof.get("host", "127.0.0.1"))
    p = port if port is not None else int(prof.get("port", 8080))
    client = CynoberClient(host=h, port=p, timeout=timeout)
    client.connect()
    return client