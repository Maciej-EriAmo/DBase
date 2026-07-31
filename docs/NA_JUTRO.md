# Na jutro

## Zrobione (Faza 0 media)

- `karmazyn_media.py` — attach_bytes / attach_file / get_bytes / export_to_path
- Export w `karmazyn_kernel` + `py-modules`
- `tests/test_media_local.py` (roundtrip SHA256 + SOUL limit)
- Gossip SOUL: próg 64 KiB, `media_ref`, `include_blobs`
- Manual + `PLAN_MULTIMEDIA_WDROZENIE.md` (checkboxy Fazy 0)

## Następne: Faza 1 (lore-editor)

1. `LoreStore.dodaj_media` / `lista_mediow` / `eksport_media`
2. Panel: „Dołącz plik”, lista bindingów
3. Zależność: `cynober-db` z `karmazyn_media` (pip install -e DBase)

## Nie teraz

- Faza 3 KAFS w tunelu
- REST/HTTP media server

---

*Aktualizacja po implementacji Fazy 0.*
