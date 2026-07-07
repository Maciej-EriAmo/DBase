#!/usr/bin/env python3
"""
karmazyn_proca.py — Proca Field Semantic Deduplication v1.4.1
==============================================================
KarmazynOS — Maciej Mazur, Warsaw 2026

Poprawki v1.4.1:
  - M_VECTOR_REGISTRY do interpretacji wersji m_vector
  - Usunięta normalizacja z _synth_phi (jedna w phi_coords_from_manifest_json)
  - Dodane _proca_v, phi_dim do ProcaCoordinate JSON
  - Poprawione is_proca_json (odporne na formatowanie)
"""

import hashlib, json, os, struct, tempfile, time
from typing import Dict, List, Optional, Tuple
import numpy as np

PFLD_MAGIC   = b"PFLD"
PFLD_VERSION = 0x0104
PHI_DIM      = 15
PFLD_EXT     = ".pfld"
M_VECTOR_VERSION = 1

DEFAULT_M_15_v1 = [0.5, 0.5, 0.5, 0.6, 0.5, 0.6, 0.5, 0.4, 2.0, 1.0, 0.3, 0.2, 0.8, 0.4, 0.1]
DEFAULT_M_VECTOR = np.array(DEFAULT_M_15_v1, dtype=np.float32)

M_VECTOR_REGISTRY = {
    1: DEFAULT_M_15_v1,
}

DEFAULT_THRESHOLD = 0.60
MIN_DEDUP_SIZE = 128
MAX_PFLD_SIZE  = 50 * 1024 * 1024


def default_m_vector_for_dim(n: int) -> np.ndarray:
    v = M_VECTOR_REGISTRY.get(M_VECTOR_VERSION, DEFAULT_M_15_v1)
    if n == 15: return np.array(v, dtype=np.float32)
    if n < 15: return np.array(v[:n], dtype=np.float32)
    ext = np.full(n, 0.5, dtype=np.float32)
    ext[:15] = v
    return ext


def yukawa_similarity(phi_a: np.ndarray, phi_b: np.ndarray, m_vector: np.ndarray) -> float:
    if len(phi_a) != len(phi_b) or len(phi_a) != len(m_vector):
        raise ValueError("Niezgodność wymiarów")
    delta = phi_a - phi_b
    r_eff = float(np.sqrt(np.sum((m_vector * delta) ** 2)))
    return float(np.exp(-r_eff))


def compton_wavelength(m: float) -> float: return 1.0 / max(m, 1e-9)

def threshold_radius(m: float, threshold: float = DEFAULT_THRESHOLD) -> float:
    return -np.log(max(threshold, 1e-10)) / max(m, 1e-9)


def phi_coords_from_bubble(bubble) -> Optional[np.ndarray]:
    for attr in ('S_sem', 'S_struct'):
        v = getattr(bubble, attr, None)
        if v is not None:
            arr = np.asarray(v, dtype=np.float32).flatten()
            if arr.size > 0: return arr
    return None


def phi_coords_from_manifest_json(d: dict) -> Optional[np.ndarray]:
    result = None
    if 'phi_coords' in d:
        try:
            arr = np.asarray(d['phi_coords'], dtype=np.float32).flatten()
            if arr.size > 0: result = arr
        except: pass
    if result is None:
        for key in ('S_sem', 'S_struct'):
            if key in d:
                try:
                    arr = np.asarray(d[key], dtype=np.float32).flatten()
                    if arr.size > 0:
                        result = arr; break
                except: pass
    if result is None:
        result = _synth_phi(d)
    if result is None: return None
    norm = float(np.linalg.norm(result))
    if norm < 1e-9: return None
    return result / norm


def _synth_phi(d: dict) -> Optional[np.ndarray]:
    try:
        T = float(d.get('T', 50.0)) / 100.0
        state = d.get('state', 'WARM')
        S_hash = int(hashlib.sha256(str(d.get('S', '')).encode()).hexdigest()[:8], 16)
        phi = np.zeros(PHI_DIM, dtype=np.float32)
        phi[8]  = T * 2.0 - 1.0
        phi[9]  = (S_hash % 1000) / 500.0 - 1.0
        phi[10] = float(d.get('age', 0)) / 1000.0
        state_map = {'HOT': (0, 0.8), 'COLD': (4, 0.7), 'TOMB': (5, 0.9)}
        if state in state_map:
            idx, val = state_map[state]
            phi[idx] = val
        # Normalizacja będzie wykonana przez phi_coords_from_manifest_json
        return phi
    except: return None


