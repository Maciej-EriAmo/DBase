#!/usr/bin/env python3
"""
cynober_replicate.py — replikacja trwałych światów między węzłami (v7.4+)
v8.0: manifest-first + shardy per region grafu.
"""

from __future__ import annotations

import base64
import json
import os
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
_EXPORT_MANIFEST_RE = re.compile(
    r'^EKSPORT\s+MANIFEST\s+ŚWIATA\s+"([^"]+)"$', re.IGNORECASE,
)
_EXPORT_SHARD_RE = re.compile(
    r'^EKSPORT\s+SHARD\s+ŚWIATA\s+"([^"]+)"\s+REGION\s+"([^"]+)"$',
    re.IGNORECASE,
)
_IMPORT_SHARD_RE = re.compile(
    r'^IMPORT\s+SHARD\s+ŚWIATA\s+"([^"]+)"\s+REGION\s+"([^"]+)"\s+DANE\s+"([A-Za-z0-9+/=]+)"$',
    re.IGNORECASE,
)
_PULL_SHARD_RE = re.compile(
    r'^PULL\s+SHARD\s+ŚWIATA\s+"([^"]+)"\s+Z\s+"([^"]+)"\s+REGION\s+"([^"]+)"$',
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
                    "label": info.get("label") or info.get("role") or name,
                    "energy": float(info.get("energy", 1.0) or 1.0),
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
                "label": str(info.get("label") or info.get("role") or name),
                "energy": float(info.get("energy", 1.0) or 1.0),
            }

    def add(
        self,
        name: str,
        host: str,
        port: int,
        *,
        user: Optional[str] = None,
        token: Optional[str] = None,
        label: Optional[str] = None,
        energy: Optional[float] = None,
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
        if label:
            entry["label"] = str(label)
        if energy is not None:
            entry["energy"] = float(energy)
        else:
            entry.setdefault("energy", 1.0)
        with self._lock:
            self._data.setdefault("peers", {})[name] = entry
            self._save()
        return {
            "name": name,
            "host": host,
            "port": port,
            "label": entry.get("label", name),
            "energy": entry.get("energy", 1.0),
        }

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


def _ensure_world_flushed(registry: WorldRegistry, name: str) -> None:
    with registry._lock:
        world = registry._worlds.get(name)
        if world is not None and world.dirty:
            registry._persist(world)
            world.dirty = False


def _media_index_for_kafd(kafd: Path) -> list:
    """Faza 6: lekki indeks mediów z pliku .kafd (bez bazy sieciowej)."""
    try:
        from karmazyn_kernel import Store
        from karmazyn_media import load_store
        from karmazyn_media_preview import build_media_index

        store = Store(thermal=True)
        load_store(store, kafd, restore=False)
        return build_media_index(store)
    except Exception:
        return []


def export_manifest_payload(registry: WorldRegistry, world_name: str) -> dict:
    """Manifest + meta + indeks shardów + media_index (Faza 6, bez blobów)."""
    name = validate_world_name(world_name)
    _ensure_world_flushed(registry, name)
    kafd = _kafd_path(registry.base_dir, name)
    if not kafd.is_file():
        raise ValueError(f"Świat '{name}' nie ma zapisu na dysku.")
    meta = _load_meta(_meta_path(registry.base_dir, name))
    from cynober_world_shards import list_shard_regions, load_shard_index

    shard_index = load_shard_index(registry.base_dir, name)
    media_index = _media_index_for_kafd(kafd)
    return {
        "world": name,
        "kafd_b64": base64.b64encode(kafd.read_bytes()).decode("ascii"),
        "meta": meta,
        "modified_at": meta.get("modified_at"),
        "bubbles": meta.get("bubbles", 0),
        "sharded": bool(meta.get("sharded") or shard_index.get("sharded")),
        "shard_index": shard_index,
        "shard_regions": list_shard_regions(registry.base_dir, name),
        "media_index": media_index,
        "media_count": len(media_index),
    }


def export_shard_payload(registry: WorldRegistry, world_name: str, region: str) -> dict:
    name = validate_world_name(world_name)
    region = region.strip()
    if not region:
        raise ValueError("Brak identyfikatora regionu sharda.")
    _ensure_world_flushed(registry, name)
    from cynober_world_shards import read_shard_bytes

    data = read_shard_bytes(registry.base_dir, name, region)
    return {
        "world": name,
        "region": region,
        "shard_b64": base64.b64encode(data).decode("ascii"),
        "bytes": len(data),
    }


def export_world_payload(registry: WorldRegistry, world_name: str) -> dict:
    name = validate_world_name(world_name)
    payload = export_manifest_payload(registry, name)
    if not payload.get("sharded"):
        return payload

    shards_out: List[dict] = []
    for entry in payload.get("shard_regions") or []:
        rid = entry.get("id")
        if not rid:
            continue
        try:
            shard = export_shard_payload(registry, name, rid)
            shards_out.append({
                "region": rid,
                "shard_b64": shard["shard_b64"],
                "bytes": shard["bytes"],
            })
        except ValueError:
            pass
    payload["shards"] = shards_out
    payload["action"] = "EXPORT_WORLD"
    return payload


def import_shard_payload(
    registry: WorldRegistry,
    world_name: str,
    region: str,
    shard_b64: str,
) -> dict:
    name = validate_world_name(world_name)
    region = region.strip()
    try:
        shard_bytes = base64.b64decode(shard_b64.encode("ascii"), validate=True)
    except Exception as e:
        raise ValueError(f"Nieprawidłowe dane sharda (base64): {e}") from e
    if not shard_bytes:
        raise ValueError("Puste dane sharda.")
    from cynober_world_shards import write_shard_bytes

    path = write_shard_bytes(registry.base_dir, name, region, shard_bytes)
    return {"world": name, "region": region, "imported": True, "bytes": len(shard_bytes), "path": str(path)}


def _import_shards_from_payload(registry: WorldRegistry, name: str, payload: dict) -> int:
    shards = payload.get("shards") or []
    count = 0
    for entry in shards:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("region")
        b64 = entry.get("shard_b64")
        if rid and b64:
            import_shard_payload(registry, name, rid, b64)
            count += 1
    shard_index = payload.get("shard_index")
    if shard_index:
        from cynober_world_shards import import_shard_index

        import_shard_index(registry.base_dir, name, shard_index)
    return count


def import_world_payload(
    registry: WorldRegistry,
    world_name: str,
    kafd_b64: str,
    meta: Optional[dict] = None,
    *,
    shards: Optional[List[dict]] = None,
    shard_index: Optional[dict] = None,
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

    shard_count = 0
    if shards or shard_index:
        shard_count = _import_shards_from_payload(registry, name, {
            "shards": shards or [],
            "shard_index": shard_index,
        })

    with registry._lock:
        world = registry._worlds.get(name)
        if world is not None and world.refs > 0:
            new_rt = registry._create_runtime()
            proca = registry.base_dir / "proca" / name
            from cynober_world_shards import atom_shard_paths

            new_rt.kafd_path = dest
            new_rt.proca_dir = proca if proca.is_dir() else None
            new_rt.shard_index = atom_shard_paths(registry.base_dir, name)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".kafd") as tmp:
                tmp.write(kafd_bytes)
                tmp_path = tmp.name
            try:
                load_runtime_from_kafd(
                    new_rt.bridge,
                    tmp_path,
                    proca_dir=new_rt.proca_dir,
                    query_indexes=(meta or {}).get("query_indexes"),
                    lazy=False,
                    shard_paths=new_rt.shard_index,
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            with world.runtime.lock:
                world.runtime = new_rt
            world.dirty = False
            if meta:
                world.modified_at = float(meta.get("modified_at", time.time()))
        else:
            registry._worlds.pop(name, None)

    return {
        "world": name,
        "imported": True,
        "bytes": len(kafd_bytes),
        "shards_imported": shard_count,
    }


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
        from cynober_rpc import (
            HS_TIMEOUT_SEC,
            PROTO_VERSION,
            apply_tcp_keepalive,
            perform_handshake,
        )
        from karmazyn_handshake import _CryptoLayer

        if self._sock is not None:
            return  # już połączony — podtrzymanie sesji peera
        self._crypto = _CryptoLayer()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(HS_TIMEOUT_SEC)
        apply_tcp_keepalive(self._sock)
        self._sock.connect((self.host, self.port))
        deadline = time.monotonic() + HS_TIMEOUT_SEC
        _, _, _, self._hsl = perform_handshake(
            self._sock, self._crypto, is_server=False, deadline=deadline,
            client_version=PROTO_VERSION,
        )
        self._sock.settimeout(self.timeout)
        apply_tcp_keepalive(self._sock)

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


# Cache stałych tuneli peer→peer (klucz host:port). Close dopiero przy błędzie / reset.
_peer_session_cache: Dict[str, "_PeerRpc"] = {}
_peer_session_lock = threading.Lock()


def _peer_cache_key(peer: dict) -> str:
    return f"{peer.get('host', '')}:{int(peer.get('port') or 0)}"


def reset_peer_sessions() -> None:
    """Testy / shutdown — zamknij wszystkie cache'owane tunele peer."""
    with _peer_session_lock:
        sessions = list(_peer_session_cache.values())
        _peer_session_cache.clear()
    for s in sessions:
        try:
            s.close()
        except Exception:
            pass


def _peer_client(peer: dict, *, reuse: bool = True) -> _PeerRpc:
    """
    TCP+HSL do peera. Domyślnie reuse=True — stała sesja między PULL/SYNC/gossip.
    R wybiera peera wcześniej (resolve_peer); tu tylko hydraulika L0.
    """
    key = _peer_cache_key(peer)
    if reuse:
        with _peer_session_lock:
            cached = _peer_session_cache.get(key)
            if cached is not None and cached._sock is not None:
                return cached
    client = _PeerRpc(peer["host"], int(peer["port"]))
    client.connect()
    if reuse:
        with _peer_session_lock:
            _peer_session_cache[key] = client
    return client


def _peer_release(client: _PeerRpc, *, drop: bool = False) -> None:
    """Po operacji: zostaw tunel (domyślnie) albo drop=True przy błędzie transportu."""
    if not drop:
        return
    key = f"{client.host}:{int(client.port)}"
    with _peer_session_lock:
        if _peer_session_cache.get(key) is client:
            _peer_session_cache.pop(key, None)
    client.close()


# Aliasy: wybór peera przez rezonans Lorentza (Faza 6), potem zwykły TCP+HSL
AUTO_PEER_ALIASES = frozenset({"@", "AUTO", "RESONANCE", "REZONANS", "*"})


def _local_peer_probe() -> dict:
    """Lokalny kontekst rankingu — nie sekret sesji."""
    return {
        "label": os.environ.get(
            "KARM_PEER_LABEL",
            os.environ.get("CYNOBER_NODE_LABEL", "cynober rpc karminql"),
        ),
        "energy": float(os.environ.get("KARM_PEER_ENERGY", "1.0") or 1.0),
        "node_id": os.environ.get("CYNOBER_NODE_ID", "local"),
    }


def select_peer_from_registry(
    peers: PeerRegistry,
    *,
    R_min: float = 0.15,
    local: Optional[dict] = None,
) -> dict:
    """
    R → ranking → wybór → (caller) TCP → handshake → HSL.
    Rezonans NIE wchodzi do shared_key.
    """
    from karmazyn_hsl import connect_plan

    probe = local or _local_peer_probe()
    entries: List[dict] = []
    for row in peers.list_peers():
        try:
            entries.append(peers.get(row["name"]))
        except ValueError:
            continue
    if not entries:
        raise ValueError("Brak zarejestrowanych węzłów do rankingu rezonansu.")
    plan = connect_plan(probe, entries, R_min=float(R_min))
    sel = plan.get("selected")
    if not sel:
        raise ValueError(
            f"Żaden peer nie osiągnął R>={R_min}. "
            "Ustaw label/energy w peers.json albo wskaż węzeł po nazwie."
        )
    peer = dict(peers.get(sel["name"]))
    peer["resonance_R"] = sel.get("R")
    peer["connect_plan"] = {
        "resonance_feeds_kdf": plan.get("resonance_feeds_kdf", False),
        "pipeline": plan.get("pipeline"),
        "ranking": plan.get("ranking"),
    }
    return peer


def resolve_peer(
    peers: PeerRegistry,
    name: str,
    *,
    R_min: float = 0.15,
    local: Optional[dict] = None,
) -> dict:
    """Nazwa węzła albo alias AUTO/@ → peer dict gotowy do _peer_client."""
    raw = (name or "").strip()
    if not raw:
        raise ValueError("Brak nazwy węzła.")
    if raw.upper() in AUTO_PEER_ALIASES or raw == "@":
        return select_peer_from_registry(peers, R_min=R_min, local=local)
    return peers.get(raw)


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


def _remote_export(client: _PeerRpc, world: str, *, manifest_only: bool = False) -> dict:
    cmd = (
        f'EKSPORT MANIFEST ŚWIATA "{world}"'
        if manifest_only
        else f'EKSPORT ŚWIATA "{world}"'
    )
    row = _first_result(client.query(cmd))
    if "kafd_b64" not in row:
        raise RuntimeError("Węzeł nie zwrócił danych świata.")
    return row


def _remote_export_shard(client: _PeerRpc, world: str, region: str) -> dict:
    row = _first_result(
        client.query(f'EKSPORT SHARD ŚWIATA "{world}" REGION "{region}"')
    )
    if "shard_b64" not in row:
        raise RuntimeError("Węzeł nie zwrócił danych sharda.")
    return row


def _remote_import(client: _PeerRpc, world: str, payload: dict) -> dict:
    b64 = payload["kafd_b64"]
    row = _first_result(client.query(f'IMPORT ŚWIATA "{world}" DANE "{b64}"'))
    return row


def missing_media_entries(
    local_index: list,
    remote_index: list,
) -> list:
    """Porównaj indeksy — wpisy remote bez lokalnego cas/id."""
    local_by_id = {
        str(e.get("id")): e for e in (local_index or []) if isinstance(e, dict)
    }
    missing = []
    for e in remote_index or []:
        if not isinstance(e, dict):
            continue
        rid = str(e.get("id") or "")
        if not rid:
            continue
        loc = local_by_id.get(rid)
        if loc is None:
            missing.append(e)
            continue
        rc = str(e.get("cas12") or "")
        lc = str(loc.get("cas12") or "")
        if rc and lc and rc != lc:
            missing.append(e)
        elif int(e.get("size") or 0) != int(loc.get("size") or 0):
            missing.append(e)
    return missing


def sync_missing_media(
    registry: WorldRegistry,
    world: str,
    peer: dict,
    remote_index: list,
) -> dict:
    """
    Faza 6: dociągnij brakujące media przez KAFS (CynoberClient).
    Wymaga kafs-stream na peerze.
    """
    world = validate_world_name(world)
    from karmazyn_media_preview import build_media_index
    from cynober_client import CynoberClient, CynoberClientError

    # lokalny indeks po attach
    try:
        wobj = registry.attach(world)
        store = (
            wobj.runtime.engine.api.store
            if hasattr(wobj.runtime, "engine")
            else wobj.runtime.store
        )
        local_index = build_media_index(store)
    except Exception as e:
        return {"fetched": 0, "error": f"attach: {e}", "missing": 0}

    missing = missing_media_entries(local_index, remote_index)
    if not missing:
        return {"fetched": 0, "missing": 0, "ok": True}

    host = peer.get("host") or "127.0.0.1"
    port = int(peer.get("port") or 8080)
    c = CynoberClient(host, port)
    fetched = 0
    errors: list = []
    try:
        c.connect()
        user, token = peer.get("user"), peer.get("token")
        if user and token:
            row = c.query_line(f'ZALOGUJ "{user}" TOKEN "{token}"')
            if row.get("status") != "ok":
                return {
                    "fetched": 0,
                    "missing": len(missing),
                    "ok": False,
                    "error": row.get("message") or "login fail",
                }
        # wybór świata na serwerze
        c.query_line(f'WYBIERZ ŚWIAT "{world}"')
        if not c.kafs_enabled:
            return {
                "fetched": 0,
                "missing": len(missing),
                "ok": False,
                "error": "peer bez kafs-stream",
            }
        from karmazyn_media import (
            MEDIA_S,
            ensure_bubble,
            sync_bubble_record,
            _cas12,
        )

        for e in missing:
            mid = str(e.get("id"))
            try:
                data, mime, _meta = c.get_media(mid)
                mime = mime or e.get("mime") or "application/octet-stream"
                existing = store.get_atom(mid)
                if existing is None and callable(getattr(store, "create_atom", None)):
                    created = store.create_atom(
                        mid, S=MEDIA_S, E=e.get("binding") or "media", T=50.0
                    )
                    existing = store.get_atom(
                        created if isinstance(created, str) else getattr(created, "id", mid)
                    )
                if existing is None:
                    existing = store.atom_new(
                        S=MEDIA_S, E=e.get("binding") or mid, value=None, T=50.0
                    )
                existing.metadata["data"] = data
                existing.metadata["mime"] = mime
                existing.metadata["_cas"] = _cas12(data)
                existing.metadata.pop("_stream", None)
                existing.metadata["v"] = {
                    "kind": "media",
                    "size": len(data),
                    "mime": mime,
                    "binding": e.get("binding") or "",
                    "bubble": e.get("bubble") or "",
                }
                if e.get("bubble") and e.get("binding"):
                    b = ensure_bubble(store, e["bubble"], as_root=True)
                    if callable(getattr(b, "bind", None)):
                        b.bind(str(e["binding"]), existing)
                    sync_bubble_record(store, b)
                fetched += 1
            except Exception as ex:
                errors.append(f"{mid}: {ex}")
        if fetched:
            try:
                registry.mark_dirty(world)
            except Exception:
                pass
            try:
                registry.flush(world)
            except Exception:
                pass
        return {
            "fetched": fetched,
            "missing": len(missing),
            "ok": fetched == len(missing) or fetched > 0,
            "errors": errors[:10],
        }
    except (CynoberClientError, OSError, RuntimeError) as e:
        return {
            "fetched": fetched,
            "missing": len(missing),
            "ok": False,
            "error": str(e),
        }
    finally:
        try:
            c.close()
        except Exception:
            pass


def pull_world(
    registry: WorldRegistry,
    peers: PeerRegistry,
    world: str,
    peer_name: str,
    *,
    manifest_first: bool = True,
    sync_media: bool = True,
) -> dict:
    world = validate_world_name(world)
    peer = resolve_peer(peers, peer_name)
    peer_name = peer["name"]
    client = _peer_client(peer)
    drop = False
    try:
        client.connect()
        _login_peer(client, peer)
        remote_media_index: list = []
        if manifest_first:
            manifest = _remote_export(client, world, manifest_only=True)
            remote_media_index = list(manifest.get("media_index") or [])
            info = import_world_payload(
                registry,
                world,
                manifest["kafd_b64"],
                manifest.get("meta"),
                shard_index=manifest.get("shard_index"),
            )
            shards_pulled = 0
            if manifest.get("sharded"):
                for entry in manifest.get("shard_regions") or []:
                    rid = entry.get("id")
                    if not rid:
                        continue
                    shard = _remote_export_shard(client, world, rid)
                    import_shard_payload(registry, world, rid, shard["shard_b64"])
                    shards_pulled += 1
                info["shards_imported"] = shards_pulled
            info["media_index_remote"] = len(remote_media_index)
        else:
            payload = _remote_export(client, world, manifest_only=False)
            remote_media_index = list(payload.get("media_index") or [])
            info = import_world_payload(
                registry,
                world,
                payload["kafd_b64"],
                payload.get("meta"),
                shards=payload.get("shards"),
                shard_index=payload.get("shard_index"),
            )
            info["media_index_remote"] = len(remote_media_index)
        # Faza 6: dociągnij braki KAFS (gdy indeks remote > local po imporcie)
        if sync_media and remote_media_index:
            try:
                media_sync = sync_missing_media(
                    registry, world, peer, remote_media_index
                )
                info["media_sync"] = media_sync
            except Exception as e:
                info["media_sync"] = {"ok": False, "error": str(e)}
        return {
            "action": "PULL_WORLD",
            "world": world,
            "peer": peer_name,
            "direction": "pull",
            **info,
        }
    except (OSError, ConnectionError, TimeoutError):
        drop = True
        raise
    finally:
        _peer_release(client, drop=drop)


def pull_shard(
    registry: WorldRegistry,
    peers: PeerRegistry,
    world: str,
    peer_name: str,
    region: str,
) -> dict:
    world = validate_world_name(world)
    peer = resolve_peer(peers, peer_name)
    peer_name = peer["name"]
    client = _peer_client(peer)
    drop = False
    try:
        client.connect()
        _login_peer(client, peer)
        shard = _remote_export_shard(client, world, region)
        info = import_shard_payload(registry, world, region, shard["shard_b64"])
        return {
            "action": "PULL_SHARD",
            "world": world,
            "peer": peer_name,
            "region": region,
            **info,
        }
    except (OSError, ConnectionError, TimeoutError):
        drop = True
        raise
    finally:
        _peer_release(client, drop=drop)


def push_world(registry: WorldRegistry, peers: PeerRegistry, world: str, peer_name: str) -> dict:
    world = validate_world_name(world)
    peer = resolve_peer(peers, peer_name)
    peer_name = peer["name"]
    payload = export_world_payload(registry, world)
    client = _peer_client(peer)
    drop = False
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
    except (OSError, ConnectionError, TimeoutError):
        drop = True
        raise
    finally:
        _peer_release(client, drop=drop)


def sync_world(registry: WorldRegistry, peers: PeerRegistry, world: str, peer_name: str) -> dict:
    world = validate_world_name(world)
    peer = resolve_peer(peers, peer_name)
    peer_name = peer["name"]
    local = registry._world_info(world)
    local_mod = float(local.get("modified_at") or 0)
    local_exists = bool(local.get("exists_on_disk"))

    client = _peer_client(peer)
    drop = False
    try:
        client.connect()
        _login_peer(client, peer)
        remote_mod = _remote_world_modified(client, world)
        remote_exists = remote_mod is not None

        if remote_exists and local_exists:
            if remote_mod > local_mod:
                payload = _remote_export(client, world)
                info = import_world_payload(
                    registry,
                    world,
                    payload["kafd_b64"],
                    payload.get("meta"),
                    shards=payload.get("shards"),
                    shard_index=payload.get("shard_index"),
                )
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
            info = import_world_payload(
                registry,
                world,
                payload["kafd_b64"],
                payload.get("meta"),
                shards=payload.get("shards"),
                shard_index=payload.get("shard_index"),
            )
            return {"action": "SYNC_WORLD", "world": world, "peer": peer_name, "direction": "pull", **info}
        if local_exists and not remote_exists:
            payload = export_world_payload(registry, world)
            remote = _remote_import(client, world, payload)
            return {"action": "SYNC_WORLD", "world": world, "peer": peer_name, "direction": "push", "remote": remote}
        raise ValueError(f"Świat '{world}' nie istnieje lokalnie ani na węźle '{peer_name}'.")
    except (OSError, ConnectionError, TimeoutError):
        drop = True
        raise
    finally:
        _peer_release(client, drop=drop)


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

    if upper in ("LISTA WĘZŁÓW REZONANS", "LISTA WEZLOW REZONANS"):
        try:
            from karmazyn_hsl import connect_plan

            probe = _local_peer_probe()
            entries = []
            for row in peers.list_peers():
                try:
                    entries.append(peers.get(row["name"]))
                except ValueError:
                    continue
            plan = connect_plan(probe, entries, R_min=0.0)
            return [{
                "status": "ok",
                "action": "LIST_PEERS_RESONANCE",
                "local": probe,
                "resonance_feeds_kdf": False,
                "pipeline": plan.get("pipeline"),
                "selected": plan.get("selected"),
                "ranking": plan.get("ranking"),
                "peers": peers.list_peers(),
                "server_version": SERVER_VERSION,
            }]
        except Exception as e:
            return [{"status": "error", "action": "LIST_PEERS_RESONANCE", "message": str(e)}]

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

    m = _EXPORT_MANIFEST_RE.match(stripped)
    if m:
        try:
            payload = export_manifest_payload(registry, m.group(1))
            return [{"status": "ok", "action": "EXPORT_MANIFEST", **payload}]
        except (ValueError, OSError) as e:
            return [{"status": "error", "message": str(e)}]

    m = _EXPORT_SHARD_RE.match(stripped)
    if m:
        try:
            payload = export_shard_payload(registry, m.group(1), m.group(2))
            return [{"status": "ok", "action": "EXPORT_SHARD", **payload}]
        except (ValueError, OSError) as e:
            return [{"status": "error", "message": str(e)}]

    m = _IMPORT_SHARD_RE.match(stripped)
    if m:
        try:
            info = import_shard_payload(registry, m.group(1), m.group(2), m.group(3))
            return [{"status": "ok", "action": "IMPORT_SHARD", **info}]
        except (ValueError, OSError) as e:
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

    m = _PULL_SHARD_RE.match(stripped)
    if m:
        try:
            info = pull_shard(registry, peers, m.group(1), m.group(2), m.group(3))
            return [{"status": "ok", **info}]
        except (ValueError, OSError, RuntimeError, ConnectionError) as e:
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
        or upper in ("LISTA WĘZŁÓW REZONANS", "LISTA WEZLOW REZONANS")
        or bool(_ADD_PEER_RE.match(stripped))
        or bool(_REMOVE_PEER_RE.match(stripped))
        or bool(_EXPORT_MANIFEST_RE.match(stripped))
        or bool(_EXPORT_SHARD_RE.match(stripped))
        or bool(_IMPORT_SHARD_RE.match(stripped))
        or bool(_EXPORT_WORLD_RE.match(stripped))
        or bool(_IMPORT_WORLD_RE.match(stripped))
        or bool(_PULL_SHARD_RE.match(stripped))
        or bool(_PULL_RE.match(stripped))
        or bool(_PUSH_RE.match(stripped))
        or bool(_SYNC_RE.match(stripped))
    )


def is_replicate_read_query(stripped: str, upper: str) -> bool:
    return (
        upper == "LISTA WĘZŁÓW"
        or upper in ("LISTA WĘZŁÓW REZONANS", "LISTA WEZLOW REZONANS")
        or bool(_EXPORT_MANIFEST_RE.match(stripped))
        or bool(_EXPORT_SHARD_RE.match(stripped))
        or bool(_EXPORT_WORLD_RE.match(stripped))
    )


def is_replicate_admin_query(stripped: str) -> bool:
    return bool(
        _ADD_PEER_RE.match(stripped)
        or _REMOVE_PEER_RE.match(stripped)
        or _IMPORT_WORLD_RE.match(stripped)
        or _IMPORT_SHARD_RE.match(stripped)
        or _PULL_SHARD_RE.match(stripped)
        or _PULL_RE.match(stripped)
        or _PUSH_RE.match(stripped)
        or _SYNC_RE.match(stripped)
    )


def world_from_replicate_query(stripped: str) -> Optional[str]:
    for pat in (
        _EXPORT_MANIFEST_RE,
        _EXPORT_SHARD_RE,
        _IMPORT_SHARD_RE,
        _EXPORT_WORLD_RE,
        _IMPORT_WORLD_RE,
        _PULL_SHARD_RE,
        _PULL_RE,
        _PUSH_RE,
        _SYNC_RE,
    ):
        m = pat.match(stripped)
        if m:
            return validate_world_name(m.group(1))
    return None