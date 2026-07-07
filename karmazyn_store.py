"""
karmazyn_store.py — Trwałość dokumentów KarmazynOS v1.3 (Z Kryptografią Phi)
=============================================================================
KarmazynOS — Warsaw 2026

Zapisuje dokumenty na dysk fizyczny w formacie KAFD.
Wspiera kompresję semantyczną w locie (Proca Index) oraz
transparentne, strumieniowe szyfrowanie (Phi-Cipher), czyniąc 
zrzuty bazy nieczytelnymi dla zewnętrznych edytorów tekstowych.
"""

import json
import os
import struct
import tempfile
import time
import hashlib
from typing import Iterable, Optional

try:
    from karmazyn_kafd import vfs_pack, vfs_unpack
    _KAFD_OK = True
except ImportError:
    _KAFD_OK = False

DOC_KINDS = ("document", "version", "__bubble__")
STORE_META = "karmazyn_store_v1.3_encrypted"

# ─── KRYPTOGRAFIA PHI (Transparentny Szyfr Strumieniowy) ────────────────────

def _get_system_phi_key() -> bytes:
    """
    Generuje wektor klucza z fundamentalnego rezonansu systemu.
    W przyszłości modyfikowany wektorem HRR podanym przez użytkownika (hasłem).
    """
    semantic_root = b"KARMAZYN_PHI_ROOT_SPACE_V1_SEED"
    return hashlib.sha256(semantic_root).digest()

def _apply_phi_cipher(data: bytes) -> bytes:
    """
    Strumieniowe szyfrowanie/deszyfrowanie (XOR) za pomocą SHA-256 w trybie CTR.
    Operacja jest symetryczna - ponowne nałożenie szyfru odwraca proces.
    Nie wymaga zewnętrznych bibliotek (jak 'cryptography').
    """
    out = bytearray(len(data))
    key_hash = _get_system_phi_key()
    counter = 0
    
    # Przetwarzanie w blokach 32-bajtowych
    for i in range(0, len(data), 32):
        block = data[i:i+32]
        # Generowanie strumienia klucza dla danego bloku
        stream = hashlib.sha256(key_hash + counter.to_bytes(8, 'big')).digest()
        
        for j in range(len(block)):
            out[i+j] = block[j] ^ stream[j]
        counter += 1
        
    return bytes(out)


# ─── ADAPTER DIALEKTU MAGAZYNU (szew D2: Store vs PhiSpace) ──────────────────
# karmazyn_store bywa wołany z dwoma różnymi powierzchniami:
#   • PhiSpace     — .matrix.atoms() oraz create_atom(id, ...)
#   • natywny Store — .atoms() oraz atom_new()/reg.create(id, ...)
# Poniższe resolwery pozwalają obsłużyć oba bez zakładania konkretnej klasy.

def _iter_atoms(phi):
    """Zwróć iterowalną kolekcję atomów niezależnie od dialektu magazynu."""
    m = getattr(phi, "matrix", None)
    if m is not None and hasattr(m, "atoms"):
        return m.atoms()
    if callable(getattr(phi, "atoms", None)):
        return phi.atoms()
    reg = getattr(phi, "reg", None)
    if reg is not None and hasattr(reg, "atoms"):
        return reg.atoms()
    raise AttributeError(
        "Magazyn nie udostępnia atoms()/matrix.atoms()/reg.atoms()")


def _make_atom(phi, aid, S, E, T):
    """Utwórz atom z JAWNYM id — konieczne, bo bąble odwołują się do atomów po id.
    Natywny Store.atom_new() sam generuje id i NIE nadaje się do wczytywania,
    dlatego dla Store używamy rejestru (reg.create), który zachowuje id."""
    if callable(getattr(phi, "create_atom", None)):
        return phi.create_atom(aid, S=S, E=E, T=T)
    reg = getattr(phi, "reg", None)
    if reg is not None and hasattr(reg, "create"):
        return reg.create(aid, S=S, E=E, T=T)
    raise AttributeError(
        "Magazyn nie potrafi utworzyć atomu z jawnym id (brak create_atom/reg.create)")


# ─── SERIALIZACJA ATOMÓW ────────────────────────────────────────────────────

