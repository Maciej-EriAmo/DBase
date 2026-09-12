#!/usr/bin/env python3
"""
karmazyn_kafd.py -- KAFD v2.0 (Karmazyn Atom Flow Datum)
=========================================================
Maciej Mazur, Warsaw 2026

KAFD nie jest formatem pliku.
KAFD jest protokolem przeplywu informacji.

Kazda informacja w KarmazynOS -- obraz, dzwiek, tekst, atom phi-space,
wynik komendy, pakiet sieciowy, snapshot klastra -- jest strumieniem KAFD.
"Plik" to jeden z mozliwych nosnikow. Pipe, RAM, socket -- rowniez.

Filozofia:
  Producent            --> [KAFD stream] --> Konsument
  shell.py             --> [KAFD stream] --> karmazyn_display.py
  karmazyn_cluster.py  --> [KAFD stream] --> inny wezel
  kafd_tool            --> [KAFD stream] --> mpv / ffplay / eog
  phi-space snapshot   --> [KAFD stream] --> BubbleVFS

Zasady:
  1. Temperatura jest pierwszorzedna -- kazdy atom ma T w naglowku
  2. Seek i stream sa rownorzedne -- format dziala w obu trybach
  3. Jeden format dla wszystkiego -- nie ma "formatu obrazu" i "formatu audio"
  4. Zewnetrzne narzedzia dostaja natywny format przez kafd_tool transform
  5. CAS hash w tabeli atomow -- dedup bez dodatkowych warstw

Struktura binarna KAFD v2.0 (layout=table_first):
  [HEADER: 64B]     -- staly, natychmiastowy seek
  [ATOM_TABLE: N*56B] -- binarne wyszukiwanie po hash
  [ID_POOL: var]    -- spakowane stringi ID atomow
  [META_JSON: var]  -- metadane kolekcji (babelmetadata)
  [PAYLOAD: var]    -- dane atomow, wyrownanie 8B

KAFD v2.1 (layout=footer, F_FOOTER_TOC):
  [HEADER 64B][PAYLOAD][ATOM_TABLE][ID_POOL][META][KTIX tick index]
  Dziennik: KAFS append (KAFDJournal) → compact_journal() → v2.1.

Naglowek (64B):
  00  MAGIC[4]       = "KAFD"
  04  VERSION[2]     = 0x0200
  06  FLAGS[2]       = encrypted|compressed|signed|streaming|phi_native
  08  ATOM_COUNT[4]
  12  CREATED[8]     = unix timestamp uint64
  20  TABLE_OFF[8]   = zawsze 64
  28  META_OFF[8]    = offset do META_JSON
  36  META_SIZE[4]
  40  PAYLOAD_OFF[8] = offset do payload
  48  PAYLOAD_SIZE[8]
  56  PHI_T[4]       = float32 srednia temperatura kolekcji
  60  RESERVED[4]

Wpis tabeli atomow (56B):
  00  ID_HASH[8]     = xxhash64(atom_id) -- szybkie wyszukiwanie
  08  ID_OFF[4]      = offset w ID_POOL
  12  ID_LEN[2]      = dlugosc ID w bajtach
  14  MIME_IDX[2]    = indeks w slowniku MIME (w META_JSON)
  16  DATA_OFF[8]    = offset w PAYLOAD
  24  DATA_SIZE[8]   = rozmiar danych
  32  T[4]           = float32 temperatura atomu
  36  T_MAX[4]       = float32 max temperatura
  40  STATE[1]       = 0=COLD 1=WARM 2=HOT 3=TOMB
  41  ATYPE[1]       = 0=raw 1=manifest 2=stream_head 3=phi_atom
  42  CAS_HASH[12]   = pierwsze 12B sha256 dla dedup
  54  RESERVED[2]
"""

import hashlib
import json
import mmap
import zlib
import math
import os
import struct
import time
import threading
from io import BytesIO
from typing import Any, Dict, Iterator, List, Optional, Tuple


# ── Stale formatu ─────────────────────────────────────────────────────────────

MAGIC        = b"KAFD"
VERSION      = 0x0200
VERSION_V21  = 0x0201
HEADER_SIZE  = 64
ATOM_ENTRY   = 56
PAYLOAD_ALIGN= 8
TICK_INDEX_MAGIC = b"KTIX"
TICK_INDEX_ENTRY = 40   # tick, data_off, data_size, T_min, T_max, kind, pad7

# FLAGS bits
F_ENCRYPTED  = 0x0001
F_COMPRESSED = 0x0002
F_SIGNED     = 0x0004
F_STREAMING  = 0x0008
F_PHI_NATIVE = 0x0010   # zawiera pelne dane phi-space
F_FOOTER_TOC = 0x0020   # v2.1: tabela + indeks tick w stopce (append/seal)

# STATE values
S_COLD = 0
S_WARM = 1
S_HOT  = 2
S_TOMB = 3

# ATYPE values
A_RAW       = 0   # surowe bajty (obraz, audio, video, binary)
A_MANIFEST  = 1   # JSON manifest (referencja do CAS/sciezki)
A_STREAM    = 2   # glowica strumienia (pierwszy fragment duzego atomu)
A_PHI_ATOM  = 3   # atom phi-space (T, S, E, relacje)
A_THERMAL   = 4   # snapshot termiczny (ThermalFrame key/delta)

MIME_THERMAL = "application/x-karmazyn-thermal-frame"

STATE_MAP = {"COLD": S_COLD, "WARM": S_WARM, "HOT": S_HOT, "TOMB": S_TOMB}
STATE_INV = {v: k for k, v in STATE_MAP.items()}


# ── Hasher ID (bez zewnetrznych zaleznosci) ───────────────────────────────────

def _id_hash(atom_id: str) -> int:
    """
    64-bitowy hash ID atomu dla szybkiego wyszukiwania w tabeli.
    Uzywamy SHA256 zamiast xxhash -- bez zewnetrznych zaleznosci.
    """
    h = hashlib.sha256(atom_id.encode("utf-8")).digest()
    return struct.unpack(">Q", h[:8])[0]


def _cas_hash(data: bytes) -> bytes:
    """Pierwsze 12B SHA256 danych -- dedup w tabeli atomow."""
    return hashlib.sha256(data).digest()[:12]


def _align8(n: int) -> int:
    """Wyrownaj do granicy 8 bajtow."""
    return (n + 7) & ~7


def _T_to_state(T: float) -> int:
    if T > 70:  return S_HOT
    if T > 30:  return S_WARM
    if T > 2:   return S_COLD
    return S_TOMB


def peek_thermal_header(data: bytes) -> Optional[dict]:
    """Koperta ThermalFrame v1/v2 bez deserializacji list atomów (LE).

    v2: version, kind, timestamp, tick, num_atoms, T_min, T_max  (30 B).
    v1: version, timestamp, tick, num_atoms (21 B) — T_min/T_max = 0.
    """
    if not data or len(data) < 21:
        return None
    ver = data[0]
    if ver == 1:
        ts, tick = struct.unpack_from("<QQ", data, 1)
        num = struct.unpack_from("<I", data, 17)[0]
        return {
            "version": 1, "kind": 0, "timestamp": ts, "tick": tick,
            "num_atoms": num, "T_min": 0.0, "T_max": 0.0,
        }
    if ver == 2 and len(data) >= 30:
        kind = data[1]
        ts, tick = struct.unpack_from("<QQ", data, 2)
        num = struct.unpack_from("<I", data, 18)[0]
        tmin, tmax = struct.unpack_from("<ff", data, 22)
        return {
            "version": 2, "kind": kind, "timestamp": ts, "tick": tick,
            "num_atoms": num, "T_min": float(tmin), "T_max": float(tmax),
        }
    return None


