"""
karmazyn_hsl.py — Holographic Session Links (HSL) v1.1 transport layer
=======================================================================
Implementacja klasyczna (CPU): Φ² tożsamość węzła, rezonans łącza, PrismMask/AAD.

L0 vs ontologia (papers + bubble_network_assumptions)
------------------------------------------------------
  Dziś Carrier L0 = TCP (KSH/Cynober = nakładka na TCP).
  L0 jest wymienne (A6): ETH / Wi-Fi / IPC / plik / mesh / przyszły QKD link.
  Ontologia łącza = HSL + (HSS seed) + Surface/bąbel — nie „mamy socket”.

Łańcuch entropii (HSL Paper §6.4, hybrid QKD + HSL)
--------------------------------------------------
  [przyszłość] k_QKD z łącza kwantowego (QKD / splątanie) — seed IT-secure
        ↓
  [dziś]       KARM_QKD_SEED — ten sam slot KDF (env/file/pipe: karmazyn_qkd.py)
        +
  handshake HSS/ECDH — wiązanie epizodyczne na klasycznym Carrier (TCP)
        +
  Φ² per węzeł — tożsamość długoterminowa (commit, nie plaintext)
        +
  epoch — rotacja s_target w czasie
        +
  KPC (karmazyn_key_predict) — ciągłość klucza przy bootstrap/rotacji epoki;
       tor |Ψ⟩ (karmazyn_qpredict, interferencja EriAmo) = brama fidelity, nie KDF

Bez KARM_QKD_SEED: link_seed = klucz z handshake (tryb klasyczny TCP).
Z KARM_QKD_SEED:  link_seed = HKDF(k_QKD ‖ handshake_key) — obie strony
                  MUSZĄ mieć identyczny seed (jak przy PSK).

Przyszła integracja QKD (bez przepisywania HSL/RPC)
---------------------------------------------------
1. Adapter QKD → ten sam slot co KARM_QKD_SEED (DT Berlin / Paderborn itd.).
2. HSS KEM na TCP może zejść na drugi plan; HSL + app bez zmian (§6.4).
3. Fingerprint qkd_fp w hsl_link wykrywa rozjazd seeda przed RPC.

Zmienne: KARM_QKD_SEED, KARM_PHI2, KARM_HSL_EPOCH_SEC,
         KARM_KPC_SOFT_GATE, KARM_KPC_SOFT_THETA — patrz cynober_manual.md
         i docs/SESSION_L0_KPC.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any

HSL_VERSION = "HSL-1.1"
HSL_TASK_DEFAULT = "cynober-rpc"
HSL_PRISMS_DEFAULT = ("karminql",)
HSL_EPOCH_SEC = int(os.environ.get("KARM_HSL_EPOCH_SEC", "3600"))
try:
    from cynober_paths import phi2_path, relocate_legacy

    relocate_legacy()
    PHI2_PATH = str(phi2_path())
except Exception:
    PHI2_PATH = os.path.join(os.path.expanduser("~"), ".karmazyn_phi2")


def current_epoch(now: float | None = None, epoch_sec: int = HSL_EPOCH_SEC) -> int:
    t = time.time() if now is None else now
    return int(t // epoch_sec)


def load_qkd_seed() -> bytes | None:
    """
    k_QKD — slot na seed z QKD (paper §6.4).

    Źródła: KARM_QKD_SEED, KARM_QKD_PATH, KARM_QKD_PIPE (karmazyn_qkd.py).
    """
    from karmazyn_qkd import load_qkd_bytes

    return load_qkd_bytes()


def qkd_seed_active() -> bool:
    return load_qkd_seed() is not None


def qkd_fingerprint(qkd_seed: bytes | None = None) -> str | None:
    """Skrót seeda QKD do weryfikacji zgodności stron (nie ujawnia seeda)."""
    seed = qkd_seed if qkd_seed is not None else load_qkd_seed()
    if not seed:
        return None
    return hashlib.sha256(b"qkd-fp-v1" + seed).hexdigest()[:16]


def load_phi2() -> bytes:
    """Φ² — trwały sekret węzła (Appendix A paperu HSL)."""
    env = os.environ.get("KARM_PHI2", "").strip()
    if env:
        raw = bytes.fromhex(env) if len(env) >= 64 else env.encode("utf-8")
        return hashlib.sha256(raw).digest()

    try:
        with open(PHI2_PATH, "rb") as f:
            data = f.read()
        if len(data) >= 32:
            return hashlib.sha256(data).digest()
    except OSError:
        pass

    seed = secrets.token_bytes(32)
    try:
        with open(PHI2_PATH, "wb") as f:
            f.write(seed)
    except OSError:
        pass
    return hashlib.sha256(seed).digest()


def _hkdf(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-SHA256 bez zależności od wersji Pythona."""
    prk = hmac.new(b"karmazyn-hsl-v1", ikm, hashlib.sha256).digest()
    out = bytearray()
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(
            prk,
            block + info + bytes([counter]),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


_UNSET_QKD: Any = object()


def hybrid_link_seed(
    shared_key: bytes,
    qkd_seed: bytes | None | Any = _UNSET_QKD,
) -> bytes:
    """
    Hybryda QKD+HSL: KDF(k_QKD, handshake_key).

    qkd_seed=None — wymuszenie trybu bez QKD (testy).
    Brak argumentu — load_qkd_seed() (adapter/env).
    """
    if qkd_seed is _UNSET_QKD:
        qkd = load_qkd_seed()
    else:
        qkd = qkd_seed
    if not qkd:
        return shared_key
    return _hkdf(qkd + shared_key, b"hsl-hybrid-qkd-v1")


def node_session_secret(
    shared_key: bytes,
    phi2: bytes,
    epoch: int,
    qkd_seed: bytes | None = None,
) -> bytes:
    """s_sess węzła: KDF(hybrid_seed, Φ², epoch) — paper Def. 2.1 + §6.4."""
    link = hybrid_link_seed(shared_key, qkd_seed)
    material = link + phi2 + epoch.to_bytes(8, "big")
    return _hkdf(material, b"hsl-sess")


def link_commit(phi2: bytes, link_nonce: bytes) -> bytes:
    return hashlib.sha256(b"hsl-commit-v1" + phi2 + link_nonce).digest()


def prism_target(
    shared_key: bytes,
    commit_local: bytes,
    commit_remote: bytes,
    epoch: int,
    task: str = HSL_TASK_DEFAULT,
    prisms: tuple[str, ...] = HSL_PRISMS_DEFAULT,
    qkd_seed: bytes | None = None,
) -> bytes:
    """PrismMask — kontekst zadania i pryzmatów (Def. 3.2 paperu)."""
    ctx = json.dumps(
        {"task": task, "prisms": list(prisms)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ordered = b"".join(sorted([commit_local, commit_remote]))
    link = hybrid_link_seed(shared_key, qkd_seed)
    material = link + ordered + epoch.to_bytes(8, "big") + ctx
    return _hkdf(material, b"hsl-prism")


def build_aad(
    source_id: str,
    target_id: str,
    task: str,
    epoch: int,
    direction: str,
) -> bytes:
    """AAD ramki RPC — wiązanie kontekstu (Def. 3.2)."""
    payload = (
        f"{source_id}\0{target_id}\0{task}\0{epoch}\0{direction}".encode("utf-8")
    )
    return hashlib.sha256(b"hsl-aad-v1" + payload).digest()


def capability_token(s_target: bytes, label: str) -> str:
    return hmac.new(s_target, label.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_capability(s_target: bytes, label: str, token: str) -> bool:
    expected = capability_token(s_target, label)
    return hmac.compare_digest(expected, token)


CAP_RPC_QUERY = "rpc:query"
CAP_RPC_ADMIN = "rpc:admin"
CAP_PRISM_KARMINQL = "karminql:query"


def derive_frame_key(s_target: bytes, aad: bytes) -> bytes:
    return _hkdf(s_target + aad, b"hsl-frame")


@dataclass
class HSLLink:
    epoch: int
    s_target: bytes
    local_id: str
    remote_id: str
    task: str
    prisms: tuple[str, ...]
    qkd_hybrid: bool = False
    _shared_key: bytes = b""
    _commit_local: bytes = b""
    _commit_remote: bytes = b""
    _qkd_seed: bytes | None = None
    # KPC: ciągłość ewolucji klucza (rotacja/bootstrap only — nie per-frame)
    _kpc: Any = None
    kpc_last_soft_residual: float = 0.0
    kpc_enabled: bool = True

    def aad_request(self) -> bytes:
        return build_aad(self.local_id, self.remote_id, self.task, self.epoch, "req")

    def aad_response(self) -> bytes:
        return build_aad(self.remote_id, self.local_id, self.task, self.epoch, "resp")

    def request_key(self) -> bytes:
        self.ensure_epoch()
        return derive_frame_key(self.s_target, self.aad_request())

    def response_key(self) -> bytes:
        self.ensure_epoch()
        return derive_frame_key(self.s_target, self.aad_response())

    def _kpc_bootstrap(self) -> None:
        """Start łańcucha KPC z s_target po establish (gen=0)."""
        if not self.kpc_enabled:
            return
        from karmazyn_key_predict import KPCSession

        sess = KPCSession(
            bubble_id=f"hsl:{self.local_id}:{self.remote_id}",
            use_thermal_in_exact=False,  # soft/thermal NIE w KDF
            soft_gate=False,
        )
        res = sess.bootstrap_from_session_key(self.s_target, epoch=self.epoch)
        if not res.ok:
            raise RuntimeError(f"HSL/KPC bootstrap reject: {res.reason.value} ({res.detail})")
        self._kpc = sess
        self.kpc_last_soft_residual = res.residual_soft

    def _kpc_rotate(self, new_epoch: int) -> None:
        """Rotacja KPC tylko przy zmianie epoki — exact ratchet; |Ψ⟩ fidelity w residual."""
        if not self.kpc_enabled or self._kpc is None:
            return
        res = self._kpc.rotate(epoch=new_epoch)
        self.kpc_last_soft_residual = float(res.residual_soft)
        if hasattr(self._kpc, "last_soft_residual"):
            self.kpc_last_soft_residual = float(self._kpc.last_soft_residual)
        if not res.ok:
            raise RuntimeError(
                f"HSL/KPC rotation reject: {res.reason.value} "
                f"exact={res.residual_exact:.2f} soft={res.residual_soft:.4f} ({res.detail})"
            )

    def ensure_epoch(self, now: float | None = None) -> bool:
        """Rotacja epoki — odśwież s_target gdy minął HSL_EPOCH_SEC (obie strony synchronicznie).

        KPC: aktualizacja łańcucha klucza **tylko tu** (nie na każdej ramce RPC).
        """
        new_epoch = current_epoch(now)
        if new_epoch == self.epoch:
            return False
        if not self._shared_key or not self._commit_local or not self._commit_remote:
            return False
        self.epoch = new_epoch
        self.s_target = prism_target(
            self._shared_key,
            self._commit_local,
            self._commit_remote,
            self.epoch,
            task=self.task,
            prisms=self.prisms,
            qkd_seed=self._qkd_seed,
        )
        # Ciągłość predykcyjna: exact evolve z historii; soft residual zapisany do diagnostyki
        self._kpc_rotate(new_epoch)
        return True

    def rpc_capability(self, label: str = CAP_RPC_QUERY) -> str:
        self.ensure_epoch()
        return capability_token(self.s_target, label)


def link_nonce_from_caps(local_caps: dict[str, Any], remote_caps: dict[str, Any]) -> bytes:
    parts = sorted([
        str(local_caps.get("session_id", "")),
        str(remote_caps.get("session_id", "")),
        str(local_caps.get("node_id", "")),
        str(remote_caps.get("node_id", "")),
    ])
    return hashlib.sha256("|".join(parts).encode("utf-8")).digest()


def perform_hsl_link(
    sock: socket.socket,
    shared_key: bytes,
    local_caps: dict[str, Any],
    remote_caps: dict[str, Any],
    is_server: bool,
    deadline: float,
    *,
    phi2: bytes | None = None,
    qkd_seed: bytes | None = None,
) -> HSLLink:
    """
    Faza 2 HSL po handshake krypto: wymiana commit Φ² + potwierdzenie rezonansu.
    Bez ujawniania Φ² — tylko H(commit) i HMAC(s_target, 'establish').
    """
    from karmazyn_handshake import _recv_json, _send_json

    phi2 = phi2 or load_phi2()
    qkd = qkd_seed if qkd_seed is not None else load_qkd_seed()
    epoch = current_epoch()
    link_nonce = link_nonce_from_caps(local_caps, remote_caps)
    local_id = str(local_caps.get("node_id", "unknown"))
    remote_id = str(remote_caps.get("node_id", "unknown"))
    commit_local = link_commit(phi2, link_nonce)
    fp_local = qkd_fingerprint(qkd)

    local_msg: dict[str, Any] = {
        "type": "hsl_link",
        "version": HSL_VERSION,
        "epoch": epoch,
        "node_id": local_id,
        "commit": commit_local.hex(),
    }
    if fp_local:
        local_msg["qkd_fp"] = fp_local

    if is_server:
        remote_msg = _recv_json(sock, deadline)
        _send_json(sock, local_msg)
    else:
        _send_json(sock, local_msg)
        remote_msg = _recv_json(sock, deadline)

    if remote_msg.get("type") != "hsl_link":
        raise RuntimeError("HSL: oczekiwano hsl_link, otrzymano inny typ ramki")
    if remote_msg.get("version") != HSL_VERSION:
        raise RuntimeError(
            f"HSL: niezgodna wersja {remote_msg.get('version')!r}, oczekiwano {HSL_VERSION}"
        )

    remote_epoch = int(remote_msg.get("epoch", -1))
    if remote_epoch != epoch:
        raise RuntimeError(
            f"HSL: rozjazd epok {epoch} vs {remote_epoch} — brak rezonansu sesji"
        )

    msg_remote_id = str(remote_msg.get("node_id", ""))
    if msg_remote_id != remote_id:
        raise RuntimeError("HSL: node_id w linku nie zgadza się z caps")

    commit_remote = bytes.fromhex(str(remote_msg.get("commit", "")))
    if len(commit_remote) != 32:
        raise RuntimeError("HSL: niepoprawny commit zdalny")

    fp_remote = remote_msg.get("qkd_fp")
    if (fp_local is None) != (fp_remote is None):
        raise RuntimeError(
            "HSL: rozjazd trybu QKD — jedna strona ma KARM_QKD_SEED, druga nie"
        )
    if fp_local and fp_remote and not hmac.compare_digest(str(fp_local), str(fp_remote)):
        raise RuntimeError("HSL: rozjazd seeda QKD — fingerprint nie zgadza się")

    s_target = prism_target(
        shared_key, commit_local, commit_remote, epoch, qkd_seed=qkd
    )
    local_cap = capability_token(s_target, "establish")

    cap_msg = {"type": "hsl_cap", "cap": local_cap}
    if is_server:
        remote_cap_msg = _recv_json(sock, deadline)
        _send_json(sock, cap_msg)
    else:
        _send_json(sock, cap_msg)
        remote_cap_msg = _recv_json(sock, deadline)

    if remote_cap_msg.get("type") != "hsl_cap":
        raise RuntimeError("HSL: oczekiwano hsl_cap")
    remote_cap = str(remote_cap_msg.get("cap", ""))
    if not verify_capability(s_target, "establish", remote_cap):
        raise RuntimeError("HSL: brak rezonansu — zdalny link_cap nieprawidłowy")

    link = HSLLink(
        epoch=epoch,
        s_target=s_target,
        local_id=local_id,
        remote_id=remote_id,
        task=HSL_TASK_DEFAULT,
        prisms=HSL_PRISMS_DEFAULT,
        qkd_hybrid=bool(qkd),
        _shared_key=shared_key,
        _commit_local=commit_local,
        _commit_remote=commit_remote,
        _qkd_seed=qkd,
    )
    # KPC bootstrap (historia gen=0) — brama ewolucji; nie per-frame
    link._kpc_bootstrap()
    return link