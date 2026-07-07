"""
cynober_client_config.py — profile klienta i konfiguracja serwera Cynober
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

CONFIG_VERSION = 2
CONFIG_PATH = Path(os.path.expanduser("~")) / ".karmazyn_client.json"
DEFAULT_CLIENT_HOST = "127.0.0.1"
DEFAULT_SERVER_BIND = "0.0.0.0"
DEFAULT_PORT = 8080

# Zachowanie kompatybilności w imporcie
DEFAULT_HOST = DEFAULT_CLIENT_HOST


def default_server_config() -> dict[str, Any]:
    from cynober_rate_limit import default_rate_limit_config

    return {
        "bind_host": DEFAULT_SERVER_BIND,
        "port": DEFAULT_PORT,
        "note": "Nasłuch na wszystkich interfejsach (LAN / Termux)",
        "rate_limit": default_rate_limit_config(),
    }


def default_config() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "active": "lokalny",
        "profiles": {
            "lokalny": {
                "host": DEFAULT_CLIENT_HOST,
                "port": DEFAULT_PORT,
                "note": "Serwer na tej samej maszynie",
            }
        },
        "server": default_server_config(),
    }


def _migrate_config(data: dict[str, Any]) -> dict[str, Any]:
    if "profiles" not in data or not isinstance(data.get("profiles"), dict):
        return default_config()
    if "server" not in data or not isinstance(data.get("server"), dict):
        data["server"] = default_server_config()
    else:
        srv = data["server"]
        if "rate_limit" not in srv or not isinstance(srv.get("rate_limit"), dict):
            srv["rate_limit"] = default_server_config()["rate_limit"]
    data["version"] = CONFIG_VERSION
    return data


def load_config() -> dict[str, Any]:
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return default_config()
        return _migrate_config(data)
    except (OSError, json.JSONDecodeError):
        return default_config()


def save_config(data: dict[str, Any]) -> None:
    data["version"] = CONFIG_VERSION
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_profiles(data: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    cfg = data or load_config()
    profiles = cfg.get("profiles", {})
    return profiles if isinstance(profiles, dict) else {}


def get_active_profile(data: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    cfg = data or load_config()
    name = str(cfg.get("active", "lokalny"))
    profiles = list_profiles(cfg)
    if name not in profiles:
        name = next(iter(profiles), "lokalny")
        if name not in profiles:
            profiles = default_config()["profiles"]
            name = "lokalny"
    return name, dict(profiles[name])


def set_active_profile(name: str) -> None:
    cfg = load_config()
    if name not in cfg.get("profiles", {}):
        raise KeyError(f"Nieznany profil: {name}")
    cfg["active"] = name
    save_config(cfg)


def upsert_profile(
    name: str,
    host: str,
    port: int,
    *,
    note: str = "",
    psk: str = "",
    qkd_seed: str = "",
) -> None:
    cfg = load_config()
    profiles = cfg.setdefault("profiles", {})
    entry: dict[str, Any] = {
        "host": host.strip(),
        "port": int(port),
        "note": note.strip(),
    }
    if psk:
        entry["psk"] = psk
    if qkd_seed:
        entry["qkd_seed"] = qkd_seed
    profiles[name] = entry
    cfg["active"] = name
    save_config(cfg)


def get_server_config(data: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = data or load_config()
    srv = cfg.get("server", {})
    return dict(srv) if isinstance(srv, dict) else default_server_config()


def upsert_server_config(
    bind_host: str,
    port: int,
    *,
    note: str = "",
    psk: str = "",
    qkd_seed: str = "",
    rate_limit: dict[str, int] | None = None,
) -> None:
    cfg = load_config()
    prev = get_server_config(cfg)
    entry: dict[str, Any] = {
        "bind_host": bind_host.strip(),
        "port": int(port),
        "note": note.strip(),
        "rate_limit": rate_limit if rate_limit is not None else prev.get(
            "rate_limit", default_server_config()["rate_limit"]
        ),
    }
    if psk:
        entry["psk"] = psk
    elif prev.get("psk"):
        entry["psk"] = prev["psk"]
    if qkd_seed:
        entry["qkd_seed"] = qkd_seed
    elif prev.get("qkd_seed"):
        entry["qkd_seed"] = prev["qkd_seed"]
    cfg["server"] = entry
    save_config(cfg)


def apply_secrets(source: dict[str, Any]) -> None:
    """Ustaw KARM_PSK / KARM_QKD_SEED (nie nadpisuj istniejących env)."""
    if source.get("psk") and not os.environ.get("KARM_PSK"):
        os.environ["KARM_PSK"] = str(source["psk"])
    if source.get("qkd_seed") and not os.environ.get("KARM_QKD_SEED"):
        os.environ["KARM_QKD_SEED"] = str(source["qkd_seed"])


def apply_profile_secrets(profile: dict[str, Any]) -> None:
    apply_secrets(profile)


def apply_server_secrets() -> None:
    apply_secrets(get_server_config())


def list_local_ips() -> list[str]:
    """Adresy IPv4 tej maszyny — podpowiedź do profilu klienta (Termux)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip in found:
                continue
            found.append(ip)
    except OSError:
        pass
    return found


