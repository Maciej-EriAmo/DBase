#!/usr/bin/env python3
"""
Podłączenie zespołu do Cynober — 15 sekund (v7.6).

  python cynober_konfigurator.py    # profil + opcjonalnie hss_profile=standard
  python cynober_server.py
  python examples/team_connect.py
  python examples/team_connect.py --profile zespol
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cynober_client import connect


def main() -> int:
    parser = argparse.ArgumentParser(description="Test połączenia zespołu z Cynober DB")
    parser.add_argument("--profile", help="Profil z ~/.karmazyn_client.json")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()

    if args.host and args.port:
        client = connect(args.host, args.port)
    else:
        client = connect(profile=args.profile)

    try:
        health = client.query_line("ZDROWIE")
        metrics = client.query_line("METRYKI SERWERA")
        worlds = client.query_line("LISTA ŚWIATÓW")
        print(f"Tunel:     {client.crypto_mode}")
        print(f"Serwer:    v{health.get('data', {}).get('server_version', '?')}")
        print(f"Profil HSS: {__import__('os').environ.get('KARM_HSS_PROFILE', 'proto')}")
        print(f"Światy:    {len(worlds.get('worlds', []))}")
        print(f"Zapytania: {metrics.get('data', {}).get('queries_total', 0)}")
        print("OK — gotowe do KarminQL.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())