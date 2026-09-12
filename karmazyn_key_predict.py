#!/usr/bin/env python3
"""
karmazyn_key_predict.py — Key Predictive Continuity (KPC)
=========================================================
Kontrola ewolucji klucza w stylu EriAmo (predykcja + historia + próg),
dla Cynober/HSL — **tylko przy rotacji / bootstrap**, nie na każdej ramce.

Dwie warstwy (świadomie rozdzielone — unikamy „dziurawego klucza”):

1. **Exact ratchet** (materiał klucza)
   K_{t+1} = HKDF(K_t ‖ commit(H) ‖ context)
   Legitimate: residual = 0.  Zewnętrzny / zły krok: reject.
   To idzie do produkcji wire.

2. **Soft / quantum predictor** (diagnostyka + opcjonalna brama)
   **Właściwa matematyka EriAmo**: ewolucja interferencyjna na C^{16}
   (``karmazyn_qpredict``) + fidelity F=|⟨ψ̂|ψ⟩|², residual ε=1−F.
   Stary model XOR-digest był błędny (nie ten operator) — stąd ~0.33 residual.
   Soft **nie wchodzi do KDF**, dopóki F nie jest stabilnie ≥~0.92 (domyślnie off).

Reguły reject:
  - brak historii przy nie-bootstrap → REJECT
  - nie da się zbudować / zweryfikować łańcucha H → REJECT
  - offer K' ≠ exact evolve(K_t, H, c) → REJECT (zewnętrzny / fork)
  - soft_gate=True i ε_soft > θ → REJECT (wadliwa trajektoria)

Env:
  KARM_KPC_SOFT_GATE=1     — włącz miękką bramę (domyślnie wyłączona)
  KARM_KPC_SOFT_THETA=0.08 — próg residual soft (ε=1−F; 0.08 ⇔ F≥0.92)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence


# ── HKDF (spójny styl z karmazyn_hsl) ─────────────────────────────────────────

def _hkdf(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    prk = hmac.new(b"karmazyn-kpc-v1", ikm, hashlib.sha256).digest()
    out = bytearray()
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def key_commit(key: bytes, context: bytes = b"") -> bytes:
    """Commit klucza — do historii (nigdy surowy K w H)."""
    return hashlib.sha256(b"kpc-commit-v1" + key + context).digest()


def thermal_digest(thermal_window: Sequence[float] | bytes | None) -> bytes:
    """
    Digest okna termicznego (opcja B).
    floaty kwantyzowane — zmniejsza szum, ale NIE usuwa go całkowicie.
    """
    if thermal_window is None:
        return b"\x00" * 32
    if isinstance(thermal_window, (bytes, bytearray)):
        return hashlib.sha256(b"kpc-thermal-raw-v1" + bytes(thermal_window)).digest()
    # kwantyzacja 1e-3 — kompromis stabilność vs precyzja (mierzona w testach)
    packed = bytearray()
    for x in thermal_window:
        q = int(round(float(x) * 1000.0))
        packed.extend(struct.pack(">i", max(-2_000_000_000, min(2_000_000_000, q))))
    return hashlib.sha256(b"kpc-thermal-q1e3-v1" + bytes(packed)).digest()


def context_blob(
    *,
    epoch: int = 0,
    bubble_id: str = "",
    gen: int = 0,
    thermal: Sequence[float] | bytes | None = None,
    extra: bytes = b"",
) -> bytes:
    th = thermal_digest(thermal)
    parts = [
        b"kpc-ctx-v1",
        epoch.to_bytes(8, "big", signed=False),
        gen.to_bytes(8, "big", signed=False),
        bubble_id.encode("utf-8"),
        th,
        extra,
    ]
    return hashlib.sha256(b"\0".join(parts)).digest()


# ── Historia ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HistoryEntry:
    gen: int
    epoch: int
    bubble_id: str
    key_commit: bytes  # 32 B
    ctx_digest: bytes  # 32 B — fingerprint kontekstu (epoch/bubble/thermal…)
    ts: float = 0.0

    def encode(self) -> bytes:
        return b"|".join([
            str(self.gen).encode(),
            str(self.epoch).encode(),
            self.bubble_id.encode("utf-8"),
            self.key_commit.hex().encode(),
            self.ctx_digest.hex().encode(),
            f"{self.ts:.6f}".encode(),
        ])


@dataclass
class KeyHistory:
    """Łańcuch commitów ewolucji — bez plaintext kluczy."""
    entries: list[HistoryEntry] = field(default_factory=list)
    max_len: int = 64

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def gen(self) -> int:
        return self.entries[-1].gen if self.entries else -1

    def chain_commit(self) -> bytes:
        """Pojedynczy commit całego łańcucha (jak Merkle-lite)."""
        h = hashlib.sha256(b"kpc-chain-v1")
        for e in self.entries:
            h.update(e.encode())
        return h.digest()

    def append(self, entry: HistoryEntry) -> None:
        if self.entries:
            prev = self.entries[-1]
            if entry.gen != prev.gen + 1:
                raise ValueError(
                    f"wadliwa historia: gen {entry.gen} po {prev.gen} (oczekiwano +1)"
                )
        elif entry.gen != 0:
            raise ValueError(f"bootstrap wymaga gen=0, dostano {entry.gen}")
        self.entries.append(entry)
        if len(self.entries) > self.max_len:
            self.entries = self.entries[-self.max_len:]

    def verify_chain(self) -> bool:
        if not self.entries:
            return True
        if self.entries[0].gen != 0:
            return False
        for i in range(1, len(self.entries)):
            if self.entries[i].gen != self.entries[i - 1].gen + 1:
                return False
            if len(self.entries[i].key_commit) != 32:
                return False
            if len(self.entries[i].ctx_digest) != 32:
                return False
        return True


# ── Exact evolve ──────────────────────────────────────────────────────────────

def evolve_exact(
    key_t: bytes,
    history: KeyHistory,
    *,
    epoch: int,
    bubble_id: str = "",
    thermal: Sequence[float] | bytes | None = None,
    extra: bytes = b"",
) -> bytes:
    """
    Deterministyczny krok ratcheta — obie strony liczą to samo przy tym samym ctx.
    thermal w KDF tylko gdy obie strony mają **identyczny** digest (ostrożnie!).
    """
    if not key_t or len(key_t) < 16:
        raise ValueError("key_t za krótki")
    next_gen = history.gen + 1
    ctx = context_blob(
        epoch=epoch,
        bubble_id=bubble_id,
        gen=next_gen,
        thermal=thermal,
        extra=extra,
    )
    chain = history.chain_commit()
    ikm = key_t + chain + ctx
    return _hkdf(ikm, b"kpc-evolve-exact-v1", 32)


# ── Soft predictor — właściwa interferencja kwantowa (EriAmo) ─────────────────

def predict_next_ctx_digest(history: KeyHistory) -> bytes:
    """
    Legacy alias: commit predykowanego stanu kwantowego z ostatniego key_commit.
    Używa ``karmazyn_qpredict`` (interferencja), nie XOR-digest.
    """
    from karmazyn_qpredict import embed_bytes, predict_state, state_commit

    if not history.entries:
        return hashlib.sha256(b"kpc-soft-no-history").digest()
    psi = embed_bytes(history.entries[-1].key_commit)
    psi_hat = predict_state(psi, steps=1, dt=0.1, mode="eriamo")
    return state_commit(psi_hat)


def soft_residual_quantum(
    key_commit_t: bytes,
    key_commit_next_or_offer: bytes,
    *,
    steps: int = 1,
    dt: float = 0.1,
    mode: str = "eriamo",
) -> float:
    """
    ε = 1 − F(|Ψ̂⟩, |Ψ_obs⟩) na trajektorii interferencyjnej.

    |Ψ_t⟩   = embed(commit_t)
    |Ψ̂⟩    = evolve_J(|Ψ_t⟩)
    |Ψ_obs⟩ = embed(commit_next)   # obserwacja / oferta
    """
    from karmazyn_qpredict import embed_bytes, predict_state, residual

    psi = embed_bytes(key_commit_t)
    psi_hat = predict_state(psi, steps=steps, dt=dt, mode=mode)
    psi_obs = embed_bytes(key_commit_next_or_offer)
    return residual(psi_hat, psi_obs)


def soft_residual(
    predicted_ctx: bytes,
    observed_ctx: bytes,
) -> float:
    """
    Residual ∈ [0, 1] przez fidelity w przestrzeni stanów.

    Interpretacja bajtów: embed → evolve nie jest tu powtarzane
    (predicted_ctx ma już być state_commit(ψ̂)); observed_ctx = state_commit lub
    surowy commit — embed obu i F bez dodatkowej ewolucji.
    """
    from karmazyn_qpredict import embed_bytes, residual

    return residual(embed_bytes(predicted_ctx), embed_bytes(observed_ctx))


# ── Werdykt bramy ──────────────────────────────────────────────────────────────

class RejectReason(str, Enum):
    OK = "ok"
    NO_HISTORY = "brak_historii"
    BAD_HISTORY = "wadliwa_historia"
    EXTERNAL_KEY = "klucz_z_zewnatrz"
    SOFT_FAIL = "wadliwa_trajektoria_soft"
    BOOTSTRAP_ONLY = "bootstrap_wymaga_gen0"


@dataclass
class AcceptResult:
    ok: bool
    reason: RejectReason
    residual_exact: float  # 0.0 lub 1.0 (bitowa równość)
    residual_soft: float   # [0,1]
    key_next: bytes | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason.value,
            "residual_exact": self.residual_exact,
            "residual_soft": self.residual_soft,
            "detail": self.detail,
        }


def soft_gate_enabled() -> bool:
    return os.environ.get("KARM_KPC_SOFT_GATE", "").strip() in ("1", "true", "yes")


def soft_theta() -> float:
    # domyślnie ε ≤ 0.08 ⇔ F ≥ 0.92 (deklarowana skuteczność interferencji EriAmo)
    raw = os.environ.get("KARM_KPC_SOFT_THETA", "0.08").strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.08


def accept_key_rotation(
    key_t: bytes | None,
    history: KeyHistory,
    *,
    epoch: int,
    bubble_id: str = "",
    thermal: Sequence[float] | bytes | None = None,
    extra: bytes = b"",
    offer_key: bytes | None = None,
    bootstrap: bool = False,
    soft_gate: bool | None = None,
    theta: float | None = None,
) -> AcceptResult:
    """
    Brama przy rotacji / bootstrap (NIE na każdej ramce RPC).

    bootstrap=True, pusta historia:
      — generuje K0 (lub bierze offer jako seed tylko gdy podany i bootstrap),
      — startuje H gen=0.
    bootstrap=False:
      — wymaga historii i key_t,
      — offer_key musi == evolve_exact(...) (anti external).
    """
    use_soft = soft_gate_enabled() if soft_gate is None else soft_gate
    th = soft_theta() if theta is None else theta

    if not history.verify_chain():
        return AcceptResult(
            False, RejectReason.BAD_HISTORY, 1.0, 1.0,
            detail="łańcuch gen/commit niespójny",
        )

    # ── Bootstrap ──────────────────────────────────────────────────────────
    if bootstrap:
        if history.entries:
            return AcceptResult(
                False, RejectReason.BOOTSTRAP_ONLY, 1.0, 1.0,
                detail="bootstrap przy niepustej historii",
            )
        if offer_key is not None:
            # seed z handshake (HSS shared / s_target) — dozwolone tylko gen=0
            k0 = hashlib.sha256(b"kpc-bootstrap-v1" + offer_key).digest()
        else:
            k0 = secrets.token_bytes(32)
        ctx = context_blob(epoch=epoch, bubble_id=bubble_id, gen=0, thermal=thermal, extra=extra)
        history.append(HistoryEntry(
            gen=0,
            epoch=epoch,
            bubble_id=bubble_id,
            key_commit=key_commit(k0, ctx),
            ctx_digest=ctx,
        ))
        return AcceptResult(True, RejectReason.OK, 0.0, 0.0, key_next=k0, detail="bootstrap gen=0")

    # ── Rotacja ────────────────────────────────────────────────────────────
    if not history.entries:
        return AcceptResult(
            False, RejectReason.NO_HISTORY, 1.0, 1.0,
            detail="brak historii — nie da się przewidzieć / zweryfikować kroku",
        )
    if key_t is None:
        return AcceptResult(
            False, RejectReason.NO_HISTORY, 1.0, 1.0,
            detail="brak key_t przy rotacji",
        )

    k_hat = evolve_exact(
        key_t, history, epoch=epoch, bubble_id=bubble_id, thermal=thermal, extra=extra
    )
    next_gen = history.gen + 1
    ctx = context_blob(
        epoch=epoch, bubble_id=bubble_id, gen=next_gen, thermal=thermal, extra=extra
    )

    if offer_key is None:
        # lokalna rotacja — produkujemy klucz
        k_offer = k_hat
        r_exact = 0.0
    else:
        k_offer = offer_key
        r_exact = 0.0 if hmac.compare_digest(k_offer, k_hat) else 1.0

    # Soft: fidelity interferencji na commit(K_t) → commit(K')
    # (właściwa matematyka EriAmo, nie digest-XOR)
    prev_commit = history.entries[-1].key_commit
    next_commit = key_commit(k_offer if r_exact == 0.0 else k_hat, ctx)
    r_soft = soft_residual_quantum(prev_commit, next_commit)

    if r_exact > 0.0:
        # przy external i tak liczymy soft względem złego offer (diagnostyka)
        r_soft = soft_residual_quantum(prev_commit, key_commit(k_offer, ctx))
        return AcceptResult(
            False, RejectReason.EXTERNAL_KEY, r_exact, r_soft,
            detail="offer ≠ evolve_exact — klucz z zewnątrz lub fork historii",
        )

    if use_soft and r_soft > th:
        return AcceptResult(
            False, RejectReason.SOFT_FAIL, r_exact, r_soft,
            detail=f"soft residual {r_soft:.4f} > θ={th:.4f} (F={1.0 - r_soft:.4f})",
        )

    history.append(HistoryEntry(
        gen=next_gen,
        epoch=epoch,
        bubble_id=bubble_id,
        key_commit=key_commit(k_offer, ctx),
        ctx_digest=ctx,
    ))
    return AcceptResult(
        True, RejectReason.OK, r_exact, r_soft, key_next=k_offer,
        detail="rotation ok",
    )


# ── Tracker sesji (hook HSL) ──────────────────────────────────────────────────

@dataclass
class KPCSession:
    """
    Stan KPC dla jednej sesji HSL.
    Rotacja tylko przez rotate() / bootstrap() — nie per-frame.

    Równolegle trzymamy |Ψ⟩ (przestrzeń interferencji EriAmo):
    soft residual = 1 − F(predict(Ψ_t), Ψ_{t+1}), gdzie Ψ ewoluuje
    **operatorem interferencji**, nie „predykcją digestu klucza”.
    """
    history: KeyHistory = field(default_factory=KeyHistory)
    key: bytes | None = None
    bubble_id: str = ""
    use_thermal_in_exact: bool = False  # domyślnie NIE — ochrona przed dziurawym KDF
    soft_gate: bool = False
    psi: Any = None  # np.ndarray complex — stan kwantowy sesji
    last_soft_residual: float = 0.0
    q_mode: str = "eriamo"
    q_dt: float = 0.1
    q_steps: int = 1

    def bootstrap_from_session_key(
        self,
        session_key: bytes,
        *,
        epoch: int,
        thermal: Sequence[float] | bytes | None = None,
    ) -> AcceptResult:
        from karmazyn_qpredict import embed_bytes

        th = thermal if self.use_thermal_in_exact else None
        res = accept_key_rotation(
            None,
            self.history,
            epoch=epoch,
            bubble_id=self.bubble_id,
            thermal=th,
            offer_key=session_key,
            bootstrap=True,
            soft_gate=False,  # soft na torze |Ψ⟩ poniżej, nie na embed(commit)
        )
        if res.ok and res.key_next is not None:
            self.key = res.key_next
            self.psi = embed_bytes(session_key)
            self.last_soft_residual = 0.0
        return res

    def rotate(
        self,
        *,
        epoch: int,
        thermal: Sequence[float] | bytes | None = None,
        offer_key: bytes | None = None,
    ) -> AcceptResult:
        from karmazyn_qpredict import evolve_interference, predict_state, residual

        th = thermal if self.use_thermal_in_exact else None
        res = accept_key_rotation(
            self.key,
            self.history,
            epoch=epoch,
            bubble_id=self.bubble_id,
            thermal=th,
            offer_key=offer_key,
            bootstrap=False,
            soft_gate=False,
        )
        if not res.ok:
            return res

        # Exact OK → ewolucja |Ψ⟩ + opcjonalna brama fidelity
        if self.psi is not None:
            psi_hat = predict_state(
                self.psi, steps=self.q_steps, dt=self.q_dt, mode=self.q_mode
            )
            psi_next = evolve_interference(
                self.psi, steps=self.q_steps, dt=self.q_dt, mode=self.q_mode
            )
            r_soft = residual(psi_hat, psi_next)
            self.last_soft_residual = r_soft
            res = AcceptResult(
                ok=res.ok,
                reason=res.reason,
                residual_exact=res.residual_exact,
                residual_soft=r_soft,
                key_next=res.key_next,
                detail=res.detail,
            )
            if self.soft_gate and r_soft > soft_theta():
                return AcceptResult(
                    False,
                    RejectReason.SOFT_FAIL,
                    res.residual_exact,
                    r_soft,
                    detail=(
                        f"Q-interference residual {r_soft:.4f} > θ={soft_theta():.4f} "
                        f"(F={1.0 - r_soft:.4f})"
                    ),
                )
            self.psi = psi_next

        if res.ok and res.key_next is not None:
            self.key = res.key_next
        return res


# ── Accuracy harness (do testów / doctor) ─────────────────────────────────────

@dataclass
class AccuracyReport:
    n: int
    mean_soft_residual_honest: float
    p95_soft_residual_honest: float
    p99_soft_residual_honest: float
    false_reject_rate_at_theta: dict[float, float]
    false_accept_rate_external: dict[float, float]
    recommend_soft_in_kdf: bool
    recommend_soft_gate: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "mean_soft_residual_honest": self.mean_soft_residual_honest,
            "p95_soft_residual_honest": self.p95_soft_residual_honest,
            "p99_soft_residual_honest": self.p99_soft_residual_honest,
            "false_reject_rate_at_theta": self.false_reject_rate_at_theta,
            "false_accept_rate_external": self.false_accept_rate_external,
            "recommend_soft_in_kdf": self.recommend_soft_in_kdf,
            "recommend_soft_gate": self.recommend_soft_gate,
            "notes": self.notes,
        }


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 1.0
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def measure_predictor_accuracy(
    n: int = 200,
    thermal_noise_sigma: float = 0.0,
    thetas: Iterable[float] = (0.02, 0.05, 0.08, 0.15, 0.25, 0.40),
    seed: int = 42,
) -> AccuracyReport:
    """
    Monte Carlo na **właściwym** torze KPC+|Ψ⟩:
      soft residual = 1−F(predict(Ψ), evolve(Ψ)) przy rotacji sesji.

    thermal_noise_sigma — legacy (thermal nie w torze |Ψ⟩); ignorowane poza notatką.
    Exact external key nadal musi być reject.
    """
    from karmazyn_qpredict import embed_bytes, fidelity, predict_state

    honest_soft: list[float] = []
    thetas = list(thetas)
    fr_num = {t: 0 for t in thetas}
    fa_num = {t: 0 for t in thetas}
    n_honest = 0
    n_ext = 0

    for i in range(n):
        sess = KPCSession(bubble_id="probe", use_thermal_in_exact=False, soft_gate=False)
        sk = hashlib.sha256(b"acc" + i.to_bytes(4, "big")).digest()
        r0 = sess.bootstrap_from_session_key(sk, epoch=1000 + i)
        assert r0.ok and sess.key is not None and sess.psi is not None

        for step in range(3):
            r = sess.rotate(epoch=1000 + i + step)
            assert r.ok, r.detail
            honest_soft.append(r.residual_soft)
            n_honest += 1
            for t in thetas:
                if r.residual_soft > t:
                    fr_num[t] += 1

        # External state vs prediction — FAR soft
        psi_hat = predict_state(sess.psi, steps=sess.q_steps, dt=sess.q_dt, mode=sess.q_mode)
        psi_evil = embed_bytes(secrets.token_bytes(32))
        r_e = 1.0 - fidelity(psi_hat, psi_evil)
        n_ext += 1
        for t in thetas:
            # FAR: external accepted when residual soft small (ε ≤ θ)
            if r_e <= t:
                fa_num[t] += 1

        evil = secrets.token_bytes(32)
        bad = accept_key_rotation(
            sess.key, sess.history, epoch=1000 + i + 99, bubble_id=sess.bubble_id,
            offer_key=evil, soft_gate=False,
        )
        assert not bad.ok and bad.reason == RejectReason.EXTERNAL_KEY

    honest_soft.sort()
    mean_h = sum(honest_soft) / max(1, len(honest_soft))
    p95 = _percentile(honest_soft, 95)
    p99 = _percentile(honest_soft, 99)

    frr = {t: fr_num[t] / max(1, n_honest) for t in thetas}
    far = {t: fa_num[t] / max(1, n_ext) for t in thetas}

    notes: list[str] = [
        "Soft = 1−F na torze interferencji EriAmo (|Ψ⟩), nie XOR-digest.",
        f"mean ε_honest={mean_h:.6f} p99={p99:.6f}  (ε=0 ⇒ F=1)",
    ]
    recommend_kdf = p99 < 0.02 and mean_h < 0.01
    recommend_gate = False
    good_theta = None
    for t in thetas:
        if frr[t] <= 0.01 and far[t] <= 0.05:
            recommend_gate = True
            good_theta = t
            break

    if recommend_kdf:
        notes.append(
            "ε~0 na honest — soft-fidelity nie w KDF (klucz i tak exact); "
            "wolno użyć F jako bramy ciągłości |Ψ⟩."
        )
    else:
        notes.append("ε honest niezerowe — sprawdź mode/dt; exact KDF bez zmian.")
    if recommend_gate:
        notes.append(
            f"Soft gate OK przy θ≈{good_theta} (ε max; F≥{1 - good_theta:.3f}), "
            f"FRR≤1%, FAR≤5%."
        )
    else:
        notes.append("Brak θ z FRR≤1% i FAR≤5% — soft_gate off.")
    if thermal_noise_sigma > 0:
        notes.append(
            f"thermal_noise_sigma={thermal_noise_sigma} nie wpływa na tor |Ψ⟩ "
            "(celowo poza KDF/predykcją interferencji)."
        )

    return AccuracyReport(
        n=n,
        mean_soft_residual_honest=mean_h,
        p95_soft_residual_honest=p95,
        p99_soft_residual_honest=p99,
        false_reject_rate_at_theta=frr,
        false_accept_rate_external=far,
        recommend_soft_in_kdf=False,  # nigdy soft w KDF — tylko exact
        recommend_soft_gate=recommend_gate,
        notes=notes,
    )
