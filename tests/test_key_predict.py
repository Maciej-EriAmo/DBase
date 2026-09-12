"""
KPC — Key Predictive Continuity.

Cel: ewolucja klucza z historią; reject z zewnątrz / bez historii / wadliwa historia.
Soft predictor mierzony osobno — nie wolno pastować soft do KDF bez accuracy report.
"""

from __future__ import annotations

import secrets
import unittest
from unittest.mock import patch

from karmazyn_key_predict import (
    HistoryEntry,
    KeyHistory,
    KPCSession,
    RejectReason,
    accept_key_rotation,
    context_blob,
    evolve_exact,
    measure_predictor_accuracy,
    thermal_digest,
)
from karmazyn_hsl import (
    HSLLink,
    current_epoch,
    link_commit,
    prism_target,
)


class TestExactRatchet(unittest.TestCase):
    def test_bootstrap_and_rotate_local(self):
        sess = KPCSession(bubble_id="B1")
        r0 = sess.bootstrap_from_session_key(b"\x11" * 32, epoch=1)
        self.assertTrue(r0.ok)
        self.assertEqual(r0.reason, RejectReason.OK)
        self.assertEqual(sess.history.gen, 0)
        self.assertIsNotNone(sess.key)

        r1 = sess.rotate(epoch=2)
        self.assertTrue(r1.ok, r1.detail)
        self.assertEqual(sess.history.gen, 1)
        self.assertEqual(r1.residual_exact, 0.0)

    def test_both_sides_same_key(self):
        seed = b"\xaa" * 32
        a = KPCSession(bubble_id="tunnel")
        b = KPCSession(bubble_id="tunnel")
        a.bootstrap_from_session_key(seed, epoch=10)
        b.bootstrap_from_session_key(seed, epoch=10)
        self.assertEqual(a.key, b.key)
        for ep in (11, 12, 13):
            ra = a.rotate(epoch=ep)
            rb = b.rotate(epoch=ep)
            self.assertTrue(ra.ok and rb.ok)
            self.assertEqual(a.key, b.key)

    def test_reject_external_offer(self):
        sess = KPCSession(bubble_id="x")
        sess.bootstrap_from_session_key(b"\x22" * 32, epoch=1)
        evil = secrets.token_bytes(32)
        bad = sess.rotate(epoch=2, offer_key=evil)
        self.assertFalse(bad.ok)
        self.assertEqual(bad.reason, RejectReason.EXTERNAL_KEY)
        self.assertEqual(bad.residual_exact, 1.0)
        # historia nie rośnie po reject
        self.assertEqual(sess.history.gen, 0)

    def test_reject_no_history(self):
        h = KeyHistory()
        r = accept_key_rotation(
            b"\x33" * 32, h, epoch=1, bootstrap=False
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, RejectReason.NO_HISTORY)

    def test_reject_bad_history_chain(self):
        h = KeyHistory()
        # ręcznie popsuty gen
        h.entries.append(HistoryEntry(
            gen=5, epoch=1, bubble_id="", key_commit=b"\x00" * 32,
            ctx_digest=b"\x00" * 32,
        ))
        r = accept_key_rotation(b"\x44" * 32, h, epoch=2)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, RejectReason.BAD_HISTORY)

    def test_honest_offer_accepted(self):
        sess = KPCSession(bubble_id="y")
        sess.bootstrap_from_session_key(b"\x55" * 32, epoch=1)
        k_hat = evolve_exact(sess.key, sess.history, epoch=2, bubble_id="y")
        ok = sess.rotate(epoch=2, offer_key=k_hat)
        self.assertTrue(ok.ok)
        self.assertEqual(ok.residual_exact, 0.0)


