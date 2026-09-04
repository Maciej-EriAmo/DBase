#!/usr/bin/env python3
"""
game_store.py — adapter pamięci gry nad KarminEngine / serwerem Cynober RPC
============================================================================
Cienka warstwa nad KarminQL: NPC, questy, graf relacji, JSON, SZUKAJ, TICK.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Protocol, Union

import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine
from cynober_rpc import parse_response_payload


class GameStoreError(RuntimeError):
    pass


class _Backend(Protocol):
    def execute(self, script: str, *, strict: bool = False) -> List[dict]: ...
    def close(self) -> None: ...


def _last(results: List[dict]) -> dict:
    return results[-1] if results else {}


def _prefer_error_row(results: List[dict]) -> Optional[dict]:
    """Serwer (strict=False) często zwraca ROLLBACK, potem właściwy błąd — bierz ten drugi."""
    errors = [r for r in results if r.get("status") == "error"]
    if not errors:
        return None
    for r in errors:
        if r.get("action") != "ROLLBACK":
            return r
    return errors[0]


def _esc(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


class LocalBackend:
    """Silnik in-process (testy, dev bez serwera)."""

    def __init__(self, engine: Optional[KarminEngine] = None):
        self.engine = engine or KarminEngine(kernel.Store(thermal=True))

    def execute(self, script: str, *, strict: bool = False) -> List[dict]:
        line = script.strip()
        upper = line.upper()
        if upper == "STATYSTYKI":
            return [self._stats_row()]
        if upper.startswith("TICK"):
            parts = upper.split()
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            self.engine.api.store.settle(n)
            return [{"status": "ok", "action": "TICK", "cycles": n}]
        return self.engine.execute(script, strict=strict)

    def _stats_row(self) -> dict:
        stats = self.engine.api.store.stats()
        return {
            "status": "ok",
            "action": "STATS",
            "data": {
                "total_atoms": stats["total"],
                "hot": stats["hot"],
                "cold": stats["cold"],
                "reaped": stats["reaped"],
                "bubbles": len(self.engine.api._bubble_index),
                "session_label": "local",
                "session_isolated": False,
            },
        }

    def close(self) -> None:
        pass


class RpcBackend:
    """Jedna sesja RPC = izolowany Store na serwerze v7.0."""

    def __init__(self, client: Any):
        self._client = client

    def execute(self, script: str, *, strict: bool = False) -> List[dict]:
        payload = self._client.query(script)
        results, transport_err = parse_response_payload(payload)
        if transport_err:
            raise GameStoreError(transport_err)
        if strict:
            err = _prefer_error_row(results)
            if err is not None:
                line = err.get("line")
                msg = err.get("message", "nieznany błąd")
                cause = err.get("cause")
                if cause:
                    msg = f"{msg} ({cause})"
                prefix = f"L{line}: " if line is not None else ""
                raise GameStoreError(f"{prefix}{msg}")
        return results

    def close(self) -> None:
        self._client.close()


class GameStore:
    """API gry: bąble = encje, cechy = stan, relacje = graf narracji."""

    def __init__(self, backend: _Backend):
        self._backend = backend

    def close(self) -> None:
        self._backend.close()

    def run(self, script: str, *, strict: bool = True) -> List[dict]:
        return self._backend.execute(script.strip(), strict=strict)

    def run_line(self, line: str, *, strict: bool = True) -> dict:
        return _last(self.run(line, strict=strict))

    def stats(self) -> dict:
        row = self.run_line("STATYSTYKI", strict=True)
        return row.get("data", {})

    def list_worlds(self) -> List[dict]:
        row = self.run_line("LISTA ŚWIATÓW", strict=True)
        return list(row.get("worlds", []))

    def select_world(
        self,
        name: str,
        *,
        create: bool = False,
        cel: str = "",
        promien: int | None = None,
    ) -> dict:
        if create:
            cmd = f'UTWÓRZ ŚWIAT "{_esc(name)}"'
        elif cel:
            cmd = f'WYBIERZ ŚWIAT "{_esc(name)}" CEL "{_esc(cel)}"'
            if promien is not None:
                cmd += f" PROMIEŃ {int(promien)}"
        else:
            cmd = f'WYBIERZ ŚWIAT "{_esc(name)}"'
        row = self.run_line(cmd, strict=True)
        return row

    def unfold(self, bubble: str, promien: int | None = None) -> dict:
        cmd = f'ROZWIJ "{_esc(bubble)}"'
        if promien is not None:
            cmd += f" PROMIEŃ {int(promien)}"
        return self.run_line(cmd, strict=True)

    def detach_world(self) -> None:
        self.run_line("ODŁĄCZ ŚWIAT", strict=True)

    def flush_world(self) -> dict:
        return self.run_line("ZAPISZ ŚWIAT", strict=True)

    def login(self, user: str, token: str) -> dict:
        return self.run_line(f'ZALOGUJ "{_esc(user)}" TOKEN "{_esc(token)}"', strict=True)

    def logout(self) -> None:
        self.run_line("WYLOGUJ", strict=True)

    def whoami(self) -> dict:
        row = self.run_line("KTO JESTEM", strict=True)
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def health(self) -> dict:
        row = self.run_line("ZDROWIE", strict=True)
        return row.get("data", {})

    def server_metrics(self) -> dict:
        row = self.run_line("METRYKI SERWERA", strict=True)
        return row.get("data", {})

    def backup_world(self, name: str) -> dict:
        row = self.run_line(f'KOPIA ZAPASOWA ŚWIATA "{_esc(name)}"', strict=True)
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def list_backups(self, name: str) -> List[dict]:
        row = self.run_line(f'LISTA KOPII ŚWIATA "{_esc(name)}"', strict=True)
        return list(row.get("backups", []))

    def restore_world(self, name: str, backup_id: str) -> dict:
        row = self.run_line(
            f'PRZYWRÓĆ ŚWIAT "{_esc(name)}" Z KOPII "{_esc(backup_id)}"',
            strict=True,
        )
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def list_peers(self) -> List[dict]:
        row = self.run_line("LISTA WĘZŁÓW", strict=True)
        return list(row.get("peers", []))

    def add_peer(
        self,
        name: str,
        host: str,
        port: int,
        *,
        user: Optional[str] = None,
        token: Optional[str] = None,
    ) -> dict:
        cmd = f'DODAJ WĘZEŁ "{_esc(name)}" HOST "{_esc(host)}" PORT {int(port)}'
        if user and token:
            cmd += f' UŻYTKOWNIK "{_esc(user)}" TOKEN "{_esc(token)}"'
        row = self.run_line(cmd, strict=True)
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def remove_peer(self, name: str) -> dict:
        row = self.run_line(f'USUŃ WĘZEŁ "{_esc(name)}"', strict=True)
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def pull_world(self, world: str, peer: str) -> dict:
        row = self.run_line(f'PULL ŚWIAT "{_esc(world)}" Z "{_esc(peer)}"', strict=True)
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def push_world(self, world: str, peer: str) -> dict:
        row = self.run_line(f'PUSH ŚWIAT "{_esc(world)}" DO "{_esc(peer)}"', strict=True)
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def sync_world(self, world: str, peer: str) -> dict:
        row = self.run_line(f'SYNC ŚWIAT "{_esc(world)}" Z "{_esc(peer)}"', strict=True)
        return {k: v for k, v in row.items() if k not in ("status", "action")}

    def seed_demo_world(self, *, reset: bool = True) -> None:
        """NPC, gracz, quest, relacje i pamięć tekstowa (JSON w cechach).

        reset=True (domyślnie): usuwa poprzednie bąble demo — bezpieczne przy
        ponownym odpaleniu na trwałym świecie (--world rivendell).
        """
        if reset:
            for name in ("Gandalf", "Aldric", "Quest_Smok"):
                # brak bąbla ≠ błąd krytyczny
                self.run(f'USUŃ BĄBEL "{_esc(name)}"', strict=False)
        self.run(
            '''
UTRWAL "Gandalf"
WSTRZYKNIJ "Rola" = "NPC" DO "Gandalf"
WSTRZYKNIJ "Opis" = "Mędrzec w Szarych Czeluści" DO "Gandalf"
WSTRZYKNIJ "Stats" = {"hp": 80, "mana": 200, "faksja": "Istari"} DO "Gandalf"
WSTRZYKNIJ "Pamięć" = "Smok straszy wioskę Rivendell na północy" DO "Gandalf"
WSTRZYKNIJ "Klucz" = "smok" DO "Gandalf"
UTRWAL "Aldric"
WSTRZYKNIJ WIELE "Rola" = "Gracz", "Imię" = "Aldric" DO "Aldric"
UTRWAL "Quest_Smok"
WSTRZYKNIJ WIELE "Tytuł" = "Smok w jaskini", "Status" = "aktywny", "Nagroda" = 500 DO "Quest_Smok"
POŁĄCZ "Gandalf" Z "Quest_Smok" JAKO "zna_quest"
POŁĄCZ "Aldric" Z "Gandalf" JAKO "spotkał"
'''.strip()
        )

    def show(self, bubble: str) -> dict:
        row = self.run_line(f'POKAŻ "{_esc(bubble)}"', strict=True)
        return row.get("data", {})

    def find_npcs(self) -> List[str]:
        row = self.run_line('ZNAJDŹ GDZIE "Rola" = "NPC"', strict=True)
        return list(row.get("matches", []))

    def find_players(self) -> List[str]:
        row = self.run_line('ZNAJDŹ GDZIE "Rola" = "Gracz"', strict=True)
        return list(row.get("matches", []))

    def npc_stats_table(self) -> List[dict]:
        row = self.run_line(
            'WYPISZ "BĄBEL", JSON_WARTOŚĆ("Stats", "$.hp") JAKO "HP", '
            'JSON_WARTOŚĆ("Stats", "$.mana") JAKO "Mana", '
            'JSON_WARTOŚĆ("Stats", "$.faksja") JAKO "Faksja" '
            'GDZIE "Rola" = "NPC"',
            strict=True,
        )
        return list(row.get("rows", []))

    def quest_givers(self, quest_bubble: str) -> List[str]:
        row = self.run_line(
            f'ZNAJDŹ POŁĄCZONE JAKO "zna_quest" Z "{_esc(quest_bubble)}"',
            strict=True,
        )
        return list(row.get("matches", []))

    _MEMORY_FIELDS = ("Pamięć", "Opis", "Tytuł")

    def search_memory(self, query: str) -> List[str]:
        """Przeszukuje tekst pamięci NPC/questów (ILIKE na cechach narracyjnych)."""
        pattern = f"%{_esc(query)}%"
        clauses = " LUB ".join(
            f'"{_esc(field)}" ILIKE "{pattern}"' for field in self._MEMORY_FIELDS
        )
        row = self.run_line(f"ZNAJDŹ GDZIE {clauses}", strict=True)
        return list(row.get("matches", []))

    def search_resonance(self, query: str) -> List[str]:
        """Rezonans Lorentz→HRR (SZUKAJ) — najlepiej na krótkich etykietach atomów (np. Klucz)."""
        row = self.run_line(f'SZUKAJ "{_esc(query)}"', strict=True)
        return list(row.get("matches", []))

    def excite(self, bubble: str, energy: float, relation: Optional[str] = None) -> None:
        rel = f' PO RELACJI "{_esc(relation)}"' if relation else ""
        self.run_line(f'WZBUDŹ "{_esc(bubble)}" ENERGIĄ {energy}{rel}', strict=True)

    def tick(self, cycles: int = 1) -> None:
        self.run_line(f"TICK {cycles}", strict=True)

    def explain_find(self, key: str, value: str) -> dict:
        row = self.run_line(
            f'WYJAŚNIJ ZNAJDŹ GDZIE "{_esc(key)}" = "{_esc(value)}"',
            strict=False,
        )
        return {k: v for k, v in row.items() if k not in ("status", "action")}


def connect_rpc(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    world: Optional[str] = None,
    create_world: bool = False,
    user: Optional[str] = None,
    token: Optional[str] = None,
) -> GameStore:
    from cynober_client import CynoberClient

    client = CynoberClient(host=host, port=port)
    client.connect()
    store = GameStore(RpcBackend(client))
    if user and token:
        store.login(user, token)
    if world:
        store.select_world(world, create=create_world)
    return store


def connect_local(engine: Optional[KarminEngine] = None) -> GameStore:
    return GameStore(LocalBackend(engine))