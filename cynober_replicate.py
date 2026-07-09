#!/usr/bin/env python3
"""
cynober_replicate.py — replikacja trwałych światów między węzłami (v7.4)
"""

from __future__ import annotations

import base64
import json
import re
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from cynober_ops import SERVER_VERSION
from cynober_worlds import (
    WorldRegistry,
    _kafd_path,
    _load_meta,
    _meta_path,
    _save_meta,
    load_runtime_from_kafd,
    validate_world_name,
)

_PEER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

_ADD_PEER_RE = re.compile(
    r'^DODAJ\s+WĘZEŁ\s+"([^"]+)"\s+HOST\s+"([^"]+)"\s+PORT\s+(\d+)'
    r'(?:\s+UŻYTKOWNIK\s+"([^"]+)"\s+TOKEN\s+"([^"]+)")?$',
    re.IGNORECASE,
)
_REMOVE_PEER_RE = re.compile(r'^USUŃ\s+WĘZEŁ\s+"([^"]+)"$', re.IGNORECASE)
_PULL_RE = re.compile(r'^PULL\s+ŚWIAT\s+"([^"]+)"\s+Z\s+"([^"]+)"$', re.IGNORECASE)
_PUSH_RE = re.compile(r'^PUSH\s+ŚWIAT\s+"([^"]+)"\s+DO\s+"([^"]+)"$', re.IGNORECASE)
_SYNC_RE = re.compile(r'^SYNC\s+ŚWIAT\s+"([^"]+)"\s+Z\s+"([^"]+)"$', re.IGNORECASE)
_EXPORT_WORLD_RE = re.compile(r'^EKSPORT\s+ŚWIATA\s+"([^"]+)"$', re.IGNORECASE)
_IMPORT_WORLD_RE = re.compile(
    r'^IMPORT\s+ŚWIATA\s+"([^"]+)"\s+DANE\s+"([A-Za-z0-9+/=]+)"$',
    re.IGNORECASE,
)


def validate_peer_name(name: str) -> str:
    name = name.strip()
    if not _PEER_NAME_RE.match(name):
        raise ValueError(
            "Nieprawidłowa nazwa węzła (dozwolone: litery, cyfry, _, -, max 64 znaki)"
        )
    return name


class PeerRegistry:
    """Rejestr węzłów replikacji — peers.json w katalogu światów."""

    def __init__(self, base_dir: Path):
        self._base = Path(base_dir)
        self._path = self._base / "peers.json"
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"peers": {}}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            if not self._path.is_file():
                self._data = {"peers": {}}
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) else {"peers": {}}
                if "peers" not in self._data or not isinstance(self._data["peers"], dict):
                    self._data["peers"] = {}
            except (OSError, json.JSONDecodeError):
                self._data = {"peers": {}}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def list_peers(self) -> List[dict]:
        with self._lock:
            out: List[dict] = []
            for name, info in sorted(self._data.get("peers", {}).items()):
                if not isinstance(info, dict):
                    continue
                out.append({
                    "name": name,
                    "host": info.get("host", ""),
                    "port": info.get("port", 0),
                    "user": info.get("user"),
                    "has_token": bool(info.get("token")),
                })
            return out

    def get(self, name: str) -> dict:
        name = validate_peer_name(name)
        with self._lock:
            info = self._data.get("peers", {}).get(name)
            if not info or not isinstance(info, dict):
                raise ValueError(f"Węzeł '{name}' nie jest zarejestrowany.")
            host = str(info.get("host", "")).strip()
            port = int(info.get("port", 0))
            if not host or port <= 0:
                raise ValueError(f"Węzeł '{name}' ma niepełną konfigurację (host/port).")
            return {
                "name": name,
                "host": host,
                "port": port,
                "user": info.get("user"),
                "token": info.get("token"),
            }

    def add(
        self,
        name: str,
        host: str,
        port: int,
        *,
        user: Optional[str] = None,
        token: Optional[str] = None,
    ) -> dict:
        name = validate_peer_name(name)
        host = host.strip()
        if not host:
            raise ValueError("Brak hosta węzła.")
        if port <= 0 or port > 65535:
            raise ValueError("Nieprawidłowy port węzła.")
        entry: Dict[str, Any] = {"host": host, "port": port}
        if user:
            entry["user"] = user
        if token:
            entry["token"] = token
        with self._lock:
            self._data.setdefault("peers", {})[name] = entry
            self._save()
        return {"name": name, "host": host, "port": port}

    def remove(self, name: str) -> dict:
        name = validate_peer_name(name)
        with self._lock:
            if name not in self._data.get("peers", {}):
                raise ValueError(f"Węzeł '{name}' nie istnieje.")
            self._data["peers"].pop(name, None)
            self._save()
        return {"name": name, "removed": True}


