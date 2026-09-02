# Most Lorentza + MRC + wybór peera (DBase / Cynober)

**Status:** zrównane ze standardem KarmazynOs.  
**Kod:** `mazur_crystal/` · światy: `create_mazur_runtime()` · sieć: `karmazyn_hsl.rank_peers` / `resolve_peer`.

## Store

```text
open_store / Store → LorentzBridge (+ MRC)   # KARMAZYN_MAZUR=0 wyłącza
```

- `cynober_worlds.create_mazur_runtime()`
- `cynober_server` sesje efemeryczne
- `KarminLambdaBridge()` bez argumentu → most domyślnie

`SEARCH` / `search_resonance` → najpierw \(R\), fallback HRR.

## Sieć (Faza 6)

```text
R → ranking → select_peer → TCP → handshake → HSL → klucz
```

- `SYNC/PULL/PUSH … Z "@"` lub `"AUTO"` — wybór peera przez rezonans
- `LISTA WĘZŁÓW REZONANS` — ranking bez łączenia
- **`resonance_feeds_kdf: false`** — \(R\) nie wchodzi do KDF

Peers (`peers.json`): pola `label`, `energy`.  
Lokalnie: `KARM_PEER_LABEL`, `KARM_PEER_ENERGY`, `CYNOBER_NODE_ID`.

## Stałe połączenia TCP

- Klient: jeden tunel na wiele `query`; `ensure_connected` + auto-reconnect (1×)
- Serwer: rate-limit **nie** zamyka sesji; TCP keepalive
- Peer RPC: cache tuneli między PULL/SYNC/gossip

```python
c = connect()
try:
    c.query("ZDROWIE")
    c.query("LISTA ŚWIATÓW")  # ten sam socket
finally:
    c.close()
```

Unikaj `with connect()` w pętli wokół pojedynczego query.

## Klient

| Narzędzie | Plik |
|-----------|------|
| CLI | `Cynober_db.py` |
| SDK | `cynober_client.py` |
| Konfigurator | `cynober_konfigurator.py` |
| Demo gry | `examples/game_memory_demo.py --world …` |

Trwały świat: po seedzie demo robi `ZAPISZ ŚWIAT`.

## Testy

```powershell
$env:KARMAZYN_SUBSTRATE = "python"
$env:KARMAZYN_MAZUR = "1"
python -m unittest tests.test_mazur_dbase tests.test_hsl_peer_rank tests.test_cynober_client -v
```

## Powiązania

- KarmazynOs: `Documents/MAZUR_CRYSTAL.md`
- L0/KPC: [`SESSION_L0_KPC.md`](SESSION_L0_KPC.md)
- Manual: [`../cynober_manual.md`](../cynober_manual.md)
