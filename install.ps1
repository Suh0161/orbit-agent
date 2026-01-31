param(
  [string]$Dir = (Get-Location).Path,
  [string]$Venv = ".venv",
  [string]$BannerPath = "",
  [switch]$NoBanner,
  [switch]$Yes,
  [ValidateSet("pipx","venv")]
  [string]$Mode = "pipx",
  [switch]$AllowVenvFallback
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "[Orbit] $msg" }
function Write-Warn($msg) { Write-Host "[Orbit] WARNING: $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[Orbit] ERROR: $msg" -ForegroundColor Red }

Set-Location $Dir

function Get-UserPythonScriptsDir {
  try {
    $base = (& python -c "import site; print(site.getuserbase())").Trim()
    if ($base) {
      $scripts = Join-Path $base "Scripts"
      return $scripts
    }
  } catch { }
  return $null
}

function Get-PythonTag {
  # "311" for Python 3.11
  try {
    return (& python -c "import sys; print(f'{sys.version_info[0]}{sys.version_info[1]}')").Trim()
  } catch {
    return ""
  }
}

function Resolve-PipxExe {
  # Prefer a pipx.exe on PATH
  try {
    $cmd = Get-Command pipx -ErrorAction Stop
    if ($cmd -and $cmd.Source) { return $cmd.Source }
  } catch { }

  # Then try user scripts dir
  $scriptsDir = Get-UserPythonScriptsDir
  if ($scriptsDir) {
    $pipxExe = Join-Path $scriptsDir "pipx.exe"
    if (Test-Path $pipxExe) { return $pipxExe }
  }

  # Then try the common Roaming path explicitly (pip --user on Windows)
  try {
    $tag = Get-PythonTag
    if ($env:APPDATA -and $tag) {
      $p1 = Join-Path $env:APPDATA ("Python\Python{0}\Scripts\pipx.exe" -f $tag)
      if (Test-Path $p1) { return $p1 }
    }
    if ($env:APPDATA) {
      $p2 = Join-Path $env:APPDATA "Python\Scripts\pipx.exe"
      if (Test-Path $p2) { return $p2 }
    }
  } catch { }

  return $null
}

function Show-Banner {
  if ($NoBanner) { return }

  $path = $BannerPath
  if (-not $path) { $path = $env:ORBIT_BANNER_PATH }

  if ($path -and (Test-Path $path)) {
    try {
      Write-Host (Get-Content -Raw -LiteralPath $path)
      return
    } catch {
      Write-Warn "Failed to read banner at '$path' (showing default)."
    }
  }

  # Default ASCII banner (ASCII-only to avoid Windows console encoding issues)
  Write-Host @"
 ________  ________  ________  ___  _________
|\   __  \|\   __  \|\   __  \|\  \|\___   ___\
\ \  \|\  \ \  \|\  \ \  \|\ /\ \  \|___ \  \_|
 \ \  \\\  \ \   _  _\ \   __  \ \  \   \ \  \
  \ \  \\\  \ \  \\  \\ \  \|\  \ \  \   \ \  \
   \ \_______\ \__\\ _\\ \_______\ \__\   \ \__\
    \|_______|\|__|\|__|\|_______|\|__|    \|__|
"@
}

Show-Banner

Write-Host ""
Write-Host "[Orbit] What this installer does:"
Write-Host "  - Installs Orbit in an isolated environment (pipx preferred)"
Write-Host "  - Installs Python dependencies"
Write-Host "  - Optionally installs Playwright browsers if Playwright is available"
Write-Host ""
Write-Host "[Orbit] What it does NOT do:"
Write-Host "  - Does not upload data anywhere"
Write-Host "  - Does not modify your system Python"
  Write-Host "  - Does not ask for API keys during install (use: orbit onboard)"
Write-Host "  - Does not start Orbit automatically"
Write-Host ""

if (-not $Yes) {
  # If running non-interactively, Read-Host can hang; require -Yes.
  if (-not $Host.UI -or -not $Host.UI.RawUI) {
    Write-Err "Non-interactive session detected. Re-run with -Yes to proceed."
    exit 1
  }
  $ans = (Read-Host "Proceed with install? (y/N)").Trim().ToLower()
  if ($ans -notin @("y", "yes")) {
    Write-Info "Cancelled."
    exit 0
  }
}

Write-Info "Installing Orbit into: $Dir"

# 1) Check Python (must be 3.11+ per pyproject.toml)
try {
  $pyver = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
} catch {
  Write-Err "Python 3.11+ is required. Install Python, then rerun this installer."
  Write-Info "Tip: on Windows you can install via winget:"
  Write-Host "  winget install -e --id Python.Python.3.11"
  exit 1
}
Write-Info "Python OK: $pyver"

