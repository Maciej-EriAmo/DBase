# Native KarmazynOS substrate (Rust)

**Decision: Rust**, not C, for the substrate core.

| Why Rust | Why not C (for this layer) |
|----------|----------------------------|
| Reach-GC graph is ownership-heavy (UAF risk in C) | C still wins for `holo/*.c` Linux LSM |
| Fearless concurrency around `tick` + hooks | Python already has the guest; we need a safe core |
| Clean C ABI + PyO3 for Python hosts | Dual language only where needed |

Python guests (Lua / mini-Lisp) stay on the **seams**:
`register_env_of` / `register_extra_reach` / `eval_line`.  
This crate owns: **atoms, bubbles, roots, tick, vacuum vs retained TOMB**.

## Layout

```
native/
  karmazyn_substrate/        # Rust core (rlib + cdylib C ABI)
    Cargo.toml
    src/{lib,atom,store,ffi}.rs
  karmazyn_substrate_rs/     # PyO3 extension (phase 4)
    Cargo.toml
    pyproject.toml           # maturin
    src/lib.rs               # CoreStore
  karmazyn_substrate_native.py   # drop-in NativeStore (PyO3 → ctypes)
  run_native.ps1 / run_native_demo.py   # prosta ścieżka + weryfikacja
  README.md
  build_native.ps1 / build_native.sh
```

## Quick start (prosta ścieżka)

```powershell
cd C:\Users\drwis\KarmazynOs

# 1) (raz / po zmianach w .rs) zbuduj native
.\native\build_native.ps1

# 2) weryfikacja: GC + restore + Lua + mini atom-DB + probe DBase
.\native\run_native.ps1

# tylko pure Rust (cargo test + example)
.\native\run_native.ps1 -RustOnly

# przebuduj i od razu sprawdź
.\native\run_native.ps1 -Build

# boot systemowy na native (demo REPL)
.\native\run_native.ps1 -Boot

# wymuś most ctypes zamiast PyO3
.\native\run_native.ps1 -Bridge ctypes
```

Równoważnie:

```powershell
$env:KARMAZYN_SUBSTRATE = "native"
python native\run_native_demo.py
python karmazyn_boot.py --demo
```

Pure Rust (bez Pythona):

```powershell
cd native\karmazyn_substrate
cargo run --example hello_store --release
```

## Build

Requires [Rust](https://rustup.rs/) (stable) and Python 3.10+.

### Full (recommended): C ABI + PyO3

```powershell
# Windows
.\native\build_native.ps1

# or manually:
cd native/karmazyn_substrate
cargo test
cargo build --release

cd ../karmazyn_substrate_rs
python -m maturin build --release
pip install --force-reinstall --no-deps target/wheels/*.whl
```

```bash
# Linux / macOS
./native/build_native.sh
```

Artifacts:

| Bridge | Artifact |
|--------|----------|
| ctypes C ABI | `karmazyn_substrate/target/release/karmazyn_substrate.dll` (`.so` / `.dylib`) |
| PyO3 | wheel `karmazyn_substrate_rs-*-*.whl` → import `karmazyn_substrate_rs` |

### Bridge selection

| Env | Meaning |
|-----|---------|
| *(unset)* | Prefer **PyO3**, else ctypes |
| `KARMAZYN_NATIVE_BRIDGE=ctypes` | Force C ABI DLL |
| `KARMAZYN_NATIVE_BRIDGE=pyo3` | Prefer PyO3 only (fail → no native unless ctypes also tried by default path) |

## Smoke (Python)

```bash
# from repo root
python native/karmazyn_substrate_native.py
# → OK native bridge=pyo3 ... hrr_on ...
```

## Compatibility tests + substrate switch

```bash
python test_substrate_compat.py -v          # both backends
python test_substrate_compat.py --native -v
python test_substrate_compat.py --python -v
```

API (also re-exported from `karmazyn_kernel`):

```python
from karmazyn_backend import open_store, substrate_backend, native_available, backend_info

s = open_store(thermal=True)                 # native if built, else python
s = open_store(backend="native")             # force Rust
s = open_store(backend="python")             # force reference Python
print(backend_info())
```

**Default (production):** native Rust — `open_store()`, `karmazyn_kernel.Store`, launchery.  
**Python `PythonStore` / `karmazyn_substrate.Store`:** reference + golden tests + `KARMAZYN_SUBSTRATE=python`.

### Luneta + JS

Luneta ładuje jadro Rust przez `LUNETA_SUBSTRATE=native` + most  
`Luneta2/luneta_native_bridge.py` (`NativePageStore`).

| Ścieżka | Store |
|---------|--------|
| HTML parse | `NativePageStore` (Rust) |
| JS / Ignatius | **ten sam** `page.store` (Rust) |
| Runtime `.reg` | `NativePageStore.reg` (string id na tym samym core) |

Start: `Luneta2\Luneta.bat` → Rust, albo  
`python luneta.py --substrate native -g URL`.

Status / TODO: `Luneta2/postępy.md` § „Substrat Rust ↔ Luneta / JS”.

## C ABI (stable seam)

| Symbol | Role |
|--------|------|
| `ksub_version` | ABI string |
| `ksub_store_new` / `ksub_store_free` | Store lifetime |
| `ksub_atom_new` / `ksub_has_atom` / `ksub_delete_atom` / `ksub_heat` | Atoms |
| `ksub_bubble_new` / `ksub_bind` / `ksub_unbind` / `ksub_lookup` | Bubbles |
| `ksub_set_root` / `ksub_unset_root` | Roots |
| `ksub_tick` / `ksub_settle` / `ksub_stats` | Thermodynamics + GC |
| `ksub_register_env_of` / `ksub_register_extra_reach` | Language hooks |

Language payloads are **opaque `u64` tokens** (e.g. Python `id(obj)`).  
The core never interprets Lua/Python values — only calls host hooks.

## PyO3 surface (`karmazyn_substrate_rs.CoreStore`)

Thin GC core: `atom_new`, `bubble_new`, `bind`/`unbind`/`lookup`, `set_root`,
`tick`/`settle`, `stats`, `register_env_of` / `register_extra_reach`.  
Full drop-in (`metadata`, `EventBus`, HRR, `Bubble` subclass) is
`NativeStore` in `karmazyn_substrate_native.py`.

## Migration plan

| Phase | Status |
|-------|--------|
| 0. Rust core + C ABI + unit tests (law) | **done** |
| 1. Golden tests vs Python (`test_substrate_compat`) | **done** |
| 2. Full `Store` drop-in (metadata, bindings, events) | **done** |
| 3. Boot `KARMAZYN_SUBSTRATE=native` end-to-end | **done** |
| 4. PyO3 bindings + HRR on native path | **done** |
| 5. Pure-Python substrate as reference / fallback only | **done** |
| 6. `restore_atoms` / snapshot rollback on native | **done** |

## Docs

- PL: [../Documents/runtime_pl.md](../Documents/runtime_pl.md)
- EN: [../Documents/runtime_en.md](../Documents/runtime_en.md)
- Architecture: [../Documents/ARCHITECTURE.pl.md](../Documents/ARCHITECTURE.pl.md) §5