def _atomic_replace(path: str, data: bytes) -> None:
    """Zapis tmp + fsync + os.replace — ten sam rytuał co karmazyn_store."""
    import tempfile
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".kafd_", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _write_kafs_frame(sink, type_byte: int, body: bytes) -> int:
    """Ramka KAFS: [type:1][size:4=len(body)+4][body][crc32:4]. CRC z body."""
    crc = zlib.crc32(body) & 0xFFFFFFFF
    sink.write(struct.pack(">BI", type_byte, len(body) + 4))
    sink.write(body)
    sink.write(struct.pack(">I", crc))
    return 5 + len(body) + 4


def _encode_checkpoint_body(tick: int, n_frames: int,
                            T_min: float, T_max: float,
                            kind: int, causality: str) -> bytes:
    c = (causality or "").encode("utf-8")
    return (struct.pack(">QQ", int(tick), int(n_frames))
            + struct.pack(">ff", float(T_min), float(T_max))
            + struct.pack(">B", int(kind) & 0xFF)
            + struct.pack(">I", len(c)) + c)


def _decode_checkpoint_body(body: bytes) -> dict:
    if len(body) < 25:
        return {}
    tick, n_frames = struct.unpack(">QQ", body[:16])
    tmin, tmax = struct.unpack(">ff", body[16:24])
    kind = body[24]
    clen = struct.unpack(">I", body[25:29])[0]
    causality = body[29:29 + clen].decode("utf-8", errors="replace")
    return {
        "tick": tick, "n_frames": n_frames,
        "T_min": tmin, "T_max": tmax, "kind": kind,
        "causality": causality,
    }


# ─────────────────────────────────────────────────────────────────────────────
# KAFDAtom -- reprezentacja atomu w pamieci
# ─────────────────────────────────────────────────────────────────────────────

class KAFDAtom:
    """
    Atom KAFD -- jednostka przeplywu informacji.
    Moze byc obraz, dzwiek, tekst, atom phi-space, wynik komendy.
    """
    __slots__ = ("id", "data", "mime", "T", "T_max",
                 "state_byte", "atype", "cas_hash")

    def __init__(self, atom_id: str, data: bytes,
                 mime: str     = "application/octet-stream",
                 T:    float   = 50.0,
                 T_max:float   = 100.0,
                 atype:int     = A_RAW):
        self.id         = atom_id
        self.data       = data
        self.mime       = mime
        self.T          = float(T)
        self.T_max      = float(T_max)
        self.state_byte = _T_to_state(T)
        self.atype      = atype
        self.cas_hash   = _cas_hash(data)

    @classmethod
    def from_phi(cls, atom: Any) -> "KAFDAtom":
        """Stwórz KAFDAtom z atomu phi-space."""
        aid   = str(getattr(atom, "id",    ""))
        S     = str(getattr(atom, "S",     ""))
        E     = str(getattr(atom, "E",     ""))
        T     = float(getattr(atom, "T",   50.0))
        T_max = float(getattr(atom, "T_max",100.0))
        state = str(getattr(atom, "state", "WARM"))

        # Dane atomu = kompaktowy JSON z polami phi-space
        d = {
            "id": aid, "S": S, "E": E, "T": T, "T_max": T_max,
            "state": state, "age": int(getattr(atom, "age", 0)),
            # Pola multimedialne
            "img_width":  int(getattr(atom,   "img_width",  0)),
            "img_height": int(getattr(atom,   "img_height", 0)),
            "img_format": str(getattr(atom,   "img_format", "")),
            "thumb_hash": str(getattr(atom,   "thumb_hash", "")),
            "phash":      int(getattr(atom,   "phash",      0)),
            "dhash":      int(getattr(atom,   "dhash",      0)),
            "entropy":    float(getattr(atom, "entropy",    0.0)),
            "_manifest_v": 2,
        }
        data = json.dumps(d, ensure_ascii=False).encode("utf-8")
        mime = S if "/" in S else "application/x-phi-atom"
        return cls(aid, data, mime=mime, T=T, T_max=T_max, atype=A_PHI_ATOM)

    @classmethod
    def from_file(cls, path: str, atom_id: str = "") -> "KAFDAtom":
        """Stwórz KAFDAtom z pliku. MIME wykrywany po rozszerzeniu."""
        import mimetypes
        if not atom_id:
            atom_id = os.path.basename(path)
        data = open(path, "rb").read()
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "application/octet-stream"
        # Temperatura: mniejszy plik = gorętszy
        kb = max(1, len(data) / 1024)
        T  = max(20.0, 65.0 - math.log10(kb) * 10)
        return cls(atom_id, data, mime=mime, T=T, atype=A_RAW)

    def to_phi(self, phi: Any) -> Optional[Any]:
        """Przywroc atom phi-space z KAFDAtom."""
        if self.atype not in (A_MANIFEST, A_PHI_ATOM):
            return None
        try:
            d   = json.loads(self.data.decode("utf-8"))
            aid = d.get("id", self.id)
            existing = phi.get_atom(aid)
            if existing:
                T_new = float(d.get("T", self.T))
                if T_new > float(getattr(existing, "T", 0)):
                    existing.T = T_new
                    existing.S = d.get("S", existing.S)
                    existing.E = d.get("E", existing.E)
                    try: existing.touch()
                    except Exception: pass
                return existing
            else:
                a = phi.create_atom(aid, S=d.get("S",""),
                                    E=d.get("E",""), T=float(d.get("T",50)))
                if a:
                    for field in ("img_width","img_height","img_format",
                                  "thumb_hash","phash","dhash","entropy"):
                        if field in d:
                            try: setattr(a, field, d[field])
                            except Exception: pass
                    try: a.touch()
                    except Exception: pass
                return a
        except Exception:
            return None

    @property
    def size(self) -> int:
        return len(self.data)


# ─────────────────────────────────────────────────────────────────────────────
# KAFDWriter -- zapis strumienia KAFD
# ─────────────────────────────────────────────────────────────────────────────

