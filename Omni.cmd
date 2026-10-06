@echo off
rem Omni : double-clic pour ouvrir l'application (premier lancement : dependances, coeur Rust, interface web).
setlocal
title omni
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo [omni] uv est introuvable : https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)

uv sync --quiet --inexact
if errorlevel 1 (
  echo [omni] uv sync a echoue.
  pause
  exit /b 1
)

uv run --no-sync python -c "import omni_native as m; m.version" >nul 2>nul
if errorlevel 1 (
  where cargo >nul 2>nul
  if errorlevel 1 (
    echo [omni] le coeur Rust est absent et cargo est introuvable : https://rustup.rs
    pause
    exit /b 1
  )
  echo [omni] construction du coeur Rust, une seule fois...
  uv run --no-sync python -m omni native --build
)

if not exist "web\.output\public\index.html" (
  where bun >nul 2>nul
  if errorlevel 1 (
    echo [omni] l'interface web n'est pas construite et bun est introuvable : https://bun.sh
    pause
    exit /b 1
  )
  echo [omni] construction de l'interface web, une seule fois...
  pushd web
  call bun install --silent
  call bun run build
  popd
)

uv run --no-sync python -m omni app
if errorlevel 1 pause
endlocal
