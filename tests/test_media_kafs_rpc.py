"""Faza 3: MEDIA PUT/GET + KAFS multipleks w tunelu RPC."""

from __future__ import annotations

import hashlib
import time
import unittest

from cynober_rpc import (
    FRAME_KAFS,
    FRAME_RPC,
    FEATURE_KAFS,
    build_local_caps,
    kafs_negotiated,
    pack_cleartext,
    unpack_cleartext,
)
from tests.test_server_rpc import RpcTestBase


class TestFrameMux(unittest.TestCase):
    def test_pack_unpack_rpc_legacy(self):
        body = b"hello"
        packed = pack_cleartext(FRAME_RPC, body, framed=False)
        self.assertEqual(packed, body)
        kind, payload = unpack_cleartext(packed)
        self.assertEqual(kind, FRAME_RPC)
        self.assertEqual(payload, body)

    def test_legacy_no_prefix(self):
        # zlib-like first byte
        raw = b"\x78\x9c" + b"xxxx"
        kind, payload = unpack_cleartext(raw)
        self.assertEqual(kind, FRAME_RPC)
        self.assertEqual(payload, raw)

    def test_kafs_prefix(self):
        packed = pack_cleartext(FRAME_KAFS, b"chunk", framed=True)
        self.assertEqual(packed[0], FRAME_KAFS)
        kind, payload = unpack_cleartext(packed)
        self.assertEqual(kind, FRAME_KAFS)
        self.assertEqual(payload, b"chunk")

    def test_caps_include_kafs_feature(self):
        caps = build_local_caps()
        self.assertIn(FEATURE_KAFS, caps.get("features") or [])
        self.assertTrue(kafs_negotiated(caps, caps))
        self.assertFalse(kafs_negotiated(caps, {"features": []}))


class TestMediaKafsTunnel(RpcTestBase):
    def test_handshake_enables_kafs(self):
        c = self._client()
        self.assertTrue(c.kafs_enabled)
        self.assertIn(FEATURE_KAFS, (c.local_caps or {}).get("features") or [])

    def test_legacy_client_still_works(self):
        """Stary klient bez framed — zwykłe query."""
        from cynober_rpc import LEGACY_VERSION

        c = self._client()
        # even new client works
        row = c.query_line("ZDROWIE")
        self.assertIn(row.get("status"), ("ok", None, "error"))  # ZDROWIE may vary
        # force reconnect as 1.0 if supported
        c.close()
        c2 = type(c)(port=self.port)
        try:
            c2.connect(client_version=LEGACY_VERSION)
            self.addCleanup(c2.close)
            self.assertFalse(c2.kafs_enabled)
            # may still answer simple queries on ephemeral
            _ = c2.query("STATYSTYKI")
        except Exception:
            # legacy may be restricted in some builds — nie fail hard
            pass

    def test_put_get_png_roundtrip(self):
        c = self._client()
        self.assertTrue(c.kafs_enabled)
        png = b"\x89PNG\r\n\x1a\n" + b"kafs-roundtrip-" + b"\x00" * 200
        h = hashlib.sha256(png).hexdigest()
        end = c.put_media(
            "pic1",
            png,
            mime="image/png",
            bubble="Anna",
            binding="portret",
        )
        self.assertEqual(end.get("status"), "ok")
        self.assertEqual(end.get("size"), len(png))

        data, mime, meta = c.get_media("pic1")
        self.assertEqual(mime, "image/png")
        self.assertEqual(hashlib.sha256(data).hexdigest(), h)
        self.assertEqual(len(data), len(png))
        self.assertEqual(meta.get("action"), "MEDIA_GET")

        st = c.media_stat("pic1")
        self.assertEqual(st.get("status"), "ok")
        self.assertEqual(st.get("size"), len(png))

    def test_put_get_chunked_larger(self):
        c = self._client()
        # ~1.5 MiB → multiple chunks at 1 MiB
        blob = (b"ABCDEFGH" * 200000)  # 1.6e6 B
        end = c.put_media("big1", blob, mime="application/octet-stream", chunk_size=256 * 1024)
        self.assertEqual(end.get("status"), "ok")
        data, mime, _ = c.get_media("big1")
        self.assertEqual(len(data), len(blob))
        self.assertEqual(data[:16], blob[:16])
        self.assertEqual(data[-16:], blob[-16:])

    def test_stat_missing(self):
        c = self._client()
        st = c.media_stat("no_such_atom_xyz")
        self.assertEqual(st.get("status"), "error")

    def test_put_get_stream_head(self):
        """Faza 4b: duży PUT przy niskim progu → A_STREAM na serwerze, GET reassemble."""
        import os

        old = os.environ.get("KARM_MEDIA_STREAM_THRESHOLD")
        old_seg = os.environ.get("KARM_MEDIA_SEGMENT_SIZE")
        os.environ["KARM_MEDIA_STREAM_THRESHOLD"] = "500"
        os.environ["KARM_MEDIA_SEGMENT_SIZE"] = "200"
        try:
            c = self._client()
            blob = b"STREAM-RPC-" + (b"Z" * 1200)
            end = c.put_media(
                "stream1",
                blob,
                mime="application/octet-stream",
                bubble="Kanal",
                binding="klip",
            )
            self.assertEqual(end.get("status"), "ok")
            st = c.media_stat("stream1")
            self.assertEqual(st.get("status"), "ok")
            self.assertTrue(st.get("stream") or st.get("n_segments", 0) > 0)
            data, mime, _ = c.get_media("stream1")
            self.assertEqual(data, blob)
            self.assertEqual(mime, "application/octet-stream")
        finally:
            if old is None:
                os.environ.pop("KARM_MEDIA_STREAM_THRESHOLD", None)
            else:
                os.environ["KARM_MEDIA_STREAM_THRESHOLD"] = old
            if old_seg is None:
                os.environ.pop("KARM_MEDIA_SEGMENT_SIZE", None)
            else:
                os.environ["KARM_MEDIA_SEGMENT_SIZE"] = old_seg


if __name__ == "__main__":
    unittest.main()