class KAFDWriter:
    """
    Serializuje atomy do strumienia KAFD v2.0 / v2.1.

    layout:
      "table_first" — v2.0 (tabela przed payloadem, pełny rewrite)
      "footer"      — v2.1 (payload, potem tabela + KTIX w stopce)
    """

    def __init__(self, meta: dict = None, flags: int = F_PHI_NATIVE,
                 layout: str = "table_first"):
        if layout not in ("table_first", "footer"):
            raise ValueError("layout: 'table_first' albo 'footer'")
        self._atoms:    List[KAFDAtom] = []
        self._meta:     dict           = meta or {}
        self._flags:    int            = flags
        self._mime_dict: Dict[str, int]= {}   # mime -> indeks
        self._layout:   str            = layout

    def add(self, atom: KAFDAtom) -> "KAFDWriter":
        """Dodaj atom. Zwraca self dla łańcuchowania."""
        self._atoms.append(atom)
        return self

    def add_phi(self, phi: Any) -> int:
        """Dodaj wszystkie atomy phi-space. Zwraca liczbe dodanych."""
        count = 0
        try:
            for a in phi.matrix.atoms():
                T = float(getattr(a, "T", 0))
                if T < 2.0: continue   # TOMB pomijamy
                self.add(KAFDAtom.from_phi(a))
                count += 1
        except Exception:
            pass
        return count

    def add_file(self, path: str, atom_id: str = "") -> KAFDAtom:
        """Dodaj plik jako atom RAW."""
        atom = KAFDAtom.from_file(path, atom_id)
        self.add(atom)
        return atom

    def _get_mime_idx(self, mime: str) -> int:
        if mime not in self._mime_dict:
            self._mime_dict[mime] = len(self._mime_dict)
        return self._mime_dict[mime]


    def _compute_layout(self) -> dict:
        """
        Faza 1: oblicz wszystkie offsety i rozmiary bez zadnego I/O.
        Zwraca layout dict uzywany przez write_stream i build.
        """
        for atom in self._atoms:
            self._get_mime_idx(atom.mime)

        id_pool     = bytearray()
        entries_meta= []
        for atom in self._atoms:
            id_bytes = atom.id.encode("utf-8")
            entries_meta.append({
                "id_hash":  _id_hash(atom.id),
                "id_off":   len(id_pool),
                "id_len":   len(id_bytes),
                "mime_idx": self._get_mime_idx(atom.mime),
                "T":        atom.T,
                "T_max":    atom.T_max,
                "state":    atom.state_byte,
                "atype":    atom.atype,
                "cas_hash": atom.cas_hash,
            })
            id_pool += id_bytes

        # Sortuj po ID_HASH
        paired = list(zip(entries_meta, self._atoms))
        paired.sort(key=lambda x: x[0]["id_hash"])
        if paired:
            entries_meta, sorted_atoms = map(list, zip(*paired))
        else:
            entries_meta, sorted_atoms = [], []

        # data_size i data_off w payload
        curr = 0
        for e, a in zip(entries_meta, sorted_atoms):
            e["data_size"] = a.size
            e["data_off"]  = curr
            curr += _align8(a.size)
        payload_total = curr

        footer = self._layout == "footer"
        self._meta["_kafd_v"]    = 21 if footer else 2
        self._meta["_mime_dict"] = {str(v): k for k, v in self._mime_dict.items()}
        self._meta["_created"]   = time.strftime("%Y-%m-%d %H:%M")
        if footer:
            self._meta["_layout"] = "footer"
        meta_bytes = json.dumps(self._meta, ensure_ascii=False).encode("utf-8")

        n = len(sorted_atoms)
        if footer:
            payload_off = HEADER_SIZE
            table_off   = payload_off + payload_total
            id_pool_off = table_off + n * ATOM_ENTRY
            meta_off    = id_pool_off + _align8(len(id_pool))
            tick_off    = meta_off + _align8(len(meta_bytes))
        else:
            table_off   = HEADER_SIZE
            id_pool_off = table_off + n * ATOM_ENTRY
            meta_off    = id_pool_off + _align8(len(id_pool))
            payload_off = meta_off + _align8(len(meta_bytes))
            tick_off    = 0
        avg_T = (sum(a.T for a in sorted_atoms) / max(1, n)
                 if sorted_atoms else 0.0)

        return {
            "n":            n,
            "entries_meta": entries_meta,
            "sorted_atoms": sorted_atoms,
            "id_pool":      bytes(id_pool),
            "meta_bytes":   meta_bytes,
            "table_off":    table_off,
            "id_pool_off":  id_pool_off,
            "meta_off":     meta_off,
            "payload_off":  payload_off,
            "payload_total":payload_total,
            "avg_T":        avg_T,
            "footer":       footer,
            "tick_off":     tick_off,
            "version":      VERSION_V21 if footer else VERSION,
            "flags":        self._flags | (F_FOOTER_TOC if footer else 0),
        }

    def _tick_index_bytes(self, L: dict) -> bytes:
        recs = []
        for e, a in zip(L["entries_meta"], L["sorted_atoms"]):
            hdr = peek_thermal_header(a.data)
            if hdr is None:
                continue
            recs.append((hdr, e))
        buf = bytearray()
        buf += TICK_INDEX_MAGIC
        buf += struct.pack(">H", 1)
        buf += struct.pack(">I", len(recs))
        for hdr, e in recs:
            buf += struct.pack(">Q", int(hdr["tick"]))
            buf += struct.pack(">QQ", int(e["data_off"]), int(e["data_size"]))
            buf += struct.pack(">ff", float(hdr["T_min"]), float(hdr["T_max"]))
            buf += struct.pack(">B", int(hdr["kind"]) & 0xFF)
            buf += b"\x00" * 7
        return bytes(buf)

    def write_stream(self, sink) -> int:
        """
        Zapisz KAFD bezposrednio do sink (socket, file, pipe, BytesIO).
        Brak posredniego BytesIO -- payload strumieniowany atom po atomie.
        Zwraca liczbe zapisanych bajtow.
        """
        L = self._compute_layout()
        w = 0

        # ── Naglowek 64B ──────────────────────────────────────────────────────
        hdr = bytearray()
        hdr += MAGIC
        hdr += struct.pack(">HH", L["version"], L["flags"])
        hdr += struct.pack(">I",  L["n"])
        hdr += struct.pack(">Q",  int(time.time()))
        hdr += struct.pack(">Q",  L["table_off"])
        hdr += struct.pack(">Q",  L["meta_off"])
        hdr += struct.pack(">I",  len(L["meta_bytes"]))
        hdr += struct.pack(">Q",  L["payload_off"])
        hdr += struct.pack(">Q",  L["payload_total"])
        hdr += struct.pack(">f",  L["avg_T"])
        assert len(hdr) == HEADER_SIZE - 4
        crc  = zlib.crc32(bytes(hdr)) & 0xFFFFFFFF
        hdr += struct.pack(">I",  crc)
        assert len(hdr) == HEADER_SIZE
        sink.write(bytes(hdr)); w += len(hdr)

        def write_table():
            n = 0
            for e in L["entries_meta"]:
                row  = struct.pack(">Q",  e["id_hash"])
                row += struct.pack(">IH", e["id_off"], e["id_len"])
                row += struct.pack(">H",  e["mime_idx"])
                row += struct.pack(">QQ", e["data_off"], e["data_size"])
                row += struct.pack(">ff", e["T"], e["T_max"])
                row += struct.pack(">BB", e["state"], e["atype"])
                row += e["cas_hash"]
                row += b"\x00" * 2
                assert len(row) == ATOM_ENTRY
                sink.write(row)
                n += len(row)
            return n

        def write_id_pool():
            sink.write(L["id_pool"])
            n = len(L["id_pool"])
            pad = _align8(len(L["id_pool"])) - len(L["id_pool"])
            if pad:
                sink.write(b"\x00" * pad)
                n += pad
            return n

        def write_meta():
            sink.write(L["meta_bytes"])
            n = len(L["meta_bytes"])
            pad = _align8(len(L["meta_bytes"])) - len(L["meta_bytes"])
            if pad:
                sink.write(b"\x00" * pad)
                n += pad
            return n

        def write_payload():
            n = 0
            for atom in L["sorted_atoms"]:
                sink.write(atom.data)
                n += atom.size
                pad = _align8(atom.size) - atom.size
                if pad:
                    sink.write(b"\x00" * pad)
                    n += pad
            return n

        def write_tick_index():
            blob = self._tick_index_bytes(L)
            sink.write(blob)
            return len(blob)

        if L["footer"]:
            w += write_payload()
            w += write_table()
            w += write_id_pool()
            w += write_meta()
            w += write_tick_index()
        else:
            w += write_table()
            w += write_id_pool()
            w += write_meta()
            w += write_payload()

        return w

    def build(self) -> bytes:
        """Zbuduj KAFD blob w pamieci (kompatybilnosc)."""
        buf = BytesIO()
        self.write_stream(buf)
        return buf.getvalue()

    def _write_to(self, buf: BytesIO) -> None:
        """Kompatybilnosc wstecz -- deleguje do write_stream."""
        self.write_stream(buf)


