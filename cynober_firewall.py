"""
cynober_firewall.py — skrypt zapory Windows dla portu Cynober.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_NAME = "cynober_firewall_windows.ps1"


def firewall_script_path() -> Path:
    return Path(__file__).resolve().parent / "scripts" / SCRIPT_NAME


def write_windows_firewall_script(port: int, rule_name: str = "Cynober DB") -> Path:
    """Generuje / aktualizuje skrypt PowerShell (parametryzowany port)."""
    port = int(port)
    scripts_dir = firewall_script_path().parent
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = firewall_script_path()
    content = f"""# Cynober DB — reguła zapory Windows (generowane przez konfigurator)
# Użycie: powershell -ExecutionPolicy Bypass -File .\\scripts\\{SCRIPT_NAME} -Port {port}
param(
    [int]$Port = {port},
    [string]$RuleName = "{rule_name}"
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "[!] Uruchom PowerShell jako Administrator." -ForegroundColor Red
    exit 1
}}

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {{
    Remove-NetFirewallRule -DisplayName $RuleName
    Write-Host "Usunięto starą regułę: $RuleName"
}}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort $Port `
    -Action Allow `
    -Profile Any `
    -Enabled True

Write-Host "[OK] Zezwolono na TCP $Port ($RuleName)" -ForegroundColor Green
"""
    path.write_text(content, encoding="utf-8")
    return path


def run_windows_firewall(port: int, rule_name: str = "Cynober DB") -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Firewall Windows: dostępne tylko na win32"

    script = write_windows_firewall_script(port, rule_name)
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Port",
                str(int(port)),
                "-RuleName",
                rule_name,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            return True, out.strip() or f"Reguła dodana dla portu {port}"
        if "Administrator" in out or proc.returncode != 0:
            return False, (
                out.strip()
                or "Błąd uruchomienia. Uruchom konfigurator/PowerShell jako Administrator."
            )
        return False, out.strip() or f"exit code {proc.returncode}"
    except FileNotFoundError:
        return False, "Nie znaleziono powershell.exe"
    except subprocess.TimeoutExpired:
        return False, "Przekroczono czas oczekiwania na firewall"