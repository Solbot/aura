# tiles/pisugar3_tile.py
# Battery status tile for AURA — powered by the PiSugar 3 Plus hardware module.
#
# Provides:
#   - aura_context injection (battery level/status in AURA's system prompt)
#   - DataSource for tile queries and status summaries

import time


# ---------------------------------------------------------------------------
# Availability probe (used by tile_registry._probe)
# ---------------------------------------------------------------------------

def _probe_pisugar3():
    try:
        import hardware
        device = hardware.get("pisugar3")
        return device is not None and device.is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tile definition
# ---------------------------------------------------------------------------

TILE = {
    "id":          "pisugar3_battery",
    "name":        "Battery Status",
    "category":    "hardware",
    "description": "PiSugar 3 Plus battery level and charging status",
    "aura_context": "Battery: {battery_level}% ({status})",
    "requires": {
        "hardware": [
            {"name": "pisugar3", "probe": _probe_pisugar3},
        ],
    },
}


# ---------------------------------------------------------------------------
# DataSource — polled by tile_registry for state queries and aura_context
# ---------------------------------------------------------------------------

class DataSource:
    _POLL_INTERVAL = 30  # seconds

    def __init__(self):
        self._last_poll = 0.0
        self._state     = {}

    def _refresh(self):
        now = time.time()
        if now - self._last_poll < self._POLL_INTERVAL and self._state:
            return
        try:
            import hardware
            device = hardware.get("pisugar3")
            if device:
                raw = device.get_state()
                level    = raw.get("battery_level")
                charging = raw.get("is_charging")
                plugged  = raw.get("is_power_plugged")

                if charging:
                    status = "charging"
                    icon   = "⚡"
                elif plugged:
                    status = "plugged in"
                    icon   = "🔌"
                else:
                    status = "on battery"
                    icon   = "🔋"

                self._state = {
                    "available":     raw.get("available", False),
                    "battery_level": level,
                    "is_charging":   charging,
                    "is_power_plugged": plugged,
                    "battery_voltage":  raw.get("battery_voltage"),
                    "status":        status,
                    "icon":          icon,
                    "display":       f"{icon} {level}%" if level is not None else f"{icon} --%",
                }
        except Exception:
            pass
        self._last_poll = now

    def get_state(self):
        self._refresh()
        return dict(self._state)
