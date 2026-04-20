#!/bin/bash
# start_ui.sh — run from physical terminal, not SSH

AURA_DIR="/home/aura/aura"
VENV="$AURA_DIR/venv/bin/python3"
LOG_DIR="$AURA_DIR/logs"
mkdir -p "$LOG_DIR"

echo "=== AURA Startup ==="

echo "[*] Detecting display environment..."
WAYLAND_SOCK=$(ls /run/user/$(id -u)/wayland-* 2>/dev/null | grep -v lock | head -1)

if [ -n "$WAYLAND_SOCK" ]; then
    echo "[*] Wayland found: $(basename $WAYLAND_SOCK)"
    export WAYLAND_DISPLAY=$(basename $WAYLAND_SOCK)
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    export SDL_VIDEODRIVER=wayland
else
    echo "[*] No Wayland — using KMS/DRM framebuffer"
    [ -e /dev/dri/card0 ] && export SDL_VIDEODRIVER=kmsdrm || export SDL_VIDEODRIVER=fbdev
    unset WAYLAND_DISPLAY DISPLAY
fi

export KIVY_WINDOW=sdl2 KIVY_GL_BACKEND=sdl2 PYTHONUNBUFFERED=1
echo "[*] SDL_VIDEODRIVER=$SDL_VIDEODRIVER"

echo "[*] Waiting for socket..."
for i in $(seq 1 15); do
    [ -S /tmp/aura.sock ] && echo "[*] Socket ready after ${i}s" && break
    sleep 1
done

if [ ! -S /tmp/aura.sock ]; then
    echo "[ERROR] Socket not found — check $LOG_DIR/aura.log"
    exit 1
fi

echo "[*] Starting UI..."
"$VENV" aura_gtk.py 2>&1 | tee "$LOG_DIR/ui.log"

