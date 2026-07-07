"""Wspólna warstwa protokołu Cynober-Secure (serwer + klient + testy)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import time
import zlib
from typing import Any

from karmazyn_handshake import (
    _CryptoLayer,
    _compress,
    _decompress,
    _recv_json,
    _send_frame,
    _send_json,
)
from karmazyn_hsl import HSL_VERSION, HSLLink, perform_hsl_link

try:
    from karmazyn_handshake import _HSS_AVAILABLE, _CRYPTO_OK
except ImportError:
    _HSS_AVAILABLE = False
    _CRYPTO_OK = False

PROTO_VERSION = "Cynober-Secure-1.2"
LEGACY_VERSION = "Cynober-Secure-1.0"
LEGACY_VERSION_11 = "Cynober-Secure-1.1"
SUPPORTED_VERSIONS = frozenset({PROTO_VERSION, LEGACY_VERSION_11, LEGACY_VERSION})

RPC_TIMEOUT_SEC = 300.0
HS_TIMEOUT_SEC = 10.0
REPLAY_WINDOW_SEC = 300.0
CRYPTO_PRIORITY = ("hss", "ecdh", "simple")

# Anty-replay: session_id widziane w oknie czasowym (RAM, nie persystentne).
_SEEN_SESSIONS: set[str] = set()
_MAX_SEEN_SESSIONS = 10_000


def is_compatible_version(version: str | None) -> bool:
    return version in SUPPORTED_VERSIONS


def build_local_caps() -> dict[str, Any]:
    crypto: list[str] = ["simple"]
    if _CRYPTO_OK:
        crypto.append("ecdh")
    if _HSS_AVAILABLE:
        crypto.append("hss")

    force = os.environ.get("CYNOBER_FORCE_CRYPTO", "").strip().lower()
    if force in CRYPTO_PRIORITY:
        crypto = [force]

    return {
        "version": PROTO_VERSION,
        "crypto": crypto,
        "hsl": HSL_VERSION,
        "node_id": _node_id(),
        "ts": time.time(),
        "session_id": secrets.token_hex(8),
    }


def clear_replay_cache() -> None:
    """Do testów — wyczyść pamięć session_id."""
    _SEEN_SESSIONS.clear()


def _get_psk() -> bytes:
    return os.environ.get("KARM_PSK", "").encode("utf-8")


def apply_psk(crypto: _CryptoLayer) -> bool:
    """Wymieszaj PSK sieci w klucz sesji (jak KSH-1.2). Zwraca True jeśli PSK aktywny."""
    psk = _get_psk()
    if not psk or not crypto._key:
        return False
    crypto._key = hashlib.sha256(crypto._key + psk).digest()
    return True


def validate_remote_caps(local: dict, remote: dict) -> None:
    """Anty-replay: okno czasowe + jednorazowy session_id (KSH-1.2)."""
    remote_ts = float(remote.get("ts", 0))
    local_ts = float(local.get("ts", 0))
    if remote_ts <= 0:
        raise RuntimeError("Anty-replay: brak znacznika czasu w caps zdalnych")
    if abs(local_ts - remote_ts) > REPLAY_WINDOW_SEC:
        raise RuntimeError(
            f"Anty-replay: różnica zegarów {abs(local_ts - remote_ts):.0f}s "
            f"> {REPLAY_WINDOW_SEC:.0f}s"
        )

    session_id = remote.get("session_id", "")
    if not session_id:
        raise RuntimeError("Anty-replay: brak session_id w caps zdalnych")
    if session_id in _SEEN_SESSIONS:
        raise RuntimeError(f"Anty-replay: session_id '{session_id}' już użyty")

    _SEEN_SESSIONS.add(session_id)
    if len(_SEEN_SESSIONS) > _MAX_SEEN_SESSIONS:
        _SEEN_SESSIONS.clear()


def select_crypto_mode(local: dict, remote: dict) -> str:
    """Wybierz tryb: legacy 1.0 → simple; 1.1 → najsilniejszy wspólny."""
    if local.get("version") == LEGACY_VERSION or remote.get("version") == LEGACY_VERSION:
        common = set(local.get("crypto", [])) & set(remote.get("crypto", []))
        if "simple" in common:
            return "simple"
        raise RuntimeError("Klient 1.0 wymaga wspólnego trybu simple")

    common = set(local.get("crypto", [])) & set(remote.get("crypto", []))
    min_crypto = os.environ.get("CYNOBER_MIN_CRYPTO", "").strip().lower()
    order = CRYPTO_PRIORITY
    if min_crypto in CRYPTO_PRIORITY:
        idx = order.index(min_crypto)
        order = order[idx:]

    for mode in order:
        if mode in common:
            return mode
    raise RuntimeError(
        f"Brak wspólnego trybu krypto: lokalny={local.get('crypto')} "
        f"zdalny={remote.get('crypto')}"
    )


def negotiate_crypto(
    sock: socket.socket,
    crypto: _CryptoLayer,
    mode: str,
    is_server: bool,
    deadline: float,
) -> str:
    if mode == "hss":
        if is_server:
            init_msg = _recv_json(sock, deadline)
            crypto.negotiate_hss_responder(sock, init_msg)
        else:
            crypto.negotiate_hss_initiator(sock, deadline)
    elif mode == "ecdh":
        if is_server:
            init_msg = _recv_json(sock, deadline)
            crypto.negotiate_ecdh_responder(sock, init_msg)
        else:
            crypto.negotiate_ecdh_initiator(sock, deadline)
    else:
        if is_server:
            init_msg = _recv_json(sock, deadline)
            crypto.negotiate_simple_responder(sock, init_msg)
        else:
            crypto.negotiate_simple_initiator(sock, deadline)
    return mode


def hsl_enabled(local_caps: dict, remote_caps: dict) -> bool:
    """HSL wymaga 1.2 po obu stronach i flagi hsl."""
    if local_caps.get("version") != PROTO_VERSION:
        return False
    if remote_caps.get("version") != PROTO_VERSION:
        return False
    return bool(local_caps.get("hsl")) and bool(remote_caps.get("hsl"))


def perform_handshake(
    sock: socket.socket,
    crypto: _CryptoLayer,
    is_server: bool,
    deadline: float,
    *,
    client_version: str | None = None,
) -> tuple[str, dict, dict, HSLLink | None]:
    """
    Fazy 0+1 (+2 HSL gdy 1.2). Zwraca (crypto_mode, local_caps, remote_caps, hsl_link).
    client_version: wymuszenie wersji caps po stronie klienta (testy legacy).
    """
    local_caps = build_local_caps()
    if not is_server and client_version:
        local_caps = {**local_caps, "version": client_version}
        if client_version == LEGACY_VERSION:
            local_caps["crypto"] = ["simple"]

    if is_server:
        _send_json(sock, local_caps)
        remote_caps = _recv_json(sock, deadline)
    else:
        remote_caps = _recv_json(sock, deadline)
        _send_json(sock, local_caps)

    remote_ver = remote_caps.get("version")
    if not is_compatible_version(remote_ver):
        raise RuntimeError(
            f"Niezgodna wersja protokołu: oczekiwano jednej z {SUPPORTED_VERSIONS}, "
            f"otrzymano {remote_ver!r}"
        )

    # Legacy 1.0 nie wysyłała ts/session_id — pomijamy anty-replay.
    if remote_ver != LEGACY_VERSION and local_caps.get("version") != LEGACY_VERSION:
        validate_remote_caps(local_caps, remote_caps)

    mode = select_crypto_mode(local_caps, remote_caps)
    negotiate_crypto(sock, crypto, mode, is_server, deadline)
    apply_psk(crypto)

    hsl_link: HSLLink | None = None
    if hsl_enabled(local_caps, remote_caps):
        assert crypto._key
        hsl_link = perform_hsl_link(
            sock,
            crypto._key,
            local_caps,
            remote_caps,
            is_server,
            deadline,
        )

    return mode, local_caps, remote_caps, hsl_link


def _node_id() -> str:
    path = os.path.join(os.path.expanduser("~"), ".karmazyn_node_id")
    try:
        with open(path, encoding="utf-8") as f:
            saved = f.read().strip()
        if saved.startswith("node_") and len(saved) == 17:
            return saved
    except OSError:
        pass
    nid = "node_" + secrets.token_hex(6)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(nid)
    except OSError:
        pass
    return nid


def error_result(message: str, action: str = "PROTOCOL") -> list[dict[str, Any]]:
    return [{"status": "error", "action": action, "message": message}]


def encode_response(results: list[dict[str, Any]]) -> bytes:
    return json.dumps({"results": results}, ensure_ascii=False).encode("utf-8")


def encrypt_rpc_request(crypto: _CryptoLayer, blob: bytes, hsl: HSLLink | None) -> bytes:
    if hsl:
        return crypto.encrypt(
            blob,
            key=hsl.request_key(),
            aad=hsl.aad_request(),
        )
    return crypto.encrypt(blob)


def decrypt_rpc_request(crypto: _CryptoLayer, enc: bytes, hsl: HSLLink | None) -> bytes:
    if hsl:
        return crypto.decrypt(
            enc,
            key=hsl.request_key(),
            aad=hsl.aad_request(),
        )
    return crypto.decrypt(enc)


def encrypt_rpc_response(crypto: _CryptoLayer, blob: bytes, hsl: HSLLink | None) -> bytes:
    if hsl:
        return crypto.encrypt(
            blob,
            key=hsl.response_key(),
            aad=hsl.aad_response(),
        )
    return crypto.encrypt(blob)


def decrypt_rpc_response(crypto: _CryptoLayer, enc: bytes, hsl: HSLLink | None) -> bytes:
    if hsl:
        return crypto.decrypt(
            enc,
            key=hsl.response_key(),
            aad=hsl.aad_response(),
        )
    return crypto.decrypt(enc)


def send_encrypted_response(
    conn,
    crypto,
    results: list[dict[str, Any]],
    hsl: HSLLink | None = None,
) -> None:
    enc = encrypt_rpc_response(crypto, _compress(encode_response(results)), hsl)
    _send_frame(conn, enc)


def decode_request(enc_req: bytes, crypto, hsl: HSLLink | None = None) -> str:
    try:
        raw = _decompress(decrypt_rpc_request(crypto, enc_req, hsl))
    except (zlib.error, ValueError, IndexError) as e:
        raise ValueError(f"Nie można odszyfrować lub zdekompresować ramki: {e}") from e
    try:
        req = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Niepoprawny JSON w żądaniu: {e}") from e
    if not isinstance(req, dict):
        raise ValueError("Żądanie RPC musi być obiektem JSON")
    if "query" not in req:
        raise ValueError("Brak pola 'query' w żądaniu RPC")
    query = req["query"]
    if not isinstance(query, str):
        raise ValueError("Pole 'query' musi być tekstem")
    return query


def parse_response_payload(res_data: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    if "error" in res_data:
        err = res_data["error"]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or str(err)
        else:
            msg = str(err)
        return [], msg
    return res_data.get("results", []), None


def send_handshake_error(conn: socket.socket, code: str, message: str, **extra) -> None:
    try:
        _send_json(conn, {"error": {"code": code, "message": message, **extra}})
    except (ConnectionError, OSError):
        pass