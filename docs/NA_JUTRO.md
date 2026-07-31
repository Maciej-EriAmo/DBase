# Na jutro

## Zrobione

### Faza 0 media
- `karmazyn_media` attach/get/export, KAFD roundtrip, SOUL 64 KiB

### Faza 2 podgląd (DBase)
- `pipe_to`, `materialize_temp`, `open_with_system`, `try_external_player`, `open_media`
- CLI: `python -m karmazyn_media extract|list|open|pipe`

## Następne w bazie: Faza 3 (KAFS over RPC)

1. Handshake caps `kafs-stream`
2. `frame_kind` RPC vs KAFS w tunelu
3. `MEDIA PUT/GET` + klient chunked
4. Test harness peer + PNG put/get

## Później / inny repo

- Faza 1 lore UI attach (lore-editor)
- REST/HTTP — **nie**

---

*Aktualizacja po Fazie 2 (lokalny podgląd).*
