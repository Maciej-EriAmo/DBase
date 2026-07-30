# Build KarmazynOS native substrate: C ABI + PyO3 wheel
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Root

Write-Host "== cargo test + release (C ABI) =="
Push-Location (Join-Path $Root "karmazyn_substrate")
try {
    cargo test
    if ($LASTEXITCODE -ne 0) { throw "cargo test failed" }
    cargo build --release
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }
} finally {
    Pop-Location
}

Write-Host "== maturin build (PyO3) =="
Push-Location (Join-Path $Root "karmazyn_substrate_rs")
try {
    python -m maturin build --release
    if ($LASTEXITCODE -ne 0) { throw "maturin build failed" }
    $wheel = Get-ChildItem "target\wheels\karmazyn_substrate_rs-*.whl" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $wheel) { throw "wheel not found" }
    Write-Host "Installing $($wheel.FullName)"
    python -m pip install --force-reinstall --no-deps $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
} finally {
    Pop-Location
}

Write-Host "== smoke =="
Push-Location $Repo
try {
    python native/karmazyn_substrate_native.py
    if ($LASTEXITCODE -ne 0) { throw "smoke failed" }
    python karmazyn_backend.py
} finally {
    Pop-Location
}

Write-Host "OK native build complete."
