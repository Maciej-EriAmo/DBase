#!/usr/bin/env python3
"""Drug węzeł testowy na stałym porcie (E2E replikacji v7.4)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from cynober_worlds import reset_world_registry_for_tests
from tests.test_server_rpc import TestServerHarness


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: peer_server.py PORT WORLDS_DIR", file=sys.stderr)
        return 2
    port = int(sys.argv[1])
    worlds = Path(sys.argv[2])
    worlds.mkdir(parents=True, exist_ok=True)
    os.environ["CYNOBER_WORLDS_DIR"] = str(worlds)
    reset_world_registry_for_tests(worlds)
    harness = TestServerHarness(port=port)
    harness.start()
    print(f"READY {port}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        harness.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())