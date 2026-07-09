# Cynober DB — Podręcznik Użytkownika i Składnia KarminQL (v6.9)

Cynober DB to relacyjno-grafowa baza danych na termodynamicznym rdzeniu **KarmazynOS**, z transportem **Cynober-Secure-1.2** i warstwą **HSL** (Holographic Session Links). Trzy autorskie elementy — silnik, baza, protokół — opierają się na jednej zasadzie: **struktura wynika z rezonansu stanu sesji**, a nie z zewnętrznych etykiet (adres, certyfikat, ACL).

| Komponent | Wersja | Plik |
|-----------|--------|------|
| KarminQL (silnik zapytań) | v6.9 | `cynober_query_engine.py` |
| Most pandas | — | `cynober_pandas_bridge.py` |
| Klient CLI | v1.8.0 | `Cynober_db.py` |
| Serwer | v7.3 | `cynober_server.py` |
| Protokół transportu | Cynober-Secure-1.2 | `cynober_rpc.py` |
| HSL (sesje sieciowe) | HSL-1.1 | `karmazyn_hsl.py` |
| Handshake / szyfrowanie | KSH-1.2 | `karmazyn_handshake.py` |
| Ring-LWE (HSS KEM) | v1.0 | `karmazyn_hss.py` |
| Jądro KarmazynOS | v1.0.0 | `karmazyn_kernel.py` |
| Specyfikacja HSL (paper) | v1.1.0 | `HSL_Paper_v1_1_0_EN.md` |
| GameStore (adapter aplikacyjny) | — | `game_store.py` |

---

## 1. Architektura

```
┌─────────────────┐        TCP :8080         ┌──────────────────┐
│  Cynober_db.py  │ ◄──────────────────────► │ cynober_server.py│
│  (klient CLI)   │   Cynober-Secure-1.2     │  (serwer RPC)    │
└────────┬────────┘                          └────────┬─────────┘
         │                                            │
         │  HSS/ECDH → PSK? → HSL link → RPC+AAD      │
         │  (karmazyn_hsl.py — Φ², PrismMask, QKD?)   │
         │                                            │
         └──────────────────┬─────────────────────────┘
                            ▼
                   ┌─────────────────┐
                   │  KarminEngine   │  KarminQL v6.9
                   └────────┬────────┘
                            ▼
                   ┌─────────────────┐
                   │ karmazyn_kernel │  atomy, bąble, T, reach-GC
                   └─────────────────┘
```

### Jedna zasada — trzy warstwy

| Poziom | Komponent | Zasada rezonansu |
|--------|-----------|------------------|
| Pamięć | **KarmazynOS** | Dane „żyją” termodynamicznie; nieosiągalne zimne atomy znikają (reach-GC). |
| Zapytania | **Cynober / KarminQL** | Operacje na bąblach i grafie w tym samym modelu co jądro — nie na zewnętrznym schemacie SQL. |
| Sieć | **HSL** | Pakiet bez właściwego stanu sesji (Φ², kontekst, seed) nie daje sensownego JSON — odszyfrowanie kończy się błędem GCM (kolaps do szumu). |

### Warstwy

* **Klient** (`Cynober_db.py`) — interaktywna powłoka z tabelami i opcjonalnymi wykresami (`WYKRES` + plotly).
* **Serwer** (`cynober_server.py`) — nasłuch TCP na porcie **8080** (domyślnie `0.0.0.0`). Każde zapytanie to ramka JSON `{"query": "..."}` w tunelu HSL.
* **RPC** (`cynober_rpc.py`) — negocjacja wersji, anty-replay, handshake, faza HSL, kodeki ramek.
* **HSL** (`karmazyn_hsl.py`) — tożsamość Φ², PrismMask, AAD, slot `KARM_QKD_SEED` (hybryda z siecią kwantową).
* **Silnik zapytań** (`cynober_query_engine.py`) — parser i executor KarminQL.
* **Jądro** (`karmazyn_kernel.py` → `karmazyn_substrate.py` → `karmazyn_atom.py`) — atomy z temperaturą, bąble, reach-GC.
* **Trwałość** (`karmazyn_store.py`, `karmazyn_kafd.py`) — format pliku `.kafd`.
* **GameStore** (`game_store.py`) — cienki adapter nad KarminQL/RPC: NPC, questy, wyszukiwanie pamięci, termodynamika.

> **Uwaga:** Serwer **nie** udostępnia HTTP. Transport to wyłącznie TCP z protokołem Karmazyn (HSS + HSL).

### Izolacja sesji (v7.0) i trwałe światy (v7.1)

**Tryb domyślny (sandbox):** każde połączenie TCP dostaje **własny** efemeryczny `Store` + `KarminEngine`. Dane jednego klienta nie są widoczne dla innego. Po rozłączeniu stan jest zwalniany.

**Tryb świata (v7.1):** po `WYBIERZ ŚWIAT "nazwa"` sesja dołącza do **współdzielonego**, **trwałego** stanu zapisanego na dysku (`~/.cynober_worlds/nazwa.kafd`). Wiele klientów może pracować na tym samym świecie; po reconnect dane nadal są dostępne.

| Polecenie | Opis |
|-----------|------|
| `LISTA ŚWIATÓW` | Katalog światów (dysk + załadowane) |
| `UTWÓRZ ŚWIAT "nazwa"` | Nowy pusty świat (błąd gdy istnieje) |
| `WYBIERZ ŚWIAT "nazwa"` | Dołącz do świata (tworzy pusty, jeśli brak na dysku) |
| `ODŁĄCZ ŚWIAT` | Powrót do efemerycznego sandboxa |
| `ZAPISZ ŚWIAT` | Wymuszenie zapisu aktywnego świata |
| `USUŃ ŚWIAT "nazwa"` | Usuwa pliki świata (gdy brak aktywnych sesji) |

Katalog światów: zmienna `CYNOBER_WORLDS_DIR` lub domyślnie `~/.cynober_worlds/`.

### Auth na światach (v7.2)

Plik `{worlds_dir}/auth.json` z `"enabled": true` włącza kontrolę dostępu. Bez pliku lub z `enabled: false` — zachowanie jak v7.1 (otwarte światy).

| Polecenie | Opis |
|-----------|------|
| `ZALOGUJ "user" TOKEN "sekret"` | Logowanie sesji |
| `WYLOGUJ` | Wylogowanie |
| `KTO JESTEM` | user, world, role, auth_enabled |
| `NADAJ "user" ROLĘ "writer" W ŚWIECIE "nazwa"` | Nadanie roli (admin) |
| `ODEBIERZ "user" Z ŚWIATA "nazwa"` | Odebranie dostępu (admin) |
| `LISTA UPRAWNIEŃ` | Wszystkie ACL (admin globalny) |
| `LISTA UPRAWNIEŃ ŚWIATA "nazwa"` | ACL jednego świata |

**Role:** `reader` (odczyt), `writer` (+ zapis), `admin` (+ zarządzanie światem i ACL).

**ACL:** sekcja `"acl"` w `auth.json` — klucz `"*"` = globalnie, klucz nazwy świata = per świat.

Audyt: `{worlds_dir}/audit.log` (JSON lines) — zapis przy operacjach na świecie.

`STATYSTYKI` zwraca m.in. `session_isolated`, `world`, `auth_user`, `auth_role`, `auth_enabled`, `persistent_worlds`, `worlds_dir`.

### Operacje serwera (v7.3)

Metryki i kopie zapasowe trwałych światów. Kopie trafiają do `{worlds_dir}/backups/{świat}/{backup_id}/`.

