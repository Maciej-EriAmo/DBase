# Cynober DB / KarmazynOS

Relacyjno-grafowa baza danych na termodynamicznym rdzeniu **KarmazynOS**, z transportem **Cynober-Secure-1.2** i warstwą **HSL** (Holographic Session Links). Telefon w Termux może gadać z serwerem na PC; zapytania **KarminQL** lecą po tunelu chronionym **Ring-LWE (HSS)** i **HSL** — splątanie jako certyfikat sesji, nie tylko szyfrowanie payloadu.

> *Ruch TCP widać, ale payload to szum. HSL nie zastępuje TLS — wiąże kontekst sesji (Φ², epoka, PrismMask). Slot `KARM_QKD_SEED` to dziś symulacja hybrydy z siecią kwantową; jutro ten sam punkt w KDF, inne źródło. **N=15** w HSS to świadomy prototyp (lekkość, Termux) — KEM skaluje się bez ruszania RPC.*

## Wersje (stan repozytorium)

| Komponent | Wersja | Plik |
|-----------|--------|------|
| KarminQL | v6.9 | `cynober_query_engine.py` |
| Serwer RPC | v7.1 | `cynober_server.py` |
| Klient CLI | v1.8.0 | `Cynober_db.py` |
| Protokół | Cynober-Secure-1.2 | `cynober_rpc.py` |
| GameStore (adapter aplikacyjny) | — | `game_store.py` |

Pełna składnia i API: [`cynober_manual.md`](cynober_manual.md)

## Trzy filary

| Filament | Rola |
|----------|------|
| **KarmazynOS** | Termodynamiczny silnik pamięci — atomy, bąble, reach-GC |
| **Cynober / KarminQL** | Baza i język zapytań (relacyjno-grafowy model) |
| **Protokół Karmazyn** | HSS (Ring-LWE KEM) → HSL (sesja, AAD, opcjonalnie QKD) → Cynober-RPC |

## Do czego to służy

| Scenariusz | Jak | Gotowość |
|------------|-----|----------|
| **Analityka / ETL** | KarminQL + `read_karmin()` → pandas, CSV, `.kafd` | ★★★★☆ |
| **Zdalny dostęp (sandbox)** | CLI lub własny klient RPC przez tunel HSS+HSL | ★★★★☆ |
| **Gry / pamięć narracyjna** | `GameStore` + demo — NPC, questy, graf, termodynamika | ★★★★★ |
| **Wspólna baza zespołu** | Trwałe światy v7.1 (`WYBIERZ ŚWIAT`); auth per użytkownik — planowane | ★★★☆☆ |

## Co widać w demo, a co nie

**Na filmiku / screencascie:**
- `cynober_konfigurator.py` — profil klienta i serwera, firewall, rate limit
- Połączenie Termux → PC, komunikat `Tunel zabezpieczony (HSS + HSL + QKD)`
- KarminQL na żywo: `UTRWAL`, `WYPISZ`, `ZAPISZ` do `.kafd`
- Odrzucenie floodu (rate limit połączeń i zapytań)
- `examples/game_memory_demo.py` — pamięć gry przez serwer v7.0

**Poza kadrem (warto przeczytać opis / manual):**
- **Izolacja sesji v7.0** — domyślnie każde połączenie = pusty sandbox; **trwałe światy v7.1** — `WYBIERZ ŚWIAT` współdzieli stan między klientami i przetrwa reconnect
- Rezonans HSL — ramka bez właściwego stanu sesji kończy się błędem GCM (kolaps do szumu)
- Weryfikacja `qkd_fp` przy rozjazdzie seeda QKD
- AAD na ramkach RPC, anty-replay (`session_id`, `ts`)
- Φ² — trwała tożsamość węzła (`~/.karmazyn_phi2`), nie sekret w handshake

## Szybki start

Wymagania: **Python 3.10+**. Rdzeń i KarminQL działają na samym stdlib.

```bash
# opcjonalnie — pełny stos kryptograficzny i pandas
pip install -r requirements.txt

# serwer
python cynober_server.py

# klient (drugi terminal)
python Cynober_db.py

# konfigurator profili (Termux / PC)
python cynober_konfigurator.py
```

Opcjonalne zmienne (obie strony muszą się zgadzać):

