#!/usr/bin/env bash
# Install and start a self-hosted tor2 server as a systemd user service.
# Safe to re-run: it never overwrites an existing data directory.
#
# For lawful use only — see README.md.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${TOR2_DATA_DIR:-$HOME/.local/share/tor2-server}"
VENV_DIR="$REPO_DIR/.venv"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$UNIT_DIR/tor2-server.service"
SERVER_NAME="${1:-tor2-server}"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- dependency checks ----------

say "checking dependencies"
command -v tor >/dev/null 2>&1 || die \
  "tor is not installed. Install it first:
     Arch:    sudo pacman -S tor
     Debian:  sudo apt install tor
     Fedora:  sudo dnf install tor"

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || die "python3 is not installed"
"$PYTHON" - <<'PY' || die "python 3.11 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

command -v ffmpeg >/dev/null 2>&1 || warn \
  "ffmpeg not found — members can still post text and images, but video
     uploads will be rejected. Install ffmpeg to enable video."

# ---------- python environment ----------

if [ ! -d "$VENV_DIR" ]; then
  say "creating virtualenv at $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi
say "installing python dependencies"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

# ---------- data directory ----------

FIRST_RUN=0
if [ ! -e "$DATA_DIR/server.db" ]; then
  FIRST_RUN=1
  say "creating server data directory at $DATA_DIR"
else
  say "using existing server data at $DATA_DIR (not touching it)"
fi
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"

# ---------- systemd user service ----------

SERVICE_OK=0
if [ "${TOR2_NO_SYSTEMD:-0}" = "1" ]; then
  warn "TOR2_NO_SYSTEMD=1 — skipping service installation"
elif command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  say "installing systemd user service"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=tor2 server (encrypted chat over Tor)
After=network-online.target

[Service]
Type=simple
ExecStart=$VENV_DIR/bin/python -m tor2.server $DATA_DIR --name $SERVER_NAME
WorkingDirectory=$REPO_DIR
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
UNIT

  # None of this is fatal: if systemd refuses, you can still run the daemon
  # by hand, so fall through to the instructions at the end.
  if systemctl --user daemon-reload 2>/dev/null \
     && systemctl --user enable tor2-server.service >/dev/null 2>&1 \
     && systemctl --user restart tor2-server.service 2>/dev/null; then
    SERVICE_OK=1
    loginctl enable-linger "$USER" >/dev/null 2>&1 || warn \
      "could not enable lingering — the server will stop when you log out
     (run: sudo loginctl enable-linger $USER)"
    say "waiting for tor to publish the onion service (up to 3 minutes)…"
    for _ in $(seq 1 180); do
      ADDR="$(journalctl --user -u tor2-server.service -n 300 --no-pager 2>/dev/null \
              | grep -oE '[a-z2-7]{56}\.onion' | tail -1 || true)"
      [ -n "${ADDR:-}" ] && break
      sleep 1
    done
    INVITE="$(journalctl --user -u tor2-server.service -n 300 --no-pager 2>/dev/null \
              | grep -oE 'ADMIN INVITE \(one use\): .*' | tail -1 | sed 's/.*: //' || true)"
  else
    warn "systemd user service could not be started — run the daemon manually (below)"
  fi
else
  warn "systemd not available — skipping service installation"
fi
ADDR="${ADDR:-}"
INVITE="${INVITE:-}"

# ---------- report ----------

echo
echo "======================================================================"
if [ -n "${ADDR:-}" ]; then
  echo "  tor2 server \"$SERVER_NAME\" is running."
  echo
  echo "  address:  $ADDR"
  if [ -n "${INVITE:-}" ]; then
    echo "  admin invite (one use):  $INVITE"
    echo
    echo "  In tor2, join it with:"
    echo "      /joinserver $ADDR $INVITE"
  else
    echo
    echo "  Mint an invite with:"
    echo "      $VENV_DIR/bin/python -m tor2.server $DATA_DIR --invite"
  fi
else
  echo "  Server installed but no address captured yet."
  echo "  Start it manually to see the address and admin invite:"
  echo "      $VENV_DIR/bin/python -m tor2.server $DATA_DIR --name $SERVER_NAME"
fi
echo
echo "  status:   systemctl --user status tor2-server"
echo "  logs:     journalctl --user -u tor2-server -f"
echo "  stop:     systemctl --user stop tor2-server"
echo "  invite:   $VENV_DIR/bin/python -m tor2.server $DATA_DIR --invite"
echo
echo "  Note: a server operator can read channel messages. Direct messages"
echo "  between two people stay end-to-end encrypted. Lawful use only."
echo "======================================================================"
[ "$FIRST_RUN" = "1" ] || echo
