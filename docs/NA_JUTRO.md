# Na jutro

## Zrobione w bazie (media)

| Faza | Co |
|------|-----|
| 0 | `karmazyn_media` local attach / KAFD / SOUL limit |
| 2 | pipe / open / CLI |
| 3 | KAFS over RPC: caps, MEDIA PUT/GET/STAT, `CynoberClient.put_media/get_media` |
| 4 | **A_STREAM lokalnie:** head + `media_seg`, `force_stream` / `stream_threshold`, `iter_bytes`, KAFD roundtrip |
| 4b | **KAFS PUT/GET** commit/load przez `get_bytes` + stream head gdy `KARM_MEDIA_STREAM_THRESHOLD` |
| lore F1 | lore-editor: `dodaj_media` / panel „Dołącz plik” |
| 5 | lore `podglad_media` + panel Podgląd (Tk/PIL jak Luneta) |
| 6 | `media_index` w manifeście + `sync_missing_media` / pull |

## Spike dekodera (DONE)

- `IncrementalVideoDecoder` — MP4 klatka-po-klatce gdy hot  
- Audyt: `docs/AUDIT_MEDIA_CANVAS.md`  
- Plan substratu: `docs/PLAN_DEKODERY_SUBSTRAT.md` (D1–D3)

## Opcjonalnie później

- D1: `media_frame` atoms w Store  
- async decode worker  

## Zrobione (KAFD)

- mmap reader KAFD (plain `KAFDReader.from_path`)  
- dziennik KAFS, v2.1 footer, GOP, KAFX — [`docs/KAFD.md`](KAFD.md)  

## Później / inny repo

- REST/HTTP — **nie**

---

*Aktualizacja po incremental decoder + audit + plan.*
