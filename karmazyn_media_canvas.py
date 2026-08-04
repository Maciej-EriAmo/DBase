# -*- coding: utf-8 -*-
"""
karmazyn_media_canvas.py — płótno atomów mediów (model Luneta)
==============================================================
Odwzorowanie jak Luneta / ThermalGifPump:

  • PNG / GIF / wideo = atomy (lub sekwencja klatek PNG w RAM)
  • Animacja TYLKO gdy atom jest gorący (widoczny) — T ≥ FREEZE_T
  • pump() przesuwa klatkę i zwraca **dirty** id — maluj tylko to, co się zmieniło
  • note_visible() = ciepło z widoczności (nie z samego pump — bez samonapędzania)

Wideo:
  • legacy preload: lista PNG (GIF / małe sekwencje)
  • **inkrementalnie** (MP4): IncrementalVideoDecoder — next_frame tylko gdy hot
    (spike pod dekoder substratu: T × reach, bez 180 klatek w RAM)

Bez pygame/SDL — Tk Canvas + PhotoImage z PNG (jak Luneta tk path).
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from karmazyn_media import MediaError, get_bytes, is_stream_atom
from karmazyn_media_incremental import (
    IncrementalVideoDecoder,
    is_video_path,
    open_incremental,
)

try:
    from PIL import Image

    _HAS_PIL = True
except Exception:
    Image = None  # type: ignore
    _HAS_PIL = False

# Jak karmazyn_gif.FREEZE_T — poniżej zamarza
FREEZE_T = 30.0
T_HOT = 80.0
DEFAULT_FPS = 12.0
MAX_VIDEO_FRAMES = 180  # cap RAM (~kilka sekund przy 12 fps)
MAX_EDGE = 960


def _frame_to_png(frame) -> bytes:
    assert Image is not None
    buf = io.BytesIO()
    frame.convert("RGBA").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _resize_rgba(im, max_edge: int):
    w, h = im.size
    if max(w, h) <= max_edge:
        return im
    sc = max_edge / max(w, h)
    return im.resize(
        (max(1, int(w * sc)), max(1, int(h * sc))),
        Image.Resampling.BILINEAR,
    )


def decode_static_png(data: bytes, *, max_edge: int = MAX_EDGE) -> Tuple[List[bytes], List[float], Tuple[int, int]]:
    """Jeden kadr PNG."""
    if not _HAS_PIL or Image is None:
        raise MediaError("Pillow wymagany do płótna atomów (pip install pillow)")
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    im = _resize_rgba(im, max_edge)
    return [_frame_to_png(im)], [1e9], im.size


def decode_gif_frames(data: bytes, *, max_edge: int = MAX_EDGE) -> Tuple[List[bytes], List[float], Tuple[int, int]]:
    """GIF → lista PNG + opóźnienia (s) — jak ThermalGifPump.load_gif."""
    if not _HAS_PIL or Image is None:
        raise MediaError("Pillow wymagany do GIF")
    img = Image.open(io.BytesIO(data))
    pngs: List[bytes] = []
    delays: List[float] = []
    size = (0, 0)
    try:
        while True:
            frame = img.convert("RGBA")
            frame = _resize_rgba(frame, max_edge)
            size = frame.size
            pngs.append(_frame_to_png(frame))
            delays.append(max(0.02, (img.info.get("duration") or 100) / 1000.0))
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    if not pngs:
        raise MediaError("GIF bez klatek")
    return pngs, delays, size


def _decode_video_via_imageio_path(
    path: str,
    *,
    max_edge: int,
    max_frames: int,
    fps: float,
) -> Tuple[List[bytes], List[float], Tuple[int, int]]:
    """MP4/WebM z dysku — imageio+ffmpeg (ścieżka pliku, nie surowe bytes)."""
    import imageio.v3 as iio  # type: ignore

    meta: dict = {}
    try:
        meta = dict(iio.immeta(path) or {})
    except Exception:
        pass
    src_fps = float(meta.get("fps") or fps or DEFAULT_FPS)
    if src_fps <= 0:
        src_fps = DEFAULT_FPS
    # target display fps ≤ DEFAULT_FPS; stride source frames
    target = min(float(fps), src_fps)
    stride = max(1, int(round(src_fps / target)))
    delay = stride / src_fps

    pngs: List[bytes] = []
    delays: List[float] = []
    size = (0, 0)
    for i, arr in enumerate(iio.imiter(path)):
        if i % stride != 0:
            continue
        if len(pngs) >= max_frames:
            break
        im = Image.fromarray(arr).convert("RGBA")
        im = _resize_rgba(im, max_edge)
        size = im.size
        pngs.append(_frame_to_png(im))
        delays.append(delay)
    if not pngs:
        raise MediaError("imageio: 0 klatek")
    return pngs, delays, size


def decode_video_frames(
    data: bytes,
    mime: str = "",
    *,
    max_edge: int = MAX_EDGE,
    max_frames: int = MAX_VIDEO_FRAMES,
    fps: float = DEFAULT_FPS,
    path: Optional[Union[str, Path]] = None,
) -> Tuple[List[bytes], List[float], Tuple[int, int]]:
    """
    Wideo → klatki PNG (tanie wyświetlanie: jedna klatka / tick / atom).

    Kolejność:
      1) Pillow — animowany WebP / APNG / GIF-as-bytes
      2) imageio-ffmpeg — **plik** (path lub temp z bytes) — właściwa ścieżka MP4
      3) fallback: statyczny PNG jeśli to obraz
    """
    import os
    import tempfile

    if not _HAS_PIL or Image is None:
        raise MediaError("Pillow wymagany do klatek wideo")

    errors: List[str] = []

    # 1) animowany webp / apng przez PIL (nie MP4)
    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1:
            pngs, delays = [], []
            size = (0, 0)
            n = min(int(img.n_frames), max_frames)
            for i in range(n):
                img.seek(i)
                frame = _resize_rgba(img.convert("RGBA"), max_edge)
                size = frame.size
                pngs.append(_frame_to_png(frame))
                delays.append(
                    max(0.02, (img.info.get("duration") or int(1000 / fps)) / 1000.0)
                )
            if pngs:
                return pngs, delays, size
    except Exception as e:
        errors.append(f"PIL:{e}")

    # 2) imageio + ffmpeg — wymaga ścieżki pliku
    src_path: Optional[str] = None
    tmp_path: Optional[str] = None
    if path is not None and Path(path).is_file():
        src_path = str(Path(path).resolve())
    else:
        # dopasuj rozszerzenie do mime
        ext = ".mp4"
        m = (mime or "").lower()
        if "webm" in m:
            ext = ".webm"
        elif "quicktime" in m or "mov" in m:
            ext = ".mov"
        elif "matroska" in m or "mkv" in m:
            ext = ".mkv"
        elif "avi" in m:
            ext = ".avi"
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="karm_vid_")
            os.write(fd, data)
            os.close(fd)
            src_path = tmp_path
        except OSError as e:
            errors.append(f"temp:{e}")
            src_path = None

    if src_path:
        try:
            return _decode_video_via_imageio_path(
                src_path,
                max_edge=max_edge,
                max_frames=max_frames,
                fps=fps,
            )
        except Exception as e:
            errors.append(f"imageio:{e}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # 3) statyczny obraz
    try:
        return decode_static_png(data, max_edge=max_edge)
    except Exception as e:
        errors.append(f"png:{e}")

    hint = (
        "pip install imageio imageio-ffmpeg  "
        "(Pillow nie czyta H.264/MP4 — potrzebny ffmpeg przez imageio)"
    )
    raise MediaError(
        f"Nie zdekodowano wideo ({mime or path or '?'}). {hint}. "
        f"Szczegóły: {'; '.join(errors)[:200]}"
    )


@dataclass
class FrameClip:
    """Klatki w RAM (GIF/static) LUB jeden bieżący PNG + dekoder inkrementalny."""

    atom_id: str
    pngs: List[bytes]
    delays: List[float]
    size: Tuple[int, int]
    idx: int = 0
    last_swap: float = field(default_factory=time.time)
    T: float = T_HOT  # lokalna temperatura (widoczność)
    kind: str = "static"  # static | gif | video | video_incr
    decoder: Optional[IncrementalVideoDecoder] = None
    # ile klatek wyemitowano (incr) — diagnostyka
    emitted: int = 0


class ThermalFramePump:
    """
    Oscylator klatek napędzany temperaturą — klon ducha ThermalGifPump (Luneta).

    pump() NIE grzeje atomów — tylko note_visible() (renderer przy blit).
    video_incr: next_png() tylko gdy hot + delay — bez preload całej listy.
    """

    def __init__(self) -> None:
        self._clips: Dict[str, FrameClip] = {}

    def has(self, atom_id: str) -> bool:
        return atom_id in self._clips

    def load_frames(
        self,
        atom_id: str,
        pngs: Sequence[bytes],
        delays: Optional[Sequence[float]] = None,
        size: Tuple[int, int] = (0, 0),
        *,
        kind: str = "static",
        T: float = T_HOT,
        decoder: Optional[IncrementalVideoDecoder] = None,
    ) -> None:
        if not pngs and decoder is None:
            raise MediaError("brak klatek")
        dlist = list(delays) if delays else ([1e9] if not pngs else [1e9] * len(pngs))
        while pngs and len(dlist) < len(pngs):
            dlist.append(dlist[-1] if dlist else 0.1)
        if size == (0, 0) and pngs and _HAS_PIL and Image is not None:
            try:
                im = Image.open(io.BytesIO(pngs[0]))
                size = im.size
            except Exception:
                size = (1, 1)
        if size == (0, 0) and decoder is not None:
            size = decoder.size or (1, 1)
        old = self._clips.pop(str(atom_id), None)
        if old and old.decoder is not None:
            try:
                old.decoder.close()
            except Exception:
                pass
        self._clips[str(atom_id)] = FrameClip(
            atom_id=str(atom_id),
            pngs=list(pngs) if pngs else [],
            delays=[float(x) for x in (dlist[: len(pngs)] if pngs else dlist[:1] or [0.08])],
            size=size,
            T=float(T),
            kind=kind,
            decoder=decoder,
            emitted=1 if pngs else 0,
        )

    def load_incremental_path(
        self,
        atom_id: str,
        path: Union[str, Path],
        *,
        max_edge: int = MAX_EDGE,
        fps: float = DEFAULT_FPS,
        T: float = T_HOT,
        loop: bool = True,
    ) -> str:
        """MP4/WebM: reader na pliku, pierwsza klatka od razu, reszta w pump()."""
        dec = open_incremental(
            path, max_edge=max_edge, target_fps=fps, loop=loop
        )
        png, delay, size = dec.peek_first()
        self.load_frames(
            atom_id,
            [png],
            [delay],
            size,
            kind="video_incr",
            T=T,
            decoder=dec,
        )
        return "video_incr"

    def load_from_bytes(
        self,
        atom_id: str,
        data: bytes,
        mime: str = "",
        *,
        max_edge: int = MAX_EDGE,
        max_frames: int = MAX_VIDEO_FRAMES,
        fps: float = DEFAULT_FPS,
        T: float = T_HOT,
        path: Optional[Union[str, Path]] = None,
        incremental: bool = True,
    ) -> str:
        """Dekoduj payload. Wideo + path → domyślnie inkrementalnie."""
        mime = (mime or "").lower()
        major = mime.split("/", 1)[0] if mime else ""
        is_vid = (
            major == "video"
            or "webm" in mime
            or "mp4" in mime
            or (path is not None and is_video_path(path))
        )
        if is_vid and incremental and path is not None and Path(path).is_file():
            try:
                return self.load_incremental_path(
                    atom_id, path, max_edge=max_edge, fps=fps, T=T
                )
            except MediaError:
                # fallback preload
                pass

        kind = "static"
        vkw = dict(max_edge=max_edge, max_frames=max_frames, fps=fps, path=path)
        if "gif" in mime:
            pngs, delays, size = decode_gif_frames(data, max_edge=max_edge)
            kind = "gif"
        elif is_vid:
            pngs, delays, size = decode_video_frames(data, mime, **vkw)
            kind = "video" if len(pngs) > 1 else "static"
        elif major == "image":
            try:
                if _HAS_PIL and Image is not None:
                    im = Image.open(io.BytesIO(data))
                    if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
                        pngs, delays, size = decode_gif_frames(data, max_edge=max_edge)
                        kind = "gif"
                    else:
                        pngs, delays, size = decode_static_png(data, max_edge=max_edge)
                else:
                    pngs, delays, size = decode_static_png(data, max_edge=max_edge)
            except MediaError:
                pngs, delays, size = decode_static_png(data, max_edge=max_edge)
        else:
            try:
                pngs, delays, size = decode_gif_frames(data, max_edge=max_edge)
                kind = "gif"
            except Exception:
                if path and is_video_path(path):
                    return self.load_incremental_path(
                        atom_id, path, max_edge=max_edge, fps=fps, T=T
                    )
                pngs, delays, size = decode_static_png(data, max_edge=max_edge)
        self.load_frames(atom_id, pngs, delays, size, kind=kind, T=T)
        return kind

    def load_from_store(self, store: Any, atom_id: str, **kw) -> str:
        data, mime = get_bytes(store, atom_id)
        return self.load_from_bytes(atom_id, data, mime, **kw)

    def load_from_path(self, atom_id: str, path: Union[str, Path], **kw) -> str:
        p = Path(path)
        import mimetypes

        mime = mimetypes.guess_type(str(p))[0] or ""
        incremental = kw.pop("incremental", True)
        # wideo: nie wczytuj całego pliku do RAM
        if incremental and is_video_path(p):
            try:
                return self.load_incremental_path(
                    atom_id,
                    p,
                    max_edge=kw.get("max_edge", MAX_EDGE),
                    fps=kw.get("fps", DEFAULT_FPS),
                    T=kw.get("T", T_HOT),
                )
            except MediaError:
                pass
        data = p.read_bytes()
        return self.load_from_bytes(
            atom_id, data, mime, path=p, incremental=incremental, **kw
        )

    def note_visible(self, atom_id: str, weight: float = 1.0) -> None:
        """WIDOCZNOŚĆ = CIEPŁO (Luneta)."""
        c = self._clips.get(str(atom_id))
        if c is None:
            return
        c.T = min(100.0, c.T + 15.0 * weight)

    def cool(self, dt: float = 0.05, rate: float = 8.0) -> None:
        """Naturalne stygnięcie niewidocznych."""
        for c in self._clips.values():
            c.T = max(0.0, c.T - rate * dt)

    def pump(self) -> Set[str]:
        """
        Przesuń klatki gorących klipów. Zwraca zbiór atom_id do **przemalowania**.
        Zimne (T < FREEZE_T) — zero decode / zero kosztu.
        """
        now = time.time()
        dirty: Set[str] = set()
        for aid, c in self._clips.items():
            if c.T < FREEZE_T:
                continue
            # --- inkrementalne wideo ---
            if c.decoder is not None:
                delay = c.delays[0] if c.delays else c.decoder.delay
                if now - c.last_swap < delay:
                    continue
                try:
                    got = c.decoder.next_png()
                except MediaError:
                    continue
                if not got:
                    continue
                png, dly = got
                c.pngs = [png]
                c.delays = [dly]
                c.idx = 0
                c.size = c.decoder.size or c.size
                c.last_swap = now
                c.emitted += 1
                dirty.add(aid)
                continue
            # --- lista w RAM (GIF / preload) ---
            n = len(c.pngs)
            if n <= 1:
                continue
            delay = c.delays[c.idx % n]
            if now - c.last_swap >= delay:
                c.idx = (c.idx + 1) % n
                c.last_swap = now
                dirty.add(aid)
        return dirty

    def current_png(self, atom_id: str) -> Optional[bytes]:
        c = self._clips.get(str(atom_id))
        if not c or not c.pngs:
            return None
        return c.pngs[c.idx % len(c.pngs)]

    def current_size(self, atom_id: str) -> Tuple[int, int]:
        c = self._clips.get(str(atom_id))
        return tuple(c.size) if c else (0, 0)

    def frame_count(self, atom_id: str) -> int:
        """Dla incr: emitted (nie total film); dla listy: len(pngs)."""
        c = self._clips.get(str(atom_id))
        if not c:
            return 0
        if c.decoder is not None:
            return max(c.emitted, 1)
        return len(c.pngs)

    def is_incremental(self, atom_id: str) -> bool:
        c = self._clips.get(str(atom_id))
        return bool(c and c.decoder is not None)

    def temperature(self, atom_id: str) -> float:
        c = self._clips.get(str(atom_id))
        return float(c.T) if c else 0.0

    def unload(self, atom_id: str) -> None:
        c = self._clips.pop(str(atom_id), None)
        if c and c.decoder is not None:
            try:
                c.decoder.close()
            except Exception:
                pass

    def clear(self) -> None:
        for aid in list(self._clips.keys()):
            self.unload(aid)


@dataclass
class CanvasSpot:
    """Atom na płótnie — pozycja; treść w ThermalFramePump."""

    atom_id: str
    x: int = 0
    y: int = 0
    z: int = 0
    # opcjonalne skalowanie wyświetlania
    scale: float = 1.0


class MediaAtomCanvas:
    """
    Płótno: lista spotów + pompa klatek.

    Renderer (Tk/SDL):
      1) note_visible dla spotów w viewport
      2) dirty = pump()
      3) dla aid in dirty: blit current_png(aid) @ spot — reszta bez ruszania
    """

    def __init__(self, pump: Optional[ThermalFramePump] = None):
        self.pump = pump or ThermalFramePump()
        self.spots: Dict[str, CanvasSpot] = {}
        self._last_cool = time.time()

    def place(
        self,
        atom_id: str,
        x: int = 0,
        y: int = 0,
        *,
        z: int = 0,
        scale: float = 1.0,
    ) -> CanvasSpot:
        sp = CanvasSpot(atom_id=str(atom_id), x=int(x), y=int(y), z=int(z), scale=float(scale))
        self.spots[sp.atom_id] = sp
        return sp

    def remove(self, atom_id: str) -> None:
        self.spots.pop(str(atom_id), None)
        self.pump.unload(str(atom_id))

    def load_store_atom(self, store: Any, atom_id: str, **kw) -> str:
        return self.pump.load_from_store(store, atom_id, **kw)

    def load_bytes(self, atom_id: str, data: bytes, mime: str = "", **kw) -> str:
        return self.pump.load_from_bytes(atom_id, data, mime, **kw)

    def tick(self, *, cool: bool = True) -> Set[str]:
        now = time.time()
        if cool:
            dt = max(0.001, now - self._last_cool)
            self.pump.cool(dt=dt)
            self._last_cool = now
        return self.pump.pump()

    def mark_visible(self, atom_ids: Optional[Sequence[str]] = None) -> None:
        ids = atom_ids if atom_ids is not None else list(self.spots.keys())
        for aid in ids:
            self.pump.note_visible(str(aid))

    def blit_list(self) -> List[Tuple[CanvasSpot, bytes, Tuple[int, int]]]:
        """Wszystkie spoty z bieżącą klatką (pełny repaint)."""
        out = []
        for sp in sorted(self.spots.values(), key=lambda s: s.z):
            png = self.pump.current_png(sp.atom_id)
            if png:
                out.append((sp, png, self.pump.current_size(sp.atom_id)))
        return out

    def dirty_blits(self, dirty: Set[str]) -> List[Tuple[CanvasSpot, bytes, Tuple[int, int]]]:
        """Tylko zmienione spoty — tanie odświeżenie."""
        out = []
        for aid in dirty:
            sp = self.spots.get(aid)
            if sp is None:
                continue
            png = self.pump.current_png(aid)
            if png:
                out.append((sp, png, self.pump.current_size(aid)))
        return out


def play_file(
    path: str | Path,
    *,
    parent: Any = None,
    title: str = "",
    dry_run: bool = False,
) -> Tuple[bool, str]:
    """
    Odtwarzacz pliku: zdjęcie / GIF / wideo → atom na płótnie.

      python -m karmazyn_media_canvas play foto.png
      python -m karmazyn_media_canvas play klip.gif
      python -m karmazyn_media_canvas play film.mp4   # klatki jeśli da się zdekodować
    """
    from pathlib import Path as _P
    import mimetypes

    p = Path(path).expanduser()
    if not p.is_file():
        return False, f"brak pliku: {p}"
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    aid = "play:" + p.name

    canvas = MediaAtomCanvas()
    try:
        # path= kluczowe dla MP4 (imageio/ffmpeg nie lubi gołych bytes)
        kind = canvas.pump.load_from_path(aid, p)
    except MediaError as e:
        return False, str(e)
    canvas.place(aid, 8, 8)
    n = canvas.pump.frame_count(aid)
    w, h = canvas.pump.current_size(aid)
    incr = canvas.pump.is_incremental(aid)
    mode = "incremental" if incr else "preload"
    info = f"{p.name} kind={kind} mode={mode} frames={n} size={w}x{h} mime={mime}"
    if dry_run:
        # nie trzymaj open ffmpeg w dry-run
        canvas.pump.unload(aid)
        return True, f"dry-run OK: {info}"

    # jednorazowy store dla okna (load już w pump)
    class _FakeStore:
        def get_atom(self, _id):
            return None

        def atoms(self):
            return []

    # open window with preloaded canvas — reuse open with custom path
    try:
        import base64
        import tkinter as tk
        from tkinter import ttk
    except Exception as e:
        return False, f"brak tkinter: {e}"

    try:
        top = tk.Toplevel(parent) if parent is not None else tk.Tk()
    except Exception as e:
        return False, f"nie otwarto okna: {e}"

    top.title(title or f"Play · {p.name} · {kind} · {n} klatek")
    cw = min(max(w + 32, 320), 1100)
    ch = min(max(h + 64, 240), 800)
    cv = tk.Canvas(top, width=cw, height=ch, bg="#121218")
    cv.pack(fill="both", expand=True)
    status = ttk.Label(top, text=info)
    status.pack(fill="x")
    ttk.Button(top, text="Zamknij", command=top.destroy).pack(pady=4)

    photos: Dict[str, Any] = {}
    state: Dict[str, Any] = {"item": None}

    def _ph(png: bytes):
        return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))

    def paint_png(png: bytes) -> None:
        ph = _ph(png)
        photos["cur"] = ph
        if state["item"] is None:
            state["item"] = cv.create_image(16, 16, anchor="nw", image=ph)
        else:
            cv.itemconfigure(state["item"], image=ph)

    def paint_full():
        canvas.mark_visible([aid])
        png = canvas.pump.current_png(aid)
        if png:
            paint_png(png)

    def tick():
        if not top.winfo_exists():
            return
        canvas.mark_visible([aid])
        dirty = canvas.tick()
        if dirty or state["item"] is None:
            png = canvas.pump.current_png(aid)
            if png:
                paint_png(png)
        clip = canvas.pump._clips.get(aid)
        fr = (clip.emitted if clip and clip.decoder else (clip.idx if clip else 0))
        hot = canvas.pump.temperature(aid) >= FREEZE_T
        status.configure(
            text=f"{info}  frame={fr}  hot={hot}  dirty={len(dirty)}"
        )
        top.after(33, tick)

    paint_full()
    top.after(33, tick)
    if parent is None:
        top.mainloop()
    return True, f"play: {info}"


def open_atom_canvas_window(
    store: Any,
    atom_ids: Union[str, Sequence[str]],
    *,
    parent: Any = None,
    title: str = "Płótno mediów",
    tick_ms: int = 33,
) -> bool:
    """
    Okno Tk: Canvas + atomy. Animacja tylko brudnych PhotoImage.
    """
    try:
        import base64
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return False

    if isinstance(atom_ids, str):
        atom_ids = [atom_ids]

    canvas_model = MediaAtomCanvas()
    x = 8
    y = 8
    row_h = 0
    max_w = 8
    for aid in atom_ids:
        try:
            kind = canvas_model.load_store_atom(store, str(aid))
            w, h = canvas_model.pump.current_size(str(aid))
            canvas_model.place(str(aid), x, y)
            row_h = max(row_h, h)
            max_w = max(max_w, x + w + 8)
            x += w + 12
            if x > 900:
                x = 8
                y += row_h + 12
                row_h = 0
            _ = kind
        except MediaError:
            continue

    if not canvas_model.spots:
        return False

    try:
        top = tk.Toplevel(parent) if parent is not None else tk.Tk()
    except Exception:
        return False
    top.title(title)
    H = y + max(row_h, 64) + 48
    cv = tk.Canvas(top, width=min(max_w + 8, 1000), height=min(H, 720), bg="#1a1a1e")
    cv.pack(fill="both", expand=True)
    status = ttk.Label(top, text="")
    status.pack(fill="x")

    # item_id na canvas per atom
    items: Dict[str, int] = {}
    photos: Dict[str, Any] = {}

    def _photo(png: bytes):
        b64 = base64.b64encode(png).decode("ascii")
        return tk.PhotoImage(data=b64)

    def full_paint():
        canvas_model.mark_visible()
        for sp, png, size in canvas_model.blit_list():
            ph = _photo(png)
            photos[sp.atom_id] = ph
            if sp.atom_id in items:
                cv.itemconfigure(items[sp.atom_id], image=ph)
                cv.coords(items[sp.atom_id], sp.x, sp.y)
            else:
                items[sp.atom_id] = cv.create_image(sp.x, sp.y, anchor="nw", image=ph)

    def tick():
        if not top.winfo_exists():
            return
        canvas_model.mark_visible()  # spoty na ekranie = gorące
        dirty = canvas_model.tick()
        for sp, png, _size in canvas_model.dirty_blits(dirty):
            ph = _photo(png)
            photos[sp.atom_id] = ph
            if sp.atom_id in items:
                cv.itemconfigure(items[sp.atom_id], image=ph)
            else:
                items[sp.atom_id] = cv.create_image(sp.x, sp.y, anchor="nw", image=ph)
        hot = sum(1 for a in canvas_model.spots if canvas_model.pump.temperature(a) >= FREEZE_T)
        status.configure(
            text=f"atomy={len(canvas_model.spots)} dirty={len(dirty)} hot={hot}  "
            f"(tylko hot animuje)"
        )
        top.after(tick_ms, tick)

    full_paint()
    top.after(tick_ms, tick)
    if parent is None:
        top.mainloop()
    return True


def main(argv: Optional[list] = None) -> int:
    """
    CLI odtwarzacza / demo:

      python -m karmazyn_media_canvas play foto.png
      python -m karmazyn_media_canvas play anim.gif
      python -m karmazyn_media_canvas play film.mp4
      python -m karmazyn_media_canvas play foto.png --dry-run
      python -m karmazyn_media_canvas demo          # wbudowany GIF + film z klatek
    """
    import sys
    from pathlib import Path as _P

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(
            "karmazyn_media_canvas — płótno atomów (PNG/GIF/wideo-klatki)\n"
            "  play <plik> [--dry-run]   odtwórz plik na płótnie\n"
            "  demo [--dry-run]         wygeneruj GIF + sekwencję i pokaż\n",
            file=sys.stderr,
        )
        return 0

    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    cmd = args[0].lower()

    if cmd == "play":
        if len(args) < 2:
            print("użycie: play <plik> [--dry-run]", file=sys.stderr)
            return 2
        ok, msg = play_file(args[1], dry_run=dry)
        print(msg)
        return 0 if ok else 1

    if cmd == "demo":
        if not _HAS_PIL or Image is None:
            print("demo wymaga Pillow", file=sys.stderr)
            return 1
        # wbudowany GIF + „film” z klatek
        import tempfile

        tmp = _P(tempfile.mkdtemp(prefix="karm_canvas_"))
        # GIF
        frames = []
        for i in range(6):
            frames.append(
                Image.new(
                    "RGBA",
                    (120, 80),
                    ((i * 40) % 256, 80, 200 - i * 20, 255),
                )
            )
        gif_path = tmp / "demo.gif"
        frames[0].save(
            gif_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=80,
            loop=0,
        )
        # statyczne zdjęcie
        photo = tmp / "demo.png"
        Image.new("RGB", (160, 100), (30, 144, 255)).save(photo, format="PNG")

        if dry:
            ok1, m1 = play_file(photo, dry_run=True)
            ok2, m2 = play_file(gif_path, dry_run=True)
            print(m1)
            print(m2)
            print(f"demo files: {photo} ; {gif_path}")
            return 0 if (ok1 and ok2) else 1

        # jedno okno z dwoma atomami
        from karmazyn_kernel import Store
        import karmazyn_media as km

        store = Store(thermal=True)
        r1 = km.attach_file(store, "Demo", "zdjecie", photo)
        r2 = km.attach_file(store, "Demo", "gif", gif_path)
        # sekwencja filmowa w pump (bez pliku mp4)
        canvas = MediaAtomCanvas()
        canvas.load_store_atom(store, r1.atom_id)
        canvas.load_store_atom(store, r2.atom_id)
        film_pngs = []
        for i in range(10):
            im = Image.new("RGB", (100, 60), (i * 20, 50, 255 - i * 15))
            b = io.BytesIO()
            im.save(b, format="PNG")
            film_pngs.append(b.getvalue())
        canvas.pump.load_frames(
            "demo:film",
            film_pngs,
            delays=[0.08] * 10,
            size=(100, 60),
            kind="video",
        )
        canvas.place(r1.atom_id, 8, 8)
        canvas.place(r2.atom_id, 180, 8)
        canvas.place("demo:film", 8, 120)

        # ręczne okno (open_atom_canvas_window wymaga store get_bytes dla film id)
        # podłącz film przez mini-atom w store
        from karmazyn_media import MEDIA_S

        fa = store.atom_new(S=MEDIA_S, E="film@Demo", value=None, T=80.0)
        # zapisz pierwszą klatkę jako data — load_store_atom zdekoduje static;
        # zamiast tego: open window z już załadowanym canvasem
        try:
            import base64
            import tkinter as tk
            from tkinter import ttk
        except Exception as e:
            print(f"brak tk: {e}", file=sys.stderr)
            return 1
        top = tk.Tk()
        top.title("Demo płótno · zdjęcie + GIF + film-klatki")
        cv = tk.Canvas(top, width=420, height=220, bg="#0e0e14")
        cv.pack(fill="both", expand=True)
        st = ttk.Label(top, text="")
        st.pack(fill="x")
        photos: Dict[str, Any] = {}
        items: Dict[str, int] = {}

        def ph(png: bytes):
            return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))

        def full():
            canvas.mark_visible()
            for sp, png, _sz in canvas.blit_list():
                pimg = ph(png)
                photos[sp.atom_id] = pimg
                if sp.atom_id in items:
                    cv.itemconfigure(items[sp.atom_id], image=pimg)
                    cv.coords(items[sp.atom_id], sp.x, sp.y)
                else:
                    items[sp.atom_id] = cv.create_image(
                        sp.x, sp.y, anchor="nw", image=pimg
                    )

        def tick():
            if not top.winfo_exists():
                return
            canvas.mark_visible()
            dirty = canvas.tick()
            for sp, png, _sz in canvas.dirty_blits(dirty):
                pimg = ph(png)
                photos[sp.atom_id] = pimg
                if sp.atom_id in items:
                    cv.itemconfigure(items[sp.atom_id], image=pimg)
            st.configure(
                text=f"dirty={len(dirty)}  "
                f"gif_fr={canvas.pump._clips.get(r2.atom_id) and canvas.pump._clips[r2.atom_id].idx}  "
                f"film_fr={canvas.pump._clips.get('demo:film') and canvas.pump._clips['demo:film'].idx}"
            )
            top.after(33, tick)

        full()
        top.after(33, tick)
        print(f"demo: photo={r1.atom_id} gif={r2.atom_id} film=demo:film  dir={tmp}")
        top.mainloop()
        return 0

    print(f"nieznana komenda: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
