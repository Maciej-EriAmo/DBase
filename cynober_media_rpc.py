"""
cynober_media_rpc.py — MEDIA PUT/GET + KAFS chunky (Faza 3)
============================================================
Sterowanie: KarminQL-RPC (JSON).
Dane: ramki FRAME_KAFS po negocjacji kafs-stream.

  MEDIA PUT START "id" MIME "…" SIZE n [BĄBEL "…" JAKO "…"]
  … klient wysyła KAFS DATA …
  MEDIA PUT END "id"

  MEDIA GET "id" [OFFSET n] [LIMIT m]
  … serwer: odpowiedź RPC + KAFS DATA… + END

  MEDIA STAT "id"
  MEDIA LIST "bąbel"          — bindingi mediów przy encji/bąblu (lore-editor)
"""

from __future__ import annotations

import re
import struct
import threading
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from cynober_rpc import KAFS_CHUNK_MAX

# KAFS body
KAFS_DATA = 1
KAFS_END = 2
KAFS_ERR = 3

_MEDIA_PUT_START_RE = re.compile(
    r'^MEDIA\s+PUT\s+START\s+"([^"]+)"\s+MIME\s+"([^"]+)"\s+SIZE\s+(\d+)'
    r'(?:\s+BĄBEL\s+"([^"]+)"\s+JAKO\s+"([^"]+)")?$',
    re.IGNORECASE,
)
_MEDIA_PUT_END_RE = re.compile(
    r'^MEDIA\s+PUT\s+END\s+"([^"]+)"$',
    re.IGNORECASE,
)
_MEDIA_GET_RE = re.compile(
    r'^MEDIA\s+GET\s+"([^"]+)"(?:\s+OFFSET\s+(\d+))?(?:\s+LIMIT\s+(\d+))?$',
    re.IGNORECASE,
)
_MEDIA_STAT_RE = re.compile(
    r'^MEDIA\s+STAT\s+"([^"]+)"$',
    re.IGNORECASE,
)
_MEDIA_LIST_RE = re.compile(
    r'^MEDIA\s+LIST\s+"([^"]+)"$',
    re.IGNORECASE,
)


def is_media_query(query: str) -> bool:
    u = (query or "").strip().upper()
    return u.startswith("MEDIA ")


@dataclass
class MediaUpload:
    atom_id: str
    mime: str
    size: int
    bubble: str = ""
    binding: str = ""
    parts: list[bytes] = field(default_factory=list)
    received: int = 0


@dataclass
class KafsMessage:
    msg_type: int
    xfer_id: str
    seq: int
    total_size: int
    data: bytes = b""
    error: str = ""


def encode_kafs_data(
    xfer_id: str,
    seq: int,
    total_size: int,
    chunk: bytes,
) -> bytes:
    xid = (xfer_id or "")[:16].encode("utf-8")
    xid = xid + b"\0" * (16 - len(xid))
    return (
        struct.pack(">B", KAFS_DATA)
        + xid
        + struct.pack(">I", int(seq))
        + struct.pack(">Q", int(total_size))
        + struct.pack(">I", len(chunk))
        + chunk
    )


def encode_kafs_end(xfer_id: str) -> bytes:
    xid = (xfer_id or "")[:16].encode("utf-8")
    xid = xid + b"\0" * (16 - len(xid))
    return struct.pack(">B", KAFS_END) + xid + struct.pack(">I", 0) + struct.pack(">Q", 0) + struct.pack(">I", 0)


def encode_kafs_err(xfer_id: str, message: str) -> bytes:
    xid = (xfer_id or "")[:16].encode("utf-8")
    xid = xid + b"\0" * (16 - len(xid))
    msg = (message or "error").encode("utf-8")[:1024]
    return (
        struct.pack(">B", KAFS_ERR)
        + xid
        + struct.pack(">I", 0)
        + struct.pack(">Q", 0)
        + struct.pack(">I", len(msg))
        + msg
    )


