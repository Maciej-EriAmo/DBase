# Na jutro

## Zrobione w bazie (media)

| Faza | Co |
|------|-----|
| 0 | `karmazyn_media` local attach / KAFD / SOUL limit |
| 2 | pipe / open / CLI |
| 3 | KAFS over RPC: caps, MEDIA PUT/GET/STAT, `CynoberClient.put_media/get_media` |
| 4 | **A_STREAM lokalnie:** head + `media_seg`, `force_stream` / `stream_threshold`, `iter_bytes`, KAFD roundtrip |

## Następne w bazie

- Faza 4b: MEDIA PUT/GET po KAFS ze stream head (sieć + segmenty)
- mmap reader KAFD (opcjonalnie)
- Faza 6: replicate media index

## Później / inny repo

- Faza 1+5 lore UI / `--rpc` stream
- REST/HTTP — **nie**

---

*Aktualizacja po Fazie 4 (local stream).*
