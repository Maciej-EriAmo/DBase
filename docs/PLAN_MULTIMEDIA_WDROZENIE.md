# Plan wdrożenia docelowego: multimedia w Cynober DB (+ lore)

**Status:** plan · **Faza 0–6 MVP DONE** (media local/KAFS/stream + lore attach/preview + media_index replicate) · **opcjonalnie: mmap / 50 MiB load-test**  


**Cel:** plik → atom w grafie → KAFD/KAFS → indeks (bąble/bindings) → z powrotem do klienta, **bez HTTP/REST**  
**Zasada transportu:** jeden tunel Karmazyn — **sterowanie = RPC/KarminQL**, **dane binarne = KAFS**  
**Poza zakresem:** osobny CDN/HTTP :8080, SQL blob store, wrzucanie filmów w JSON-RPC

---

## 1. Stan wyjściowy (co już jest)

| Warstwa | Stan | Pliki / symbole |
|---------|------|-----------------|
| Atom + binaria | `metadata["data"]` w Store | `karmazyn_atom`, `karmazyn_store._encode_atom` |
| KAFD v2 (seekable blob) | MIME, `A_RAW`/`A_STREAM`/`A_PHI_ATOM`, CAS hash | `karmazyn_kafd.KAFDAtom`, `KAFDWriter/Reader` |
| KAFS (flow frames) | `KAFDFlowWriter/Reader`, `FT_ATOM`, sort po T | `karmazyn_kafd` (~L889+) |
| Pipe do odtwarzacza | `KAFDStream.pipe_to` / `save_to` | lokalnie, bez sieci |
| Lazy + shardy + Proca | COLD fold, `ROZWIJ`, dedup `.pfld` | `cynober_worlds`, `karmazyn_store`, `karmazyn_proca` |
| RPC | tylko `{"query": str}` → JSON results | `cynober_rpc`, `cynober_server.handle_client` |
| Replikacja świata | cały `.kafd` w RPC (OK na małe/średnie) | `cynober_replicate`, lore `team_sync` |
| Gossip SOUL | bąble + opcjonalnie `data_b64` (źle na duże media) | `cynober_gossip` |
| Lore-editor | rozdziały lokalnie; graf tekstowy; **brak attach media** | `lore/store.py`, kierunek w `docs/KIERUNEK_MULTIMEDIA_STREAMING.md` |

**Luki krytyczne:** brak `put/get blob` API, brak multipleksu `frame_kind` w tunelu, `A_STREAM` tylko jako stała (brak łańcucha chunków), KAFDReader ładuje cały blob do RAM, SOUL/JSON nie nadaje się na duże pliki.

---

## 2. Docelowy model danych

```text
Bąbel (encja lore / kanał)
  bindings:
    "portret" → atom_id   # image/*
    "głos"    → atom_id   # audio/*
    "klip"    → atom_id   # video/*  (A_RAW mały | A_STREAM + segmenty)
    "bio"     → atom_id   # text w metadata["v"]

Atom:
  id, S, E, T, state
  metadata["v"]      — lekkie JSON/tekst (opis, wymiary, duration)
  metadata["data"]   — bajty (małe/średnie) ALBO puste gdy fold/CAS
  metadata["mime"]   — kanoniczny MIME (duplikat w KAFD table)
  metadata["_cas"]   — opcjonalnie hash treści
  metadata["_fold_src"] / Proca coord — odroczony payload
```

**Przepływ kanoniczny (potwierdzony model):**

```text
ZAPIS:  plik → bytes → atom (+ bind w bąblu) → KAFD (.kafd / shard / proca)
ODCZYT: KAFD → atom → bytes → plik / pipe / odtwarzacz
SIEĆ:   RPC (metadane, seek, bind) + KAFS (chunki bajtów) — NIE base64 w KarminQL
```

---

## 3. Architektura transportu docelowa

