"""
karmazyn_media.py — lokalne API mediów (Faza 0)
================================================
Plik / bajty → atom w Store (+ opcjonalny bind w bąblu) → KAFD → z powrotem.

Zasady (PLAN_MULTIMEDIA_WDROZENIE.md):
  • media = atomy z metadata[\"data\"] + metadata[\"mime\"]
  • S = \"media\" (persystencja przez karmazyn_store.save_documents)
  • sieć / KAFS = Faza 3+ (tu tylko lokalnie)
  • zero HTTP, zero base64 w KarminQL

Publiczna powierzchnia:
  attach_bytes, attach_file, get_bytes, export_to_path
  list_bindings, sync_bubble_record, restore_bubbles
  save_store / load_store  (cienkie wrappery na save/load_documents)
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

# Kind zapisany w KAFD (karmazyn_store.DOC_KINDS musi zawierać "media")
MEDIA_S = "media"
BUBBLE_S = "__bubble__"

# Limit ostrzeżenia (duże pliki OK lokalnie; sieć to Faza 3)
DEFAULT_WARN_BYTES = 16 * 1024 * 1024  # 16 MiB


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
) -> MediaRef:
    """
    Utwórz atom mediów i podepnij pod bąbel.

    Returns:
        MediaRef z atom_id i metadanymi.
    """
    raw = _as_bytes(data)
    if not raw:
        raise MediaError("Puste dane — odmowa attach.")
    binding = (binding or "").strip()
    if not binding:
        raise MediaError("Wymagana nazwa bindingu (np. 'portret').")
    mime = (mime or "application/octet-stream").strip() or "application/octet-stream"

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
    }

    # bind
    if callable(getattr(bubble, "bind", None)):
        bubble.bind(binding, atom)
    else:
        bubble.bindings[binding] = atom.id

    if sync_bubble:
        sync_bubble_record(store, bubble)

    if warn_over and len(raw) > warn_over:
        # nie rzucamy — lokalnie dozwolone; log przez stderr opcjonalnie
        import sys

        print(
            f"[karmazyn_media] ostrzeżenie: atom {atom.id} ma {len(raw)} B "
            f"(>{warn_over}); sieć KAFS = Faza 3+",
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
) -> MediaRef:
    """Wczytaj plik z dysku i attach_bytes."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise MediaError(f"Brak pliku: {p}")
    data = p.read_bytes()
    use_mime = mime or _guess_mime(p)
    # temperatura: mniejsze pliki „cieplejsze” (jak KAFDAtom.from_file)
    if T is None:
        import math

        kb = max(1, len(data) / 1024)
        T = max(20.0, 65.0 - math.log10(kb) * 10)
    return attach_bytes(
        store,
        bubble_or_label,
        binding,
        data,
        mime=use_mime,
        T=float(T),
        as_root=as_root,
        sync_bubble=sync_bubble,
    )


def get_bytes(store: Any, atom_id: str) -> tuple[bytes, str]:
    """Zwróć (data, mime) atomu mediów (lub dowolnego z metadata data)."""
    if not callable(getattr(store, "get_atom", None)):
        raise MediaError("Store nie udostępnia get_atom.")
    atom = store.get_atom(str(atom_id))
    if atom is None:
        raise MediaError(f"Brak atomu „{atom_id}”.")
    data = atom.metadata.get("data")
    if not isinstance(data, (bytes, bytearray)):
        raise MediaError(f"Atom „{atom_id}” nie ma payloadu metadata['data'].")
    mime = str(atom.metadata.get("mime") or "application/octet-stream")
    # fallback z v
    v = atom.metadata.get("v")
    if mime == "application/octet-stream" and isinstance(v, dict) and v.get("mime"):
        mime = str(v["mime"])
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
        data = atom.metadata.get("data")
        if not isinstance(data, (bytes, bytearray)):
            continue
        mime = str(atom.metadata.get("mime") or "application/octet-stream")
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
