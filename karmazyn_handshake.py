#!/usr/bin/env python3
"""
karmazyn_handshake.py — KarmazynOS Symmetric Handshake v1.2
=============================================================
Maciej Mazur, Warsaw 2026

Protokół symetryczny A↔B: oba węzły mają PEŁNY phi-space po uścisku.

Architektura protokołu (4 fazy):
  FAZA 0 — Powitanie:    wymiana wersji, node_id, capability flags
  FAZA 1 — Kryptografia: Ring-LWE (HSS) → ECDH (X25519) → PBKDF2 fallback
  FAZA 2 — Serializacja: pełny phi-space → JSON blob
  FAZA 3 — Wymiana:      wzajemny transfer zaszyfrowanych blobów
  FAZA 4 — Scalenie:     union atomów; konflikt → wyższe T wygrywa + MAC confirm

Strategia scalenia symetrycznego:
  • Atom istnieje po obu stronach → zachowaj ten z wyższym T (gorętszy = aktywniejszy)
  • Atom tylko po jednej stronie   → dodaj do drugiej strony
  • Wynik: oba phi-space mają identyczny zestaw atomów (union + conflict-by-T)

Transport:
  TCP socket, IPv4, framing = 4-bajty big-endian length prefix + JSON payload
  Maks payload: 64 MB. Timeout: 30 s.
  Kompresja: zlib level-6 przed szyfrowaniem (JSON atomów kompresuje się 3-10x).

Kryptografia (priorytety):
  1. HSS  — Ring-LWE post-quantum (karmazyn_hss, gdy dostępny)
  2. ECDH — X25519 + HKDF-SHA256 + AES-256-GCM (cryptography lib)
  3. PBKDF2 — fallback bez zewnętrznych zależności (słabsze, tylko dev)

Użycie z shella KarmazynOS:
  HANDSHAKE SERVE [port]       ← węzeł A czeka
  HANDSHAKE <host> [port]      ← węzeł B łączy

Użycie standalone demo:
  python3 karmazyn_handshake.py --demo
"""

import hashlib
import hmac as _hmac
import math
import zlib
import json
import os
import secrets
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Importy KarmazynOS (graceful degradation) ─────────────────────────────────

try:
    from karmazyn_hss import HSSDaemon, resolve_hss_profile
    _HSS_AVAILABLE = True
except ImportError:
    _HSS_AVAILABLE = False

try:
    from karmazyn_phi import PhiSpace
    _PHI_AVAILABLE = True
except ImportError:
    _PHI_AVAILABLE = False

# ── Crypto fallback: X25519 + AES-256-GCM (cryptography >= 2.6) ──────────────

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey,
    )
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

# ── Stałe protokołu ───────────────────────────────────────────────────────────

PROTO_VERSION   = "KSH-1.2"

# SYNCUJE:     atomy phi-space (RAM): id, T, S, E, state, age, T_max
# SOUL (v8.1): bąble+bindings przez cynober_gossip GOSSIP * SOUL (BubbleVFS-lite)
# PhiSpace API: phi.matrix.atoms() / get_atom() / create_atom()
# PSK auth:     export KARM_PSK=haslo_sieci
FRAME_MAX_BYTES = 64 * 1024 * 1024   # 64 MB hard cap
TIMEOUT_SEC     = 30.0
TOMB_THRESHOLD  = 2.0                 # atomy T < 2.0 nie są transferowane
MERGE_TEMP_BIAS  = 0.0
REPLAY_WINDOW_SEC = 300.0              # anty-replay: 5 min — zapas na Termux NTP drift                 # bias scalenia (0 = zdalny musi być > lokalny)


# ─────────────────────────────────────────────────────────────────────────────
# Transport helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_frame(sock: socket.socket, data: bytes) -> None:
    """Wyślij ramkę: 4-bajtowy big-endian length prefix + payload."""
    if len(data) > FRAME_MAX_BYTES:
        raise ValueError(f"Payload za duży: {len(data)} > {FRAME_MAX_BYTES}")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recv_frame(sock: socket.socket, deadline: float = 0.0) -> bytes:
    """Odbierz ramkę z length prefix."""
    hdr = _recv_exact(sock, 4, deadline)
    n   = struct.unpack(">I", hdr)[0]
    if n > FRAME_MAX_BYTES:
        raise ValueError(f"Ramka za duża: {n}")
    return _recv_exact(sock, n, deadline)


def _recv_exact(sock: socket.socket, n: int, deadline: float = 0.0) -> bytes:
    """[BUG FIX #4] deadline anti-slowloris — bez tego 1 bajt/20s wisi w nieskończoność."""
    buf = bytearray()
    while len(buf) < n:
        if deadline and time.monotonic() > deadline:
            raise TimeoutError(f"Deadline przekroczony: odebrano {len(buf)}/{n} B")
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Połączenie zamknięte przed odebraniem danych")
        buf.extend(chunk)
    return bytes(buf)


