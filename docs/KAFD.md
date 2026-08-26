# KAFD — format, dziennik, klatki termiczne, KAFX

**Status:** zaimplementowane (P1–P6)  
**Kod:** `karmazyn_kafd.py`, `karmazyn_cipher.py`, `karmazyn_thermal.py`, `karmazyn_store.py`  
**Testy:** `tests/test_kafd_p15.py`, `tests/test_kafd_p6.py`

KAFD jest protokołem przepływu atomów (plik, pipe, socket). Nie ma osobnego Memvida — timeline termiczny idzie tym samym torem.

---

## 1. Dwa nosniki

| Nosnik | Magia | Rola |
|--------|-------|------|
| **KAFD** seekable | `KAFD` | snapshot świata / po `seal` |
| **KAFS** dziennik | `KAFS` | append na tick, recovery ogona |
| **KAFX** koperta | `KAFX` | AES-256-GCM całego `.kafd` na dysku |

```text
tick Store
  → ThermalFrame (GOP key/delta)
  → KAFS append (FT_ATOM + CRC, payload KX1)
  → seal / save_documents
  → KAFD v2.1 (tabela w stopce + KTIX)
  → KAFX (klucz świata)
```

---

## 2. KAFD v2.0 (table-first)

```text
[HEADER 64 B][ATOM_TABLE N×56][ID_POOL][META JSON][PAYLOAD]
```

- CRC32 nagłówka (bajty 0–59) — mismatch = **błąd**, nie warning.
- CAS = pierwsze 12 B SHA-256 payloadu; `get_atom` **weryfikuje**.
- `layout="table_first"` — pełny rewrite (nadal `vfs_pack` / stare światy).

Odczyt plain: `KAFDReader.from_path` + **mmap** gdy plik zaczyna się od `KAFD`.

---

## 3. KAFD v2.1 (footer TOC)

`layout="footer"`, flaga `F_FOOTER_TOC`, `VERSION 0x0201`.

```text
[HEADER 64 B]
[PAYLOAD — rośnie]
[ATOM_TABLE]
[ID_POOL]
[META]
[KTIX — indeks tick / T_min / T_max / kind]
```

Stary reader v2 czyta v2.1 po offsetach w nagłówku (ignoruje KTIX).  
`compact_journal()` i `ThermalLog.seal()` piszą v2.1.

**KTIX** (`ticks_in_T_range`): query T z koperty, bez deserializacji `ThermalFrame`.

---

## 4. Dziennik KAFS (`KAFDJournal`)

Append-only, flaga `F_STREAMING` (kolejność ticków, **nie** sort po T).

| Ramka | Treść |
|-------|--------|
| `FT_ATOM` | atom + CRC32 body |
| `FT_CHECKPOINT` | tick, n_frames, T_min/T_max, kind, causality |
| `FT_END` | opcjonalnie przy `close(write_end=True)` |

`recover()`: skan do ostatniego dobrego CRC, `truncate` urwanej klatki.

Payload ramki termicznej na dysku: **KX1** (4 magic + 12 nonce + AES-GCM). CRC KAFS liczy się na szyfrogramie — recovery nie wymaga klucza; odczyt treści tak.

---

## 5. Klatki termiczne (`karmazyn_thermal.py`)

`ThermalFrame` v2: kind KEY/DELTA, T_min/T_max, crc32 LE na końcu.  
`ThermalGopEncoder`: KEY co `gop_size` albo gdy zmieni się zbiór id; delta vs KEY; jeśli delta ≥ KEY → KEY.

```python
from karmazyn_thermal import ThermalLog

tlog = ThermalLog(store, path="thermal.kafd", auto_snapshot=True, gop_size=16)
store.tick()
tlog.as_of_tick(417)
tlog.ticks_in_T_range(40, 70)
ok, err = tlog.verify_chain()
tlog.seal()
tlog.close()
```

Pliki: `{stem}.kafs` (WAL) + `{stem}.kafd` (snapshot).  
`Store.tick_count` rośnie przy każdym `tick()` (Python i native).

---

## 6. KAFX — AES-256-GCM, klucz per świat

Ten sam AEAD co tunel (`cryptography.AESGCM`). **Nie** XOR ze stałym seedem.

```text
KAFX | ver=1 | kdf=HKDF | flags | world | nonce 12 B | ciphertext+tag
```

`klucz_świata = HKDF-SHA256(master, salt="karmazyn-kafx-v1", info="world:<nazwa>")`

Master (kolejność):

1. `CYNOBER_MASTER_KEY` / `KARMAZYN_MASTER_KEY` (64 znaki hex albo string → SHA-256)
2. `CYNOBER_MASTER_KEY_FILE` / `KARMAZYN_MASTER_KEY_FILE`
3. `{data_home}/master.key` — przy pierwszym zapisie 32 B losowe (`%LOCALAPPDATA%\Cynober`)

`KARMAZYN_KAFD_PLAIN=1` — zapis bez koperty (debug).

Odczyt `open_stored_bytes` / `KAFDReader.from_path`:

| Prefiks | Tryb |
|---------|------|
| `KAFX` | AES-GCM; nazwa świata **w kopercie** (shardy regionu też klucz świata) |
| `KAFD` / `KAFS` | plain (+ mmap) |
| inny | legacy XOR `KARMAZYN_PHI_ROOT_SPACE_V1_SEED`; jeśli wyjdzie `KAFD` — stary zrzut |

Nowe zapisy **nigdy** nie używają XOR. Brak `cryptography` → jawny błąd, nie cichy XOR.

Narzut: KAFX **~42 B** / plik, KX1 **32 B** / ramka. GOP przy N≥32 atomów i małym ΔT dominuje oszczędność.

Flaga `F_ENCRYPTED` w wewnętrznym KAFD = znacznik po odszyfrowaniu.

---

## 7. API skrót

| Symbol | Plik |
|--------|------|
| `KAFDWriter(..., layout="table_first"\|"footer")` | `karmazyn_kafd` |
| `KAFDReader.from_path` / `get_atom` (CAS) | `karmazyn_kafd` |
| `KAFDJournal` / `compact_journal` | `karmazyn_kafd` |
| `wrap_kafx` / `read_stored_file` | `karmazyn_cipher` |
| `save_documents(..., world=, encrypt=)` | `karmazyn_store` |
| `ThermalLog` / `ThermalFrame` | `karmazyn_thermal` |

Światy (`cynober_worlds`) podają `world=` przy zapisie manifestu i shardów.

---

## 8. Czego tu nie ma

- Memvid / `.mv2` / H.264 / indeks lex-wektor
- Szyfr „hasłem użytkownika” w GUI — master to sekret węzła, nie login KarminQL
- mmap **zaszyfrowanego** pliku (najpierw GCM do RAM, potem parse)
