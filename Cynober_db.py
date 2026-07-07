#!/usr/bin/env python3
"""
Cynober_db.py — Interaktywny Klient CLI v1.8.0
==========================================================================
Zaktualizowany do pracy na bezpośrednim gnieździe TCP z szyfrowaniem 
zapożyczonym z protokołu Karmazyn Handshake.
"""

import os
import socket
import json
import sys
import time

from cynober_rpc import (
    PROTO_VERSION,
    RPC_TIMEOUT_SEC,
    HS_TIMEOUT_SEC,
    decrypt_rpc_response,
    encrypt_rpc_request,
    parse_response_payload,
    perform_handshake,
)
from karmazyn_handshake import (
    _CryptoLayer, _send_frame, _recv_frame,
    _compress, _decompress,
)

class CynoberClient:
    def __init__(self, host="127.0.0.1", port=8080):
        self.host = host
        self.port = port
        self.running = True
        self.sock = None
        self.crypto = _CryptoLayer()
        self._crypto_mode = None
        self._hsl_link = None

    def _connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(10.0)
            self.sock.connect((self.host, self.port))

            # deadline w handshake to ABSOLUTNY znacznik time.monotonic()
            # (kontrakt _recv_exact), a nie względne sekundy — inaczej
            # 'time.monotonic() > 5.0' jest prawdą od razu (natychmiastowy timeout).
            hs_deadline = time.monotonic() + HS_TIMEOUT_SEC
            try:
                crypto_mode, _, remote_caps, self._hsl_link = perform_handshake(
                    self.sock, self.crypto, is_server=False, deadline=hs_deadline
                )
            except (ConnectionError, ConnectionResetError, OSError):
                print("[!] Błąd: Serwer odrzucił połączenie (niezgodna wersja protokołu?).")
                return False
            except RuntimeError as e:
                print(f"[!] Błąd handshake: {e}")
                return False

            self._crypto_mode = crypto_mode
            self.sock.settimeout(RPC_TIMEOUT_SEC)
            return True
        except socket.timeout:
            print(f"[!] Błąd połączenia: przekroczono limit czasu ({HS_TIMEOUT_SEC}s).")
            return False
        except Exception as e:
            print(f"[!] Błąd połączenia: {e}")
            return False

    def run(self):
        print("=" * 60)
        print("  Cynober DB Shell Client v1.8.0 (SECURE TUNNEL)")
        print(f"  Nawiązywanie połączenia z {self.host}:{self.port}...")
        
        if not self._connect():
            return

        mode = (self._crypto_mode or "?").upper()
        psk = " + PSK" if os.environ.get("KARM_PSK") else ""
        hsl = " + HSL" if self._hsl_link else ""
        qkd = " + QKD" if self._hsl_link and self._hsl_link.qkd_hybrid else ""
        print(f"  Tunel zabezpieczony ({mode}{psk}{hsl}{qkd}) — protokół {PROTO_VERSION}.")
        print("=" * 60)
        print("Wpisz POMOC, aby wyświetlić listę poleceń. WYJDŹ zamyka tunel.")
        print("-" * 60)

        while self.running:
            try:
                cmd_line = input("Cynober> ").strip()
                if not cmd_line: continue
                if cmd_line.upper() in ("WYJDŹ", "EXIT", "QUIT"): break
                
                if cmd_line.upper() in ("POMOC", "HELP"):
                    self._show_help()
                    continue

                if cmd_line.upper().startswith("WYKRES "):
                    self._handle_chart(cmd_line[7:].strip())
                    continue

                self._send_query(cmd_line)

            except KeyboardInterrupt:
                print("\n[!] Przerwano. Wpisz WYJDŹ, aby zamknąć bezpiecznie tunel.")
            except (ConnectionResetError, BrokenPipeError):
                print("\n[!] Utracono bezpieczne połączenie z serwerem.")
                break
            except EOFError:
                break
                
        if self.sock:
            self.sock.close()

    def _send_query(self, query: str) -> list:
        try:
            req_blob = json.dumps({"query": query}, ensure_ascii=False).encode('utf-8')
            enc_req = encrypt_rpc_request(self.crypto, _compress(req_blob), self._hsl_link)
            _send_frame(self.sock, enc_req)

            enc_resp = _recv_frame(self.sock)
            if not enc_resp:
                raise ConnectionResetError()

            raw_resp = _decompress(
                decrypt_rpc_response(self.crypto, enc_resp, self._hsl_link)
            )
            res_data = json.loads(raw_resp.decode('utf-8'))

            results, transport_err = parse_response_payload(res_data)
            if transport_err:
                print(f"[Błąd transportu]: {transport_err}")
                return []

            for w in results:
                self._render_output(w)
            return results

        except socket.timeout:
            print(f"[!] Błąd tunelu: brak odpowiedzi w ciągu {RPC_TIMEOUT_SEC}s.")
            self.running = False
            return []
        except Exception as e:
            print(f"[!] Błąd tunelu: {e}")
            self.running = False
            return []

    def _render_output(self, w: dict):
        if w.get("status") == "error":
            action = w.get("action", "ERROR")
            line = w.get("line")
            msg = w.get("message", "nieznany błąd")
            if line is not None:
                print(f" > [{action} L{line}]: {msg}")
            else:
                print(f" > [{action}]: {msg}")
            return

        action = w.get("action", "OK")

        if action == "FIND_WHERE" or action == "SEARCH" or action == "FIND_REL":
            matches = w.get("matches", [])
            print(f"\n[{action}] Znaleziono obiektów: {len(matches)}")
            if matches:
                self._print_table(["Nazwa Bąbla"], [[m] for m in matches])

        elif action == "PROJECT":
            data = w.get("data", {})
            print(f"\n[PROJEKCJA] Bąbel: {w.get('target')}")
            self._print_table(["Cecha", "Wartość"], [[k, str(v)] for k, v in data.items()])

        elif action == "SHOW":
            props = w.get("data", {}).get("properties", {})
            rels = w.get("data", {}).get("relations", [])
            print(f"\n[WNĘTRZE] Bąbel: {w.get('target')}")
            if props:
                self._print_table(["Atrybut", "Wartość"], [[k, str(v)] for k, v in props.items()])
            if rels:
                self._print_table(["Relacja", "Cel"], [[r['relation'], r['target']] for r in rels])

        elif action.startswith("AGGREGATE_"):
            res = w.get("result")
            if isinstance(res, dict):
                print(f"\n[{action}] Grupowanie po: '{w.get('group_by')}'")
                self._print_table([w.get("group_by", "Grupa"), "Wynik"], [[k, str(v)] for k, v in res.items()])
            else:
                print(f"\n[{action}] Globalnie dla '{w.get('key')}': {res}")

        elif action == "HISTORY":
            data = w.get("data", [])
            print(f"\n[HISTORIA] Cecha '{w.get('key')}' w '{w.get('target')}'")
            self._print_table(["Status", "Wartość", "Timestamp"], [[d['status'], str(d['value']), d['timestamp']] for d in data])
        
        elif action == "ASSIGN":
            print(f" > [ZMIENNA]: Utworzono {w.get('variable')} (elementów: {len(w.get('value', []))})")

        else:
            target = w.get("target", w.get("count", w.get("deleted_count", "")))
            print(f" > [{action}]: {target}")

    def _print_table(self, headers: list, rows: list):
        if not rows: return
        cols_width = [max(len(str(item)) for item in col) for col in zip(*rows, headers)]
        format_str = " | ".join([f"{{:<{w}}}" for w in cols_width])
        sep = "-" * (sum(cols_width) + 3 * len(headers) - 1)
        print(sep)
        print(format_str.format(*headers))
        print(sep)
        for row in rows:
            print(format_str.format(*[str(i) for i in row]))
        print(sep + "\n")

    def _handle_chart(self, query: str):
        print(f"[WYKRES] Pobieranie danych (Secure Tunnel): {query}")
        results = self._send_query(query)
        if not results: return
        last_result = results[-1]
        if not last_result.get("action", "").startswith("AGGREGATE_"):
            print("[!] Komenda WYKRES działa tylko z zapytaniami agregującymi.")
            return
        data = last_result.get("result")
        if not isinstance(data, dict):
            print("[!] Aby narysować wykres, zapytanie musi zawierać POGRUPUJ.")
            return
        try:
            import plotly.express as px
            import pandas as pd
            x_label = last_result.get("group_by", "Grupa")
            y_label = last_result.get("key", "Wartość")
            action = last_result.get("action")
            df = pd.DataFrame(list(data.items()), columns=[x_label, y_label])
            df = df.sort_values(by=y_label, ascending=False)
            fig = px.bar(df, x=x_label, y=y_label, title=f"Cynober DB: {action} {y_label} po {x_label}", template="plotly_dark")
            fig.show()
            print("[WYKRES] Otwarto w przeglądarce.")
        except ImportError:
            print("\n[!] Błąd: Brakuje bibliotek (plotly pandas).")

    def _show_help(self):
        mode = (self._crypto_mode or "?").upper()
        tunel = mode
        if self._hsl_link:
            tunel += " + HSL"
        if os.environ.get("KARM_PSK"):
            tunel += " + PSK"
        if self._hsl_link and self._hsl_link.qkd_hybrid:
            tunel += " + QKD"

        print("\n" + "=" * 60)
        print("  CYNOBER DB — KarminQL v4.8 | Klient v1.8.0")
        print(f"  Połączenie: {self.host}:{self.port}  |  Protokół: {PROTO_VERSION}")
        if self.sock and self._crypto_mode:
            print(f"  Aktywny tunel: {tunel}")
        else:
            print("  Tunel: niepołączony")
        print("  Pełny podręcznik: cynober_manual.md")
        print("=" * 60)

        print("\n[Bezpieczeństwo tunelu — zmienne środowiskowe]")
        print("  (ustaw PRZED uruchomieniem serwera i klienta; obie strony muszą być zgodne)")
        print("  KARM_PSK          — hasło sieci (opcjonalne)")
        print("  KARM_QKD_SEED     — seed hybrydy QKD+HSL / symulacja splątania (opcjonalne)")
        print("  KARM_PHI2          — tożsamość węzła Φ² (domyślnie ~/.karmazyn_phi2)")
        print("  KARM_HSL_EPOCH_SEC — długość epoki HSL w sekundach (domyślnie 3600)")
        print("  Negocjacja krypto: hss → ecdh → simple (auto)")

        print("\n[Połączenie z serwerem]")
        print("  python cynober_konfigurator.py     — klient (1–5) + serwer (6–9)")
        print("  python Cynober_db.py IP [port]     — jednorazowo bez profilu")
        print("  python Cynober_db.py --profile NAZWA")
        print(f"  Plik profili: ~/.karmazyn_client.json")

        print("\n[Polecenia powłoki klienta]")
        print("  POMOC / HELP     — ten podręcznik")
        print("  WYJDŹ / EXIT     — zamknij tunel i wyjdź")
        print("  WYKRES <zapytanie> — wykres słupkowy (wymaga plotly + pandas)")

        print("\n[Polecenia serwera (poza KarminQL)]")
        print("  STATYSTYKI              — atomy hot/cold/reaped, liczba bąbli")
        print("  TICK [n]                — n cykli termodynamicznych (domyślnie 1)")
        print("  ZAPISZ [plik.kafd]      — zapis zrzutu (domyślnie zrzut_cynober.kafd)")
        print("  WCZYTAJ [plik.kafd]     — wczytanie zrzutu")

        print("\n[Typy danych]")
        print("  Tekst: \"wartość\"  |  Liczba: 1024, 3.14  |  Logiczne: PRAWDA, FAŁSZ  |  Puste: NIC")

        print("\n[Przestrzenie nazw]  (domyślna: DEFAULT)")
        print("  UTRWAL PRZESTRZEŃ \"Nazwa\"")
        print("  WYBIERZ PRZESTRZEŃ \"Nazwa\"")

        print("\n[CRUD — bąble i cechy]")
        print("  UTRWAL \"Bąbel\"")
        print("  USUŃ BĄBEL \"Bąbel\"")
        print("  WSTRZYKNIJ \"Cecha\" = Wartość DO \"Bąbel\"")
        print("  ZAKTUALIZUJ \"Cecha\" = +50 W \"Bąbel\"   (względna aktualizacja liczb)")
        print("  USUŃ \"Cecha\" Z \"Bąbel\"")

        print("\n[Podgląd i wyszukiwanie]")
        print("  POKAŻ \"Bąbel\"")
        print("  WYPISZ \"C1\", \"C2\" Z \"Bąbel\"")
        print("  HISTORIA \"Cecha\" W \"Bąbel\"")
        print("  SZUKAJ \"fraza\"              (rezonans HRR — wymaga numpy)")
        print("  ZNAJDŹ GDZIE \"Cecha\" = Wartość [ORAZ|LUB \"Cecha2\" > 10]")
        print("  POLICZ BĄBLE GDZIE ...")
        print("  USUŃ BĄBLE GDZIE ...")
        print("  Operatory: = != > < >= <= ZAWIERA W NIE W")

        print("\n[Zmienne skryptowe]")
        print("  NIECH $zmienna = ZNAJDŹ GDZIE \"Typ\" = \"Serwer\"")
        print("  ZNAJDŹ GDZIE \"BĄBEL\" W $zmienna ORAZ \"RAM\" > 1024")

        print("\n[Modyfikatory]  (na końcu zapytania)")
        print("  SORTUJ WEDŁUG \"Cecha\" [MALEJĄCO|ROSNĄCO]  |  LIMIT n  |  PRZESUNIĘCIE n")

        print("\n[Agregacje]")
        print("  SUMA|ŚREDNIA|MIN|MAX \"Cecha\" GDZIE ... [POGRUPUJ \"Cecha\"]")
        print("  WYKRES SUMA \"RAM\" GDZIE \"Typ\" != \"NIC\" POGRUPUJ \"Typ\"")

        print("\n[Graf i propagacja ciepła]")
        print("  POŁĄCZ \"A\" Z \"B\" JAKO \"Relacja\"")
        print("  ROZŁĄCZ \"A\" Z \"B\" JAKO \"Relacja\"")
        print("  ZNAJDŹ POŁĄCZONE JAKO \"syn\" Z \"Dziecko\"")
        print("    (po POŁĄCZ \"Rodzic\" Z \"Dziecko\" JAKO \"syn\" zwraca Rodzic)")
        print("  WZBUDŹ \"Start\" ENERGIĄ 100 [PO RELACJI \"Relacja\"]")
        print("  ZNAJDŹ GDZIE \"TEMPERATURA\" > 1 SORTUJ WEDŁUG \"TEMPERATURA\" MALEJĄCO")

        print("\n[Transakcje]")
        print("  BEGIN  →  ...polecenia...  →  COMMIT | ROLLBACK")
        print("  Skrypt wieloliniowy jest auto-transakcyjny (rollback przy błędzie).")

        print("=" * 60 + "\n")

if __name__ == "__main__":
    from cynober_client_config import resolve_client_target

    host, port, profile = resolve_client_target()
    if profile not in ("(argv)", "(env)"):
        print(f"[Cynober] Profil: {profile} → {host}:{port}")
    client = CynoberClient(host=host, port=port)
    client.run()