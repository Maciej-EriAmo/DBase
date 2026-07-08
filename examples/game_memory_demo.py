#!/usr/bin/env python3
"""
Demo pamięci gry — Cynober DB przez serwer RPC (v7.0 izolacja sesji).

Uruchom serwer w osobnym terminalu:
  python cynober_server.py

Następnie:
  python examples/game_memory_demo.py
  python examples/game_memory_demo.py --host 192.168.1.10 --port 8080
  python examples/game_memory_demo.py --local   # bez serwera (in-process)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cynober_client_config import get_active_profile, load_config, resolve_client_target
from game_store import GameStore, connect_local, connect_rpc


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def _section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def run_demo(store: GameStore, *, via: str) -> None:
    _section(f"Połączenie: {via}")
    stats = store.stats()
    print(f"  Sesja izolowana: {stats.get('session_isolated', 'n/d (tryb lokalny)')}")
    print(f"  Etykieta sesji:  {stats.get('session_label', 'local')}")
    print(f"  Aktywne tunele:  {stats.get('active_sessions', '—')}")
    print(f"  Bąble w sesji:   {stats.get('bubbles', 0)}")

    _section("1. Świat startowy (UTRWAL + WSTRZYKNIJ + JSON + POŁĄCZ)")
    store.seed_demo_world()
    gandalf = store.show("Gandalf")
    print(f"  Gandalf — cechy: {list(gandalf.get('properties', {}).keys())}")
    print(f"  Relacje: {gandalf.get('relations', [])}")

    _section("2. NPC i ścieżki JSON (JSON_WARTOŚĆ)")
    npc_rows = store.npc_stats_table()
    if npc_rows:
        _print_table(
            ["BĄBEL", "HP", "Mana", "Faksja"],
            [[r.get("BĄBEL"), r.get("HP"), r.get("Mana"), r.get("Faksja")] for r in npc_rows],
        )

    _section("3. Graf questów (ZNAJDŹ POŁĄCZONE JAKO …)")
    givers = store.quest_givers("Quest_Smok")
    print(f"  Kto zna quest 'Quest_Smok': {givers}")

    _section("4. Pamięć narracyjna (ZNAJDŹ + ILIKE)")
    for query in ("smok", "mędrzec", "jaskini"):
        hits = store.search_memory(query)
        print(f"  fraza \"{query}\" → bąble: {hits}")

    _section("4b. Rezonans HRR (SZUKAJ — krótkie etykiety atomów)")
    print("  HRR rezonuje z E atomu; długi tekst Pamięci lepiej szukać przez search_memory().")
    for query in ("smok",):
        hits = store.search_resonance(query)
        print(f"  SZUKAJ \"{query}\" → {hits}")

    _section("5. Plan zapytania (WYJAŚNIJ)")
    plan = store.explain_find("Rola", "NPC")
    print(f"  Strategia: {plan.get('plan', {}).get('strategy')}")
    print(f"  Szacowane wiersze: {plan.get('plan', {}).get('estimated_rows')}")

    _section("6. Termodynamika pamięci (WZBUDŹ + TICK)")
    print("  WZBUDŹ Gandalf ENERGIĄ 120 — podbija temperaturę atomów (ważniejsza pamięć).")
    store.excite("Gandalf", 120.0)
    before = store.stats()
    print(f"  Atomy gorące przed TICK: {before.get('hot')}")
    print("  TICK 8 — cykle termodynamiczne (zapominanie zimnych atomów).")
    store.tick(8)
    after = store.stats()
    print(f"  Atomy gorące po TICK:   {after.get('hot')}")
    print(f"  Odczytane (reaped):     {after.get('reaped')}")

    _section("7. Co pamięta sesja po podgrzaniu")
    hits = store.search_memory("smok")
    print(f"  ILIKE \"smok\" po TICK → {hits}")
    if store.find_npcs():
        props = store.show("Gandalf").get("properties", {})
        print(f"  Gandalf.Pamięć = {props.get('Pamięć', '—')}")

    _section("Podsumowanie")
    print("  Ta sesja RPC żyje tylko w Twoim tunelu (v7.0).")
    print("  Drugi klient nie zobaczy Gandalfa ani questów z tego demo.")
    print("  Rozłączenie = utrata stanu, chyba że ZAPISZ w tej samej sesji.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo pamięci gry — Cynober przez RPC")
    parser.add_argument("--local", action="store_true", help="Silnik lokalny (bez serwera)")
    parser.add_argument("--host", help="Host serwera (domyślnie profil aktywny)")
    parser.add_argument("--port", type=int, help="Port serwera")
    parser.add_argument("--profile", help="Profil z ~/.karmazyn_client.json")
    args = parser.parse_args()

    store: GameStore | None = None
    try:
        if args.local:
            store = connect_local()
            run_demo(store, via="lokalny KarminEngine")
        else:
            cfg = load_config()
            if args.profile:
                from cynober_client_config import set_active_profile
                set_active_profile(args.profile)
                cfg = load_config()
            prof_name, prof = get_active_profile(cfg)
            if args.host or args.port is not None:
                argv: list[str] = []
                if args.host:
                    argv.append(args.host)
                if args.port is not None:
                    argv.append(str(args.port))
                host, port, _ = resolve_client_target(argv)
            else:
                host, port, _ = resolve_client_target([])
            store = connect_rpc(host, port)
            run_demo(store, via=f"RPC {host}:{port} (profil: {prof_name})")
        return 0
    except (ConnectionError, OSError, RuntimeError) as e:
        print(f"\n[!] Nie udało się połączyć z serwerem: {e}", file=sys.stderr)
        print("    Uruchom: python cynober_server.py", file=sys.stderr)
        print("    Lub użyj: python examples/game_memory_demo.py --local", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[!] Przerwano.")
        return 130
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())