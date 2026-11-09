<#
.SYNOPSIS
    Every check of a pull request, locally: what CI (.woodpecker/check.yml and windows.yml) runs, in the same order.

.DESCRIPTION
    .\scripts\check.ps1            # everything
    .\scripts\check.ps1 -Fast      # without the web build and the Rust tests (lints, types, Python tests)
    For the exact CI environment (Linux, same Docker images) run .\scripts\ci-local.ps1; .\scripts\install-hooks.ps1 makes git push do it.
    Needs the Rust core installed (uv sync --inexact, then python -m omni native --build) and `bun install` in web\.
#>
param([switch]$Fast)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:OMNI_REQUIRE_NATIVE = '1'

function Step([string]$Name, [scriptblock]$Body) {
    Write-Host "== $Name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit code $LASTEXITCODE)" }
}

Step 'Python: lockfile is current' { uv lock --check }
Step 'Python: ruff' { uv run --no-sync ruff check }
Step 'Python: import contracts' { uv run --no-sync lint-imports }
Step 'Python: types (pyright ratchet)' { uv run --no-sync python scripts/typecheck.py }
Step 'Python: dependency advisories' { uv run --no-sync pip-audit --skip-editable }
Step 'Versions agree' { uv run --no-sync python scripts/release.py --check }
Step 'Python: tests and coverage' { uv run --no-sync pytest -q --cov=omni }
Step 'Rust: format' { cargo fmt --manifest-path native/Cargo.toml -- --check }
Step 'Rust: clippy' {
    cargo clippy --manifest-path native/Cargo.toml --all-targets --no-default-features -- -D warnings
    if ($LASTEXITCODE -eq 0) { cargo clippy --manifest-path native/Cargo.toml --all-targets -- -D warnings }
}
Step 'Rust: cargo-deny' {
    if (Get-Command cargo-deny -ErrorAction SilentlyContinue) { cargo deny --manifest-path native/Cargo.toml --config native/deny.toml check }
    else { Write-Host 'cargo-deny not installed: left to .\scripts\ci-local.ps1' -ForegroundColor Yellow }
}
if (-not $Fast) {
    Step 'Rust: tests' { cargo test --manifest-path native/Cargo.toml --no-default-features }
    Step 'Web: lint, types, build' { Push-Location web; try { bun run check } finally { Pop-Location } }
}
Write-Host 'All checks passed.' -ForegroundColor Green
