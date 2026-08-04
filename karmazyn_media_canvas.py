# -*- coding: utf-8 -*-
"""
karmazyn_media_canvas.py — płótno atomów mediów (model Luneta)
==============================================================
Odwzorowanie jak Luneta / ThermalGifPump:

  • PNG / GIF / wideo = atomy (lub sekwencja klatek PNG w RAM)
  • Animacja TYLKO gdy atom jest gorący (widoczny) — T ≥ FREEZE_T
  • pump() przesuwa klatkę i zwraca **dirty** id — maluj tylko to, co się zmieniło
  • note_visible() = ciepło z widoczności (nie z samego pump — bez samonapędzania)

Wideo: dekodowane do klatek PNG (Pillow / imageio opcjonalnie); aktywne
wyświetlanie = jedna klatka na atom w danym ticku — tanie, bo reszta zamarza.

Bez pygame/SDL — Tk Canvas + PhotoImage z PNG (jak Luneta tk path).
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from karmazyn_media import MediaError, get_bytes, is_stream_atom

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


def decode_video_frames(
    data: bytes,
    mime: str = "",
    *,
    max_edge: int = MAX_EDGE,
    max_frames: int = MAX_VIDEO_FRAMES,
    fps: float = DEFAULT_FPS,
) -> Tuple[List[bytes], List[float], Tuple[int, int]]:
    """
    Wideo → klatki PNG (tanie wyświetlanie: jedna klatka / tick / atom).

    Kolejność backendów:
      1) Pillow (animowany WebP / APNG)
      2) imageio / imageio-ffmpeg (opcjonalnie)
      3) fallback: pierwsza klatka jako statyczny PNG jeśli to obraz
    """
    if not _HAS_PIL or Image is None:
        raise MediaError("Pillow wymagany do klatek wideo")

    # animowany webp / apng przez PIL
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
                delays.append(max(0.02, (img.info.get("duration") or int(1000 / fps)) / 1000.0))
            if pngs:
                return pngs, delays, size
    except Exception:
        pass

    # imageio
    try:
        import imageio.v3 as iio  # type: ignore

        frames = iio.imread(data, index=None)
        # frames may be ndarray list or single
        import numpy as np

        if not isinstance(frames, np.ndarray):
            raise MediaError("imageio: nieoczekiwany format")
        # (T,H,W,C) or (H,W,C)
        if frames.ndim == 3:
            frames = frames[None, ...]
        pngs, delays = [], []
        size = (0, 0)
        step = max(1, frames.shape[0] // max_frames) if frames.shape[0] > max_frames else 1
        for i in range(0, frames.shape[0], step):
            if len(pngs) >= max_frames:
                break
            arr = frames[i]
            im = Image.fromarray(arr).convert("RGBA")
            im = _resize_rgba(im, max_edge)
            size = im.size
            pngs.append(_frame_to_png(im))
            delays.append(1.0 / max(fps, 1.0))
        if pngs:
            return pngs, delays, size
    except Exception:
        pass

    # ostatnia deska: spróbuj jako obraz statyczny
    try:
        return decode_static_png(data, max_edge=max_edge)
    except Exception as e:
        raise MediaError(
            f"Nie zdekodowano wideo ({mime or '?'}). "
            f"Zainstaluj pillow / imageio-ffmpeg lub użyj GIF/WebP. ({e})"
        ) from e


@dataclass
class FrameClip:
    """Sekwencja klatek w RAM (atom na płótnie)."""

    atom_id: str
    pngs: List[bytes]
    delays: List[float]
    size: Tuple[int, int]
    idx: int = 0
    last_swap: float = field(default_factory=time.time)
    T: float = T_HOT  # lokalna temperatura (widoczność)
    kind: str = "static"  # static | gif | video


class ThermalFramePump:
    """
    Oscylator klatek napędzany temperaturą — klon ducha ThermalGifPump (Luneta).

    pump() NIE grzeje atomów — tylko note_visible() (renderer przy blit).
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
    ) -> None:
        if not pngs:
            raise MediaError("brak klatek")
        dlist = list(delays) if delays else [1e9] * len(pngs)
        while len(dlist) < len(pngs):
            dlist.append(dlist[-1] if dlist else 0.1)
        if size == (0, 0) and _HAS_PIL and Image is not None:
            try:
                im = Image.open(io.BytesIO(pngs[0]))
                size = im.size
            except Exception:
                size = (1, 1)
        self._clips[str(atom_id)] = FrameClip(
            atom_id=str(atom_id),
            pngs=list(pngs),
            delays=[float(x) for x in dlist[: len(pngs)]],
            size=size,
            T=float(T),
            kind=kind,
        )

    def load_from_bytes(
        self,
        atom_id: str,
        data: bytes,
        mime: str = "",
        *,
        max_edge: int = MAX_EDGE,
        T: float = T_HOT,
    ) -> str:
        """Dekoduj payload → klatki. Zwraca kind."""
        mime = (mime or "").lower()
        major = mime.split("/", 1)[0] if mime else ""
        kind = "static"
        if "gif" in mime:
            pngs, delays, size = decode_gif_frames(data, max_edge=max_edge)
            kind = "gif"
        elif major == "video" or "webm" in mime or "mp4" in mime:
            pngs, delays, size = decode_video_frames(data, mime, max_edge=max_edge)
            kind = "video" if len(pngs) > 1 else "static"
        elif major == "image":
            # animated webp?
            try:
                if _HAS_PIL and Image is not None:
                    im = Image.open(io.BytesIO(data))
                    if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
                        pngs, delays, size = decode_video_frames(data, mime, max_edge=max_edge)
                        kind = "gif"
                    else:
                        pngs, delays, size = decode_static_png(data, max_edge=max_edge)
                else:
                    pngs, delays, size = decode_static_png(data, max_edge=max_edge)
            except MediaError:
                pngs, delays, size = decode_static_png(data, max_edge=max_edge)
        else:
            # spróbuj gif/png
            try:
                pngs, delays, size = decode_gif_frames(data, max_edge=max_edge)
                kind = "gif"
            except Exception:
                pngs, delays, size = decode_static_png(data, max_edge=max_edge)
        self.load_frames(atom_id, pngs, delays, size, kind=kind, T=T)
        return kind

    def load_from_store(self, store: Any, atom_id: str, **kw) -> str:
        data, mime = get_bytes(store, atom_id)
        return self.load_from_bytes(atom_id, data, mime, **kw)

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
        Zimne (T < FREEZE_T) — zero kosztu.
        """
        now = time.time()
        dirty: Set[str] = set()
        for aid, c in self._clips.items():
            if c.T < FREEZE_T:
                continue
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
        c = self._clips.get(str(atom_id))
        return len(c.pngs) if c else 0

    def temperature(self, atom_id: str) -> float:
        c = self._clips.get(str(atom_id))
        return float(c.T) if c else 0.0

    def unload(self, atom_id: str) -> None:
        self._clips.pop(str(atom_id), None)

    def clear(self) -> None:
        self._clips.clear()


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

    p = _P(path).expanduser()
    if not p.is_file():
        return False, f"brak pliku: {p}"
    data = p.read_bytes()
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    aid = "play:" + p.name

    canvas = MediaAtomCanvas()
    try:
        kind = canvas.load_bytes(aid, data, mime)
    except MediaError as e:
        return False, str(e)
    canvas.place(aid, 8, 8)
    n = canvas.pump.frame_count(aid)
    w, h = canvas.pump.current_size(aid)
    info = f"{p.name} kind={kind} frames={n} size={w}x{h} mime={mime}"
    if dry_run:
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
        fr = canvas.pump._clips[aid].idx if canvas.pump.has(aid) else 0
        hot = canvas.pump.temperature(aid) >= FREEZE_T
        status.configure(
            text=f"{info}  frame={fr}/{n}  hot={hot}  dirty={len(dirty)}"
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
