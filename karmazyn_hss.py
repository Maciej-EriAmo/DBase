"""
karmazyn_hss.py — HSS Daemon KarmazynOS v2.5 (profile Ring-LWE)
================================================================
Holographic Secure Storage — Ring-LWE KEM dla handshake TCP.

Profile zgodne z kierunkiem HSS Paper v2.5.0 (LPR, N potęga 2):
  proto      — N=15,  Q=256   (Termux / dev, kompatybilność wsteczna)
  standard   — N=128, Q=3329  (Kyber-class modulus, zespół / LAN)
  production — N=512, Q=12289 (wysoka siła, serwer zespołowy)

Spec: https://github.com/Maciej-EriAmo/holonOs/blob/main/HSS_Paper_v2.5.0_PL.md

Wybór profilu: zmienna KARM_HSS_PROFILE=proto|standard|production (obie strony muszą się zgadzać).
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

BITS_PER_ELEMENT = 2
HS_KEM_SHARED_SALT = b"KARMAZYN_HSS_KEM_SHARED_v3"


@dataclass(frozen=True)
class HSSProfile:
    name: str
    n: int
    q: int
    noise_base: int = 3
    kem_noise: int = 0
    sk_max: int = 8
    buckets: int = 8
    recon_tolerance: int = 4
    a_seed: bytes = b"KARMAZYN_HSS_KEM_A_v3"
    bits_per_element: int = BITS_PER_ELEMENT

    @property
    def pk_bytes(self) -> int:
        return self.n * 8

    @property
    def ack_bytes(self) -> int:
        return self.pk_bytes + 4


PROFILES: Dict[str, HSSProfile] = {
    "proto": HSSProfile(
        name="proto",
        n=15,
        q=256,
        sk_max=8,
        buckets=8,
        recon_tolerance=4,
        a_seed=b"KARMAZYN_HSS_KEM_A_v2_proto",
    ),
    "standard": HSSProfile(
        name="standard",
        n=128,
        q=3329,
        sk_max=3,
        buckets=16,
        recon_tolerance=8,
        a_seed=b"KARMAZYN_HSS_KEM_A_v3_n128",
    ),
    "production": HSSProfile(
        name="production",
        n=512,
        q=12289,
        sk_max=3,
        buckets=32,
        recon_tolerance=24,
        a_seed=b"KARMAZYN_HSS_KEM_A_v3_n512",
    ),
}

DEFAULT_PROFILE_NAME = "proto"


def resolve_hss_profile(name: Optional[str] = None) -> HSSProfile:
    raw = (name or os.environ.get("KARM_HSS_PROFILE") or DEFAULT_PROFILE_NAME).strip().lower()
    if raw not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"Nieznany profil HSS '{raw}'. Dostępne: {known}")
    return PROFILES[raw]


# Kompatybilność wsteczna modułu (domyślny profil z env)
_active = resolve_hss_profile()
N = _active.n
Q = _active.q
NOISE_BASE = _active.noise_base
HS_KEM_NOISE = _active.kem_noise
HS_KEM_SK_MAX = _active.sk_max
HS_KEM_A_SEED = _active.a_seed
HS_PK_BYTES = _active.pk_bytes
HS_KEM_BUCKETS = _active.buckets
HS_KEM_RECON_TOLERANCE = _active.recon_tolerance
HS_ACK_BYTES = _active.ack_bytes


@dataclass
class RLWEResult:
    projection_id: str
    u: np.ndarray
    v: int


@dataclass
class HistoryEntry:
    shape: Tuple[int, ...]
    bits_per_element: int
    results: List[RLWEResult]


def _kdf(seed_bytes: bytes, salt: str, profile: HSSProfile) -> np.ndarray:
    h = hashlib.sha256(seed_bytes + salt.encode()).digest()
    extended = (h * ((profile.n // len(h)) + 1))[: profile.n]
    return np.frombuffer(extended, dtype=np.uint8).astype(np.int64) % profile.q


def _make_seed(context: str) -> int:
    h = hashlib.sha256(context.encode()).digest()
    return int.from_bytes(h[:4], "little")


def _make_projection_id(prisms: List[str], task: str) -> str:
    raw = "|".join(sorted(prisms)) + "|" + task
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _encode_value(val: int, bits: int) -> List[int]:
    return [(val >> i) & 1 for i in range(bits)]


def _decode_bits(bits: List[int]) -> int:
    return sum(b << i for i, b in enumerate(bits))


def _kem_matrix_a(profile: HSSProfile) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(profile.a_seed).digest()[:4], "little")
    rng = np.random.default_rng(seed)
    M = rng.integers(0, profile.q, size=(profile.n, profile.n), dtype=np.int64)
    return ((M + M.T) // 2) % profile.q


def _kem_noise(rng: np.random.Generator, profile: HSSProfile) -> np.ndarray:
    return rng.integers(-profile.kem_noise, profile.kem_noise + 1, size=profile.n, dtype=np.int64)


def _kem_serialize_vec(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.int64).tobytes()


def _kem_deserialize_vec(data: bytes, profile: HSSProfile) -> np.ndarray:
    if len(data) != profile.pk_bytes:
        raise ValueError(f"HSS KEM pubkey/ack: oczekiwano {profile.pk_bytes} B, jest {len(data)}")
    return np.frombuffer(data, dtype=np.int64).copy() % profile.q


def _kem_bucket(dot_val: int, profile: HSSProfile) -> int:
    return int(dot_val % profile.q) // max(1, profile.q // profile.buckets)


def _kem_shared_from_bucket(bucket: int) -> bytes:
    return hashlib.sha256(bucket.to_bytes(4, "big") + HS_KEM_SHARED_SALT).digest()


def decrypt_multibit(
    s_agent: np.ndarray,
    results: List[RLWEResult],
    bits_per_element: int,
    *,
    profile: Optional[HSSProfile] = None,
) -> np.ndarray:
    prof = profile or resolve_hss_profile()
    it = iter(results)
    values = []
    num_elements = len(results) // bits_per_element
    for _ in range(num_elements):
        elem_bits = []
        for _ in range(bits_per_element):
            r = next(it)
            dot = np.dot(s_agent, r.u) % prof.q
            diff = (r.v - dot) % prof.q
            if prof.q // 4 < diff < 3 * prof.q // 4:
                elem_bits.append(1)
            else:
                elem_bits.append(0)
        values.append(_decode_bits(elem_bits))
    return np.array(values, dtype=np.int64)


class HSSDaemon:
    def __init__(
        self,
        bits_per_element: int = BITS_PER_ELEMENT,
        profile: Optional[HSSProfile] = None,
    ):
        self._profile = profile or resolve_hss_profile()
        self.bits_per_element = bits_per_element
        self._phi_sessions: Dict = {}
        self._agents: Dict = {}
        self._inodes: Dict = {}
        self._inode_prisms: Dict = {}
        self._terminated_agents = set()
        self._hs_init: Dict[str, np.ndarray] = {}

    @property
    def profile(self) -> HSSProfile:
        return self._profile

    def init_phi_session(self, phi2_vec: np.ndarray, phi_pid: int) -> np.ndarray:
        seed = phi2_vec.tobytes()
        s_sess = _kdf(seed, f"phi_sess_{phi_pid}", self._profile)
        self._phi_sessions[phi_pid] = s_sess
        return s_sess

    def derive_agent_key(self, agent_uuid: str, task: str, prisms: List[str] = None) -> np.ndarray:
        if prisms is None:
            prisms = ["core"]
        master = b"HSS_MASTER_SEED_v1"
        seed = f"{agent_uuid}|{task}|{','.join(sorted(prisms))}"
        s_agent = _kdf(master, seed, self._profile)
        self._agents[agent_uuid] = (s_agent, prisms, task)
        return s_agent

    def phi_write(self, inode: str, vec: np.ndarray, required_prisms: List[str] = None):
        if inode not in self._inodes:
            self._inodes[inode] = []
            self._inode_prisms[inode] = set(required_prisms) if required_prisms else {"core"}
        mask = (1 << self.bits_per_element) - 1
        clamped = vec.copy() & mask
        self._inodes[inode].append(clamped)

    def upcall_read(self, agent_uuid: str, inode: str, prisms: List[str], task: str) -> Optional[List[HistoryEntry]]:
        if agent_uuid not in self._agents:
            return None
        s_agent, agent_prisms, agent_task = self._agents[agent_uuid]
        if not any(p in agent_prisms for p in prisms):
            return None
        required = self._inode_prisms.get(inode, {"core"})
        if not required.intersection(set(agent_prisms)):
            return None
        history = self._inodes.get(inode, [])
        if not history:
            return []
        s_effective = self._effective_agent_key(s_agent, agent_task, task)
        proj_id = _make_projection_id(prisms, task)
        entries = []
        p = self._profile
        for i, stored_vec in enumerate(history):
            shape = stored_vec.shape
            seed_int = _make_seed(f"read:{inode}:{i}")
            rng = np.random.default_rng(seed_int)
            results = []
            for idx, val in enumerate(stored_vec.flat):
                val_bits = _encode_value(int(val), self.bits_per_element)
                for bit_idx, bit in enumerate(val_bits):
                    u = rng.integers(0, p.q, size=p.n, dtype=np.int64)
                    dot = np.dot(s_effective, u) % p.q
                    noise_rng = np.random.default_rng(_make_seed(f"noise:{inode}:{i}:{idx}:{bit_idx}"))
                    raw_noise = noise_rng.integers(-p.noise_base, p.noise_base + 1, dtype=np.int64)
                    noise = int(raw_noise)
                    v = (dot + bit * (p.q // 2) + noise) % p.q
                    results.append(RLWEResult(projection_id=proj_id, u=u, v=int(v)))
            entries.append(HistoryEntry(shape=shape, bits_per_element=self.bits_per_element, results=results))
        return entries

    def _effective_agent_key(self, s_agent: np.ndarray, agent_task: str, req_task: str) -> np.ndarray:
        if agent_task == req_task:
            return s_agent
        mixed = _kdf(s_agent.tobytes(), f"task_mismatch:{req_task}:{agent_task}", self._profile)
        return (s_agent + mixed) % self._profile.q

    def terminate_agent(self, agent_uuid: str):
        if agent_uuid in self._agents:
            del self._agents[agent_uuid]
            self._terminated_agents.add(agent_uuid)

    def vacuum_decay(self):
        pass

    def init_session(self) -> Tuple[str, bytes]:
        token = secrets.token_hex(16)
        rng = np.random.default_rng(int.from_bytes(secrets.token_bytes(8), "big"))
        p = self._profile
        sk_a = rng.integers(0, p.sk_max, size=p.n, dtype=np.int64)
        A = _kem_matrix_a(p)
        pk_a = (A @ sk_a + _kem_noise(rng, p)) % p.q
        self._hs_init[token] = sk_a
        return token, _kem_serialize_vec(pk_a)

    def respond_handshake(self, pubkey_a: bytes) -> Tuple[str, bytes, bytes]:
        p = self._profile
        pk_a = _kem_deserialize_vec(pubkey_a, p)
        token = secrets.token_hex(16)
        rng = np.random.default_rng(int.from_bytes(secrets.token_bytes(8), "big"))
        sk_b = rng.integers(0, p.sk_max, size=p.n, dtype=np.int64)
        A = _kem_matrix_a(p)
        pk_b = (A @ sk_b + _kem_noise(rng, p)) % p.q
        dot_b = int(np.dot(sk_b, pk_a) % p.q)
        bucket = _kem_bucket(dot_b, p)
        shared = _kem_shared_from_bucket(bucket)
        ack = _kem_serialize_vec(pk_b) + bucket.to_bytes(4, "big")
        return token, shared, ack

    def finalize(self, token: str, ack: bytes) -> bytes:
        p = self._profile
        sk_a = self._hs_init.pop(token, None)
        if sk_a is None:
            raise ValueError(f"Nieznany token handshake HSS: {token}")
        if len(ack) != p.ack_bytes:
            raise ValueError(f"HSS ack: oczekiwano {p.ack_bytes} B, jest {len(ack)}")
        pk_b = _kem_deserialize_vec(ack[: p.pk_bytes], p)
        hint_bucket = int.from_bytes(ack[p.pk_bytes : p.ack_bytes], "big")
        dot_a = int(np.dot(sk_a, pk_b) % p.q)
        local_bucket = _kem_bucket(dot_a, p)
        if abs(local_bucket - hint_bucket) > p.recon_tolerance:
            raise ValueError(
                f"HSS KEM ({p.name}): rekonsyliacja nieudana "
                f"(hint={hint_bucket}, local={local_bucket})"
            )
        return _kem_shared_from_bucket(hint_bucket)