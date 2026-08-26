# Cynober DB / KarmazynOS

Relacyjno-grafowa baza danych na termodynamicznym rdzeniu **KarmazynOS**, z transportem **Cynober-Secure-1.2** i warstwą **HSL** (Holographic Session Links). Telefon w Termux może gadać z serwerem na PC; zapytania **KarminQL** lecą po tunelu chronionym **Ring-LWE (HSS)** i **HSL** — splątanie jako certyfikat sesji, nie tylko szyfrowanie payloadu.

> *Ruch TCP widać, ale payload to szum. Transport jest **post-quantum oriented**: Ring-LWE (HSS) na handshake + **HSL** wiąże sesję (Φ², epoka, PrismMask, opcjonalny QKD-seed). Nie planujemy równoległego HTTP/REST — jeden protokół, więcej klientów na tym samym wire. **N=15** w HSS to prototyp (Termux); KEM skaluje się bez ruszania RPC.*

## Wersje (stan repozytorium)

| Komponent | Wersja | Plik |
|-----------|--------|------|
| **Pakiet PyPI / serwer** | **8.2.2** | `pyproject.toml` · `cynober_ops.SERVER_VERSION` |
| Klient SDK | 8.2.2 | `cynober_client.py` (`session_info`, media KAFS) |
| Klient CLI | v1.8.0 | `Cynober_db.py` |
| Protokół wire | **Cynober-Secure-1.2** | `cynober_rpc.py` (numer protokołu ≠ numer pakietu) |
| HSL | HSL-1.1 | `karmazyn_hsl.py` (+ KPC bootstrap/epoch) |
| KPC / qpredict | 1.0 | `karmazyn_key_predict.py`, `karmazyn_qpredict.py` |
| KarminQL | v6.9 | `cynober_query_engine.py` |
| Jądro KarmazynOS | v1.1.0 | `karmazyn_kernel.py` / `karmazyn_substrate.py` |
| Substrat Rust (DB_karmin) | 0.1.0-karmazyn-substrate | `native/` + `karmazyn_backend.py` |
| GameStore | — | `game_store.py` |
| KAFD / KAFX | v2.1 + journal | `karmazyn_kafd.py`, `karmazyn_cipher.py`, `karmazyn_thermal.py` · [docs/KAFD.md](docs/KAFD.md) |

Pełna składnia i API: [`cynober_manual.md`](cynober_manual.md) · KAFD / KAFX / klatki: [`docs/KAFD.md`](docs/KAFD.md) · sesja L0/KPC: [`docs/SESSION_L0_KPC.md`](docs/SESSION_L0_KPC.md) · historia: [`CHANGELOG.md`](CHANGELOG.md)

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
| **Wspólna baza zespołu** | Światy v7.1 + auth + ops + replikacja manifest-first + shardy v8.0 | ★★★★☆ |
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
- **Lazy unfold v7.9** — przy `WYBIERZ ŚWIAT` ładowany jest manifest; `ROZWIJ` / `CEL` dociąga payload z dysku w promieniu grafu
- **Shardy v8.0** — COLD payloady per region grafu `rel:*`; replikacja manifest-first (`EKSPORT MANIFEST`, `PULL SHARD`)
- Rezonans HSL — ramka bez właściwego stanu sesji kończy się błędem GCM (kolaps do szumu)
- Weryfikacja `qkd_fp` przy rozjazdzie seeda QKD
- AAD na ramkach RPC, anty-replay (`session_id`, `ts`)
- Φ² — trwała tożsamość węzła (`~/.karmazyn_phi2`), nie sekret w handshake

## Szybki start

Wymagania: **Python 3.10+**.

### Z PyPI (zalecane dla zespołu)

```bash
pip install -U "cynober-db>=8.2.2"
```

**Start serwera** (Windows: jeśli `cynober-server` nie jest w PATH — normalne przy Python Store):

```bash
python -m cynober_server          # zalecane, zawsze działa
# albo, gdy Scripts w PATH:
cynober-server
cynober-cli
cynober-konfigurator
```

Katalog `Scripts` (np. `%LOCALAPPDATA%\Python\pythoncore-3.14-64\Scripts`) musi być w **User PATH**, inaczej PowerShell nie znajdzie `cynober-server.exe`.

Opcjonalnie analityka i wykresy: `pip install "cynober-db[viz]"`.

### Z repozytorium (dev)

```bash
git clone https://github.com/Maciej-EriAmo/DBase.git && cd DBase
pip install -e ".[dev]"
python -m unittest discover -s tests -q
python -m cynober_server
```

### Publikacja (maintainer)