| Polecenie | Opis | Auth (gdy włączone) |
|-----------|------|---------------------|
| `ZDROWIE` | Status serwera, wersja, uptime | publiczne |
| `METRYKI SERWERA` | Liczniki zapytań, sesji, światów, top_actions | publiczne |
| `KOPIA ZAPASOWA ŚWIATA "nazwa"` | Snapshot `.kafd` (+ meta) | writer+ w świecie |
| `LISTA KOPII ŚWIATA "nazwa"` | Katalog kopii | reader+ w świecie |
| `PRZYWRÓĆ ŚWIAT "nazwa" Z KOPII "id"` | Przywrócenie z kopii | admin w świecie |

`GameStore`: `health()`, `server_metrics()`, `backup_world()`, `list_backups()`, `restore_world()`.

Przykład seedu świata CRPG: `python examples/crpg_world_setup.py --world dungeon --create-world`

---

## 2. Szybki start

### Uruchomienie serwera

```bash
python cynober_server.py
```

Serwer nasłuchuje na porcie 8080. Zatrzymanie: `Ctrl+C`.

### Uruchomienie klienta

W drugim terminalu:

```bash
python Cynober_db.py
```

Domyślny adres: `127.0.0.1:8080`. Po nawiązaniu tunelu pojawi się prompt `Cynober>`.

Przykładowy komunikat po połączeniu:

```
Tunel zabezpieczony (HSS + HSL) — protokół Cynober-Secure-1.2.
```

Z opcjonalnym PSK i seedem QKD:

```
Tunel zabezpieczony (HSS + PSK + HSL + QKD) — protokół Cynober-Secure-1.2.
```

### Konfigurator (klient + serwer)

Interaktywny kreator — profile klienta **i** sekcja serwera w jednym pliku:

```bash
python cynober_konfigurator.py
```

Zapisuje `~/.karmazyn_client.json`:

| Sekcja | Zawartość |
|--------|-----------|
| `profiles` | Profile klienta (host, port, PSK, QKD) |
| `server` | `bind_host`, `port`, PSK, QKD dla `cynober_server.py` |

**Serwer (na PC):** opcje 6–10 — edycja `bind 0.0.0.0`, limity rate-limit, IP LAN, uruchomienie, **firewall Windows** (`scripts/cynober_firewall_windows.ps1`).

**Klient (Termux / lokalnie):** opcje 1–5 — profil z IP z opcji 8, test, uruchomienie CLI.

```bash
python cynober_server.py              # wczytuje sekcję server z JSON
python cynober_server.py 0.0.0.0 8080   # nadpisuje argv

python Cynober_db.py                  # aktywny profil klienta
python Cynober_db.py --profile termux
python Cynober_db.py 192.168.1.42 8080
```

Zmienne środowiskowe (gdy brak argv): `CYNOBER_SERVER_BIND` / `CYNOBER_SERVER_PORT` (serwer), `CYNOBER_HOST` / `CYNOBER_PORT` (klient).

### Telefon (Termux) → komputer (serwer)

Typowy układ: **serwer na PC**, **klient na telefonie** w tym samym Wi‑Fi.

| Co | Telefon | Komputer |
|----|---------|----------|
| Rola | `Cynober_db.py` / konfigurator | `cynober_server.py` |
| `~/.karmazyn_node_id` | własny | własny — **różne OK** |
| `~/.karmazyn_phi2` | własny | własny — **różne OK** |
| `KARM_PSK` / `KARM_QKD_SEED` | identyczne | identyczne — **jeśli używasz** |
| Adres w profilu | IP komputera w LAN | nasłuch `0.0.0.0:8080` |

**Krok 1 — PC (serwer)**

```powershell
# Windows: zezwól na port 8080 (jednorazowo)
New-NetFirewallRule -DisplayName "Cynober" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow

cd C:\Users\drwis\DBase
python cynober_konfigurator.py
# 7 → serwer: bind 0.0.0.0, port 8080, KARM_PSK (opcjonalnie)
# 8 → adres IP LAN do wpisania w Termux
# 9 → uruchom serwer
```

**Krok 2 — Termux (klient)**

```bash
pkg install python numpy
cd ~/DBase    # skopiowany projekt (git / scp / zip)

python cynober_konfigurator.py
# 2 → nowy profil: host=192.168.1.42, port=8080, PSK jak na PC
# 4 → test połączenia
# 5 → uruchom klienta
```

Bez konfiguratora:

```bash
export KARM_PSK="haslo-sieci"
python Cynober_db.py 192.168.1.42 8080
```

**Poza domem (internet):** użyj VPN (np. Tailscale) — w profilu wpisz IP VPN komputera (`100.x.x.x`), nie publiczne IP routera bez zabezpieczeń. Minimum: `KARM_PSK` + VPN.

**Typowe błędy**

| Objaw | Rozwiązanie |
|-------|-------------|
| Timeout | Zły IP, firewall PC, telefon w innym Wi‑Fi |
| `rozjazd seeda QKD` | Ten sam `KARM_QKD_SEED` na obu stronach |
| `różnica zegarów` | Włącz automatyczny czas (NTP) na telefonie |
| `rozjazd trybu QKD` | QKD seed tylko na jednej stronie |

### Bezpośrednie użycie silnika (bez sieci)

```python
import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine

store = kernel.Store(thermal=True)
engine = KarminEngine(store)
results = engine.execute('UTRWAL "Test"\nWSTRZYKNIJ "X" = 1 DO "Test"')
```

### Praca analityczna (pandas)

Most `cynober_pandas_bridge.read_karmin()` wykonuje zapytanie i zwraca `pandas.DataFrame` — przydatne w pipeline'ach analitycznych i raportach.

```bash
python examples/analyst_demo.py
```

```python
import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine
from cynober_pandas_bridge import read_karmin

engine = KarminEngine(kernel.Store(thermal=True))
engine.execute('UTRWAL "Zam" ...')  # dane startowe
df = read_karmin(engine, 'WYPISZ "BĄBEL", "Qty" GDZIE "Qty" > 0')
```

### Pamięć gry (GameStore)

`game_store.py` opakowuje KarminQL w API pod gry i prototypy narracyjne. Dwa backendy:

| Backend | Użycie |
|---------|--------|
| `connect_local()` | Testy, dev bez serwera (in-process `KarminEngine`) |
| `connect_rpc(host, port, world=…)` | RPC; opcjonalnie `WYBIERZ ŚWIAT` przy połączeniu (v7.1) |

```bash
python cynober_server.py                              # terminal 1
python examples/game_memory_demo.py                     # sandbox RPC (efemeryczny)
python examples/game_memory_demo.py --world rivendell --create-world  # trwały świat
python examples/game_memory_demo.py --local           # bez serwera
python examples/game_memory_demo.py --host IP --port 8080
```

Główne metody `GameStore`:

| Metoda | Opis |
|--------|------|
| `seed_demo_world()` | NPC, gracz, quest, relacje, JSON w cechach |
| `find_npcs()` / `find_players()` | Filtrowanie po roli |
| `npc_stats_table()` | Projekcja JSON (`JSON_WARTOŚĆ`) |
| `quest_givers(quest)` | Graf: `ZNAJDŹ POŁĄCZONE JAKO …` |
| `search_memory(query)` | Tekst narracji: `ZNAJDŹ` + `ILIKE` na `Pamięć` / `Opis` / `Tytuł` |
| `search_resonance(query)` | HRR: `SZUKAJ` — najlepiej na krótkich etykietach atomów (`Klucz`) |
| `excite(bubble, energy)` | `WZBUDŹ` — podbija temperaturę atomów |
| `tick(cycles)` | Cykle termodynamiczne (serwer: `TICK`; lokalnie: emulacja w `LocalBackend`) |
| `list_worlds()` / `select_world()` / `detach_world()` | Zarządzanie trwałymi światami (v7.1) |
| `health()` / `server_metrics()` | Zdrowie i metryki serwera (v7.3) |
| `backup_world()` / `list_backups()` / `restore_world()` | Kopie zapasowe światów (v7.3) |
| `stats()` | `STATYSTYKI` (`world`, `session_isolated`, `session_label`) |

