# -*- coding: utf-8 -*-
"""
karmazyn_media_preview.py — podgląd mediów (wzorzec Luneta image loader)
========================================================================
Jak Luneta2/luneta_image_loader + luneta_images:

  L1  MediaPreviewCache  — RAM (atom_id / cas → PNG bytes + meta)
  decode                 — PIL → PNG (thumb/full), bez HTTP
  UI                     — tkinter PhotoImage z PNG (bez pygame/SDL w lore)
  audio/video            — try_external_player / open_with_system (karmazyn_media)

Nie jest przeglądarką — to cienki odtwarzacz/podgląd atomów mediów w grafie.
"""

from __future__ import annotations

import hashlib
import io
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from karmazyn_media import (
    MediaError,
    get_bytes,
    is_stream_atom,
    materialize_temp,
    open_media,
    open_with_system,
    try_external_player,
)

try:
    from PIL import Image

    _HAS_PIL = True
except Exception:
    Image = None  # type: ignore
    _HAS_PIL = False

# Limity jak Luneta (ochrona)
MAX_EDGE = 1200
THUMB_EDGE = 240
MAX_CACHE = 64


@dataclass
class PreviewImage:
    atom_id: str
    mime: str
    width: int
    height: int
    png_bytes: bytes
    ok: bool = True
    error: str = ""
    cas12: str = ""
    tier: str = "full"  # thumb | full


class MediaPreviewCache:
    """L1 RAM — wzorzec ImageCache z Lunety (OrderedDict + limit)."""

    def __init__(self, max_items: int = MAX_CACHE):
        self._map: "OrderedDict[str, PreviewImage]" = OrderedDict()
        self._lock = threading.Lock()
        self.max_items = max(8, int(max_items))

    def get(self, key: str) -> Optional[PreviewImage]:
        with self._lock:
            img = self._map.get(key)
            if img is not None:
                self._map.move_to_end(key)
            return img

    def put(self, key: str, img: PreviewImage) -> None:
        with self._lock:
            self._map[key] = img
            self._map.move_to_end(key)
            while len(self._map) > self.max_items:
                self._map.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._map.clear()


_GLOBAL_CACHE = MediaPreviewCache()


def get_preview_cache() -> MediaPreviewCache:
    return _GLOBAL_CACHE


def decode_image_bytes(
    data: bytes,
    *,
    max_edge: int = MAX_EDGE,
) -> Tuple[Optional[bytes], int, int, str]:
    """
    Raster → PNG (jak Luneta _decode_raster).
    Zwraca (png_bytes|None, w, h, error).
    """
    if not data:
        return None, 0, 0, "puste dane"
    if not _HAS_PIL or Image is None:
        return None, 0, 0, "brak Pillow (pip install pillow)"
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = im.convert("RGBA")
        w, h = im.size
        sc = min(1.0, max_edge / max(w, h, 1))
        if sc < 1.0:
            w, h = max(1, int(w * sc)), max(1, int(h * sc))
            filt = (
                Image.Resampling.BILINEAR
                if max_edge <= THUMB_EDGE
                else Image.Resampling.LANCZOS
            )
            im = im.resize((w, h), filt)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), w, h, ""
    except Exception as e:
        return None, 0, 0, str(e)[:120]


def load_preview_image(
    store: Any,
    atom_id: str,
    *,
    thumb: bool = False,
    cache: Optional[MediaPreviewCache] = None,
) -> PreviewImage:
    """Załaduj atom mediów → PreviewImage (cache L1)."""
    cache = cache or _GLOBAL_CACHE
    tier = "thumb" if thumb else "full"
    key = f"{atom_id}:{tier}"
    hit = cache.get(key)
    if hit is not None and hit.ok:
        return hit

    try:
        data, mime = get_bytes(store, atom_id)
    except MediaError as e:
        img = PreviewImage(
            atom_id=atom_id, mime="", width=0, height=0,
            png_bytes=b"", ok=False, error=str(e),
        )
        cache.put(key, img)
        return img

    major = (mime or "").split("/", 1)[0].lower()
    cas12 = hashlib.sha256(data).digest()[:12].hex()

    if major != "image":
        img = PreviewImage(
            atom_id=atom_id, mime=mime, width=0, height=0,
            png_bytes=b"", ok=False,
            error=f"nie-obraz ({mime}) — użyj open_preview (player)",
            cas12=cas12, tier=tier,
        )
        cache.put(key, img)
        return img

    edge = THUMB_EDGE if thumb else MAX_EDGE
    png, w, h, err = decode_image_bytes(data, max_edge=edge)
    if not png:
        img = PreviewImage(
            atom_id=atom_id, mime=mime, width=0, height=0,
            png_bytes=b"", ok=False, error=err or "decode fail",
            cas12=cas12, tier=tier,
        )
    else:
        img = PreviewImage(
            atom_id=atom_id, mime=mime, width=w, height=h,
            png_bytes=png, ok=True, cas12=cas12, tier=tier,
        )
    cache.put(key, img)
    return img


