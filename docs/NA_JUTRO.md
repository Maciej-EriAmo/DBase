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

## Opcjonalnie później

- mmap reader KAFD
- pełny 50 MiB load-test na serwerze
- GIF pump jak Luneta (animacja w Tk)

## Później / inny repo

- REST/HTTP — **nie**

---

*Aktualizacja po Faza 5+6 + podgląd Luneta-style.*
