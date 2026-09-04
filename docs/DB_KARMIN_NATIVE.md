# DB_karmin × substrat Rust

**DB_karmin** = wspólna baza pod lore_editor, studio_klimat i kolejne projekty.  
Implementacja: ten pakiet (**cynober-db** / DBase).  
**Substrat Rust** = produkcyjny Store (reach-GC) pod KarminQL / KAFD / światy.

## Architektura

```
Aplikacje (lore-editor, Studio, GameStore, …)
        │
        ▼
   KarminQL / światy / KAFD / RPC     ← warstwa DB_karmin
        │
        ▼
   create_mazur_runtime() → LorentzBridge   ← KARMAZYN_MAZUR (default ON)
        │
        ▼
   karmazyn_kernel.Store / open_store()     ← fasada
        │
   ┌────┴────┐
   ▼         ▼
NativeStore  PythonStore        ← KARMAZYN_SUBSTRATE=native|python
(Rust GC)    (referencja)
```

Sieć (Cynober-Secure) nie wybiera substratu — widzi tylko API Store po fasadzie.
Hydrate KAFD / rollback muszą iść przez `Bubble.bind` (nie `bindings = dict`),
inaczej NativeStore reach-GC nie widzi bindingów.

## Identyfikatory atomów

| Warstwa | Typ id |
|---------|--------|
| Publiczne (KAFD, KarminQL, gossip) | **string** (`a0`, `cell_…`, custom) |
| Rdzeń Rust | **int** (wewnętrzny) |

Mapowanie `sid ↔ aid` jest w `native/karmazyn_substrate_native.py` (`NativeStore`).

## Przełącznik

```powershell
$env:KARMAZYN_SUBSTRATE = "native"   # default gdy most zbudowany
$env:KARMAZYN_SUBSTRATE = "python"   # golden / awaryjnie
```

```python
import karmazyn_kernel as kernel
print(kernel.kernel_info()["substrate"])
s = kernel.Store(thermal=True)  # NativeStore lub Store
```

## Build mostu

```powershell
cd native\karmazyn_substrate
cargo test
cargo build --release

# opcjonalnie PyO3 (maturin) — wheel systemowy też działa, jeśli zainstalowany
cd ..\karmazyn_substrate_rs
python -m maturin develop --release
```

Albo: `.\native\build_native.ps1`

## Test

```powershell
python -m unittest tests.test_kernel tests.test_karminql -q
python -c "from karmazyn_kernel import kernel_info; print(kernel_info()['substrate'])"
```