def _send_json(sock: socket.socket, obj: dict) -> None:
    # [BUG FIX #15] sort_keys=True — canonical JSON niezależny od platformy
    _send_frame(sock, json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _recv_json(sock: socket.socket, deadline: float = 0.0) -> dict:
    # [BUG FIX #18] errors=replace — nie crashuje na malformed UTF-8
    return json.loads(_recv_frame(sock, deadline).decode("utf-8", errors="replace"))


def _compress(data: bytes) -> bytes:
    """zlib compress — JSON kompresuje się 3-10x."""
    return zlib.compress(data, level=6)


def _decompress(data: bytes) -> bytes:
    return zlib.decompress(data)


# ─────────────────────────────────────────────────────────────────────────────
# Warstwa kryptograficzna
# ─────────────────────────────────────────────────────────────────────────────

class _CryptoLayer:
    """
    Trzy implementacje z identycznym API:
      HSS    → Ring-LWE (post-quantum, karmazyn_hss)
      ECDH   → X25519 + HKDF-SHA256 + AES-256-GCM (cryptography lib)
      simple → PBKDF2-HMAC-SHA256 + keystream XOR (brak zewnętrznych zależności)

    Każda negocjacja ustawia self._key i self._mode.
    encrypt()/decrypt() działają automatycznie w wynegocjowanym trybie.
    """

    def __init__(self) -> None:
        self._key:  Optional[bytes] = None
        self._mode: str             = "none"

    # ── HSS (Ring-LWE post-quantum) ───────────────────────────────────────────

    def negotiate_hss_initiator(self, sock: socket.socket, deadline: float = 0.0) -> bytes:
        # Zakładane: init_session() → (token, pubkey_bytes: bytes)
        profile = resolve_hss_profile()
        hss = HSSDaemon(profile=profile)
        try:
            token, pubkey_bytes = hss.init_session()
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"HSSDaemon.init_session() niezgodne API: {e}") from e
        _send_json(sock, {
            "mode": "hss",
            "pubkey": pubkey_bytes.hex(),
            "hss_profile": profile.name,
        })
        resp        = _recv_json(sock, deadline)
        ack         = bytes.fromhex(resp["ack"])
        key         = hss.finalize(token, ack)
        self._key   = key
        self._mode  = "hss"
        return key

    def negotiate_hss_responder(self, sock: socket.socket,
                                 init_msg: dict) -> bytes:
        # Zakładane: respond_handshake(pubkey) → (token, shared_key, ack: bytes)
        remote_profile = init_msg.get("hss_profile", "proto")
        profile = resolve_hss_profile()
        if remote_profile != profile.name:
            raise RuntimeError(
                f"Niezgodny profil HSS: klient={remote_profile}, serwer={profile.name}. "
                f"Ustaw KARM_HSS_PROFILE na obu stronach."
            )
        hss = HSSDaemon(profile=profile)
        pubkey_a = bytes.fromhex(init_msg["pubkey"])
        try:
            _token_b, shared_key, ack = hss.respond_handshake(pubkey_a)
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"HSSDaemon.respond_handshake() niezgodne API: {e}") from e
        _send_json(sock, {"ack": ack.hex(), "hss_profile": profile.name})
        self._key  = shared_key
        self._mode = "hss"
        return shared_key

    # ── ECDH fallback (X25519 + HKDF + AES-256-GCM) ──────────────────────────

    def negotiate_ecdh_initiator(self, sock: socket.socket, deadline: float = 0.0) -> bytes:
        priv = X25519PrivateKey.generate()
        pub  = priv.public_key().public_bytes_raw()
        _send_json(sock, {"mode": "ecdh", "pubkey": pub.hex()})
        resp   = _recv_json(sock, deadline)
        pub_b  = X25519PublicKey.from_public_bytes(bytes.fromhex(resp["pubkey"]))
        shared = priv.exchange(pub_b)
        key    = self._hkdf(shared)
        self._key  = key
        self._mode = "ecdh"
        return key

    def negotiate_ecdh_responder(self, sock: socket.socket,
                                  init_msg: dict) -> bytes:
        pub_a  = X25519PublicKey.from_public_bytes(
            bytes.fromhex(init_msg["pubkey"]))
        priv   = X25519PrivateKey.generate()
        pub    = priv.public_key().public_bytes_raw()
        _send_json(sock, {"pubkey": pub.hex()})
        shared = priv.exchange(pub_a)
        key    = self._hkdf(shared)
        self._key  = key
        self._mode = "ecdh"
        return key

    # ── Simple fallback (PBKDF2 — tylko dev/demo) ─────────────────────────────

    def negotiate_simple_initiator(self, sock: socket.socket, deadline: float = 0.0) -> bytes:
        nonce_a = secrets.token_bytes(32)
        _send_json(sock, {"mode": "simple", "nonce": nonce_a.hex()})
        resp    = _recv_json(sock, deadline)
        nonce_b = bytes.fromhex(resp["nonce"])
        key     = hashlib.pbkdf2_hmac(
            "sha256", nonce_a + nonce_b, b"karmazyn-handshake-v1", 100_000)
        self._key  = key
        self._mode = "simple"
        return key

    def negotiate_simple_responder(self, sock: socket.socket,
                                    init_msg: dict) -> bytes:
        nonce_a = bytes.fromhex(init_msg["nonce"])
        nonce_b = secrets.token_bytes(32)
        _send_json(sock, {"nonce": nonce_b.hex()})
        key     = hashlib.pbkdf2_hmac(
            "sha256", nonce_a + nonce_b, b"karmazyn-handshake-v1", 100_000)
        self._key  = key
        self._mode = "simple"
        return key

    # ── Encrypt / Decrypt ─────────────────────────────────────────────────────

    def encrypt(
        self,
        data: bytes,
        *,
        key: Optional[bytes] = None,
        aad: Optional[bytes] = None,
    ) -> bytes:
        assert self._key, "Klucz nie wynegocjowany — wywołaj negotiate_* najpierw"
        use_key = (key or self._key)[:32]
        if self._mode in ("hss", "ecdh") and _CRYPTO_OK:
            nonce = secrets.token_bytes(12)
            ct    = AESGCM(use_key).encrypt(nonce, data, aad)
            return nonce + ct
        # Fallback: keystream XOR z per-ramkowym nonce (bez reużycia pada).
        # Nonce (8 B) idzie jawnie z ramką; dwie ramki nie dzielą już keystreamu.
        nonce = secrets.token_bytes(8)
        stream_key = hashlib.sha256(use_key + (aad or b"")).digest()
        ks = hashlib.shake_256(stream_key + b"ks" + nonce).digest(len(data))
        body = bytes(a ^ b for a, b in zip(data, ks))
        return nonce + body

    def decrypt(
        self,
        data: bytes,
        *,
        key: Optional[bytes] = None,
        aad: Optional[bytes] = None,
    ) -> bytes:
        assert self._key, "Klucz nie wynegocjowany"
        use_key = (key or self._key)[:32]
        if self._mode in ("hss", "ecdh") and _CRYPTO_OK:
            nonce, ct = data[:12], data[12:]
            return AESGCM(use_key).decrypt(nonce, ct, aad)
        nonce, body = data[:8], data[8:]
        stream_key = hashlib.sha256(use_key + (aad or b"")).digest()
        ks = hashlib.shake_256(stream_key + b"ks" + nonce).digest(len(body))
        return bytes(a ^ b for a, b in zip(body, ks))

    @property
    def mode(self) -> str:
        return self._mode

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _hkdf(self, ikm: bytes) -> bytes:
        h = HKDF(
            algorithm=_hashes.SHA256(),
            length=32,
            salt=b"karmazyn-phi-sync",
            info=b"handshake-v1",
        )
        return h.derive(ikm)

    def _xor_stream(self, data: bytes, nonce: bytes = b"") -> bytes:
        """Keystream z SHAKE-256, różnicowany per-ramkę przez nonce.
        Symetryczny: encrypt = decrypt przy tym samym (key, nonce, długość).
        Bez nonce degeneruje do trybu poprzedniego — dlatego encrypt ZAWSZE
        podaje świeży nonce, by uniknąć reużycia pada (two-time pad)."""
        ks = hashlib.shake_256(self._key + b"ks" + nonce).digest(len(data))
        return bytes(a ^ b for a, b in zip(data, ks))