Przykład integracji w aplikacji:

```python
from game_store import connect_rpc, connect_local

store = connect_rpc("127.0.0.1", 8080, world="rivendell", create_world=True)
try:
    store.seed_demo_world()
    print(store.find_npcs())           # ['Gandalf']
    print(store.search_memory("smok")) # ['Gandalf', 'Quest_Smok']
finally:
    store.close()
```

> **Uwaga:** `search_memory()` szuka w tekście cech (ILIKE). `search_resonance()` używa HRR na `E` atomu — dobrze działa na krótkich etykietach, słabiej na długich opisach narracyjnych.

---

## 3. Protokół Cynober-Secure-1.2 (HSL)

Logika negocjacji: `cynober_rpc.py`; kryptografia: `karmazyn_handshake.py`, `karmazyn_hss.py`; warstwa HSL: `karmazyn_hsl.py`.

### Fazy połączenia

1. **Powitanie (capabilities)** — wymiana JSON:
   ```json
   {
     "version": "Cynober-Secure-1.2",
     "crypto": ["hss", "ecdh", "simple"],
     "hsl": "HSL-1.1",
     "node_id": "node_a1b2c3...",
     "ts": 1738848000.0,
     "session_id": "a1b2c3d4e5f67890"
   }
   ```
2. **Negocjacja klucza** — wybór najsilniejszego wspólnego trybu (priorytet: **hss → ecdh → simple**).
3. **PSK (opcjonalnie)** — jeśli ustawiono `KARM_PSK`, klucz sesji jest mieszany: `SHA256(key + PSK)`.
4. **HSL link (1.2)** — wymiana `commit(Φ²)` + potwierdzenie rezonansu (`link_cap`):
   ```json
   {"type": "hsl_link", "version": "HSL-1.1", "epoch": 12345,
    "node_id": "node_…", "commit": "<hex 32 B>", "qkd_fp": "<opcjonalnie>"}
   ```
   Następnie obie strony wysyłają `{"type": "hsl_cap", "cap": "<HMAC>"}`. Brak rezonansu → `RuntimeError`, tunel zamknięty.
5. **RPC** — `zlib` → AES-256-GCM z kluczem z PrismMask i AAD (`req` / `resp`) → ramka 4-bajtowa (length prefix).

Odpowiedź serwera: `{"results": [...]}`. Błędy transportu: `{"error": {"code": "...", "message": "..."}}`.

#### HSL — łańcuch kluczy (skrót)

```
Φ² (per węzeł)     →  commit = SHA256(Φ² ‖ link_nonce)
handshake + PSK?   →  shared_key
KARM_QKD_SEED?     →  link_seed = HKDF(k_QKD ‖ shared_key)
PrismMask          →  s_target = HKDF(link_seed, commit_A, commit_B, epoch, task, prisms)
Ramka RPC          →  frame_key = HKDF(s_target, AAD)
```

Domyślny kontekst PrismMask dla Cynober: `task=cynober-rpc`, `prisms=["karminql"]`.

### Tryby kryptograficzne

| Tryb | Mechanizm | Kiedy |
|------|-----------|-------|
| **hss** | Ring-LWE KEM (domyślnie N=15, Q=256 — prototyp) + AES-256-GCM | Domyślny, gdy `numpy` + `karmazyn_hss.py` |
| **ecdh** | X25519 + HKDF + AES-256-GCM | Gdy brak HSS, jest `cryptography` |
| **simple** | PBKDF2 + XOR/SHAKE | Fallback dev; klient legacy 1.0 |

#### Ring-LWE KEM (HSS) — skrót techniczny

* Macierz publiczna **A** (symetryczna, deterministyczna z seedu protokołu).
* Inicjator: `pk_a = A·sk_a`, responder: `pk_b = A·sk_b`.
* Wspólny klucz z kwantyzacji iloczynu skalarnego `sk_b·pk_a` (hint w `ack`).
* Inicjator weryfikuje `sk_a·pk_b` w tolerancji rekonsyliacji (ochrona MITM).

**Parametry N i Q (prototyp).** W `karmazyn_hss.py` domyślnie `N=15`, `Q=256` — to świadomy wybór prototypowy (m.in. lekkość na Termux / telefon), przeniesiony z wcześniejszego projektu. **Nie jest to limit architektury:** wartości można podnieść do pożądanej siły (np. N=256 i parametry zbliżone do Kyber) przez zmianę stałych w `karmazyn_hss.py`. Warstwy HSL, Cynober-RPC i KarminQL pozostają bez zmian — skaluje się wyłącznie KEM. Przy większym N warto rozważyć NTT zamiast zwykłego mnożenia wielomianów (opis w `HSL_Paper_v1_1_0_EN.md`).

### Anty-replay (1.1+)

* **`ts`** — różnica zegarów ≤ 300 s (jak KSH-1.2).
* **`session_id`** — jednorazowy identyfikator sesji (pamięć RAM serwera/klienta).
* Klient **1.0** (legacy) nie wysyła pełnych caps — anty-replay jest pomijany.
* Wersje **1.1** i **1.2** wymagają `ts` + `session_id`.

### Hasło sieci (PSK)

```bash
# Windows PowerShell — oba procesy MUSZĄ mieć to samo hasło
$env:KARM_PSK = "twoje-haslo-sieci"
python cynober_server.py
# drugi terminal:
$env:KARM_PSK = "twoje-haslo-sieci"
python Cynober_db.py
```

Klient wyświetli: `Tunel zabezpieczony (HSS + PSK + HSL)`.

### Hybryda QKD + HSL (`KARM_QKD_SEED`)

Slot na seed z sieci kwantowej (paper HSL §6.4). **Dziś:** symulacja przez zmienną środowiskową; **przyszłość:** ten sam punkt w KDF, inne źródło (daemon QKD z testbedu metropolitalnego).

```powershell
# Obie strony — identyczny seed (jak PSK)
$env:KARM_QKD_SEED = "twoj-wspolny-seed-symulujacy-splatanie"
python cynober_server.py
# drugi terminal:
$env:KARM_QKD_SEED = "twoj-wspolny-seed-symulujacy-splatanie"
python Cynober_db.py
```

Łańcuch KDF:

```
link_seed = HKDF(k_QKD ‖ klucz_handshake)   # gdy KARM_QKD_SEED ustawiony
s_target  = HKDF(link_seed, commit_A, commit_B, epoch, task, prisms)
```

Klient wyświetli: `Tunel zabezpieczony (HSS + HSL + QKD)`. Rozjazd seeda między stronami → błąd rezonansu przy `hsl_link` (pole `qkd_fp`).

**Przyszła podmiana (bez zmiany protokołu):** `load_qkd_seed()` w `karmazyn_hsl.py` czyta z adaptera QKD zamiast ze zmiennej — reszta HSL bez zmian.

### Zmienne środowiskowe