if ($Mode -eq "pipx") {
  # pipx = "clean" like Claw (isolated env, no repo .venv)
  Write-Info "Mode: pipx (clean install)"
  $pipxExe = Resolve-PipxExe
  if (-not $pipxExe) {
    Write-Info "pipx not found; installing pipx to user site"
    try {
      & python -m pip install --user --upgrade pipx
    } catch {
      Write-Warn "pipx install failed. Falling back to venv mode."
      $Mode = "venv"
    }
    $pipxExe = Resolve-PipxExe
  }

  if ($Mode -eq "pipx" -and -not $pipxExe) {
    if ($AllowVenvFallback) {
      Write-Warn "pipx still not available after install. Falling back to venv mode."
      $Mode = "venv"
    } else {
      Write-Err "pipx is not available (and fallback is disabled)."
      Write-Info "Fix: add this to PATH and restart terminal:"
      Write-Host "  $env:APPDATA\Python\Python311\Scripts"
      Write-Info "Then rerun the installer."
      exit 1
    }
  }

  if ($Mode -eq "pipx") {
    # Make sure pipx.exe directory is usable in this process (PATH not required, but helps).
    try {
      $pipxDir = Split-Path -Parent $pipxExe
      if ($pipxDir -and ($env:Path -notlike "*$pipxDir*")) {
        $env:Path = "$pipxDir;$env:Path"
      }
      $pipxBin = $env:PIPX_BIN_DIR
      if (-not $pipxBin) { $pipxBin = (Join-Path $env:USERPROFILE ".local\bin") }
      if ($pipxBin -and (Test-Path $pipxBin) -and ($env:Path -notlike "*$pipxBin*")) {
        $env:Path = "$pipxBin;$env:Path"
      }
    } catch { }

    try {
      & $pipxExe ensurepath | Out-Null
    } catch {
      # ok
    }

    Write-Info "Installing Orbit via pipx (editable)"
    & $pipxExe install -e "." --force
    if ($LASTEXITCODE -ne 0) {
      Write-Err "pipx install failed."
      if ($AllowVenvFallback) {
        Write-Warn "Falling back to venv mode."
        $Mode = "venv"
      } else {
        exit 1
      }
    }
  }

  # Best-effort: run playwright install inside pipx venv (if playwright exists).
  try {
    $pipxHome = $env:PIPX_HOME
    if (-not $pipxHome) { $pipxHome = (Join-Path $env:USERPROFILE ".local\pipx") }
    $orbitVenvPy = Join-Path $pipxHome "venvs\orbit-agent\Scripts\python.exe"
    if (Test-Path $orbitVenvPy) {
      & $orbitVenvPy -c "import playwright" | Out-Null
      Write-Info "Playwright detected; installing browsers"
      & $orbitVenvPy -m playwright install
    } else {
      Write-Warn "Couldn't locate pipx venv python; skipping Playwright browser install."
    }
  } catch {
    Write-Warn "Playwright not installed in Orbit env; skipping browser install."
  }
}

if ($Mode -ne "pipx") {
  # venv mode = local dev
  Write-Info "Mode: venv ($Venv)"
  $venvPath = Join-Path $Dir $Venv
  $venvPy = Join-Path $venvPath "Scripts\python.exe"

  if (-not (Test-Path $venvPy)) {
    Write-Info "Creating venv: $Venv"
    & python -m venv $Venv
  }

  if (-not (Test-Path $venvPy)) {
    Write-Err "Venv creation failed (missing $venvPy)"
    exit 1
  }

  Write-Info "Upgrading pip"
  & $venvPy -m pip install --upgrade pip

  if (Test-Path (Join-Path $Dir "requirements.txt")) {
    Write-Info "Installing requirements.txt"
    & $venvPy -m pip install -r "requirements.txt"
  } else {
    Write-Warn "requirements.txt not found; continuing"
  }

  Write-Info "Installing Orbit (editable)"
  & $venvPy -m pip install -e "."
  if ($LASTEXITCODE -ne 0) {
    Write-Err "Install failed (pip install -e .)."
    exit 1
  }

  try {
    & $venvPy -c "import playwright" | Out-Null
    if ($LASTEXITCODE -eq 0) {
      Write-Info "Playwright detected; installing browsers"
      & $venvPy -m playwright install
    } else {
      Write-Warn "Playwright not installed; skipping browser installation."
    }
  } catch {
    Write-Warn "Playwright check failed; skipping browser installation."
  }
}

Write-Info "Done."
Write-Host ""
Write-Info "Next steps:"
Write-Host "  1) Run onboarding (writes .env + orbit_config.yaml):"
Write-Host "     orbit onboard"
Write-Host ""
Write-Info "Run Uplink (Telegram):"
if ($Mode -eq "pipx") {
  Write-Host "  orbit uplink"
  Write-Host "  (If 'orbit' isn't recognized, restart your terminal. pipx may have just updated PATH.)"
} else {
  Write-Host "  $Venv\Scripts\python -m orbit_agent.uplink.main"
}
Write-Host ""
Write-Info "Run CLI:"
if ($Mode -eq "pipx") {
  Write-Host "  orbit chat"
} else {
  Write-Host "  $Venv\Scripts\python -m orbit_agent.cli.main chat"
}

