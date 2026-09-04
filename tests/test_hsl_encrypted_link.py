"""HSL link/cap — ramki zaszyfrowane (HSL1), nie cleartext JSON."""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
import unittest

from cynober_rpc import PROTO_VERSION, clear_replay_cache
from karmazyn_handshake import _CryptoLayer
from karmazyn_hsl import (
    HSL_VERSION,
    _HSL_AAD_LINK,
    _HSL_WIRE_MAGIC,
    _send_hsl_msg,
    perform_hsl_link,
)


class _CaptureSock:
    """Minimalny sock: zbiera sendall, dokłada do peer bufora."""

    def __init__(self):
        self.peer: _CaptureSock | None = None
        self._buf = bytearray()
        self.sent: list[bytes] = []

    def bind_peer(self, other: "_CaptureSock") -> None:
        self.peer = other
        other.peer = self

    def sendall(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        if self.peer is not None:
            self.peer._buf.extend(data)

    def recv(self, n: int) -> bytes:
        deadline = time.time() + 2.0
        while len(self._buf) < 1 and time.time() < deadline:
            time.sleep(0.001)
        if not self._buf:
            return b""
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def settimeout(self, _t):
        pass

    def close(self):
        pass


class TestHslEncryptedWire(unittest.TestCase):
    def setUp(self):
        clear_replay_cache()

    def test_send_hsl_msg_uses_hsl1_magic_not_json(self):
        crypto = _CryptoLayer()
        crypto._key = b"\xaa" * 32
        crypto._mode = "hss"
        sock = _CaptureSock()
        peer = _CaptureSock()
        sock.bind_peer(peer)
        _send_hsl_msg(
            sock,
            {"type": "hsl_link", "epoch": 1, "node_id": "n", "commit": "ab" * 32, "version": HSL_VERSION},
            crypto,
            aad=_HSL_AAD_LINK,
        )
        self.assertTrue(sock.sent)
        blob = b"".join(sock.sent)
        # length-prefixed frame
        n = struct.unpack(">I", blob[:4])[0]
        body = blob[4 : 4 + n]
        self.assertTrue(body.startswith(_HSL_WIRE_MAGIC))
        self.assertFalse(body[4:].lstrip().startswith(b"{"))
        with self.assertRaises(Exception):
            json.loads(body.decode("utf-8"))

    def test_perform_hsl_link_roundtrip_encrypted(self):
        s_srv, s_cli = socket.socketpair()
        shared = b"\xaa" * 32
        crypto_s = _CryptoLayer()
        crypto_s._key = shared
        crypto_s._mode = "hss"
        crypto_c = _CryptoLayer()
        crypto_c._key = shared
        crypto_c._mode = "hss"
        caps_srv = {
            "node_id": "node_srv",
            "session_id": "enc_s1",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        caps_cli = {
            "node_id": "node_cli",
            "session_id": "enc_s2",
            "version": PROTO_VERSION,
            "hsl": HSL_VERSION,
        }
        deadline = time.monotonic() + 5.0
        err: list = []

        def server():
            try:
                link = perform_hsl_link(
                    s_srv,
                    shared,
                    caps_srv,
                    caps_cli,
                    is_server=True,
                    deadline=deadline,
                    phi2=b"\x11" * 32,
                    crypto=crypto_s,
                    crypto_mode="hss",
                )
                err.append(("ok", link.epoch))
            except Exception as e:
                err.append(("fail", str(e)))

        t = threading.Thread(target=server, daemon=True)
        t.start()
        link = perform_hsl_link(
            s_cli,
            shared,
            caps_cli,
            caps_srv,
            is_server=False,
            deadline=deadline,
            phi2=b"\x22" * 32,
            crypto=crypto_c,
            crypto_mode="hss",
        )
        self.assertGreater(link.epoch, 0)
        t.join(timeout=3)
        self.assertEqual(err[0][0], "ok")
        s_srv.close()
        s_cli.close()


if __name__ == "__main__":
    unittest.main()
