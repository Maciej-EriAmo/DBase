# Changelog — cynober-db

Format: skrót dla deweloperów. Protokół wire (**Cynober-Secure-1.2**) jest wersjonowany osobno od pakietu.

## 8.2.3

- **MEDIA LIST "bąbel"** — lista bindingów mediów przy encji (lore-editor `lista_mediow` po RPC).

## 8.2.2

### Sesja / bezpieczeństwo
- **KPC** (`karmazyn_key_predict`) — exact ratchet + historia; hook w HSL przy bootstrap i rotacji epoki (nie per-frame).
- **qpredict** (`karmazyn_qpredict`) — interferencja EriAmo, fidelity \(F\); soft **nie** w KDF.
- **L0** — TCP jako Carrier (dziś); QKD = ten sam slot KDF (`karmazyn_qkd`, HSL Paper §6.4). Docs: `docs/SESSION_L0_KPC.md`.

### Klient / serwer
- `CynoberClient.session_info()` — L0, KAFS, HSL, KPC gen/residual.
- `put_media` — czytelne błędy bez KAFS / bez connect.
- Serwer: log `+ KPC(g=…) L0=TCP`; facade `_hsl_link`.
- `ZDROWIE`: `protocol`, `l0_carrier`, `kpc`, `media_kafs`, `server_version=8.2.2`.

### Dystrybucja
- PyPI `cynober-db==8.2.2`.
- Windows: gdy brak PATH do Scripts → `python -m cynober_server`.

## 8.0.x (skrót)

- 8.0.3 — baseline przed KPC (media, ACL, gossip fixes).
- 8.0 — shardy KAFD, manifest-first.
- 8.0.1 / 8.0.2 — poprawki ACL / GOSSIP.

## 7.7

- Gossip PHI, PyPI, capability tokens, QKD adapter slot.