| Zmienna | Efekt |
|---------|-------|
| `KARM_PSK` | Miesza hasło sieci w klucz sesji (obie strony) |
| `KARM_QKD_SEED` | Seed hybrydowy QKD+HSL (hex 64 zn. lub hasło→SHA-256); obie strony muszą być identyczne |
| `KARM_PHI2` | Nadpisuje trwałą tożsamość węzła Φ² (domyślnie `~/.karmazyn_phi2`) |
| `KARM_HSL_EPOCH_SEC` | Długość epoki HSL w sekundach (domyślnie 3600) |
| `CYNOBER_HOST` / `CYNOBER_PORT` | Adres serwera — klient (gdy brak profilu / argv) |
| `CYNOBER_SERVER_BIND` / `CYNOBER_SERVER_PORT` | Nasłuch serwera (gdy brak sekcji server / argv) |
| `CYNOBER_MAX_CONCURRENT` | Max równoczesnych połączeń (nadpisuje config) |
| `CYNOBER_MAX_CONN_PER_IP` | Max połączeń z jednego IP |
| `CYNOBER_MAX_CONN_RATE` | Max nowych połączeń/IP na minutę |
| `CYNOBER_MAX_QUERIES_PER_MIN` | Max zapytań RPC na sesję na minutę |
| `CYNOBER_MIN_CRYPTO=ecdh` | Odrzuć tryb `simple` w negocjacji |
| `CYNOBER_FORCE_CRYPTO=hss` | Wymuś konkretny tryb (debug/testy) |

### Kompatybilność wsteczna

| Klient | Serwer | Tryb |
|--------|--------|------|
| 1.2 | 1.2 | hss + HSL (+ opcjonalnie QKD) |
| 1.1 | 1.2 | hss / ecdh / simple, bez HSL |
| 1.0 | 1.2 | simple |
| * | — | Odrzucenie przy złej wersji |

### Bezpieczeństwo — co chroni, a co nie

**Chronione:**

* Treść zapytań KarminQL — szyfrowanie HSS/ECDH + klucze ramek HSL z AAD.
* Fałszywy kontekst (zła epoka, zły seed QKD, sfałszowany `link_cap`) — brak rezonansu, odrzucenie ramki.
* Replay handshake — `session_id` + okno `ts` (1.1/1.2).

**Nie chronione (świadome ograniczenia prototypu):**

* **Metadane TCP** — port :8080, rozmiar pakietów, timing (podsłuch widzi ruch, nie KarminQL).
* Brak TLS / X.509 — zaufanie z HSL i opcjonalnego PSK/QKD-seed, nie z CA.
* Brak uwierzytelnienia użytkownika — PSK/QKD to hasło **sieci**, nie konta.
* **DoS** — częściowa ochrona: rate limit połączeń i zapytań (sekcja `server.rate_limit`); flood TCP nadal możliwy przy wielu IP.
* Brak persystencji między sesjami — po rozłączeniu dane znikają, chyba że zapiszesz `.kafd` w sesji.
* Zrzuty `.kafd` — Phi-Cipher (obfuskacja), nie AES z hasłem użytkownika.
* HSS w tej wersji domyślnie ma **N=15** (prototyp); można podnieść N/Q w `karmazyn_hss.py` bez zmiany protokołu — obecna wartość nie jest równoważna pełnemu Kyber-256.

---

## 4. Zależności

### Wymagane (stdlib)

Python 3.10+ — rdzeń i KarminQL działają **bez pip**.

### Opcjonalne

| Pakiet | Po co |
|--------|-------|
| `numpy` | **HSS Ring-LWE KEM**, HRR, rezonans (`SZUKAJ`), Proca |
| `cryptography` | **ECDH/X25519** (fallback) + AES-GCM w tunelu |
| `plotly`, `pandas` | Komenda `WYKRES` w kliencie CLI |

Instalacja opcjonalnych pakietów:

```bash
pip install -r requirements.txt
```

---

## 5. Polecenia serwera (poza KarminQL)

Dostępne tylko przez tunel (klient lub RPC), obsługiwane w `cynober_server.py`:

| Polecenie | Opis |
|-----------|------|
| `STATYSTYKI` | Atomy, bąble; `world`, `session_isolated`, `persistent_worlds`, `worlds_dir` |
| `LISTA ŚWIATÓW` | Katalog trwałych światów (v7.1) |
| `UTWÓRZ / WYBIERZ / ODŁĄCZ / USUŃ ŚWIAT` | Zarządzanie trwałymi światami (v7.1) |
| `ZDROWIE` / `METRYKI SERWERA` | Operacje serwera (v7.3) |
| `KOPIA ZAPASOWA / LISTA KOPII / PRZYWRÓĆ ŚWIAT` | Backup i restore światów (v7.3) |
| `ZAPISZ ŚWIAT` | Zapis aktywnego świata na dysk |
| `TICK [n]` | `n` cykli termodynamicznych (domyślnie 1) |
| `ZAPISZ [ścieżka]` | Zapis do `.kafd`; w świecie bez ścieżki → `ZAPISZ ŚWIAT` |
| `WCZYTAJ [ścieżka]` | Wczytanie z `.kafd` do bieżącego kontekstu (sandbox lub świat) |

Przykład sesji:

```
Cynober> UTRWAL "Serwer_A"
Cynober> WSTRZYKNIJ "RAM" = 8192 DO "Serwer_A"
Cynober> ZAPISZ baza_test.kafd
Cynober> STATYSTYKI
```

---

## 6. Typy danych KarminQL

| Typ | Składnia | Przykład |
|-----|----------|----------|
| Tekst | w cudzysłowie | `"Wartość"` |
| Liczba całkowita | bez cudzysłowu | `1024` |
| Liczba zmiennoprzecinkowa | kropka dziesiętna | `3.14` |
| Logiczne | słowa kluczowe | `PRAWDA`, `FAŁSZ` |
| Puste | słowo kluczowe | `NIC` |

Aktualizacja liczbowa z operatorem względnym: `ZAKTUALIZUJ "RAM" = +50 W "Encja"` (dodaje 50 do bieżącej wartości).

---

## 7. Zarządzanie przestrzeniami (Namespaces)

Izolacja danych logicznych. Domyślna przestrzeń: `DEFAULT`.

```
UTRWAL PRZESTRZEŃ "NazwaProjektu"
WYBIERZ PRZESTRZEŃ "NazwaProjektu"
```

Bąble i indeksy w jednej przestrzeni nie są widoczne w innej.

---

## 8. KarminQL: CRUD (Bąble i Cechy)

| Operacja | Składnia |
|----------|----------|
| Tworzenie bąbla | `UTRWAL "Encja_1"` |
| Dodanie cechy | `WSTRZYKNIJ "Zmienna" = 100 DO "Encja_1"` |
| Wiele cech naraz | `WSTRZYKNIJ WIELE "RAM" = 8192, "Typ" = "Prod" DO "Encja_1"` |
| Wiele bąbli | `UTRWAL WIELE "A", "B", "C"` |
| Bąble z cechami | `UTRWAL WIELE "A" Z "RAM"=100 ORAZ "B" Z "RAM"=200` |
| Aktualizacja | `ZAKTUALIZUJ "Zmienna" = +50 W "Encja_1"` |
| Masowa aktualizacja | `ZAKTUALIZUJ "Zmienna" = "Wartość" GDZIE "Typ" = "Serwer"` |
| Usunięcie cechy | `USUŃ "Zmienna" Z "Encja_1"` |
| Usunięcie bąbla | `USUŃ BĄBEL "Encja_1"` |

Alternatywna składnia wstrzykiwania: `WSTRZYKNIJ "Klucz" -> "Wartość" DO "Bąbel"`.

---

## 9. KarminQL: Wyszukiwanie i filtrowanie

