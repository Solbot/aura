# hardware/pisugar3.py
# PiSugar 3 Plus hardware driver for AURA.
# Communicates with the pisugar-server daemon via Unix domain socket.
#
# pisugar-server must be installed and running:
#   curl -s https://cdn.pisugar.com/release/pisugar-server.sh | sudo bash
#   sudo systemctl enable --now pisugar-server
#
# Protocol: send "get <key>\n", receive "<key>: <value>\n"
# Socket:   /tmp/pisugar-server.sock  (default; configurable via DB key pisugar3_socket)

import os
import socket
import threading
import time

import tools
import db

DEVICE_ID   = "pisugar3"
DEVICE_NAME = "PiSugar 3 Plus"

_SOCKET_DEFAULT = "/tmp/pisugar-server.sock"
_CACHE_TTL      = 30   # seconds — state is re-read at most once per interval

_lock       = threading.Lock()
_cache      = {}
_cache_time = 0.0


# ---------------------------------------------------------------------------
# Socket communication
# ---------------------------------------------------------------------------

def _socket_path():
    return db.get("pisugar3_socket") or _SOCKET_DEFAULT


def _query(command):
    """Send one text command to pisugar-server; return the value string or None."""
    try:
        path = _socket_path()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(path)
            s.sendall((command + "\n").encode())
            raw = b""
            while True:
                chunk = s.recv(256)
                if not chunk:
                    break
                raw += chunk
                if b"\n" in raw:
                    break
        text = raw.decode("utf-8", errors="replace").strip()
        # Response: "battery: 85.30"
        if ": " in text:
            return text.split(": ", 1)[1]
        return text or None
    except Exception:
        return None


def _read_state():
    """Read all relevant values from pisugar-server; return state dict."""
    level_s   = _query("get battery")
    charge_s  = _query("get battery_charging")
    plugged_s = _query("get battery_power_plugged")
    voltage_s = _query("get battery_v")

    state = {
        "available":         False,
        "battery_level":     None,
        "is_charging":       None,
        "is_power_plugged":  None,
        "battery_voltage":   None,
    }

    if level_s is not None:
        try:
            state["battery_level"] = round(float(level_s))
            state["available"]     = True
        except (ValueError, TypeError):
            pass

    if charge_s is not None:
        state["is_charging"] = charge_s.strip().lower() == "true"

    if plugged_s is not None:
        state["is_power_plugged"] = plugged_s.strip().lower() == "true"

    if voltage_s is not None:
        try:
            state["battery_voltage"] = round(float(voltage_s), 2)
        except (ValueError, TypeError):
            pass

    return state


# ---------------------------------------------------------------------------
# Public device API
# ---------------------------------------------------------------------------

def is_available():
    """Return True if pisugar-server socket exists and accepts connections."""
    path = _socket_path()
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(path)
        return True
    except Exception:
        return False


def get_state():
    """Return cached battery state dict; re-reads if cache is stale."""
    global _cache, _cache_time
    with _lock:
        if time.time() - _cache_time < _CACHE_TTL and _cache:
            return dict(_cache)
        state = _read_state()
        _cache      = state
        _cache_time = time.time()
        return dict(_cache)


def invalidate_cache():
    """Force next get_state() call to re-read from hardware."""
    global _cache_time
    with _lock:
        _cache_time = 0.0


# ---------------------------------------------------------------------------
# Tool: battery_status (available to AURA's LLM)
# ---------------------------------------------------------------------------

def _tool_battery_status():
    state = get_state()
    if not state.get("available"):
        return (
            "Battery status unavailable — "
            "PiSugar 3 not detected or pisugar-server daemon is not running."
        )

    level   = state.get("battery_level")
    charging = state.get("is_charging")
    plugged  = state.get("is_power_plugged")
    voltage  = state.get("battery_voltage")

    parts = [f"Battery: {level}%"]
    if charging:
        parts.append("Charging")
    elif plugged:
        parts.append("Plugged in (fully charged)")
    else:
        parts.append("Running on battery")
    if voltage is not None:
        parts.append(f"{voltage} V")

    return " · ".join(parts)


tools.register(
    name        = "battery_status",
    description = (
        "Get the current battery level and charging status from the PiSugar 3 Plus "
        "battery module. Returns percentage, charging state, and voltage."
    ),
    parameters  = {"type": "object", "properties": {}, "required": []},
    function    = _tool_battery_status,
    permission  = tools.FREE,
)


# ---------------------------------------------------------------------------
# Self-register in hardware registry
# ---------------------------------------------------------------------------

class _PiSugar3Device:
    device_id    = DEVICE_ID
    name         = DEVICE_NAME
    is_available = staticmethod(is_available)
    get_state    = staticmethod(get_state)


import hardware
hardware.register(DEVICE_ID, _PiSugar3Device())
