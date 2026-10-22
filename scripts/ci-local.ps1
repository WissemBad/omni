<#
.SYNOPSIS
    Runs the real CI pipeline (.woodpecker/check.yml) on this machine, in the same Docker images and with the same
    commands as Woodpecker: if this passes, the Linux checks of the pull request pass.

.DESCRIPTION
    .\scripts\ci-local.ps1                 # rust-check, python-check, python-test
    .\scripts\ci-local.ps1 -Step rust-check
    Needs Docker and woodpecker-cli. The web steps are left out: they install a private package (a token) and
    scripts\check.ps1 already runs `bun run check` on this machine.
#>
param([string[]]$Step)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
foreach ($tool in 'docker', 'woodpecker-cli') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool not found" }
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker is not running' }

# the pipeline minus the steps that need a secret (or only the steps asked for), written outside the repository
$text = (Get-Content .woodpecker/check.yml -Raw) -replace "`r`n", "`n"
$parts = [regex]::Split($text, '(?m)^(?=  - name: )')
$keep = $parts | Select-Object -Skip 1 | Where-Object {
    $name = [regex]::Match($_, '(?m)^  - name: (\S+)').Groups[1].Value
    if ($Step) { $Step -contains $name } else { $name -notlike 'web-*' }
}
$tmp = Join-Path ([IO.Path]::GetTempPath()) 'omni-ci-local'
New-Item -ItemType Directory -Force $tmp | Out-Null
$file = Join-Path $tmp 'check.yml'
[IO.File]::WriteAllText($file, ($parts[0] + ($keep -join '')), (New-Object Text.UTF8Encoding $false))
# a step that depends on a removed one would wait for ever
(Get-Content $file -Raw) -replace '(?ms)^    depends_on:\n(      - .*\n)+', '' | Set-Content $file -NoNewline -Encoding utf8

woodpecker-cli exec --backend-engine docker --repo-path $root --pipeline-event pull_request --commit-branch ci-local $file
if ($LASTEXITCODE -ne 0) { throw "CI steps failed (exit code $LASTEXITCODE)" }
Write-Host 'The CI pipeline passes locally.' -ForegroundColor Green