def _encode_atom(atom, override_data: Optional[bytes] = None) -> bytes:
    meta = {k: v for k, v in atom.metadata.items() if k != "data"}
    head = json.dumps(
        {"S": atom.S, "E": atom.E, "T": float(atom.T), "meta": meta},
        ensure_ascii=False,
    ).encode("utf-8")
    
    if override_data is not None:
        data = override_data
    else:
        data = atom.metadata.get("data", b"")
        
    if not isinstance(data, (bytes, bytearray)):
        data = b""
    return struct.pack(">I", len(head)) + head + bytes(data)


def _decode_atom(blob: bytes):
    hlen = struct.unpack(">I", blob[:4])[0]
    head = json.loads(blob[4:4 + hlen].decode("utf-8"))
    data = blob[4 + hlen:]
    return head, data


def _is_doc_atom(atom, kinds: Iterable[str]) -> bool:
    S = atom.S or ""
    if S in kinds:
        return True
    if S.startswith("content:"):
        return True
    return False


# ─── ATOMOWY ZAPIS I/O ──────────────────────────────────────────────────────

def _atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".store_", suffix=".kafd_part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass


def save_documents(phi, path: str,
                   kinds: Iterable[str] = DOC_KINDS,
                   proca_index=None) -> int:
    """Zapisz atomy z opcjonalną deduplikacją ProcaIndex i szyfrowaniem Phi."""
    if not _KAFD_OK:
        raise RuntimeError("Brak karmazyn_kafd — nie można zapisać na dysk")

    kinds = tuple(kinds)
    atoms_dict = {}
    for atom in _iter_atoms(phi):
        if _is_doc_atom(atom, kinds):
            override_data = None
            data = atom.metadata.get("data", b"")
            
            # Deduplikacja Proca
            if proca_index and len(data) > 0:
                phi_vec = getattr(atom, 'vector', None)
                typ, res = proca_index.register_or_deduplicate(atom.id, data, phi_vec, float(atom.T))
                if typ == "coordinate":
                    override_data = res.to_json_bytes()

            atoms_dict[atom.id] = _encode_atom(atom, override_data)

    if proca_index:
        proca_index.save_all_sources()

    meta = {"format": STORE_META, "saved": time.time(), "count": len(atoms_dict)}
    
    # 1. Kompresja KAFD
    blob = vfs_pack(atoms_dict, meta)
    # 2. Transparentne szyfrowanie Phi
    encrypted_blob = _apply_phi_cipher(blob)
    # 3. Zapis
    _atomic_write(path, encrypted_blob)
    
    return len(atoms_dict)


def load_documents(phi, path: str) -> int:
    """Wczytaj atomy (z automatycznym deszyfrowaniem Phi)."""
    if not _KAFD_OK:
        raise RuntimeError("Brak karmazyn_kafd — nie można odczytać z dysku")
    if not os.path.exists(path):
        return 0

    with open(path, "rb") as f:
        encrypted_blob = f.read()

    # Deszyfrowanie w locie przed przekazaniem do rozpakowania
    try:
        decrypted_blob = _apply_phi_cipher(encrypted_blob)
    except Exception as e:
        raise RuntimeError(f"Błąd kryptograficzny (uszkodzony plik lub nieznany klucz): {e}")

    try:
        atoms_dict, _meta = vfs_unpack(decrypted_blob)
    except Exception:
        # Fallback na stary, nieszyfrowany format, gdybyśmy wczytywali stare zrzuty
        try:
            atoms_dict, _meta = vfs_unpack(encrypted_blob)
        except Exception:
            return 0

    n = 0
    for aid, atom_bytes in atoms_dict.items():
        try:
            head, data = _decode_atom(atom_bytes)
        except Exception:
            continue
        atom = _make_atom(phi, aid, head.get("S", ""),
                          head.get("E", ""),
                          float(head.get("T", 50.0)))
        m = head.get("meta", {})
        if isinstance(m, dict):
            atom.metadata.update(m)
        if data:
            atom.metadata["data"] = data
        n += 1
    return n


def store_stats(path: str) -> dict:
    if not os.path.exists(path):
        return {"exists": False}
    try:
        with open(path, "rb") as f:
            blob = f.read()
        decrypted_blob = _apply_phi_cipher(blob)
        atoms_dict, meta = vfs_unpack(decrypted_blob)
        return {"exists": True, "atoms": len(atoms_dict),
                "size": len(blob), "meta": meta, "encrypted": True}
    except Exception as e:
        return {"exists": True, "error": str(e)}