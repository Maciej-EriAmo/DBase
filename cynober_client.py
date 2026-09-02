#!/usr/bin/env python3
"""
cynober_client.py — oficjalny klient Cynober-Secure-1.2 (v8.2)
==============================================================
Jeden protokół: TCP (L0 Carrier) + HSS + HSL (+ KPC na serwerze) + KarminQL-RPC.
Media: put_media / get_media przez KAFS (negocjacja features).

  from cynober_client import connect, CynoberClient

  # Stała sesja (wiele query na jednym TCP+HSL):
  c = connect()
  try:
      print(c.query("ZDROWIE"))
      print(c.query("LISTA ŚWIATÓW"))  # ten sam socket
  finally:
      c.close()

  # with connect() zamyka tunel przy wyjściu z bloku — OK do jednorazówek.
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any, Optional

from cynober_rpc import (
    FRAME_KAFS,
    FRAME_RPC,
    HS_TIMEOUT_SEC,
    KAFS_CHUNK_MAX,
    PROTO_VERSION,
    RPC_TIMEOUT_SEC,
    SUPPORTED_VERSIONS,
    apply_tcp_keepalive,
    decode_rpc_response_frame,
    encode_kafs_request_frame,
    encode_rpc_request_frame,
    features_of,
    kafs_negotiated,
    perform_handshake,
)
from karmazyn_handshake import (
    _CryptoLayer,
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
        self.local_caps: Optional[dict] = None
        self.remote_caps: Optional[dict] = None
        self.kafs_enabled: bool = False

    def connect(self, client_version: str = PROTO_VERSION) -> "CynoberClient":
        if client_version not in SUPPORTED_VERSIONS:
            raise CynoberClientError(f"Nieobsługiwana wersja klienta: {client_version!r}")

        # Ponowne connect() bez close() wyciekało poprzednie gniazdo.
        if self.sock is not None:
            self.close()

        self.crypto = _CryptoLayer()
        self.crypto_mode = None
        self.hsl_link = None
        self.local_caps = None
        self.remote_caps = None
        self.kafs_enabled = False

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(HS_TIMEOUT_SEC)
        apply_tcp_keepalive(self.sock)
        try:
            self.sock.connect((self.host, self.port))
        except OSError as e:
            self.close()
            raise CynoberClientError(f"Nie można połączyć z {self.host}:{self.port}: {e}") from e

        hs_deadline = time.monotonic() + HS_TIMEOUT_SEC
        try:
            self.crypto_mode, self.local_caps, self.remote_caps, self.hsl_link = (
                perform_handshake(
                    self.sock,
                    self.crypto,
                    is_server=False,
                    deadline=hs_deadline,
                    client_version=client_version,
                )
            )
            self.kafs_enabled = kafs_negotiated(self.local_caps, self.remote_caps)
        except (ConnectionError, ConnectionResetError, OSError) as e:
            self.close()
            raise CynoberClientError(
                "Serwer odrzucił połączenie — sprawdź wersję protokołu i profil HSS."
            ) from e
        except RuntimeError as e:
            self.close()
            raise CynoberClientError(str(e)) from e

        self.sock.settimeout(self.timeout)
        apply_tcp_keepalive(self.sock)
        return self

    def ensure_connected(self) -> "CynoberClient":
        """Podtrzymaj / odtwórz tunel — bez zbędnego handshake gdy sock żyje."""
        if self.sock is not None:
            return self
        return self.connect()

    def _transport_dead(self, exc: BaseException) -> bool:
        msg = str(exc).lower()
        needles = (
            "zamkn",
            "closed",
            "reset",
            "broken",
            "timed out",
            "timeout",
            "eof",
            "nie połączono",
            "pusta odpowiedź",
            "tunel",
        )
        return any(n in msg for n in needles)

    def session_info(self) -> dict[str, Any]:
        """
        Metadane tunelu po connect() — do lore-editor / panelu / doctor.
        L0 dziś = TCP; QKD = hybrid seed gdy HSL.qkd_hybrid.
        """
        hsl = self.hsl_link
        kpc_residual = None
        kpc_gen = None
        if hsl is not None:
            kpc_residual = getattr(hsl, "kpc_last_soft_residual", None)
            kpc = getattr(hsl, "_kpc", None)
            if kpc is not None and getattr(kpc, "history", None) is not None:
                kpc_gen = kpc.history.gen
        return {
            "host": self.host,
            "port": self.port,
            "connected": self.sock is not None,
            "protocol": PROTO_VERSION,
            "l0_carrier": "tcp",
            "crypto_mode": self.crypto_mode,
            "kafs_enabled": bool(self.kafs_enabled),
            "hsl": bool(hsl),
            "qkd_hybrid": bool(hsl and getattr(hsl, "qkd_hybrid", False)),
            "hsl_epoch": getattr(hsl, "epoch", None) if hsl else None,
            "kpc_gen": kpc_gen,
            "kpc_soft_residual": kpc_residual,
            "local_features": sorted(features_of(self.local_caps)),
            "remote_features": sorted(features_of(self.remote_caps)),
            "node_id_local": (self.local_caps or {}).get("node_id"),
            "node_id_remote": (self.remote_caps or {}).get("node_id"),
        }

    def query(self, text: str, *, _retried: bool = False) -> dict:
        """
        Jedno zapytanie na **istniejącym** tunelu (stała sesja).
        Przy padnięciu TCP — jeden auto-reconnect + ponowienie (nie zamyka po sukcesie).
        """
        self.ensure_connected()
        try:
            enc_req = encode_rpc_request_frame(
                text, self.crypto, self.hsl_link, framed=self.kafs_enabled
            )
            _send_frame(self.sock, enc_req)

            enc_resp = _recv_frame(self.sock)
            if not enc_resp:
                raise CynoberClientError("Pusta odpowiedź — serwer zamknął tunel")
            kind, data = decode_rpc_response_frame(enc_resp, self.crypto, self.hsl_link)
            if kind != FRAME_RPC:
                raise CynoberClientError("Oczekiwano ramki RPC, otrzymano KAFS")
            if not isinstance(data, dict):
                raise CynoberClientError("Uszkodzona odpowiedź serwera (nie JSON)")
            return data
        except CynoberClientError as e:
            if not _retried and self._transport_dead(e):
                self.close()
                self.connect()
                return self.query(text, _retried=True)
            raise
        except (TimeoutError, socket.timeout, OSError) as e:
            err = CynoberClientError(f"Błąd tunelu RPC: {e}")
            if not _retried:
                self.close()
                try:
                    self.connect()
                    return self.query(text, _retried=True)
                except Exception:
                    raise err from e
            raise err from e
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError, IndexError) as e:
            raise CynoberClientError(f"Uszkodzona odpowiedź serwera: {e}") from e

    def _send_kafs(self, body: bytes) -> None:
        if not self.sock or not self.kafs_enabled:
            raise CynoberClientError("KAFS niedostępne na tej sesji")
        enc = encode_kafs_request_frame(body, self.crypto, self.hsl_link)
        _send_frame(self.sock, enc)

    def _recv_kafs_until_end(self, xfer_id: str) -> bytes:
        from cynober_media_rpc import KAFS_DATA, KAFS_END, KAFS_ERR, decode_kafs_body

        parts: list[bytes] = []
        while True:
            enc = _recv_frame(self.sock)
            if not enc:
                raise CynoberClientError("Tunel zamknięty w trakcie KAFS GET")
            kind, payload = decode_rpc_response_frame(enc, self.crypto, self.hsl_link)
            if kind == FRAME_RPC:
                # błąd serwera w środku streamu?
                raise CynoberClientError(f"RPC w trakcie KAFS: {payload}")
            msg = decode_kafs_body(payload)
            if msg.msg_type == KAFS_ERR:
                raise CynoberClientError(msg.error or "KAFS ERR")
            if msg.msg_type == KAFS_END:
                break
            if msg.msg_type == KAFS_DATA:
                parts.append(msg.data)
        return b"".join(parts)

    def put_media(
        self,
        atom_id: str,
        data: bytes,
        *,
        mime: str = "application/octet-stream",
        bubble: str = "",
        binding: str = "",
        chunk_size: int = KAFS_CHUNK_MAX,
    ) -> dict:
        """
        Wyślij medium przez MEDIA PUT + KAFS chunki.
        Wymaga kafs_enabled (negocjacja features).
        """
        from cynober_media_rpc import encode_kafs_data, iter_chunks, kafs_wire_id

        if not isinstance(data, (bytes, bytearray)):
            raise CynoberClientError("put_media: data musi być bytes")
        data = bytes(data)
        if not self.sock:
            raise CynoberClientError("put_media: nie połączono — wywołaj connect()")
        if not self.kafs_enabled:
            loc = features_of(self.local_caps)
            rem = features_of(self.remote_caps)
            raise CynoberClientError(
                "put_media wymaga negocjacji kafs-stream (Cynober-Secure features). "
                f"local={sorted(loc) or '∅'} remote={sorted(rem) or '∅'}. "
                "Zaktualizuj cynober-db po obu stronach (serwer + klient ≥8.0)."
            )
        if not atom_id or not str(atom_id).strip():
            raise CynoberClientError("put_media: atom_id nie może być puste")
        atom_id = str(atom_id).strip()
        # KAFS wire field is 16 bytes — use stable digest for long ids (server indexes both)
        wire_id = kafs_wire_id(atom_id)
        q = (
            f'MEDIA PUT START "{atom_id}" MIME "{mime}" SIZE {len(data)}'
        )
        if bubble and binding:
            q += f' BĄBEL "{bubble}" JAKO "{binding}"'
        start = self.query_line(q)
        if start.get("status") != "ok":
            raise CynoberClientError(start.get("message") or "MEDIA PUT START failed")
        seq = 0
        for chunk in iter_chunks(data, chunk_size=chunk_size or KAFS_CHUNK_MAX):
            self._send_kafs(encode_kafs_data(wire_id, seq, len(data), chunk))
            seq += 1
        end = self.query_line(f'MEDIA PUT END "{atom_id}"')
        if end.get("status") != "ok":
            raise CynoberClientError(end.get("message") or "MEDIA PUT END failed")
        return end

    def get_media(
        self,
        atom_id: str,
        *,
        offset: int = 0,
        limit: int = 0,
    ) -> tuple[bytes, str, dict]:
        """Pobierz medium. Zwraca (bytes, mime, meta_row)."""
        import base64

        q = f'MEDIA GET "{atom_id}"'
        if offset:
            q += f" OFFSET {int(offset)}"
        if limit:
            q += f" LIMIT {int(limit)}"
        row = self.query_line(q)
        if row.get("status") != "ok":
            raise CynoberClientError(row.get("message") or "MEDIA GET failed")
        mime = str(row.get("mime") or "application/octet-stream")
        if row.get("stream") and self.kafs_enabled:
            data = self._recv_kafs_until_end(str(row.get("id") or atom_id))
            return data, mime, row
        b64 = row.get("data_b64")
        if isinstance(b64, str) and b64:
            return base64.b64decode(b64.encode("ascii")), mime, row
        raise CynoberClientError("MEDIA GET: brak streamu KAFS i data_b64")

    def media_stat(self, atom_id: str) -> dict:
        return self.query_line(f'MEDIA STAT "{atom_id}"')

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