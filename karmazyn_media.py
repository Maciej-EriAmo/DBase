"""
karmazyn_media.py — lokalne API mediów (Faza 0 + 2 + 4 stream)
================================================================
Plik / bajty → atom w Store (+ opcjonalny bind w bąblu) → KAFD → z powrotem.

Zasady (PLAN_MULTIMEDIA_WDROZENIE.md):
  • media = atomy z metadata["data"] + metadata["mime"]  (A_RAW)
  • duże: head S=media (A_STREAM) + segmenty S=media_seg
  • S = "media" / "media_seg" (persystencja save_documents)
  • sieć / KAFS = Faza 3+ (tu lokalnie + reassemble)
  • zero HTTP, zero base64 w KarminQL

Publiczna powierzchnia:
  attach_bytes, attach_file, get_bytes, iter_bytes, export_to_path
  is_stream_atom, list_bindings, sync_bubble_record, restore_bubbles
  save_store / load_store
  pipe_to, materialize_temp, open_with_system, try_external_player  (Faza 2)

CLI:  python -m karmazyn_media extract|list|open|pipe …
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Optional, Union

# Kind zapisany w KAFD (karmazyn_store.DOC_KINDS)
MEDIA_S = "media"
MEDIA_SEG_S = "media_seg"
BUBBLE_S = "__bubble__"

# Limit ostrzeżenia (duże pliki OK lokalnie; sieć to Faza 3)
DEFAULT_WARN_BYTES = 16 * 1024 * 1024  # 16 MiB
# Faza 4: powyżej progu → head A_STREAM + segmenty (nie jeden monolit w head)
DEFAULT_STREAM_THRESHOLD = 8 * 1024 * 1024  # 8 MiB
DEFAULT_SEGMENT_SIZE = 1 * 1024 * 1024  # 1 MiB


def stream_threshold_effective(override: Optional[int] = None) -> int:
    """Próg A_STREAM: arg → env KARM_MEDIA_STREAM_THRESHOLD → default 8 MiB."""
    if override is not None:
        return int(override)
    env = os.environ.get("KARM_MEDIA_STREAM_THRESHOLD")
    if env is not None and str(env).strip() != "":
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_STREAM_THRESHOLD


def segment_size_effective(override: Optional[int] = None) -> int:
    if override is not None and int(override) > 0:
        return int(override)
    env = os.environ.get("KARM_MEDIA_SEGMENT_SIZE")
    if env is not None and str(env).strip() != "":
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass
    return DEFAULT_SEGMENT_SIZE


class MediaError(RuntimeError):
    """Błąd API mediów (brak atomu, zły typ, pusty plik, …)."""


@dataclass(frozen=True)
class MediaRef:
    """Lekki opis dołączonego medium."""

    atom_id: str
    binding: str
    mime: str
    size: int
    bubble_label: str = ""
    cas12: str = ""  # hex pierwszych 12 B SHA-256

    def sha256_hex(self, store: Any) -> str:
        data, _ = get_bytes(store, self.atom_id)
        return hashlib.sha256(data).hexdigest()


def _as_bytes(data: Union[bytes, bytearray, memoryview]) -> bytes:
    if isinstance(data, memoryview):
        return data.tobytes()
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, bytes):
        return data
    raise MediaError(f"Oczekiwano bytes, jest {type(data).__name__}")


def _cas12(data: bytes) -> str:
    return hashlib.sha256(data).digest()[:12].hex()


def _guess_mime(path: str | Path, fallback: str = "application/octet-stream") -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or fallback


def _bubble_label(bubble: Any) -> str:
    if isinstance(bubble, str):
        return bubble.strip()
    label = getattr(bubble, "label", None) or getattr(bubble, "name", None)
    if label:
        return str(label)
    return str(getattr(bubble, "id", "bubble") or "bubble")


def ensure_bubble(store: Any, bubble_or_label: Any, *, as_root: bool = False) -> Any:
    """Zwróć Bubble; utwórz po etykiecie gdy podano str."""
    if not isinstance(bubble_or_label, str):
        b = bubble_or_label
        if as_root and callable(getattr(store, "set_root", None)):
            try:
                store.set_root(b)
            except Exception:
                pass
        return b

    label = bubble_or_label.strip()
    if not label:
        raise MediaError("Pusta etykieta bąbla.")

    # Szukaj istniejącego bąbla po label (gdy store trzyma listę)
    for name in ("get_bubble",):
        fn = getattr(store, name, None)
        if callable(fn):
            try:
                existing = fn(label)
                if existing is not None:
                    return existing
            except Exception:
                pass

    # Native Store: bubble_new
    if not callable(getattr(store, "bubble_new", None)):
        raise MediaError("Store nie udostępnia bubble_new / get_bubble.")
    b = store.bubble_new(label=label)
    if as_root and callable(getattr(store, "set_root", None)):
        store.set_root(b)
    return b


def sync_bubble_record(store: Any, bubble: Any) -> Optional[str]:
    """
    Zapisz stan bąbla jako atom S=__bubble__ (żeby save_documents przeniósł bindings).
    Zwraca id atomu rekordu lub None.
    """
    label = _bubble_label(bubble)
    bindings = dict(getattr(bubble, "bindings", {}) or {})
    # szukaj istniejącego rekordu
    rec_id = None
    for atom in _iter_atoms_safe(store):
        if getattr(atom, "S", "") != BUBBLE_S:
            continue
        if str(getattr(atom, "E", "")) == label or (
            isinstance(atom.metadata.get("v"), dict)
            and atom.metadata["v"].get("label") == label
        ):
            rec_id = atom.id
            atom.metadata["v"] = {"label": label, "bindings": bindings}
            atom.E = label
            return rec_id

    if not callable(getattr(store, "atom_new", None)):
        return None
    rec = store.atom_new(S=BUBBLE_S, E=label, value={"label": label, "bindings": bindings})
    # atom_new może włożyć value do metadata['v']
    if not isinstance(rec.metadata.get("v"), dict):
        rec.metadata["v"] = {"label": label, "bindings": bindings}
    else:
        rec.metadata["v"] = {"label": label, "bindings": bindings}
    return rec.id


def restore_bubbles(store: Any, *, as_root: bool = True) -> dict[str, Any]:
    """
    Po load_documents: odtwórz Bubble z atomów __bubble__.
    Zwraca {label: bubble}.
    """
    out: dict[str, Any] = {}
    for atom in _iter_atoms_safe(store):
        if getattr(atom, "S", "") != BUBBLE_S:
            continue
        v = atom.metadata.get("v")
        if isinstance(v, dict):
            label = str(v.get("label") or atom.E or atom.id)
            bindings = v.get("bindings") or {}
        else:
            label = str(atom.E or atom.id)
            bindings = {}
        if not isinstance(bindings, dict):
            bindings = {}
        b = ensure_bubble(store, label, as_root=as_root and not out)
        # ustaw bindings na id (stringi)
        for name, aid in bindings.items():
            if not name or not aid:
                continue
            try:
                # prefer bind(name, atom) gdy atom istnieje
                target = store.get_atom(str(aid)) if callable(getattr(store, "get_atom", None)) else None
                if target is not None and callable(getattr(b, "bind", None)):
                    b.bind(str(name), target)
                else:
                    # surowy zapis id
                    if not hasattr(b, "bindings") or b.bindings is None:
                        continue
                    b.bindings[str(name)] = str(aid)
            except Exception:
                if hasattr(b, "bindings"):
                    b.bindings[str(name)] = str(aid)
        out[label] = b
    return out


def _iter_atoms_safe(store: Any):
    if callable(getattr(store, "atoms", None)):
        return list(store.atoms())
    return []


def is_stream_atom(atom: Any) -> bool:
    """True gdy head A_STREAM (segmenty w metadata / v)."""
    if atom is None:
        return False
    if atom.metadata.get("_stream"):
        return True
    v = atom.metadata.get("v")
    if isinstance(v, dict) and v.get("kind") in ("media_stream", "media_stream_head"):
        return True
    if isinstance(v, dict) and v.get("segments"):
        return True
    return False


def _segment_ids(atom: Any) -> list[str]:
    v = atom.metadata.get("v")
    if not isinstance(v, dict):
        return []
    segs = v.get("segments") or []
    return [str(s) for s in segs if s]


def attach_bytes(
    store: Any,
    bubble_or_label: Any,
    binding: str,
    data: Union[bytes, bytearray, memoryview],
    *,
    mime: str = "application/octet-stream",
    T: float = 50.0,
    as_root: bool = True,
    sync_bubble: bool = True,
    warn_over: int = DEFAULT_WARN_BYTES,
    stream_threshold: int = DEFAULT_STREAM_THRESHOLD,
    segment_size: int = DEFAULT_SEGMENT_SIZE,
    force_stream: bool = False,
) -> MediaRef:
    """
    Utwórz atom mediów i podepnij pod bąbel.

    Faza 4: gdy ``len(data) > stream_threshold`` lub ``force_stream``,
    head bez pełnego ``data`` + atomy ``media_seg``.
    """
    raw = _as_bytes(data)
    if not raw:
        raise MediaError("Puste dane — odmowa attach.")
    binding = (binding or "").strip()
    if not binding:
        raise MediaError("Wymagana nazwa bindingu (np. 'portret').")
    mime = (mime or "application/octet-stream").strip() or "application/octet-stream"

    thr = stream_threshold_effective(stream_threshold)
    seg_sz = segment_size_effective(segment_size)
    use_stream = bool(force_stream) or (thr >= 0 and len(raw) > thr)
    if use_stream:
        return _attach_stream_bytes(
            store,
            bubble_or_label,
            binding,
            raw,
            mime=mime,
            T=T,
            as_root=as_root,
            sync_bubble=sync_bubble,
            segment_size=seg_sz,
            warn_over=warn_over,
        )

    bubble = ensure_bubble(store, bubble_or_label, as_root=as_root)
    label = _bubble_label(bubble)

    if not callable(getattr(store, "atom_new", None)):
        raise MediaError("Store nie udostępnia atom_new.")

    # E = krótki opis (binding@bubble); S = media
    atom = store.atom_new(
        S=MEDIA_S,
        E=f"{binding}@{label}"[:120],
        value=None,
        T=float(T),
    )
    atom.metadata["data"] = raw
    atom.metadata["mime"] = mime
    atom.metadata["_cas"] = _cas12(raw)
    atom.metadata["v"] = {
        "kind": "media",
        "binding": binding,
        "bubble": label,
        "size": len(raw),
        "mime": mime,
        "atype": "A_RAW",
    }

    # bind
    if callable(getattr(bubble, "bind", None)):
        bubble.bind(binding, atom)
    else:
        bubble.bindings[binding] = atom.id

    if sync_bubble:
        sync_bubble_record(store, bubble)

    if warn_over and len(raw) > warn_over:
        print(
            f"[karmazyn_media] ostrzeżenie: atom {atom.id} ma {len(raw)} B "
            f"(>{warn_over}); rozważ force_stream / stream_threshold",
            file=sys.stderr,
        )

    return MediaRef(
        atom_id=str(atom.id),
        binding=binding,
        mime=mime,
        size=len(raw),
        bubble_label=label,
        cas12=str(atom.metadata.get("_cas") or _cas12(raw)),
    )


def _attach_stream_bytes(
    store: Any,
    bubble_or_label: Any,
    binding: str,
    raw: bytes,
    *,
    mime: str,
    T: float,
    as_root: bool,
    sync_bubble: bool,
    segment_size: int,
    warn_over: int,
) -> MediaRef:
    """Head A_STREAM + N× media_seg (chunki). Head nie trzyma pełnego payloadu."""
    if segment_size <= 0:
        raise MediaError("segment_size musi być > 0")
    bubble = ensure_bubble(store, bubble_or_label, as_root=as_root)
    label = _bubble_label(bubble)
    if not callable(getattr(store, "atom_new", None)):
        raise MediaError("Store nie udostępnia atom_new.")

    total = len(raw)
    cas_full = hashlib.sha256(raw).hexdigest()
    cas12 = hashlib.sha256(raw).digest()[:12].hex()

    head = store.atom_new(
        S=MEDIA_S,
        E=f"{binding}@{label}"[:120],
        value=None,
        T=float(T),
    )
    head_id = str(head.id)
    seg_ids: list[str] = []
    offset = 0
    idx = 0
    while offset < total:
        chunk = raw[offset : offset + segment_size]
        seg = store.atom_new(
            S=MEDIA_SEG_S,
            E=f"seg{idx}:{binding}@{label}"[:120],
            value=None,
            T=max(10.0, float(T) - 5.0),
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

    # head: bez monolit data
    head.metadata["data"] = b""
    head.metadata["mime"] = mime
    head.metadata["_cas"] = cas12
    head.metadata["_stream"] = True
    head.metadata["v"] = {
        "kind": "media_stream",
        "atype": "A_STREAM",
        "binding": binding,
        "bubble": label,
        "size": total,
        "mime": mime,
        "segment_size": int(segment_size),
        "segments": seg_ids,
        "n_segments": len(seg_ids),
        "sha256": cas_full,
    }

    if callable(getattr(bubble, "bind", None)):
        bubble.bind(binding, head)
    else:
        bubble.bindings[binding] = head.id

    if sync_bubble:
        sync_bubble_record(store, bubble)

    if warn_over and total > warn_over:
        print(
            f"[karmazyn_media] stream {head_id}: {total} B w {len(seg_ids)} seg "
            f"(chunk≈{segment_size})",
            file=sys.stderr,
        )

    return MediaRef(
        atom_id=head_id,
        binding=binding,
        mime=mime,
        size=total,
        bubble_label=label,
        cas12=cas12,
    )


def attach_file(
    store: Any,
    bubble_or_label: Any,
    binding: str,
    path: str | Path,
    *,
    mime: Optional[str] = None,
    T: Optional[float] = None,
    as_root: bool = True,
    sync_bubble: bool = True,
    stream_threshold: int = DEFAULT_STREAM_THRESHOLD,
    segment_size: int = DEFAULT_SEGMENT_SIZE,
    force_stream: bool = False,
) -> MediaRef:
    """Wczytaj plik z dysku; duże pliki → stream z dysku (Faza 4)."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise MediaError(f"Brak pliku: {p}")
    use_mime = mime or _guess_mime(p)
    size = p.stat().st_size
    if T is None:
        import math

        kb = max(1, size / 1024)
        T = max(20.0, 65.0 - math.log10(kb) * 10)

    thr = stream_threshold_effective(stream_threshold)
    seg_sz = segment_size_effective(segment_size)
    use_stream = bool(force_stream) or (thr >= 0 and size > thr)
    if use_stream:
        return _attach_stream_file(
            store,
            bubble_or_label,
            binding,
            p,
            mime=use_mime,
            T=float(T),
            as_root=as_root,
            sync_bubble=sync_bubble,
            segment_size=seg_sz,
            size=size,
        )

    data = p.read_bytes()
    return attach_bytes(
        store,
        bubble_or_label,
        binding,
        data,
        mime=use_mime,
        T=float(T),
        as_root=as_root,
        sync_bubble=sync_bubble,
        stream_threshold=stream_threshold,
        segment_size=segment_size,
        force_stream=False,
    )


