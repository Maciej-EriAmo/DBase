"""
karmazyn_media.py — lokalne API mediów (Faza 0 + Faza 2 podgląd)
================================================================
Plik / bajty → atom w Store (+ opcjonalny bind w bąblu) → KAFD → z powrotem.

Zasady (PLAN_MULTIMEDIA_WDROZENIE.md):
  • media = atomy z metadata["data"] + metadata["mime"]
  • S = "media" (persystencja przez karmazyn_store.save_documents)
  • sieć / KAFS = Faza 3+ (tu tylko lokalnie)
  • zero HTTP, zero base64 w KarminQL

Publiczna powierzchnia:
  attach_bytes, attach_file, get_bytes, export_to_path
  list_bindings, sync_bubble_record, restore_bubbles
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
from typing import Any, BinaryIO, Optional, Union

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
    Zwraca liczbę wysłanych bajtów.
    """
    data, _mime = get_bytes(store, atom_id)
    if chunk_size <= 0:
        chunk_size = len(data) or 1
    sent = 0
    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + chunk_size]
        sink.write(chunk)
        sent += len(chunk)
        offset += chunk_size
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
