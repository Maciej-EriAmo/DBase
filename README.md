# Cynober DB / KarmazynOS

Relacyjno-grafowa baza danych na termodynamicznym rdzeniu **KarmazynOS**, z transportem **Cynober-Secure-1.2** i warstwą **HSL** (Holographic Session Links). Telefon w Termux może gadać z serwerem na PC; zapytania **KarminQL** lecą po tunelu chronionym **Ring-LWE (HSS)** i **HSL** — splątanie jako certyfikat sesji, nie tylko szyfrowanie payloadu.

> *Ruch TCP widać, ale payload to szum. Transport jest **post-quantum oriented**: Ring-LWE (HSS) na handshake + **HSL** wiąże sesję (Φ², epoka, PrismMask, opcjonalny QKD-seed). Nie planujemy równoległego HTTP/REST — jeden protokół, więcej klientów na tym samym wire. **N=15** w HSS to prototyp (Termux); KEM skaluje się bez ruszania RPC.*

## Wersje (stan repozytorium)

| Komponent | Wersja | Plik |
|-----------|--------|------|
| KarminQL | v6.9 | `cynober_query_engine.py` |
| Serwer RPC | v7.6 | `cynober_server.py` |
| Klient SDK | v7.6 | `cynober_client.py` |
| Klient CLI | v1.8.0 | `Cynober_db.py` |
| Protokół | Cynober-Secure-1.2 | `cynober_rpc.py` |
| GameStore (adapter aplikacyjny) | — | `game_store.py` |

Pełna składnia i API: [`cynober_manual.md`](cynober_manual.md)

## Trzy filary

| Filament | Rola |
|----------|------|
| **KarmazynOS** | Termodynamiczny silnik pamięci — atomy, bąble, reach-GC |
| **Cynober / KarminQL** | Baza i język zapytań (relacyjno-grafowy model) |
| **Protokół Karmazyn** | Jedyny transport: HSS (Ring-LWE KEM) → HSL (sesja post-kwantowa, AAD, QKD) → Cynober-RPC |

## Do czego to służy

| Scenariusz | Jak | Gotowość |
|------------|-----|----------|
| **Analityka / ETL** | KarminQL + `read_karmin()` → pandas, CSV, `.kafd` | ★★★★☆ |
| **Zdalny dostęp (sandbox)** | CLI lub własny klient RPC przez tunel HSS+HSL | ★★★★☆ |
| **Gry / pamięć narracyjna** | `GameStore` + demo — NPC, questy, graf, termodynamika | ★★★★★ |
| **Wspólna baza zespołu** | Światy v7.1 + auth v7.2 + ops v7.3 + replikacja v7.4 + SDK v7.6 | ★★★★☆ |
| **CRPG hack and slash** | `crpg_world_setup.py` — seed świata gry na trwałym serwerze | ★★★★★ |

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

# klient CLI (drugi terminal)
python Cynober_db.py

# klient SDK (Python)
python examples/team_connect.py

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

# seed świata CRPG (bohater, potwory, loot, questy)
python examples/crpg_world_setup.py --world dungeon --create-world
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

Stan: **283 testów** (kernel, KarminQL v6.0–v6.9, HSS, HSL, RPC, sesje v7.0–v7.6, GameStore, `cynober_client`).

## Status i ograniczenia

Projekt jest w **fazie użytkowej dla early adopterów** — działa end-to-end, ma testy i warstwę aplikacyjną. To nadal **prototyp**, nie produkcyjna baza zespołowa.

**Co działa:**
- KarminQL v6.9 z rozbudowanym dialektem SQL-owym (JOIN, CTE, okna, JSON, EXPLAIN, indeksy)
- Serwer v7.6: **izolacja sesji** + **trwałe światy** + **auth/role** + **operacje** + **replikacja** + **SDK klienta** (`cynober_client.py`)
- Tunel HSS + HSL + opcjonalny PSK/QKD-seed
- Trwałość plikowa: `ZAPISZ` / `WCZYTAJ` (`.kafd`) w ramach sesji
- Integracja pandas, CSV, `GameStore` dla gier i prototypów

**Czego brakuje do pracy zawodowej w zespole:**
- Publikacja **pakietu PyPI** — dziś: `cynober_client.py` + CLI + `GameStore`
- **Profile HSS w produkcji** — `KARM_HSS_PROFILE=standard|production` (domyślnie `proto` N=15); paper [v2.5](https://github.com/Maciej-EriAmo/holonOs/blob/main/HSS_Paper_v2.5.0_PL.md)
- PSK/QKD to hasło sieci; konta użytkowników wymagają `auth.json` na serwerze
- Metadane TCP widoczne; częściowa ochrona DoS (rate limit)

## Mapa drogowa (jeden protokół)

**Zasada:** jeden wire — **Cynober-Secure-1.2** (TCP + HSS + HSL + KarminQL). Bez równoległego REST/ODBC/HTTP — kolejne porty i dialekty rozproszyłyby adopcję; zamiast tego **więcej cienkich klientów** na tym samym tunelu.

| Wersja | Kierunek | Cel |
|--------|----------|-----|
| v7.4 ✓ | Replikacja światów | HA-lite: `PULL`/`PUSH`/`SYNC`, `peers.json` |
| v7.5 ✓ | Bezpieczeństwo (część) | Profile HSS `proto`/`standard`/`production`, negocjacja w handshake |
| **v7.6** ✓ | **Klient SDK** | `cynober_client.py`, `examples/team_connect.py`, profil `hss_profile` w konfiguratorze |
| v7.5+ | Bezpieczeństwo (reszta) | Adapter QKD, capability tokens, rotacja epoki, NTT |
| v7.7+ | Gossip pełny | Synchronizacja BubbleVFS / phi-space (`karmazyn_gossip.py`) — nadal po RPC |

**Dlaczego nie REST:** HTTP dałby znajome narzędzia, ale drugi silnik transportu i gorsze wykorzystanie HSL. Produktem jest **Cynober end-to-end** — post-quantum oriented tunnel + KarminQL + światy, nie „JSON API obok”.

## Szukam współpracy

Stack jest warstwowy — można wnieść kawałek bez znajomości całości. Przydatne obszary:

- **Bezpieczeństwo v7.5** — profile HSS N=128/512, NTT, adapter QKD
- **Klient SDK** — PyPI, opcjonalnie Go/TypeScript na tym samym handshake
- **Gossip** — `karmazyn_gossip.py`, synchronizacja BubbleVFS po istniejącym tunelu

Jeśli chcesz dołączyć — issue, PR albo kontakt przez profil GitHub.

## Licencja

Kod w tym repozytorium (Cynober DB, KarminQL, warstwa HSS/HSL, pliki `karmazyn_*` i `cynober_*`) jest na licencji **[MIT](LICENSE)**.

Dokumentacja specyfikacji HSL: [`HSL_Paper_v1_1_0_EN.md`](HSL_Paper_v1_1_0_EN.md) — **CC BY 4.0** (osobno od licencji kodu).

Rdzeń KarmazynOS pochodzi z ekosystemu [KarmazynOs](https://github.com/Maciej-EriAmo/KarmazynOs) — również na licencji MIT.