class KAFDReader:
    """
    Czyta strumien KAFD v2.0 / v2.1.
    Seek O(log N) po ID_HASH -- bez ladowania calego payload.
    Kompatybilny z v1.x (fallback do starego KAFD.unpack).
    v2.1: tabela w stopce, indeks tick (KTIX), from_path+mmap.
    """

    def __init__(self, data, verify_cas: bool = True, strict_crc: bool = True):
        self._data  = data
        self._atoms: Dict[str, KAFDAtom] = {}
        self._meta:  dict                = {}
        self._table: List[dict]          = []
        self._mime_dict: Dict[int, str]  = {}
        self._payload_off = 0
        self._tick_index: List[dict]     = []
        self._verify_cas = verify_cas
        self._strict_crc = strict_crc
        self._mmap = None
        self._owned_file = None
        self._flags = 0
        self._valid = self._parse_header()

    def _parse_header(self) -> bool:
        d = self._data
        if len(d) < HEADER_SIZE:
            return False
        if d[:4] != MAGIC:
            return False

        version  = struct.unpack(">H", d[4:6])[0]
        if version < 0x0200:
            # v1.x -- fallback
            return self._parse_v1()
        # CRC naglowka: mismatch → błąd (crc=0: stary blob, pomijamy)
        if len(d) >= HEADER_SIZE:
            stored_crc   = struct.unpack(">I", d[60:64])[0]
            computed_crc = zlib.crc32(bytes(d[:60])) & 0xFFFFFFFF
            if stored_crc != 0 and stored_crc != computed_crc:
                msg = (f"KAFD: CRC naglowka niezgodny (stored={stored_crc:#010x}"
                       f" computed={computed_crc:#010x})")
                if self._strict_crc:
                    raise ValueError(msg)
                import warnings
                warnings.warn(msg + " -- uszkodzony blob?")

        self._flags  = struct.unpack(">H", d[6:8])[0]
        n            = struct.unpack(">I", d[8:12])[0]
        table_off    = struct.unpack(">Q", d[20:28])[0]
        meta_off     = struct.unpack(">Q", d[28:36])[0]
        meta_size    = struct.unpack(">I", d[36:40])[0]
        self._payload_off = struct.unpack(">Q", d[40:48])[0]

        # Parsuj META_JSON
        try:
            meta_raw       = d[meta_off: meta_off + meta_size]
            self._meta     = json.loads(meta_raw.decode("utf-8"))
            mime_dict_raw  = self._meta.get("_mime_dict", {})
            self._mime_dict= {int(k): v for k, v in mime_dict_raw.items()}
        except Exception:
            pass

        # Parsuj ID_POOL
        id_pool_off = table_off + n * ATOM_ENTRY
        id_pool     = d[id_pool_off: meta_off]

        # Parsuj tabele atomow
        for i in range(n):
            off = table_off + i * ATOM_ENTRY
            e   = d[off: off + ATOM_ENTRY]
            if len(e) < ATOM_ENTRY:
                break
            id_hash  = struct.unpack(">Q", e[0:8])[0]
            id_off   = struct.unpack(">I", e[8:12])[0]
            id_len   = struct.unpack(">H", e[12:14])[0]
            mime_idx = struct.unpack(">H", e[14:16])[0]
            data_off = struct.unpack(">Q", e[16:24])[0]
            data_size= struct.unpack(">Q", e[24:32])[0]
            T        = struct.unpack(">f", e[32:36])[0]
            T_max    = struct.unpack(">f", e[36:40])[0]
            state    = e[40]
            atype    = e[41]
            cas_hash = e[42:54]

            try:
                atom_id = id_pool[id_off: id_off + id_len].decode("utf-8")
            except Exception:
                atom_id = f"atom_{i}"

            self._table.append({
                "id":       atom_id,
                "id_hash":  id_hash,
                "data_off": data_off,
                "data_size":data_size,
                "T":        T,
                "T_max":    T_max,
                "state":    state,
                "atype":    atype,
                "mime_idx": mime_idx,
                "cas_hash": cas_hash,
            })
        self._parse_tick_index(d, meta_off, meta_size)
        return True

    def _parse_tick_index(self, d, meta_off: int, meta_size: int) -> None:
        """KTIX zaraz po wyrównanym META — tylko v2.1 / F_FOOTER_TOC."""
        self._tick_index = []
        flags = getattr(self, "_flags", 0)
        if not (flags & F_FOOTER_TOC):
            return
        tick_off = meta_off + _align8(meta_size)
        if tick_off + 10 > len(d):
            return
        blob = bytes(d[tick_off:tick_off + 10])
        if blob[:4] != TICK_INDEX_MAGIC:
            return
        _ver, n = struct.unpack(">HI", blob[4:10])
        need = 10 + n * TICK_INDEX_ENTRY
        if tick_off + need > len(d):
            return
        raw = bytes(d[tick_off + 10: tick_off + need])
        for i in range(n):
            off = i * TICK_INDEX_ENTRY
            tick, data_off, data_size = struct.unpack(">QQQ", raw[off:off+24])
            tmin, tmax = struct.unpack(">ff", raw[off+24:off+32])
            kind = raw[off+32]
            self._tick_index.append({
                "tick": tick,
                "data_off": data_off,
                "data_size": data_size,
                "T_min": tmin,
                "T_max": tmax,
                "kind": kind,
            })

    def _parse_v1(self) -> bool:
        """Fallback dla KAFD v1.x (stary format z JSON naglowkiem)."""
        try:
            d          = self._data
            meta_size  = struct.unpack(">I", d[4:8])[0]
            header     = json.loads(d[8:8 + meta_size].decode("utf-8"))
            payload    = d[8 + meta_size:]
            self._meta = header.get("meta", {})
            self._meta["_kafd_v"] = 1
            self._payload_off = 8 + meta_size
            for aid, info in header.get("atoms", {}).items():
                self._table.append({
                    "id":       aid,
                    "id_hash":  _id_hash(aid),
                    "data_off": info["offset"],
                    "data_size":info["size"],
                    "T":        50.0,
                    "T_max":    100.0,
                    "state":    S_WARM,
                    "atype":    A_RAW,
                    "mime_idx": 0,
                    "cas_hash": b"\x00" * 12,
                    "_v1_payload": payload,
                })
            return True
        except Exception:
            return False

    @property
    def meta(self) -> dict:
        return self._meta

    @property
    def atom_ids(self) -> List[str]:
        return [e["id"] for e in self._table]

    def get_entry(self, atom_id: str) -> Optional[dict]:
        """Znajdz wpis tabeli po ID (binary search po hash)."""
        target = _id_hash(atom_id)
        lo, hi = 0, len(self._table) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            h   = self._table[mid]["id_hash"]
            if h == target:
                # [FIX3] pelne skanowanie kolizji -- nie tylko mid±1
                if self._table[mid]["id"] == atom_id:
                    return self._table[mid]
                # Skanuj w lewo
                j = mid - 1
                while j >= 0 and self._table[j]["id_hash"] == target:
                    if self._table[j]["id"] == atom_id: return self._table[j]
                    j -= 1
                # Skanuj w prawo
                j = mid + 1
                while j < len(self._table) and self._table[j]["id_hash"] == target:
                    if self._table[j]["id"] == atom_id: return self._table[j]
                    j += 1
                return None
            elif h < target:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def get_atom(self, atom_id: str) -> Optional[KAFDAtom]:
        """Pobierz atom z danymi -- O(log N) seek. CAS weryfikowany."""
        entry = self.get_entry(atom_id)
        if not entry:
            return None
        data = self._get_data(entry)
        stored = entry["cas_hash"]
        if self._verify_cas and stored and stored != b"\x00" * 12:
            got = _cas_hash(data)
            if got != stored:
                raise ValueError(
                    f"KAFD CAS mismatch id={atom_id!r} "
                    f"stored={stored.hex()} computed={got.hex()}")
        mime = self._mime_dict.get(entry["mime_idx"], "application/octet-stream")
        a    = KAFDAtom(atom_id, data, mime=mime,
                        T=entry["T"], T_max=entry["T_max"],
                        atype=entry["atype"])
        a.state_byte = entry["state"]
        a.cas_hash   = stored
        return a

    @property
    def tick_index(self) -> List[dict]:
        return list(self._tick_index)

    def ticks_in_T_range(self, T_min: float, T_max: float) -> List[dict]:
        """P5: trafienia z koperty KTIX — bez rozpakowania ThermalFrame."""
        hits = []
        for rec in self._tick_index:
            if rec["T_max"] >= T_min and rec["T_min"] <= T_max:
                hits.append(dict(rec))
        return hits

    @classmethod
    def from_path(cls, path: str, use_mmap: bool = True,
                  verify_cas: bool = True, strict_crc: bool = True,
                  world: Optional[str] = None) -> "KAFDReader":
        """Odczyt z pliku. KAFX/XOR → decrypt do RAM; plain KAFD → mmap."""
        with open(path, "rb") as peek:
            magic = peek.read(4)
        if magic != MAGIC:
            from karmazyn_cipher import read_stored_file
            data, _mode = read_stored_file(path, world=world)
            return cls(data, verify_cas=verify_cas, strict_crc=strict_crc)
        f = open(path, "rb")
        try:
            size = os.fstat(f.fileno()).st_size
            if use_mmap and size > 0:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                r = cls(mm, verify_cas=verify_cas, strict_crc=strict_crc)
                r._mmap = mm
                r._owned_file = f
                f = None
                return r
            data = f.read()
        finally:
            if f is not None:
                f.close()
        return cls(data, verify_cas=verify_cas, strict_crc=strict_crc)

    def close(self) -> None:
        mm = getattr(self, "_mmap", None)
        if mm is not None:
            try:
                mm.close()
            except Exception:
                pass
            self._mmap = None
        f = getattr(self, "_owned_file", None)
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
            self._owned_file = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _get_data(self, entry: dict) -> bytes:
        if "_v1_payload" in entry:
            p   = entry["_v1_payload"]
            off = entry["data_off"]
            sz  = entry["data_size"]
            # [FIX4] walidacja v1
            if off < 0 or sz < 0 or off + sz > len(p):
                raise ValueError(f"KAFD v1: offset {off}+{sz} poza payload ({len(p)}B)")
            return p[off: off + sz]
        off  = self._payload_off + entry["data_off"]
        size = entry["data_size"]
        # [FIX4] walidacja v2 -- uszkodzony blob nie czyta poza danymi
        if off < 0 or size < 0 or off + size > len(self._data):
            raise ValueError(
                f"KAFD v2: offset {off}+{size} poza danymi ({len(self._data)}B)")
        return self._data[off: off + size]

    def iter_atoms(self, min_T: float = 0.0) -> Iterator[KAFDAtom]:
        """Iteruj po atomach, opcjonalnie filtruj po minimalnym T."""
        for entry in self._table:
            if entry["T"] < min_T:
                continue
            yield self.get_atom(entry["id"])

    def hot_atoms(self, threshold: float = 70.0) -> List[KAFDAtom]:
        """Zwroc gorące atomy posortowane malejaco po T."""
        result = [self.get_atom(e["id"]) for e in self._table if e["T"] >= threshold]
        return sorted(result, key=lambda a: -a.T)

    def to_phi(self, phi: Any) -> Tuple[int, int]:
        """Przelej wszystkie atomy phi-space do runtime. Zwraca (dodane, zaktualizowane)."""
        added = updated = 0
        for entry in self._table:
            if entry["atype"] not in (A_MANIFEST, A_PHI_ATOM):
                continue
            atom = self.get_atom(entry["id"])
            if not atom:
                continue
            result = atom.to_phi(phi)
            if result:
                if phi.get_atom(atom.id) is not None:
                    updated += 1
                else:
                    added += 1
        return added, updated


