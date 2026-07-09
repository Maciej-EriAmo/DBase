"""
cynober_gossip.py — synchronizacja phi-space między węzłami (v7.7)
================================================================
Gossip nad istniejącym tunelu RPC (bez REST). BubbleVFS (.soul) — przyszłość.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from typing import Any, Dict, List, Tuple

TOMB_THRESHOLD = 0.01
MERGE_TEMP_BIAS = 0.001

_EXPORT_PHI_RE = re.compile(r"^GOSSIP\s+EKSPORT\s+PHI$", re.IGNORECASE)
_IMPORT_PHI_RE = re.compile(
    r'^GOSSIP\s+IMPORT\s+PHI\s+DANE\s+"([A-Za-z0-9+/=]+)"$',
    re.IGNORECASE,
)
_SYNC_PHI_RE = re.compile(r'^GOSSIP\s+SYNC\s+PHI\s+Z\s+"([^"]+)"$', re.IGNORECASE)


def is_gossip_query(stripped: str, upper: str) -> bool:
    return (
        _EXPORT_PHI_RE.match(stripped) is not None
        or _IMPORT_PHI_RE.match(stripped) is not None
        or _SYNC_PHI_RE.match(stripped) is not None
    )


def serialize_phi_atoms(store: Any, *, node_id: str = "local") -> List[dict]:
    """Serializuj atomy ze Store (karmazyn_substrate) do listy dict."""
    atoms = store.atoms()
    result: List[dict] = []
    for a in atoms:
        T = float(getattr(a, "T", 0))
        if not math.isfinite(T):
            T = 0.0
        state = str(getattr(a, "state", "WARM"))
        if T < TOMB_THRESHOLD or state == "TOMB":
            continue
        aid = str(getattr(a, "id", getattr(a, "S", "")))
        age_raw = getattr(a, "age", 0)
        age = int(age_raw()) if callable(age_raw) else int(age_raw or 0)
        result.append({
            "id": aid,
            "S": str(getattr(a, "S", "")),
            "E": str(getattr(a, "E", "")),
            "T": T,
            "T_max": float(getattr(a, "T_max", 100.0)),
            "state": state,
            "age": age,
            "_node_id": node_id,
        })
    return result


def merge_phi_atoms(store: Any, remote_atoms: List[dict]) -> Tuple[int, int, int]:
    """Scal zdalne atomy do Store — wyższe T wygrywa (jak KSH handshake)."""
    added = updated = skipped = 0
    for rec in remote_atoms:
        atom_id = rec.get("id", "")
        T_remote = float(rec.get("T", 0))
        if not atom_id or T_remote < TOMB_THRESHOLD:
            skipped += 1
            continue
        existing = store.get_atom(atom_id)
        if existing is not None:
            T_local = float(getattr(existing, "T", 0))
            if T_remote > T_local + MERGE_TEMP_BIAS:
                r_win = True
            elif abs(T_remote - T_local) <= MERGE_TEMP_BIAS:
                _r = {k: rec.get(k) for k in ("id", "S", "E", "T")}
                _l = {
                    "id": atom_id,
                    "S": str(getattr(existing, "S", "")),
                    "E": str(getattr(existing, "E", "")),
                    "T": T_local,
                }
                r_win = (
                    hashlib.sha256(json.dumps(_r, sort_keys=True).encode()).hexdigest()
                    > hashlib.sha256(json.dumps(_l, sort_keys=True).encode()).hexdigest()
                )
            else:
                r_win = False
            if r_win:
                existing.T = T_remote
                if rec.get("S"):
                    existing.S = rec["S"]
                if rec.get("E"):
                    existing.E = rec["E"]
                updated += 1
            else:
                skipped += 1
        else:
            store.atom_new(
                S=rec.get("S", atom_id),
                E=rec.get("E", ""),
                T=T_remote,
            )
            added += 1
    return added, updated, skipped


def export_phi_payload(store: Any, *, node_id: str = "local") -> str:
    atoms = serialize_phi_atoms(store, node_id=node_id)
    raw = json.dumps({"atoms": atoms, "v": 1}, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def import_phi_payload(store: Any, b64_data: str) -> Dict[str, Any]:
    raw = base64.b64decode(b64_data.encode("ascii"))
    data = json.loads(raw.decode("utf-8"))
    atoms = data.get("atoms", [])
    if not isinstance(atoms, list):
        raise ValueError("GOSSIP: niepoprawny format atoms")
    added, updated, skipped = merge_phi_atoms(store, atoms)
    return {
        "status": "ok",
        "action": "GOSSIP_IMPORT_PHI",
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "remote_count": len(atoms),
    }


def gossip_peer_query(peers: Any, peer_name: str, query: str) -> dict:
    """Zapytanie RPC do zarejestrowanego węzła (peers.json)."""
    from cynober_replicate import _login_peer, _peer_client

    peer = peers.get(peer_name)
    client = _peer_client(peer)
    client.connect()
    try:
        _login_peer(client, peer)
        resp = client.query(query)
        row = resp.get("results", [{}])[0]
        if row.get("status") != "ok":
            raise RuntimeError(row.get("message", "Błąd zapytania na węźle"))
        return row
    finally:
        client.close()


def try_gossip_command(
    stripped: str,
    *,
    store: Any,
    node_id: str,
    peers: Any = None,
) -> List[dict] | None:
    """Wykonaj GOSSIP EKSPORT / IMPORT / SYNC na aktywnym Store."""
    m = _EXPORT_PHI_RE.match(stripped)
    if m:
        payload = export_phi_payload(store, node_id=node_id)
        return [{
            "status": "ok",
            "action": "GOSSIP_EXPORT_PHI",
            "data": payload,
            "atom_count": len(serialize_phi_atoms(store, node_id=node_id)),
        }]

    m = _IMPORT_PHI_RE.match(stripped)
    if m:
        try:
            stats = import_phi_payload(store, m.group(1))
            return [stats]
        except (ValueError, json.JSONDecodeError) as e:
            return [{"status": "error", "action": "GOSSIP_IMPORT_PHI", "message": str(e)}]

    m = _SYNC_PHI_RE.match(stripped)
    if m:
        if peers is None:
            return [{
                "status": "error",
                "action": "GOSSIP_SYNC_PHI",
                "message": "Brak rejestru węzłów (peers.json)",
            }]
        peer = m.group(1)
        try:
            remote = gossip_peer_query(peers, peer, "GOSSIP EKSPORT PHI")
            payload = remote.get("data") or ""
            if not payload:
                return [{
                    "status": "error",
                    "action": "GOSSIP_SYNC_PHI",
                    "message": "Peer nie zwrócił danych phi",
                }]
            stats = import_phi_payload(store, payload)
            stats["action"] = "GOSSIP_SYNC_PHI"
            stats["peer"] = peer
            return [stats]
        except Exception as e:
            return [{"status": "error", "action": "GOSSIP_SYNC_PHI", "message": str(e)}]

    return None