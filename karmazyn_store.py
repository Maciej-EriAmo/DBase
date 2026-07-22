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
from typing import Any, Iterable, Optional

try:
    from karmazyn_kafd import vfs_pack, vfs_unpack
    _KAFD_OK = True
except ImportError:
    _KAFD_OK = False

DOC_KINDS = ("document", "version", "__bubble__")
STORE_META = "karmazyn_store_v1.3_encrypted"
FOLDED_META_KEY = "_folded"
FOLD_SRC_KEY = "_fold_src"

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
        # AtomStore.create_atom -> id (str); reg.create -> Atom
        created = phi.create_atom(aid, S=S, E=E, T=T)
        if isinstance(created, str):
            atom = phi.get_atom(created) if callable(getattr(phi, "get_atom", None)) else None
            if atom is None:
                raise AttributeError(
                    f"create_atom({aid!r}) zwróciło id, ale get_atom nie znalazł atomu")
            return atom
        return created
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

def _read_decrypted_kafd(path: str) -> bytes:
    """Odczytaj plik .kafd i zwróć odszyfrowany blob KAFD."""
    with open(path, "rb") as f:
        encrypted_blob = f.read()
    try:
        return _apply_phi_cipher(encrypted_blob)
    except Exception as e:
        raise RuntimeError(f"Błąd kryptograficzny (uszkodzony plik lub nieznany klucz): {e}") from e


def _resolve_payload_data(data: bytes, aid: str, proca_index) -> bytes:
    if not data or proca_index is None:
        return data
    try:
        from karmazyn_proca import ProcaCoordinate

        if ProcaCoordinate.is_proca_json(data):
            coord = ProcaCoordinate.from_json_bytes(data, aid)
            resolved = proca_index.resolve_coordinate(coord)
            if resolved is not None:
                return resolved
    except Exception:
        pass
    return data


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


def _atom_phi_vector(phi, atom) -> Optional[Any]:
    vec = getattr(atom, "vector", None)
    if vec is not None and len(vec) > 0:
        return vec
    av = getattr(phi, "atom_vector", None)
    if callable(av):
        return av(atom)
    return None


def save_documents(phi, path: str,
                   kinds: Iterable[str] = DOC_KINDS,
                   proca_index=None,
                   proca_cold_only: bool = False) -> int:
    """Zapisz atomy z opcjonalną deduplikacją ProcaIndex i szyfrowaniem Phi."""
    return save_documents_filtered(
        phi,
        path,
        kinds=kinds,
        proca_index=proca_index,
        proca_cold_only=proca_cold_only,
    )


def save_documents_filtered(
    phi,
    path: str,
    *,
    kinds: Iterable[str] = DOC_KINDS,
    proca_index=None,
    proca_cold_only: bool = False,
    atom_filter=None,
    include_payload=None,
) -> int:
    """
    Zapisz podzbiór atomów. include_payload(atom)->bool steruje payloadem
    (False = sam nagłówek, bez data).
    """
    if not _KAFD_OK:
        raise RuntimeError("Brak karmazyn_kafd — nie można zapisać na dysk")

    from karmazyn_atom import T_HOT

    kinds = tuple(kinds)
    atoms_dict = {}
    for atom in _iter_atoms(phi):
        if atom_filter is not None and not atom_filter(atom):
            continue
        if _is_doc_atom(atom, kinds):
            override_data = None
            data = atom.metadata.get("data", b"")
            strip_payload = include_payload is not None and not include_payload(atom)

            if not strip_payload and proca_index and len(data) > 0:
                use_proca = not proca_cold_only or float(atom.T) < T_HOT
                if use_proca:
                    phi_vec = _atom_phi_vector(phi, atom)
                    typ, res = proca_index.register_or_deduplicate(
                        atom.id, data, phi_vec, float(atom.T)
                    )
                    if typ == "coordinate":
                        override_data = res.to_json_bytes()

            if strip_payload:
                atoms_dict[atom.id] = _encode_atom(atom, override_data=b"")
            else:
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


def _ingest_atom_bytes(
    phi,
    aid: str,
    atom_bytes: bytes,
    *,
    proca_index=None,
    fold_below_T: float | None = None,
    fold_src: str = "",
) -> tuple[bool, bool]:
    """
    Zarejestruj atom z bajtów KAFD store.
    Zwraca (utworzono, zwinięty).
    """
    from karmazyn_atom import T_HOT

    try:
        head, data = _decode_atom(atom_bytes)
    except Exception:
        return False, False

    S = head.get("S", "")
    T = float(head.get("T", 50.0))
    atom = _make_atom(phi, aid, S, head.get("E", ""), T)
    m = head.get("meta", {})
    if isinstance(m, dict):
        atom.metadata.update(m)

    fold = (
        fold_below_T is not None
        and S != "__bubble__"
        and T < fold_below_T
        and len(data) > 0
    )
    if fold:
        atom.metadata[FOLDED_META_KEY] = True
        if fold_src:
            atom.metadata[FOLD_SRC_KEY] = fold_src
        return True, True

    if data:
        atom.metadata["data"] = _resolve_payload_data(data, aid, proca_index)
    return True, False