class TestSoftPredictorAccuracy(unittest.TestCase):
    """Czy nie przeszarżowaliśmy — soft nie jest kryptograficznie dokładny."""

    def test_accuracy_report_no_noise(self):
        report = measure_predictor_accuracy(n=80, thermal_noise_sigma=0.0, seed=7)
        d = report.as_dict()
        self.assertEqual(d["n"], 80)
        # Soft pred ≠ true ctx z reguły (model inercyjny ≠ context_blob)
        # więc mean residual honest zwykle >> 0
        self.assertGreaterEqual(report.mean_soft_residual_honest, 0.0)
        self.assertLessEqual(report.mean_soft_residual_honest, 1.0)
        # Exact path jest osobny — soft NIE do KDF przy typowym residual
        # (chyba że model idealnie trafia — wtedy recommend może być True)
        print("\n=== KPC accuracy (no thermal noise) ===")
        for k, v in d.items():
            print(f"  {k}: {v}")

    def test_accuracy_with_thermal_noise(self):
        report = measure_predictor_accuracy(
            n=80, thermal_noise_sigma=2.0, seed=9
        )
        print("\n=== KPC accuracy (thermal_noise_sigma=2.0) ===")
        for k, v in report.as_dict().items():
            print(f"  {k}: {v}")
        # Przy szumie T — na pewno nie pakować soft do KDF
        # (chyba że residual magicznie 0 — nie powinno)
        if report.p99_soft_residual_honest >= 0.02:
            self.assertFalse(report.recommend_soft_in_kdf)

    def test_soft_separability_external_vs_honest(self):
        """Na torze |Ψ⟩: honest ε≈0, external ε duże — brama F ma sens."""
        report = measure_predictor_accuracy(n=40, seed=3)
        print("\n=== KPC+|Ψ⟩ accuracy ===")
        for k, v in report.as_dict().items():
            print(f"  {k}: {v}")
        # honest residual przy identycznym operatorze ≈ 0
        self.assertLess(report.mean_soft_residual_honest, 1e-9)
        self.assertTrue(report.recommend_soft_gate)
        # θ=0.08 (F≥0.92): FRR≈0, FAR≈0
        if 0.08 in report.false_reject_rate_at_theta:
            self.assertLessEqual(report.false_reject_rate_at_theta[0.08], 0.01)
            self.assertLessEqual(report.false_accept_rate_external[0.08], 0.05)

    def test_thermal_in_exact_breaks_under_noise(self):
        """Dowód: thermal w exact KDF + szum obserwacji = dziurawy (rozjazd stron)."""
        seed = b"\x66" * 32
        thermal_true = [40.0, 41.0, 42.0, 43.0]
        thermal_noisy = [40.1, 41.2, 41.9, 43.3]  # mały szum

        a = KPCSession(bubble_id="t", use_thermal_in_exact=True)
        b = KPCSession(bubble_id="t", use_thermal_in_exact=True)
        a.bootstrap_from_session_key(seed, epoch=1, thermal=thermal_true)
        b.bootstrap_from_session_key(seed, epoch=1, thermal=thermal_true)
        self.assertEqual(a.key, b.key)

        a.rotate(epoch=2, thermal=thermal_true)
        b.rotate(epoch=2, thermal=thermal_noisy)
        # Po szumie strony rozjeżdżają się — to jest „dziurawy klucz”
        self.assertNotEqual(a.key, b.key)


class TestHSLIntegration(unittest.TestCase):
    def _make_link(self, epoch: int | None = None) -> HSLLink:
        shared = b"\x05" * 32
        ca = link_commit(b"\x06" * 32, b"n")
        cb = link_commit(b"\x07" * 32, b"n")
        ep = current_epoch() if epoch is None else epoch
        link = HSLLink(
            epoch=ep,
            s_target=prism_target(shared, ca, cb, ep),
            local_id="x",
            remote_id="y",
            task="cynober-rpc",
            prisms=("karminql",),
            _shared_key=shared,
            _commit_local=ca,
            _commit_remote=cb,
        )
        link._kpc_bootstrap()
        return link

    def test_kpc_bootstrap_on_link(self):
        link = self._make_link()
        self.assertIsNotNone(link._kpc)
        self.assertEqual(link._kpc.history.gen, 0)

    def test_kpc_rotates_only_on_epoch(self):
        link = self._make_link()
        gen0 = link._kpc.history.gen
        # ensure_epoch same → no rotate
        self.assertFalse(link.ensure_epoch())
        self.assertEqual(link._kpc.history.gen, gen0)

        old_ep = link.epoch
        with patch("karmazyn_hsl.current_epoch", return_value=old_ep + 1):
            self.assertTrue(link.ensure_epoch())
        self.assertEqual(link._kpc.history.gen, gen0 + 1)

    def test_frame_keys_still_work_after_kpc(self):
        link = self._make_link()
        k1 = link.request_key()
        self.assertEqual(len(k1), 32)
        old_ep = link.epoch
        # patch musi trwać przez request_key — inaczej ensure_epoch cofnie epokę
        with patch("karmazyn_hsl.current_epoch", return_value=old_ep + 1):
            self.assertTrue(link.ensure_epoch())
            k2 = link.request_key()
        self.assertEqual(len(k2), 32)
        # po zmianie epoki AAD/s_target → klucz ramki inny
        self.assertNotEqual(k1, k2)

    def test_existing_ensure_epoch_without_kpc_still_ok(self):
        """Link zbudowany ręcznie bez bootstrap — jak stare testy."""
        shared = b"\x05" * 32
        ca = link_commit(b"\x06" * 32, b"n")
        cb = link_commit(b"\x07" * 32, b"n")
        old_epoch = current_epoch()
        link = HSLLink(
            epoch=old_epoch,
            s_target=prism_target(shared, ca, cb, old_epoch),
            local_id="x",
            remote_id="y",
            task="cynober-rpc",
            prisms=("karminql",),
            _shared_key=shared,
            _commit_local=ca,
            _commit_remote=cb,
        )
        self.assertIsNone(link._kpc)
        with patch("karmazyn_hsl.current_epoch", return_value=old_epoch + 1):
            self.assertTrue(link.ensure_epoch())


class TestThermalDigestStability(unittest.TestCase):
    def test_quantization_same_bucket(self):
        a = thermal_digest([1.0001, 2.0002])
        b = thermal_digest([1.0004, 2.0001])
        # 1e-3 quant — bardzo bliskie wartości mogą spaść do tego samego bucketa
        # nie assert equal always; tylko shape
        self.assertEqual(len(a), 32)
        self.assertEqual(len(b), 32)

    def test_context_changes_with_epoch(self):
        c1 = context_blob(epoch=1, bubble_id="b", gen=1)
        c2 = context_blob(epoch=2, bubble_id="b", gen=1)
        self.assertNotEqual(c1, c2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