def _attach_stream_file(
    store: Any,
    bubble_or_label: Any,
    binding: str,
    path: Path,
    *,
    mime: str,
    T: float,
    as_root: bool,
    sync_bubble: bool,
    segment_size: int,
    size: int,
) -> MediaRef:
    """Stream z pliku — nie ładuje całości do RAM naraz (hash przy zapisie)."""
    if segment_size <= 0:
        raise MediaError("segment_size musi być > 0")
    binding = (binding or "").strip()
    if not binding:
        raise MediaError("Wymagana nazwa bindingu (np. 'portret').")
    bubble = ensure_bubble(store, bubble_or_label, as_root=as_root)
    label = _bubble_label(bubble)
    if not callable(getattr(store, "atom_new", None)):
        raise MediaError("Store nie udostępnia atom_new.")

    head = store.atom_new(
        S=MEDIA_S,
        E=f"{binding}@{label}"[:120],
        value=None,
        T=float(T),
    )
    head_id = str(head.id)
    seg_ids: list[str] = []
    h = hashlib.sha256()
    idx = 0
    offset = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(segment_size)
            if not chunk:
                break
            h.update(chunk)
            seg = store.atom_new(
                S=MEDIA_SEG_S,
                E=f"seg{idx}:{binding}@{label}"[:120],
                value=None,
                T=max(10.0, float(T) - 5.0),
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

    if offset == 0:
        raise MediaError("Pusty plik — odmowa attach stream.")
    if size and offset != size:
        # rare race: file changed mid-read
        size = offset

    cas_full = h.hexdigest()
    cas12 = h.digest()[:12].hex()

    head.metadata["data"] = b""
    head.metadata["mime"] = mime
    head.metadata["_cas"] = cas12
    head.metadata["_stream"] = True
    head.metadata["v"] = {
        "kind": "media_stream",
        "atype": "A_STREAM",
        "binding": binding,
        "bubble": label,
        "size": offset,
        "mime": mime,
        "segment_size": int(segment_size),
        "segments": seg_ids,
        "n_segments": len(seg_ids),
        "sha256": cas_full,
    }

    if callable(getattr(bubble, "bind", None)):
        bubble.bind(binding, head)
    else:
        bubble.bindings[binding] = head.id
    if sync_bubble:
        sync_bubble_record(store, bubble)

    return MediaRef(
        atom_id=head_id,
        binding=binding,
        mime=mime,
        size=offset,
        bubble_label=label,
        cas12=cas12,
    )


def iter_bytes(store: Any, atom_id: str, *, chunk_size: int = 0) -> Iterator[bytes]:
    """Yield payload w kawałkach (stream head → segmenty; A_RAW → jeden/blok)."""
    if not callable(getattr(store, "get_atom", None)):
        raise MediaError("Store nie udostępnia get_atom.")
    atom = store.get_atom(str(atom_id))
    if atom is None:
        raise MediaError(f"Brak atomu „{atom_id}”.")

    if is_stream_atom(atom):
        for sid in _segment_ids(atom):
            seg = store.get_atom(sid)
            if seg is None:
                raise MediaError(f"Brak segmentu „{sid}” (stream {atom_id}).")
            data = seg.metadata.get("data")
            if not isinstance(data, (bytes, bytearray)):
                raise MediaError(f"Segment „{sid}” bez data.")
            raw = bytes(data)
            if chunk_size and chunk_size > 0 and len(raw) > chunk_size:
                for i in range(0, len(raw), chunk_size):
                    yield raw[i : i + chunk_size]
            else:
                yield raw
        return

    data = atom.metadata.get("data")
    if not isinstance(data, (bytes, bytearray)):
        raise MediaError(f"Atom „{atom_id}” nie ma payloadu metadata['data'].")
    raw = bytes(data)
    if not chunk_size or chunk_size <= 0:
        yield raw
        return
    for i in range(0, len(raw), chunk_size):
        yield raw[i : i + chunk_size]


def get_bytes(store: Any, atom_id: str) -> tuple[bytes, str]:
    """Zwróć (data, mime) — reassemble stream jeśli trzeba."""
    if not callable(getattr(store, "get_atom", None)):
        raise MediaError("Store nie udostępnia get_atom.")
    atom = store.get_atom(str(atom_id))
    if atom is None:
        raise MediaError(f"Brak atomu „{atom_id}”.")

    mime = str(atom.metadata.get("mime") or "application/octet-stream")
    v = atom.metadata.get("v")
    if mime == "application/octet-stream" and isinstance(v, dict) and v.get("mime"):
        mime = str(v["mime"])

    if is_stream_atom(atom):
        parts = list(iter_bytes(store, atom_id))
        return b"".join(parts), mime

    data = atom.metadata.get("data")
    if not isinstance(data, (bytes, bytearray)):
        raise MediaError(f"Atom „{atom_id}” nie ma payloadu metadata['data'].")
    return bytes(data), mime


def export_to_path(store: Any, atom_id: str, path: str | Path) -> int:
    """Zapisz payload atomu do pliku. Zwraca liczbę bajtów."""
    data, _mime = get_bytes(store, atom_id)
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return len(data)


def list_bindings(store: Any, bubble_or_label: Any) -> list[MediaRef]:
    """Lista bindingów bąbla, które wskazują na atomy z data/mime."""
    bubble = ensure_bubble(store, bubble_or_label, as_root=False)
    label = _bubble_label(bubble)
    out: list[MediaRef] = []
    bindings = dict(getattr(bubble, "bindings", {}) or {})
    for name, target in bindings.items():
        aid = getattr(target, "id", None) or target
        aid = str(aid)
        atom = store.get_atom(aid) if callable(getattr(store, "get_atom", None)) else None
        if atom is None:
            continue
        mime = str(atom.metadata.get("mime") or "application/octet-stream")
        v = atom.metadata.get("v")
        if isinstance(v, dict) and v.get("mime"):
            mime = str(v["mime"])
        # Faza 4: stream head bez monolit data
        if is_stream_atom(atom):
            size = int(v.get("size") or 0) if isinstance(v, dict) else 0
            cas = str(atom.metadata.get("_cas") or "")
            out.append(
                MediaRef(
                    atom_id=aid,
                    binding=str(name),
                    mime=mime,
                    size=size,
                    bubble_label=label,
                    cas12=cas,
                )
            )
            continue
        data = atom.metadata.get("data")
        if not isinstance(data, (bytes, bytearray)):
            continue
        out.append(
            MediaRef(
                atom_id=aid,
                binding=str(name),
                mime=mime,
                size=len(data),
                bubble_label=label,
                cas12=str(atom.metadata.get("_cas") or _cas12(bytes(data))),
            )
        )
    return out


def save_store(store: Any, path: str | Path, **kwargs) -> int:
    """Zapisz atomy mediów (+ bąble __bubble__) do .kafd."""
    from karmazyn_store import save_documents

    return save_documents(store, str(path), **kwargs)


def load_store(store: Any, path: str | Path, *, restore: bool = True, **kwargs) -> int:
    """Wczytaj .kafd; domyślnie restore_bubbles()."""
    from karmazyn_store import load_documents

    n = load_documents(store, str(path), **kwargs)
    if restore:
        restore_bubbles(store)
    return n


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Faza 2: podgląd lokalny / pipe ────────────────────────────────────────────

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "application/pdf": ".pdf",
    "application/octet-stream": ".bin",
    "text/plain": ".txt",
}


