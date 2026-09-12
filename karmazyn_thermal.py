#!/usr/bin/env python3
"""
karmazyn_thermal.py — klatki termiczne Store na KAFD/KAFS
=========================================================
Maciej Mazur, Warsaw 2026

Każdy tick Store → ThermalFrame → dziennik KAFS → seal do .kafd.

Architektura:
  Store.tick()
    ├─ _tick_body() — thermal GC
    └─ ThermalLog.commit_snapshot()
         ├─ snapshot() — (id, T, state, vector)
         ├─ GOP key/delta
         └─ KAFDJournal.append (KX1) / compact_journal (.kafd KAFX)

Cechy:
  • Holograficzne: klatka = pełny stan + łańcuch causality
  • Temporal: as_of_tick, query T z koperty
  • GOP: temperatura zmienia się powoli → delta vs KEY
  • Zero-dep poza KAFD: struct + io (numpy opcjonalnie)
"""

import struct
import io
import os
import time
import hashlib
import json
import zlib
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import threading

try:
    from karmazyn_atom import T_INIT, T_MAX, T_TOMB
except ImportError:
    T_INIT = 50.0
    T_MAX = 100.0
    T_TOMB = 2.0

try:
    from karmazyn_kafd import (
        A_THERMAL,
        MIME_THERMAL,
        KAFDAtom,
        KAFDJournal,
        KAFDReader,
        compact_journal,
        peek_thermal_header,
    )
    _HAS_KAFD = True
except ImportError:
    A_THERMAL = 4
    MIME_THERMAL = "application/x-karmazyn-thermal-frame"
    KAFDAtom = KAFDJournal = KAFDReader = compact_journal = None  # type: ignore
    peek_thermal_header = None
    _HAS_KAFD = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None
    _HAS_NUMPY = False

# Versioning — v2: kind, T_min/T_max, checksum na końcu (LE crc32)
T_FRAME_VERSION = 2
T_FRAME_VERSION_V1 = 1
THERMAL_LOG_VERSION = "1.1.0"

KIND_KEY = 0
KIND_DELTA = 1
DF_T = 0x01
DF_STATE = 0x02
DF_ENTROPY = 0x04
DF_VECTOR = 0x08

DECAY_DEFAULT = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Serializacja binarnego formatu ThermalFrame
# ─────────────────────────────────────────────────────────────────────────────

class BinaryStream:
    """Pomocnik do pisania/czytania z zachowaniem porządku bajtów."""

    def __init__(self, data: bytes = b""):
        self.buf = io.BytesIO(data)

    def write_u8(self, v: int) -> None:
        self.buf.write(struct.pack('<B', v))

    def write_u32(self, v: int) -> None:
        self.buf.write(struct.pack('<I', v))

    def write_u64(self, v: int) -> None:
        self.buf.write(struct.pack('<Q', v))

    def write_f32(self, v: float) -> None:
        self.buf.write(struct.pack('<f', v))

    def write_f64(self, v: float) -> None:
        self.buf.write(struct.pack('<d', v))

    def write_pascal_str(self, s: str) -> None:
        """Pascal string: [len:u32][data:utf8]"""
        data = s.encode('utf-8')
        self.write_u32(len(data))
        self.buf.write(data)

    def write_bytes(self, data: bytes) -> None:
        """Raw bytes: [len:u32][data]"""
        self.write_u32(len(data))
        self.buf.write(data)

    def read_u8(self) -> int:
        return struct.unpack('<B', self.buf.read(1))[0]

    def read_u32(self) -> int:
        return struct.unpack('<I', self.buf.read(4))[0]

    def read_u64(self) -> int:
        return struct.unpack('<Q', self.buf.read(8))[0]

    def read_f32(self) -> float:
        return struct.unpack('<f', self.buf.read(4))[0]

    def read_f64(self) -> float:
        return struct.unpack('<d', self.buf.read(8))[0]

    def read_pascal_str(self) -> str:
        length = self.read_u32()
        return self.buf.read(length).decode('utf-8')

    def read_bytes(self) -> bytes:
        length = self.read_u32()
        return self.buf.read(length)

    def getvalue(self) -> bytes:
        return self.buf.getvalue()

    def tell(self) -> int:
        return self.buf.tell()

    def seek(self, pos: int) -> None:
        self.buf.seek(pos)


