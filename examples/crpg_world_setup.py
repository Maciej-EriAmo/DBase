#!/usr/bin/env python3
"""
Seed trwałego świata CRPG hack-and-slash przez serwer RPC (v7.3).

Uruchom serwer:
  python cynober_server.py

Następnie:
  python examples/crpg_world_setup.py
  python examples/crpg_world_setup.py --world dungeon --create-world
  python examples/crpg_world_setup.py --user admin --token sekret
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cynober_client_config import get_active_profile, load_config, resolve_client_target
from game_store import GameStore, connect_rpc


def seed_crpg_world(store: GameStore) -> None:
    """Bohater, potwory, loot, strefy i questy — gotowe pod hack and slash."""
    store.run(
        '''
UTRWAL "Aldric"
WSTRZYKNIJ WIELE
  "Rola" = "Gracz",
  "Klasa" = "Wojownik",
  "Stats" = {"hp": 120, "max_hp": 120, "atk": 18, "def": 12, "xp": 0, "level": 1}
DO "Aldric"

UTRWAL "Kowal_Bron"
WSTRZYKNIJ WIELE "Rola" = "NPC", "Typ" = "Handlarz", "Opis" = "Kowal w wiosce Ashford" DO "Kowal_Bron"
UTRWAL "Kapłanka_Mira"
WSTRZYKNIJ WIELE "Rola" = "NPC", "Typ" = "QuestGiver", "Opis" = "Szuka oczyszczenia lochu" DO "Kapłanka_Mira"

UTRWAL "Goblin_Scout"
WSTRZYKNIJ WIELE
  "Rola" = "Potwór",
  "Typ" = "goblin",
  "Stats" = {"hp": 25, "atk": 6, "def": 2, "loot_tier": 1}
DO "Goblin_Scout"
UTRWAL "Orc_Berserker"
WSTRZYKNIJ WIELE
  "Rola" = "Potwór",
  "Typ" = "orc",
  "Stats" = {"hp": 80, "atk": 14, "def": 6, "loot_tier": 2}
DO "Orc_Berserker"
UTRWAL "Smok_Młody"
WSTRZYKNIJ WIELE
  "Rola" = "Potwór",
  "Typ" = "boss",
  "Stats" = {"hp": 400, "atk": 28, "def": 18, "loot_tier": 4}
DO "Smok_Młody"

UTRWAL "Miecz_Żelazny"
WSTRZYKNIJ WIELE "Rola" = "Loot", "Typ" = "broń", "Stats" = {"atk": 5, "rzadkość": "zwykły"} DO "Miecz_Żelazny"
UTRWAL "Mikstura_HP"
WSTRZYKNIJ WIELE "Rola" = "Loot", "Typ" = "consumable", "Stats" = {"heal": 40} DO "Mikstura_HP"
UTRWAL "Skarb_Smoka"
WSTRZYKNIJ WIELE "Rola" = "Loot", "Typ" = "skarb", "Stats" = {"gold": 500, "rzadkość": "legendarny"} DO "Skarb_Smoka"

UTRWAL "Wioska_Ashford"
WSTRZYKNIJ WIELE "Rola" = "Strefa", "Typ" = "bezpieczna", "Opis" = "Hub startowy" DO "Wioska_Ashford"
UTRWAL "Loch_Cieni"
WSTRZYKNIJ WIELE "Rola" = "Strefa", "Typ" = "dungeon", "Opis" = "Gobliny i orki" DO "Loch_Cieni"
UTRWAL "Jaskinia_Smoka"
WSTRZYKNIJ WIELE "Rola" = "Strefa", "Typ" = "boss_arena", "Opis" = "Smok Młody strzeże skarbu" DO "Jaskinia_Smoka"

UTRWAL "Quest_Oczyść_Loch"
WSTRZYKNIJ WIELE
  "Rola" = "Quest",
  "Tytuł" = "Oczyść Loch Cieni",
  "Status" = "aktywny",
  "Nagroda" = 200,
  "Cel" = "Pokonaj 3 gobliny i orka"
DO "Quest_Oczyść_Loch"

POŁĄCZ "Aldric" Z "Wioska_Ashford" JAKO "w_strefie"
POŁĄCZ "Wioska_Ashford" Z "Loch_Cieni" JAKO "wejście"
POŁĄCZ "Loch_Cieni" Z "Jaskinia_Smoka" JAKO "głębiej"
POŁĄCZ "Goblin_Scout" Z "Loch_Cieni" JAKO "spawn"
POŁĄCZ "Orc_Berserker" Z "Loch_Cieni" JAKO "spawn"
POŁĄCZ "Smok_Młody" Z "Jaskinia_Smoka" JAKO "boss"
POŁĄCZ "Skarb_Smoka" Z "Smok_Młody" JAKO "drop"
POŁĄCZ "Kapłanka_Mira" Z "Quest_Oczyść_Loch" JAKO "daje_quest"
POŁĄCZ "Aldric" Z "Quest_Oczyść_Loch" JAKO "ma_quest"
POŁĄCZ "Kowal_Bron" Z "Miecz_Żelazny" JAKO "sprzedaje"
'''.strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed świata CRPG na serwerze Cynober")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--world", default="crpg_dungeon", help="Nazwa trwałego świata")
    parser.add_argument("--create-world", action="store_true", help="UTWÓRZ ŚWIAT zamiast WYBIERZ")
    parser.add_argument("--user", default=None)
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    cfg = load_config()
    profile = get_active_profile(cfg)
    host, port = resolve_client_target(args.host, args.port, profile)

    store = connect_rpc(
        host=host,
        port=port,
        world=args.world,
        create_world=args.create_world,
        user=args.user,
        token=args.token,
    )
    try:
        print(f"Świat: {args.world} @ {host}:{port}")
        seed_crpg_world(store)
        stats = store.stats()
        print(f"  Bąble: {stats.get('bubbles', 0)}")
        print(f"  Gracze: {store.find_players()}")
        print("  Potwory (Rola=Potwór):", end=" ")
        row = store.run_line('ZNAJDŹ GDZIE "Rola" = "Potwór"', strict=True)
        print(row.get("matches", []))
        print(f"  Quest giverzy: {store.quest_givers('Quest_Oczyść_Loch')}")
        print("Gotowe — świat CRPG zaseedowany.")
    finally:
        store.close()


if __name__ == "__main__":
    main()