Operatory: `=`, `!=`, `>`, `<`, `>=`, `<=`, `ZAWIERA`, `W`, `NIE W`, `JEST NIC`, `NIE JEST NIC`, `MIĘDZY … DO …`.  
Logika: `ORAZ`, `LUB`, `NIE`, nawiasy `( … )` — dowolna długość łańcucha.

Podzapytania w `W` / `NIE W` (odpowiednik SQL `IN` / `NOT IN`):

```
ZNAJDŹ GDZIE "BĄBEL" W (ZNAJDŹ GDZIE "Typ" = "Serwer")
ZNAJDŹ GDZIE "Typ" W (WYPISZ "Typ" GDZIE "RAM" > 500)
ZNAJDŹ GDZIE "RAM" MIĘDZY 100 DO 500
```

| Operacja | Przykład |
|----------|----------|
| Podgląd | `POKAŻ "Encja_1"` |
| Projekcja (jeden bąbel) | `WYPISZ "Kolumna1", "Kolumna2" Z "Encja_1"` |
| Tabela wyników (SELECT) | `WYPISZ "BĄBEL", "RAM", "Typ" GDZIE "Typ" = "Serwer"` |
| DISTINCT | `WYPISZ UNIKALNE "Typ" GDZIE "RAM" > 0` lub `... GDZIE ... UNIKALNE` |
| Wyszukiwanie | `ZNAJDŹ GDZIE "Typ" = "Proces" ORAZ "RAM" > 500` |
| JOIN po relacji | `ZNAJDŹ POŁĄCZONE JAKO "syn" Z "Dziecko" GDZIE "RAM" > 1000` |
| Zliczanie | `POLICZ BĄBLE GDZIE "Typ" = "Aplikacja"` |
| Masowe usuwanie | `USUŃ BĄBLE GDZIE "Zmienna" < 0` |
| Rezonans (HRR) | `SZUKAJ "koncepcja"` *(wymaga numpy)* |

Klauzula `POŁĄCZONE JAKO "relacja" Z "cel"` ogranicza kandydatów do bąbli wskazujących na cel daną relacją (JOIN 1-hop). Działa z `WYPISZ … GDZIE`, `ZNAJDŹ … GDZIE`, `ZAKTUALIZUJ … GDZIE` i agregacjami.

### JOIN relacyjny (v6.0–v6.1)

Łączenie bąbli po **wartości cech** (jak SQL JOIN po kluczu), nie po grafie:

```
WYPISZ "BĄBEL", "Qty", Katalog.Cena GDZIE "Qty" > 0
  DOŁĄCZ Z "Katalog" GDZIE "Sku" = "Sku"
```

| SQL | KarminQL |
|-----|----------|
| INNER JOIN | `DOŁĄCZ Z "Alias" GDZIE "Klucz" = "Klucz"` |
| LEFT JOIN | `LEWY DOŁĄCZ Z "Alias" GDZIE …` |
| JOIN z filtrem | `DOŁĄCZ Z (ZNAJDŹ GDZIE "Typ"="Katalog") JAKO "Katalog" GDZIE …` |

Kolumny z prefiksem (`Katalog.Cena`) biorą wartości z dopasowanego bąbla po prawej. Wiele JOIN-ów można łańcuchować.

### Katalog, UPSERT, pandas (v6.0)

| Operacja | Składnia |
|----------|----------|
| Katalog bazy | `OPISZ BAZĘ` (`DESCRIBE DATABASE`) |
| UPSERT po nazwie | `SCAL "Bąbel" Z "Cecha" = Wartość` |
| UPSERT po kluczu | `SCAL PO "Sku" = "X" Z "RAM" = 16` |
| DataFrame | `read_karmin(engine, "WYPISZ … GDZIE …")` — `cynober_pandas_bridge.py` |

Demo: `examples/analyst_demo.py`.

### Wyrażenia w SELECT (v6.0)

- `LIKE` → `PODOBNE` (wzorzec z `%` i `_`)
- `CASE WHEN … THEN … ELSE … END AS "Kolumna"`
- Arytmetyka: `"Cena" * "Ilość" AS "Suma"`

### CTE, widoki i constraints (v6.2)

**CTE (Common Table Expression):**

```
Z $serwery JAKO (ZNAJDŹ GDZIE "Typ" = "Serwer")
ZNAJDŹ GDZIE "BĄBEL" W $serwery
```

Angielski alias: `WITH $x AS (query)`.

**Widoki zapisane:**

```
UTRWAL WIDOK "Aktywne" JAKO (WYPISZ "BĄBEL", "RAM" GDZIE "RAM" > 0)
WYPISZ Z WIDOKU "Aktywne"
```

**Constraints (jakość danych):**

```
WYMAGAJ UNIKALNE "Sku"
WYMAGAJ NIE NULL "Sku"
```

**IMPORT CSV z UPSERT:**

```
IMPORT CSV "dane.csv" SCAL PO "Sku"
```

### Wyrażenia NULL, widoki i CHECK (v6.3)

**COALESCE / NULLIF w projekcji:**

```
WYPISZ COALESCE("RAM", 0) JAKO "RAM_eff", NULLIF("Sku", "") JAKO "Sku_clean" GDZIE ...
```

**Usuwanie widoku:**

```
USUŃ WIDOK "Aktywne"
```

Angielski alias: `DROP VIEW "Aktywne"`.

**CHECK constraint (warunek na cechę):**

```
WYMAGAJ SPRAWDŹ "RAM" > 0
WYMAGAJ SPRAWDŹ GDZIE "Cena" >= 0
```

Angielski alias: `REQUIRE CHECK "RAM" > 0`. Naruszenie przy `WSTRZYKNIJ` / `ZAKTUALIZUJ` zwraca błąd `SPRAWDŹ`.

**Sortowanie wielokolumnowe:**

```
WYPISZ "BĄBEL", "Grp", "Score" GDZIE ... SORTUJ WEDŁUG "Grp", "Score" MALEJĄCO
ZNAJDŹ GDZIE ... SORTUJ WEDŁUG "A", "B" ROSNĄCO
```

### EXISTS, CAST, CONCAT i DROP CONSTRAINT (v6.4)

**EXISTS / NOT EXISTS:**

```
ZNAJDŹ GDZIE "Typ" = "Serwer" ORAZ ISTNIEJE (ZNAJDŹ GDZIE "RAM" > 1000)
ZNAJDŹ GDZIE NIE ISTNIEJE (ZNAJDŹ GDZIE "RAM" < 0)
```

Aliasy EN: `EXISTS (…)`, `NOT EXISTS (…)`. Korelacja z bąblem zewnętrznym przez `$BĄBEL` w podzapytaniu.

**CAST i CONCAT w projekcji:**

```
WYPISZ CAST("Qty" AS INT) JAKO "Qty_num", CONCAT("Imie", " ", "Nazwisko") JAKO "Pełna" GDZIE ...
```

Typy CAST: `INT`, `FLOAT`, `TEXT`, `BOOL` (oraz polskie: `LICZBA`, `TEKST`, `LOGICZNE`).

**Usuwanie constraints:**

```
USUŃ WYMAGANIE UNIKALNE "Sku"
USUŃ WYMAGANIE NIE NULL "Sku"
USUŃ WYMAGANIE SPRAWDŹ "RAM"
```

Aliasy EN: `DROP CONSTRAINT UNIQUE "Sku"`, `DROP CONSTRAINT NOT NULL "Sku"`, `DROP CONSTRAINT CHECK "RAM"`.

### Funkcje stringowe i podzapytania skalarne (v6.5)

**Funkcje stringowe w projekcji:**

