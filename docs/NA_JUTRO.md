# Na jutro

## Domknięte (2026-09-29, cynober-db 8.2.6)

| Tor | Co |
|-----|-----|
| ACL | rola max(świat, `*`), `SYNC`/`FETCH` = global admin, zepsuty `auth.json` zamyka login, id kopii w `backups/{świat}/` |
| Klient | urwany KAFS zamyka tunel, jedno zapytanie na peer, brak powtórki po wysłaniu, lista mediów wstaje raz |
| Zapis | `ZAPISZ ŚWIAT` zostawia media po id i segmenty żywego strumienia |
| Numer | `SERVER_VERSION` i `pyproject.toml` = **8.2.6**. Wheel na PyPI: 8.2.5 |

## Domknięte (2026-09, cynober-db 8.2.4 → 8.2.5)

| Tor | Co |
|-----|-----|
| Sieć 1→4 | RPC reconnect, gossip ACL/REZONANS, NativeStore hydrate bind, Mazur wheel + KONTEKST |
| Security | Legacy/MIN_CRYPTO gates, HSL1 encrypt link/cap, scrypt+lockout, `SECURE_BOOT`, suite `test_security_audit` |
| Packaging | `cynober_paths` w wheel (**8.2.5** na PyPI) |
| Docs | [`SECURITY_CONNECTION.md`](SECURITY_CONNECTION.md), SESSION/MAZUR/README zsynchronizowane |
| lore-editor | **0.7.9** wymaga `>=8.2.5` |

## Opcjonalnie później

- D1–D3: dekodery substratu ([`PLAN_DEKODERY_SUBSTRAT.md`](PLAN_DEKODERY_SUBSTRAT.md))
- REST/HTTP — **nie** (jeden wire Karmazyn)
- Argon2 zamiast scrypt (opcjonalny extra)
- Audyt połączenia i klienta jest w repozytorium 8.2.6

## Zrobione wcześniej (media / KAFD)

| Faza | Co |
|------|-----|
| 0–6 | media local + KAFS + A_STREAM + lore podgląd + `sync_missing_media` |
| KAFD | v2.1 journal, KAFX, ThermalLog — [`KAFD.md`](KAFD.md) |

---

*Aktualizacja po 8.2.6 w repozytorium. Wheel PyPI: 8.2.5.*
