#!/usr/bin/env python3
"""
karmazyn_cipher.py — P6: AES-256-GCM na dysku (KAFX), klucz per świat.

Tunel Cynober już używa AES-GCM (`karmazyn_handshake._CryptoLayer`).
Ten moduł to ten sam pancerz na plikach .kafd / payloadach dziennika KAFS.

  KAFX1  — koperta całego bloba KAFD (save / compact / seal)
  KX1    — koperta payloadu ramki dziennika (atom.data)

Klucz świata = HKDF-SHA256(master, info="world:<nazwa>").
Master: CYNOBER_MASTER_KEY / KARMAZYN_MASTER_KEY (hex albo surowy),
        plik CYNOBER_MASTER_KEY_FILE, albo `{data_home}/master.key`.

Stary XOR ze stałym seedem (`KARMAZYN_PHI_ROOT_SPACE_V1_SEED`) — TYLKO odczyt.
Nowe zapisy nigdy go nie używają.

KARMAZYN_KAFD_PLAIN=1 — zapis bez koperty (debug).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import struct
from pathlib import Path
from typing import Optional, Tuple

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes as _hashes
    _CRYPTO_OK = True
except ImportError:
    AESGCM = None  # type: ignore
    HKDF = None  # type: ignore
    _hashes = None  # type: ignore
    _CRYPTO_OK = False

KAFX_MAGIC = b"KAFX"
KAFX_VERSION = 1
KAFX_KDF_HKDF = 1
KAFD_MAGIC = b"KAFD"
KAFS_MAGIC = b"KAFS"
KX1_MAGIC = b"KX1\0"
NONCE_LEN = 12
KEY_LEN = 32
HKDF_SALT = b"karmazyn-kafx-v1"
PHI_XOR_SEED = b"KARMAZYN_PHI_ROOT_SPACE_V1_SEED"


def crypto_available() -> bool:
    return bool(_CRYPTO_OK)


def plain_requested() -> bool:
    raw = (os.environ.get("KARMAZYN_KAFD_PLAIN") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def infer_world_from_path(path: str) -> str:
    stem = Path(path).stem
    return stem or "_default"


def _parse_key_material(raw: str) -> bytes:
    s = raw.strip()
    if len(s) == 64:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    if len(s) == 32 and all(ord(c) < 128 for c in s):
        # 32 znaki ASCII — przyjmij jako surowy klucz
        return s.encode("ascii")
    return hashlib.sha256(s.encode("utf-8")).digest()


def _master_key_path() -> Path:
    env = (
        os.environ.get("CYNOBER_MASTER_KEY_FILE")
        or os.environ.get("KARMAZYN_MASTER_KEY_FILE")
        or ""
    ).strip()
    if env:
        return Path(env).expanduser()
    try:
        from cynober_paths import data_home
        return data_home() / "master.key"
    except ImportError:
        local = os.environ.get("LOCALAPPDATA") or ""
        if local:
            return Path(local) / "Cynober" / "master.key"
        return Path.home() / ".local" / "share" / "cynober" / "master.key"


def resolve_master_key(*, create: bool = True) -> bytes:
    env = (
        os.environ.get("CYNOBER_MASTER_KEY")
        or os.environ.get("KARMAZYN_MASTER_KEY")
        or ""
    ).strip()
    if env:
        return _parse_key_material(env)
    path = _master_key_path()
    if path.is_file():
        data = path.read_bytes()
        if len(data) >= KEY_LEN:
            return data[:KEY_LEN]
        return hashlib.sha256(data).digest()
    if not create:
        raise FileNotFoundError(f"brak master key ({path})")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_LEN)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = KEY_LEN) -> bytes:
    if _CRYPTO_OK and HKDF is not None:
        return HKDF(
            algorithm=_hashes.SHA256(),
            length=length,
            salt=salt,
            info=info,
        ).derive(ikm)
    # RFC 5869 extract+expand
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    prev = b""
    counter = 1
    while len(okm) < length:
        prev = hmac.new(prk, prev + info + bytes([counter]), hashlib.sha256).digest()
        okm += prev
        counter += 1
    return okm[:length]


def derive_world_key(world: str, master: Optional[bytes] = None) -> bytes:
    master = master if master is not None else resolve_master_key(create=True)
    name = (world or "_default").encode("utf-8")
    return _hkdf_sha256(master, HKDF_SALT, b"world:" + name, KEY_LEN)


def _aesgcm_encrypt(key: bytes, plaintext: bytes, aad: bytes) -> Tuple[bytes, bytes]:
    if not _CRYPTO_OK or AESGCM is None:
        raise RuntimeError(
            "P6 wymaga pakietu cryptography (AES-256-GCM). "
            "pip install cryptography  albo KARMAZYN_KAFD_PLAIN=1"
        )
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = AESGCM(key[:KEY_LEN]).encrypt(nonce, plaintext, aad)
    return nonce, ct


def _aesgcm_decrypt(key: bytes, nonce: bytes, ct: bytes, aad: bytes) -> bytes:
    if not _CRYPTO_OK or AESGCM is None:
        raise RuntimeError("P6 wymaga pakietu cryptography (AES-256-GCM)")
    try:
        return AESGCM(key[:KEY_LEN]).decrypt(nonce, ct, aad)
    except Exception as e:
        raise ValueError("KAFX: zły klucz, zły świat albo uszkodzony plik") from e


def wrap_kafx(plaintext: bytes, world: str, *, master: Optional[bytes] = None) -> bytes:
    """Koperta pliku: KAFX1 + świat + nonce + AES-GCM(KAFD)."""
    world = world or "_default"
    key = derive_world_key(world, master)
    w = world.encode("utf-8")
    if len(w) > 65535:
        raise ValueError("nazwa świata za długa")
    aad = KAFX_MAGIC + bytes([KAFX_VERSION, KAFX_KDF_HKDF]) + w
    nonce, ct = _aesgcm_encrypt(key, plaintext, aad)
    hdr = bytearray()
    hdr += KAFX_MAGIC
    hdr += bytes([KAFX_VERSION, KAFX_KDF_HKDF])
    hdr += struct.pack(">H", 0)  # flags
    hdr += struct.pack(">H", len(w))
    hdr += w
    hdr += nonce
    hdr += ct
    return bytes(hdr)


def unwrap_kafx(blob: bytes, *, world: Optional[str] = None,
                master: Optional[bytes] = None) -> bytes:
    if len(blob) < 10 + NONCE_LEN or blob[:4] != KAFX_MAGIC:
        raise ValueError("to nie jest koperta KAFX")
    ver, kdf = blob[4], blob[5]
    if ver != KAFX_VERSION:
        raise ValueError(f"KAFX: nieznana wersja {ver}")
    if kdf != KAFX_KDF_HKDF:
        raise ValueError(f"KAFX: nieznany KDF {kdf}")
    wlen = struct.unpack(">H", blob[8:10])[0]
    off = 10
    wbytes = blob[off:off + wlen]
    off += wlen
    if len(blob) < off + NONCE_LEN + 16:
        raise ValueError("KAFX: za krótki blob")
    stored_world = wbytes.decode("utf-8", errors="replace")
    if world and world != stored_world:
        raise ValueError(
            f"KAFX: świat w pliku {stored_world!r} ≠ żądany {world!r}"
        )
    nonce = blob[off:off + NONCE_LEN]
    ct = blob[off + NONCE_LEN:]
    aad = KAFX_MAGIC + bytes([ver, kdf]) + wbytes
    key = derive_world_key(stored_world, master)
    return _aesgcm_decrypt(key, nonce, ct, aad)


def wrap_payload(plaintext: bytes, world: str, aad: bytes,
                 *, master: Optional[bytes] = None) -> bytes:
    """Koperta payloadu ramki KAFS (KX1)."""
    key = derive_world_key(world, master)
    nonce, ct = _aesgcm_encrypt(key, plaintext, aad)
    return KX1_MAGIC + nonce + ct


def unwrap_payload(blob: bytes, world: str, aad: bytes,
                   *, master: Optional[bytes] = None) -> bytes:
    if not blob.startswith(KX1_MAGIC) or len(blob) < 4 + NONCE_LEN + 16:
        raise ValueError("to nie jest koperta KX1")
    nonce = blob[4:4 + NONCE_LEN]
    ct = blob[4 + NONCE_LEN:]
    key = derive_world_key(world, master)
    return _aesgcm_decrypt(key, nonce, ct, aad)


def is_kafx(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == KAFX_MAGIC


def is_kx1(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == KX1_MAGIC


def is_plain_kafd(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == KAFD_MAGIC


def is_plain_kafs(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == KAFS_MAGIC


def legacy_phi_xor(data: bytes) -> bytes:
    """Historyczny XOR SHA-256-CTR ze stałym seedem — tylko odczyt starych .kafd."""
    out = bytearray(len(data))
    key_hash = hashlib.sha256(PHI_XOR_SEED).digest()
    counter = 0
    for i in range(0, len(data), 32):
        block = data[i:i + 32]
        stream = hashlib.sha256(key_hash + counter.to_bytes(8, "big")).digest()
        for j in range(len(block)):
            out[i + j] = block[j] ^ stream[j]
        counter += 1
    return bytes(out)


def open_stored_bytes(data: bytes, *, world: Optional[str] = None,
                      master: Optional[bytes] = None) -> Tuple[bytes, str]:
    """Zwraca (plaintext, tryb: kafx|plain|xor-legacy)."""
    if is_kafx(data):
        return unwrap_kafx(data, world=world, master=master), "kafx"
    if is_plain_kafd(data) or is_plain_kafs(data):
        return data, "plain"
    xor = legacy_phi_xor(data)
    if is_plain_kafd(xor) or is_plain_kafs(xor):
        return xor, "xor-legacy"
    raise ValueError("nierozpoznany blob (ani KAFX, ani KAFD/KAFS, ani stary XOR)")


def read_stored_file(path: str, *, world: Optional[str] = None,
                     master: Optional[bytes] = None) -> Tuple[bytes, str]:
    with open(path, "rb") as f:
        data = f.read()
    return open_stored_bytes(data, world=world, master=master)


def encrypt_for_path(plaintext: bytes, path: str, *,
                     world: Optional[str] = None,
                     master: Optional[bytes] = None,
                     encrypt: Optional[bool] = None) -> bytes:
    if encrypt is None:
        encrypt = not plain_requested()
    if not encrypt:
        return plaintext
    w = world if world is not None else infer_world_from_path(path)
    return wrap_kafx(plaintext, w, master=master)
