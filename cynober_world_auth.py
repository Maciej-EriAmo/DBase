#!/usr/bin/env python3
"""
cynober_world_auth.py — auth per użytkownik na trwałych światach (v7.2)
=======================================================================
Role: reader < writer < admin. Konfiguracja: {worlds_dir}/auth.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

ROLE_READER = "reader"
ROLE_WRITER = "writer"
ROLE_ADMIN = "admin"
ROLES = (ROLE_READER, ROLE_WRITER, ROLE_ADMIN)
ROLE_RANK = {ROLE_READER: 1, ROLE_WRITER: 2, ROLE_ADMIN: 3}

_LOGIN_RE = re.compile(
    r'^ZALOGUJ\s+"([^"]+)"\s+TOKEN\s+"([^"]+)"$',
    re.IGNORECASE,
)
_GRANT_RE = re.compile(
    r'^NADAJ\s+"([^"]+)"\s+ROLĘ\s+"([^"]+)"\s+W\s+ŚWIECIE\s+"([^"]+)"$',
    re.IGNORECASE,
)
_REVOKE_RE = re.compile(
    r'^ODEBIERZ\s+"([^"]+)"\s+Z\s+ŚWIATA\s+"([^"]+)"$',
    re.IGNORECASE,
)
_LIST_ACL_WORLD_RE = re.compile(
    r'^LISTA\s+UPRAWNIEŃ\s+ŚWIATA\s+"([^"]+)"$',
    re.IGNORECASE,
)


def _normalize_role(role: str) -> str:
    r = role.strip().lower()
    if r not in ROLES:
        raise ValueError(f"Nieznana rola '{role}'. Dozwolone: reader, writer, admin.")
    return r


def _hash_token(salt: str, token: str) -> str:
    payload = f"{salt}:{token}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class WorldAuthStore:
    def __init__(self, base_dir: Path):
        self._base = Path(base_dir)
        self._path = self._base / "auth.json"
        self._audit_path = self._base / "audit.log"
        self._lock = threading.RLock()
        self._data = self._load()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return bool(self._data.get("enabled"))

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict:
        if not self._path.is_file():
            return {"enabled": False, "users": {}, "acl": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"enabled": False, "users": {}, "acl": {}}
        if not isinstance(data, dict):
            return {"enabled": False, "users": {}, "acl": {}}
        data.setdefault("users", {})
        data.setdefault("acl", {})
        return data

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def verify_login(self, user: str, token: str) -> bool:
        with self._lock:
            if not self._data.get("enabled"):
                return True
            users = self._data.get("users", {})
            entry = users.get(user)
            if not entry:
                return False
            salt = self._data.get("salt", "")
            return entry.get("token_hash") == _hash_token(salt, token)

    def role_for(self, user: Optional[str], world: Optional[str]) -> Optional[str]:
        if not user:
            return None
        with self._lock:
            if not self._data.get("enabled"):
                return ROLE_ADMIN
            acl = self._data.get("acl", {})
            if world:
                role = acl.get(world, {}).get(user)
                if role:
                    return _normalize_role(role)
            role = acl.get("*", {}).get(user)
            if role:
                return _normalize_role(role)
            return None

    def worlds_for(self, user: Optional[str]) -> Optional[Set[str]]:
        """None = wszystkie (auth wyłączone); set = dostępne światy."""
        if not self.enabled:
            return None
        if not user:
            return set()
        with self._lock:
            acl = self._data.get("acl", {})
            out: Set[str] = set()
            for world, grants in acl.items():
                if world == "*":
                    continue
                if user in grants:
                    out.add(world)
            return out

    def has_min_role(self, user: Optional[str], world: Optional[str], minimum: str) -> bool:
        role = self.role_for(user, world)
        if role is None:
            return False
        return ROLE_RANK[role] >= ROLE_RANK[minimum]

    def grant(self, actor: str, target: str, role: str, world: str) -> None:
        role = _normalize_role(role)
        with self._lock:
            if not self.has_min_role(actor, world, ROLE_ADMIN):
                if not self.has_min_role(actor, "*", ROLE_ADMIN):
                    raise PermissionError("Wymagana rola admin na świecie lub globalnie.")
            self._data.setdefault("acl", {}).setdefault(world, {})[target] = role
            self._save()

    def revoke(self, actor: str, target: str, world: str) -> None:
        with self._lock:
            if not self.has_min_role(actor, world, ROLE_ADMIN):
                if not self.has_min_role(actor, "*", ROLE_ADMIN):
                    raise PermissionError("Wymagana rola admin na świecie lub globalnie.")
            grants = self._data.get("acl", {}).get(world, {})
            if target not in grants:
                raise ValueError(f"Użytkownik '{target}' nie ma uprawnień w świecie '{world}'.")
            del grants[target]
            self._save()

    def list_acl(self, world: Optional[str] = None) -> List[dict]:
        with self._lock:
            acl = self._data.get("acl", {})
            if world:
                grants = acl.get(world, {})
                return [{"user": u, "role": r, "world": world} for u, r in sorted(grants.items())]
            rows: List[dict] = []
            for w, grants in sorted(acl.items()):
                for u, r in sorted(grants.items()):
                    rows.append({"user": u, "role": r, "world": w})
            return rows

    def audit(
        self,
        *,
        user: Optional[str],
        world: Optional[str],
        action: str,
        query: str,
        allowed: bool,
    ) -> None:
        if not self.enabled:
            return
        entry = {
            "ts": time.time(),
            "user": user or "anonymous",
            "world": world,
            "action": action,
            "allowed": allowed,
            "query": query[:200],
        }
        try:
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def write_config_for_tests(
        self,
        users: Dict[str, str],
        acl: Dict[str, Dict[str, str]],
        *,
        enabled: bool = True,
    ) -> None:
        """users: login -> token; acl: world -> {user: role}"""
        with self._lock:
            salt = secrets.token_hex(16)
            self._data = {
                "enabled": enabled,
                "salt": salt,
                "users": {
                    u: {"token_hash": _hash_token(salt, tok)}
                    for u, tok in users.items()
                },
                "acl": acl,
            }
            self._base.mkdir(parents=True, exist_ok=True)
            self._save()


_auth_stores: Dict[str, WorldAuthStore] = {}
_auth_lock = threading.Lock()


def get_auth_store(base_dir: Optional[Path] = None) -> WorldAuthStore:
    if base_dir is None:
        from cynober_worlds import worlds_dir
        base_dir = worlds_dir()
    key = str(Path(base_dir).resolve())
    with _auth_lock:
        store = _auth_stores.get(key)
        if store is None:
            store = WorldAuthStore(Path(base_dir))
            _auth_stores[key] = store
        return store


def reset_auth_store_for_tests(base_dir: Path) -> WorldAuthStore:
    key = str(Path(base_dir).resolve())
    with _auth_lock:
        store = WorldAuthStore(base_dir)
        _auth_stores[key] = store
        return store


def is_read_only_query(upper: str) -> bool:
    if upper in ("STATYSTYKI", "OPISZ BAZĘ", "LISTA ŚWIATÓW", "KTO JESTEM", "LISTA UPRAWNIEŃ"):
        return True
    if upper.startswith(("POKAŻ", "ZNAJDŹ", "WYPISZ", "POLICZ", "SZUKAJ", "WYJAŚNIJ", "EXPLAIN")):
        return True
    if upper.startswith("LISTA UPRAWNIEŃ"):
        return True
    return False


def is_world_admin_command(upper: str) -> bool:
    return bool(
        upper.startswith("USUŃ ŚWIAT")
        or upper.startswith("UTWÓRZ ŚWIAT")
        or upper.startswith("NADAJ ")
        or upper.startswith("ODEBIERZ ")
        or upper.startswith("LISTA UPRAWNIEŃ")
    )