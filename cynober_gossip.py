"""
cynober_gossip.py — synchronizacja między węzłami po RPC (v7.7 + v8.1 SOUL)
=============================================================================
Gossip nad istniejącym tunelu Cynober-Secure (bez REST).

  PHI  (v7.7)  — atomy phi-space: id, S, E, T (bez bąbli)
  SOUL (v8.1)  — BubbleVFS-lite: atomy + bąble + bindings (+ meta v / data)

Dalsza ewolucja: pełne BubbleVFS (.soul pliki, Proca COLD, shardy) — ten
sam format transportowy (base64 JSON v1), rozbudowa payloadu.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

TOMB_THRESHOLD = 0.01
MERGE_TEMP_BIAS = 0.001
SOUL_FORMAT_V = 1

# ── PHI ───────────────────────────────────────────────────────────────────────
_EXPORT_PHI_RE = re.compile(r"^GOSSIP\s+EKSPORT\s+PHI$", re.IGNORECASE)
_IMPORT_PHI_RE = re.compile(
    r'^GOSSIP\s+IMPORT\s+PHI\s+DANE\s+"([A-Za-z0-9+/=]+)"$',
    re.IGNORECASE,
)
_SYNC_PHI_RE = re.compile(r'^GOSSIP\s+SYNC\s+PHI\s+Z\s+"([^"]+)"$', re.IGNORECASE)

# ── SOUL (v8.1) ───────────────────────────────────────────────────────────────
_EXPORT_SOUL_RE = re.compile(r"^GOSSIP\s+EKSPORT\s+SOUL$", re.IGNORECASE)
_IMPORT_SOUL_RE = re.compile(
    r'^GOSSIP\s+IMPORT\s+SOUL\s+DANE\s+"([A-Za-z0-9+/=]+)"$',
    re.IGNORECASE,
)
_SYNC_SOUL_RE = re.compile(r'^GOSSIP\s+SYNC\s+SOUL\s+Z\s+"([^"]+)"$', re.IGNORECASE)


def is_gossip_query(stripped: str, upper: str) -> bool:
    return (
        _EXPORT_PHI_RE.match(stripped) is not None
        or _IMPORT_PHI_RE.match(stripped) is not None
        or _SYNC_PHI_RE.match(stripped) is not None
        or _EXPORT_SOUL_RE.match(stripped) is not None
        or _IMPORT_SOUL_RE.match(stripped) is not None
        or _SYNC_SOUL_RE.match(stripped) is not None
    )


def _atom_age(a: Any) -> int:
    age_raw = getattr(a, "age", 0)
    return int(age_raw()) if callable(age_raw) else int(age_raw or 0)


def _safe_jsonable(value: Any) -> Tuple[bool, Any]:
    """True + value jeśli JSON-serializable; inaczej False + None."""
    try:
        json.dumps(value, ensure_ascii=False)
        return True, value
    except (TypeError, ValueError, OverflowError):
        return False, None


def serialize_phi_atoms(store: Any, *, node_id: str = "local") -> List[dict]:
    """Serializuj atomy ze Store (phi-space) — bez __bubble__ syntez."""
    result: List[dict] = []
    for a in store.atoms():
        if str(getattr(a, "S", "")) == "__bubble__":
            continue
        T = float(getattr(a, "T", 0))
        if not math.isfinite(T):
            T = 0.0
        state = str(getattr(a, "state", "WARM"))
        if T < TOMB_THRESHOLD or state == "TOMB":
            continue
        aid = str(getattr(a, "id", getattr(a, "S", "")))
        result.append({
            "id": aid,
            "S": str(getattr(a, "S", "")),
            "E": str(getattr(a, "E", "")),
            "T": T,
            "T_max": float(getattr(a, "T_max", 100.0)),
            "state": state,
            "age": _atom_age(a),
            "_node_id": node_id,
        })
    return result


def _upsert_atom(store: Any, rec: dict) -> str:
    """
    Wstaw lub zaktualizuj atom z ZACHOWANYM id.
    Zwraca: 'added' | 'updated' | 'skipped'
    """
    atom_id = str(rec.get("id", "") or "")
    T_remote = float(rec.get("T", 0))
    if not atom_id or T_remote < TOMB_THRESHOLD:
        return "skipped"

    existing = store.get_atom(atom_id) if callable(getattr(store, "get_atom", None)) else None
    meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}

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
        if not r_win:
            return "skipped"
        existing.T = T_remote
        if rec.get("S"):
            existing.S = rec["S"]
        if rec.get("E") is not None:
            existing.E = rec["E"]
        _apply_meta(existing, meta)
        if hasattr(existing, "_update_state"):
            existing._update_state()
        return "updated"

    # Nowy atom — create_atom z jawnym id (nie atom_new: gubi id)
    S = rec.get("S", atom_id) or atom_id
    E = rec.get("E", "") or ""
    value = meta.get("v", None) if meta else None
    if callable(getattr(store, "create_atom", None)):
        try:
            store.create_atom(atom_id, S=S, E=E, T=T_remote, value=value)
        except ValueError:
            # kolizja id między check a create — spróbuj update
            existing = store.get_atom(atom_id)
            if existing is None:
                return "skipped"
            existing.T = T_remote
            _apply_meta(existing, meta)
            return "updated"
    else:
        # Legacy store bez create_atom
        a = store.atom_new(S=S, E=E, T=T_remote, value=value)
        # nie da się zmienić id — oznacz w meta skąd przyszedł
        a.metadata["_gossip_remote_id"] = atom_id
        _apply_meta(a, meta)
        return "added"

    atom = store.get_atom(atom_id)
    if atom is not None:
        _apply_meta(atom, meta)
        if hasattr(store, "sync_id_counter"):
            store.sync_id_counter()
    return "added"


def _apply_meta(atom: Any, meta: dict) -> None:
    if not meta or not hasattr(atom, "metadata"):
        return
    if "v" in meta:
        atom.metadata["v"] = meta["v"]
    data_b64 = meta.get("data_b64")
    if isinstance(data_b64, str) and data_b64:
        try:
            atom.metadata["data"] = base64.b64decode(data_b64.encode("ascii"))
        except Exception:
            pass


def merge_phi_atoms(store: Any, remote_atoms: List[dict]) -> Tuple[int, int, int]:
    """Scal zdalne atomy do Store — wyższe T wygrywa; id zdalne są zachowane."""
    added = updated = skipped = 0
    for rec in remote_atoms:
        outcome = _upsert_atom(store, rec)
        if outcome == "added":
            added += 1
        elif outcome == "updated":
            updated += 1
        else:
            skipped += 1
    return added, updated, skipped


def export_phi_payload(store: Any, *, node_id: str = "local") -> str:
    atoms = serialize_phi_atoms(store, node_id=node_id)
    raw = json.dumps({"atoms": atoms, "v": 1, "kind": "phi"}, ensure_ascii=False).encode("utf-8")
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


# ── SOUL (BubbleVFS-lite) ─────────────────────────────────────────────────────

# Faza 0 media: domyślnie nie dmuchaj dużych blobów w JSON gossip.
# None w include_blobs → próg; False → nigdy; True → zawsze.
SOUL_MAX_INLINE_BLOB = 64 * 1024  # 64 KiB


def _serialize_atom_soul(
    a: Any,
    *,
    node_id: str,
    include_blobs: bool | None = None,
    max_inline_blob: int = SOUL_MAX_INLINE_BLOB,
) -> Optional[dict]:
    if str(getattr(a, "S", "")) == "__bubble__":
        return None
    T = float(getattr(a, "T", 0))
    if not math.isfinite(T):
        T = 0.0
    state = str(getattr(a, "state", "WARM"))
    if T < TOMB_THRESHOLD or state == "TOMB":
        return None
    aid = str(getattr(a, "id", ""))
    meta: Dict[str, Any] = {}
    md = getattr(a, "metadata", None) or {}
    if "v" in md:
        ok, val = _safe_jsonable(md["v"])
        if ok:
            meta["v"] = val
    if "mime" in md and md["mime"] is not None:
        meta["mime"] = str(md["mime"])
    data = md.get("data")
    if isinstance(data, (bytes, bytearray)) and data:
        raw = bytes(data)
        size = len(raw)
        allow = False
        if include_blobs is True:
            allow = True
        elif include_blobs is False:
            allow = False
        else:
            # domyślnie: tylko małe (≤ próg)
            allow = size <= int(max_inline_blob)
        if allow:
            meta["data_b64"] = base64.b64encode(raw).decode("ascii")
        else:
            # ref dla przyszłego GOSSIP FETCH MEDIA (Faza 6)
            cas = md.get("_cas") or hashlib.sha256(raw).digest()[:12].hex()
            meta["media_ref"] = {
                "size": size,
                "mime": str(md.get("mime") or "application/octet-stream"),
                "cas": str(cas),
            }
    return {
        "id": aid,
        "S": str(getattr(a, "S", "")),
        "E": str(getattr(a, "E", "")),
        "T": T,
        "T_max": float(getattr(a, "T_max", 100.0)),
        "state": state,
        "age": _atom_age(a),
        "meta": meta,
        "_node_id": node_id,
    }


def serialize_soul(
    store: Any,
    *,
    node_id: str = "local",
    api: Any = None,
    include_blobs: bool | None = None,
    max_inline_blob: int = SOUL_MAX_INLINE_BLOB,
) -> dict:
    """
    Snapshot SOUL: atomy + bąble z bindings.
    api: opcjonalnie KarminEngine.api — używa _bubble_index (kanoniczne etykiety).

    include_blobs:
      None  — domyślnie: data_b64 tylko gdy len(data) ≤ max_inline_blob (64 KiB)
      False — nigdy nie wkładaj data_b64 (tylko media_ref)
      True  — zawsze data_b64 (legacy / jawne pełne SOUL)
    """
    atoms: List[dict] = []
    for a in store.atoms():
        rec = _serialize_atom_soul(
            a,
            node_id=node_id,
            include_blobs=include_blobs,
            max_inline_blob=max_inline_blob,
        )
        if rec is not None:
            atoms.append(rec)

    roots = {id(b) for b in getattr(store, "roots", [])}
    bubbles: List[dict] = []
    if api is not None and hasattr(api, "_bubble_index"):
        for label, b in api._bubble_index.items():
            bubbles.append({
                "label": str(label),
                "bindings": dict(getattr(b, "bindings", {}) or {}),
                "root": id(b) in roots,
            })
    else:
        for i, b in enumerate(getattr(store, "bubbles", []) or []):
            label = getattr(b, "label", None) or f"_anon_{i}"
            bubbles.append({
                "label": str(label),
                "bindings": dict(getattr(b, "bindings", {}) or {}),
                "root": id(b) in roots,
            })

    return {
        "v": SOUL_FORMAT_V,
        "kind": "soul",
        "node_id": node_id,
        "atoms": atoms,
        "bubbles": bubbles,
    }


def export_soul_payload(
    store: Any,
    *,
    node_id: str = "local",
    api: Any = None,
) -> str:
    doc = serialize_soul(store, node_id=node_id, api=api)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def merge_soul(
    store: Any,
    doc: dict,
    *,
    api: Any = None,
) -> Dict[str, int]:
    """Scal SOUL: atomy (T wygrywa, id zachowane) + bąble/bindings."""
    if not isinstance(doc, dict) or doc.get("kind") not in (None, "soul"):
        # None kind: tolerancja; kind "phi" nie powinien tu wchodzić
        if doc.get("kind") == "phi":
            raise ValueError("GOSSIP SOUL: otrzymano payload PHI — użyj IMPORT PHI")
    atoms = doc.get("atoms", [])
    bubbles = doc.get("bubbles", [])
    if not isinstance(atoms, list) or not isinstance(bubbles, list):
        raise ValueError("GOSSIP SOUL: niepoprawny format")

    a_add = a_upd = a_skip = 0
    for rec in atoms:
        if not isinstance(rec, dict):
            a_skip += 1
            continue
        outcome = _upsert_atom(store, rec)
        if outcome == "added":
            a_add += 1
        elif outcome == "updated":
            a_upd += 1
        else:
            a_skip += 1

    b_add = b_upd = b_skip = 0
    index = getattr(api, "_bubble_index", None) if api is not None else None

    for brec in bubbles:
        if not isinstance(brec, dict):
            b_skip += 1
            continue
        label = str(brec.get("label") or "")
        if not label:
            b_skip += 1
            continue
        bindings = brec.get("bindings") or {}
        if not isinstance(bindings, dict):
            bindings = {}

        bubble = None
        created = False
        if index is not None:
            bubble = index.get(label)
            if bubble is None:
                if api is not None and hasattr(api, "create_bubble"):
                    try:
                        api.create_bubble(label)
                    except Exception:
                        bubble = store.bubble_new(label=label)
                        index[label] = bubble
                        created = True
                    else:
                        bubble = index.get(label)
                        created = bubble is not None
                else:
                    bubble = store.bubble_new(label=label)
                    index[label] = bubble
                    created = True
        else:
            if callable(getattr(store, "get_bubble", None)):
                bubble = store.get_bubble(label)
            if bubble is None:
                bubble = store.bubble_new(label=label)
                created = True

        if bubble is None:
            b_skip += 1
            continue

        if brec.get("root") and callable(getattr(store, "set_root", None)):
            store.set_root(bubble)

        changed = False
        for name, aid in bindings.items():
            aid = str(aid) if aid is not None else ""
            if not name or not aid:
                continue
            atom = store.get_atom(aid) if callable(getattr(store, "get_atom", None)) else None
            if atom is None:
                continue
            local_aid = (bubble.bindings or {}).get(name)
            if local_aid == aid:
                continue
            if local_aid is None:
                try:
                    bubble.bind(name, atom)
                except Exception:
                    bubble.bindings[name] = aid
                changed = True
            else:
                # konflikt: wybierz wiązanie do atomu o wyższym T
                local_atom = store.get_atom(local_aid)
                T_local = float(getattr(local_atom, "T", 0)) if local_atom else -1.0
                T_remote = float(getattr(atom, "T", 0))
                if T_remote > T_local + MERGE_TEMP_BIAS:
                    try:
                        bubble.bind(name, atom)
                    except Exception:
                        bubble.bindings[name] = aid
                    changed = True

        if created:
            b_add += 1
        elif changed:
            b_upd += 1
        else:
            b_skip += 1

    if api is not None and hasattr(api, "prune_dead_bindings"):
        try:
            api.prune_dead_bindings()
        except Exception:
            pass
    if api is not None:
        try:
            from cynober_worlds import rebuild_all_indexes
            rebuild_all_indexes(api)
        except Exception:
            pass

    return {
        "atoms_added": a_add,
        "atoms_updated": a_upd,
        "atoms_skipped": a_skip,
        "bubbles_added": b_add,
        "bubbles_updated": b_upd,
        "bubbles_skipped": b_skip,
    }


def import_soul_payload(
    store: Any,
    b64_data: str,
    *,
    api: Any = None,
) -> Dict[str, Any]:
    raw = base64.b64decode(b64_data.encode("ascii"))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GOSSIP SOUL: oczekiwano obiektu JSON")
    stats = merge_soul(store, data, api=api)
    return {
        "status": "ok",
        "action": "GOSSIP_IMPORT_SOUL",
        **stats,
        "remote_atoms": len(data.get("atoms") or []),
        "remote_bubbles": len(data.get("bubbles") or []),
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
    api: Any = None,
) -> List[dict] | None:
    """Wykonaj GOSSIP EKSPORT/IMPORT/SYNC (PHI lub SOUL) na aktywnym Store."""
    # ── PHI ────────────────────────────────────────────────────────────────
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
            return [import_phi_payload(store, m.group(1))]
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

    # ── SOUL ───────────────────────────────────────────────────────────────
    m = _EXPORT_SOUL_RE.match(stripped)
    if m:
        payload = export_soul_payload(store, node_id=node_id, api=api)
        doc = serialize_soul(store, node_id=node_id, api=api)
        return [{
            "status": "ok",
            "action": "GOSSIP_EXPORT_SOUL",
            "data": payload,
            "atom_count": len(doc["atoms"]),
            "bubble_count": len(doc["bubbles"]),
            "format_v": SOUL_FORMAT_V,
        }]

    m = _IMPORT_SOUL_RE.match(stripped)
    if m:
        try:
            return [import_soul_payload(store, m.group(1), api=api)]
        except (ValueError, json.JSONDecodeError) as e:
            return [{"status": "error", "action": "GOSSIP_IMPORT_SOUL", "message": str(e)}]

    m = _SYNC_SOUL_RE.match(stripped)
    if m:
        if peers is None:
            return [{
                "status": "error",
                "action": "GOSSIP_SYNC_SOUL",
                "message": "Brak rejestru węzłów (peers.json)",
            }]
        peer = m.group(1)
        try:
            remote = gossip_peer_query(peers, peer, "GOSSIP EKSPORT SOUL")
            payload = remote.get("data") or ""
            if not payload:
                return [{
                    "status": "error",
                    "action": "GOSSIP_SYNC_SOUL",
                    "message": "Peer nie zwrócił danych soul",
                }]
            stats = import_soul_payload(store, payload, api=api)
            stats["action"] = "GOSSIP_SYNC_SOUL"
            stats["peer"] = peer
            return [stats]
        except Exception as e:
            return [{"status": "error", "action": "GOSSIP_SYNC_SOUL", "message": str(e)}]

    return None
