#!/usr/bin/env python3
"""
cynober_konfigurator.py — kreator profili klienta i konfiguracji serwera
"""

from __future__ import annotations

import subprocess
import sys

from cynober_client_config import (
    CONFIG_PATH,
    DEFAULT_PORT,
    DEFAULT_SERVER_BIND,
    default_hss_profile,
    get_active_profile,
    get_server_config,
    list_local_ips,
    list_profiles,
    load_config,
    save_config,
    set_active_profile,
    test_connection,
    upsert_profile,
    upsert_server_config,
)
from cynober_firewall import (
    firewall_script_path,
    run_windows_firewall,
    write_windows_firewall_script,
)
from cynober_rate_limit import default_rate_limit_config


def _prompt(label: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    val = input(f"{label}{hint}: ").strip()
    return val if val else default


def _menu() -> None:
    print("\n" + "=" * 60)
    print("  Cynober — konfigurator klienta i serwera")
    print(f"  Plik: {CONFIG_PATH}")
    print("=" * 60)
    print("  --- Klient (połączenie DO serwera) ---")
    print("  1. Lista profili klienta")
    print("  2. Nowy / edytuj profil klienta")
    print("  3. Wybierz aktywny profil klienta")
    print("  4. Test połączenia (handshake)")
    print("  5. Uruchom klienta (Cynober_db.py)")
    print("  --- Serwer (nasłuch na tej maszynie) ---")
    print("  6. Pokaż konfigurację serwera")
    print("  7. Edytuj konfigurację serwera")
    print("  8. Adresy IP tej maszyny (dla profilu Termux)")
    print("  9. Uruchom serwer (cynober_server.py)")
    print("  10. Firewall Windows (generuj / uruchom skrypt)")
    print("  --- Inne ---")
    print("  11. Wskazówki Termux / LAN")
    print("  0. Wyjście")


def _show_profiles() -> None:
    cfg = load_config()
    active = cfg.get("active", "?")
    print(f"\nAktywny profil klienta: {active}\n")
    for name, prof in list_profiles(cfg).items():
        mark = "*" if name == active else " "
        host = prof.get("host", "?")
        port = prof.get("port", "?")
        note = prof.get("note", "")
        extras = []
        if prof.get("psk"):
            extras.append("PSK")
        if prof.get("qkd_seed"):
            extras.append("QKD")
        hss = prof.get("hss_profile")
        if hss:
            extras.append(f"HSS={hss}")
        extra = f" ({', '.join(extras)})" if extras else ""
        print(f"  {mark} {name}: {host}:{port}{extra}")
        if note:
            print(f"      {note}")


def _edit_profile() -> None:
    name = _prompt("Nazwa profilu klienta", "termux")
    profiles = list_profiles()
    if name in profiles:
        current = profiles[name]
    else:
        _, current = get_active_profile()

    print("\nHost — adres serwera widziany Z TEJ maszyny.")
    print("Termux → wpisz IP komputera z opcji 8 (na serwerze).")
    print("Lokalnie → 127.0.0.1")
    host = _prompt("Host serwera", str(current.get("host", "127.0.0.1")))
    port_s = _prompt("Port", str(current.get("port", DEFAULT_PORT)))
    note = _prompt("Opis", str(current.get("note", "")))
    psk = _prompt("KARM_PSK", str(current.get("psk", "")))
    qkd = _prompt("KARM_QKD_SEED", str(current.get("qkd_seed", "")))
    hss_default = str(
        current.get("hss_profile") or get_server_config().get("hss_profile") or default_hss_profile()
    )
    print("\nProfil HSS (proto|standard|production) — musi się zgadzać z serwerem.")
    hss = _prompt("KARM_HSS_PROFILE", hss_default)

    try:
        port = int(port_s)
    except ValueError:
        print("[!] Port musi być liczbą.")
        return

    upsert_profile(
        name, host, port, note=note, psk=psk, qkd_seed=qkd, hss_profile=hss
    )
    print(f"\n[OK] Profil klienta '{name}' → {host}:{port}")


def _pick_active() -> None:
    profiles = list_profiles()
    if not profiles:
        print("[!] Brak profili.")
        return
    print("Dostępne:", ", ".join(profiles))
    name = _prompt("Aktywny profil", load_config().get("active", ""))
    try:
        set_active_profile(name)
        print(f"[OK] Aktywny profil klienta: {name}")
    except KeyError as e:
        print(f"[!] {e}")


def _run_test() -> None:
    name, prof = get_active_profile()
    host = str(prof.get("host", "127.0.0.1"))
    port = int(prof.get("port", DEFAULT_PORT))
    print(f"\nTest klienta: {name} → {host}:{port} ...")
    from cynober_client_config import apply_profile_secrets
    apply_profile_secrets(prof)
    ok, msg = test_connection(host, port)
    print(f"[{'OK' if ok else 'BŁĄD'}] {msg}")


def _run_client() -> None:
    name, prof = get_active_profile()
    host = str(prof.get("host", "127.0.0.1"))
    port = int(prof.get("port", DEFAULT_PORT))
    print(f"Uruchamiam klienta: {name} → {host}:{port}\n")
    subprocess.run([sys.executable, "Cynober_db.py", "--profile", name], check=False)


def _show_server() -> None:
    srv = get_server_config()
    print("\n[Konfiguracja serwera — ta maszyna]")
    print(f"  Nasłuch (bind): {srv.get('bind_host', DEFAULT_SERVER_BIND)}")
    print(f"  Port:           {srv.get('port', DEFAULT_PORT)}")
    if srv.get("note"):
        print(f"  Opis:           {srv['note']}")
    if srv.get("psk"):
        print("  KARM_PSK:       (zapisany w profilu)")
    if srv.get("qkd_seed"):
        print("  KARM_QKD_SEED:  (zapisany w profilu)")
    print(f"  KARM_HSS_PROFILE: {srv.get('hss_profile', default_hss_profile())}")
    rl = srv.get("rate_limit") or default_rate_limit_config()
    print("\n  Rate limit (ochrona przed flood):")
    print(f"    Równoczesne globalnie:     {rl.get('max_concurrent_global', 0)}")
    print(f"    Równoczesne na IP:         {rl.get('max_connections_per_ip', 0)}")
    print(f"    Nowe połączenia/IP/min:    {rl.get('max_new_connections_per_ip_per_min', 0)}")
    print(f"    Zapytania na sesję/min:   {rl.get('max_queries_per_minute', 0)}")
    print("    (0 = wyłącz dany limit)")
    ips = list_local_ips()
    if ips:
        print("\n  Adresy LAN do wpisania w profilu klienta (Termux):")
        for ip in ips:
            print(f"    → {ip}:{srv.get('port', DEFAULT_PORT)}")


def _edit_server() -> None:
    srv = get_server_config()
    print("\n[Edycja serwera]")
    print("bind_host 0.0.0.0 = wszystkie interfejsy (zalecane pod Termux/LAN)")
    print("bind_host 127.0.0.1 = tylko ten komputer")
    bind = _prompt("bind_host", str(srv.get("bind_host", DEFAULT_SERVER_BIND)))
    port_s = _prompt("Port", str(srv.get("port", DEFAULT_PORT)))
    note = _prompt("Opis", str(srv.get("note", "")))
    psk = _prompt("KARM_PSK (jak na kliencie)", str(srv.get("psk", "")))
    qkd = _prompt("KARM_QKD_SEED (jak na kliencie)", str(srv.get("qkd_seed", "")))
    print("\nProfil HSS (proto|standard|production) — obie strony muszą mieć ten sam.")
    hss = _prompt(
        "KARM_HSS_PROFILE",
        str(srv.get("hss_profile", default_hss_profile())),
    )
    rl_prev = srv.get("rate_limit") or default_rate_limit_config()
    print("\nLimity (Enter = zostaw, 0 = wyłącz limit):")
    try:
        port = int(port_s)
        rl = {
            "max_concurrent_global": int(_prompt(
                "  max równoczesnych połączeń (globalnie)",
                str(rl_prev.get("max_concurrent_global", 32)),
            )),
            "max_connections_per_ip": int(_prompt(
                "  max równoczesnych na jedno IP",
                str(rl_prev.get("max_connections_per_ip", 4)),
            )),
            "max_new_connections_per_ip_per_min": int(_prompt(
                "  max nowych połączeń/IP na minutę",
                str(rl_prev.get("max_new_connections_per_ip_per_min", 20)),
            )),
            "max_queries_per_minute": int(_prompt(
                "  max zapytań na sesję na minutę",
                str(rl_prev.get("max_queries_per_minute", 120)),
            )),
        }
    except ValueError:
        print("[!] Port i limity muszą być liczbami.")
        return
    upsert_server_config(
        bind, port, note=note, psk=psk, qkd_seed=qkd, hss_profile=hss, rate_limit=rl
    )
    print(f"\n[OK] Serwer: nasłuch {bind}:{port}")
    if bind == "0.0.0.0":
        for ip in list_local_ips():
            print(f"     Klient Termux → profil host={ip}, port={port}")


def _show_ips() -> None:
    srv = get_server_config()
    port = srv.get("port", DEFAULT_PORT)
    ips = list_local_ips()
    print("\n[Adresy IPv4 tej maszyny]")
    if not ips:
        print("  Nie wykryto adresów LAN — sprawdź ipconfig / ip addr")
        return
    for ip in ips:
        print(f"  {ip}:{port}  ← wpisz ten host w profilu klienta (Termux)")


def _firewall_windows() -> None:
    import sys

    srv = get_server_config()
    port = int(srv.get("port", DEFAULT_PORT))
    print(f"\n[Firewall Windows — port TCP {port}]")
    script = write_windows_firewall_script(port)
    print(f"  Skrypt: {script}")
    print("\n  Ręcznie (PowerShell jako Administrator):")
    print(
        f"  powershell -ExecutionPolicy Bypass -File \"{script}\" -Port {port}"
    )
    if sys.platform != "win32":
        print("\n  [!] Ten komputer nie jest Windows — tylko wygenerowano skrypt.")
        return
    if _prompt("Uruchomić teraz (wymaga Administratora)? (t/n)", "n").lower() != "t":
        return
    ok, msg = run_windows_firewall(port)
    print(f"[{'OK' if ok else 'BŁĄD'}] {msg}")
    if not ok and "Administrator" in msg:
        print(f"  Otwórz PowerShell jako Admin i uruchom:\n  {firewall_script_path()}")


def _run_server() -> None:
    srv = get_server_config()
    bind = srv.get("bind_host", DEFAULT_SERVER_BIND)
    port = srv.get("port", DEFAULT_PORT)
    print(f"Uruchamiam serwer: {bind}:{port}")
    print("Zatrzymanie: Ctrl+C\n")
    subprocess.run([sys.executable, "cynober_server.py"], check=False)


def _termux_hints() -> None:
    print(
        """
[Telefon Termux → komputer serwer]

NA SERWERZE (PC):
  7 → edytuj serwer: bind 0.0.0.0, port 8080, ten sam PSK co klient
  8 → skopiuj IP LAN (np. 192.168.1.42)
  9 → uruchom serwer
  10 → firewall Windows (skrypt + opcjonalnie uruchom)

NA TELEFONIE (Termux):
  2 → profil klienta: host=IP z kroku 8, port=8080, ten sam PSK
  4 → test połączenia
  5 → uruchom klienta

Pliki ~/.karmazyn_node_id i ~/.karmazyn_phi2 — osobne na każdym urządzeniu (OK).
KARM_PSK / KARM_QKD_SEED — identyczne na serwerze i kliencie (sekcje server + profil).

Termux: pkg install python numpy && cd ~/DBase && python cynober_konfigurator.py
"""
    )


def main() -> None:
    if not CONFIG_PATH.exists():
        save_config(load_config())

    actions = {
        "1": _show_profiles,
        "2": _edit_profile,
        "3": _pick_active,
        "4": _run_test,
        "5": _run_client,
        "6": _show_server,
        "7": _edit_server,
        "8": _show_ips,
        "9": _run_server,
        "10": _firewall_windows,
        "11": _termux_hints,
    }

    while True:
        _menu()
        choice = input("\nWybór: ").strip()
        if choice == "0":
            break
        action = actions.get(choice)
        if action:
            action()
        else:
            print("[!] Nieznana opcja.")


if __name__ == "__main__":
    main()