class ProcaFieldSource:
    __slots__ = ("field_id", "phi_source", "m_vector", "m_vector_version",
                 "data", "record_id", "phi_dim", "T", "created", "dirty")

    def __init__(self, record_id: str, data: bytes, phi_source: np.ndarray,
                 m_vector: np.ndarray = None, m_vector_version: int = M_VECTOR_VERSION,
                 T: float = 50.0):
        self.record_id = record_id
        self.data = data
        self.phi_source = np.asarray(phi_source, dtype=np.float32)
        self.phi_dim = len(self.phi_source)
        if m_vector is not None:
            self.m_vector = np.asarray(m_vector, dtype=np.float32)
        else:
            self.m_vector = default_m_vector_for_dim(self.phi_dim)
        if len(self.m_vector) != self.phi_dim:
            raise ValueError(f"Niezgodność wymiarów m_vector")
        self.m_vector_version = m_vector_version
        self.T = float(T)
        self.field_id = hashlib.sha256(data).hexdigest()
        self.created = time.time()
        self.dirty = True

    def covers(self, phi, threshold=DEFAULT_THRESHOLD):
        if phi is None or len(phi) != self.phi_dim: return False
        return yukawa_similarity(self.phi_source, phi, self.m_vector) >= threshold

    def coordinate_delta(self, phi):
        if len(phi) != self.phi_dim:
            raise ValueError(f"Nieprawidłowy wymiar phi")
        return np.asarray(phi, dtype=np.float32) - self.phi_source

    def serialize(self) -> bytes:
        import zlib
        body = bytearray()
        body += PFLD_MAGIC
        body += struct.pack('>H', PFLD_VERSION)
        fid_b = self.field_id.encode('ascii')
        body += struct.pack('>H', len(fid_b)) + fid_b
        body += struct.pack('>H', self.phi_dim)
        body += struct.pack('>H', self.m_vector_version)
        body += self.phi_source.astype('>f4').tobytes()
        body += self.m_vector.astype('>f4').tobytes()
        body += struct.pack('>f', self.T)
        rid_b = self.record_id.encode('utf-8')
        body += struct.pack('>H', len(rid_b)) + rid_b
        body += struct.pack('>Q', len(self.data))
        body += self.data
        body += struct.pack('>I', zlib.crc32(bytes(body)) & 0xFFFFFFFF)
        return bytes(body)

    @classmethod
    def deserialize(cls, raw: bytes) -> "ProcaFieldSource":
        import zlib
        if len(raw) > MAX_PFLD_SIZE: raise ValueError("Plik za duży")
        if raw[:4] != PFLD_MAGIC: raise ValueError("Zły magic")
        crc_stored = struct.unpack('>I', raw[-4:])[0]
        if crc_stored != (zlib.crc32(raw[:-4]) & 0xFFFFFFFF):
            raise ValueError("CRC niezgodny")
        off = 4
        _ver = struct.unpack('>H', raw[off:off+2])[0]; off += 2
        fid_len = struct.unpack('>H', raw[off:off+2])[0]; off += 2
        field_id = raw[off:off+fid_len].decode('ascii'); off += fid_len
        phi_dim = struct.unpack('>H', raw[off:off+2])[0]; off += 2
        m_vec_ver = struct.unpack('>H', raw[off:off+2])[0]; off += 2
        if m_vec_ver not in M_VECTOR_REGISTRY:
            raise ValueError(f"Nieznana wersja m_vector: {m_vec_ver}")
        phi_bytes = phi_dim * 4
        phi_source = np.frombuffer(raw[off:off+phi_bytes], dtype='>f4').astype(np.float32).copy(); off += phi_bytes
        m_vector = np.frombuffer(raw[off:off+phi_bytes], dtype='>f4').astype(np.float32).copy(); off += phi_bytes
        T = struct.unpack('>f', raw[off:off+4])[0]; off += 4
        rid_len = struct.unpack('>H', raw[off:off+2])[0]; off += 2
        record_id = raw[off:off+rid_len].decode('utf-8'); off += rid_len
        data_size = struct.unpack('>Q', raw[off:off+8])[0]; off += 8
        if off + data_size > len(raw) - 4: raise ValueError("data_size poza zakresem")
        data = raw[off:off+data_size]
        obj = cls(record_id, data, phi_source, m_vector, m_vector_version=m_vec_ver, T=T)
        obj.field_id = field_id
        obj.dirty = False
        return obj