_peer_stores: Dict[str, PeerRegistry] = {}
_peer_lock = threading.Lock()


def get_peer_registry(base_dir: Path) -> PeerRegistry:
    key = str(Path(base_dir).resolve())
    with _peer_lock:
        store = _peer_stores.get(key)
        if store is None:
            store = PeerRegistry(base_dir)
            _peer_stores[key] = store
        return store


def reset_peer_registry_for_tests(base_dir: Path) -> PeerRegistry:
    key = str(Path(base_dir).resolve())
    with _peer_lock:
        store = PeerRegistry(base_dir)
        _peer_stores[key] = store
        return store


def export_world_payload(registry: WorldRegistry, world_name: str) -> dict:
    name = validate_world_name(world_name)
    with registry._lock:
        world = registry._worlds.get(name)
        if world is not None and world.dirty:
            registry._persist(world)
            world.dirty = False
    kafd = _kafd_path(registry.base_dir, name)
    if not kafd.is_file():
        raise ValueError(f"Świat '{name}' nie ma zapisu na dysku.")
    meta = _load_meta(_meta_path(registry.base_dir, name))
    return {
        "world": name,
        "kafd_b64": base64.b64encode(kafd.read_bytes()).decode("ascii"),
        "meta": meta,
        "modified_at": meta.get("modified_at"),
        "bubbles": meta.get("bubbles", 0),
    }


def import_world_payload(
    registry: WorldRegistry,
    world_name: str,
    kafd_b64: str,
    meta: Optional[dict] = None,
) -> dict:
    name = validate_world_name(world_name)
    try:
        kafd_bytes = base64.b64decode(kafd_b64.encode("ascii"), validate=True)
    except Exception as e:
        raise ValueError(f"Nieprawidłowe dane świata (base64): {e}") from e
    if not kafd_bytes:
        raise ValueError("Puste dane świata.")

    with registry._lock:
        world = registry._worlds.get(name)
        if world is not None and world.dirty:
            registry._persist(world)
            world.dirty = False

    dest = _kafd_path(registry.base_dir, name)
    dest.write_bytes(kafd_bytes)
    if meta:
        merged = dict(meta)
        merged["name"] = name
        _save_meta(_meta_path(registry.base_dir, name), merged)
    elif dest.is_file():
        info = registry._world_info(name)
        _save_meta(_meta_path(registry.base_dir, name), {
            "name": name,
            "modified_at": time.time(),
            "bubbles": info.get("bubbles", 0),
        })

    with registry._lock:
        world = registry._worlds.get(name)
        if world is not None and world.refs > 0:
            new_rt = registry._create_runtime()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".kafd") as tmp:
                tmp.write(kafd_bytes)
                tmp_path = tmp.name
            try:
                load_runtime_from_kafd(new_rt.bridge, tmp_path, lazy=False)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            with world.runtime.lock:
                world.runtime = new_rt
            world.dirty = False
            if meta:
                world.modified_at = float(meta.get("modified_at", time.time()))
        else:
            registry._worlds.pop(name, None)

    return {"world": name, "imported": True, "bytes": len(kafd_bytes)}