# ─────────────────────────────────────────────────────────────────────────────
# Streaming API -- przeplyw przez pipe / socket
# ─────────────────────────────────────────────────────────────────────────────

class KAFDStream:
    """
    Przezywa pojedynczy atom jako natywny strumien bajtow.
    Uzyj do przekazywania do zewnetrznych narzedziach (mpv, ffplay, eog).

    Przyklad:
        reader = KAFDReader(blob)
        stream = KAFDStream(reader, "audio.track1")
        stream.pipe_to(sys.stdout.buffer)  # -> mpv -
        stream.save_to("/tmp/track.mp3")
        stream.pipe_fifo("/tmp/kafd_fifo") # named pipe
    """

    def __init__(self, reader: KAFDReader, atom_id: str):
        self._reader  = reader
        self._atom_id = atom_id
        self._atom    = reader.get_atom(atom_id)

    @property
    def mime(self) -> str:
        return self._atom.mime if self._atom else ""

    @property
    def size(self) -> int:
        return self._atom.size if self._atom else 0

    def pipe_to(self, sink, chunk_size: int = 65536) -> int:
        """Przelej dane do dowolnego obiektu z .write(bytes). Zwraca bajty."""
        if not self._atom:
            return 0
        data   = self._atom.data
        sent   = 0
        offset = 0
        while offset < len(data):
            chunk  = data[offset: offset + chunk_size]
            sink.write(chunk)
            sent  += len(chunk)
            offset+= chunk_size
        return sent

    def save_to(self, path: str) -> int:
        """Zapisz atom jako natywny plik (bez naglowka KAFD)."""
        if not self._atom:
            return 0
        with open(path, "wb") as f:
            return self.pipe_to(f)

    def pipe_fifo(self, fifo_path: str) -> None:
        """
        Utwórz named pipe i zapisz dane.
        Zewnetrzny process czyta z fifo_path.

        Przyklad:
            stream.pipe_fifo("/tmp/kafd_video")
            # w innym terminalu:  mpv /tmp/kafd_video
        """
        try:
            os.mkfifo(fifo_path)
        except FileExistsError:
            pass
        with open(fifo_path, "wb") as f:
            self.pipe_to(f)


# ─────────────────────────────────────────────────────────────────────────────
# Konwersja V1 <-> V2
# ─────────────────────────────────────────────────────────────────────────────

def upgrade_v1(v1_blob: bytes, meta: dict = None) -> bytes:
    """
    Konwertuj stary KAFD v1.x do v2.0.
    Uzyj przy migracji istniejacych bubbli.
    """
    reader = KAFDReader(v1_blob)
    writer = KAFDWriter(meta=meta or reader.meta)
    for atom_id in reader.atom_ids:
        atom = reader.get_atom(atom_id)
        if atom:
            writer.add(atom)
    return writer.build()


# ─────────────────────────────────────────────────────────────────────────────
# Integracja z BubbleVFS
# ─────────────────────────────────────────────────────────────────────────────

def vfs_pack(atoms_dict: Dict[str, bytes],
              meta: dict = None,
              phi: Any = None,
              flags: int = None) -> bytes:
    """
    Drop-in replacement dla KAFD.pack() -- uzywa v2.0.
    atoms_dict: {atom_id: bytes}  (kompatybilnosc z v1 API)
    """
    writer = KAFDWriter(meta=meta, flags=F_PHI_NATIVE if flags is None else flags)
    for aid, data in atoms_dict.items():
        # Sprobuj wykryc atype z zawartosci
        atype = A_MANIFEST if _is_manifest(data) else A_RAW
        mime  = _detect_mime(data, aid)
        T     = 50.0
        if atype == A_MANIFEST:
            try:
                d = json.loads(data.decode("utf-8"))
                T = float(d.get("T", 50.0))
            except Exception:
                pass
        writer.add(KAFDAtom(aid, data, mime=mime, T=T, atype=atype))
    return writer.build()