class ProcaCoordinate:
    __slots__ = ("record_id", "field_id", "phi_coords", "delta",
                 "T", "similarity", "_proca_v", "phi_dim")

    def __init__(self, record_id: str, field_id: str, phi_coords: np.ndarray,
                 delta: np.ndarray, T: float = 50.0, similarity: float = 1.0):
        self.record_id = record_id
        self.field_id = field_id
        self.phi_coords = np.asarray(phi_coords, dtype=np.float32)
        self.delta = np.asarray(delta, dtype=np.float32)
        self.T = float(T)
        self.similarity = float(similarity)
        self._proca_v = 2
        self.phi_dim = len(self.phi_coords)

    def to_json_bytes(self) -> bytes:
        d = {
            "_proca": 1,
            "_proca_v": self._proca_v,
            "phi_dim": self.phi_dim,
            "fid": self.field_id,
            "phi": self.phi_coords.tolist(),
            "delta": self.delta.tolist(),
            "T": round(self.T, 4),
            "sim": round(self.similarity, 6),
        }
        return json.dumps(d, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_json_bytes(cls, data: bytes, record_id: str = "") -> "ProcaCoordinate":
        d = json.loads(data.decode('utf-8'))
        phi = np.asarray(d['phi'], dtype=np.float32)
        delta = np.asarray(d['delta'], dtype=np.float32)
        return cls(record_id, d['fid'], phi, delta,
                   float(d.get('T', 50.0)), float(d.get('sim', 1.0)))

    @staticmethod
    def is_proca_json(data: bytes) -> bool:
        # Szukamy "_proca" jako klucza – bardziej odporne na formatowanie
        return b'"_proca"' in data[:20] or b'"_proca"' in data


class ProcaIndex:
    def __init__(self, fields_dir: str = "./karmazyn_data/fields",
                 threshold: float = DEFAULT_THRESHOLD, min_dedup_size: int = MIN_DEDUP_SIZE):
        self.fields_dir = fields_dir
        self.threshold = threshold
        self.min_dedup_size = min_dedup_size
        self._sources: Dict[str, ProcaFieldSource] = {}
        self._stats = {"sources": 0, "coordinates": 0, "bytes_saved": 0}
        os.makedirs(fields_dir, exist_ok=True)

    def register_or_deduplicate(self, record_id, data, phi, T=50.0):
        if len(data) < self.min_dedup_size or phi is None or len(phi) == 0:
            return ("raw", None)
        dim = len(phi)
        new_id = hashlib.sha256(data).hexdigest()
        existing = self._sources.get(new_id)
        if existing is not None:
            delta = existing.coordinate_delta(phi)
            coord = ProcaCoordinate(record_id, existing.field_id, phi, delta, T, 1.0)
            self._stats["coordinates"] += 1
            self._stats["bytes_saved"] += len(data) - len(coord.to_json_bytes())
            return ("coordinate", coord)
        m_vec = default_m_vector_for_dim(dim)
        source = ProcaFieldSource(record_id, data, phi, m_vec, T=T)
        self._sources[source.field_id] = source
        self._stats["sources"] += 1
        return ("source", source)

    def resolve_coordinate(self, coord):
        src = self._get_source(coord.field_id)
        return src.data if src else None

    def save_all_sources(self) -> int:
        saved = 0
        for src in self._sources.values():
            if not src.dirty: continue
            path = self._source_path(src.field_id)
            _atomic_write_pfld(path, src.serialize())
            src.dirty = False
            saved += 1
        return saved

    def load_sources_from_disk(self) -> int:
        loaded = 0
        if not os.path.isdir(self.fields_dir): return 0
        for fname in os.listdir(self.fields_dir):
            if not fname.endswith(PFLD_EXT): continue
            path = os.path.join(self.fields_dir, fname)
            try:
                with open(path, 'rb') as f: raw = f.read()
                src = ProcaFieldSource.deserialize(raw)
                self._sources[src.field_id] = src
                loaded += 1
            except Exception as e:
                import warnings
                warnings.warn(f"[Proca] Nie wczytano {fname}: {e}")
        return loaded

    def stats(self): return self._stats

    def _get_source(self, field_id):
        if field_id in self._sources: return self._sources[field_id]
        path = self._source_path(field_id)
        if os.path.exists(path):
            try:
                with open(path, 'rb') as f: raw = f.read()
                src = ProcaFieldSource.deserialize(raw)
                self._sources[field_id] = src
                return src
            except: pass
        return None

    def _source_path(self, field_id): return os.path.join(self.fields_dir, f"proca_{field_id}{PFLD_EXT}")


def analyze_field_coverage(atoms_with_phi, m_vector=None, threshold=DEFAULT_THRESHOLD):
    if m_vector is None: m_vector = DEFAULT_M_VECTOR
    n = len(atoms_with_phi)
    if n == 0: return {"atoms": 0, "fields": 0, "savings_pct": 0.0}
    assigned = [False] * n
    clusters = []
    for i, (_, data_i, phi_i) in enumerate(atoms_with_phi):
        if assigned[i] or len(data_i) < MIN_DEDUP_SIZE or phi_i is None: continue
        cluster = [i]
        assigned[i] = True
        dim_i = len(phi_i)
        mv_i = m_vector[:dim_i] if len(m_vector) >= dim_i else np.pad(m_vector, (0, dim_i - len(m_vector)), constant_values=0.5)
        for j, (_, data_j, phi_j) in enumerate(atoms_with_phi):
            if i == j or assigned[j] or phi_j is None: continue
            if len(phi_j) != dim_i: continue
            if yukawa_similarity(phi_i, phi_j, mv_i) >= threshold:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    total_raw = sum(len(d) for _, d, _ in atoms_with_phi)
    total_proca = 0
    # rozmiar koordynaty
    sample_size = 80
    try:
        for cl in clusters:
            if len(cl) > 1:
                phi0 = atoms_with_phi[cl[0]][2]
                if phi0 is not None:
                    sample_size = len(ProcaCoordinate("x","y",phi0, np.zeros_like(phi0)).to_json_bytes())
                    break
        else:
            sample_size = len(ProcaCoordinate("x","y", np.zeros(PHI_DIM, dtype=np.float32),
                                              np.zeros(PHI_DIM, dtype=np.float32)).to_json_bytes())
    except: pass

    for cl in clusters:
        sizes = [len(atoms_with_phi[i][1]) for i in cl]
        total_proca += max(sizes) + (len(cl)-1) * sample_size
    for i, (_, d, _) in enumerate(atoms_with_phi):
        if not assigned[i]: total_proca += len(d)
    savings = max(0, total_raw - total_proca)
    return {
        "atoms": n, "fields": len(clusters),
        "total_raw_b": total_raw, "total_proca_b": total_proca,
        "savings_b": savings, "savings_pct": round(savings / max(1, total_raw) * 100, 1),
        "avg_cluster_sz": round(sum(len(c) for c in clusters) / max(1, len(clusters)), 2)
    }


def _atomic_write_pfld(path, data):
    dir_path = os.path.dirname(path) or '.'
    os.makedirs(dir_path, exist_ok=True)
    tmp_fd = tmp_path = None
    try:
        # Zmiana z generycznego .tmp na domenowe .pfld_part w celu ochrony przed skasowaniem
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix='.pfld_', suffix='.pfld_part')
        with os.fdopen(tmp_fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        tmp_fd = None
        os.replace(tmp_path, path)
        tmp_path = None
    except:
        if tmp_fd is not None: os.close(tmp_fd)
        if tmp_path is not None: os.unlink(tmp_path)
        raise


if __name__ == "__main__":
    print("="*60)
    print(" Proca Field v1.4.1 – test")
    print("="*60)
    rng = np.random.default_rng(42)
    phi_doc = np.zeros(15, np.float32); phi_doc[8]=0.9; phi_doc[9]=0.8; phi_doc /= np.linalg.norm(phi_doc)
    phi_emo = np.zeros(15, np.float32); phi_emo[0]=0.8; phi_emo[7]=0.7; phi_emo /= np.linalg.norm(phi_emo)
    atoms = []
    for i in range(8):
        phi = phi_doc + rng.standard_normal(15).astype(np.float32)*0.05; phi /= np.linalg.norm(phi)
        atoms.append((f"doc_{i}", b"Dokumentacja "*80, phi))
    for i in range(7):
        phi = phi_emo + rng.standard_normal(15).astype(np.float32)*0.05; phi /= np.linalg.norm(phi)
        atoms.append((f"emo_{i}", b"Emocja "*100, phi))
    atoms.append(("no_phi", b"Bez phi "*50, None))
    for i in range(4):
        phi = rng.standard_normal(15).astype(np.float32); phi /= np.linalg.norm(phi)
        atoms.append((f"unique_{i}", bytes(rng.bytes(512)), phi))
    print("\n[1] Analiza:", analyze_field_coverage(atoms))
    print("\n[2] Yukawa similarity:")
    print(f"  same: {yukawa_similarity(phi_doc, phi_doc, DEFAULT_M_VECTOR):.4f}")
    print(f"  near: {yukawa_similarity(phi_doc, phi_doc+0.01, DEFAULT_M_VECTOR):.4f}")
    print(f"  far:  {yukawa_similarity(phi_doc, phi_emo, DEFAULT_M_VECTOR):.4f}")
    with tempfile.TemporaryDirectory() as td:
        idx = ProcaIndex(fields_dir=td)
        res = {"source":0,"coord":0,"raw":0}
        for aid,data,phi in atoms:
            typ,_ = idx.register_or_deduplicate(aid,data,phi)
            key = typ if typ!="coordinate" else "coord"
            res[key] = res.get(key,0)+1
        print(f"\n[3] ProcaIndex: {idx.stats()}")
        if idx._sources:
            src = next(iter(idx._sources.values()))
            raw = src.serialize()
            back = ProcaFieldSource.deserialize(raw)
            ok = back.field_id == src.field_id and np.allclose(back.phi_source, src.phi_source)
            print(f"  Roundtrip: {'OK' if ok else 'FAIL'} (m_vector v{back.m_vector_version})")
    print("="*60)