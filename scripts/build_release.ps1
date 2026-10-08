<#
.SYNOPSIS
    Builds the Windows installer, the portable archive and SHA256SUMS.txt of a release, and publishes it.

.DESCRIPTION
    Releases are built on a Windows machine (CI runs on Linux). Run it from a clean checkout of the release commit:

        uv run --no-sync python scripts/release.py 1.0.2     # in the chore(release) pull request, then merge it
        git tag -a v1.0.2 -m v1.0.2 ; git push origin v1.0.2
        .\scripts\build_release.ps1 1.0.2 -Publish

    Needs uv, Bun (with a read:packages token for @wissem-industries/ui in ~/.npmrc), Rust with the MSVC toolchain
    (Visual Studio Build Tools, "Desktop development with C++"), Inno Setup 6 and the GitHub CLI.
    Without -Publish the files are only written to dist\.
#>
param(
    [Parameter(Mandatory)][string]$Version,
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

Step 'Version matches pyproject.toml' {
    $declared = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
    if ($declared -ne $Version) { throw "pyproject.toml says $declared, not $Version" }
}

if ($Publish) {
    Step 'Release commit is tagged and the tree is clean' {
        if (git status --porcelain) { throw 'the working tree has changes' }
        $tag = git rev-parse "v$Version^{commit}" 2>$null
        if ($LASTEXITCODE -ne 0) { throw "the tag v$Version does not exist: create and push it first" }
        if ($tag -ne (git rev-parse HEAD)) { throw "v$Version does not point at the checked out commit" }
        $global:LASTEXITCODE = 0
    }
}

Step 'Web interface' {
    Push-Location web
    try {
        bun install --frozen-lockfile
        if ($LASTEXITCODE -eq 0) { bun run build }
    }
    finally { Pop-Location }
}

Step 'Python environment' { uv sync --locked }

Step 'Rust core' {
    if (Test-Path wheels) { Remove-Item wheels -Recurse -Force }
    uvx maturin build --release -m native/Cargo.toml -o wheels
    if ($LASTEXITCODE -eq 0) { uv pip install --reinstall --find-links wheels omni_native }
}

Step 'Tests' { uv run --no-sync pytest -q }

Step 'Application' {
    uv run --no-sync pyinstaller packaging/omni.spec --noconfirm --distpath dist --workpath build/pyi
}

Step 'Smoke test of the package' { uv run --no-sync python scripts/smoke_package.py }

Step 'Installer and archive' {
    $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found' }
    & $iscc "/DAppVersion=$Version" packaging\omni.iss
    if ($LASTEXITCODE -ne 0) { return }
    $zip = "dist\Omni-$Version-windows.zip"
    Compress-Archive -Path dist\Omni\* -DestinationPath $zip -Force
    $sums = Get-ChildItem "dist\Omni-Setup-$Version.exe", $zip | ForEach-Object {
        "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower())  $($_.Name)"
    }
    # LF line endings, so that `sha256sum -c` reads the file as it is
    [IO.File]::WriteAllText((Join-Path (Get-Location) 'dist\SHA256SUMS.txt'), (($sums -join "`n") + "`n"), [Text.Encoding]::ASCII)
}

Step 'Release notes' {
    $env:PYTHONIOENCODING = 'utf-8'
    cmd /c "uv run --no-sync python scripts\release_notes.py $Version > dist\notes.md"
}

if ($Publish) {
    Step 'Publish' {
        gh release create "v$Version" "dist\Omni-Setup-$Version.exe" "dist\Omni-$Version-windows.zip" dist\SHA256SUMS.txt `
            --title "omni v$Version" --notes-file dist\notes.md --verify-tag
    }
}
else {
    Write-Host "Built in dist\ (not published: add -Publish)." -ForegroundColor Green
}