```text
TCP
 └─ [4B len][encrypted frame body]
      └─ frame_kind (1B):
           0x01 RPC_JSON   → zlib → JSON {query|results}     (sterowanie)
           0x02 KAFS       → KAFDFlow frames (FT_ATOM…)      (dane)
           0x03 GOSSIP     → opcjonalnie (później, bez ciężkich data)
```

Handshake caps (rozszerzenie):

```json
{
  "version": "Cynober-Secure-1.2",
  "prisms": ["karminql", "kafs-stream"],
  "features": ["media:stream", "media:put"]
}
```

HSL: capability `media:stream` / `media:put` (obok `karminql:query`).

**Limity polityki (do ustalenia w kodzie, propozycja):**

| Parametr | Propozycja startowa |
|----------|---------------------|
| Max atom w RAM bez fold | 8–16 MiB |
| Max KAFS frame | 256 KiB–1 MiB chunk |
| Max PUT w sesji | quota per world / rola |
| JSON-RPC z `data_b64` | tylko ≤ 64 KiB (ostrzeżenie / reject powyżej) |

---

## 4. Fazy wdrożenia

### Faza 0 — Fundament API lokalnego (cynober-db)  
**Cel:** jeden oficjalny ingest/odczyt bez sieci.  
**Czas orientacyjny:** 0.5–1 tydzień.

**Zadania:**

1. **`MediaAPI` na `Store` lub helper `karmazyn_media.py`:**
   - `attach_bytes(store, bubble_or_label, binding, data, *, mime, S, E, T)`
   - `attach_file(path, …)` → `KAFDAtom.from_file` + mapowanie do Store atom
   - `get_bytes(store, atom_id) -> (bytes, mime)`
   - `export_to_path(atom_id, path)`
2. Ustawianie `metadata["mime"]`, `metadata["data"]`; `create_atom` z jawnym id gdzie trzeba.
3. Testy unit: roundtrip PNG/WAV mały → save_documents → load → bytes równe.
4. Zakaz / limit: SOUL export pomija `data` powyżej progu (flaga `include_blobs=False` default).

**Kryteria akceptacji:**

- [x] Roundtrip plik → atom → `.kafd` → atom → plik (hash SHA256) — `tests/test_media_local.py`
- [x] Bind w bąblu po `load_store` + `restore_bubbles` (`list_bindings`)
- [x] Brak regresji (339+ testów, w tym media + gossip)
- [x] SOUL: `data_b64` ≤ 64 KiB domyślnie; powyżej `media_ref`

**Wersja:** media local + KAFS RPC; pakiet PyPI **cynober-db 8.2.2** (KPC/session_info).

---

### Faza 1 — Lore Pack + UI lokalne (lore-editor)  
**Cel:** pisarz dołącza portret/referencję; rozdziały nadal plikami lokalnymi.  
**Zależność:** Faza 0.  
**Czas:** 1 tydzień.

**Zadania:**

