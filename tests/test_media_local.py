"""Faza 0: lokalne API mediów — attach / KAFD roundtrip / SOUL blob limit."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import karmazyn_kernel as kernel
import karmazyn_media as media
from cynober_gossip import SOUL_MAX_INLINE_BLOB, serialize_soul
from karmazyn_store import DOC_KINDS


class TestMediaLocal(unittest.TestCase):
    def setUp(self) -> None:
        self.store = kernel.Store(thermal=True)

    def test_doc_kinds_includes_media(self) -> None:
        self.assertIn("media", DOC_KINDS)
        self.assertIn("__bubble__", DOC_KINDS)

    def test_attach_bytes_and_lookup(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"fake-body-001"
        ref = media.attach_bytes(
            self.store,
            "Anna",
            "portret",
            png,
            mime="image/png",
            as_root=True,
        )
        self.assertEqual(ref.mime, "image/png")
        self.assertEqual(ref.size, len(png))
        self.assertEqual(ref.binding, "portret")
        data, mime = media.get_bytes(self.store, ref.atom_id)
        self.assertEqual(data, png)
        self.assertEqual(mime, "image/png")

        listed = media.list_bindings(self.store, "Anna")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].atom_id, ref.atom_id)

    def test_attach_file_roundtrip_kafd(self) -> None:
        raw = b"\x89PNG\r\n\x1a\n" + b"roundtrip-" + os_urandom(64)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "hero.png"
            src.write_bytes(raw)
            src_hash = hashlib.sha256(raw).hexdigest()

            ref = media.attach_file(
                self.store, "Bohater", "portret", src, as_root=True
            )
            self.assertEqual(ref.mime, "image/png")
            self.assertEqual(ref.size, len(raw))

            kafd = Path(tmp) / "world.kafd"
            n = media.save_store(self.store, kafd)
            self.assertGreaterEqual(n, 1)

            store2 = kernel.Store(thermal=True)
            n2 = media.load_store(store2, kafd, restore=True)
            self.assertGreaterEqual(n2, 1)

            data, mime = media.get_bytes(store2, ref.atom_id)
            self.assertEqual(hashlib.sha256(data).hexdigest(), src_hash)
            self.assertEqual(mime, "image/png")

            # binding po restore_bubbles
            bindings = media.list_bindings(store2, "Bohater")
            ids = {b.atom_id for b in bindings}
            self.assertIn(ref.atom_id, ids)

            out = Path(tmp) / "export.png"
            written = media.export_to_path(store2, ref.atom_id, out)
            self.assertEqual(written, len(raw))
            self.assertEqual(out.read_bytes(), raw)

    def test_kernel_exports_media(self) -> None:
        self.assertTrue(hasattr(kernel, "attach_file"))
        self.assertTrue(hasattr(kernel, "get_bytes"))
        ref = kernel.attach_bytes(
            self.store, "X", "klip", b"abc", mime="audio/wav"
        )
        data, mime = kernel.get_bytes(self.store, ref.atom_id)
        self.assertEqual(data, b"abc")
        self.assertEqual(mime, "audio/wav")

    def test_empty_bytes_rejected(self) -> None:
        with self.assertRaises(media.MediaError):
            media.attach_bytes(self.store, "A", "x", b"")

    def test_missing_file(self) -> None:
        with self.assertRaises(media.MediaError):
            media.attach_file(self.store, "A", "x", "/no/such/file.zzz")


class TestMediaPreview(unittest.TestCase):
    """Faza 2: pipe / temp / CLI (bez GUI w CI)."""

    def setUp(self) -> None:
        self.store = kernel.Store(thermal=True)
        self.raw = b"\x89PNG\r\n\x1a\n" + b"preview-bytes"
        self.ref = media.attach_bytes(
            self.store, "P", "img", self.raw, mime="image/png"
        )

    def test_pipe_to_buffer(self) -> None:
        import io

        buf = io.BytesIO()
        n = media.pipe_to(self.store, self.ref.atom_id, buf, chunk_size=8)
        self.assertEqual(n, len(self.raw))
        self.assertEqual(buf.getvalue(), self.raw)

    def test_materialize_temp_extension(self) -> None:
        p = media.materialize_temp(self.store, self.ref.atom_id)
        try:
            self.assertTrue(p.is_file())
            self.assertTrue(str(p).lower().endswith(".png"))
            self.assertEqual(p.read_bytes(), self.raw)
        finally:
            p.unlink(missing_ok=True)

    def test_export_and_open_path_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.png"
            n = media.export_to_path(self.store, self.ref.atom_id, out)
            self.assertEqual(n, len(self.raw))
            self.assertEqual(out.read_bytes(), self.raw)

    def test_try_external_player_graceful(self) -> None:
        # Bez playerów w PATH i tak nie może paść hard crash
        ok, msg = media.try_external_player(
            self.store,
            self.ref.atom_id,
            players=["___no_such_player_xyz___"],
            keep_temp=False,
        )
        self.assertFalse(ok)
        self.assertIn("Brak", msg)

    def test_cli_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kafd = Path(tmp) / "m.kafd"
            media.save_store(self.store, kafd)
            out = Path(tmp) / "out.png"
            rc = media.main(["extract", str(kafd), self.ref.atom_id, str(out)])
            self.assertEqual(rc, 0)
            self.assertEqual(out.read_bytes(), self.raw)

    def test_cli_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kafd = Path(tmp) / "m.kafd"
            media.save_store(self.store, kafd)
            rc = media.main(["list", str(kafd)])
            self.assertEqual(rc, 0)
            rc2 = media.main(["list", str(kafd), "P"])
            self.assertEqual(rc2, 0)


def os_urandom(n: int) -> bytes:
    import os

    return os.urandom(n)


class TestMediaStream(unittest.TestCase):
    """Faza 4: A_STREAM head + media_seg."""

    def setUp(self) -> None:
        self.store = kernel.Store(thermal=True)

    def test_force_stream_reassemble(self) -> None:
        raw = b"STREAM-" + os_urandom(5000)
        ref = media.attach_bytes(
            self.store,
            "Film",
            "klip",
            raw,
            mime="video/mp4",
            force_stream=True,
            segment_size=1024,
            as_root=True,
        )
        atom = self.store.get_atom(ref.atom_id)
        self.assertTrue(media.is_stream_atom(atom))
        self.assertEqual(atom.metadata.get("data"), b"")
        v = atom.metadata["v"]
        self.assertEqual(v["kind"], "media_stream")
        self.assertGreaterEqual(v["n_segments"], 5)
        self.assertEqual(len(v["segments"]), v["n_segments"])

        data, mime = media.get_bytes(self.store, ref.atom_id)
        self.assertEqual(data, raw)
        self.assertEqual(mime, "video/mp4")
        self.assertEqual(hashlib.sha256(data).hexdigest(), v["sha256"])

        # segmenty istnieją i nie są monolit head
        for sid in v["segments"]:
            seg = self.store.get_atom(sid)
            self.assertIsNotNone(seg)
            self.assertEqual(seg.S, media.MEDIA_SEG_S)
            self.assertTrue(isinstance(seg.metadata.get("data"), (bytes, bytearray)))

    def test_stream_kafd_roundtrip(self) -> None:
        raw = b"\x00KAFD-STREAM\x00" + os_urandom(3000)
        ref = media.attach_bytes(
            self.store,
            "W",
            "blob",
            raw,
            mime="application/octet-stream",
            force_stream=True,
            segment_size=800,
            as_root=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            kafd = Path(tmp) / "s.kafd"
            n = media.save_store(self.store, kafd)
            self.assertGreaterEqual(n, 2)  # head + segs + bubble

            store2 = kernel.Store(thermal=True)
            media.load_store(store2, kafd, restore=True)
            data, _ = media.get_bytes(store2, ref.atom_id)
            self.assertEqual(data, raw)
            listed = media.list_bindings(store2, "W")
            self.assertTrue(any(b.atom_id == ref.atom_id for b in listed))
            self.assertEqual(listed[0].size, len(raw))

    def test_stream_threshold_auto(self) -> None:
        raw = os_urandom(200)
        ref = media.attach_bytes(
            self.store,
            "T",
            "x",
            raw,
            stream_threshold=100,
            segment_size=40,
            as_root=True,
        )
        self.assertTrue(media.is_stream_atom(self.store.get_atom(ref.atom_id)))
        self.assertEqual(media.get_bytes(self.store, ref.atom_id)[0], raw)

    def test_attach_file_stream(self) -> None:
        raw = os_urandom(2500)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "big.bin"
            p.write_bytes(raw)
            ref = media.attach_file(
                self.store,
                "F",
                "plik",
                p,
                force_stream=True,
                segment_size=512,
                as_root=True,
            )
            data, _ = media.get_bytes(self.store, ref.atom_id)
            self.assertEqual(data, raw)
            # pipe bez reassemble w jednej alokacji testujemy sumą
            import io

            buf = io.BytesIO()
            n = media.pipe_to(self.store, ref.atom_id, buf)
            self.assertEqual(n, len(raw))
            self.assertEqual(buf.getvalue(), raw)

    def test_kernel_exports_stream(self) -> None:
        self.assertTrue(hasattr(kernel, "iter_bytes"))
        self.assertTrue(hasattr(kernel, "is_stream_atom"))
        self.assertEqual(kernel.MEDIA_SEG_S, "media_seg")


class TestMediaAtomCanvas(unittest.TestCase):
    """Płótno atomów: dirty paint + termiczny pump (model Luneta)."""

    def test_static_and_gif_pump_dirty(self) -> None:
        from karmazyn_media_canvas import MediaAtomCanvas, FREEZE_T, _HAS_PIL

        if not _HAS_PIL:
            self.skipTest("Pillow")
        # minimalny 1x1 PNG
        from PIL import Image
        import io

        def png_color(rgb, n=1):
            im = Image.new("RGBA", (4, 4), rgb + (255,))
            b = io.BytesIO()
            im.save(b, format="PNG")
            return b.getvalue()

        # ręczny clip 3 klatki
        canvas = MediaAtomCanvas()
        frames = [png_color((i * 40, 0, 0)) for i in range(3)]
        canvas.pump.load_frames(
            "v1", frames, delays=[0.01, 0.01, 0.01], size=(4, 4), kind="video"
        )
        canvas.place("v1", 0, 0)
        # zimny — brak dirty
        canvas.pump._clips["v1"].T = FREEZE_T - 5
        self.assertEqual(canvas.tick(cool=False), set())
        # gorący — po delay dirty
        canvas.mark_visible(["v1"])
        import time

        time.sleep(0.03)
        dirty = canvas.tick(cool=False)
        self.assertIn("v1", dirty)
        self.assertIsNotNone(canvas.pump.current_png("v1"))

    def test_load_png_from_store(self) -> None:
        from karmazyn_media_canvas import MediaAtomCanvas, _HAS_PIL

        if not _HAS_PIL:
            self.skipTest("Pillow")
        store = kernel.Store(thermal=True)
        # valid tiny PNG via PIL
        from PIL import Image
        import io

        im = Image.new("RGB", (8, 8), (0, 128, 255))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        raw = buf.getvalue()
        ref = media.attach_bytes(store, "B", "img", raw, mime="image/png")
        c = MediaAtomCanvas()
        kind = c.load_store_atom(store, ref.atom_id)
        self.assertEqual(kind, "static")
        c.place(ref.atom_id, 10, 10)
        blits = c.blit_list()
        self.assertEqual(len(blits), 1)
        self.assertEqual(blits[0][0].atom_id, ref.atom_id)


class TestMediaIndexAndPreview(unittest.TestCase):
    """Faza 6 indeks + Faza 5 decode (Pillow opcjonalne)."""

    def setUp(self) -> None:
        self.store = kernel.Store(thermal=True)

    def test_build_media_index(self) -> None:
        from karmazyn_media_preview import build_media_index

        media.attach_bytes(
            self.store, "A", "portret", b"\x89PNG\r\n\x1a\nxx", mime="image/png"
        )
        media.attach_bytes(
            self.store,
            "A",
            "klip",
            b"Z" * 500,
            mime="video/mp4",
            force_stream=True,
            segment_size=100,
        )
        idx = build_media_index(self.store)
        self.assertGreaterEqual(len(idx), 2)
        ids = {e["id"] for e in idx}
        self.assertEqual(len(ids), len(idx))
        streams = [e for e in idx if e.get("stream")]
        self.assertTrue(streams)
        self.assertGreater(streams[0]["n_segments"], 1)

    def test_missing_media_entries(self) -> None:
        from cynober_replicate import missing_media_entries

        local = [{"id": "a0", "cas12": "aa", "size": 10}]
        remote = [
            {"id": "a0", "cas12": "aa", "size": 10},
            {"id": "a1", "cas12": "bb", "size": 20},
            {"id": "a0", "cas12": "cc", "size": 10},  # cas mismatch → missing
        ]
        miss = missing_media_entries(local, remote)
        miss_ids = {m["id"] for m in miss}
        self.assertIn("a1", miss_ids)
        self.assertIn("a0", miss_ids)  # cas diff

    def test_decode_png_header_optional(self) -> None:
        from karmazyn_media_preview import decode_image_bytes, _HAS_PIL

        # minimal invalid — may fail decode gracefully
        png, w, h, err = decode_image_bytes(b"\x89PNG\r\n\x1a\nnot-real")
        if _HAS_PIL:
            # Pillow may still error
            self.assertTrue(png is None or isinstance(png, bytes))
        else:
            self.assertIsNone(png)
            self.assertIn("Pillow", err)


class TestSoulBlobLimit(unittest.TestCase):
    def setUp(self) -> None:
        self.store = kernel.Store(thermal=True)

    def test_small_blob_inline_by_default(self) -> None:
        small = b"x" * 100
        ref = media.attach_bytes(
            self.store, "A", "ikona", small, mime="image/png"
        )
        doc = serialize_soul(self.store, include_blobs=None)
        atom = next(a for a in doc["atoms"] if a["id"] == ref.atom_id)
        self.assertIn("data_b64", atom["meta"])
        self.assertNotIn("media_ref", atom["meta"])

    def test_large_blob_becomes_media_ref(self) -> None:
        big = b"Y" * (SOUL_MAX_INLINE_BLOB + 1)
        ref = media.attach_bytes(
            self.store, "A", "foto", big, mime="image/jpeg"
        )
        doc = serialize_soul(self.store, include_blobs=None)
        atom = next(a for a in doc["atoms"] if a["id"] == ref.atom_id)
        self.assertNotIn("data_b64", atom["meta"])
        self.assertIn("media_ref", atom["meta"])
        self.assertEqual(atom["meta"]["media_ref"]["size"], len(big))
        self.assertEqual(atom["meta"]["media_ref"]["mime"], "image/jpeg")

    def test_include_blobs_false_always_ref(self) -> None:
        small = b"tiny"
        ref = media.attach_bytes(
            self.store, "A", "i", small, mime="image/png"
        )
        doc = serialize_soul(self.store, include_blobs=False)
        atom = next(a for a in doc["atoms"] if a["id"] == ref.atom_id)
        self.assertNotIn("data_b64", atom["meta"])
        self.assertIn("media_ref", atom["meta"])

    def test_include_blobs_true_always_b64(self) -> None:
        big = b"Z" * (SOUL_MAX_INLINE_BLOB + 50)
        ref = media.attach_bytes(
            self.store, "A", "big", big, mime="application/octet-stream"
        )
        doc = serialize_soul(self.store, include_blobs=True)
        atom = next(a for a in doc["atoms"] if a["id"] == ref.atom_id)
        self.assertIn("data_b64", atom["meta"])
        self.assertNotIn("media_ref", atom["meta"])


if __name__ == "__main__":
    unittest.main()
