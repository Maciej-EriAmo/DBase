# Publikacja cynober-db na PyPI
# Wymaga: pip install build twine
# Token: https://pypi.org/manage/account/token/ → scope Entire account (lub projekt)

param(
    [string]$Repository = "pypi",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not $SkipBuild) {
    python -m build
}

python -m twine check dist/cynober_db-*

if ($Repository -eq "test") {
    Write-Host "Upload → TestPyPI (test.pypi.org)"
    python -m twine upload --repository testpypi dist/cynober_db-*
} else {
    Write-Host "Upload → PyPI (pypi.org)"
    python -m twine upload dist/cynober_db-*
}

Write-Host ""
Write-Host "Po publikacji:"
Write-Host "  pip install cynober-db"
Write-Host "  cynober-server"