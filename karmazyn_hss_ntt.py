"""
karmazyn_hss_ntt.py — negacyclic NTT dla Ring-LWE HSS (v7.5)
=============================================================
O(n log n) mnożenie w Z_q[X]/(X^n+1) dla profili standard/production (n potęga 2).
"""

from __future__ import annotations

import hashlib
from typing import Tuple

import numpy as np

from karmazyn_hss import HSSProfile


def _mod_pow(base: int, exp: int, mod: int) -> int:
    return pow(base % mod, exp, mod)


def _mod_inv(a: int, q: int) -> int:
    return _mod_pow(a, q - 2, q)


def _primitive_root(q: int, n: int) -> int:
    """Pierwiastek pierwotny rzędu 2n (wymaga q ≡ 1 mod 2n)."""
    if q % (2 * n) != 1:
        raise ValueError(f"NTT: q={q} nie spełnia q ≡ 1 (mod 2n={2*n})")
    for g in range(2, q):
        if _mod_pow(g, 2 * n, q) != 1:
            continue
        if _mod_pow(g, n, q) == q - 1 and _mod_pow(g, n // 2, q) != q - 1:
            return g
    raise ValueError(f"NTT: brak pierwiastka dla q={q}, n={n}")


def _bit_reverse(x: int, bits: int) -> int:
    r = 0
    for _ in range(bits):
        r = (r << 1) | (x & 1)
        x >>= 1
    return r


def ntt_negacyclic(a: np.ndarray, q: int, root: int, n: int, *, inverse: bool = False) -> np.ndarray:
    """NTT negacyclic (Cooley-Tukey), wektor długości n (n potęga 2)."""
    bits = n.bit_length() - 1
    if 1 << bits != n:
        raise ValueError(f"NTT: n={n} musi być potęgą 2")

    a = np.asarray(a, dtype=np.int64) % q
    out = np.zeros(n, dtype=np.int64)
    for i in range(n):
        out[_bit_reverse(i, bits)] = a[i]

    length = 2
    while length <= n:
        half = length // 2
        step = _mod_pow(root, n // length, q)
        if inverse:
            step = _mod_inv(step, q)
        for start in range(0, n, length):
            w = 1
            for j in range(half):
                u = out[start + j]
                v = (out[start + j + half] * w) % q
                out[start + j] = (u + v) % q
                out[start + j + half] = (u - v) % q
                w = (w * step) % q
        length *= 2

    if inverse:
        inv_n = _mod_inv(n, q)
        out = (out * inv_n) % q
    return out


def ntt_poly_mul(a: np.ndarray, b: np.ndarray, profile: HSSProfile) -> np.ndarray:
    """Mnożenie negacyclic mod (X^n+1) w Z_q."""
    n, q = profile.n, profile.q
    root = _primitive_root(q, n)
    fa = ntt_negacyclic(a, q, root, n, inverse=False)
    fb = ntt_negacyclic(b, q, root, n, inverse=False)
    fc = (fa * fb) % q
    return ntt_negacyclic(fc, q, root, n, inverse=True)


def kem_poly_a(profile: HSSProfile) -> np.ndarray:
    """Wielomian a z seeda profilu (pierwszy wiersz macierzy circulant)."""
    h = hashlib.sha256(profile.a_seed + b"_ntt_v1").digest()
    extended = (h * ((profile.n // len(h)) + 1))[: profile.n]
    return np.frombuffer(extended, dtype=np.uint8).astype(np.int64) % profile.q


def kem_pubkey_ntt(sk: np.ndarray, profile: HSSProfile) -> np.ndarray:
    """pk = a * sk (negacyclic) — odpowiednik A @ sk dla circulant."""
    a = kem_poly_a(profile)
    return ntt_poly_mul(a, sk % profile.q, profile) % profile.q


def kem_shared_ntt(sk: np.ndarray, pk: np.ndarray, profile: HSSProfile) -> int:
    """Wspólny sekret z iloczynu negacyclic (współczynnik stały)."""
    prod = ntt_poly_mul(sk % profile.q, pk % profile.q, profile)
    return int(prod[0] % profile.q)


def kem_dot_ntt(sk: np.ndarray, pk: np.ndarray, profile: HSSProfile) -> int:
    return kem_shared_ntt(sk, pk, profile)


def ntt_compatible(profile: HSSProfile) -> bool:
    """Czy profil obsługuje NTT (n potęga 2, q ≡ 1 mod 2n)."""
    n = profile.n
    if n & (n - 1) != 0:
        return False
    return profile.q % (2 * n) == 1