# ─────────────────────────────────────────────────────────────────────────────
# Serializacja / scalenie phi-space
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_atoms(phi: Any) -> List[dict]:
    """
    Serializuj atomy z PhiSpace do listy dict.
    Pomija TOMB i atomy z T < TOMB_THRESHOLD.
    Duck-typing: phi.matrix.atoms() → List[Atom|dict]
    """
    try:
        atoms = phi.matrix.atoms()
    except Exception:
        return []

    node = _node_id()   # dołączany do każdego atomu — potrzebny do tiebreakera
    result = []
    for a in atoms:
        T     = float(getattr(a, "T", 0))
        if not math.isfinite(T):   # [BUG FIX #12] NaN/Inf → crash parsera
            T = 0.0
        state = str(getattr(a, "state", "WARM"))
        if T < TOMB_THRESHOLD or state == "TOMB":
            continue
        result.append({
            "id":       str(getattr(a, "id",    "")),
            "S":        str(getattr(a, "S",     "")),
            "E":        str(getattr(a, "E",     "")),
            "T":        T,
            "T_max":    float(getattr(a, "T_max", 100.0)),
            "state":    state,
            "age":      int(getattr(a, "age",   0)),
            "_node_id": node,   # tiebreaker w _merge_atoms
        })
    return result


