# Most Lorentza + MRC — DBase / Cynober

Kopia standardu z KarmazynOs. Domyślnie włączany przy tworzeniu światów i sesji efemerycznych.

```
open_store / Store → LorentzBridge (+ MRC)
```

Wyłączenie: `KARMAZYN_MAZUR=0`.

## Szew

```python
from mazur_crystal import open_mazur_store
# albo
from karmazyn_backend import open_mazur_store
```

`cynober_worlds.create_mazur_runtime()` — używane przez serwer i registry.

## Sieć (Faza 6)

```
R → rank_peers → select_peer → TCP → handshake → HSL → klucz
```

- `SYNC ŚWIAT "x" Z "@"` / `Z "AUTO"` — wybór peera przez rezonans
- `LISTA WĘZŁÓW REZONANS` — ranking bez łączenia
- `resonance_feeds_kdf: false` — R nie wchodzi do KDF

Lokalny kontekst: `KARM_PEER_LABEL`, `KARM_PEER_ENERGY`, `CYNOBER_NODE_ID`.
Peers: pola `label` / `energy` w `peers.json`.

## Test

```powershell
cd C:\Users\drwis\DBase
$env:KARMAZYN_SUBSTRATE = "python"
$env:KARMAZYN_MAZUR = "1"
python -m unittest tests.test_mazur_dbase tests.test_hsl_peer_rank -v
```