def decode_kafs_body(body: bytes) -> KafsMessage:
    if len(body) < 1 + 16 + 4 + 8 + 4:
        raise ValueError("KAFS: za krótka ramka")
    msg_type = body[0]
    xfer_id = body[1:17].split(b"\0", 1)[0].decode("utf-8", errors="replace")
    seq = struct.unpack(">I", body[17:21])[0]
    total_size = struct.unpack(">Q", body[21:29])[0]
    clen = struct.unpack(">I", body[29:33])[0]
    data = body[33 : 33 + clen]
    if len(data) != clen:
        raise ValueError("KAFS: niepełny chunk")
    if msg_type == KAFS_ERR:
        return KafsMessage(
            msg_type=msg_type,
            xfer_id=xfer_id,
            seq=seq,
            total_size=total_size,
            error=data.decode("utf-8", errors="replace"),
        )
    return KafsMessage(
        msg_type=msg_type,
        xfer_id=xfer_id,
        seq=seq,
        total_size=total_size,
        data=data,
    )


def iter_chunks(data: bytes, chunk_size: int = KAFS_CHUNK_MAX) -> Iterator[bytes]:
    if chunk_size <= 0:
        chunk_size = KAFS_CHUNK_MAX
    if not data:
        yield b""
        return
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


class MediaSession:
    """Stan uploadów KAFS w jednej sesji TCP."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.uploads: dict[str, MediaUpload] = {}
        self.bytes_in = 0
        self.bytes_out = 0

    def start_put(
        self,
        atom_id: str,
        mime: str,
        size: int,
        *,
        bubble: str = "",
        binding: str = "",
    ) -> MediaUpload:
        atom_id = (atom_id or "").strip()
        if not atom_id:
            raise ValueError("MEDIA PUT: puste id")
        if size < 0:
            raise ValueError("MEDIA PUT: ujemny SIZE")
        if size > 512 * 1024 * 1024:
            raise ValueError("MEDIA PUT: SIZE > 512 MiB (limit sesji)")
        with self._lock:
            up = MediaUpload(
                atom_id=atom_id,
                mime=(mime or "application/octet-stream").strip(),
                size=int(size),
                bubble=(bubble or "").strip(),
                binding=(binding or "").strip(),
            )
            self.uploads[atom_id] = up
            return up

    def feed_kafs(self, body: bytes) -> None:
        msg = decode_kafs_body(body)
        if msg.msg_type == KAFS_ERR:
            raise ValueError(msg.error or "KAFS ERR")
        if msg.msg_type == KAFS_END:
            return
        if msg.msg_type != KAFS_DATA:
            raise ValueError(f"KAFS: nieznany typ {msg.msg_type}")
        with self._lock:
            up = self.uploads.get(msg.xfer_id)
            if up is None:
                raise ValueError(f"KAFS: brak aktywnego PUT dla „{msg.xfer_id}”")
            if len(msg.data) > KAFS_CHUNK_MAX + 64:
                raise ValueError("KAFS: chunk za duży")
            up.parts.append(msg.data)
            up.received += len(msg.data)
            self.bytes_in += len(msg.data)
            if up.size and up.received > up.size:
                raise ValueError("KAFS: przekroczono zadeklarowany SIZE")

    def end_put(self, atom_id: str) -> bytes:
        with self._lock:
            up = self.uploads.pop(atom_id, None)
            if up is None:
                raise ValueError(f"MEDIA PUT END: brak START dla „{atom_id}”")
            data = b"".join(up.parts)
            if up.size and len(data) != up.size:
                raise ValueError(
                    f"MEDIA PUT END: otrzymano {len(data)} B, oczekiwano {up.size} B"
                )
            # zachowaj meta na atom
            self._last_end_meta = {
                "atom_id": up.atom_id,
                "mime": up.mime,
                "bubble": up.bubble,
                "binding": up.binding,
                "size": len(data),
            }
            return data

    def last_end_meta(self) -> dict:
        return dict(getattr(self, "_last_end_meta", {}) or {})


def try_media_command(
    facade: Any,
    query: str,
    *,
    media_session: MediaSession,
    kafs_enabled: bool,
) -> Optional[list[dict]]:
    """
    Obsłuż MEDIA * na facade. Zwraca results lub None (nie media).
    Dla MEDIA GET dołącza klucz _stream w dict wyniku (dla handle_client).
    """
    stripped = query.strip()
    if not is_media_query(stripped):
        return None

    m = _MEDIA_STAT_RE.match(stripped)
    if m:
        return [_media_stat(facade, m.group(1))]

    m = _MEDIA_LIST_RE.match(stripped)
    if m:
        deny = _media_perm(facade, write=False)
        if deny:
            return deny
        return [_media_list_bubble(facade, m.group(1))]

    m = _MEDIA_PUT_START_RE.match(stripped)
    if m:
        if not kafs_enabled:
            return [{
                "status": "error",
                "action": "MEDIA_PUT_START",
                "message": "MEDIA PUT wymaga negocjacji kafs-stream (zaktualizuj klienta/serwer).",
            }]
        deny = _media_perm(facade, write=True)
        if deny:
            return deny
        atom_id, mime, size_s, bubble, binding = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        try:
            media_session.start_put(
                atom_id,
                mime,
                int(size_s),
                bubble=bubble or "",
                binding=binding or "",
            )
        except ValueError as e:
            return [{"status": "error", "action": "MEDIA_PUT_START", "message": str(e)}]
        return [{
            "status": "ok",
            "action": "MEDIA_PUT_START",
            "id": atom_id,
            "mime": mime,
            "size": int(size_s),
            "kafs": True,
            "chunk_max": KAFS_CHUNK_MAX,
        }]

    m = _MEDIA_PUT_END_RE.match(stripped)
    if m:
        if not kafs_enabled:
            return [{
                "status": "error",
                "action": "MEDIA_PUT_END",
                "message": "MEDIA PUT wymaga kafs-stream.",
            }]
        deny = _media_perm(facade, write=True)
        if deny:
            return deny
        atom_id = m.group(1)
        try:
            data = media_session.end_put(atom_id)
            meta = media_session.last_end_meta()
            _commit_media_atom(
                facade,
                atom_id=meta.get("atom_id") or atom_id,
                data=data,
                mime=meta.get("mime") or "application/octet-stream",
                bubble=meta.get("bubble") or "",
                binding=meta.get("binding") or "",
            )
        except ValueError as e:
            return [{"status": "error", "action": "MEDIA_PUT_END", "message": str(e)}]
        except Exception as e:
            return [{"status": "error", "action": "MEDIA_PUT_END", "message": str(e)}]
        return [{
            "status": "ok",
            "action": "MEDIA_PUT_END",
            "id": atom_id,
            "size": len(data),
            "mime": meta.get("mime"),
        }]

    m = _MEDIA_GET_RE.match(stripped)
    if m:
        deny = _media_perm(facade, write=False)
        if deny:
            return deny
        atom_id = m.group(1)
        offset = int(m.group(2) or 0)
        limit = int(m.group(3) or 0)
        try:
            data, mime, T = _load_media_atom(facade, atom_id)
        except ValueError as e:
            return [{"status": "error", "action": "MEDIA_GET", "message": str(e)}]
        if offset < 0:
            offset = 0
        if offset > len(data):
            offset = len(data)
        end = len(data) if limit <= 0 else min(len(data), offset + limit)
        slice_ = data[offset:end]
        row: dict[str, Any] = {
            "status": "ok",
            "action": "MEDIA_GET",
            "id": atom_id,
            "mime": mime,
            "size": len(data),
            "offset": offset,
            "length": len(slice_),
            "T": T,
            "kafs": bool(kafs_enabled),
            "chunk_max": KAFS_CHUNK_MAX,
        }
        if kafs_enabled:
            row["_stream_bytes"] = slice_  # handle_client wyśle KAFS i usunie
            row["stream"] = True
        else:
            # fallback mały: tylko gdy ≤ 64 KiB
            if len(slice_) <= 64 * 1024:
                import base64

                row["data_b64"] = base64.b64encode(slice_).decode("ascii")
                row["stream"] = False
            else:
                return [{
                    "status": "error",
                    "action": "MEDIA_GET",
                    "message": (
                        "Brak kafs-stream — payload > 64 KiB. "
                        "Zaktualizuj klienta z FEATURE kafs-stream."
                    ),
                }]
        return [row]

    return [{
        "status": "error",
        "action": "MEDIA",
        "message": "Nieznana składnia MEDIA (PUT START/END, GET, STAT).",
    }]


def _media_perm(facade: Any, *, write: bool) -> Optional[list]:
    auth = getattr(facade, "_auth", None)
    if auth is None or not getattr(auth, "enabled", False):
        return None
    user = getattr(facade, "_auth_user", None)
    if not user:
        return [{"status": "error", "message": "Wymagane logowanie: ZALOGUJ \"user\" TOKEN \"…\"."}]
    world = facade.world_name or "*"
    from cynober_world_auth import ROLE_READER, ROLE_WRITER

    need = ROLE_WRITER if write else ROLE_READER
    if auth.has_min_role(user, world, need) or auth.has_min_role(user, "*", need):
        return None
    role_name = "writer" if write else "reader"
    return [{
        "status": "error",
        "message": f"MEDIA wymaga roli {role_name} (świat '{world}').",
    }]


def _runtime_store(facade: Any):
    rt = facade._runtime()
    return rt.engine.api.store if hasattr(rt, "engine") else rt.store


def _commit_media_atom(
    facade: Any,
    *,
    atom_id: str,
    data: bytes,
    mime: str,
    bubble: str,
    binding: str,
) -> None:
    """Zapis payloadu: A_RAW lub A_STREAM (Faza 4) przy przekroczeniu progu."""
    from karmazyn_media import (
        MEDIA_S,
        MEDIA_SEG_S,
        ensure_bubble,
        is_stream_atom,
        segment_size_effective,
        stream_threshold_effective,
        sync_bubble_record,
        _cas12,
    )
    import hashlib

    store = _runtime_store(facade)
    mime = (mime or "application/octet-stream").strip()
    thr = stream_threshold_effective()
    seg_sz = segment_size_effective()
    use_stream = thr >= 0 and len(data) > thr

    # usuń stare segmenty gdy nadpisujemy stream
    old = store.get_atom(atom_id) if callable(getattr(store, "get_atom", None)) else None
    if old is not None and is_stream_atom(old):
        v_old = old.metadata.get("v") if isinstance(old.metadata.get("v"), dict) else {}
        for sid in v_old.get("segments") or []:
            try:
                if callable(getattr(store, "atom_delete", None)):
                    store.atom_delete(str(sid))
                elif callable(getattr(store, "delete_atom", None)):
                    store.delete_atom(str(sid))
            except Exception:
                pass

    if not use_stream:
        existing = old
        if existing is None and callable(getattr(store, "create_atom", None)):
            created = store.create_atom(
                atom_id, S=MEDIA_S, E=binding or "media", T=50.0
            )
            existing = store.get_atom(
                created if isinstance(created, str) else getattr(created, "id", atom_id)
            )
        if existing is None:
            existing = store.atom_new(
                S=MEDIA_S, E=binding or atom_id, value=None, T=50.0
            )
        if hasattr(existing, "S"):
            try:
                existing.S = MEDIA_S
            except Exception:
                pass
        existing.metadata["data"] = data
        existing.metadata["mime"] = mime
        existing.metadata["_cas"] = _cas12(data)
        existing.metadata.pop("_stream", None)
        existing.metadata["v"] = {
            "kind": "media",
            "atype": "A_RAW",
            "size": len(data),
            "mime": mime,
            "binding": binding,
            "bubble": bubble,
            "requested_id": atom_id,
        }
        head = existing
    else:
        # head ze stałym id (protokół MEDIA PUT "id")
        head = old
        if head is None and callable(getattr(store, "create_atom", None)):
            created = store.create_atom(
                atom_id, S=MEDIA_S, E=binding or "media", T=50.0
            )
            head = store.get_atom(
                created if isinstance(created, str) else getattr(created, "id", atom_id)
            )
        if head is None:
            head = store.atom_new(S=MEDIA_S, E=binding or atom_id, value=None, T=50.0)
        head_id = str(getattr(head, "id", atom_id))
        seg_ids: list[str] = []
        offset = 0
        idx = 0
        while offset < len(data):
            chunk = data[offset : offset + seg_sz]
            seg = store.atom_new(
                S=MEDIA_SEG_S,
                E=f"seg{idx}:{binding or head_id}"[:120],
                value=None,
                T=45.0,
            )
            seg.metadata["data"] = chunk
            seg.metadata["mime"] = "application/octet-stream"
            seg.metadata["_cas"] = _cas12(chunk)
            seg.metadata["v"] = {
                "kind": "media_segment",
                "parent": head_id,
                "index": idx,
                "size": len(chunk),
                "offset": offset,
            }
            seg_ids.append(str(seg.id))
            offset += len(chunk)
            idx += 1
        cas_full = hashlib.sha256(data).hexdigest()
        head.metadata["data"] = b""
        head.metadata["mime"] = mime
        head.metadata["_cas"] = hashlib.sha256(data).digest()[:12].hex()
        head.metadata["_stream"] = True
        if hasattr(head, "S"):
            try:
                head.S = MEDIA_S
            except Exception:
                pass
        head.metadata["v"] = {
            "kind": "media_stream",
            "atype": "A_STREAM",
            "binding": binding,
            "bubble": bubble,
            "size": len(data),
            "mime": mime,
            "segment_size": seg_sz,
            "segments": seg_ids,
            "n_segments": len(seg_ids),
            "sha256": cas_full,
            "requested_id": atom_id,
        }

    if bubble and binding and callable(getattr(store, "bubble_new", None)):
        b = ensure_bubble(store, bubble, as_root=True)
        if callable(getattr(b, "bind", None)):
            b.bind(binding, head)
        else:
            b.bindings[binding] = head.id
        sync_bubble_record(store, b)
    if getattr(facade, "_world", None) is not None:
        facade._registry.mark_dirty(facade._world.name)


def _load_media_atom(facade: Any, atom_id: str) -> tuple[bytes, str, float]:
    """Odczyt A_RAW lub reassemble A_STREAM (Faza 4)."""
    from karmazyn_media import get_bytes

    store = _runtime_store(facade)
    atom = store.get_atom(atom_id)
    if atom is None:
        raise ValueError(f"Brak atomu „{atom_id}”.")
    data, mime = get_bytes(store, atom_id)
    T = float(getattr(atom, "T", 50.0))
    return data, mime, T


def _media_list_bubble(facade: Any, bubble_label: str) -> dict:
    """Lista bindingów mediów przy bąblu/encji (lore-editor lista_mediow)."""
    from karmazyn_media import list_bindings

    store = _runtime_store(facade)
    label = (bubble_label or "").strip()
    items: list[dict] = []
    try:
        refs = list_bindings(store, label)
    except Exception as e:
        return {
            "status": "error",
            "action": "MEDIA_LIST",
            "message": str(e),
        }
    for ref in refs:
        items.append({
            "atom_id": ref.atom_id,
            "binding": ref.binding,
            "mime": ref.mime,
            "size": int(ref.size or 0),
            "cas12": getattr(ref, "cas12", "") or "",
        })
    return {
        "status": "ok",
        "action": "MEDIA_LIST",
        "bubble": label,
        "media": items,
        "count": len(items),
    }


def _media_stat(facade: Any, atom_id: str) -> dict:
    deny = _media_perm(facade, write=False)
    if deny:
        return deny[0]
    store = _runtime_store(facade)
    atom = store.get_atom(atom_id)
    if atom is None:
        return {
            "status": "error",
            "action": "MEDIA_STAT",
            "message": f"Brak atomu „{atom_id}”.",
        }
    from karmazyn_media import is_stream_atom

    mime = str(atom.metadata.get("mime") or "application/octet-stream")
    v = atom.metadata.get("v") if isinstance(atom.metadata.get("v"), dict) else {}
    if v.get("mime"):
        mime = str(v["mime"])
    if is_stream_atom(atom):
        size = int(v.get("size") or 0)
        stream = True
        n_seg = int(v.get("n_segments") or len(v.get("segments") or []))
    else:
        data = atom.metadata.get("data")
        if not isinstance(data, (bytes, bytearray)):
            return {
                "status": "error",
                "action": "MEDIA_STAT",
                "message": f"Atom „{atom_id}” nie ma payloadu.",
            }
        size = len(data)
        stream = False
        n_seg = 0
    T = float(getattr(atom, "T", 50.0))
    folded = bool((getattr(atom, "metadata", None) or {}).get("_folded"))
    return {
        "status": "ok",
        "action": "MEDIA_STAT",
        "id": atom_id,
        "mime": mime,
        "size": size,
        "T": T,
        "folded": folded,
        "stream": stream,
        "n_segments": n_seg,
    }