```
WYPISZ TRIM("Nazwa") JAKO "Czyste", UPPER("Kod") JAKO "Wielkie", LOWER("Kod") JAKO "Male" GDZIE ...
WYPISZ LENGTH("Kod") JAKO "Len", SUBSTRING("Kod", 2, 3) JAKO "Frag" GDZIE ...
```

Obsługiwane: `TRIM`, `LTRIM`, `RTRIM`, `UPPER`, `LOWER`, `LENGTH`, `SUBSTRING(tekst, start [, długość])` — indeks `SUBSTRING` od 1 (jak w SQL).

**Podzapytania skalarne w `WYPISZ`:**

```
WYPISZ "BĄBEL", (SUMA "RAM" GDZIE "Typ" = "Serwer") JAKO "Suma_srv" GDZIE "Typ" = "Serwer"
WYPISZ "BĄBEL", (WYPISZ "RAM" GDZIE "BĄBEL" W $BĄBEL) JAKO "Self_ram" GDZIE "Typ" != "NIC"
```

Wyrażenie w nawiasach zwraca pojedynczą wartość: agregat, pierwszy wynik `ZNAJDŹ`, pierwszą komórkę `WYPISZ`. Korelacja z bąblem zewnętrznym przez `$BĄBEL`.

### Funkcje okienkowe i ALL/ANY (v6.6)

**Funkcje okienkowe w `WYPISZ`:**

```
WYPISZ "BĄBEL", "Typ", "Score",
  ROW_NUMBER() OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" MALEJĄCO) JAKO "Rn"
  GDZIE "Typ" != "NIC"

WYPISZ "BĄBEL", RANK() OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "R" GDZIE ...
```

Obsługiwane: `ROW_NUMBER()`, `RANK()`, `DENSE_RANK()` z klauzulą `OVER (…)`. Opcjonalne `PODZIEL NA "Cecha1", "Cecha2"` (odpowiednik `PARTITION BY`), wymagane `SORTUJ WEDŁUG "Cecha" [MALEJĄCO|ROSNĄCO]`. Aliasy EN: `PARTITION BY`, `ORDER BY`, `DESC`/`ASC`.

**Kwantyfikatory ALL / ANY z podzapytaniem:**

```
ZNAJDŹ GDZIE "RAM" > WSZYSTKIE (WYPISZ "RAM" GDZIE "RAM" < 1000)
ZNAJDŹ GDZIE "RAM" > DOWOLNE (WYPISZ "RAM" GDZIE "RAM" < 600)
```

Aliasy EN: `ALL (…)`, `ANY (…)`. Semantyka SQL: `> WSZYSTKIE` = większe od każdej wartości podzapytania; `> DOWOLNE` = większe od co najmniej jednej.

### Rozszerzone okna, agregaty OVER i ILIKE (v6.7)

**Offset i wartości okienkowe:**

```
WYPISZ "BĄBEL", "Score",
  LAG("Score", 1) OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Prev",
  LEAD("Score", 1) OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Next"
  GDZIE "Typ" = "X"

WYPISZ FIRST_VALUE("Score") OVER (PODZIEL NA "Typ" SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "First" GDZIE ...
WYPISZ NTILE(4) OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Bucket" GDZIE ...
```

**Agregaty okienkowe:**

```
WYPISZ SUM("Score") OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "RunSum" GDZIE ...     — suma bieżąca
WYPISZ SUM("Score") OVER (PODZIEL NA "Typ") JAKO "PartSum" GDZIE ...                  — suma w partycji
```

Obsługiwane: `LAG`, `LEAD`, `FIRST_VALUE`, `LAST_VALUE`, `NTILE(n)`, `SUM`/`AVG`/`MIN`/`MAX` (oraz `SUMA`/`ŚREDNIA`). Partycja i sortowanie czytają cechy z bąbla nawet bez kolumny w projekcji.

**ILIKE (bez rozróżniania wielkości liter):**

```
ZNAJDŹ GDZIE "Nazwa" ILIKE "serwer%"
ZNAJDŹ GDZIE "Nazwa" NIE ILIKE "plik%"
```

Alias jawny dla `PODOBNE` / `NIE PODOBNE` (obie formy dopasowują wzorzec `%`/`_` bez rozróżniania wielkości liter).

### COUNT OVER, ramki okien i PRZEMIANUJ (v6.8)

**COUNT w oknie:**

```
WYPISZ COUNT(*) OVER (PODZIEL NA "Typ") JAKO "Cnt" GDZIE ...
WYPISZ COUNT("Score") OVER (SORTUJ WEDŁUG "Score" ROSNĄCO) JAKO "Run" GDZIE ...
```

**Ramka `WIERSZE MIĘDZY` (alias: `ROWS BETWEEN`):**

```
SUM("Score") OVER (
  SORTUJ WEDŁUG "Score" ROSNĄCO
  WIERSZE MIĘDZY 1 POPRZEDZAJĄCE A 1 NASTĘPUJĄCE
) JAKO "WinSum"
```

Granice: `NIESKOŃCZONA POPRZEDZAJĄCE`, `BIEŻĄCY WIERSZ`, `N NASTĘPUJĄCE` / `N POPRZEDZAJĄCE`, `NIESKOŃCZONA NASTĘPUJĄCE`.

**REGEXP (wyrażenia regularne POSIX):**

```
ZNAJDŹ GDZIE "Kod" PASUJE DO "^item_\\d+"
ZNAJDŹ GDZIE "Kod" ~ "^other$"
ZNAJDŹ GDZIE "Kod" NIE PASUJE DO "test"
```

Aliasy EN: `REGEXP`, `~`, `NOT REGEXP`, `!~`.

**Zmiana nazw (DDL):**

```
PRZEMIANUJ BĄBEL "Stary" NA "Nowy"
PRZEMIANUJ CECHĘ "Sku" NA "SKU" W "Bąbel"
```

Aliasy EN: `RENAME BUBBLE "A" TO "B"`, `RENAME COLUMN "x" TO "y" IN "Bubble"`. Aktualizuje też relacje wskazujące na przemianowany bąbel.

### Indeksy jawne, EXPLAIN, RANGE BETWEEN i JSON (v6.9)

**CREATE / DROP INDEX** — przypięcie odwrotnego indeksu do cechy:

```
UTWÓRZ INDEKS NA "Sku"
USUŃ INDEKS NA "Sku"
```

Aliasy EN: `CREATE INDEX ON "Sku"`, `DROP INDEX ON "Sku"`. `UTWÓRZ INDEKS` przebudowuje `inv_index` dla cechy i oznacza ją w katalogu (`OPISZ BAZĘ` → `indexes`).

**EXPLAIN** — plan bez mutacji danych:

```
WYJAŚNIJ ZNAJDŹ GDZIE "Sku" = "X1"
WYJAŚNIJ WYPISZ "BĄBEL", "RAM" GDZIE "Typ" = "Serwer"
```

Alias EN: `EXPLAIN`. Zwraca `index_lookup` (szacowana liczba wierszy z `inv_index`) lub `full_scan`.

**Ramka `ZAKRES MIĘDZY` (alias: `RANGE BETWEEN`)** — granice po wartości kolumny sortowania (peery):

```
COUNT(*) OVER (
  SORTUJ WEDŁUG "Score" ROSNĄCO
  ZAKRES MIĘDZY BIEŻĄCY WIERSZ A BIEŻĄCY WIERSZ
) JAKO "Peers"
```

Dla wartości liczbowych: `N POPRZEDZAJĄCE` oznacza zakres `[wartość−N, bieżąca]`.

**JSON i ścieżki do cech:**

```
WSTRZYKNIJ "Stats" = {"hp": 100, "mp": 50} DO "Bohater"
WYPISZ JSON_WARTOŚĆ("Stats", "$.hp") JAKO "HP" GDZIE "BĄBEL" = "Bohater"
ZNAJDŹ GDZIE "Stats.hp" = 100
```