def load_documents_lazy(
    phi,
    path: str,
    proca_index=None,
    *,
    fold_below_T: float | None = None,
) -> tuple[int, set[str]]:
    """
    Wczytaj manifest: nagłówki wszystkich atomów; payload tylko HOT / __bubble__.
    Zwraca (liczba atomów, zbiór zwiniętych id).
    """
    if not _KAFD_OK:
        raise RuntimeError("Brak karmazyn_kafd — nie można odczytać z dysku")
    if not os.path.exists(path):
        return 0, set()

    from karmazyn_atom import T_HOT
    from karmazyn_kafd import KAFDReader

    if fold_below_T is None:
        fold_below_T = T_HOT

    try:
        blob = _read_decrypted_kafd(path)
    except RuntimeError:
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except OSError:
            return 0, set()

    reader = KAFDReader(blob)
    if not reader._valid:
        return 0, set()

    folded: set[str] = set()
    n = 0
    for aid in reader.atom_ids:
        entry = reader.get_entry(aid)
        if entry is None:
            continue
        raw = reader._get_data(entry)
        ok, is_folded = _ingest_atom_bytes(
            phi,
            aid,
            raw,
            proca_index=proca_index,
            fold_below_T=fold_below_T,
            fold_src=path,
        )
        if ok:
            n += 1
            if is_folded:
                folded.add(aid)
    return n, folded


def load_folded_atoms(phi, path: str, atom_ids: set[str], proca_index=None) -> int:
    """Dociągnij payload zwiniętych atomów z pliku .kafd."""
    if not atom_ids or not _KAFD_OK or not os.path.exists(path):
        return 0

    from karmazyn_kafd import KAFDReader

    try:
        blob = _read_decrypted_kafd(path)
    except RuntimeError:
        with open(path, "rb") as f:
            blob = f.read()

    reader = KAFDReader(blob)
    loaded = 0
    for aid in atom_ids:
        entry = reader.get_entry(aid)
        if entry is None:
            continue
        try:
            head, data = _decode_atom(reader._get_data(entry))
        except Exception:
            continue
        atom = phi.get_atom(aid) if hasattr(phi, "get_atom") else None
        reg = getattr(phi, "reg", None)
        if atom is None and reg is not None:
            atom = reg.get(aid)
        if atom is None:
            continue
        if data:
            atom.metadata["data"] = _resolve_payload_data(data, aid, proca_index)
        atom.metadata.pop(FOLDED_META_KEY, None)
        atom.metadata.pop(FOLD_SRC_KEY, None)
        loaded += 1
    return loaded


def load_folded_atoms_multi(
    phi,
    atom_paths: dict[str, str],
    proca_index=None,
) -> int:
    """Dociągnij zwinięte atomy z wielu plików .kafd (shardy)."""
    if not atom_paths:
        return 0
    by_path: dict[str, set[str]] = {}
    for aid, path in atom_paths.items():
        by_path.setdefault(path, set()).add(aid)
    total = 0
    for path, aids in by_path.items():
        total += load_folded_atoms(phi, path, aids, proca_index=proca_index)
    return total


def load_documents(phi, path: str, proca_index=None) -> int:
    """Wczytaj wszystkie atomy (pełny load, bez zwijania)."""
    if not _KAFD_OK:
        raise RuntimeError("Brak karmazyn_kafd — nie można odczytać z dysku")
    if not os.path.exists(path):
        return 0

    try:
        decrypted_blob = _read_decrypted_kafd(path)
    except RuntimeError:
        with open(path, "rb") as f:
            decrypted_blob = f.read()

    try:
        atoms_dict, _meta = vfs_unpack(decrypted_blob)
    except Exception:
        try:
            with open(path, "rb") as f:
                atoms_dict, _meta = vfs_unpack(f.read())
        except Exception:
            return 0

    n = 0
    for aid, atom_bytes in atoms_dict.items():
        ok, _ = _ingest_atom_bytes(
            phi, aid, atom_bytes, proca_index=proca_index, fold_below_T=None
        )
        if ok:
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