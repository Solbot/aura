#!/bin/bash
# launch_ui.sh — clean launcher for aura_gtk.py as a system service.
# Sets Wayland display and injects venv site-packages via PYTHONPATH so
# system python3 (which owns gi/GTK) also sees sounddevice/faster-whisper.

AURA_DIR="$(cd "$(dirname "$0")" && pwd)"

# Inject venv site-packages (STT dependencies live there)
VENV_SITE=$(python3 -c "
import glob, os
sites = glob.glob(os.path.join('$AURA_DIR', 'venv', 'lib', 'python3*', 'site-packages'))
print(sites[0] if sites else '')
" 2>/dev/null)
[ -n "$VENV_SITE" ] && export PYTHONPATH="$VENV_SITE"

# Detect Wayland socket
XDG_RT="/run/user/$(id -u)"
WD=$(ls "${XDG_RT}"/wayland-* 2>/dev/null | grep -v lock | head -1)
export WAYLAND_DISPLAY="$(basename "${WD:-wayland-0}")"
export XDG_RUNTIME_DIR="${XDG_RT}"
export PYTHONUNBUFFERED=1

exec python3 "${AURA_DIR}/aura_gtk.py"
