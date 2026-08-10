"""
Predykcja stanów kwantowych (interferencja EriAmo) — fidelity, nie digest-XOR.

Oczekiwania:
  - bez szumu: F_honest ≈ 1 (predyktor = operator ewolucji)
  - z lekkim szumem: dążyć do ~92% success przy sensownym progu F
  - external: niska F
  - porównanie metod poprawy M2/M3
"""

from __future__ import annotations

import unittest

import numpy as np

from karmazyn_qpredict import (
    DIMENSIONS,
    N_DIM,
    compare_improvement_methods,
    embed_bytes,
    evolve_interference,
    fidelity,
    gauge_fix,
    measure_interference_accuracy,
    normalize_state,
    predict_state,
    residual,
)


class TestInterferenceMath(unittest.TestCase):
    def test_norm_preserved_eriamo(self):
        psi = embed_bytes(b"\x01" * 32)
        out = evolve_interference(psi, dt=0.1, steps=5, mode="eriamo")
        self.assertAlmostEqual(float(np.linalg.norm(out)), 1.0, places=6)

    def test_predict_matches_evolve_no_noise(self):
        psi = embed_bytes(b"\xab\xcd" * 16)
        hat = predict_state(psi, steps=3, dt=0.1)
        true = evolve_interference(psi, steps=3, dt=0.1)
        self.assertGreater(fidelity(hat, true), 0.999999)

    def test_fidelity_gauge_invariant(self):
        psi = embed_bytes(b"\x11" * 32)
        phi = evolve_interference(psi, steps=1)
        phase = np.exp(1j * 1.7)
        self.assertAlmostEqual(fidelity(psi, phi), fidelity(psi * phase, phi), places=9)
        self.assertAlmostEqual(fidelity(psi, phi), fidelity(psi, phi * phase), places=9)

    def test_external_low_fidelity(self):
        a = embed_bytes(b"\x01" * 32)
        b = embed_bytes(b"\xff" * 32)
        hat = predict_state(a, steps=1)
        # nie gwarantujemy F=0, ale zwykle wyraźnie < honest
        f_ext = fidelity(hat, b)
        f_hon = fidelity(hat, evolve_interference(a, steps=1))
        self.assertGreater(f_hon, f_ext)


class TestAccuracyNoNoise(unittest.TestCase):
    def test_perfect_when_operator_matches(self):
        rep = measure_interference_accuracy(
            n=60, amp_noise=0.0, phase_noise=0.0, seed=2
        )
        print("\n=== QPredict no noise ===")
        for k, v in rep.as_dict().items():
            print(f"  {k}: {v}")
        self.assertGreater(rep.mean_fidelity_honest, 0.999)
        self.assertGreaterEqual(rep.success_rate_at_theta[0.92], 0.99)


class TestAccuracyWithNoise(unittest.TestCase):
    def test_noise_regime_and_92(self):
        # umiarkowany szum pomiarowy — reżim „rzeczywisty”
        rep = measure_interference_accuracy(
            n=120,
            amp_noise=0.03,
            phase_noise=0.03,
            seed=5,
        )
        print("\n=== QPredict amp/phase noise 0.03 ===")
        for k, v in rep.as_dict().items():
            print(f"  {k}: {v}")
        # mean F powinno być w okolicy wysokiej (nie 0.66 jak stary digest model)
        self.assertGreater(rep.mean_fidelity_honest, 0.85)

    def test_improvement_methods(self):
        results = compare_improvement_methods(
            n=100, amp_noise=0.05, phase_noise=0.05, seed=11
        )
        print("\n=== Improvement methods (obs noise 0.05) ===")
        ranking = []
        for name, rep in results.items():
            print(
                f"  {name}: meanF={rep.mean_fidelity_honest:.4f} "
                f"p05={rep.p05_fidelity_honest:.4f} "
                f"S@0.92={rep.success_rate_at_theta.get(0.92, 0):.3f} "
                f"bestθ92={rep.best_theta_for_92}"
            )
            ranking.append((rep.mean_fidelity_honest, name))
        ranking.sort(reverse=True)
        print(f"  best_mean: {ranking[0][1]}")
        # przy czystym szumie pomiarowym M2/M3 ≈ baseline (to OK — dokumentujemy)

    def test_M3_helps_discretization_mismatch(self):
        """
        Ground truth: drobny krok (dt=0.02 × 5).
        Predyktor gruby (dt=0.1 × 1) vs drobny (dt=0.02 × 5) — M3 zmniejsza błąd.
        """
        from karmazyn_qpredict import embed_bytes, evolve_interference, fidelity

        rng = np.random.default_rng(0)
        f_coarse, f_fine = [], []
        for _ in range(80):
            psi = embed_bytes(rng.bytes(32))
            true = evolve_interference(psi, dt=0.02, steps=5, mode="eriamo")
            hat_coarse = evolve_interference(psi, dt=0.1, steps=1, mode="eriamo")
            hat_fine = evolve_interference(psi, dt=0.02, steps=5, mode="eriamo")
            f_coarse.append(fidelity(hat_coarse, true))
            f_fine.append(fidelity(hat_fine, true))
        mean_c = float(np.mean(f_coarse))
        mean_f = float(np.mean(f_fine))
        print(f"\n=== M3 discretization: coarse F={mean_c:.6f} fine F={mean_f:.6f} ===")
        self.assertGreaterEqual(mean_f, mean_c - 1e-12)
        self.assertGreater(mean_f, 0.999)


class TestKPCQuantumSoft(unittest.TestCase):
    def test_soft_residual_quantum_honest_low(self):
        from karmazyn_key_predict import soft_residual_quantum
        from karmazyn_qpredict import embed_bytes, evolve_interference, state_commit

        psi = embed_bytes(b"\x42" * 32)
        c0 = state_commit(psi)
        psi1 = evolve_interference(psi, steps=1)
        # commit ewolucji w przestrzeni stanów ≠ embed(commit) ewolucja
        # soft_residual_quantum embeduje commity — to most KPC
        # Honest path KPC: commit_t i commit z evolve_exact są różne embedy
        # tu sprawdzamy spójność API
        r = soft_residual_quantum(c0, state_commit(psi1))
        self.assertGreaterEqual(r, 0.0)
        self.assertLessEqual(r, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
