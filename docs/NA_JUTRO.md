# Na jutro

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
- Następna sesja DBase: **mocny audyt + code review** (nie feature-first)

## Zrobione wcześniej (media / KAFD)

| Faza | Co |
|------|-----|
| 0–6 | media local + KAFS + A_STREAM + lore podgląd + `sync_missing_media` |
| KAFD | v2.1 journal, KAFX, ThermalLog — [`KAFD.md`](KAFD.md) |

---

*Aktualizacja po release 8.2.5 + docs security.*