def _ext_for_mime(mime: str) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    if mime in _MIME_EXT:
        return _MIME_EXT[mime]
    if "/" in mime:
        sub = mime.split("/", 1)[1].split("+")[0]
        if sub and sub.isalnum() and len(sub) <= 8:
            return f".{sub}"
    return ".bin"


def pipe_to(
    store: Any,
    atom_id: str,
    sink: BinaryIO,
    *,
    chunk_size: int = 65536,
) -> int:
    """
    Przelej payload atomu do obiektu z .write(bytes) (stdout, plik, socket).
    Stream: leci segmentami (bez pełnego reassemble w RAM jeśli sink konsumuje).
    Zwraca liczbę wysłanych bajtów.
    """
    sent = 0
    # chunk_size tu: max podział przy A_RAW; segmenty stream i tak lecą po seg
    for piece in iter_bytes(store, atom_id, chunk_size=chunk_size if chunk_size > 0 else 0):
        sink.write(piece)
        sent += len(piece)
    if hasattr(sink, "flush"):
        try:
            sink.flush()
        except Exception:
            pass
    return sent


def materialize_temp(
    store: Any,
    atom_id: str,
    *,
    suffix: Optional[str] = None,
    directory: Optional[str | Path] = None,
    prefix: str = "karm_media_",
) -> Path:
    """
    Zapisz atom do pliku tymczasowego (z sensownym rozszerzeniem MIME).
    Wywołujący jest właścicielem pliku (może skasować).
    """
    data, mime = get_bytes(store, atom_id)
    ext = suffix if suffix is not None else _ext_for_mime(mime)
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    dir_arg = str(directory) if directory else None
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=ext or ".bin", dir=dir_arg)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name)


