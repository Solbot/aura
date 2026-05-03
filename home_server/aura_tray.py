#!/usr/bin/env python3
"""
aura_tray.py — System tray controller for AURA Home LLM Server

Manages aura_llm_server.py from a cross-platform system tray icon.
The icon is green when the server is running, red when stopped.

Requirements (pip install):
    pystray
    pillow

Linux system packages (one of):
    sudo apt install gir1.2-ayatana-appindicator3-0.1   # Ubuntu 22.04+
    sudo apt install gir1.2-appindicator3-0.1           # older Debian/Ubuntu

macOS / Windows: no extra packages needed.

Usage:
    python aura_tray.py
"""

import os
import sys
import json
import time
import threading
import subprocess
import platform
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print(
        "Missing packages.  Install with:\n"
        "    pip install pystray pillow",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    print("tkinter is not available — settings dialog will not work.", file=sys.stderr)
    tk = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Paths & tier catalogue
# ---------------------------------------------------------------------------

SCRIPT_DIR    = Path(__file__).parent.resolve()
CONFIG_PATH   = SCRIPT_DIR / "aura_server_config.json"
SERVER_SCRIPT = SCRIPT_DIR / "aura_llm_server.py"
ICON_SIZE     = 64

# V6 / V8 / V12 engine tiers
MODEL_TIERS = [
    ("pi",     "V6 — Pi Tier",      "Lightweight 7B model. Runs on Raspberry Pi hardware."),
    ("home",   "V8 — Home Tier",    "Mid-range 13–34B model. Home PC with a GPU recommended."),
    ("remote", "V12 — Remote API",  "Cloud API endpoint (Claude, GPT-4). Maximum capability."),
]
_TIER_LABELS = {k: label for k, label, _ in MODEL_TIERS}
_TIER_DESCS  = {k: desc  for k, _,     desc in MODEL_TIERS}
_TIER_KEYS   = [k for k, _, _ in MODEL_TIERS]

DEFAULT_CONFIG = {
    "llama_server_bin":      "llama-server",
    "model_path":            "",
    "model_url":             "",
    "hf_token":              "",
    "host":                  "0.0.0.0",
    "port":                  8081,
    "llama_server_port":     8080,
    "context_size":          4096,
    "gpu_layers":            -1,
    "threads":               -1,
    "log_file":              "aura_server.log",
    "auto_restart":          True,
    "restart_delay_seconds": 5,
    "model_tier":            "home",
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except Exception:
        return dict(DEFAULT_CONFIG)


def _save_config(updates: dict) -> bool:
    try:
        cfg = _load_config()
        cfg.update(updates)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
            f.write("\n")
        return True
    except Exception as e:
        if tk:
            messagebox.showerror("AURA Home Server", f"Could not save settings:\n{e}")
        return False

# ---------------------------------------------------------------------------
# Icon generation (PIL — no external image files needed)
# ---------------------------------------------------------------------------

def _make_icon(running: bool) -> Image.Image:
    img  = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = (35, 195, 80) if running else (200, 50, 50)
    m    = 4
    draw.ellipse([m, m, ICON_SIZE - m, ICON_SIZE - m], fill=fill)
    # subtle highlight cap
    hl = tuple(min(255, c + 70) for c in fill) + (160,)
    draw.ellipse([15, 10, 32, 25], fill=hl)
    return img

# ---------------------------------------------------------------------------
# Server process management
# ---------------------------------------------------------------------------

_proc: "subprocess.Popen | None" = None
_proc_lock = threading.Lock()


def _server_running() -> bool:
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def _start_server() -> None:
    global _proc
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return
        kw: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        _proc = subprocess.Popen([sys.executable, str(SERVER_SCRIPT)], **kw)


def _stop_server() -> None:
    global _proc
    with _proc_lock:
        proc  = _proc
        _proc = None
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

# ---------------------------------------------------------------------------
# Settings dialog (tkinter)
# ---------------------------------------------------------------------------

_settings_open = False
_settings_lock = threading.Lock()


def _show_settings() -> None:
    global _settings_open
    with _settings_lock:
        if _settings_open:
            return
        _settings_open = True
    try:
        _run_settings_dialog()
    finally:
        with _settings_lock:
            _settings_open = False


def _run_settings_dialog() -> None:
    if not tk:
        print("tkinter unavailable — cannot open settings.", file=sys.stderr)
        return

    cfg = _load_config()

    root = tk.Tk()
    root.title("AURA Home Server — Settings")
    root.resizable(False, False)

    P = {"padx": 12, "pady": 5}

    # ── Proxy port ────────────────────────────────────────────────────────
    tk.Label(root, text="Proxy Port:", anchor="w").grid(row=0, column=0, sticky="w", **P)
    port_var = tk.StringVar(value=str(cfg.get("port", 8081)))
    tk.Entry(root, textvariable=port_var, width=8).grid(row=0, column=1, sticky="w", **P)

    # ── Model path ────────────────────────────────────────────────────────
    tk.Label(root, text="Model (.gguf):", anchor="w").grid(row=1, column=0, sticky="w", **P)
    model_var = tk.StringVar(value=cfg.get("model_path", ""))
    mf = tk.Frame(root)
    mf.grid(row=1, column=1, sticky="ew", **P)
    tk.Entry(mf, textvariable=model_var, width=44).pack(side="left")

    def browse_model() -> None:
        path = filedialog.askopenfilename(
            parent=root,
            title="Select GGUF model file",
            filetypes=[("GGUF model", "*.gguf"), ("All files", "*.*")],
        )
        if path:
            model_var.set(path)

    tk.Button(mf, text="Browse…", command=browse_model).pack(side="left", padx=(4, 0))

    # ── llama-server binary ───────────────────────────────────────────────
    tk.Label(root, text="llama-server:", anchor="w").grid(row=2, column=0, sticky="w", **P)
    bin_var = tk.StringVar(value=cfg.get("llama_server_bin", "llama-server"))
    bf = tk.Frame(root)
    bf.grid(row=2, column=1, sticky="ew", **P)
    tk.Entry(bf, textvariable=bin_var, width=44).pack(side="left")

    def browse_bin() -> None:
        ftypes = (
            [("Executable", "*.exe"), ("All files", "*.*")]
            if platform.system() == "Windows"
            else [("All files", "*.*")]
        )
        path = filedialog.askopenfilename(
            parent=root, title="Select llama-server binary", filetypes=ftypes
        )
        if path:
            bin_var.set(path)

    tk.Button(bf, text="Browse…", command=browse_bin).pack(side="left", padx=(4, 0))

    # ── Server tier ───────────────────────────────────────────────────────
    tk.Label(root, text="Server Tier:", anchor="w").grid(row=3, column=0, sticky="w", **P)
    label_list   = [_TIER_LABELS[k] for k in _TIER_KEYS]
    label_to_key = {_TIER_LABELS[k]: k for k in _TIER_KEYS}
    current_key  = cfg.get("model_tier", "home")

    combo = ttk.Combobox(root, values=label_list, state="readonly", width=36)
    combo.set(_TIER_LABELS.get(current_key, label_list[1]))
    combo.grid(row=3, column=1, sticky="w", **P)

    desc_var = tk.StringVar(value=_TIER_DESCS.get(current_key, ""))
    tk.Label(
        root, textvariable=desc_var, wraplength=380,
        justify="left", fg="#666", font=("TkDefaultFont", 9),
    ).grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4))

    def on_tier_change(_event=None) -> None:
        key = label_to_key.get(combo.get(), "home")
        desc_var.set(_TIER_DESCS[key])

    combo.bind("<<ComboboxSelected>>", on_tier_change)

    # ── Buttons ───────────────────────────────────────────────────────────
    ttk.Separator(root, orient="horizontal").grid(
        row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=8
    )
    btn_row = tk.Frame(root)
    btn_row.grid(row=6, column=0, columnspan=2, pady=(0, 12))

    def on_save() -> None:
        try:
            port = int(port_var.get())
        except ValueError:
            messagebox.showerror("AURA Home Server", "Port must be a whole number.", parent=root)
            return
        tier_key = label_to_key.get(combo.get(), "home")
        if _save_config({
            "port":            port,
            "model_path":      model_var.get().strip(),
            "llama_server_bin": bin_var.get().strip(),
            "model_tier":      tier_key,
        }):
            messagebox.showinfo(
                "AURA Home Server",
                "Settings saved.\n\nRestart the server to apply changes.",
                parent=root,
            )
            root.destroy()

    tk.Button(btn_row, text="Save",   command=on_save,       width=10).pack(side="left", padx=6)
    tk.Button(btn_row, text="Cancel", command=root.destroy,  width=10).pack(side="left", padx=6)

    root.mainloop()

