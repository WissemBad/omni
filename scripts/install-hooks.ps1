<#
.SYNOPSIS
    Installs the git hooks of the repository (a pre-push that runs the CI pipeline locally, in Docker).
#>
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
git config core.hooksPath scripts/hooks
Write-Host 'Hooks installed: git push now runs scripts\ci-local.ps1 first (skip once with --no-verify).' -ForegroundColor Green
