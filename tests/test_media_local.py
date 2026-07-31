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