# ---------------------------------------------------------------------------
# Tray callbacks
# ---------------------------------------------------------------------------

_icon: "pystray.Icon | None" = None


def _refresh_icon() -> None:
    if _icon is None:
        return
    running     = _server_running()
    _icon.icon  = _make_icon(running)
    _icon.title = f"AURA Home Server — {'Running' if running else 'Stopped'}"


def _on_start(icon, item) -> None:
    def _do() -> None:
        _start_server()
        time.sleep(0.8)
        _refresh_icon()
    threading.Thread(target=_do, daemon=True).start()


def _on_stop(icon, item) -> None:
    def _do() -> None:
        _stop_server()
        _refresh_icon()
    threading.Thread(target=_do, daemon=True).start()


def _on_settings(icon, item) -> None:
    threading.Thread(target=_show_settings, daemon=True).start()


def _on_exit(icon, item) -> None:
    _stop_server()
    icon.stop()

# ---------------------------------------------------------------------------
# Status poller — keeps the icon colour in sync with the process
# ---------------------------------------------------------------------------

def _poll_loop() -> None:
    prev = None
    while True:
        time.sleep(3)
        curr = _server_running()
        if curr != prev:
            prev = curr
            _refresh_icon()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _icon

    _start_server()  # auto-start on tray launch

    _icon = pystray.Icon(
        name="aura_home_server",
        icon=_make_icon(_server_running()),
        title="AURA Home Server",
        menu=pystray.Menu(
            pystray.MenuItem(
                lambda item: "● Running" if _server_running() else "○ Stopped",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start Server",
                _on_start,
                enabled=lambda item: not _server_running(),
            ),
            pystray.MenuItem(
                "Stop Server",
                _on_stop,
                enabled=lambda item: _server_running(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings…", _on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", _on_exit),
        ),
    )

    threading.Thread(target=_poll_loop, daemon=True).start()
    _icon.run()


if __name__ == "__main__":
    main()