Aliasy EN: `JSON_VALUE`. Literały `{...}` / `[...]` są parsowane jako JSON. Ścieżki: `$.pole`, `$.zagnieżdżone.pole`, `$.tab[0]`.

### Optymalizacja substratu (v6.1)

Silnik utrzymuje w przestrzeni nazw:

- **`inv_index`** — odwrotny indeks `cecha → wartość → bąble`; przyspiesza `GDZIE "Typ" = "X"` i hash-join przy pojedynczym kluczu równości.
- **`atom_index`** — mapa `atom_id → bąble`; przyspiesza `SZUKAJ` (rezonans HRR) bez skanowania wszystkich bąbli.

Indeksy są aktualizowane przy `WSTRZYKNIJ` / `ZAKTUALIZUJ` / `USUŃ` i objęte rollbackiem transakcji.

### Operacje zbiorów (UNION / INTERSECT / EXCEPT)

| SQL | KarminQL | Przykład |
|-----|----------|----------|
| UNION | `ZŁĄCZ` | `ZNAJDŹ GDZIE "Typ"="X" ZŁĄCZ ZNAJDŹ GDZIE "Typ"="Y"` |
| INTERSECT | `PRZECIĘCIE` | `ZNAJDŹ GDZIE "A"=1 PRZECIĘCIE ZNAJDŹ GDZIE "B"=2` |
| EXCEPT | `RÓŻNICA` | `ZNAJDŹ GDZIE "Typ"="X" RÓŻNICA ZNAJDŹ GDZIE "BĄBEL"="A"` |

Na zmiennych skryptowych: `ZŁĄCZ $lista_a $lista_b`. Działa też z `WYPISZ … GDZIE` (łączenie wierszy). `PRZECIĘCIE` wiąże mocniej niż `ZŁĄCZ` / `RÓŻNICA` (jak w SQL). Modyfikatory `SORTUJ`, `LIMIT`, `PRZESUNIĘCIE` na końcu całego wyrażenia.

### Zmienne skryptowe

```
NIECH $lista = ZNAJDŹ GDZIE "Typ" = "Serwer"
ZNAJDŹ GDZIE "BĄBEL" W $lista ORAZ "RAM" > 1024
```

Zmienne obowiązują w obrębie jednego wywołania `execute()` (jednej ramki RPC lub jednego skryptu wieloliniowego).

---

## 10. Modyfikatory zapytań

Na końcu zapytań wyszukujących lub agregujących:

1. `SORTUJ WEDŁUG "Cecha" [MALEJĄCO|ROSNĄCO]` lub `SORTUJ WEDŁUG "Cecha1", "Cecha2" [MALEJĄCO|ROSNĄCO]`
2. `LIMIT X`
3. `PRZESUNIĘCIE Y`
4. `POGRUPUJ "Cecha"` lub `POGRUPUJ "Cecha1", "Cecha2"` *(tylko agregacje)*

Przykład:

```
ZNAJDŹ GDZIE "Typ" = "Plik" SORTUJ WEDŁUG "Rozmiar" MALEJĄCO LIMIT 5
```

---

## 11. Agregacje i grupowanie

Funkcje: `SUMA`, `ŚREDNIA`, `MIN`, `MAX`, `POLICZ`, `POLICZ RÓŻNE`.

```
SUMA "RAM" GDZIE "Stan" = "Aktywny"
ŚREDNIA "RAM" GDZIE "Stan" != "NIC" POGRUPUJ "Typ"
SUMA "RAM" GDZIE "RAM" > 0 POGRUPUJ "Typ", "Region"
POLICZ "RAM" GDZIE "RAM" > 0
POLICZ RÓŻNE "Typ" GDZIE "Typ" != "NIC"
SUMA "RAM" GDZIE "Typ" != "NIC" POGRUPUJ "Typ" MAJĄCE SUMA > 150
```

`MAJĄCE` to odpowiednik SQL `HAVING` — filtruje grupy po agregacji.

W kliencie CLI — wizualizacja:

```
WYKRES SUMA "RAM" GDZIE "Typ" != "NIC" POGRUPUJ "Typ"
```

*(Wymaga `plotly` i `pandas`.)*

---

## 12. Graf i propagacja ciepła

Spreading Activation — energia spada o 50% na każdym przeskoku relacji.

| Operacja | Składnia |
|----------|----------|
| Łączenie | `POŁĄCZ "A" Z "B" JAKO "Relacja"` |
| Rozłączanie | `ROZŁĄCZ "A" Z "B" JAKO "Relacja"` |
| Kto wskazuje na cel | `ZNAJDŹ POŁĄCZONE JAKO "syn" Z "Dziecko"` *(po `POŁĄCZ "Rodzic" Z "Dziecko" JAKO "syn"` zwraca `Rodzic`)* |
| Wzbudzenie fali | `WZBUDŹ "Start" ENERGIĄ 100 PO RELACJI "Zależność"` |
| Odczyt temperatury | `ZNAJDŹ GDZIE "TEMPERATURA" > 1 SORTUJ WEDŁUG "TEMPERATURA" MALEJĄCO` |

---

## 13. Historia i transakcje

### Historia cechy

```
HISTORIA "Stan" W "Encja_1"
```

Zwraca wpisy `CURRENT`, `ARCHIVE`, `DELETED` z timestampami.

### Transakcje ręczne

```
BEGIN
WSTRZYKNIJ "RAM" = 9999 DO "Encja_1"
ROLLBACK
```

lub `COMMIT` zamiast `ROLLBACK`.

### Auto-transakcja

Skrypt **wieloliniowy** (więcej niż jedna komenda, bez wiodącego `BEGIN`) jest automatycznie owijany transakcją — przy błędzie wykonywany jest rollback.

> Transakcja obejmuje **aktywną przestrzeń** w momencie `BEGIN`. Zmiana przestrzeni (`WYBIERZ PRZESTRZEŃ`) wewnątrz transakcji nie jest objęta rollbackiem.

---

## 14. Testy

Projekt zawiera **269 testów** w katalogu `tests/` (stan na serwer v7.3 + KarminQL v6.9). Część wymaga uruchomionego serwera w procesie testowym (harness w `test_server_rpc.py`).

### Uruchomienie wszystkich testów

```bash
python -m unittest discover -s tests -v
```

### Uruchomienie wybranej grupy

```bash
python -m unittest tests.test_karminql -v
python -m unittest tests.test_kernel -v
python -m unittest tests.test_parser -v
python -m unittest tests.test_hsl_session -v
python -m unittest tests.test_server_rpc -v
python -m unittest tests.test_cynober_rpc -v
python -m unittest tests.test_hss_handshake -v
python -m unittest tests.test_client_config -v
python -m unittest tests.test_game_store -v
python -m unittest tests.test_v69 -v
python -m unittest tests.test_v70 -v
```

### Co jest testowane