# ─────────────────────────────────────────────────────────────────────────────
# ThermalFrame: Atomic snapshot termiczny
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ThermalFrame:
    """
    Jeden immutable frame termiczny — snapshot całego Store w jednym ticku.
    
    Pola:
      timestamp: Unix nanosekund (dokładność dla seq)
      tick_number: Logiczny tick Store
      num_atoms: Liczba atomów w snapshocie
      
      atom_ids: Lista [id0, id1, ..., idN] — canonical order
      temperatures: [T0, T1, ..., TN] — float32, zawsze zsynchronizowane z atom_ids
      states: ["HOT", "WARM", "COLD", "TOMB", ...] — kanon FSM z karmazyn_atom
      entropies: [e0, e1, ..., eN] — float32, może być pusty
      
      vectors: {atom_id → bytes(float32[])} — HRR wektor lub None → brak wpisu
      
      store_stats: Dict z Store.stats() — snapshot zasobu (alive, reaped, etc)
      causality_hash: SHA256 frame'u poprzedniego (chain dla temporal queries)
    """

    timestamp: int
    tick_number: int
    num_atoms: int

    atom_ids: List[str] = field(default_factory=list)
    temperatures: List[float] = field(default_factory=list)
    states: List[str] = field(default_factory=list)
    entropies: List[float] = field(default_factory=list)

    vectors: Dict[str, bytes] = field(default_factory=dict)

    store_stats: Dict[str, Any] = field(default_factory=dict)
    causality_hash: str = ""

    def validate(self) -> Tuple[bool, str]:
        """Sprawdzenie spójności frame'u."""
        if self.num_atoms < 0:
            return False, "num_atoms < 0"
        if not (len(self.atom_ids) == self.num_atoms):
            return False, f"atom_ids length {len(self.atom_ids)} != num_atoms {self.num_atoms}"
        if not (len(self.temperatures) == self.num_atoms):
            return False, "temperatures length != num_atoms"
        if not (len(self.states) == self.num_atoms):
            return False, "states length != num_atoms"
        if self.entropies and len(self.entropies) != self.num_atoms:
            return False, f"entropies length {len(self.entropies)} != num_atoms"
        # Wektor: opcjonalny, ale jeśli jest — atom_id musi być w atom_ids
        for vid in self.vectors.keys():
            if vid not in self.atom_ids:
                return False, f"vector for unknown atom_id {vid}"
        return True, ""

    def envelope(self) -> Dict[str, Any]:
        """T_min/T_max/tick/kind — koperta bez payloadu (P5)."""
        temps = self.temperatures or [0.0]
        return {
            "tick": self.tick_number,
            "kind": KIND_KEY,
            "T_min": float(min(temps)),
            "T_max": float(max(temps)),
            "num_atoms": self.num_atoms,
            "causality": self.causality_hash,
        }

    def to_bytes(self) -> bytes:
        """Serializuj klatkę KEY (v2 + crc32 LE na końcu)."""
        return self._serialize_key()

    def _write_body(self, bs: "BinaryStream") -> None:
        for aid in self.atom_ids:
            bs.write_pascal_str(aid)
        for t in self.temperatures:
            bs.write_f32(max(0.0, min(T_MAX, t)))
        for s in self.states:
            bs.write_pascal_str(s)
        has_entropies = len(self.entropies) > 0
        bs.write_u8(1 if has_entropies else 0)
        if has_entropies:
            for e in self.entropies:
                bs.write_f32(max(0.0, e))
        bs.write_u32(len(self.vectors))
        for aid in sorted(self.vectors.keys()):
            bs.write_pascal_str(aid)
            bs.write_bytes(self.vectors[aid])
        stats_json = json.dumps(self.store_stats, sort_keys=True, default=str)
        bs.write_pascal_str(stats_json)
        bs.write_pascal_str(self.causality_hash)

    def _serialize_key(self) -> bytes:
        valid, msg = self.validate()
        if not valid:
            raise ValueError(f"ThermalFrame validation failed: {msg}")
        bs = BinaryStream()
        bs.write_u8(T_FRAME_VERSION)
        bs.write_u8(KIND_KEY)
        bs.write_u64(self.timestamp)
        bs.write_u64(self.tick_number)
        bs.write_u32(self.num_atoms)
        env = self.envelope()
        bs.write_f32(env["T_min"])
        bs.write_f32(env["T_max"])
        self._write_body(bs)
        payload = bs.getvalue()
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        return payload + struct.pack("<I", crc)

    def to_delta_bytes(self, key: "ThermalFrame") -> bytes:
        """P4: inter vs KEY (ten sam zbiór atom_ids)."""
        if self.atom_ids != key.atom_ids:
            raise ValueError("delta wymaga identycznego zbioru atom_ids — użyj keyframe")
        bs = BinaryStream()
        bs.write_u8(T_FRAME_VERSION)
        bs.write_u8(KIND_DELTA)
        bs.write_u64(self.timestamp)
        bs.write_u64(self.tick_number)
        bs.write_u32(self.num_atoms)
        env = self.envelope()
        bs.write_f32(env["T_min"])
        bs.write_f32(env["T_max"])
        bs.write_u64(key.tick_number)
        changes = []
        for i, aid in enumerate(self.atom_ids):
            flags = 0
            if self.temperatures[i] != key.temperatures[i]:
                flags |= DF_T
            if self.states[i] != key.states[i]:
                flags |= DF_STATE
            e = self.entropies[i] if self.entropies else None
            ke = key.entropies[i] if key.entropies else None
            if e != ke:
                flags |= DF_ENTROPY
            if self.vectors.get(aid) != key.vectors.get(aid):
                flags |= DF_VECTOR
            if flags:
                changes.append((i, flags, aid))
        bs.write_u32(len(changes))
        for i, flags, aid in changes:
            bs.write_u32(i)
            bs.write_u8(flags)
            if flags & DF_T:
                bs.write_f32(max(0.0, min(T_MAX, self.temperatures[i])))
            if flags & DF_STATE:
                bs.write_pascal_str(self.states[i])
            if flags & DF_ENTROPY:
                val = self.entropies[i] if self.entropies else 0.0
                bs.write_f32(max(0.0, val))
            if flags & DF_VECTOR:
                bs.write_bytes(self.vectors.get(aid, b""))
        stats_json = json.dumps(self.store_stats, sort_keys=True, default=str)
        bs.write_pascal_str(stats_json)
        bs.write_pascal_str(self.causality_hash)
        payload = bs.getvalue()
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        return payload + struct.pack("<I", crc)

    @staticmethod
    def _check_crc(data: bytes) -> bytes:
        if len(data) < 5:
            raise ValueError("ThermalFrame too short")
        payload, crc_b = data[:-4], data[-4:]
        stored = struct.unpack("<I", crc_b)[0]
        got = zlib.crc32(payload) & 0xFFFFFFFF
        if stored != got:
            raise ValueError(
                f"ThermalFrame CRC mismatch stored={stored:#010x} computed={got:#010x}")
        return payload

    @classmethod
    def header_from_bytes(cls, data: bytes) -> Optional[Dict[str, Any]]:
        if peek_thermal_header is not None:
            return peek_thermal_header(data)
        if not data or len(data) < 21:
            return None
        if data[0] == T_FRAME_VERSION and len(data) >= 30:
            kind = data[1]
            ts, tick = struct.unpack_from("<QQ", data, 2)
            num = struct.unpack_from("<I", data, 18)[0]
            tmin, tmax = struct.unpack_from("<ff", data, 22)
            return {
                "version": 2, "kind": kind, "timestamp": ts, "tick": tick,
                "num_atoms": num, "T_min": float(tmin), "T_max": float(tmax),
            }
        return None

    @classmethod
    def from_bytes(cls, data: bytes) -> "ThermalFrame":
        """Deserializuj KEY v2 (crc) albo v1 (bez crc). Delta → apply_delta."""
        if not data:
            raise ValueError("empty ThermalFrame")
        version = data[0]
        if version == T_FRAME_VERSION_V1:
            return cls._from_bytes_v1(data)
        if version != T_FRAME_VERSION:
            raise ValueError(f"ThermalFrame version mismatch: {version}")
        payload = cls._check_crc(data)
        kind = payload[1]
        if kind == KIND_DELTA:
            raise ValueError("delta wymaga ThermalFrame.apply_delta(key, data)")
        return cls._from_bytes_v2_key(payload)

    @classmethod
    def _read_tail(cls, bs: "BinaryStream", num_atoms: int):
        atom_ids = [bs.read_pascal_str() for _ in range(num_atoms)]
        temperatures = [bs.read_f32() for _ in range(num_atoms)]
        states = [bs.read_pascal_str() for _ in range(num_atoms)]
        has_entropies = bs.read_u8()
        entropies = [bs.read_f32() for _ in range(num_atoms)] if has_entropies else []
        vectors = {}
        num_vectors = bs.read_u32()
        for _ in range(num_vectors):
            aid = bs.read_pascal_str()
            vectors[aid] = bs.read_bytes()
        stats_json = bs.read_pascal_str()
        try:
            store_stats = json.loads(stats_json)
        except json.JSONDecodeError:
            store_stats = {}
        causality_hash = bs.read_pascal_str()
        return atom_ids, temperatures, states, entropies, vectors, store_stats, causality_hash

    @classmethod
    def _from_bytes_v2_key(cls, payload: bytes) -> "ThermalFrame":
        bs = BinaryStream(payload)
        bs.read_u8()
        bs.read_u8()
        timestamp = bs.read_u64()
        tick_number = bs.read_u64()
        num_atoms = bs.read_u32()
        bs.read_f32()
        bs.read_f32()
        atom_ids, temperatures, states, entropies, vectors, store_stats, causality = \
            cls._read_tail(bs, num_atoms)
        return cls(
            timestamp=timestamp, tick_number=tick_number, num_atoms=num_atoms,
            atom_ids=atom_ids, temperatures=temperatures, states=states,
            entropies=entropies, vectors=vectors, store_stats=store_stats,
            causality_hash=causality,
        )

    @classmethod
    def _from_bytes_v1(cls, data: bytes) -> "ThermalFrame":
        bs = BinaryStream(data)
        version = bs.read_u8()
        if version != T_FRAME_VERSION_V1:
            raise ValueError(f"ThermalFrame v1 mismatch: {version}")
        timestamp = bs.read_u64()
        tick_number = bs.read_u64()
        num_atoms = bs.read_u32()
        atom_ids, temperatures, states, entropies, vectors, store_stats, causality = \
            cls._read_tail(bs, num_atoms)
        return cls(
            timestamp=timestamp, tick_number=tick_number, num_atoms=num_atoms,
            atom_ids=atom_ids, temperatures=temperatures, states=states,
            entropies=entropies, vectors=vectors, store_stats=store_stats,
            causality_hash=causality,
        )

    @classmethod
    def apply_delta(cls, key: "ThermalFrame", data: bytes) -> "ThermalFrame":
        payload = cls._check_crc(data)
        if payload[0] != T_FRAME_VERSION or payload[1] != KIND_DELTA:
            raise ValueError("apply_delta: oczekiwano v2 KIND_DELTA")
        bs = BinaryStream(payload)
        bs.read_u8(); bs.read_u8()
        timestamp = bs.read_u64()
        tick_number = bs.read_u64()
        num_atoms = bs.read_u32()
        bs.read_f32(); bs.read_f32()
        base_tick = bs.read_u64()
        if base_tick != key.tick_number:
            raise ValueError(
                f"delta base_tick={base_tick} != key.tick={key.tick_number}")
        if num_atoms != key.num_atoms:
            raise ValueError("delta num_atoms != key")
        temps = list(key.temperatures)
        states = list(key.states)
        had_entropy = bool(key.entropies)
        entropies = list(key.entropies) if had_entropy else [0.0] * num_atoms
        entropy_touched = False
        vectors = dict(key.vectors)
        n_changed = bs.read_u32()
        for _ in range(n_changed):
            idx = bs.read_u32()
            flags = bs.read_u8()
            aid = key.atom_ids[idx]
            if flags & DF_T:
                temps[idx] = bs.read_f32()
            if flags & DF_STATE:
                states[idx] = bs.read_pascal_str()
            if flags & DF_ENTROPY:
                entropies[idx] = bs.read_f32()
                entropy_touched = True
            if flags & DF_VECTOR:
                vectors[aid] = bs.read_bytes()
        if not had_entropy and not entropy_touched:
            entropies = []
        stats_json = bs.read_pascal_str()
        try:
            store_stats = json.loads(stats_json)
        except json.JSONDecodeError:
            store_stats = dict(key.store_stats)
        causality = bs.read_pascal_str()
        return cls(
            timestamp=timestamp, tick_number=tick_number, num_atoms=num_atoms,
            atom_ids=list(key.atom_ids), temperatures=temps, states=states,
            entropies=entropies, vectors=vectors, store_stats=store_stats,
            causality_hash=causality,
        )

    @classmethod
    def decode(cls, data: bytes, key: Optional["ThermalFrame"] = None) -> "ThermalFrame":
        hdr = cls.header_from_bytes(data)
        if hdr and hdr.get("kind") == KIND_DELTA:
            if key is None:
                raise ValueError("decode delta bez klatki KEY")
            return cls.apply_delta(key, data)
        return cls.from_bytes(data)

    def compute_hash(self) -> str:
        """Deterministic SHA256 hash całego frame'u (for causality chain)."""
        h = hashlib.sha256()
        h.update(self.to_bytes())
        return h.hexdigest()

    def size_bytes(self) -> int:
        """Rozmiar serializowanego frame'u."""
        return len(self.to_bytes())

    def density(self) -> float:
        """Gęstość wektora: ile atomów ma wektor."""
        if self.num_atoms == 0:
            return 0.0
        return len(self.vectors) / self.num_atoms

    def summary(self) -> Dict[str, Any]:
        """Podsumowanie frame'u (lekkie)."""
        return {
            "tick": self.tick_number,
            "timestamp": self.timestamp,
            "atoms": self.num_atoms,
            "vectors": len(self.vectors),
            "size_bytes": self.size_bytes(),
            "density": self.density(),
            "causality": self.causality_hash[:16] + "..."
        }


