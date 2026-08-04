# -*- coding: utf-8 -*-
"""
karmazyn_media_incremental.py — inkrementalny dekoder wideo (spike substratu)
==============================================================================
Zamiast:  MP4 → 180 PNG w RAM → pump idx
Robimy:   MP4 → otwarty reader → next_frame() tylko gdy head jest gorący

Backend na start: imageio + imageio-ffmpeg (ścieżka pliku).
Architektura pod przyszły Native/SubstrateDecoder (ten sam protokół).

Protokół StreamDecoder:
  open(path) → self
  next_png() → (png_bytes, delay_s) | None  (EOF → loop/reopen)
  close()
  size, src_fps, frames_emitted
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple, Union

from karmazyn_media import MediaError

try:
    from PIL import Image

    _HAS_PIL = True
except Exception:
    Image = None  # type: ignore
    _HAS_PIL = False

DEFAULT_FPS = 12.0
MAX_EDGE = 960


def _frame_to_png(im) -> bytes:
    buf = io.BytesIO()
    im.convert("RGBA").save(buf, format="PNG", optimize=False)
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


class IncrementalVideoDecoder:
    """
    Jedna klatka na żądanie. Nie ładuje całego filmu do listy PNG.

    Użycie (ThermalFramePump):
      gdy T ≥ FREEZE i minął delay → decoder.next_png() → podmień bieżącą klatkę
    """

    def __init__(
        self,
        path: Union[str, Path],
        *,
        max_edge: int = MAX_EDGE,
        target_fps: float = DEFAULT_FPS,
        loop: bool = True,
    ):
        if not _HAS_PIL or Image is None:
            raise MediaError("Pillow wymagany (pip install pillow)")
        self.path = str(Path(path).resolve())
        if not os.path.isfile(self.path):
            raise MediaError(f"brak pliku: {self.path}")
        self.max_edge = int(max_edge)
        self.target_fps = float(target_fps) if target_fps > 0 else DEFAULT_FPS
        self.loop = bool(loop)
        self.src_fps: float = self.target_fps
        self.size: Tuple[int, int] = (0, 0)
        self.frames_emitted: int = 0
        self._stride: int = 1
        self._delay: float = 1.0 / self.target_fps
        self._iter: Optional[Iterator] = None
        self._closed: bool = False
        self._open_reader()

    def _open_reader(self) -> None:
        try:
            import imageio.v3 as iio  # type: ignore
        except ImportError as e:
            raise MediaError(
                "imageio wymagany do inkrementalnego MP4: "
                "pip install imageio imageio-ffmpeg"
            ) from e
        self.close_reader_only()
        meta: dict = {}
        try:
            meta = dict(iio.immeta(self.path) or {})
        except Exception:
            pass
        self.src_fps = float(meta.get("fps") or self.target_fps or DEFAULT_FPS)
        if self.src_fps <= 0:
            self.src_fps = DEFAULT_FPS
        self._stride = max(1, int(round(self.src_fps / min(self.target_fps, self.src_fps))))
        self._delay = self._stride / self.src_fps
        self._iter = iio.imiter(self.path)
        self._closed = False

    def close_reader_only(self) -> None:
        """Zamknij iterator (plik); obiekt można reopen."""
        self._iter = None

    def close(self) -> None:
        self.close_reader_only()
        self._closed = True

    def reopen(self) -> None:
        if self._closed:
            raise MediaError("decoder closed")
        self._open_reader()

    @property
    def delay(self) -> float:
        return float(self._delay)

    def next_png(self) -> Optional[Tuple[bytes, float]]:
        """
        Następna klatka (po stride). EOF → loop reopen lub None.
        Zwraca (png_bytes, delay_s).
        """
        if self._closed:
            return None
        if self._iter is None:
            try:
                self._open_reader()
            except MediaError:
                return None
        assert self._iter is not None
        try:
            arr = None
            for _ in range(self._stride):
                arr = next(self._iter)
            if arr is None:
                return None
            im = Image.fromarray(arr).convert("RGBA")
            im = _resize_rgba(im, self.max_edge)
            self.size = im.size
            png = _frame_to_png(im)
            self.frames_emitted += 1
            return png, self._delay
        except StopIteration:
            if not self.loop:
                return None
            try:
                self.reopen()
            except MediaError:
                return None
            return self.next_png()
        except Exception as e:
            raise MediaError(f"incremental decode: {e}") from e

    def peek_first(self) -> Tuple[bytes, float, Tuple[int, int]]:
        """Pierwsza klatka + ustaw size (do place na canvas)."""
        got = self.next_png()
        if not got:
            raise MediaError("wideo bez klatek")
        png, delay = got
        return png, delay, self.size


def open_incremental(
    path: Union[str, Path],
    *,
    max_edge: int = MAX_EDGE,
    target_fps: float = DEFAULT_FPS,
    loop: bool = True,
) -> IncrementalVideoDecoder:
    return IncrementalVideoDecoder(
        path, max_edge=max_edge, target_fps=target_fps, loop=loop
    )


def is_video_path(path: Union[str, Path]) -> bool:
    s = str(path).lower()
    return s.endswith((".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"))


def bytes_to_temp_video(data: bytes, mime: str = "") -> str:
    """Gdy mamy tylko bytes — zapisz temp (imageio chce path). Caller unlink."""
    ext = ".mp4"
    m = (mime or "").lower()
    if "webm" in m:
        ext = ".webm"
    elif "mov" in m:
        ext = ".mov"
    elif "mkv" in m or "matroska" in m:
        ext = ".mkv"
    fd, path = tempfile.mkstemp(suffix=ext, prefix="karm_incr_")
    os.write(fd, data)
    os.close(fd)
    return path