def vfs_unpack(blob: bytes) -> Tuple[Dict[str, bytes], dict]:
    """
    Drop-in replacement dla KAFD.unpack() -- czyta v1 i v2.
    Zwraca (atoms_dict, meta) jak stary API.
    """
    reader = KAFDReader(blob)
    atoms  = {aid: reader.get_atom(aid).data
               for aid in reader.atom_ids
               if reader.get_atom(aid)}
    return atoms, reader.meta


def _is_manifest(data: bytes) -> bool:
    try:
        d = json.loads(data.decode("utf-8"))
        return isinstance(d, dict) and "_manifest_v" in d
    except Exception:
        return False


def _detect_mime(data: bytes, atom_id: str = "") -> str:
    """Wykryj MIME po sygnaturze binarnej lub rozszerzeniu ID."""
    if len(data) >= 8:
        if data[:3]  == b"\xff\xd8\xff":       return "image/jpeg"
        if data[:8]  == b"\x89PNG\r\n\x1a\n":  return "image/png"
        if data[:6]  in (b"GIF87a", b"GIF89a"): return "image/gif"
        if data[:4]  == b"RIFF" and data[8:12] == b"WAVE": return "audio/wav"
        if data[:3]  == b"ID3" or data[:2] == b"\xff\xfb": return "audio/mpeg"
        if data[:4]  == b"fLaC":               return "audio/flac"
        if data[:4]  == b"OggS":               return "audio/ogg"
        if data[:4]  in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x1c"): return "video/mp4"
        if data[:4]  == b"KAFD":               return "application/x-kafd"
    if _is_manifest(data):
        return "application/x-phi-manifest"
    # Rozszerzenie z atom_id
    ext = os.path.splitext(atom_id)[1].lower()
    ext_map = {".jpg":"image/jpeg",".png":"image/png",".mp3":"audio/mpeg",
               ".mp4":"video/mp4",".wav":"audio/wav",".txt":"text/plain",
               ".json":"application/json",".py":"text/x-python"}
    return ext_map.get(ext, "application/octet-stream")


# ─────────────────────────────────────────────────────────────────────────────
# Test standalone
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    print("=" * 60)
    print("  KAFD v2.0 -- test")
    print("=" * 60)

    # Stwórz atomy róznych typów
    writer = KAFDWriter(meta={"label": "test_stream", "description": "KAFD v2 test"})

    # Atom tekstowy
    writer.add(KAFDAtom("doc.readme",
                         b"Dokumentacja KarmazynOS\n",
                         mime="text/plain", T=45.0))

    # Symulowany atom obrazu (nagłówek PNG)
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    writer.add(KAFDAtom("img.logo",
                         png_header,
                         mime="image/png", T=72.0))

    # Atom phi-space
    class MockAtom:
        id="shell.init"; S="sys"; E=""; T=85.0; T_max=100; state="HOT"
        age=10; img_width=0; img_height=0; img_format=""; thumb_hash=""
        phash=0; dhash=0; entropy=0.0

    writer.add(KAFDAtom.from_phi(MockAtom()))

    blob = writer.build()
    print(f"\n[1] Zapis: {len(blob)} bajtów")

    # Odczyt
    reader = KAFDReader(blob)
    print(f"\n[2] Odczyt: {len(reader.atom_ids)} atomów")
    print(f"    IDs: {reader.atom_ids}")

    # Seek do atomu
    atom = reader.get_atom("img.logo")
    print(f"\n[3] Seek do img.logo: mime={atom.mime} T={atom.T:.1f} "
          f"size={atom.size} sig={atom.data[:4]}")

    # Binary search
    atom2 = reader.get_atom("shell.init")
    print(f"\n[4] Binary search shell.init: T={atom2.T:.1f} atype={atom2.atype}")
    d = json.loads(atom2.data.decode())
    print(f"    phi state={d.get('state')} S={d.get('S')}")

    # Hot atoms
    hot = reader.hot_atoms(70.0)
    print(f"\n[5] Hot atoms (T>70): {[(a.id, a.T) for a in hot]}")

    # vfs_pack/unpack compatibility
    v1_atoms = {"__main__": b"tekst", "img.test": png_header}
    v2_blob  = vfs_pack(v1_atoms, {"label": "compat_test"})
    back, m  = vfs_unpack(v2_blob)
    print(f"\n[6] vfs_pack/unpack compat: klucze={list(back.keys())}")

    # Stream API
    stream = KAFDStream(reader, "img.logo")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        n = stream.save_to(f.name)
        print(f"\n[7] Stream do pliku: {n}B -> {f.name}")
        sig = open(f.name,"rb").read(4)
        print(f"    Sygnatura: {sig} (oczekiwane \\x89PNG)")
        os.unlink(f.name)

    print("\n  Wszystkie testy OK")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# KAFD_FLOW -- frame-based streaming protocol
# ─────────────────────────────────────────────────────────────────────────────

KAFS_MAGIC   = b"KAFS"   # odroznienie od KAFD seekable
FT_ATOM      = 0x01      # ramka atomu
FT_META      = 0x02      # ramka metadanych
FT_CHECKPOINT= 0x03      # checkpoint (tick, causality, T envelope)
FT_END       = 0xFF      # koniec strumienia


def _encode_atom_frame_body(atom: "KAFDAtom") -> bytes:
    id_b   = atom.id.encode("utf-8")
    mime_b = atom.mime.encode("utf-8")
    frame_body = bytearray()
    frame_body += struct.pack(">H", len(id_b))
    frame_body += id_b
    frame_body += struct.pack(">H", len(mime_b))
    frame_body += mime_b
    frame_body += struct.pack(">ff", atom.T, atom.T_max)
    frame_body += struct.pack(">BB", atom.state_byte, atom.atype)
    frame_body += struct.pack(">Q",  atom.size)
    frame_body += atom.data
    return bytes(frame_body)


def _parse_atom_frame_body(body: bytes) -> Optional["KAFDAtom"]:
    try:
        off     = 0
        id_len  = struct.unpack(">H", body[off:off+2])[0]; off += 2
        atom_id = body[off:off+id_len].decode("utf-8");    off += id_len
        ml      = struct.unpack(">H", body[off:off+2])[0]; off += 2
        mime    = body[off:off+ml].decode("utf-8");         off += ml
        T, T_max= struct.unpack(">ff", body[off:off+8]);   off += 8
        state   = body[off]; atype = body[off+1];          off += 2
        data_len= struct.unpack(">Q", body[off:off+8])[0]; off += 8
        data    = body[off:off+data_len]
        a            = KAFDAtom(atom_id, data, mime=mime, T=T, T_max=T_max, atype=atype)
        a.state_byte = state
        return a
    except Exception:
        return None