class _PeerRpc:
    """Minimalny klient RPC do komunikacji między węzłami."""

    def __init__(self, host: str, port: int, timeout: float = 60.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._crypto = None
        self._hsl = None

    def connect(self) -> None:
        from cynober_rpc import HS_TIMEOUT_SEC, PROTO_VERSION, perform_handshake
        from karmazyn_handshake import _CryptoLayer

        self._crypto = _CryptoLayer()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(HS_TIMEOUT_SEC)
        self._sock.connect((self.host, self.port))
        deadline = time.monotonic() + HS_TIMEOUT_SEC
        _, _, _, self._hsl = perform_handshake(
            self._sock, self._crypto, is_server=False, deadline=deadline,
            client_version=PROTO_VERSION,
        )
        self._sock.settimeout(self.timeout)

    def query(self, text: str) -> dict:
        import json
        from cynober_rpc import build_rpc_request, decrypt_rpc_response, encrypt_rpc_request
        from karmazyn_handshake import _compress, _decompress, _recv_frame, _send_frame

        if not self._sock or not self._crypto:
            raise RuntimeError("Brak połączenia z węzłem.")
        req = json.dumps(
            build_rpc_request(text, self._hsl), ensure_ascii=False
        ).encode("utf-8")
        enc = encrypt_rpc_request(self._crypto, _compress(req), self._hsl)
        _send_frame(self._sock, enc)
        enc_resp = _recv_frame(self._sock)
        if not enc_resp:
            raise ConnectionError("Węzeł zamknął połączenie.")
        raw = _decompress(decrypt_rpc_response(self._crypto, enc_resp, self._hsl))
        return json.loads(raw.decode("utf-8"))

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _peer_client(peer: dict) -> _PeerRpc:
    return _PeerRpc(peer["host"], int(peer["port"]))


def _login_peer(client: _PeerRpc, peer: dict) -> None:
    user = peer.get("user")
    token = peer.get("token")
    if not user or not token:
        return
    resp = client.query(f'ZALOGUJ "{user}" TOKEN "{token}"')
    row = resp.get("results", [{}])[0]
    if row.get("status") != "ok":
        raise RuntimeError(row.get("message", "Logowanie na węźle nie powiodło się."))


def _first_result(resp: dict) -> dict:
    results = resp.get("results") or []
    if not results:
        raise RuntimeError("Pusta odpowiedź węzła.")
    row = results[0]
    if row.get("status") == "error":
        raise RuntimeError(row.get("message", "Błąd węzła."))
    return row


def _remote_world_modified(client: _PeerRpc, world: str) -> Optional[float]:
    row = _first_result(client.query("LISTA ŚWIATÓW"))
    for info in row.get("worlds", []):
        if info.get("name") == world:
            mod = info.get("modified_at")
            return float(mod) if mod is not None else None
    return None


def _remote_export(client: _PeerRpc, world: str) -> dict:
    row = _first_result(client.query(f'EKSPORT ŚWIATA "{world}"'))
    if "kafd_b64" not in row:
        raise RuntimeError("Węzeł nie zwrócił danych świata.")
    return row


def _remote_import(client: _PeerRpc, world: str, payload: dict) -> dict:
    b64 = payload["kafd_b64"]
    row = _first_result(client.query(f'IMPORT ŚWIATA "{world}" DANE "{b64}"'))
    return row


def pull_world(registry: WorldRegistry, peers: PeerRegistry, world: str, peer_name: str) -> dict:
    world = validate_world_name(world)
    peer = peers.get(peer_name)
    client = _peer_client(peer)
    try:
        client.connect()
        _login_peer(client, peer)
        payload = _remote_export(client, world)
        info = import_world_payload(registry, world, payload["kafd_b64"], payload.get("meta"))
        return {
            "action": "PULL_WORLD",
            "world": world,
            "peer": peer_name,
            "direction": "pull",
            **info,
        }
    finally:
        client.close()


def push_world(registry: WorldRegistry, peers: PeerRegistry, world: str, peer_name: str) -> dict:
    world = validate_world_name(world)
    peer = peers.get(peer_name)
    payload = export_world_payload(registry, world)
    client = _peer_client(peer)
    try:
        client.connect()
        _login_peer(client, peer)
        remote = _remote_import(client, world, payload)
        return {
            "action": "PUSH_WORLD",
            "world": world,
            "peer": peer_name,
            "direction": "push",
            "remote": remote,
            "bytes": len(base64.b64decode(payload["kafd_b64"])),
        }
    finally:
        client.close()


def sync_world(registry: WorldRegistry, peers: PeerRegistry, world: str, peer_name: str) -> dict:
    world = validate_world_name(world)
    peer = peers.get(peer_name)
    local = registry._world_info(world)
    local_mod = float(local.get("modified_at") or 0)
    local_exists = bool(local.get("exists_on_disk"))

    client = _peer_client(peer)
    try:
        client.connect()
        _login_peer(client, peer)
        remote_mod = _remote_world_modified(client, world)
        remote_exists = remote_mod is not None

        if remote_exists and local_exists:
            if remote_mod > local_mod:
                payload = _remote_export(client, world)
                info = import_world_payload(registry, world, payload["kafd_b64"], payload.get("meta"))
                return {"action": "SYNC_WORLD", "world": world, "peer": peer_name, "direction": "pull", **info}
            if local_mod > remote_mod:
                payload = export_world_payload(registry, world)
                remote = _remote_import(client, world, payload)
                return {"action": "SYNC_WORLD", "world": world, "peer": peer_name, "direction": "push", "remote": remote}
            return {
                "action": "SYNC_WORLD",
                "world": world,
                "peer": peer_name,
                "direction": "none",
                "synced": True,
                "message": "Światy są zsynchronizowane.",
            }
        if remote_exists and not local_exists:
            payload = _remote_export(client, world)
            info = import_world_payload(registry, world, payload["kafd_b64"], payload.get("meta"))
            return {"action": "SYNC_WORLD", "world": world, "peer": peer_name, "direction": "pull", **info}
        if local_exists and not remote_exists:
            payload = export_world_payload(registry, world)
            remote = _remote_import(client, world, payload)
            return {"action": "SYNC_WORLD", "world": world, "peer": peer_name, "direction": "push", "remote": remote}
        raise ValueError(f"Świat '{world}' nie istnieje lokalnie ani na węźle '{peer_name}'.")
    finally:
        client.close()


def try_replicate_command(
    stripped: str,
    upper: str,
    registry: WorldRegistry,
    peers: PeerRegistry,
) -> Optional[list]:
    if upper == "LISTA WĘZŁÓW":
        return [{
            "status": "ok",
            "action": "LIST_PEERS",
            "peers": peers.list_peers(),
            "server_version": SERVER_VERSION,
        }]

    m = _ADD_PEER_RE.match(stripped)
    if m:
        try:
            info = peers.add(
                m.group(1), m.group(2), int(m.group(3)),
                user=m.group(4), token=m.group(5),
            )
            return [{"status": "ok", "action": "ADD_PEER", **info}]
        except ValueError as e:
            return [{"status": "error", "message": str(e)}]

    m = _REMOVE_PEER_RE.match(stripped)
    if m:
        try:
            info = peers.remove(m.group(1))
            return [{"status": "ok", "action": "REMOVE_PEER", **info}]
        except ValueError as e:
            return [{"status": "error", "message": str(e)}]

    m = _EXPORT_WORLD_RE.match(stripped)
    if m:
        try:
            payload = export_world_payload(registry, m.group(1))
            return [{"status": "ok", "action": "EXPORT_WORLD", **payload}]
        except (ValueError, OSError) as e:
            return [{"status": "error", "message": str(e)}]

    m = _IMPORT_WORLD_RE.match(stripped)
    if m:
        try:
            info = import_world_payload(registry, m.group(1), m.group(2))
            return [{"status": "ok", "action": "IMPORT_WORLD", **info}]
        except (ValueError, OSError) as e:
            return [{"status": "error", "message": str(e)}]

    m = _PULL_RE.match(stripped)
    if m:
        try:
            info = pull_world(registry, peers, m.group(1), m.group(2))
            return [{"status": "ok", **info}]
        except (ValueError, OSError, RuntimeError, ConnectionError) as e:
            return [{"status": "error", "message": str(e)}]

    m = _PUSH_RE.match(stripped)
    if m:
        try:
            info = push_world(registry, peers, m.group(1), m.group(2))
            return [{"status": "ok", **info}]
        except (ValueError, OSError, RuntimeError, ConnectionError) as e:
            return [{"status": "error", "message": str(e)}]

    m = _SYNC_RE.match(stripped)
    if m:
        try:
            info = sync_world(registry, peers, m.group(1), m.group(2))
            return [{"status": "ok", **info}]
        except (ValueError, OSError, RuntimeError, ConnectionError) as e:
            return [{"status": "error", "message": str(e)}]

    return None


def is_replicate_query(stripped: str, upper: str) -> bool:
    return (
        upper == "LISTA WĘZŁÓW"
        or bool(_ADD_PEER_RE.match(stripped))
        or bool(_REMOVE_PEER_RE.match(stripped))
        or bool(_EXPORT_WORLD_RE.match(stripped))
        or bool(_IMPORT_WORLD_RE.match(stripped))
        or bool(_PULL_RE.match(stripped))
        or bool(_PUSH_RE.match(stripped))
        or bool(_SYNC_RE.match(stripped))
    )


def is_replicate_read_query(stripped: str, upper: str) -> bool:
    return upper == "LISTA WĘZŁÓW" or bool(_EXPORT_WORLD_RE.match(stripped))


def is_replicate_admin_query(stripped: str) -> bool:
    return bool(
        _ADD_PEER_RE.match(stripped)
        or _REMOVE_PEER_RE.match(stripped)
        or _IMPORT_WORLD_RE.match(stripped)
        or _PULL_RE.match(stripped)
        or _PUSH_RE.match(stripped)
        or _SYNC_RE.match(stripped)
    )


def world_from_replicate_query(stripped: str) -> Optional[str]:
    for pat in (_EXPORT_WORLD_RE, _IMPORT_WORLD_RE, _PULL_RE, _PUSH_RE, _SYNC_RE):
        m = pat.match(stripped)
        if m:
            return validate_world_name(m.group(1))
    return None