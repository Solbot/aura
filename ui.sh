#!/bin/bash
# start_ui.sh

AURA_DIR="/home/aura/aura"
VENV="$AURA_DIR/venv/bin/python3"
LOG_DIR="$AURA_DIR/logs"
mkdir -p "$LOG_DIR"

exec >> "$LOG_DIR/ui.log" 2>&1
echo "=== AURA UI Startup $(date) ==="

AURA_UID=$(id -u aura)
export XDG_RUNTIME_DIR=/run/user/$AURA_UID

WAYLAND_SOCK=$(ls /run/user/$AURA_UID/wayland-* 2>/dev/null | grep -v lock | head -1)

if [ -n "$WAYLAND_SOCK" ]; then
    echo "[*] Wayland: $(basename $WAYLAND_SOCK)"
    export WAYLAND_DISPLAY=$(basename $WAYLAND_SOCK)
    export SDL_VIDEODRIVER=wayland
else
    echo "[*] No Wayland — using KMS/DRM"
    [ -e /dev/dri/card0 ] && export SDL_VIDEODRIVER=kmsdrm || export SDL_VIDEODRIVER=fbdev
    unset WAYLAND_DISPLAY DISPLAY
fi

export KIVY_WINDOW=sdl2 KIVY_GL_BACKEND=sdl2 PYTHONUNBUFFERED=1
echo "[*] SDL_VIDEODRIVER=$SDL_VIDEODRIVER"

cd "$AURA_DIR"
"$VENV" ui.py
echo "[*] UI exited ($?)"

