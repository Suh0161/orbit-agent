#!/usr/bin/env bash
set -euo pipefail

ORBIT_YES="${ORBIT_YES:-0}"
ORBIT_INSTALL_MODE="${ORBIT_INSTALL_MODE:-pipx}"

if [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ]; then
  ORBIT_YES="1"
  shift
fi

if [ "${1:-}" = "--venv" ]; then
  ORBIT_INSTALL_MODE="venv"
  shift
fi

DIR="${1:-$(pwd)}"
VENV="${VENV:-.venv}"

info() { echo "[Orbit] $*"; }
warn() { echo "[Orbit] WARNING: $*" >&2; }
err()  { echo "[Orbit] ERROR: $*" >&2; }

show_banner() {
  if [ "${ORBIT_NO_BANNER:-0}" = "1" ]; then
    return 0
  fi

  if [ -n "${ORBIT_BANNER_PATH:-}" ] && [ -f "${ORBIT_BANNER_PATH}" ]; then
    cat "${ORBIT_BANNER_PATH}" || true
    return 0
  fi

  # Default ASCII banner (ASCII-only)
  cat <<'EOF'
 ________  ________  ________  ___  _________
|\   __  \|\   __  \|\   __  \|\  \|\___   ___\
\ \  \|\  \ \  \|\  \ \  \|\ /\ \  \|___ \  \_|
 \ \  \\\  \ \   _  _\ \   __  \ \  \   \ \  \
  \ \  \\\  \ \  \\  \\ \  \|\  \ \  \   \ \  \
   \ \_______\ \__\\ _\\ \_______\ \__\   \ \__\
    \|_______|\|__|\|__|\|_______|\|__|    \|__|
EOF
}

cd "$DIR"
show_banner
info "Installing Orbit into: $DIR"
echo ""
echo "[Orbit] What this installer does:"
echo "  - Installs Orbit in an isolated environment (pipx preferred)"
echo "  - Installs Python dependencies"
echo "  - Optionally installs Playwright browsers if Playwright is available"
echo ""
echo "[Orbit] What it does NOT do:"
echo "  - Does not upload data anywhere"
echo "  - Does not modify your system python"
echo "  - Does not ask for API keys during install (use: orbit onboard)"
echo "  - Does not start Orbit automatically"
echo ""

if [ "$ORBIT_YES" != "1" ]; then
  read -r -p "Proceed with install? (y/N) " ans
  ans="$(echo "${ans:-}" | tr '[:upper:]' '[:lower:]')"
  if [ "$ans" != "y" ] && [ "$ans" != "yes" ]; then
    info "Cancelled."
    exit 0
  fi
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  err "python3 not found. Install Python 3.11+ and rerun."
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'; then
  err "Python 3.11+ is required."
  exit 1
fi

if [ "$ORBIT_INSTALL_MODE" = "pipx" ]; then
  info "Mode: pipx (clean install)"
  if ! command -v pipx >/dev/null 2>&1; then
    info "pipx not found; installing pipx to user site"
    "$PYTHON_BIN" -m pip install --user pipx
  fi
  "$PYTHON_BIN" -m pipx ensurepath >/dev/null 2>&1 || true
  "$PYTHON_BIN" -m pipx install -e . --force

  # Best-effort Playwright install inside pipx venv, if available
  PIPX_HOME="${PIPX_HOME:-$HOME/.local/pipx}"
  ORBIT_PY="$PIPX_HOME/venvs/orbit-agent/bin/python"
  if [ -x "$ORBIT_PY" ] && "$ORBIT_PY" -c 'import playwright' >/dev/null 2>&1; then
    info "Playwright detected; installing browsers"
    "$ORBIT_PY" -m playwright install
  else
    warn "Playwright not installed in Orbit env; skipping browser installation."
  fi
else
  info "Mode: venv ($VENV)"
  if [ ! -x "$VENV/bin/python" ]; then
    info "Creating venv: $VENV"
    "$PYTHON_BIN" -m venv "$VENV"
  fi
  PY="$VENV/bin/python"
  info "Upgrading pip"
  "$PY" -m pip install --upgrade pip
  if [ -f "requirements.txt" ]; then
    info "Installing requirements.txt"
    "$PY" -m pip install -r requirements.txt
  else
    warn "requirements.txt not found; continuing"
  fi
  info "Installing Orbit (editable)"
  "$PY" -m pip install -e .
  if "$PY" -c 'import playwright' >/dev/null 2>&1; then
    info "Playwright detected; installing browsers"
    "$PY" -m playwright install
  else
    warn "Playwright not installed; skipping browser installation."
  fi
fi

info "Done."
echo ""
info "Next steps:"
echo "  1) orbit onboard   (writes .env + orbit_config.yaml)"
echo ""
info "Run Uplink (Telegram):"
if [ "$ORBIT_INSTALL_MODE" = "pipx" ]; then
  echo "  orbit uplink"
  echo "  (If 'orbit' isn't recognized, restart your terminal or run: python3 -m pipx ensurepath)"
else
  echo "  $VENV/bin/python -m orbit_agent.uplink.main"
fi
echo ""
info "Run CLI:"
if [ "$ORBIT_INSTALL_MODE" = "pipx" ]; then
  echo "  orbit chat"
else
  echo "  $VENV/bin/python -m orbit_agent.cli.main chat"
fi

