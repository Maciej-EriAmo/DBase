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

# document/version — treść tekstowa; media — Faza 0 multimedia; __bubble__ — bindings
# media = payload single / stream head; media_seg = chunk A_STREAM (Faza 4)
DOC_KINDS = ("document", "version", "__bubble__", "media", "media_seg")
STORE_META = "karmazyn_store_v1.4_kafx"
FOLDED_META_KEY = "_folded"
FOLD_SRC_KEY = "_fold_src"

# ─── Kryptografia pliku (P6: AES-GCM / KAFX; XOR tylko odczyt legacy) ────────

def _get_system_phi_key() -> bytes:
    """Legacy: seed starego XOR. Nie używany przy nowym zapisie."""
    from karmazyn_cipher import PHI_XOR_SEED
    return hashlib.sha256(PHI_XOR_SEED).digest()


def _apply_phi_cipher(data: bytes) -> bytes:
    """DEPRECATED — stary XOR. Zostawiony dla narzędzi/testów legacy."""
    from karmazyn_cipher import legacy_phi_xor
    return legacy_phi_xor(data)


def _read_decrypted_kafd(path: str, world: Optional[str] = None) -> bytes:
    """Odczytaj .kafd: KAFX → AES-GCM; KAFD → plain; inaczej XOR-legacy."""
    from karmazyn_cipher import read_stored_file
    blob, _mode = read_stored_file(path, world=world)
    return blob


# ─── ADAPTER DIALEKTU MAGAZYNU (szew D2: Store vs PhiSpace) ──────────────────
# karmazyn_store bywa wołany z dwoma różnymi powierzchniami:
#   • PhiSpace     — .matrix.atoms() oraz create_atom(id, ...)
#   • natywny Store — .atoms() / create_atom(id, ...) / get_atom (bez .reg)
# Preferuj publiczne API Store; .reg tylko jako ostatni fallback legacy
# (dostęp do Store.reg emituje UserWarning — P6).

def _legacy_reg(phi):
    """Rejestr tylko gdy brak publicznego API (PhiSpace / stare adaptery)."""
    if callable(getattr(phi, "atoms", None)) or callable(getattr(phi, "create_atom", None)):
        return None
    if callable(getattr(phi, "get_atom", None)):
        return None
    return getattr(phi, "reg", None)


def _get_atom(phi, aid):
    """Pobierz atom po id — get_atom, potem legacy reg (bez reg na Store)."""
    if callable(getattr(phi, "get_atom", None)):
        return phi.get_atom(aid)
    reg = _legacy_reg(phi)
    if reg is not None and hasattr(reg, "get"):
        return reg.get(aid)
    return None


def _iter_atoms(phi):
    """Zwróć iterowalną kolekcję atomów niezależnie od dialektu magazynu."""
    m = getattr(phi, "matrix", None)
    if m is not None and hasattr(m, "atoms"):
        return m.atoms()
    if callable(getattr(phi, "atoms", None)):
        return phi.atoms()
    reg = _legacy_reg(phi)
    if reg is not None and hasattr(reg, "atoms"):
        return reg.atoms()
    raise AttributeError(
        "Magazyn nie udostępnia atoms()/matrix.atoms() (ani legacy reg.atoms)")


def _make_atom(phi, aid, S, E, T):
    """Utwórz atom z JAWNYM id — konieczne, bo bąble odwołują się do atomów po id.
    Natywny Store.atom_new() sam generuje id i NIE nadaje się do wczytywania;
    używamy create_atom(id, ...) (AtomStore). Legacy: reg.create."""
    if callable(getattr(phi, "create_atom", None)):
        # AtomStore.create_atom -> id (str) lub Atom
        created = phi.create_atom(aid, S=S, E=E, T=T)
        if isinstance(created, str):
            atom = _get_atom(phi, created)
            if atom is None:
                raise AttributeError(
                    f"create_atom({aid!r}) zwróciło id, ale get_atom nie znalazł atomu")
            return atom
        return created
    reg = _legacy_reg(phi)
    if reg is not None and hasattr(reg, "create"):
        return reg.create(aid, S=S, E=E, T=T)
    raise AttributeError(
        "Magazyn nie potrafi utworzyć atomu z jawnym id (brak create_atom)")


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
                   proca_cold_only: bool = False,
                   world: Optional[str] = None,
                   encrypt: Optional[bool] = None) -> int:
    """Zapisz atomy. Domyślnie koperta KAFX (AES-GCM, klucz świata)."""
    return save_documents_filtered(
        phi,
        path,
        kinds=kinds,
        proca_index=proca_index,
        proca_cold_only=proca_cold_only,
        world=world,
        encrypt=encrypt,
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
    world: Optional[str] = None,
    encrypt: Optional[bool] = None,
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

    from karmazyn_cipher import encrypt_for_path, infer_world_from_path, plain_requested
    from karmazyn_kafd import F_ENCRYPTED, F_PHI_NATIVE

    mark = encrypt if encrypt is not None else (not plain_requested())
    wname = world if world is not None else infer_world_from_path(path)
    flags = F_PHI_NATIVE | (F_ENCRYPTED if mark else 0)
    blob = vfs_pack(atoms_dict, meta, flags=flags)
    encrypted_blob = encrypt_for_path(blob, path, world=wname, encrypt=mark)
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

    # media / media_seg: nigdy nie zwijaj (lore-editor Dołącz plik + KAFS).
    # T mediów jest zwykle < T_HOT — lazy fold kasował payload i lista/podgląd
    # wracały puste mimo poprawnego attach.
    fold = (
        fold_below_T is not None
        and S != "__bubble__"
        and S not in ("media", "media_seg")
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
    world: Optional[str] = None,
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
        blob = _read_decrypted_kafd(path, world=world)
    except (RuntimeError, ValueError, OSError):
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


def load_folded_atoms(phi, path: str, atom_ids: set[str], proca_index=None,
                      world: Optional[str] = None) -> int:
    """Dociągnij payload zwiniętych atomów z pliku .kafd."""
    if not atom_ids or not _KAFD_OK or not os.path.exists(path):
        return 0

    from karmazyn_kafd import KAFDReader

    try:
        blob = _read_decrypted_kafd(path, world=world)
    except (RuntimeError, ValueError, OSError):
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
        atom = _get_atom(phi, aid)
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


def load_documents(phi, path: str, proca_index=None,
                   world: Optional[str] = None) -> int:
    """Wczytaj wszystkie atomy (pełny load, bez zwijania)."""
    if not _KAFD_OK:
        raise RuntimeError("Brak karmazyn_kafd — nie można odczytać z dysku")
    if not os.path.exists(path):
        return 0

    try:
        decrypted_blob = _read_decrypted_kafd(path, world=world)
    except (RuntimeError, ValueError, OSError):
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
        from karmazyn_cipher import open_stored_bytes
        decrypted_blob, mode = open_stored_bytes(blob)
        atoms_dict, meta = vfs_unpack(decrypted_blob)
        return {"exists": True, "atoms": len(atoms_dict),
                "size": len(blob), "meta": meta, "encrypted": mode != "plain",
                "cipher": mode}
    except Exception as e:
        return {"exists": True, "error": str(e)}