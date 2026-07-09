"""Kompatybilność testów — delegacja do cynober_client.py (v7.6)."""

from cynober_client import CynoberClient, CynoberClientError, CynoberRpcClient, connect

__all__ = ["CynoberClient", "CynoberClientError", "CynoberRpcClient", "connect"]