def _merge_atoms(phi: Any,
                 remote_atoms: List[dict]) -> Tuple[int, int, int]:
    """
    Symetryczne scalenie phi-space.

    Zasada:
      • Atom istnieje lokalnie i zdalnie → wygrywa wyższe T (aktywniejszy)
      • Atom tylko zdalny               → dodaj do lokalnego phi-space
      • Atom tylko lokalny              → bez zmian (lokalny zostaje)

    Zwraca: (dodane, zaktualizowane, pominięte)
    """
    added = updated = skipped = 0

    for rec in remote_atoms:
        atom_id  = rec.get("id", "")
        T_remote = float(rec.get("T", 0))

        if not atom_id or T_remote < TOMB_THRESHOLD:
            skipped += 1
            continue

        existing = None
        try:
            existing = phi.get_atom(atom_id)
        except Exception:
            pass

        if existing is not None:
            T_local = float(getattr(existing, "T", 0))
            # [BUG FIX #2] tiebreaker oparty o hash treści — nie node_id
            # node_id jest arbitralny; hash treści jest deterministyczny
            if T_remote > T_local + MERGE_TEMP_BIAS:
                r_win = True
            elif abs(T_remote - T_local) <= MERGE_TEMP_BIAS:
                _r = {k: rec.get(k) for k in ("id", "S", "E", "T")}
                _l = {"id": atom_id, "S": str(getattr(existing, "S", "")),
                      "E": str(getattr(existing, "E", "")), "T": T_local}
                r_win = (hashlib.sha256(json.dumps(_r, sort_keys=True).encode()).hexdigest()
                       > hashlib.sha256(json.dumps(_l, sort_keys=True).encode()).hexdigest())
            else:
                r_win = False
            if r_win:
                try:
                    existing.T = T_remote
                    if rec.get("S"):
                        existing.S = rec["S"]
                    if rec.get("E"):
                        existing.E = rec["E"]
                    updated += 1
                except Exception:
                    skipped += 1
            else:
                # Lokalny wygrał (lub remis + lokalny node_id mniejszy)
                skipped += 1
        else:
            # Nowy atom — dodaj do phi-space
            try:
                _na = phi.create_atom(atom_id,
                    S=rec.get("S", ""), E=rec.get("E", ""), T=T_remote)
                if _na is not None:
                    try:
                        _st = rec.get("state", "WARM")
                        _na.state = _st if _st != "TOMB" else "COLD"  # TOMB guard
                        _na.age   = rec.get("age",   0)
                        _na.T_max = rec.get("T_max", 100.0)
                        _na.touch()   # aktywuj w thermal engine
                    except Exception: pass
                added += 1
            except Exception as _ex:
                skipped += 1  # [BUG FIX #13] loguj
                try:
                    import logging as _lg; _lg.warning("[merge] create %s: %s", atom_id, _ex)
                except Exception: pass

    return added, updated, skipped


# Cache widzianych session_id — anty-replay (RAM, reset przy restarcie procesu)
# Wystarczy dla LAN; produkcja powinna persystować na dysk z TTL
_SEEN_SESSIONS: set = set()


# ─────────────────────────────────────────────────────────────────────────────
# KarmazynHandshake — główna klasa
# ─────────────────────────────────────────────────────────────────────────────