def open_with_system(
    store: Any,
    atom_id: str,
    *,
    keep_temp: bool = False,
    path: Optional[str | Path] = None,
) -> Path:
    """
    Otwórz medium domyślną aplikacją systemu (bez HTTP).

    Windows: os.startfile · macOS: open · Linux: xdg-open
    Zwraca ścieżkę pliku (temp lub wskazany path).
    Best-effort: OSError / FileNotFoundError → MediaError.
    """
    if path is not None:
        out = Path(path).expanduser()
        export_to_path(store, atom_id, out)
    else:
        out = materialize_temp(store, atom_id)

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(out))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(out)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Linux / BSD
            opener = shutil.which("xdg-open") or shutil.which("gio")
            if not opener:
                raise MediaError(
                    "Brak xdg-open — zapisz plik (export_to_path) i otwórz ręcznie."
                )
            cmd = [opener, str(out)] if "xdg-open" in opener else [opener, "open", str(out)]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except MediaError:
        if not keep_temp and path is None:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except OSError as e:
        if not keep_temp and path is None:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
        raise MediaError(f"Nie otwarto podglądu systemowego: {e}") from e

    return out


def try_external_player(
    store: Any,
    atom_id: str,
    *,
    players: Optional[list[str]] = None,
    path: Optional[str | Path] = None,
    keep_temp: bool = False,
) -> tuple[bool, str]:
    """
    Best-effort odtwarzacz: mpv / ffplay / vlc / …
    Zwraca (ok, komunikat). Nie rzuca przy braku playera.
    """
    order = players or ["mpv", "ffplay", "vlc", "mplayer"]
    if path is not None:
        out = Path(path).expanduser()
        export_to_path(store, atom_id, out)
        temp_created = False
    else:
        out = materialize_temp(store, atom_id)
        temp_created = True

    for name in order:
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            if name == "ffplay":
                cmd = [exe, "-autoexit", "-loglevel", "error", str(out)]
            else:
                cmd = [exe, str(out)]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, f"uruchomiono {name}: {out}"
        except OSError:
            continue

    if temp_created and not keep_temp:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
    return False, (
        f"Brak zewnętrznego playera ({', '.join(order)}). "
        f"Użyj open_with_system lub export_to_path → {out if not temp_created else 'plik temp'}."
    )