```powershell
$env:KARM_PSK = "haslo-sieci"
$env:KARM_QKD_SEED = "seed-hybrydowy-qkd"
```

### Przykłady aplikacyjne

```bash
# analityka lokalna (pandas + JOIN)
python examples/analyst_demo.py

# pamięć gry przez serwer RPC (domyślnie profil z ~/.karmazyn_client.json)
python cynober_server.py          # terminal 1
python examples/game_memory_demo.py   # terminal 2

# to samo bez serwera (silnik in-process)
python examples/game_memory_demo.py --local

# demo na trwałym świecie serwera (współdzielony stan między sesjami)
python examples/game_memory_demo.py --world rivendell --create-world
```

## Architektura (skrót)

```
Cynober_db.py / GameStore  ◄── TCP :8080, Cynober-Secure-1.2 ──►  cynober_server.py
         │                              (1 sesja = 1 Store)              │
         └── HSS/ECDH → PSK? → HSL link → RPC+AAD ──────────────────────┘
                                        │
                                        ▼
                              KarminQL v6.9 → karmazyn_kernel
```

Szczegóły: [`cynober_manual.md`](cynober_manual.md) · specyfikacja HSL: [`HSL_Paper_v1_1_0_EN.md`](HSL_Paper_v1_1_0_EN.md)

## Testy

```bash
python -m unittest discover -s tests -v
# lub
python -m pytest tests/ -q
```

Stan: **257 testów** (kernel, KarminQL v6.0–v6.9, HSS, HSL, RPC, sesje v7.0, światy v7.1, GameStore).

## Status i ograniczenia

Projekt jest w **fazie użytkowej dla early adopterów** — działa end-to-end, ma testy i warstwę aplikacyjną. To nadal **prototyp**, nie produkcyjna baza zespołowa.

**Co działa:**
- KarminQL v6.9 z rozbudowanym dialektem SQL-owym (JOIN, CTE, okna, JSON, EXPLAIN, indeksy)
- Serwer v7.1: **izolacja sesji** (sandbox) + **trwałe światy** (`LISTA ŚWIATÓW`, `WYBIERZ/UTWÓRZ ŚWIAT`, zapis `.kafd` w `~/.cynober_worlds`)
- Tunel HSS + HSL + opcjonalny PSK/QKD-seed
- Trwałość plikowa: `ZAPISZ` / `WCZYTAJ` (`.kafd`) w ramach sesji
- Integracja pandas, CSV, `GameStore` dla gier i prototypów

**Czego brakuje do pracy zawodowej w zespole:**
- Auth per użytkownik i role (światy są współdzielone, ale bez kont)
- Uwierzytelnienie **per użytkownik** (dziś: PSK/QKD = hasło sieci)
- REST/ODBC, metryki operacyjne, HA — poza zakresem obecnej wersji
- HSS domyślnie **N=15, Q=256** — podnoszenie parametrów w `karmazyn_hss.py`
- Metadane TCP widoczne; częściowa ochrona DoS (rate limit)

## Szukam współpracy

Stack jest warstwowy — można wnieść kawałek bez znajomości całości. Przydatne obszary:

- **Auth i role** na trwałych światach v7.1
- **NTT / N=256** w `karmazyn_hss.py` (Kyber-class KEM)
- **Adapter QKD** zamiast `KARM_QKD_SEED` (ten sam KDF, inne źródło)
- **Hardening** — TLS overlay, capability tokens, rotacja epoki w locie, auth per użytkownik
- **Gossip / replikacja** — `karmazyn_gossip.py`, synchronizacja BubbleVFS

Jeśli chcesz dołączyć — issue, PR albo kontakt przez profil GitHub.

## Licencja

Kod w tym repozytorium (Cynober DB, KarminQL, warstwa HSS/HSL, pliki `karmazyn_*` i `cynober_*`) jest na licencji **[MIT](LICENSE)**.

Dokumentacja specyfikacji HSL: [`HSL_Paper_v1_1_0_EN.md`](HSL_Paper_v1_1_0_EN.md) — **CC BY 4.0** (osobno od licencji kodu).

Rdzeń KarmazynOS pochodzi z ekosystemu [KarmazynOs](https://github.com/Maciej-EriAmo/KarmazynOs) — również na licencji MIT.