class KarmazynHandshake:
    """
    Symetryczny uścisk dwóch węzłów KarmazynOS (A↔B równoprawne).

    Po zakończeniu uścisku oba phi-space zawierają union wszystkich atomów
    z obu węzłów. Konflikty rozstrzygane przez temperaturę T — gorętszy atom
    (bardziej aktywny) zachowuje swoje S i E.

    Użycie — węzeł A (czeka):
        hs = KarmazynHandshake(phi_space)
        result = hs.serve(port=7700)

    Użycie — węzeł B (łączy):
        hs = KarmazynHandshake(phi_space)
        result = hs.connect(host="192.168.x.x", port=7700)

    Wynik (dict):
        status, role, crypto_mode, remote_node,
        local_atoms, remote_atoms, merged_total,
        added, updated, skipped,
        state_hash, peer_mac_ok, elapsed_s
    """

    def __init__(self,
                 phi:    Any,
                 log_fn: Optional[Callable[[str], None]] = None,
                 psk:    Optional[bytes] = None) -> None:  # [BUG FIX #8] peer auth
        self.phi        = phi
        self._log       = log_fn or print
        self._crypto    = _CryptoLayer()
        self._phi_lock  = threading.Lock()  # [BUG FIX #10] race w serve_async
        self._psk       = psk               # [BUG FIX #8] optional PSK auth

    # ── Publiczne API ─────────────────────────────────────────────────────────

    def serve(self,
              host:    str   = "0.0.0.0",
              port:    int   = 7700,
              timeout: float = TIMEOUT_SEC) -> dict:
        """
        Blokuje wątek i czeka na jedno połączenie.
        Zwraca dict z wynikiem uścisku.
        """
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(1)
        srv.settimeout(timeout)
        self._log(f"[KSH] Czekam na port {port} ...")
        try:
            conn, addr = srv.accept()
        finally:
            srv.close()
        self._log(f"[KSH] Połączenie od {addr[0]}:{addr[1]}")
        conn.settimeout(timeout)
        try:
            return self._run_protocol(conn, role="server")
        finally:
            conn.close()

    def connect(self,
                host:    str,
                port:    int   = 7700,
                timeout: float = TIMEOUT_SEC) -> dict:
        """
        Łączy się z węzłem-serwerem.
        Zwraca dict z wynikiem uścisku.
        """
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        self._log(f"[KSH] Łączę się z {host}:{port} ...")
        conn.connect((host, port))
        self._log(f"[KSH] Połączono")
        try:
            return self._run_protocol(conn, role="client")
        finally:
            conn.close()

    def serve_async(self,
                    host:     str   = "0.0.0.0",
                    port:     int   = 7700,
                    callback: Optional[Callable[[dict], None]] = None
                    ) -> threading.Thread:
        """
        Nieblokująca wersja serve().
        callback(result) wywoływany po zakończeniu uścisku.
        """
        def _run():
            try:
                result = self.serve(host, port)
            except Exception as e:
                result = {"status": "error", "error": str(e)}
            if callback:
                callback(result)

        t = threading.Thread(
            target=_run, daemon=True, name="karmazyn-handshake-server")
        t.start()
        return t

    # ── Protokół (4 fazy) ─────────────────────────────────────────────────────

    def _run_protocol(self, sock: socket.socket, role: str) -> dict:
        t0        = time.monotonic()
        deadline  = t0 + TIMEOUT_SEC   # [BUG FIX #4] globalny deadline
        is_server = (role == "server")

        # ── FAZA 0: Powitanie ─────────────────────────────────────────────────
        self._log("[KSH] Faza 0: powitanie")
        local_caps = self._build_caps()

        if is_server:
            _send_json(sock, local_caps)
            remote_caps = _recv_json(sock, deadline)
        else:
            remote_caps = _recv_json(sock, deadline)
            _send_json(sock, local_caps)

        # Wersja protokołu musi być zgodna
        if remote_caps.get("version") != PROTO_VERSION:
            raise RuntimeError(
                f"Niezgodna wersja protokołu: "
                f"lokalna={PROTO_VERSION}, zdalna={remote_caps.get('version')}")

        # Anti-replay: sprawdź timestamp zdalny — odrzuć stare sesje
        remote_ts = float(remote_caps.get("ts", 0))
        local_ts  = float(local_caps["ts"])
        if abs(local_ts - remote_ts) > REPLAY_WINDOW_SEC:
            raise RuntimeError(
                f"Anty-replay: różnica zegarów {abs(local_ts-remote_ts):.1f}s "
                f"> {REPLAY_WINDOW_SEC}s — możliwy replay attack lub desync NTP")

        # Sprawdź czy session_id nie był już widziany (RAM cache, nie persystentny)
        _session_id = remote_caps.get("session_id", "")
        if _session_id in _SEEN_SESSIONS:
            raise RuntimeError(f"Anty-replay: session_id '{_session_id}' już użyty")
        if _session_id:
            _SEEN_SESSIONS.add(_session_id)

        self._log(f"[KSH] Zdalny: {remote_caps.get('node_id','?')} "
                  f"({remote_caps.get('phi_atoms',0)} atomów)")

        # ── FAZA 1: Kryptografia ──────────────────────────────────────────────
        self._log("[KSH] Faza 1: negocjacja klucza")
        crypto_mode = self._select_crypto_mode(local_caps, remote_caps)
        shared_key  = self._negotiate_crypto(sock, is_server, crypto_mode, deadline)
        # [BUG FIX #8] PSK — wmieszany do klucza, peer bez PSK nie przejdzie MAC
        if self._psk:
            shared_key = hashlib.sha256(shared_key + self._psk).digest()
            self._crypto._key = shared_key
        self._log(f"[KSH] Klucz: {crypto_mode.upper()}, "
                  f"{len(shared_key)*8} bit"
                  + (" [PSK]" if self._psk else ""))

        # ── FAZA 2: Serializacja lokalnego phi-space ──────────────────────────
        self._log("[KSH] Faza 2: serializacja phi-space")
        local_atoms = _serialize_atoms(self.phi)
        local_blob  = json.dumps({
            "atoms":   local_atoms,
            "ts":      time.time(),
            "node_id": local_caps["node_id"],
        }, ensure_ascii=False).encode("utf-8")
        # Kompresja zlib przed szyfrowaniem — JSON atomów kompresuje się 3-10x
        compressed  = _compress(local_blob)
        encrypted   = self._crypto.encrypt(compressed)
        ratio       = len(local_blob) / max(1, len(compressed))
        self._log(f"[KSH] Serializacja: {len(local_atoms)} atomów → "
                  f"{len(local_blob)} B → {len(compressed)} B (zlib {ratio:.1f}x) → "
                  f"{len(encrypted)} B (szyfrowane)")

        # ── FAZA 3: Wymiana ───────────────────────────────────────────────────
        # Serwer wysyła pierwszy (klient już czeka na odbiór)
        self._log("[KSH] Faza 3: wymiana blobów")
        if is_server:
            _send_frame(sock, encrypted)
            remote_enc = _recv_frame(sock, deadline)
        else:
            remote_enc  = _recv_frame(sock, deadline)
            _send_frame(sock, encrypted)

        remote_raw   = self._crypto.decrypt(remote_enc)
        remote_blob  = _decompress(remote_raw)   # odwrotność kompresji
        remote_data  = json.loads(remote_blob.decode("utf-8", errors="replace"))
        remote_atoms = remote_data.get("atoms", [])
        self._log(f"[KSH] Odebrano: {len(remote_atoms)} atomów zdalnych")

        # ── FAZA 4: Scalenie + potwierdzenie MAC ──────────────────────────────
        self._log("[KSH] Faza 4: scalenie (union + conflict-by-T)")
        added, updated, skipped = _merge_atoms(self.phi, remote_atoms)

        # [BUG FIX #1]  — dodano age do kanonicznej reprezentacji
        # [BUG FIX #11] — pełny SHA256 nie truncated 64-bit
        # [BUG FIX #15] — sort_keys=True
        merged_atoms = _serialize_atoms(self.phi)
        canonical    = sorted(
            (a["id"], round(a["T"], 4), a["S"], a["E"], a["state"], a["age"])
            for a in merged_atoms
        )
        state_hash   = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

        # [BUG FIX #11] pełny HMAC-SHA256 — nie truncated 64-bit
        mac = _hmac.new(shared_key, state_hash.encode(), hashlib.sha256).hexdigest()

        if is_server:
            _send_json(sock, {"mac": mac, "n": len(merged_atoms)})
            peer = _recv_json(sock, deadline)
        else:
            peer = _recv_json(sock, deadline)
            _send_json(sock, {"mac": mac, "n": len(merged_atoms)})

        peer_mac_ok = (peer.get("mac") == mac)
        elapsed     = time.monotonic() - t0

        self._log(
            f"[KSH] Zakończono: +{added} nowych, ~{updated} zaktualizowanych, "
            f"={len(merged_atoms)} łącznie | "
            f"MAC: {'OK ✓' if peer_mac_ok else 'NIEZGODNY ✗'} | "
            f"{elapsed:.2f} s"
        )

        # sync: phi-space (RAM) only — BubbleVFS (.soul) future: karmazyn_gossip
        return {
            "status":         "ok",
            "role":           role,
            "crypto_mode":    crypto_mode,
            "remote_node":    remote_caps.get("node_id", "?"),
            "local_atoms":    len(local_atoms),
            "remote_atoms":   len(remote_atoms),
            "merged_total":   len(merged_atoms),
            "added":          added,
            "updated":        updated,
            "skipped":        skipped,
            "state_hash":     state_hash,
            "peer_mac_ok":    peer_mac_ok,
            "elapsed_s":      round(elapsed, 3),
            "bubbles_synced": False,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_caps(self) -> dict:
        """Capability advertisement — wysyłane w fazie 0."""
        crypto_list = []
        if _HSS_AVAILABLE:
            crypto_list.append("hss")
        if _CRYPTO_OK:
            crypto_list.append("ecdh")
        crypto_list.append("simple")   # zawsze dostępny

        return {
            "version":    PROTO_VERSION,
            "node_id":    _node_id(),
            "crypto":     crypto_list,
            "phi_atoms":  len(_serialize_atoms(self.phi)),
            "ts":         time.time(),          # anty-replay: timestamp sesji
            "session_id": secrets.token_hex(8), # anty-replay: unikalny ID sesji
        }

    def _select_crypto_mode(self, local: dict, remote: dict) -> str:
        """Wybierz najsilniejszy wspólny tryb (HSS > ECDH > simple)."""
        local_c  = set(local.get("crypto",  []))
        remote_c = set(remote.get("crypto", []))
        common   = local_c & remote_c
        for preferred in ("hss", "ecdh", "simple"):
            if preferred in common:
                return preferred
        raise RuntimeError(
            f"Brak wspólnego trybu krypto: lokalny={local_c} zdalny={remote_c}")

    def _negotiate_crypto(self,
                           sock:      socket.socket,
                           is_server: bool,
                           mode:      str,
                           deadline:  float = 0.0) -> bytes:
        """Dispatcher dla negocjacji klucza."""
        # Przekaż deadline do wszystkich metod crypto które wołają _recv_json
        if mode == "hss":
            if is_server:
                return self._crypto.negotiate_hss_responder(
                    sock, _recv_json(sock, deadline))
            return self._crypto.negotiate_hss_initiator(sock, deadline)

        if mode == "ecdh":
            if is_server:
                return self._crypto.negotiate_ecdh_responder(
                    sock, _recv_json(sock, deadline))
            return self._crypto.negotiate_ecdh_initiator(sock, deadline)

        # simple (PBKDF2)
        if is_server:
            return self._crypto.negotiate_simple_responder(
                sock, _recv_json(sock, deadline))
        return self._crypto.negotiate_simple_initiator(sock, deadline)


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _node_id() -> str:
    """
    [BUG FIX #9] Persystentny node_id w LOCALAPPDATA\\Cynober\\node_id.
    uuid.getnode() może się zmieniać (VM, kontener, random MAC).
    Plik zapewnia stabilność między restartami.
    """
    try:
        from cynober_paths import node_id_path, relocate_legacy

        relocate_legacy()
        id_path = str(node_id_path())
    except Exception:
        id_path = os.path.join(os.path.expanduser("~"), ".karmazyn_node_id")
    try:
        saved = open(id_path, encoding="utf-8").read().strip()
        if saved.startswith("node_") and len(saved) == 17:
            return saved
    except Exception:
        pass
    raw = socket.gethostname().encode("utf-8", errors="replace")
    try:
        import uuid
        raw += str(uuid.getnode()).encode()
    except Exception:
        pass
    node_id = "node_" + hashlib.sha256(raw).hexdigest()[:12]
    try:
        with open(id_path, "w", encoding="utf-8") as f:
            f.write(node_id)
    except Exception:
        pass
    return node_id


# ─────────────────────────────────────────────────────────────────────────────
# Komenda shella KarmazynOS
# ─────────────────────────────────────────────────────────────────────────────

def cmd_handshake(args,
                  runtime    = None,
                  term_state = None,
                  **_kw) -> str:
    """
    HANDSHAKE SERVE [port]       — czekaj na połączenie (blokuje worker thread)
    HANDSHAKE <host> [port]      — połącz z węzłem
    HANDSHAKE SERVE_BG [port]    — serwer w tle (nieblokujący)
    """
    if runtime is None:
        return "Brak runtime."

    def _out(msg: str) -> None:
        if term_state is not None:
            term_state.append(msg, (180, 220, 100))
        else:
            print(msg)

    if not args:
        return ("Uzycie:\n"
                "  HANDSHAKE SERVE [port]      — czekaj na polaczenie\n"
                "  HANDSHAKE <host> [port]     — polacz z weglem\n"
                "  HANDSHAKE SERVE_BG [port]   — serwer w tle")

    sub  = args[0].upper()
    # Ostatni argument numeryczny interpretuj jako port
    port = int(args[-1]) if len(args) > 1 and args[-1].isdigit() else 7700

    hs   = KarmazynHandshake(runtime, log_fn=_out,
                             psk=os.environb.get(b'KARM_PSK'))

    if sub == "SERVE":
        result = hs.serve(port=port)

    elif sub == "SERVE_BG":
        def _cb(r: dict) -> None:
            _out(f"[KSH] Zakonczone w tle: {r.get('status')} "
                 f"+{r.get('added',0)} atomow")
        hs.serve_async(port=port, callback=_cb)
        return f"[KSH] Serwer uruchomiony w tle na porcie {port}"

    else:
        host   = args[0]
        result = hs.connect(host=host, port=port)

    if result.get("status") != "ok":
        return f"[KSH] BLAD: {result.get('error', '?')}"

    lines = [
        "──────────────────────────────────────",
        f"  Uscisk:     {result['status'].upper()}",
        f"  Rola:       {result['role']}",
        f"  Krypto:     {result['crypto_mode'].upper()}",
        f"  Zdalny:     {result['remote_node']}",
        f"  Phi-space:  {result['merged_total']} atomow lacznie",
        f"  Dodane:     +{result['added']}  zaktualizowane: ~{result['updated']}",
        f"  Stan hash:  {result['state_hash']}",
        f"  MAC peer:   {'OK  ✓' if result['peer_mac_ok'] else 'BLAD ✗  (rozbieznosc stanu!)'}",
        f"  Czas:       {result['elapsed_s']} s",
        "  Bable:      nie syncowane (tylko phi-space RAM)",
        "──────────────────────────────────────",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Punkt wejścia CLI + Demo
# ─────────────────────────────────────────────────────────────────────────────

def _run_demo() -> None:
    """
    Dwa MockPhi w jednym procesie, połączone przez loopback 127.0.0.1:17700.
    Weryfikuje symetrię stanu po uścisku.
    """
    print("=" * 60)
    print("  KarmazynOS Symmetric Handshake — DEMO")
    print("=" * 60)

    # Mock PhiSpace — duck-typing
    class _MockAtom:
        def __init__(self, id, S="", E="", T=50.0):
            self.id    = id
            self.S     = S
            self.E     = E
            self.T     = T
            self.T_max = 100.0
            self.state = "WARM"
            self.age   = 0

    class _MockPhi:
        def __init__(self, atoms):
            self._atoms = list(atoms)
            self._index = {a.id: a for a in self._atoms}

        class _Matrix:
            def __init__(self, phi): self._phi = phi
            def atoms(self): return self._phi._atoms

        def __init__(self, atoms):        # noqa: F811
            self._atoms = list(atoms)
            self._index = {a.id: a for a in self._atoms}
            self.matrix = type("M", (), {
                "atoms": lambda s: self._atoms,
            })()

        def get_atom(self, id):
            return self._index.get(id)

        def create_atom(self, id, S="", E="", T=50.0):
            a = _MockAtom(id, S, E, T)
            self._atoms.append(a)
            self._index[id] = a
            return a

    # Węzeł A: 3 atomy (shell, bubble.alpha, program.logo)
    phi_a = _MockPhi([
        _MockAtom("shell.init",      S="sys", T=80.0),
        _MockAtom("bubble.alpha",    S="doc", T=65.0),
        _MockAtom("program.logo",    S="app", T=55.0),
    ])

    # Węzeł B: 3 atomy (shell.init [niższe T], bubble.beta, program.luneta)
    phi_b = _MockPhi([
        _MockAtom("shell.init",      S="sys", T=40.0),   # konflikt: A wygra
        _MockAtom("bubble.beta",     S="doc", T=70.0),
        _MockAtom("program.luneta",  S="app", T=60.0),
    ])

    PORT        = 17700
    result_srv  = {}
    result_cli  = {}

    def _server():
        hs = KarmazynHandshake(
            phi_a, log_fn=lambda m: print(f"  [A-serwer] {m}"))
        result_srv.update(hs.serve(host="127.0.0.1", port=PORT))

    t = threading.Thread(target=_server, daemon=True, name="demo-server")
    t.start()
    time.sleep(0.1)   # daj serwerowi czas na bind

    hs_b = KarmazynHandshake(
        phi_b, log_fn=lambda m: print(f"  [B-klient] {m}"))
    result_cli.update(hs_b.connect("127.0.0.1", port=PORT))
    t.join(timeout=10.0)

    print("\n" + "─" * 60)
    print("  WYNIK UŚCISKU")
    print("─" * 60)
    for k, v in result_cli.items():
        print(f"  {k:16}: {v}")

    ids_a = sorted(a.id for a in phi_a._atoms)
    ids_b = sorted(a.id for a in phi_b._atoms)
    print(f"\n  Phi A atomy: {ids_a}")
    print(f"  Phi B atomy: {ids_b}")
    print(f"  Symetria:    {'OK ✓' if ids_a == ids_b else 'BŁĄD ✗'}")

    # Sprawdź czy konflikt shell.init rozstrzygnięty poprawnie (T_A=80 > T_B=40)
    atom_b_shell = phi_b.get_atom("shell.init")
    if atom_b_shell:
        print(f"  Konflikt shell.init: T={atom_b_shell.T:.1f} "
              f"(oczekiwane 80.0) → {'OK ✓' if atom_b_shell.T == 80.0 else 'BŁĄD ✗'}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="KarmazynOS Symmetric Handshake v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Przykłady:
  python3 karmazyn_handshake.py --demo
  python3 karmazyn_handshake.py --serve --port 7700
  python3 karmazyn_handshake.py --connect 192.168.1.42 --port 7700
""")
    ap.add_argument("--demo",    action="store_true", help="Uruchom demo (loopback)")
    ap.add_argument("--serve",   action="store_true", help="Czekaj na połączenie")
    ap.add_argument("--connect", metavar="HOST",      help="Połącz z hostem")
    ap.add_argument("--port",    type=int, default=7700)
    opt = ap.parse_args()

    if opt.demo:
        _run_demo()
    elif opt.serve:
        if not _PHI_AVAILABLE:
            print("Brak karmazyn_phi — uruchom w środowisku KarmazynOS")
        else:
            phi = PhiSpace()
            hs  = KarmazynHandshake(phi)
            print(hs.serve(port=opt.port))
    elif opt.connect:
        if not _PHI_AVAILABLE:
            print("Brak karmazyn_phi")
        else:
            phi = PhiSpace()
            hs  = KarmazynHandshake(phi)
            print(hs.connect(opt.connect, port=opt.port))
    else:
        ap.print_help()