def resolve_server_bind(argv: list[str] | None = None) -> tuple[str, int, str]:
    """
    bind host:port dla cynober_server.py.
    Priorytet: argv | CYNOBER_SERVER_HOST/PORT | sekcja server w JSON | 0.0.0.0:8080.
    """
    args = list(argv if argv is not None else sys.argv[1:])

    if args and args[0] in ("-h", "--help"):
        print(
            "Użycie: python cynober_server.py [bind_host] [port]\n"
            "       python cynober_konfigurator.py\n"
            f"Konfiguracja: {CONFIG_PATH} (sekcja server)"
        )
        sys.exit(0)

    if len(args) >= 1 and not args[0].startswith("-"):
        host = args[0]
        port = int(args[1]) if len(args) >= 2 else DEFAULT_PORT
        return host, port, "(argv)"

    env_host = os.environ.get("CYNOBER_SERVER_BIND", "").strip()
    if not env_host:
        env_host = os.environ.get("CYNOBER_SERVER_HOST", "").strip()
    env_port = os.environ.get("CYNOBER_SERVER_PORT", "").strip()
    if env_host:
        return env_host, int(env_port or DEFAULT_PORT), "(env)"

    srv = get_server_config()
    apply_server_secrets()
    return (
        str(srv.get("bind_host", DEFAULT_SERVER_BIND)),
        int(srv.get("port", DEFAULT_PORT)),
        "(config)",
    )


def resolve_client_target(argv: list[str] | None = None) -> tuple[str, int, str]:
    """
    Wybierz host:port i nazwę profilu.
    Priorytet: argv [host] [port] | --profile NAME | CYNOBER_HOST/PORT | plik JSON.
    """
    args = list(argv if argv is not None else sys.argv[1:])

    if args and args[0] in ("-h", "--help"):
        print(
            "Użycie: python Cynober_db.py [host] [port]\n"
            "       python Cynober_db.py --profile NAZWA\n"
            "       python cynober_konfigurator.py\n"
            f"Konfiguracja: {CONFIG_PATH}"
        )
        sys.exit(0)

    if len(args) >= 1 and args[0] == "--profile":
        name = args[1] if len(args) > 1 else load_config().get("active", "lokalny")
        cfg = load_config()
        if name not in cfg.get("profiles", {}):
            raise SystemExit(f"[!] Nieznany profil: {name}")
        cfg["active"] = name
        save_config(cfg)
        _, prof = get_active_profile(cfg)
        apply_profile_secrets(prof)
        return str(prof["host"]), int(prof["port"]), str(name)

    if len(args) >= 1 and not args[0].startswith("-"):
        host = args[0]
        port = int(args[1]) if len(args) >= 2 else DEFAULT_PORT
        return host, port, "(argv)"

    env_host = os.environ.get("CYNOBER_HOST", "").strip()
    env_port = os.environ.get("CYNOBER_PORT", "").strip()
    if env_host:
        return env_host, int(env_port or DEFAULT_PORT), "(env)"

    name, prof = get_active_profile()
    apply_profile_secrets(prof)
    return str(prof.get("host", DEFAULT_HOST)), int(prof.get("port", DEFAULT_PORT)), name


def test_connection(host: str, port: int, timeout: float = 5.0) -> tuple[bool, str]:
    """TCP + handshake Cynober-Secure (bez zapytania RPC)."""
    from cynober_rpc import HS_TIMEOUT_SEC, perform_handshake, clear_replay_cache
    from karmazyn_handshake import _CryptoLayer

    clear_replay_cache()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        crypto = _CryptoLayer()
        deadline = time.monotonic() + HS_TIMEOUT_SEC
        mode, _, _, hsl = perform_handshake(
            sock, crypto, is_server=False, deadline=deadline
        )
        note = f"{mode.upper()}"
        if hsl:
            note += " + HSL"
            if hsl.qkd_hybrid:
                note += " + QKD"
        return True, f"OK — tunel {note}"
    except Exception as e:
        return False, str(e)
    finally:
        try:
            sock.close()
        except OSError:
            pass