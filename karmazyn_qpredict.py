#!/usr/bin/env python3
"""
karmazyn_qpredict.py — predykcja stanów kwantowych (interferencja EriAmo)
=======================================================================
Ścisła matematyka z EriAmo ``EmotionalInterference`` / ``QuantumEmotionalState``,
przeniesiona do DBase bez zależności od ścieżki EriAmo.

Model (jak w emotional_interference.py)::

    α_i(t+Δt) = α_i(t) + Σ_{j≠i} J_{ji} · α_j(t) · Δt
    |Ψ⟩ ← |Ψ⟩ / ‖Ψ‖₂

Macierz J ∈ [-1,1]^{n×n}: pary konstruktywne (+0.7) / destruktywne (−0.7).

Predykcja::

    |Ψ̂_{t+1}⟩ = U_J^{steps}(|Ψ_t⟩)     # apply_interference × steps
    F = |⟨Ψ̂|Ψ_obs⟩|²                  # fidelity (Born)
    ε = 1 − F                         # residual do bramy

Skuteczność referencyjna EriAmo ~92% na trajektoriach interferencyjnych
(nie na XOR digestów — tamten soft w KPC był złym modelem).

Metody poprawy F (kolejność wdrożenia)::
  M1  Globalna faza U(1): gauge-fix przed F (faza ref → 0) — QRM-like
  M2  Unitaryzacja: H = i(A−A†)/2, U=exp(−i H Δt) — zachowanie normy
  M3  Mniejszy Δt + multi-step (mniej błędu dyskretyzacji)
  M4  Uczenie J z historii (jak VectorCortex, ale na couplingach)
  M5  Fidelity uśredniona po oknie (robust na 1-krokowy szum)
  M6  Nie mieszać thermal/digest noise w przestrzeń amplitud

Domyślnie M1+M3 włączone; M2 opcjonalnie (mode="unitary").
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

# Te same osie co EriAmo QuantumEmotionalState.DIMENSIONS
DIMENSIONS: tuple[str, ...] = (
    "joy", "trust", "fear", "surprise",
    "sadness", "disgust", "anger", "anticipation",
    "logic", "knowledge", "time", "creation",
    "being", "space", "chaos",
    "vacuum",
)
N_DIM = len(DIMENSIONS)
_DIM_INDEX = {d: i for i, d in enumerate(DIMENSIONS)}

# Pary z emotional_interference.py (symetryczne ±0.7)
_POSITIVE = [
    ("joy", "trust"), ("joy", "anticipation"),
    ("trust", "being"), ("logic", "knowledge"),
    ("creation", "anticipation"), ("space", "time"),
]
_NEGATIVE = [
    ("joy", "sadness"), ("joy", "fear"),
    ("trust", "disgust"), ("logic", "chaos"),
    ("being", "chaos"), ("fear", "trust"),
]


def build_interference_matrix(coupling: float = 0.7) -> np.ndarray:
    """J[j, i] = wpływ j → i (wiersz źródło, kolumna cel) — jak matrix[other][emotion]."""
    J = np.zeros((N_DIM, N_DIM), dtype=np.float64)
    for a, b in _POSITIVE:
        ia, ib = _DIM_INDEX[a], _DIM_INDEX[b]
        J[ia, ib] = coupling
        J[ib, ia] = coupling
    for a, b in _NEGATIVE:
        ia, ib = _DIM_INDEX[a], _DIM_INDEX[b]
        J[ia, ib] = -coupling
        J[ib, ia] = -coupling
    return J


# Cache domyślnej macierzy
_J_DEFAULT = build_interference_matrix()


def normalize_state(psi: np.ndarray) -> np.ndarray:
    nrm = float(np.linalg.norm(psi))
    if nrm < 1e-12:
        out = np.zeros_like(psi)
        # równa superpozycja bez vacuum (jak EriAmo init)
        m = N_DIM - 1
        out[:m] = 1.0 / math.sqrt(m)
        return out
    return psi / nrm


def gauge_fix(psi: np.ndarray, ref_index: int | None = None) -> np.ndarray:
    """
    M1: usuń globalną fazę U(1) — faza ref_dim → 0.
    Fidelity jest U(1)-niezmiennicza, ale gauge stabilizuje porównania
    i ewolucję multi-step gdy fazy dryfują numerycznie.
    """
    psi = np.asarray(psi, dtype=np.complex128).reshape(-1)
    if ref_index is None:
        # najsilniejsza oś strukturalna (nie vacuum) — jak QRM ref_dim
        mags = np.abs(psi.copy())
        mags[N_DIM - 1] = -1.0  # vacuum nie jest ref
        ref_index = int(np.argmax(mags[: N_DIM - 1]))
    phase = np.angle(psi[ref_index])
    if abs(psi[ref_index]) < 1e-15:
        return psi.copy()
    return psi * np.exp(-1j * phase)


def fidelity(psi: np.ndarray, phi: np.ndarray, *, gauge: bool = True) -> float:
    """F = |⟨ψ|φ⟩|² ∈ [0,1] dla stanów czystych (po normalizacji)."""
    a = normalize_state(np.asarray(psi, dtype=np.complex128).reshape(-1))
    b = normalize_state(np.asarray(phi, dtype=np.complex128).reshape(-1))
    if gauge:
        a = gauge_fix(a)
        b = gauge_fix(b)
    ov = np.vdot(a, b)  # ⟨a|b⟩
    return float(np.clip(np.abs(ov) ** 2, 0.0, 1.0))


def residual(psi_hat: np.ndarray, psi_obs: np.ndarray, *, gauge: bool = True) -> float:
    """ε = 1 − F."""
    return 1.0 - fidelity(psi_hat, psi_obs, gauge=gauge)


# ── Ewolucja ──────────────────────────────────────────────────────────────────

def evolve_interference(
    psi: np.ndarray,
    *,
    dt: float = 0.1,
    steps: int = 1,
    J: np.ndarray | None = None,
    mode: str = "eriamo",
) -> np.ndarray:
    """
    Ewolucja interferencyjna.

    mode="eriamo"  — dokładny wzór EriAmo (liniowy + normalize co krok)
    mode="unitary" — M2: unitaryzacja generatora (zachowuje ‖ψ‖ bez ad-hoc norm
                     w sensie unitarnym; potem i tak gauge)
    """
    psi = normalize_state(np.asarray(psi, dtype=np.complex128).reshape(-1))
    if psi.shape[0] != N_DIM:
        raise ValueError(f"oczekiwano dim={N_DIM}, dostano {psi.shape[0]}")
    J = _J_DEFAULT if J is None else J
    steps = max(1, int(steps))
    dt = float(dt)

    if mode == "unitary":
        # A_ij (wpływ na i od j): w EriAmo influence na emotion i z other j:
        #   influence_i += J[j,i] * α_j * dt
        # więc dα/dt = M α  z M[i,j] = J[j,i] (j≠i), M[i,i]=0
        M = J.T.copy()  # M[i,j] = J[j,i]
        np.fill_diagonal(M, 0.0)
        # anti-Hermitian generator: G = (M - M†)/2 → unitary exp(G dt) przy G†=-G
        # używamy H = i(M - M†)/2 hermitowski, U = exp(-i H dt)
        Mh = (M - M.conj().T) / 2.0
        H = 1j * Mh  # hermitowski jeśli M rzeczywiste: H = i(M-M.T)/2
        # dla M rzeczywistego: H = i(M-M.T)/2 jest antyhermitowski? 
        # (M-M.T) skośne → i*(skośne) hermitowskie ✓
        for _ in range(steps):
            # expm małe: Padé 1. rzędu / skalowanie — niska dim, full eigOK
            w, V = np.linalg.eigh(H * dt)
            U = (V * np.exp(-1j * w)) @ V.conj().T
            psi = U @ psi
            psi = normalize_state(psi)
        return psi

    # mode eriamo (domyślny) — 1:1 z apply_interference
    for _ in range(steps):
        new = psi.copy()
        for i in range(N_DIM):
            influence = 0j
            for j in range(N_DIM):
                if j == i:
                    continue
                # coupling = interference_matrix[other][emotion] = J[j, i]
                influence += J[j, i] * psi[j] * dt
            new[i] = psi[i] + influence
        psi = normalize_state(new)
    return psi


def predict_state(
    psi: np.ndarray,
    *,
    steps: int = 1,
    dt: float = 0.1,
    mode: str = "eriamo",
    J: np.ndarray | None = None,
) -> np.ndarray:
    """Predykcja |Ψ̂⟩ = ewolucja interferencyjna z |Ψ⟩."""
    return evolve_interference(psi, dt=dt, steps=steps, J=J, mode=mode)


# ── Embedding bajtów → stan (most KPC ↔ przestrzeń kwantowa) ──────────────────

def embed_bytes(data: bytes, *, salt: bytes = b"kpc-qembed-v1") -> np.ndarray:
    """
    Deterministyczne osadzenie materiału sesji/klucza w C^{n}.
    Magnituda z hash, faza z hash — potem normalizacja (vacuum start ≈ 0).
    """
    h = hashlib.sha256(salt + data).digest()
    h2 = hashlib.sha256(salt + b"phase" + data).digest()
    mags = np.zeros(N_DIM, dtype=np.float64)
    phases = np.zeros(N_DIM, dtype=np.float64)
    for i in range(N_DIM - 1):  # vacuum ≈ 0
        # 2 bajty na mag, 2 na fazę
        mags[i] = (h[i % len(h)] + 1) / 256.0
        phases[i] = 2.0 * math.pi * (h2[i % len(h2)] / 256.0)
    mags[N_DIM - 1] = 1e-6
    psi = mags * np.exp(1j * phases)
    return normalize_state(psi)


def state_commit(psi: np.ndarray) -> bytes:
    """Commit stanu (gauge-fixed) — do historii, nie plaintext amplitud w wire."""
    g = gauge_fix(normalize_state(psi))
    raw = g.real.tobytes() + g.imag.tobytes()
    return hashlib.sha256(b"kpc-qstate-v1" + raw).digest()


# ── Accuracy / skuteczność ────────────────────────────────────────────────────

@dataclass
class QPredictReport:
    n: int
    mean_fidelity_honest: float
    p05_fidelity_honest: float  # dolny ogon (gorsze przypadki)
    success_rate_at_theta: dict[float, float]  # % honest z F ≥ θ
    far_at_theta: dict[float, float]  # % external z F ≥ θ (fałszywy accept)
    mean_fidelity_external: float
    best_theta_for_92: float | None
    notes: list[str]

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean_fidelity_honest": self.mean_fidelity_honest,
            "p05_fidelity_honest": self.p05_fidelity_honest,
            "success_rate_at_theta": self.success_rate_at_theta,
            "far_at_theta": self.far_at_theta,
            "mean_fidelity_external": self.mean_fidelity_external,
            "best_theta_for_92": self.best_theta_for_92,
            "notes": self.notes,
        }


def measure_interference_accuracy(
    n: int = 200,
    *,
    dt: float = 0.1,
    steps: int = 1,
    mode: str = "eriamo",
    amp_noise: float = 0.0,
    phase_noise: float = 0.0,
    thetas: Sequence[float] = (0.80, 0.85, 0.90, 0.92, 0.95, 0.99),
    seed: int = 42,
    multi_step_predict: int | None = None,
) -> QPredictReport:
    """
    Honest: ψ_{t+1} = evolve(ψ_t) [+ opc. szum pomiarowy],
            ψ̂ = predict(ψ_t), F = fidelity(ψ̂, ψ_{t+1}).

    External: losowy stan vs predykcja.

    multi_step_predict: jeśli podane, predyktor używa innego steps niż ground truth
    (symulacja mismatch) — domyślnie ten sam steps.
    """
    rng = np.random.default_rng(seed)
    fidel_h: list[float] = []
    fidel_e: list[float] = []
    thetas = list(thetas)
    pred_steps = steps if multi_step_predict is None else multi_step_predict

    for i in range(n):
        # stan startowy: embed losowego seeda
        raw = rng.bytes(32)
        psi = embed_bytes(raw)
        # ground-truth ewolucja (rzeczywista dynamika)
        psi_next = evolve_interference(psi, dt=dt, steps=steps, mode=mode)
        if amp_noise > 0 or phase_noise > 0:
            noise = (
                rng.normal(0.0, amp_noise, size=N_DIM)
                + 1j * rng.normal(0.0, phase_noise, size=N_DIM)
            )
            psi_next = normalize_state(psi_next + noise)

        psi_hat = predict_state(psi, steps=pred_steps, dt=dt, mode=mode)
        fh = fidelity(psi_hat, psi_next)
        fidel_h.append(fh)

        # external: niezwiązany stan
        psi_evil = embed_bytes(rng.bytes(32))
        fe = fidelity(psi_hat, psi_evil)
        fidel_e.append(fe)

    fidel_h_arr = np.array(fidel_h)
    fidel_e_arr = np.array(fidel_e)
    mean_h = float(fidel_h_arr.mean())
    p05 = float(np.percentile(fidel_h_arr, 5))
    mean_e = float(fidel_e_arr.mean())

    success = {t: float(np.mean(fidel_h_arr >= t)) for t in thetas}
    far = {t: float(np.mean(fidel_e_arr >= t)) for t in thetas}

    best_92 = None
    for t in sorted(thetas):
        if success[t] >= 0.92 and far[t] <= 0.05:
            best_92 = t
            break
    # jeśli nie ma w liście — poszukaj progu empirycznego
    if best_92 is None:
        for t in np.linspace(0.5, 0.99, 50):
            s = float(np.mean(fidel_h_arr >= t))
            f = float(np.mean(fidel_e_arr >= t))
            if s >= 0.92 and f <= 0.05:
                best_92 = float(t)
                break

    notes: list[str] = []
    notes.append(f"mode={mode} dt={dt} steps={steps} amp_noise={amp_noise} phase_noise={phase_noise}")
    notes.append(f"mean F_honest={mean_h:.4f}  p05={p05:.4f}  mean F_external={mean_e:.4f}")
    if amp_noise == 0 and phase_noise == 0 and pred_steps == steps:
        notes.append(
            "Bez szumu i z tym samym operatorem: F_honest powinno być ~1.0 "
            "(predyktor = ewolucja). To baseline matematyki, nie 92%."
        )
    if mean_h >= 0.92:
        notes.append("mean F ≥ 0.92 — predyktor w reżimie deklarowanej skuteczności EriAmo.")
    else:
        notes.append(
            "mean F < 0.92 — włącz M1 gauge, mniejszy dt (M3), mode=unitary (M2) "
            "lub zmniejsz szum; nie wkładaj do KDF."
        )
    if best_92 is not None:
        notes.append(f"Próg F≥{best_92:.3f} daje success≥92% przy FAR≤5%.")
    else:
        notes.append("Brak progu z success≥92% i FAR≤5% przy tym szumie.")

    return QPredictReport(
        n=n,
        mean_fidelity_honest=mean_h,
        p05_fidelity_honest=p05,
        success_rate_at_theta=success,
        far_at_theta=far,
        mean_fidelity_external=mean_e,
        best_theta_for_92=best_92,
        notes=notes,
    )


def compare_improvement_methods(
    n: int = 150,
    amp_noise: float = 0.05,
    phase_noise: float = 0.05,
    seed: int = 1,
) -> dict[str, QPredictReport]:
    """
    Porównaj warianty poprawy pod tym samym szumem pomiarowym.
    Zwraca mapę nazwa → report.
    """
    variants = {
        "baseline_eriamo_dt0.1": dict(mode="eriamo", dt=0.1, steps=1),
        "M3_smaller_dt": dict(mode="eriamo", dt=0.02, steps=5),  # ten sam czas 0.1
        "M2_unitary_dt0.1": dict(mode="unitary", dt=0.1, steps=1),
        "M2+M3_unitary_fine": dict(mode="unitary", dt=0.02, steps=5),
    }
    out = {}
    for name, kw in variants.items():
        out[name] = measure_interference_accuracy(
            n=n, amp_noise=amp_noise, phase_noise=phase_noise, seed=seed, **kw
        )
    return out
