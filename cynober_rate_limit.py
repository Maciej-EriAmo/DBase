"""
cynober_rate_limit.py — limity połączeń i zapytań (ochrona przed flood / DoS).
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def default_rate_limit_config() -> dict[str, int]:
    return {
        "max_concurrent_global": 32,
        "max_connections_per_ip": 4,
        "max_new_connections_per_ip_per_min": 20,
        "max_queries_per_minute": 120,
    }


def load_rate_limit_config(server_cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """Priorytet: zmienne CYNOBER_* > sekcja server.rate_limit > domyślne."""
    base = default_rate_limit_config()
    if server_cfg:
        rl = server_cfg.get("rate_limit")
        if isinstance(rl, dict):
            for key in base:
                if key in rl:
                    try:
                        base[key] = max(0, int(rl[key]))
                    except (TypeError, ValueError):
                        pass

    return {
        "max_concurrent_global": _int_env(
            "CYNOBER_MAX_CONCURRENT", base["max_concurrent_global"]
        ),
        "max_connections_per_ip": _int_env(
            "CYNOBER_MAX_CONN_PER_IP", base["max_connections_per_ip"]
        ),
        "max_new_connections_per_ip_per_min": _int_env(
            "CYNOBER_MAX_CONN_RATE", base["max_new_connections_per_ip_per_min"]
        ),
        "max_queries_per_minute": _int_env(
            "CYNOBER_MAX_QUERIES_PER_MIN", base["max_queries_per_minute"]
        ),
    }


class ServerRateLimiter:
    def __init__(self, config: dict[str, int] | None = None) -> None:
        self.cfg = config or load_rate_limit_config()
        self._lock = threading.Lock()
        self._global_active = 0
        self._active_per_ip: dict[str, int] = defaultdict(int)
        self._connect_times: dict[str, list[float]] = defaultdict(list)

    def _prune(self, bucket: list[float], window: float, now: float) -> list[float]:
        return [t for t in bucket if now - t < window]

    def acquire_connection(self, ip: str) -> tuple[bool, str]:
        c = self.cfg
        if not any(c.values()):
            return True, ""

        now = time.monotonic()
        with self._lock:
            if c["max_concurrent_global"] and self._global_active >= c["max_concurrent_global"]:
                return False, "RATE_LIMIT: zbyt wiele równoczesnych połączeń (globalnie)"

            if c["max_connections_per_ip"] and self._active_per_ip[ip] >= c["max_connections_per_ip"]:
                return False, f"RATE_LIMIT: zbyt wiele połączeń z {ip}"

            if c["max_new_connections_per_ip_per_min"]:
                times = self._prune(self._connect_times[ip], 60.0, now)
                if len(times) >= c["max_new_connections_per_ip_per_min"]:
                    return False, f"RATE_LIMIT: zbyt częste nowe połączenia z {ip}"
                times.append(now)
                self._connect_times[ip] = times

            self._global_active += 1
            self._active_per_ip[ip] += 1
            return True, ""

    def release_connection(self, ip: str) -> None:
        with self._lock:
            if self._global_active > 0:
                self._global_active -= 1
            if self._active_per_ip[ip] > 0:
                self._active_per_ip[ip] -= 1


class SessionQueryLimiter:
    """Limiter zapytań w obrębie jednej sesji TCP."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max(0, max_per_minute)
        self._times: list[float] = []

    def allow(self) -> tuple[bool, str]:
        if not self.max_per_minute:
            return True, ""
        now = time.monotonic()
        self._times = [t for t in self._times if now - t < 60.0]
        if len(self._times) >= self.max_per_minute:
            return False, "RATE_LIMIT: zbyt wiele zapytań w tej sesji (poczekaj chwilę)"
        self._times.append(now)
        return True, ""