class KAFDFlowWriter:
    """
    Zapis strumienia KAFD_FLOW.

    Format ramkowy -- bez pre-pass, bez tabeli, bez plikow.
    Dziala nad kazdym jednokierunkowym kanalem: socket, pipe, stdout.

    T-ordering: gorące atomy idą pierwsze.
    Konsument moze przerwac odczyt gdy T spadnie ponizej progu.

    Format ramki atomu:
      [FT_ATOM:1][FRAME_SIZE:4][ID_LEN:2][ID:N][MIME_LEN:2][MIME:M]
      [T:4f][T_MAX:4f][STATE:1][ATYPE:1][DATA_LEN:8][DATA:N][CRC32:4]
    """

    def __init__(self, meta: dict = None):
        self._meta   = meta or {}
        self._atoms: list = []

    def add(self, atom: "KAFDAtom") -> "KAFDFlowWriter":
        self._atoms.append(atom)
        return self

    def add_phi(self, phi) -> int:
        count = 0
        try:
            for a in phi.matrix.atoms():
                if float(getattr(a, "T", 0)) >= 2.0:
                    self.add(KAFDAtom.from_phi(a))
                    count += 1
        except Exception:
            pass
        return count

    def write_stream(self, sink) -> int:
        """
        Zapisz caly strumien KAFS do sink.
        Atomy posortowane malejaco po T -- gorące pierwsze.
        Zwraca bajty zapisane.
        """
        w = 0

        # Naglowek strumienia
        sink.write(KAFS_MAGIC); w += 4
        sink.write(struct.pack(">HH", VERSION, F_PHI_NATIVE)); w += 4

        # META ramka (metadane kolekcji)
        if self._meta:
            w += self._write_meta_frame(sink)

        # Sortuj po T malejaco -- gorące atomy pierwsze
        sorted_atoms = sorted(self._atoms, key=lambda a: -a.T)
        total_data   = 0

        for atom in sorted_atoms:
            n = self._write_atom_frame(sink, atom)
            w += n
            total_data += atom.size

        # END ramka
        w += self._write_end_frame(sink, len(sorted_atoms), total_data)
        return w

    def _write_atom_frame(self, sink, atom: "KAFDAtom") -> int:
        return _write_kafs_frame(sink, FT_ATOM, _encode_atom_frame_body(atom))

    def _write_meta_frame(self, sink) -> int:
        meta_b = json.dumps(self._meta, ensure_ascii=False).encode("utf-8")
        body   = struct.pack(">I", len(meta_b)) + meta_b
        return _write_kafs_frame(sink, FT_META, body)

    def _write_end_frame(self, sink, atom_count: int, total_bytes: int) -> int:
        body = struct.pack(">IQ", atom_count, total_bytes)
        return _write_kafs_frame(sink, FT_END, body)


class KAFDFlowReader:
    """
    Odczyt strumienia KAFS -- ramka po ramce.

    Dziala nad dowolnym strumieniem (socket, pipe, plik).
    Weryfikuje CRC kazdej ramki.

    Uzycie:
        reader = KAFDFlowReader(socket_or_file)
        for atom in reader.iter_atoms():
            process(atom)  # gorące atomy przychodzą pierwsze

        # Lub z progiem T:
        for atom in reader.iter_atoms(min_T=50.0):
            ...           # zimne atomy juz nie dotrą
    """

    def __init__(self, source, sorted_by_T: bool = True):
        """source: obiekt z metodą .read(n) -> bytes.
        sorted_by_T: strumień batch (hot-first) — min_T kończy iterację.
        Dziennik chronologiczny: sorted_by_T=False (min_T tylko filtruje).
        """
        self._src  = source
        self._meta = {}
        self._sorted_by_T = sorted_by_T
        self._flags = 0
        self.last_checkpoint: Optional[dict] = None

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._src.read(n - len(buf))
            if not chunk:
                raise EOFError(f"KAFS: oczekiwano {n}B, dostano {len(buf)}B")
            buf += chunk
        return buf

    def read_header(self) -> dict:
        """Wczytaj i zweryfikuj naglowek strumienia."""
        magic = self._read_exact(4)
        if magic != KAFS_MAGIC:
            raise ValueError(f"KAFS: zly magic {magic!r}")
        version, flags = struct.unpack(">HH", self._read_exact(4))
        self._flags = flags
        if flags & F_STREAMING:
            self._sorted_by_T = False
        return {"version": version, "flags": flags}

    def iter_frames(self, verify_crc: bool = True):
        """Surowy strumień ramek: (type_byte, body). END kończy. EOF kończy."""
        while True:
            try:
                type_byte = self._read_exact(1)[0]
                frame_size= struct.unpack(">I", self._read_exact(4))[0]
                payload   = self._read_exact(frame_size)
            except EOFError:
                return
            if len(payload) < 4:
                return
            body_data = payload[:-4]
            crc_stored = struct.unpack(">I", payload[-4:])[0]
            if verify_crc:
                crc_computed = zlib.crc32(body_data) & 0xFFFFFFFF
                if crc_stored != crc_computed:
                    raise ValueError(
                        f"KAFS CRC: stored={crc_stored:#010x} "
                        f"computed={crc_computed:#010x}")
            yield type_byte, body_data

    def iter_atoms(self, min_T: float = 0.0, verify_crc: bool = True):
        """
        Iteruj po atomach w strumieniu.
        Zatrzymuje sie na END_FRAME.
        Gdy sorted_by_T i min_T>0 — pierwsze T poniżej progu kończy (hot-first).
        """
        for type_byte, body_data in self.iter_frames(verify_crc=verify_crc):
            if type_byte == FT_META:
                json_len = struct.unpack(">I", body_data[:4])[0]
                try:
                    self._meta = json.loads(body_data[4:4+json_len].decode())
                except Exception:
                    pass
                continue

            if type_byte == FT_END:
                return

            if type_byte == FT_CHECKPOINT:
                self.last_checkpoint = _decode_checkpoint_body(body_data)
                continue

            if type_byte == FT_ATOM:
                atom = _parse_atom_frame_body(body_data)
                if not atom:
                    continue
                if atom.T >= min_T:
                    yield atom
                elif min_T > 0 and self._sorted_by_T:
                    return

    def _parse_atom_body(self, body: bytes) -> "KAFDAtom":
        return _parse_atom_frame_body(body)

    @property
    def meta(self) -> dict:
        return self._meta


# ─────────────────────────────────────────────────────────────────────────────
# P1: KAFS journal — append WAL, checkpoint, recovery ogona
# ─────────────────────────────────────────────────────────────────────────────

