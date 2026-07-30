#!/usr/bin/env bash
# Build KarmazynOS native substrate: C ABI + PyO3 wheel
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"

echo "== cargo test + release (C ABI) =="
cd "$ROOT/karmazyn_substrate"
cargo test
cargo build --release

echo "== maturin build (PyO3) =="
cd "$ROOT/karmazyn_substrate_rs"
python -m maturin build --release
WHEEL="$(ls -t target/wheels/karmazyn_substrate_rs-*.whl | head -n1)"
python -m pip install --force-reinstall --no-deps "$WHEEL"

echo "== smoke =="
cd "$REPO"
python native/karmazyn_substrate_native.py
python karmazyn_backend.py

echo "OK native build complete."
