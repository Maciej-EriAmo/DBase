"""Minimalny klient RPC Cynober-Secure do testów integracyjnych."""

import json
import socket
import time

from cynober_rpc import (
    LEGACY_VERSION,
    LEGACY_VERSION_11,
    PROTO_VERSION,
    RPC_TIMEOUT_SEC,
    HS_TIMEOUT_SEC,
    SUPPORTED_VERSIONS,
    decrypt_rpc_response,
    encrypt_rpc_request,
    perform_handshake,
)
from karmazyn_handshake import (
    _CryptoLayer,
    _compress,
    _decompress,
    _recv_frame,
    _send_frame,
)


class CynoberRpcClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 8080, timeout: float = RPC_TIMEOUT_SEC):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.crypto = _CryptoLayer()
        self.crypto_mode: str | None = None
        self.hsl_link = None

    def connect(self, client_version: str = PROTO_VERSION) -> None:
        if client_version not in SUPPORTED_VERSIONS:
            raise ConnectionError(f"Nieobsługiwana wersja klienta: {client_version!r}")

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(HS_TIMEOUT_SEC)
        self.sock.connect((self.host, self.port))

        hs_deadline = time.monotonic() + HS_TIMEOUT_SEC
        try:
            self.crypto_mode, _, _, self.hsl_link = perform_handshake(
                self.sock,
                self.crypto,
                is_server=False,
                deadline=hs_deadline,
                client_version=client_version,
            )
        except (ConnectionError, ConnectionResetError, OSError) as e:
            raise ConnectionError(
                "Serwer odrzucił połączenie — sprawdź wersję protokołu."
            ) from e
        except RuntimeError as e:
            raise ConnectionError(str(e)) from e

        self.sock.settimeout(self.timeout)

    def query(self, text: str) -> dict:
        if not self.sock:
            raise RuntimeError("Nie połączono — wywołaj connect()")
        req_blob = json.dumps({"query": text}, ensure_ascii=False).encode("utf-8")
        enc_req = encrypt_rpc_request(self.crypto, _compress(req_blob), self.hsl_link)
        _send_frame(self.sock, enc_req)

        enc_resp = _recv_frame(self.sock)
        if not enc_resp:
            raise ConnectionResetError("Pusta odpowiedź — serwer zamknął tunel")
        raw_resp = _decompress(
            decrypt_rpc_response(self.crypto, enc_resp, self.hsl_link)
        )
        return json.loads(raw_resp.decode("utf-8"))

    def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None