# Na jutro

## Zrobione w bazie (media)

| Faza | Co |
|------|-----|
| 0 | `karmazyn_media` local attach / KAFD / SOUL limit |
| 2 | pipe / open / CLI |
| 3 | KAFS over RPC: caps, MEDIA PUT/GET/STAT, `CynoberClient.put_media/get_media` |
| 4 | **A_STREAM lokalnie:** head + `media_seg`, `force_stream` / `stream_threshold`, `iter_bytes`, KAFD roundtrip |
| 4b | **KAFS PUT/GET** commit/load przez `get_bytes` + stream head gdy `KARM_MEDIA_STREAM_THRESHOLD` |
| lore F1 | lore-editor: `dodaj_media` / panel „Dołącz plik” (osobne repo) |

## Następne w bazie (opcjonalnie)

- mmap reader KAFD
- Faza 6: replicate media index (manifest + fetch)
- Faza 5: lore `--rpc` preview stream UI

## Później / inny repo

- REST/HTTP — **nie**

---

*Aktualizacja po Faza 4b + lore F1.*