def show_image_window(
    png_bytes: bytes,
    *,
    title: str = "Podgląd media",
    parent: Any = None,
) -> bool:
    """
    Okno tkinter z PhotoImage (PNG) — jak paint Lunety, bez SDL.
    Wymaga wątku z Tk (lore-editor ma root).
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return False

    try:
        top = tk.Toplevel(parent) if parent is not None else tk.Tk()
    except Exception:
        return False

    top.title(title)
    top.minsize(200, 150)
    try:
        photo = tk.PhotoImage(data=png_bytes)  # needs base64 for some tk
    except Exception:
        # Tk na Windows często wymaga base64
        import base64

        b64 = base64.b64encode(png_bytes).decode("ascii")
        try:
            photo = tk.PhotoImage(data=b64)
        except Exception:
            # fallback: temp file
            import tempfile
            from pathlib import Path

            fd, name = tempfile.mkstemp(suffix=".png")
            import os

            os.close(fd)
            Path(name).write_bytes(png_bytes)
            try:
                photo = tk.PhotoImage(file=name)
            except Exception:
                top.destroy()
                return False

    lbl = ttk.Label(top, image=photo)
    lbl.image = photo  # keep ref
    lbl.pack(padx=8, pady=8)
    ttk.Button(top, text="Zamknij", command=top.destroy).pack(pady=(0, 8))
    if parent is None:
        top.mainloop()
    return True


def open_preview(
    store: Any,
    atom_id: str,
    *,
    parent: Any = None,
    prefer_external: bool = False,
    thumb_first: bool = True,
    use_canvas: bool = True,
) -> Tuple[bool, str]:
    """
    Uniwersalny podgląd atomu mediów:

    - domyślnie **płótno atomów** (PNG/GIF/klatki wideo, dirty paint) — jak Luneta
    - prefer_external: mpv/ffplay
    - fallback: system open
    """
    try:
        data, mime = get_bytes(store, atom_id)
    except MediaError as e:
        return False, str(e)

    major = (mime or "").split("/", 1)[0].lower()
    atom = store.get_atom(str(atom_id)) if callable(getattr(store, "get_atom", None)) else None
    stream_note = ""
    if atom is not None and is_stream_atom(atom):
        stream_note = " [A_STREAM]"

    # Płótno: PNG + GIF + wideo jako klatki (tanie: tylko dirty/hot)
    if use_canvas and not prefer_external and major in ("image", "video"):
        try:
            from karmazyn_media_canvas import open_atom_canvas_window

            if open_atom_canvas_window(
                store,
                atom_id,
                parent=parent,
                title=f"Atom {atom_id}{stream_note}",
            ):
                return True, f"płótno atomów{stream_note}"
        except Exception as e:
            canvas_err = str(e)[:80]
        else:
            canvas_err = ""
    else:
        canvas_err = ""

    if major == "image" and not prefer_external:
        prev = load_preview_image(store, atom_id, thumb=thumb_first)
        if prev.ok and prev.png_bytes:
            if show_image_window(
                prev.png_bytes,
                title=f"Media {atom_id}{stream_note} ({prev.width}×{prev.height})",
                parent=parent,
            ):
                return True, f"podgląd Tk {prev.width}×{prev.height}{stream_note}"
        try:
            p = open_with_system(store, atom_id, keep_temp=False)
            return True, f"system: {p}{stream_note}"
        except MediaError as e:
            return False, prev.error or canvas_err or str(e)

    if major in ("audio", "video") or prefer_external:
        ok, msg = try_external_player(store, atom_id, keep_temp=False)
        if ok:
            return True, msg + stream_note
        ok2, msg2, _ = open_media(store, atom_id, prefer_player=False)
        return ok2, (msg2 if ok2 else f"{msg}; {msg2}; {canvas_err}") + stream_note

    ok, msg, _ = open_media(store, atom_id)
    return ok, msg + stream_note


def build_media_index(store: Any) -> list:
    """
    Faza 6: lekki indeks mediów świata (bez payloadów).
    [{id, mime, size, cas12, stream, n_segments, binding, bubble}]
    """
    out: list = []
    atoms_fn = getattr(store, "atoms", None)
    if not callable(atoms_fn):
        return out
    for atom in list(atoms_fn()):
        if getattr(atom, "S", "") != "media":
            continue
        # pomiń segmenty (S=media_seg)
        v = atom.metadata.get("v") if isinstance(atom.metadata.get("v"), dict) else {}
        if v.get("kind") == "media_segment":
            continue
        mime = str(atom.metadata.get("mime") or v.get("mime") or "application/octet-stream")
        stream = bool(atom.metadata.get("_stream") or v.get("kind") == "media_stream")
        if stream:
            size = int(v.get("size") or 0)
            n_seg = int(v.get("n_segments") or len(v.get("segments") or []))
            cas12 = str(atom.metadata.get("_cas") or "")
        else:
            data = atom.metadata.get("data")
            if not isinstance(data, (bytes, bytearray)):
                continue
            size = len(data)
            n_seg = 0
            cas12 = str(atom.metadata.get("_cas") or hashlib.sha256(bytes(data)).digest()[:12].hex())
        out.append({
            "id": str(atom.id),
            "mime": mime,
            "size": size,
            "cas12": cas12,
            "stream": stream,
            "n_segments": n_seg,
            "binding": str(v.get("binding") or ""),
            "bubble": str(v.get("bubble") or ""),
            "sha256": str(v.get("sha256") or ""),
        })
    out.sort(key=lambda x: x["id"])
    return out