class KAFDJournal:
    """
    Trwały dziennik KAFS: dopisywanie ramek, checkpoint, obcięcie urwanej klatki.

    Nie sortuje po T (kolejność ticków). Flaga F_STREAMING w nagłówku.
    Brak FT_END do close() — recovery kończy na ostatniej ramce z dobrym CRC.
    """

    def __init__(self, path: str, *, fsync: bool = True, meta: dict = None,
                 world: Optional[str] = None, encrypt: Optional[bool] = None):
        self.path = path
        self.fsync = fsync
        self._meta = meta or {}
        self.lock = threading.RLock()
        self._file = None
        self.n_frames = 0
        self.last_tick = 0
        self.last_causality = ""
        self.last_checkpoint: Optional[dict] = None
        try:
            from karmazyn_cipher import (
                infer_world_from_path, plain_requested, crypto_available,
            )
            self.world = world if world is not None else infer_world_from_path(path)
            if encrypt is None:
                encrypt = (not plain_requested()) and crypto_available()
            self.encrypt = bool(encrypt)
        except ImportError:
            self.world = world or ""
            self.encrypt = False
        self._open()

    def _open(self) -> dict:
        d = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(d, exist_ok=True)
        recovered = {"truncated": False, "frames": 0, "empty": True}
        if os.path.exists(self.path) and os.path.getsize(self.path) >= 8:
            recovered = self.recover()
            self._file = open(self.path, "r+b")
            self._file.seek(0, os.SEEK_END)
        else:
            self._file = open(self.path, "w+b")
            self._file.write(KAFS_MAGIC)
            self._file.write(struct.pack(">HH", VERSION, F_PHI_NATIVE | F_STREAMING))
            if self._meta:
                meta_b = json.dumps(self._meta, ensure_ascii=False).encode("utf-8")
                body = struct.pack(">I", len(meta_b)) + meta_b
                _write_kafs_frame(self._file, FT_META, body)
            self._sync()
        self.n_frames = int(recovered.get("frames") or 0)
        ck = recovered.get("last_checkpoint") or {}
        self.last_tick = int(recovered.get("last_tick") or ck.get("tick") or 0)
        self.last_causality = ck.get("causality") or ""
        self.last_checkpoint = ck or None
        return recovered

    def _wrap_atom(self, atom: "KAFDAtom") -> "KAFDAtom":
        if not self.encrypt:
            return atom
        from karmazyn_cipher import wrap_payload, is_kx1
        if is_kx1(atom.data):
            return atom
        wrapped = wrap_payload(atom.data, self.world, atom.id.encode("utf-8"))
        out = KAFDAtom(atom.id, wrapped, mime=atom.mime, T=atom.T,
                       T_max=atom.T_max, atype=atom.atype)
        out.state_byte = atom.state_byte
        return out

    def _unwrap_atom(self, atom: Optional["KAFDAtom"]) -> Optional["KAFDAtom"]:
        if atom is None:
            return None
        from karmazyn_cipher import is_kx1, unwrap_payload
        if not is_kx1(atom.data):
            return atom
        plain = unwrap_payload(atom.data, self.world, atom.id.encode("utf-8"))
        out = KAFDAtom(atom.id, plain, mime=atom.mime, T=atom.T,
                       T_max=atom.T_max, atype=atom.atype)
        out.state_byte = atom.state_byte
        return out

    def _sync(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        if self.fsync:
            os.fsync(self._file.fileno())

    def recover(self) -> dict:
        """Skanuj ramki; obetnij po ostatnim dobrym CRC. Zwraca statystyki."""
        with self.lock:
            return self._recover_unlocked()

    def _recover_unlocked(self) -> dict:
        path = self.path
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return {"truncated": False, "frames": 0, "empty": True, "last_good": 0}
        size = os.path.getsize(path)
        last_tick = 0
        with open(path, "r+b") as f:
            header = f.read(8)
            if len(header) < 8 or header[:4] != KAFS_MAGIC:
                raise ValueError("KAFS journal: zły nagłówek")
            last_good = 8
            n_atoms = 0
            last_ckpt = None
            truncated = False
            reason = ""
            while True:
                hdr = f.read(5)
                if len(hdr) == 0:
                    break
                if len(hdr) < 5:
                    f.truncate(last_good)
                    truncated, reason = True, "short_header"
                    break
                type_byte = hdr[0]
                frame_size = struct.unpack(">I", hdr[1:5])[0]
                payload = f.read(frame_size)
                if len(payload) < frame_size:
                    f.truncate(last_good)
                    truncated, reason = True, "short_payload"
                    break
                if frame_size < 4:
                    f.truncate(last_good)
                    truncated, reason = True, "bad_size"
                    break
                body, crc_b = payload[:-4], payload[-4:]
                crc_stored = struct.unpack(">I", crc_b)[0]
                crc_computed = zlib.crc32(body) & 0xFFFFFFFF
                if crc_stored != crc_computed:
                    f.truncate(last_good)
                    truncated, reason = True, "crc"
                    break
                last_good = f.tell()
                if type_byte == FT_ATOM:
                    n_atoms += 1
                    atom = _parse_atom_frame_body(body)
                    if atom:
                        atom = self._unwrap_atom(atom)
                        hdr_t = peek_thermal_header(atom.data if atom else b"")
                        if hdr_t:
                            last_tick = int(hdr_t["tick"])
                elif type_byte == FT_CHECKPOINT:
                    last_ckpt = _decode_checkpoint_body(body)
                elif type_byte == FT_END:
                    break
            return {
                "truncated": truncated,
                "reason": reason,
                "frames": n_atoms,
                "empty": False,
                "last_good": last_good,
                "file_size": size,
                "last_checkpoint": last_ckpt,
                "last_tick": last_tick,
            }

    def append_atom(self, atom: "KAFDAtom") -> int:
        with self.lock:
            if self._file is None:
                raise RuntimeError("KAFDJournal zamknięty")
            stored = self._wrap_atom(atom)
            n = _write_kafs_frame(self._file, FT_ATOM, _encode_atom_frame_body(stored))
            self.n_frames += 1
            hdr = peek_thermal_header(atom.data)
            if hdr:
                self.last_tick = int(hdr["tick"])
            self._sync()
            return n

    def checkpoint(self, tick: int, causality: str = "",
                   T_min: float = 0.0, T_max: float = 0.0, kind: int = 0) -> int:
        with self.lock:
            if self._file is None:
                raise RuntimeError("KAFDJournal zamknięty")
            body = _encode_checkpoint_body(
                tick, self.n_frames, T_min, T_max, kind, causality)
            n = _write_kafs_frame(self._file, FT_CHECKPOINT, body)
            self.last_checkpoint = _decode_checkpoint_body(body)
            self.last_causality = causality or ""
            self.last_tick = int(tick)
            self._sync()
            return n

    def iter_atoms(self, min_T: float = 0.0):
        """Skan od początku (osobny uchwyt). Nie rusza pozycji append."""
        with open(self.path, "rb") as f:
            r = KAFDFlowReader(f, sorted_by_T=False)
            r.read_header()
            for atom in r.iter_atoms(min_T=min_T):
                yield self._unwrap_atom(atom)

    def close(self, write_end: bool = False) -> None:
        with self.lock:
            if self._file is None:
                return
            if write_end:
                _write_kafs_frame(
                    self._file, FT_END,
                    struct.pack(">IQ", self.n_frames, 0))
                self._sync()
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def compact_journal(journal_path: str, kafd_path: str,
                    layout: str = "footer",
                    reset_journal: bool = True,
                    meta: dict = None,
                    world: Optional[str] = None,
                    encrypt: Optional[bool] = None) -> dict:
    """
    P2: seal — dziennik KAFS → seekable KAFD (v2.1 footer domyślnie).
    P6: koperta KAFX na pliku .kafd (AES-GCM, klucz świata).
    """
    if not os.path.exists(journal_path) or os.path.getsize(journal_path) < 8:
        return {"atoms": 0, "kafd": kafd_path, "empty": True}

    tmp_j = KAFDJournal(journal_path, fsync=False, world=world, encrypt=encrypt)
    recovered_frames = tmp_j.n_frames
    atoms = list(tmp_j.iter_atoms())
    tmp_j.close(write_end=False)

    flags = F_PHI_NATIVE
    try:
        from karmazyn_cipher import (
            encrypt_for_path, infer_world_from_path, plain_requested, crypto_available,
        )
        mark = encrypt if encrypt is not None else (
            (not plain_requested()) and crypto_available()
        )
        wname = world if world is not None else infer_world_from_path(kafd_path)
    except ImportError:
        mark = False
        wname = world or ""
        encrypt_for_path = None  # type: ignore
    if mark:
        flags |= F_ENCRYPTED

    writer = KAFDWriter(
        meta=meta or {"source": "kafs-journal", "frames": len(atoms)},
        layout=layout,
        flags=flags,
    )
    for atom in atoms:
        writer.add(atom)
    blob = writer.build()
    if mark and encrypt_for_path is not None:
        blob = encrypt_for_path(blob, kafd_path, world=wname, encrypt=True)
    _atomic_replace(kafd_path, blob)

    if reset_journal:
        with open(journal_path, "wb") as f:
            f.write(KAFS_MAGIC)
            f.write(struct.pack(">HH", VERSION, F_PHI_NATIVE | F_STREAMING))
            f.flush()
            os.fsync(f.fileno())

    return {
        "atoms": len(atoms),
        "kafd": kafd_path,
        "bytes": len(blob),
        "layout": layout,
        "recovered_frames": recovered_frames,
        "reset_journal": reset_journal,
    }