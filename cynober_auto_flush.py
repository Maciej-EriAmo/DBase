"""
cynober_auto_flush.py — okresowy zapis brudnych światów (v7.8, faza 0)
"""

from __future__ import annotations

import threading
from typing import Any

from cynober_paths import int_env as _int_env
from cynober_worlds import WorldRegistry


def default_auto_flush_config() -> dict[str, int]:
    """interval_sec=0 wyłącza auto-flush."""
    return {"interval_sec": 60}


def load_auto_flush_config(server_cfg: dict[str, Any] | None = None) -> dict[str, int]:
    base = default_auto_flush_config()
    if server_cfg:
        af = server_cfg.get("auto_flush")
        if isinstance(af, dict) and "interval_sec" in af:
            try:
                base["interval_sec"] = max(0, int(af["interval_sec"]))
            except (TypeError, ValueError):
                pass
    base["interval_sec"] = _int_env("CYNOBER_AUTO_FLUSH_SEC", base["interval_sec"])
    return base


class AutoFlushWorker:
    """Daemon: co interval_sec zapisuje światy z flagą dirty."""

    def __init__(self, registry: WorldRegistry, interval_sec: int):
        self._registry = registry
        self._interval = max(0, int(interval_sec))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="cynober-auto-flush", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def flush_now(self) -> list[dict]:
        return self._registry.flush_all_dirty()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                flushed = self.flush_now()
                if flushed:
                    names = ", ".join(f["name"] for f in flushed)
                    print(f"[Cynober] Auto-flush: {names} ({len(flushed)} światów)")
            except Exception as exc:
                print(f"[Cynober] Auto-flush błąd: {exc}")