def open_media(
    store: Any,
    atom_id: str,
    *,
    prefer_player: bool = False,
    keep_temp: bool = False,
) -> tuple[bool, str, Optional[Path]]:
    """
    Uniwersalny podgląd lokalny:
      1) opcjonalnie zewnętrzny player (audio/video)
      2) systemowy handler (obrazy, PDF, …)

    Zwraca (ok, msg, path_or_None).
    """
    _data, mime = get_bytes(store, atom_id)
    major = (mime or "").split("/", 1)[0].lower()

    if prefer_player or major in ("audio", "video"):
        ok, msg = try_external_player(store, atom_id, keep_temp=keep_temp)
        if ok:
            return True, msg, None
        # fallback system
    try:
        p = open_with_system(store, atom_id, keep_temp=keep_temp)
        return True, f"otwarto systemowo: {p}", p
    except MediaError as e:
        return False, str(e), None


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_load(kafd_path: str):
    from karmazyn_kernel import Store

    store = Store(thermal=True)
    n = load_store(store, kafd_path, restore=True)
    return store, n


def main(argv: Optional[list[str]] = None) -> int:
    """
    python -m karmazyn_media <cmd> …

    extract <kafd> <atom_id> <out>
    list    <kafd> [bubble]
    open    <kafd> <atom_id>
    pipe    <kafd> <atom_id>          # stdout.buffer
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(
            "karmazyn_media — lokalne media (Faza 0/2)\n"
            "  extract <kafd> <atom_id> <out>   zapisz payload do pliku\n"
            "  list    <kafd> [bubble]          lista atomów media / bindingów\n"
            "  open    <kafd> <atom_id>         podgląd systemowy / player\n"
            "  pipe    <kafd> <atom_id>         bajty na stdout (binarnie)\n",
            file=sys.stderr,
        )
        return 0

    cmd = args[0].lower()
    try:
        if cmd == "extract":
            if len(args) < 4:
                print("użycie: extract <kafd> <atom_id> <out>", file=sys.stderr)
                return 2
            store, _n = _cli_load(args[1])
            n = export_to_path(store, args[2], args[3])
            print(f"OK {n} B → {args[3]}")
            return 0

        if cmd == "list":
            if len(args) < 2:
                print("użycie: list <kafd> [bubble]", file=sys.stderr)
                return 2
            store, n = _cli_load(args[1])
            print(f"# atoms loaded: {n}")
            if len(args) >= 3:
                for ref in list_bindings(store, args[2]):
                    print(
                        f"{ref.binding} | {ref.atom_id} | {ref.mime} | {ref.size}"
                    )
            else:
                for atom in store.atoms():
                    if getattr(atom, "S", "") != MEDIA_S:
                        continue
                    data = atom.metadata.get("data")
                    size = len(data) if isinstance(data, (bytes, bytearray)) else 0
                    mime = atom.metadata.get("mime", "")
                    print(f"{atom.id} | {atom.E} | {mime} | {size}")
            return 0

        if cmd == "open":
            if len(args) < 3:
                print("użycie: open <kafd> <atom_id>", file=sys.stderr)
                return 2
            store, _n = _cli_load(args[1])
            ok, msg, _p = open_media(store, args[2], keep_temp=True)
            print(msg)
            return 0 if ok else 1

        if cmd == "pipe":
            if len(args) < 3:
                print("użycie: pipe <kafd> <atom_id>", file=sys.stderr)
                return 2
            store, _n = _cli_load(args[1])
            # binarnie na stdout
            if hasattr(sys.stdout, "buffer"):
                pipe_to(store, args[2], sys.stdout.buffer)
            else:
                data, _ = get_bytes(store, args[2])
                sys.stdout.write(data)  # type: ignore[arg-type]
            return 0

        print(f"nieznana komenda: {cmd}", file=sys.stderr)
        return 2
    except MediaError as e:
        print(f"błąd: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"błąd: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
