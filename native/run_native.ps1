# Prosta sciezka uruchomienia substratu Rust (KarmazynOs)
# Uzycie:
#   .\native\run_native.ps1              # demo weryfikacji (Lua + atom-DB)
#   .\native\run_native.ps1 -Build       # najpierw przebuduj native
#   .\native\run_native.ps1 -Bridge ctypes
#   .\native\run_native.ps1 -Boot        # interaktywny boot na native
#   .\native\run_native.ps1 -RustOnly    # tylko cargo test + example

param(
    [switch]$Build,
    [switch]$Boot,
    [switch]$RustOnly,
    [switch]$Bench,
    [ValidateSet("auto", "pyo3", "ctypes")]
    [string]$Bridge = "auto",
    [switch]$SkipLua,
    [switch]$SkipDbase,
    [switch]$Quick
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Root

# cargo w PATH
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoBin) {
    $env:Path = "$cargoBin;$env:Path"
}

Set-Location $Repo

function Assert-Rust {
    if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
        throw "Brak rustc - zainstaluj rustup (https://rustup.rs/)"
    }
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        throw "Brak cargo"
    }
    Write-Host "rustc: $(rustc --version)"
    Write-Host "cargo: $(cargo --version)"
}

if ($RustOnly) {
    Assert-Rust
    Write-Host "== cargo test =="
    Push-Location (Join-Path $Root "karmazyn_substrate")
    try {
        cargo test
        if ($LASTEXITCODE -ne 0) { throw "cargo test failed" }
        Write-Host "== example hello_store =="
        cargo run --example hello_store --release
        if ($LASTEXITCODE -ne 0) { throw "hello_store failed" }
    } finally {
        Pop-Location
    }
    Write-Host "OK Rust-only path"
    exit 0
}

if ($Bench) {
    Assert-Rust
    Write-Host "== pure Rust bench_store =="
    Push-Location (Join-Path $Root "karmazyn_substrate")
    try {
        cargo run --example bench_store --release
        if ($LASTEXITCODE -ne 0) { throw "bench_store failed" }
    } finally {
        Pop-Location
    }
    Write-Host "== Python bench_substrate.py =="
    $benchArgs = @("bench_substrate.py")
    if ($Quick) { $benchArgs += "--quick" }
    if ($SkipLua) { $benchArgs += "--skip-lua" }
    python @benchArgs
    exit $LASTEXITCODE
}

if ($Build) {
    Assert-Rust
    Write-Host "== build_native.ps1 =="
    & (Join-Path $Root "build_native.ps1")
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
}

$env:KARMAZYN_SUBSTRATE = "native"
if ($Bridge -ne "auto") {
    $env:KARMAZYN_NATIVE_BRIDGE = $Bridge
} else {
    Remove-Item Env:KARMAZYN_NATIVE_BRIDGE -ErrorAction SilentlyContinue
}

if ($Boot) {
    Write-Host "== karmazyn_boot.py (native) =="
    python karmazyn_boot.py --demo
    exit $LASTEXITCODE
}

$demoArgs = @("native/run_native_demo.py")
if ($Bridge -ne "auto") {
    $demoArgs += @("--bridge", $Bridge)
}
if ($SkipLua) { $demoArgs += "--skip-lua" }
if ($SkipDbase) { $demoArgs += "--skip-dbase" }

Write-Host "== run_native_demo.py =="
python @demoArgs
exit $LASTEXITCODE
