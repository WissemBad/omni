<#
.SYNOPSIS
    The Windows CI job (.woodpecker/windows.yml): checks the Windows-only code, then builds the real package.

.DESCRIPTION
    Pull request / main: Rust checks on Windows, then scripts/build_release.ps1 (web build, Rust core, tests with the core
    required, PyInstaller, smoke test of the package, installer, archive, checksums) without publishing.
    Tag vX.Y.Z (CI_COMMIT_TAG): the same script with -Publish: the signed release is created from the pipeline.
    Locally: .\scripts\ci_windows.ps1 [-Version 1.0.6] [-Publish]
#>
param(
    [string]$Version = '',
    [switch]$Publish
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

function Step([string]$Name, [scriptblock]$Body) {
    Write-Host "== $Name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit code $LASTEXITCODE)" }
}

$tag = $env:CI_COMMIT_TAG
if ($tag) {
    if ($tag -notmatch '^v(\d+\.\d+\.\d+)$') { throw "tag $tag is not vX.Y.Z" }
    $Version = $Matches[1]
    $Publish = $true
}
if (-not $Version) {
    $Version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
}

# The package registry token of @wissem-industries/ui for bun
if ($env:GITHUB_PACKAGES_TOKEN) {
    Set-Content -Path (Join-Path $HOME '.npmrc') -Value "//npm.pkg.github.com/:_authToken=$env:GITHUB_PACKAGES_TOKEN" -Encoding ascii
}

# The release key arrives as a secret (hex): the release script reads a file
$keyFile = $null
if ($env:OMNI_RELEASE_KEY_HEX) {
    $keyFile = Join-Path ([IO.Path]::GetTempPath()) "omni-release-$PID.key"
    Set-Content -Path $keyFile -Value $env:OMNI_RELEASE_KEY_HEX -Encoding ascii
    $env:OMNI_RELEASE_KEY = $keyFile
}

try {
    Step 'Rust: format, lints, tests (Windows code paths)' {
        cargo fmt --manifest-path native/Cargo.toml -- --check
        if ($LASTEXITCODE -eq 0) { cargo clippy --manifest-path native/Cargo.toml --all-targets --no-default-features -- -D warnings }
        if ($LASTEXITCODE -eq 0) { cargo test --manifest-path native/Cargo.toml --no-default-features }
    }
    Step 'Versions agree' { uv run --no-sync python scripts/release.py --check }

    $args2 = @($Version)
    if ($Publish) { $args2 += '-Publish' }
    Step "Package $Version" { & "$PSScriptRoot\build_release.ps1" @args2 }
}
finally {
    if ($keyFile) { Remove-Item $keyFile -Force -ErrorAction SilentlyContinue }
}