```powershell
pip install build twine
# Token PyPI: https://pypi.org/manage/account/token/
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "pypi-AgEI..."   # jednorazowo w sesji
.\scripts\publish_pypi.ps1
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

### Trwałość światów (v8.0)

Pliki `.kafd` na dysku to koperta **KAFX** (AES-GCM, klucz świata). Szczegóły: [`docs/KAFD.md`](docs/KAFD.md). Dane: `%LOCALAPPDATA%\Cynober\worlds\` (albo `CYNOBER_DATA_HOME`).

```
worlds/
  rivendell.kafd              # manifest KAFX (nagłówki + bąble + HOT)
  rivendell.meta.json         # indeksy zapytań, shard_index, folded_atoms
  shards/rivendell/
    index.json                # mapa region → bąble, atomy, plik
    region_0.kafd             # payload COLD danego regionu grafu
  proca/rivendell/*.pfld      # deduplikacja semantyczna COLD
  backups/…                   # kopie (kafd + meta + shards + proca)
  peers.json                  # węzły replikacji
```

Szczegóły: [`cynober_manual.md`](cynober_manual.md) · KAFD: [`docs/KAFD.md`](docs/KAFD.md) · specyfikacja HSL: [`HSL_Paper_v1_1_0_EN.md`](HSL_Paper_v1_1_0_EN.md) · sesja L0/KPC: [`docs/SESSION_L0_KPC.md`](docs/SESSION_L0_KPC.md)

## Testy

```bash
python -m unittest discover -s tests -v
# lub
python -m pytest tests/ -q
```

Stan: **322 testy** (kernel v1.1, KarminQL v6.0–v6.9, HSS/NTT, HSL, RPC+cap, sesje v7.0–v8.0, lazy unfold, shardy, GameStore).

## Status i ograniczenia

Projekt jest w **fazie użytkowej dla early adopterów** — działa end-to-end, ma testy i warstwę aplikacyjną. To nadal **prototyp**, nie produkcyjna baza zespołowa.

**Co działa:**
- KarminQL v6.9 z rozbudowanym dialektem SQL-owym (JOIN, CTE, okna, JSON, EXPLAIN, indeksy)
- **Jądro v1.1.0:** reach-GC, `retained_tomb`, dual-emit tick (`both`/`batch`/`per_atom`), publiczne API bez `Store.reg`
- Serwer v8.0: **izolacja sesji** + **trwałe światy** + **auth/role** + **ops** + **replikacja manifest-first** + **shardy KAFD** + **lazy unfold**
- Pakiet PyPI [`cynober-db`](https://pypi.org/project/cynober-db/) **8.2.2** (KPC/HSL, `session_info`, media KAFS)
- Tunel HSS + HSL + opcjonalny PSK/QKD-seed
- Trwałość: `ZAPISZ ŚWIAT`, auto-flush co 60s, kopie zapasowe z `shards/` i `proca/`
- Integracja pandas, CSV, `GameStore` dla gier i prototypów

**Czego brakuje do pełnej produkcji:**
- **Profile HSS w produkcji** — `KARM_HSS_PROFILE=standard|production` (domyślnie `proto` N=15); paper [v2.5](https://github.com/Maciej-EriAmo/holonOs/blob/main/HSS_Paper_v2.5.0_PL.md)
- PSK/QKD to hasło sieci; konta użytkowników wymagają `auth.json` na serwerze
- Metadane TCP widoczne; częściowa ochrona DoS (rate limit)

## Mapa drogowa (jeden protokół)

**Zasada:** jeden wire — **Cynober-Secure-1.2** (TCP + HSS + HSL + KarminQL). Bez równoległego REST/ODBC/HTTP — kolejne porty i dialekty rozproszyłyby adopcję; zamiast tego **więcej cienkich klientów** na tym samym tunelu.

| Wersja | Kierunek | Cel |
|--------|----------|-----|
| v7.4 ✓ | Replikacja światów | HA-lite: `PULL`/`PUSH`/`SYNC`, `peers.json` |
| v7.5 ✓ | Bezpieczeństwo | Profile HSS, QKD adapter, capability tokens, rotacja epoki, NTT |
| v7.6 ✓ | Klient SDK | `cynober_client.py`, `team_connect.py` |
| v7.7 ✓ | Pro | Gossip phi-space, PyPI, ZDROWIE z metadanymi HSS/QKD |
| v7.8 ✓ | Persystencja | Auto-flush dirty, `inv_index`/`atom_index` w meta, Proca COLD |
| v7.9 ✓ | Lazy load | Manifest przy `WYBIERZ ŚWIAT`, `ROZWIJ` / `CEL` + promień grafu |
| **v8.0** ✓ | **Shardy** | Regiony grafu → `shards/<świat>/`; replikacja manifest-first |
| **v8.1** ✓ (slice) | Gossip SOUL | `GOSSIP EKSPORT/IMPORT/SYNC SOUL` — bąble+bindings+atomy (BubbleVFS-lite); pełne `.soul`/Proca — dalej |

**Dlaczego nie REST:** HTTP dałby znajome narzędzia, ale drugi silnik transportu i gorsze wykorzystanie HSL. Produktem jest **Cynober end-to-end** — post-quantum oriented tunnel + KarminQL + światy, nie „JSON API obok”.

## Szukam współpracy

Stack jest warstwowy — można wnieść kawałek bez znajomości całości. Przydatne obszary:

- **Bezpieczeństwo v7.5** — profile HSS N=128/512, NTT, adapter QKD
- **Klient SDK** — bindingi Go/TypeScript na tym samym handshake (PyPI ✓)
- **Gossip SOUL** — `cynober_gossip.py` (PHI + SOUL); pełne pliki `.soul`/Proca COLD po RPC

Jeśli chcesz dołączyć — issue, PR albo kontakt przez profil GitHub.

## Licencja

Kod w tym repozytorium (Cynober DB, KarminQL, warstwa HSS/HSL, pliki `karmazyn_*` i `cynober_*`) jest na licencji **[MIT](LICENSE)**.

Dokumentacja specyfikacji HSL: [`HSL_Paper_v1_1_0_EN.md`](HSL_Paper_v1_1_0_EN.md) — **CC BY 4.0** (osobno od licencji kodu).

Rdzeń KarmazynOS pochodzi z ekosystemu [KarmazynOs](https://github.com/Maciej-EriAmo/KarmazynOs) — również na licencji MIT.