| Plik | Zakres |
|------|--------|
| `tests/test_kernel.py` | `Store`: atomy/bąble, statystyki, tick/settle |
| `tests/test_parser.py` | Parser KarminQL, operatory `>=`/`<=`, błędy składni |
| `tests/test_karminql.py` | CRUD, wyszukiwanie, agregacje, transakcje, przestrzenie, graf |
| `tests/test_sql_closure.py` | Domknięcie SQL v6.0 (JOIN, LIKE, CASE, OPISZ BAZĘ) |
| `tests/test_v62.py` … `tests/test_v69.py` | Rozszerzenia KarminQL v6.2–v6.9 (CTE, okna, JSON, EXPLAIN…) |
| `tests/test_hss_handshake.py` | Ring-LWE KEM: init/respond/finalize, odrzucenie złego tokena |
| `tests/test_cynober_rpc.py` | Kodeki RPC, caps 1.2, PSK, anty-replay, wybór trybu hss |
| `tests/test_hsl_session.py` | Φ², PrismMask, HSL link, AAD, QKD seed, kolaps przy złym kluczu |
| `tests/test_server_rpc.py` | Tunel TCP end-to-end: HSS+HSL, PSK, QKD, legacy 1.0 |
| `tests/test_v70.py` | Izolacja sandbox per połączenie RPC (v7.0) |
| `tests/test_v71.py` | Trwałe światy: współdzielenie, reconnect, LISTA/USUŃ (v7.1) |
| `tests/test_v72.py` | Auth: ZALOGUJ, role reader/writer/admin, ACL (v7.2) |
| `tests/test_v73.py` | Ops: ZDROWIE, METRYKI, backup/restore światów (v7.3) |
| `tests/test_game_store.py` | GameStore: lokalnie + RPC, trwały świat, izolacja sandbox |
| `tests/test_client_config.py` | Profile połączeń, argv/env, zapis JSON |
| `tests/test_rate_limit.py` | Limity połączeń i zapytań na serwerze |
| `tests/rpc_client.py` | Pomocniczy klient RPC dla testów integracyjnych |

### Czego testy **nie** obejmują (na razie)

* Prawdziwy adapter QKD / sprzęt kwantowy (tylko `KARM_QKD_SEED`).
* TLS / certyfikaty X.509.
* `WCZYTAJ` przez RPC end-to-end (implementacja w serwerze jest; brak dedykowanych testów integracyjnych).
* Wizualizacja `WYKRES` (plotly).
* Uwierzytelnienie użytkownika (konta, ACL poza PSK sieci).
* Auth per użytkownik na współdzielonych światach (planowane).

### Dodawanie nowych testów

1. Utwórz klasę dziedziczącą po `unittest.TestCase` w `tests/`.
2. W `setUp()` zbuduj świeży `kernel.Store()` i `KarminEngine(store)`.
3. Wywołuj `engine.execute(...)` i sprawdzaj pola `status`, `action`, `matches`, `result`.
4. Uruchom: `python -m unittest discover -s tests -v`.

Przykład minimalnego testu:

```python
import karmazyn_kernel as kernel
from cynober_query_engine import KarminEngine

store = kernel.Store(thermal=True)
engine = KarminEngine(store)
r = engine.execute('UTRWAL "A"\nWSTRZYKNIJ "X" = 1 DO "A"')
assert r[-1]["action"] == "ADD_PROP"
```

---

## 15. Struktura plików projektu

```
DBase/
├── README.md                  ← szybki start i status projektu
├── cynober_manual.md          ← ten podręcznik
├── HSL_Paper_v1_1_0_EN.md     ← specyfikacja HSL (teoria)
├── cynober_server.py          ← serwer TCP v7.3 (sesje + światy + ops)
├── cynober_ops.py             ← metryki, zdrowie, backup światów (v7.3)
├── cynober_worlds.py          ← rejestr światów (.kafd + .meta.json)
├── cynober_world_auth.py      ← auth.json, role, ACL, audyt (v7.2)
├── Cynober_db.py              ← klient CLI v1.8.0
├── game_store.py              ← adapter aplikacyjny (gry / RPC)
├── cynober_konfigurator.py    ← kreator profili połączenia
├── cynober_client_config.py   ← wczytywanie ~/.karmazyn_client.json
├── cynober_pandas_bridge.py   ← read_karmin() → DataFrame
├── cynober_lambda_bridge.py   ← most lambda na serwerze
├── cynober_rate_limit.py      ← limity połączeń i zapytań (serwer)
├── cynober_firewall.py        ← generator skryptu zapory Windows
├── scripts/cynober_firewall_windows.ps1
├── cynober_rpc.py             ← Cynober-Secure-1.2 (handshake + HSL + RPC)
├── cynober_query_engine.py    ← KarminQL v6.9
├── karmazyn_kernel.py         ← publiczna fasada jądra
├── karmazyn_atom.py           ← model atomu + FSM temperatury
├── karmazyn_substrate.py      ← Store + reach-GC
├── karmazyn_hss.py            ← Ring-LWE KEM (post-quantum handshake)
├── karmazyn_hsl.py            ← HSL: Φ², PrismMask, QKD seed, AAD
├── karmazyn_hrr.py            ← operacje wektorowe HRR (opcjonalne)
├── karmazyn_handshake.py      ← KSH-1.2: transport i szyfrowanie ramek
├── karmazyn_store.py          ← serializacja dokumentów
├── karmazyn_kafd.py           ← format binarny KAFD v2.0
├── karmazyn_proca.py          ← deduplikacja semantyczna
├── karmazyn_atomstore.py      ← kontrakt AtomStore
├── examples/
│   ├── analyst_demo.py        ← pandas + JOIN (analityka)
│   └── game_memory_demo.py    ← pamięć gry przez RPC / --local
├── requirements.txt           ← zależności opcjonalne
├── tests/                     ← 257 testów
│   ├── test_kernel.py … test_karminql.py
│   ├── test_sql_closure.py, test_v62.py … test_v69.py
│   ├── test_v70.py, test_v71.py, test_game_store.py
│   ├── test_hss_handshake.py, test_hsl_session.py
│   ├── test_cynober_rpc.py, test_server_rpc.py
│   ├── test_client_config.py, test_rate_limit.py
│   └── rpc_client.py
└── baza_test.kafd             ← przykładowy zrzut
```

---

## 16. Stan projektu i ograniczenia

### Faza użytkowa — co jest gotowe

| Obszar | Opis |
|--------|------|
| **Silnik** | KarminQL v6.9 — bogaty dialekt zapytań, transakcje, JSON, EXPLAIN, indeksy |
| **Sieć** | Tunel HSS + HSL, profile klienta, rate limit, sandbox v7.0, trwałe światy v7.1 |
| **Analityka** | pandas, CSV, `.kafd`, `examples/analyst_demo.py` |
| **Aplikacje** | `GameStore` + demo gry przez RPC lub lokalnie |
| **Jakość** | 257 testów jednostkowych i integracyjnych |

### Ograniczenia (prototyp → produkcja)

| Ograniczenie | Wpływ |
|--------------|-------|
| Sandbox = efemeryczna baza | Bez `WYBIERZ ŚWIAT` rozłączenie kasuje stan |
| Auth opcjonalne | Bez `auth.json` światy są otwarte; z auth — role w ACL |
| PSK/QKD = hasło sieci | Brak kont użytkowników i ról |
| Brak HTTP/ODBC | Integracja tylko przez własny klient TCP / Python |
| HSS N=15 | Prototyp kryptograficzny; podnieść parametry przed ekspozycją na internet |

### Planowany kierunek (v7.4+)

1. **Hardening** — HSS N=256, adapter QKD, opcjonalny TLS overlay
2. **Replikacja** — gossip / synchronizacja między węzłami
3. **REST/ODBC** — integracja poza własnym klientem TCP

### Pliki tożsamości węzła (poza repozytorium)

| Plik | Zawartość |
|------|-----------|
| `~/.karmazyn_node_id` | Identyfikator węzła w caps (`node_…`) |
| `~/.karmazyn_phi2` | Sekret Φ² (generowany przy pierwszym HSL) |
| `~/.karmazyn_client.json` | Profile połączeń klienta (host, port, opcjonalnie PSK/QKD) |