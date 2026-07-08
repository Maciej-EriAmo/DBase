# Cynober DB — Podręcznik Użytkownika i Składnia KarminQL (v6.3)

Cynober DB to relacyjno-grafowa baza danych na termodynamicznym rdzeniu **KarmazynOS**, z transportem **Cynober-Secure-1.2** i warstwą **HSL** (Holographic Session Links). Trzy autorskie elementy — silnik, baza, protokół — opierają się na jednej zasadzie: **struktura wynika z rezonansu stanu sesji**, a nie z zewnętrznych etykiet (adres, certyfikat, ACL).

| Komponent | Wersja | Plik |
|-----------|--------|------|
| KarminQL (silnik zapytań) | v6.3 | `cynober_query_engine.py` |
| Most pandas | — | `cynober_pandas_bridge.py` |
| Klient CLI | v1.8.0 | `Cynober_db.py` |
| Serwer | — | `cynober_server.py` |
| Protokół transportu | Cynober-Secure-1.2 | `cynober_rpc.py` |
| HSL (sesje sieciowe) | HSL-1.1 | `karmazyn_hsl.py` |
| Handshake / szyfrowanie | KSH-1.2 | `karmazyn_handshake.py` |
| Ring-LWE (HSS KEM) | v1.0 | `karmazyn_hss.py` |
| Jądro KarmazynOS | v1.0.0 | `karmazyn_kernel.py` |
| Specyfikacja HSL (paper) | v1.1.0 | `HSL_Paper_v1_1_0_EN.md` |

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
                   │  KarminEngine   │  KarminQL v6.3
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

> **Uwaga:** Serwer **nie** udostępnia HTTP. Transport to wyłącznie TCP z protokołem Karmazyn (HSS + HSL).

### Współdzielony stan

Serwer trzyma **jedną globalną instancję** bazy w pamięci (`facade`). Wszyscy podłączeni klienci operują na tych samych danych. To świadomy kompromis prototypu — nie jest to izolacja per sesja.

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
* Współdzielony `facade` — wszyscy klienci widzą te same dane (brak izolacji per sesja w bazie).
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
| `STATYSTYKI` | Liczba atomów (hot/cold/reaped) i bąbli |
| `TICK [n]` | `n` cykli termodynamicznych (domyślnie 1) |
| `ZAPISZ [ścieżka]` | Zapis do `.kafd` (domyślnie `zrzut_cynober.kafd`) |
| `WCZYTAJ [ścieżka]` | Wczytanie z `.kafd` (domyślnie `zrzut_cynober.kafd`) |

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

Projekt zawiera testy w katalogu `tests/` (stan na Cynober-Secure-1.2, w tym `test_client_config.py`). Część wymaga uruchomionego serwera w procesie testowym (harness w `test_server_rpc.py`).

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
```

### Co jest testowane

| Plik | Zakres |
|------|--------|
| `tests/test_kernel.py` | `Store`: atomy/bąble, statystyki, tick/settle |
| `tests/test_parser.py` | Parser KarminQL, operatory `>=`/`<=`, błędy składni |
| `tests/test_karminql.py` | CRUD, wyszukiwanie, agregacje, transakcje, przestrzenie, graf |
| `tests/test_hss_handshake.py` | Ring-LWE KEM: init/respond/finalize, odrzucenie złego tokena |
| `tests/test_cynober_rpc.py` | Kodeki RPC, caps 1.2, PSK, anty-replay, wybór trybu hss |
| `tests/test_hsl_session.py` | Φ², PrismMask, HSL link, AAD, QKD seed, kolaps przy złym kluczu |
| `tests/test_server_rpc.py` | Tunel TCP end-to-end: HSS+HSL, PSK, QKD, legacy 1.0, współdzielony stan |
| `tests/test_client_config.py` | Profile połączeń, argv/env, zapis JSON |
| `tests/rpc_client.py` | Pomocniczy klient RPC dla testów integracyjnych |

### Czego testy **nie** obejmują (na razie)

* Prawdziwy adapter QKD / sprzęt kwantowy (tylko `KARM_QKD_SEED`).
* TLS / certyfikaty X.509.
* `WCZYTAJ` z pliku po stronie serwera (tylko `ZAPISZ` przez RPC).
* Wizualizacja `WYKRES` (plotly).
* Uwierzytelnienie użytkownika, limity DoS, izolacja per sesja w bazie.

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
├── cynober_manual.md          ← ten podręcznik
├── HSL_Paper_v1_1_0_EN.md     ← specyfikacja HSL (teoria)
├── cynober_server.py          ← serwer TCP
├── Cynober_db.py              ← klient CLI
├── cynober_konfigurator.py    ← kreator profili połączenia
├── cynober_client_config.py   ← wczytywanie ~/.karmazyn_client.json
├── cynober_rate_limit.py      ← limity połączeń i zapytań (serwer)
├── cynober_firewall.py        ← generator skryptu zapory Windows
├── scripts/cynober_firewall_windows.ps1
├── cynober_rpc.py             ← Cynober-Secure-1.2 (handshake + HSL + RPC)
├── cynober_query_engine.py    ← KarminQL v4.8
├── karmazyn_kernel.py         ← publiczna fasada jądra
├── karmazyn_atom.py           ← model atomu + FSM temperatury
├── karmazyn_substrate.py      ← Store + reach-GC
├── karmazyn_hss.py            ← Ring-LWE KEM (post-quantum handshake)
├── karmazyn_hsl.py            ← HSL: Φ², PrismMask, QKD seed, AAD
├── karmazyn_hrr.py            ← operacje wektorowe (opcjonalne)
├── karmazyn_handshake.py      ← KSH-1.2: transport i szyfrowanie ramek
├── karmazyn_store.py          ← serializacja dokumentów
├── karmazyn_kafd.py           ← format binarny KAFD v2.0
├── karmazyn_proca.py          ← deduplikacja semantyczna
├── karmazyn_atomstore.py      ← kontrakt AtomStore
├── requirements.txt           ← zależności opcjonalne
├── tests/                     ← 71 testów (kernel, KarminQL, HSS, HSL, RPC)
│   ├── test_kernel.py
│   ├── test_parser.py
│   ├── test_karminql.py
│   ├── test_hss_handshake.py
│   ├── test_cynober_rpc.py
│   ├── test_hsl_session.py
│   ├── test_server_rpc.py
│   └── rpc_client.py
└── baza_test.kafd             ← przykładowy zrzut
```

### Pliki tożsamości węzła (poza repozytorium)

| Plik | Zawartość |
|------|-----------|
| `~/.karmazyn_node_id` | Identyfikator węzła w caps (`node_…`) |
| `~/.karmazyn_phi2` | Sekret Φ² (generowany przy pierwszym HSL) |
| `~/.karmazyn_client.json` | Profile połączeń klienta (host, port, opcjonalnie PSK/QKD) |