1. `LoreStore.dodaj_media(encja, rola, sciezka|bytes, mime=None)`
2. `LoreStore.lista_mediow(encja)`, `eksport_media(encja, rola, sciezka)`
3. Persist przez istniejący `zapisz()` / Lore Pack (`metadata["data"]` już w patch path).
4. Panel: przycisk „Dołącz plik”, lista bindingów mediów, miniatury tylko dla małych image/*.
5. `CYNOBER_SHARDED=0` pozostaje domyślne dla lore (jeden `.kafd`).

**Kryteria akceptacji:**

- [x] Portret przy postaci przeżywa restart (`test_dodaj_media_i_eksport` + `zapisz`)
- [x] Panel „Dołącz plik” + `LoreStore.dodaj_media` / `lista_mediow` / `eksport_media`
- [ ] PUSH/PULL świata przenosi małe media (cały `.kafd`) — Faza 6 / team_sync smoke
- [x] Brak zmian w workflow rozdziałów `.txt`

---

### Faza 2 — Podgląd lokalny / pipe  
**Cel:** odtworzenie bez HTTP.  
**Zależność:** Faza 0 (Faza 1 lore UI — osobno).  
**Czas:** 0.5 tygodnia.

**Zadania:**

1. Wrapper: atom → temp file **lub** `pipe_to` / system player (ffplay/mpv/os.startfile).
2. Panel lore: „Otwórz / Podgląd” — **później (lore)**.
3. CLI: `python -m karmazyn_media extract|list|open|pipe`.

**Kryteria akceptacji:**

- [x] `pipe_to` / `materialize_temp` / `export` (testy bez GUI)
- [x] `open_with_system` + `try_external_player` (graceful fail gdy brak playera)
- [x] CLI extract/list (`python -m karmazyn_media`)
- [ ] Panel lore — poza DBase

---

### Faza 3 — Multipleks tunelu + KAFS over RPC (cynober-db)  
**Cel:** sieć bez base64 w KarminQL.  
**Zależność:** Faza 0.  
**Czas:** 2–3 tygodnie (rdzeń).

**Zadania (kolejność ścisła):**

1. **Handshake:** negocjacja `kafs-stream` w caps; stary klient bez feature → tylko RPC.
2. **Frame envelope:**  
   `body = kind(1) || payload` przed encrypt **albo** po decrypt (ustalić jedno miejsce — preferowane: po decrypt, cleartext kind).
3. **Serwer `handle_client`:** pętla: recv → decrypt → branch RPC vs KAFS session.
4. **Sesja KAFS (protokół sterowania przez RPC):**
   - `MEDIA PUT START id mime size` → serwer gotowy na chunki KAFS  
   - `MEDIA PUT END id` → atom w Store + opcjonalnie bind  
   - `MEDIA GET id [OFFSET n] [LIMIT m]` → serwer streamuje KAFS  
   - `MEDIA STAT id` → mime, size, T, folded?
5. **Klient:** `CynoberClient.put_media(...)`, `get_media_iter(...)`.
6. **Chunking:** nigdy nie wysyłać całego filmu w jednej ramce 64 MiB; chunk ≤ 1 MiB.
7. **Auth:** `media:put` wymaga writer/admin; `media:get` reader+.
8. Testy: peer harness + mały PNG put/get; duży plik 5–20 MiB chunked; reject bez cap.

**Kryteria akceptacji:**

- [x] Put/get PNG przez sieć, hash zgodny (`tests/test_media_kafs_rpc.py`)
- [x] Stary klient (tylko RPC) nie psuje handshake (RPC bez prefiksu kind)
- [x] Rate limit nadal na pętli zapytań; `MediaSession.bytes_in/out`
- [x] Brak ścieżki „cały film w `query` string” (KAFS chunki ≤ 1 MiB)
- [x] Caps `features: [kafs-stream, media:put, media:stream]`

---

### Faza 4 — A_STREAM / duże media / true fold  
**Cel:** pliki > limitu RAM i > sensownego monolit KAFD.  
**Zależność:** Faza 3.  
**Czas:** 2 tygodnie.

**Zadania:**

1. Model segmentów: atom nagłówkowy `A_STREAM` + `metadata["segments"]` / CAS lista id chunków.
2. PUT dzieli plik na segmenty; GET reassembluje lub streamuje sekwencyjnie.
3. Opcja: nie trzymać pełnego `metadata["data"]` w RAM — tylko fold + `ROZWIJ` / GET.
4. Ulepszenie KAFDReader: opcjonalny odczyt z `mmap` / file offset (nie cały blob) — **osobny PR jeśli trudne**.
5. Polityka: video default → stream segments; image < 2 MiB → single A_RAW.

**Kryteria akceptacji:**

- [x] Lokalnie: head + `media_seg`, reassemble `get_bytes` / `iter_bytes` (`tests/test_media_local.py::TestMediaStream`)
- [x] `force_stream` / `stream_threshold` + KAFD roundtrip (segmenty w `DOC_KINDS`)
- [x] `pipe_to` po segmentach (bez monolit w head)
- [x] PUT/GET KAFS z reassemble stream (`test_put_get_stream_head`, próg env)
- [ ] Plik 50 MiB E2E + restart serwera (load test / ops)
- [ ] `ROZWIJ` nie ładuje wszystkich mediów świata naraz

---

### Faza 5 — Lore RPC + „kino lore” (lore-editor)  
**Cel:** zespół ogląda media ze świata na serwerze.  
**Zależność:** Faza 3 (min), 4 (dla dużych).  
**Czas:** 1–1.5 tygodnia.

**Zadania:**

1. `RpcLoreBackend` / cienki wrapper: `put_media` / `get_media` gdy klient ma kafs.
2. UI: w `--rpc` podgląd z serwera (stream → pipe/temp).
3. Fallback: bez kafs — tylko metadane + komunikat „zaktualizuj cynober-db”.
4. Dokumentacja F1: „media ≠ HTTP”.

**Kryteria akceptacji:**

- [ ] A na serwerze dodaje portret; B w `--rpc` widzi podgląd
- [ ] Rozdziały nadal lokalne

---

### Faza 6 — Sync mediów w zespole (replicate + gossip)  
**Cel:** spójność bez zawsze pełnego monolit PUSH.  
**Zależność:** Faza 3–4, SOUL v8.1 (już jest lite).  
**Czas:** 1–2 tygodnie.

**Zadania:**

1. **Replicate:** manifest-first już jest — dodać „media index” (lista cas/id/size) w meta; pull brakujących blobów KAFS.
2. **Gossip SOUL:** default **bez** `data_b64`; zamiast tego `media_refs: [{id, cas, mime, size}]`; osobne `GOSSIP FETCH MEDIA id`.
3. Proca: upewnić się, że backup/replicate kopiuje `proca/<world>/` (już częściowo w ops).
4. Test E2E: A push świata z 3 obrazkami; B pull; C przyrostowo tylko nowy obraz.

**Kryteria akceptacji:**

- [ ] Przyrostowy pull < full world gdy brakuje 1 pliku
- [ ] SOUL sync grafu nie dmucha filmów w base64

---

### Faza 7 (opcjonalna / research) — AI + media memory  
**Cel:** agent zna „jest portret Anny”, nie łyka pikseli.

**Zadania:**

1. Tool: `list_media(entity)`, `describe_media(id)` → mime/size/T/path-or-cas.
2. OCR/caption opcjonalnie → `metadata["v"]` tekst, nie blob w prompcie.
3. Policy: agent `media:get` read-only na sandbox world.

---

## 5. Mapa plików (gdzie kod)

| Obszar | Pliki |
|--------|--------|
| API media lokalne | nowy `karmazyn_media.py` lub metody w `karmazyn_store` / thin facade w `karmazyn_kernel` |
| KAFD/KAFS | `karmazyn_kafd.py` (segmenty A_STREAM, ewentualnie mmap reader) |
| Tunel | `cynober_rpc.py`, `cynober_server.handle_client`, `cynober_client.py`, `karmazyn_handshake` (caps) |
| HSL caps | `karmazyn_hsl.py` |
| KarminQL media cmds | `cynober_server.CynoberFacade` / mały `cynober_media_ops.py` |
| Gossip | `cynober_gossip.py` (refs zamiast pełnych data) |
| Replicate | `cynober_replicate.py` (media index + fetch) |
| Lore | `lore/store.py`, panel, `lore/cynober_patch.py` (MIME w pack), docs F1 |
| Testy | `tests/test_media_local.py`, `test_media_kafs_rpc.py`, lore `scripts/test_media_*.py` |

---

## 6. Testy i jakość

| Faza | Testy obowiązkowe |
|------|-------------------|
| 0 | unit roundtrip hash; store save/load |
| 1 | lore store media + restart |
| 2 | smoke open (może skip CI bez playera) |
| 3 | unittest + harness 2 procesów; stary klient handshake |
| 4 | plik 20–50 MiB chunked; memory ceiling smoke |
| 5–6 | E2E jak `test_team_replicate` + media |

CI: nie wymagać ffplay; wymagać hash roundtrip.

---

## 7. Wersjonowanie i kompatybilność

| Release | Zawartość |
|---------|-----------|
| **8.1.x** | SOUL gossip (już) + Faza 0–1 media local |
| **8.2.0** | Faza 3 KAFS multiplex (feature flag / caps) |
| **8.3.0** | Faza 4 segments + Faza 6 media-aware replicate |
| lore-editor | `cynober-db>=8.1` potem `>=8.2` dla stream UI |

Backward compatible: klient bez `kafs-stream` działa jak dziś (tylko graf + full world pull).

---

## 8. Ryzyka i mitigacje

| Ryzyko | Mitigacja |
|--------|-----------|
| 64 MiB hard cap + JSON explosion | KAFS chunks; ban large b64 in query |
| Szyfrowanie całego frame w RAM | małe chunki; przyszłość: streaming AEAD (później) |
| Phi-cipher whole-file | shardy per media region; fold COLD |
| DoS PUT | auth + quota + rate limit bytes |
| Złożoność A_STREAM | Faza 4 dopiero po stabilnym single-blob KAFS |
| Proca nie dla JPEG | media idą RAW/CAS; Proca opcjonalnie tylko gdy wektor ma sens |

---

## 9. Kolejność prac (backlog gotowy do sprintów)

1. **P0** `karmazyn_media.attach_*` + testy roundtrip (Faza 0) — **DONE**  
2. **P0** limit SOUL blobs / `include_blobs` (higiena) — **DONE**  
3. **P1** LoreStore + panel attach (Faza 1) — **DONE** (lore-editor)  
4. **P1** podgląd lokalny (Faza 2) — **DONE**  
5. **P2** caps + frame_kind + MEDIA PUT/GET (Faza 3) — **DONE**  
6. **P2** test harness sieciowy media — **DONE**  
7. **P3** A_STREAM segments local (Faza 4) — **DONE**  
7b. **P3** A_STREAM over KAFS MEDIA PUT/GET (Faza 4b) — **DONE**  
8. **P3** lore attach UI (Faza 1) — **DONE** (lore-editor)  
8b. **P3** lore preview (Faza 5) — **DONE** (`podglad_media`, Luneta-style Tk/PIL)  
9. **P3** replicate media index (Faza 6) — **DONE** (`media_index` + `sync_missing_media`)  
10. **P4** AI tools describe/list (Faza 7)

---

## 10. Definition of Done (docelowy system)

System uznajemy za „docelowy multimedia MVP”, gdy:

1. Lokalnie: plik ↔ atom ↔ `.kafd` ↔ plik (hash).  
2. W grafie: binding w bąblu + KarminQL widzi encję.  
3. Sieć: put/get przez KAFS, **zero** dużych base64 w KarminQL.  
4. Stary klient RPC bez mediów nie psuje się.  
5. Lore: attach portretu + podgląd; rozdziały nadal plikami.  
6. Zespół: świat z mediami da się zreplikować (full lub przyrostowo).  
7. Dokumentacja: manual + F1 „nie HTTP”.

---

## 11. Pierwszy konkretny PR (rekomendacja startu)

**Tytuł:** `feat(media): local attach API and KAFD roundtrip`  

**Zakres tylko Faza 0:**

- `karmazyn_media.py` + export w `karmazyn_kernel`  
- testy `tests/test_media_local.py`  
- `export_soul`: domyślnie bez dużych `data_b64` (próg 64 KiB)  
- krótka sekcja w `cynober_manual.md`

Po merge: osobny PR lore Faza 1 (może równolegle, zależy tylko od API).

---

*Plan oparty o audit kodu DBase (KAFD/KAFS, RPC, store, gossip) oraz `lore-editor/docs/KIERUNEK_MULTIMEDIA_STREAMING.md`.*
