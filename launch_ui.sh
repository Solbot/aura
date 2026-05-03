#!/bin/bash
# launch_ui.sh — desktop launcher for aura_gtk.py.
# Injects venv site-packages so system python3 (which owns gi/GTK)
# also sees sounddevice/faster-whisper from the venv.

AURA_DIR="$(cd "$(dirname "$0")" && pwd)"

VENV_SITE=$(python3 -c "
import glob, os
sites = glob.glob(os.path.join('$AURA_DIR', 'venv', 'lib', 'python3*', 'site-packages'))
print(sites[0] if sites else '')
" 2>/dev/null)
[ -n "$VENV_SITE" ] && export PYTHONPATH="$VENV_SITE"

export PYTHONUNBUFFERED=1
export GTK_A11Y=none
exec python3 "${AURA_DIR}/aura_gtk.py"