class ThermalGopEncoder:
    """P4: KEY co gop_size ticków albo gdy zmieni się zbiór atom_ids."""

    def __init__(self, gop_size: int = 16):
        self.gop_size = max(1, int(gop_size))
        self.key: Optional[ThermalFrame] = None
        self.since_key = 0

    def reset(self) -> None:
        self.key = None
        self.since_key = 0

    def encode(self, frame: ThermalFrame) -> Tuple[int, bytes]:
        """Zwraca (kind, blob)."""
        force_key = (
            self.key is None
            or self.since_key >= self.gop_size
            or frame.atom_ids != self.key.atom_ids
        )
        if force_key:
            blob = frame.to_bytes()
            self.key = frame
            self.since_key = 0
            return KIND_KEY, blob
        blob = frame.to_delta_bytes(self.key)
        key_blob = frame.to_bytes()
        if len(blob) >= len(key_blob):
            self.key = frame
            self.since_key = 0
            return KIND_KEY, key_blob
        self.since_key += 1
        return KIND_DELTA, blob


# ─────────────────────────────────────────────────────────────────────────────
# ThermalLog: Store.tick() → KAFS journal → .kafd
# ─────────────────────────────────────────────────────────────────────────────

class ThermalLog:
    """
    Kontroler: Store → klatki termiczne → KAFD/KAFS.

    Użycie:
        store = Store(thermal=True)
        tlog = ThermalLog(store, path="thermal.kafd", auto_snapshot=True)
        store.tick()
        tlog.as_of_tick(1)
        tlog.close()
    """

    def __init__(self, store, path: str = "thermal.kafd",
                 auto_snapshot: bool = False,
                 journal_path: Optional[str] = None,
                 kafd_path: Optional[str] = None,
                 gop_size: int = 16,
                 compact_every: int = 0,
                 fsync: bool = True,
                 world: Optional[str] = None):
        self.store = store
        self.auto_snapshot = auto_snapshot
        base, ext = os.path.splitext(path)
        if ext.lower() == ".kafs":
            self.journal_path = journal_path or path
            self.kafd_path = kafd_path or (base + ".kafd")
        else:
            self.kafd_path = kafd_path or (
                path if ext.lower() == ".kafd" else base + ".kafd"
            )
            self.journal_path = journal_path or (
                os.path.splitext(self.kafd_path)[0] + ".kafs"
            )
        self.compact_every = int(compact_every or 0)
        self.fsync = fsync
        self.world = world if world is not None else os.path.splitext(
            os.path.basename(self.kafd_path))[0]

        self.last_hash = ""
        self.frame_count = 0
        self.total_bytes = 0
        self.frames_by_tick: Dict[int, str] = {}  # tick_number → hash

        self.lock = threading.RLock()
        self.gop = ThermalGopEncoder(gop_size=gop_size)
        self.journal: Optional[Any] = None
        self._journal_ready = False

        if auto_snapshot:
            self._install_hook()

    def _install_hook(self) -> None:
        """Zarejestruj callback na Store.tick() — wywoła snapshot."""
        if hasattr(self.store, 'events') and hasattr(self.store.events, 'on'):
            self.store.events.on('tick_batch', self._on_tick_batch)

    def _on_tick_batch(self, batch_info: Dict[str, Any]) -> None:
        """Callback z tick_batch event. batch_info = {atoms, reaped, retained}."""
        try:
            self.commit_snapshot()
        except Exception as e:
            # Log, ale nie krusz ticka
            print(f"[ThermalLog] snapshot error: {e}")

    def open_journal(self) -> bool:
        """Otwórz dziennik KAFS (P1). Zwraca True gdy zapis na dysk jest możliwy."""
        if not _HAS_KAFD or KAFDJournal is None:
            self._journal_ready = False
            return False
        try:
            self.journal = KAFDJournal(
                self.journal_path,
                fsync=self.fsync,
                meta={"thermal": True, "version": THERMAL_LOG_VERSION},
                world=self.world,
            )
            self._journal_ready = True
            return True
        except Exception as e:
            print(f"[ThermalLog] journal open error: {e}")
            self.journal = None
            self._journal_ready = False
            return False

    def snapshot(self) -> Optional[ThermalFrame]:
        """
        Zrób snapshot termalny z bieżącego stanu Store.
        Zwraca ThermalFrame lub None (jeśli Store nie dostępny).
        """
        with self.store.lock:
            try:
                atoms_fn = getattr(self.store, "atoms", None)
                if callable(atoms_fn):
                    atoms = list(atoms_fn())
                else:
                    atoms = self.store._reg.atoms()
            except (AttributeError, RuntimeError):
                return None

            atom_ids = []
            temperatures = []
            states = []
            entropies = []
            vectors = {}

            for atom in atoms:
                atom_ids.append(atom.id)
                temperatures.append(atom.T)
                states.append(atom.state)

                # Entropy opcjonalny
                entropy_val = atom.metadata.get("entropy", 0.0)
                entropies.append(float(entropy_val))

                # Wektor HRR (jeśli dostępny)
                try:
                    vec = self.store.atom_vector(atom)
                    if vec is not None:
                        if _HAS_NUMPY and isinstance(vec, np.ndarray):
                            vec_bytes = vec.astype(np.float32).tobytes()
                        else:
                            # Fallback: bezpośrednio jako bytes (musi być float32)
                            vec_bytes = vec if isinstance(vec, bytes) else b''
                        if vec_bytes:
                            vectors[atom.id] = vec_bytes
                except (AttributeError, TypeError):
                    pass

            frame = ThermalFrame(
                timestamp=int(time.time_ns()),
                tick_number=self.store.tick_count if hasattr(self.store, 'tick_count') else 0,
                num_atoms=len(atoms),
                atom_ids=atom_ids,
                temperatures=temperatures,
                states=states,
                entropies=entropies,
                vectors=vectors,
                store_stats=self.store.stats(),
                causality_hash=self.last_hash
            )

            return frame

    def commit_snapshot(self, frame: Optional[ThermalFrame] = None) -> bool:
        """
        Snapshot → GOP → append KAFS. False gdy nic nie zapisano na dysk.
        """
        with self.lock:
            if frame is None:
                frame = self.snapshot()
                if frame is None:
                    return False

            valid, msg = frame.validate()
            if not valid:
                print(f"[ThermalLog] frame invalid: {msg}")
                return False

            frame.causality_hash = frame.causality_hash or self.last_hash
            kind, blob = self.gop.encode(frame)
            env = frame.envelope()

            if self.journal is None:
                if not self.open_journal():
                    return False

            try:
                atom = KAFDAtom(
                    f"t{frame.tick_number}",
                    blob,
                    mime=MIME_THERMAL,
                    T=env["T_max"],
                    T_max=T_MAX,
                    atype=A_THERMAL,
                )
                self.journal.append_atom(atom)
                self.journal.checkpoint(
                    tick=frame.tick_number,
                    causality=frame.causality_hash,
                    T_min=env["T_min"],
                    T_max=env["T_max"],
                    kind=kind,
                )
            except Exception as e:
                print(f"[ThermalLog] commit error: {e}")
                return False

            frame_hash = frame.compute_hash()
            self.last_hash = frame_hash
            self.frame_count += 1
            self.total_bytes += len(blob)
            self.frames_by_tick[frame.tick_number] = frame_hash

            if self.compact_every and self.frame_count % self.compact_every == 0:
                self.seal()

            return True

    def seal(self) -> dict:
        """P2: compact journal → seekable KAFD v2.1, reset WAL."""
        if not _HAS_KAFD or compact_journal is None:
            return {"ok": False, "reason": "no kafd"}
        with self.lock:
            if self.journal is not None:
                self.journal.close(write_end=False)
                self.journal = None
            info = compact_journal(
                self.journal_path,
                self.kafd_path,
                layout="footer",
                reset_journal=True,
                meta={
                    "source": "thermal-journal",
                    "thermal": True,
                    "frames": self.frame_count,
                },
                world=self.world,
            )
            self.open_journal()
            self.gop.reset()
            info["ok"] = True
            return info

    def load_frame_by_tick(self, tick_number: int) -> Optional[ThermalFrame]:
        """P5: as_of_tick — ostatnia klatka o tick <= tick_number."""
        return self.as_of_tick(tick_number)

    def as_of_tick(self, tick_number: int) -> Optional[ThermalFrame]:
        """Rekonstrukcja: max(kafd, journal) o tick ≤ żądany."""
        k = self._as_of_tick_kafd(tick_number)
        j = self._as_of_tick_journal(tick_number)
        cand = [f for f in (k, j) if f is not None]
        if not cand:
            return None
        return max(cand, key=lambda f: f.tick_number)

    def _iter_journal_atoms(self):
        if not _HAS_KAFD or not os.path.exists(self.journal_path):
            return
        from karmazyn_kafd import KAFDAtom, KAFDFlowReader
        try:
            from karmazyn_cipher import is_kx1, unwrap_payload
        except ImportError:
            is_kx1 = unwrap_payload = None  # type: ignore
        with open(self.journal_path, "rb") as f:
            r = KAFDFlowReader(f, sorted_by_T=False)
            try:
                r.read_header()
            except Exception:
                return
            for atom in r.iter_atoms():
                data = atom.data
                if is_kx1 is not None and is_kx1(data):
                    try:
                        data = unwrap_payload(
                            data, self.world, atom.id.encode("utf-8")
                        )
                        atom = KAFDAtom(
                            atom.id, data, mime=atom.mime, T=atom.T,
                            T_max=atom.T_max, atype=atom.atype,
                        )
                    except Exception:
                        pass
                yield atom

    def _as_of_tick_journal(self, tick_number: int) -> Optional[ThermalFrame]:
        last_key = None
        last_frame = None
        try:
            for atom in self._iter_journal_atoms():
                hdr = ThermalFrame.header_from_bytes(atom.data) or {}
                t = int(hdr.get("tick") or 0)
                if t > tick_number:
                    break
                kind = int(hdr.get("kind") or 0)
                if kind == KIND_KEY:
                    last_key = ThermalFrame.from_bytes(atom.data)
                    last_frame = last_key
                elif kind == KIND_DELTA and last_key is not None:
                    last_frame = ThermalFrame.apply_delta(last_key, atom.data)
        except Exception:
            return last_frame
        return last_frame

    def _as_of_tick_kafd(self, tick_number: int) -> Optional[ThermalFrame]:
        if not _HAS_KAFD or not os.path.exists(self.kafd_path):
            return None
        try:
            reader = KAFDReader.from_path(self.kafd_path, world=self.world)
        except Exception:
            return None
        try:
            idx = sorted(reader.tick_index, key=lambda r: r["tick"])
            if not idx:
                # bez KTIX — skan id tN
                return self._as_of_tick_kafd_scan(reader, tick_number)
            last_key_rec = None
            target_rec = None
            for rec in idx:
                if rec["tick"] > tick_number:
                    break
                if rec["kind"] == KIND_KEY:
                    last_key_rec = rec
                target_rec = rec
            if target_rec is None or last_key_rec is None:
                return None
            key_atom = reader.get_atom(f"t{last_key_rec['tick']}")
            if key_atom is None:
                return None
            key = ThermalFrame.from_bytes(key_atom.data)
            if target_rec["tick"] == last_key_rec["tick"]:
                return key
            d_atom = reader.get_atom(f"t{target_rec['tick']}")
            if d_atom is None:
                return key
            return ThermalFrame.decode(d_atom.data, key=key)
        finally:
            reader.close()

    def _as_of_tick_kafd_scan(self, reader, tick_number: int) -> Optional[ThermalFrame]:
        last_key = None
        last_frame = None
        for aid in reader.atom_ids:
            atom = reader.get_atom(aid)
            if atom is None:
                continue
            hdr = ThermalFrame.header_from_bytes(atom.data) or {}
            t = int(hdr.get("tick") or 0)
            if t > tick_number:
                continue
            kind = int(hdr.get("kind") or 0)
            if kind == KIND_KEY:
                last_key = ThermalFrame.from_bytes(atom.data)
                last_frame = last_key
            elif kind == KIND_DELTA and last_key is not None:
                fr = ThermalFrame.apply_delta(last_key, atom.data)
                if fr.tick_number <= tick_number:
                    last_frame = fr
        return last_frame

    def ticks_in_T_range(self, T_min: float, T_max: float) -> List[Dict[str, Any]]:
        """P5: query T z koperty — bez pełnego decode."""
        hits: List[Dict[str, Any]] = []
        if _HAS_KAFD and os.path.exists(self.kafd_path):
            try:
                reader = KAFDReader.from_path(self.kafd_path, world=self.world)
                try:
                    hits.extend(reader.ticks_in_T_range(T_min, T_max))
                finally:
                    reader.close()
            except Exception:
                pass
        if _HAS_KAFD and os.path.exists(self.journal_path):
            try:
                for atom in self._iter_journal_atoms():
                    hdr = ThermalFrame.header_from_bytes(atom.data)
                    if not hdr:
                        continue
                    if hdr["T_max"] >= T_min and hdr["T_min"] <= T_max:
                        hits.append(hdr)
            except Exception:
                pass
        return hits

    def verify_chain(self, from_tick: int = 0, to_tick: Optional[int] = None) -> Tuple[bool, str]:
        """P5: causality_hash[i] == hash(frame[i-1]) na zrekonstruowanych klatkach."""
        ticks = sorted(self.frames_by_tick.keys())
        if not ticks and os.path.exists(self.journal_path):
            ticks = []
            for atom in self._iter_journal_atoms():
                hdr = ThermalFrame.header_from_bytes(atom.data) or {}
                if "tick" in hdr:
                    ticks.append(int(hdr["tick"]))
        prev_hash = ""
        for t in ticks:
            if t < from_tick:
                continue
            if to_tick is not None and t > to_tick:
                break
            fr = self.as_of_tick(t)
            if fr is None:
                return False, f"brak klatki tick={t}"
            if (fr.causality_hash or "") != prev_hash:
                return False, f"łańcuch pękł przy tick={t}"
            prev_hash = fr.compute_hash()
        return True, ""

    def stats(self) -> Dict[str, Any]:
        """Statystyki ThermalLog."""
        with self.lock:
            return {
                "frames_committed": self.frame_count,
                "total_bytes": self.total_bytes,
                "avg_frame_size": self.total_bytes // max(1, self.frame_count),
                "unique_ticks": len(self.frames_by_tick),
                "journal_ready": self._journal_ready,
                "journal_path": self.journal_path,
                "kafd_path": self.kafd_path,
                "journal_frames": getattr(self.journal, "n_frames", 0),
            }

    def close(self) -> None:
        """Zamknij dziennik KAFS."""
        with self.lock:
            if self.journal is not None:
                self.journal.close(write_end=False)
                self.journal = None
            self._journal_ready = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Query Interface: Szukanie po temperaturze, stanie, timeline
