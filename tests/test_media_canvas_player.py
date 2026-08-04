# -*- coding: utf-8 -*-
"""Testy płótna atomów + smoke dekodowania GIF / sekwencji klatek (jak film)."""

from __future__ import annotations

import io
import tempfile
import time
import unittest
from pathlib import Path

import karmazyn_kernel as kernel
import karmazyn_media as media

try:
    from PIL import Image

    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


def _png(rgb=(0, 0, 255), size=(16, 16)) -> bytes:
    im = Image.new("RGBA", size, rgb + (255,))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _gif_frames(n: int = 4, size=(24, 24)) -> bytes:
    """Animowany GIF (różne kolory)."""
    frames = []
    for i in range(n):
        c = ((i * 50) % 256, 40, 200 - i * 30)
        frames.append(Image.new("RGBA", size, c + (255,)))
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=40,
        loop=0,
    )
    return buf.getvalue()


@unittest.skipUnless(_HAS_PIL, "Pillow required")
class TestCanvasGifAndVideoFrames(unittest.TestCase):
    def setUp(self) -> None:
        self.store = kernel.Store(thermal=True)

    def test_png_on_canvas(self) -> None:
        from karmazyn_media_canvas import MediaAtomCanvas

        raw = _png((10, 200, 50))
        ref = media.attach_bytes(
            self.store, "Gal", "zdjecie", raw, mime="image/png"
        )
        c = MediaAtomCanvas()
        kind = c.load_store_atom(self.store, ref.atom_id)
        self.assertEqual(kind, "static")
        c.place(ref.atom_id, 0, 0)
        blits = c.blit_list()
        self.assertEqual(len(blits), 1)
        self.assertGreater(len(blits[0][1]), 20)

    def test_gif_animates_when_hot(self) -> None:
        from karmazyn_media_canvas import FREEZE_T, MediaAtomCanvas

        raw = _gif_frames(5)
        ref = media.attach_bytes(
            self.store, "Gal", "anim", raw, mime="image/gif"
        )
        c = MediaAtomCanvas()
        kind = c.load_store_atom(self.store, ref.atom_id)
        self.assertEqual(kind, "gif")
        self.assertGreaterEqual(c.pump.frame_count(ref.atom_id), 3)
        c.place(ref.atom_id, 0, 0)

        # zimny
        c.pump._clips[ref.atom_id].T = FREEZE_T - 1
        self.assertEqual(c.tick(cool=False), set())

        # gorący + wait past delay
        c.mark_visible([ref.atom_id])
        clip = c.pump._clips[ref.atom_id]
        clip.delays = [0.01] * len(clip.pngs)
        clip.last_swap = time.time() - 1.0
        dirty = c.tick(cool=False)
        self.assertIn(ref.atom_id, dirty)
        idx1 = c.pump._clips[ref.atom_id].idx
        clip.last_swap = time.time() - 1.0
        dirty2 = c.tick(cool=False)
        self.assertIn(ref.atom_id, dirty2)
        idx2 = c.pump._clips[ref.atom_id].idx
        self.assertNotEqual(idx1, idx2)

    def test_video_as_frame_sequence(self) -> None:
        """„Film” = sekwencja klatek PNG w pump (bez codecs)."""
        from karmazyn_media_canvas import MediaAtomCanvas

        frames = [_png((i * 40, 20, 100 - i * 10), (32, 24)) for i in range(8)]
        c = MediaAtomCanvas()
        c.pump.load_frames(
            "film1",
            frames,
            delays=[0.05] * 8,
            size=(32, 24),
            kind="video",
        )
        c.place("film1", 0, 0)
        c.mark_visible(["film1"])
        self.assertEqual(c.pump.frame_count("film1"), 8)
        c.pump._clips["film1"].last_swap = time.time() - 1
        dirty = c.tick(cool=False)
        self.assertIn("film1", dirty)
        # dirty_blits tanie
        bl = c.dirty_blits(dirty)
        self.assertEqual(len(bl), 1)
        self.assertEqual(bl[0][0].atom_id, "film1")

    def test_only_dirty_not_all(self) -> None:
        from karmazyn_media_canvas import MediaAtomCanvas

        c = MediaAtomCanvas()
        c.pump.load_frames(
            "a", [_png((1, 0, 0))], delays=[1e9], size=(16, 16), kind="static"
        )
        c.pump.load_frames(
            "b",
            [_png((0, 1, 0)), _png((0, 2, 0)), _png((0, 3, 0))],
            delays=[0.01, 0.01, 0.01],
            size=(16, 16),
            kind="video",
        )
        c.place("a", 0, 0)
        c.place("b", 20, 0)
        c.mark_visible(["a", "b"])
        c.pump._clips["b"].last_swap = time.time() - 1
        dirty = c.tick(cool=False)
        self.assertIn("b", dirty)
        self.assertNotIn("a", dirty)  # statyczny nie brudzi

    def test_roundtrip_photo_kafd_play_path(self) -> None:
        """Zdjęcie → store → kafd → load → canvas (ścieżka jak CLI play)."""
        from karmazyn_media_canvas import MediaAtomCanvas

        raw = _png((255, 128, 0), (48, 32))
        ref = media.attach_bytes(
            self.store, "Album", "foto", raw, mime="image/png"
        )
        with tempfile.TemporaryDirectory() as tmp:
            kafd = Path(tmp) / "album.kafd"
            media.save_store(self.store, kafd)
            store2 = kernel.Store(thermal=True)
            media.load_store(store2, kafd, restore=True)
            c = MediaAtomCanvas()
            c.load_store_atom(store2, ref.atom_id)
            c.place(ref.atom_id, 0, 0)
            self.assertEqual(len(c.blit_list()), 1)

    def test_cli_play_headless_load(self) -> None:
        """CLI play --dry-run ładuje plik bez okna."""
        from karmazyn_media_canvas import main as canvas_main

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "shot.png"
            p.write_bytes(_png((9, 9, 9)))
            rc = canvas_main(["play", str(p), "--dry-run"])
            self.assertEqual(rc, 0)

    def test_mp4_via_imageio_if_present(self) -> None:
        """test.mp4 → inkrementalnie (video_incr), nie 180 PNG w RAM."""
        from karmazyn_media_canvas import MediaAtomCanvas

        mp4 = Path(__file__).resolve().parents[1] / "test.mp4"
        if not mp4.is_file():
            self.skipTest("brak test.mp4")
        try:
            import imageio  # noqa: F401
            import imageio_ffmpeg  # noqa: F401
        except ImportError:
            self.skipTest("brak imageio-ffmpeg")
        c = MediaAtomCanvas()
        kind = c.pump.load_from_path("t:mp4", mp4)
        self.assertEqual(kind, "video_incr")
        self.assertTrue(c.pump.is_incremental("t:mp4"))
        # w RAM tylko bieżąca klatka (1 png), nie preload
        clip = c.pump._clips["t:mp4"]
        self.assertEqual(len(clip.pngs), 1)
        c.place("t:mp4", 0, 0)
        c.mark_visible(["t:mp4"])
        self.assertIsNotNone(c.pump.current_png("t:mp4"))
        # pump dekoduje następną gdy hot
        clip.last_swap = time.time() - 10
        dirty = c.tick(cool=False)
        self.assertIn("t:mp4", dirty)
        self.assertGreaterEqual(clip.emitted, 2)
        c.pump.unload("t:mp4")

    def test_incremental_decoder_unit(self) -> None:
        from karmazyn_media_incremental import open_incremental

        mp4 = Path(__file__).resolve().parents[1] / "test.mp4"
        if not mp4.is_file():
            self.skipTest("brak test.mp4")
        try:
            import imageio_ffmpeg  # noqa: F401
        except ImportError:
            self.skipTest("brak imageio-ffmpeg")
        dec = open_incremental(mp4, target_fps=10)
        png1, d1, size = dec.peek_first()
        self.assertGreater(len(png1), 50)
        self.assertGreater(size[0], 0)
        png2, d2 = dec.next_png()
        self.assertIsNotNone(png2)
        self.assertGreater(dec.frames_emitted, 1)
        # nie trzymamy listy — tylko reader
        dec.close()


if __name__ == "__main__":
    unittest.main()
