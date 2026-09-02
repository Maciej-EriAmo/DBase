# Changelog — cynober-db

Format: skrót dla deweloperów. Protokół wire (**Cynober-Secure-1.2**) jest wersjonowany osobno od pakietu.

## 8.2.4 (dev)

### Mazur Crystal / Lorentz (standard KarmazynOs)
- Pakiet `mazur_crystal/` — most na Store (+ MRC); `create_mazur_runtime` / `open_mazur_store`.
- Światy i sesje efemeryczne serwera owinięte mostem (`KARMAZYN_MAZUR=0` wyłącza).
- `search_resonance` → Lorentz \(R\), fallback HRR.
- Docs: [`docs/MAZUR_CRYSTAL.md`](docs/MAZUR_CRYSTAL.md).

### HSL Faza 6 — wybór peera
- `rank_peers` / `select_peer` / `connect_plan` w `karmazyn_hsl.py` (\(R\) ∉ KDF).
- `resolve_peer`: alias `@` / `AUTO`; `LISTA WĘZŁÓW REZONANS`.
- peers.json: `label`, `energy`.

### Stałe połączenia
- TCP keepalive (klient/serwer); rate-limit nie zamyka sesji.
- Cache tuneli peer (PULL/SYNC/gossip); `CynoberClient.ensure_connected` + 1× reconnect.
- Test: `test_persistent_session_many_queries`.

### Demo / GameStore
- `seed_demo_world(reset=True)` idempotentny; RpcBackend pokazuje prawdziwy błąd (nie sam ROLLBACK).
- `game_memory_demo --world` → `ZAPISZ ŚWIAT` + poprawione podsumowanie trwałości.

## 8.2.3

- **MEDIA LIST "bąbel"** — lista bindingów mediów przy encji (lore-editor `lista_mediow` po RPC).
- **KAFD v2.1 + dziennik KAFS** — append na tick, recovery urwanej klatki, `seal` → tabela w stopce + indeks tick/T (`KTIX`). CRC nagłówka i CAS przy odczycie = błąd. `KAFDReader.from_path` mmap na plain `.kafd`.
- **KAFX** — AES-256-GCM na `.kafd` (HKDF, klucz per świat). Payload dziennika: KX1. Stary XOR ze seedem **tylko odczyt**. `KARMAZYN_KAFD_PLAIN=1` wyłącza kopertę. Moduł `karmazyn_cipher.py`.
- **ThermalLog** (`karmazyn_thermal.py`) — klatki `ThermalFrame` GOP key/delta, `as_of_tick`, `verify_chain`. Memvid / `.mv2` usunięte.
- `Store.tick_count`. Docs: [`docs/KAFD.md`](docs/KAFD.md). Testy: `tests/test_kafd_p15.py`, `tests/test_kafd_p6.py`.

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