# ─────────────────────────────────────────────────────────────────────────────

class ThermalQuery:
    """Referencyjny query in-memory (testy). Produkcja: ThermalLog.as_of_tick / ticks_in_T_range."""

    def __init__(self, tmem: ThermalLog):
        self.tmem = tmem
        self._frame_cache: Dict[int, ThermalFrame] = {}

    def add_frame_to_cache(self, frame: ThermalFrame) -> None:
        """Dodaj frame do cache'u (dla testów)."""
        self._frame_cache[frame.tick_number] = frame

    def search_by_temperature(
        self,
        T_min: float,
        T_max: float,
        atom_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Szukaj snapshot'ów, gdzie atom (opcjonalnie) był w [T_min, T_max].
        Cache in-memory — na dysku: ThermalLog.ticks_in_T_range.
        """
        results = []

        for frame in sorted(self._frame_cache.values(), key=lambda f: f.tick_number):
            if atom_id:
                try:
                    idx = frame.atom_ids.index(atom_id)
                    T = frame.temperatures[idx]
                    if T_min <= T <= T_max:
                        results.append({
                            "tick": frame.tick_number,
                            "atom_id": atom_id,
                            "T": T,
                            "state": frame.states[idx],
                            "entropy": frame.entropies[idx] if frame.entropies else None,
                        })
                except (ValueError, IndexError):
                    pass
            else:
                # Zwróć całą klatkę (wszystkie atomy)
                in_range = sum(
                    1 for t in frame.temperatures if T_min <= t <= T_max
                )
                if in_range > 0:
                    results.append({
                        "tick": frame.tick_number,
                        "atoms_in_range": in_range,
                        "total_atoms": frame.num_atoms,
                        "store_stats": frame.store_stats,
                    })

            if len(results) >= limit:
                break

        return results

    def search_by_state(
        self,
        state: str,
        atom_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Szukaj snapshot'ów, gdzie atom był w danym stanie."""
        results = []

        for frame in sorted(self._frame_cache.values(), key=lambda f: f.tick_number):
            if atom_id:
                try:
                    idx = frame.atom_ids.index(atom_id)
                    if frame.states[idx] == state:
                        results.append({
                            "tick": frame.tick_number,
                            "atom_id": atom_id,
                            "state": state,
                            "T": frame.temperatures[idx],
                        })
                except (ValueError, IndexError):
                    pass
            else:
                count = sum(1 for s in frame.states if s == state)
                if count > 0:
                    results.append({
                        "tick": frame.tick_number,
                        "atoms_in_state": count,
                        "total_atoms": frame.num_atoms,
                    })

            if len(results) >= limit:
                break

        return results

    def timeline_of_atom(self, atom_id: str) -> List[Dict[str, Any]]:
        """Timeline: [tick, T, state, entropy] dla jednego atomu."""
        timeline = []

        for frame in sorted(self._frame_cache.values(), key=lambda f: f.tick_number):
            try:
                idx = frame.atom_ids.index(atom_id)
                timeline.append({
                    "tick": frame.tick_number,
                    "T": frame.temperatures[idx],
                    "state": frame.states[idx],
                    "entropy": frame.entropies[idx] if frame.entropies else None,
                    "has_vector": atom_id in frame.vectors,
                })
            except ValueError:
                pass

        return timeline

    def causality_chain(self, from_tick: int, to_tick: int) -> List[Dict[str, Any]]:
        """Zwróć łańcuch przyczynowości (hasz) między tickami."""
        chain = []

        ticks = sorted([t for t in self._frame_cache.keys() if from_tick <= t <= to_tick])
        for tick in ticks:
            frame = self._frame_cache[tick]
            chain.append({
                "tick": tick,
                "hash": frame.compute_hash()[:16] + "...",
                "causality_prev": frame.causality_hash[:16] + "..." if frame.causality_hash else "NONE",
            })

        return chain


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def estimate_compression_ratio(frames: List[ThermalFrame]) -> float:
    """
    Oszacuj stosunek kompresji dla grupy frame'ów.
    Wyżej = więcej redundancji między klatkami (lepiej dla GOP delta).
    """
    if not frames:
        return 0.0

    # Entropy frame'ów (Shannon)
    total_bytes = sum(len(f.to_bytes()) for f in frames)
    if total_bytes == 0:
        return 0.0

    # Naiwnie: ile bajtów zmienia się między tickami
    diffs = 0
    for i in range(1, len(frames)):
        prev_bytes = frames[i - 1].to_bytes()
        curr_bytes = frames[i].to_bytes()
        for p, c in zip(prev_bytes, curr_bytes):
            if p != c:
                diffs += 1

    # Stosunek zmiany
    changing_ratio = diffs / max(1, len(frames[0].to_bytes()) * len(frames))
    compression_potential = 1.0 - changing_ratio
    return compression_potential


# ─────────────────────────────────────────────────────────────────────────────
# Tests (zero-dep)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    def test_thermal_frame_roundtrip():
        """Test: serialize → deserialize → sprawdzenie spójności."""
        frame = ThermalFrame(
            timestamp=int(time.time_ns()),
            tick_number=42,
            num_atoms=3,
            atom_ids=["a1", "a2", "a3"],
            temperatures=[80.0, 50.0, 1.5],
            states=["HOT", "WARM", "TOMB"],
            entropies=[1.5, 2.0, 0.5],
            vectors={"a1": b"deadbeef"},
            store_stats={"total": 3, "alive": 2, "reaped": 1},
            causality_hash="abc123"
        )

        # Serialize
        data = frame.to_bytes()
        assert len(data) > 0, "Frame serialization failed"

        # Deserialize
        frame2 = ThermalFrame.from_bytes(data)
        assert frame2.tick_number == 42
        assert frame2.num_atoms == 3
        assert frame2.atom_ids == ["a1", "a2", "a3"]
        assert abs(frame2.temperatures[0] - 80.0) < 1e-5
        assert abs(frame2.temperatures[1] - 50.0) < 1e-5
        assert abs(frame2.temperatures[2] - 1.5) < 1e-5
        assert frame2.states == ["HOT", "WARM", "TOMB"]
        assert frame2.vectors["a1"] == b"deadbeef"

        # Hash
        hash1 = frame.compute_hash()
        hash2 = frame2.compute_hash()
        assert hash1 == hash2, "Hash mismatch after roundtrip"

        print("✓ test_thermal_frame_roundtrip PASS")

    def test_thermal_frame_validation():
        """Test: sprawdzenie walidacji frame'u."""
        # Valid frame
        frame = ThermalFrame(
            timestamp=1000,
            tick_number=1,
            num_atoms=2,
            atom_ids=["a", "b"],
            temperatures=[300.0, 250.0],
            states=["HOT", "WARM"]
        )
        valid, msg = frame.validate()
        assert valid, f"Valid frame rejected: {msg}"

        # Invalid: mismatch num_atoms
        frame_bad = ThermalFrame(
            timestamp=1000,
            tick_number=1,
            num_atoms=3,  # ← źle
            atom_ids=["a", "b"],
            temperatures=[80.0, 50.0],
            states=["HOT", "WARM"]
        )
        valid, msg = frame_bad.validate()
        assert not valid, "Invalid frame accepted"

        # Invalid: vector for unknown atom
        frame_bad2 = ThermalFrame(
            timestamp=1000,
            tick_number=1,
            num_atoms=1,
            atom_ids=["a"],
            temperatures=[300.0],
            states=["HOT"],
            vectors={"unknown": b"xyz"}  # ← atom nie istnieje
        )
        valid, msg = frame_bad2.validate()
        assert not valid, "Invalid vector reference accepted"

        print("✓ test_thermal_frame_validation PASS")

    def test_binary_stream():
        """Test: BinaryStream read/write."""
        bs = BinaryStream()
        bs.write_u32(42)
        bs.write_f32(3.14)
        bs.write_pascal_str("hello")

        data = bs.getvalue()
        bs2 = BinaryStream(data)
        assert bs2.read_u32() == 42
        assert abs(bs2.read_f32() - 3.14) < 0.01
        assert bs2.read_pascal_str() == "hello"

        print("✓ test_binary_stream PASS")

    def test_query_interface():
        """Test: ThermalQuery."""
        # Mock Store
        class MockStore:
            lock = threading.RLock()
            def stats(self):
                return {"total": 3, "alive": 2}
            def _reg_atoms(self):
                return []
        
        tmem = ThermalLog(MockStore())
        query = ThermalQuery(tmem)

        # Dodaj frames do cache
        f1 = ThermalFrame(
            timestamp=1000,
            tick_number=1,
            num_atoms=2,
            atom_ids=["x", "y"],
            temperatures=[300.0, 200.0],
            states=["HOT", "COLD"]
        )
        f2 = ThermalFrame(
            timestamp=2000,
            tick_number=2,
            num_atoms=2,
            atom_ids=["x", "y"],
            temperatures=[280.0, 180.0],
            states=["WARM", "TOMB"]
        )

        query.add_frame_to_cache(f1)
        query.add_frame_to_cache(f2)

        # Szukaj po temperaturze
        results = query.search_by_temperature(T_min=250.0, T_max=350.0, atom_id="x")
        assert len(results) > 0, "Temperature search returned no results"
        assert results[0]["atom_id"] == "x"

        # Timeline
        timeline = query.timeline_of_atom("x")
        assert len(timeline) == 2, "Timeline should have 2 entries"
        assert timeline[0]["T"] == 300.0
        assert timeline[1]["T"] == 280.0

        print("✓ test_query_interface PASS")

    def test_compression_estimate():
        """Test: estimate_compression_ratio."""
        f1 = ThermalFrame(
            timestamp=1000,
            tick_number=1,
            num_atoms=1,
            atom_ids=["a"],
            temperatures=[80.0],
            states=["HOT"]
        )
        f2 = ThermalFrame(
            timestamp=1100,
            tick_number=2,
            num_atoms=1,
            atom_ids=["a"],
            temperatures=[299.9],  # Nieznaczna zmiana
            states=["HOT"]
        )

        ratio = estimate_compression_ratio([f1, f2])
        assert 0.0 <= ratio <= 1.0, f"Compression ratio out of range: {ratio}"
        print(f"✓ test_compression_estimate PASS (ratio={ratio:.2%})")

    def test_gop_delta():
        ids = [f"a{i}" for i in range(24)]
        t0 = [50.0] * 24
        t1 = list(t0)
        t1[0] = 49.0
        key = ThermalFrame(
            timestamp=1, tick_number=1, num_atoms=24,
            atom_ids=ids, temperatures=t0,
            states=["WARM"] * 24, causality_hash="",
        )
        nxt = ThermalFrame(
            timestamp=2, tick_number=2, num_atoms=24,
            atom_ids=ids, temperatures=t1,
            states=["WARM"] * 24, causality_hash=key.compute_hash(),
        )
        enc = ThermalGopEncoder(gop_size=8)
        knd, blob0 = enc.encode(key)
        assert knd == KIND_KEY
        knd, blob1 = enc.encode(nxt)
        assert knd == KIND_DELTA
        assert len(blob1) < len(blob0)
        back = ThermalFrame.decode(blob1, key=key)
        assert back.tick_number == 2
        assert abs(back.temperatures[0] - 49.0) < 1e-4
        assert back.states == ["WARM"] * 24
        print("✓ test_gop_delta PASS")

    # Run all tests
    try:
        test_thermal_frame_roundtrip()
        test_thermal_frame_validation()
        test_binary_stream()
        test_query_interface()
        test_compression_estimate()
        test_gop_delta()
        print("\n✅ All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)