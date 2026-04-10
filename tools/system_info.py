# tools/system_info.py
# Full system awareness tool — Aether's proprioception.
# Covers all available Pi 5 sensors plus system state.
# PiSugar battery support placeholder ready for when hardware arrives.

import tools
import os
import subprocess
import glob
from datetime import datetime, timedelta

# --- Hwmon paths (verified for this Pi) ---
HWMON_CPU   = "/sys/class/hwmon/hwmon0"   # cpu_thermal
HWMON_NVME  = "/sys/class/hwmon/hwmon1"   # nvme
HWMON_ADC   = "/sys/class/hwmon/hwmon2"   # rp1_adc
HWMON_FAN   = "/sys/class/hwmon/hwmon3"   # pwmfan
HWMON_VOLT  = "/sys/class/hwmon/hwmon4"   # rpi_volt

def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default

def _read_int(path, default=None):
    v = _read(path)
    try:
        return int(v)
    except Exception:
        return default

def _cpu_temp():
    t = _read_int("/sys/class/thermal/thermal_zone0/temp")
    return round(t / 1000, 1) if t is not None else None

def _nvme_temp():
    t = _read_int(f"{HWMON_NVME}/temp1_input")
    return round(t / 1000, 1) if t is not None else None

def _fan_rpm():
    paths = glob.glob("/sys/devices/platform/cooling_fan/hwmon/*/fan1_input")
    if paths:
        return _read_int(paths[0])
    return None

def _fan_pwm():
    v = _read_int(f"{HWMON_FAN}/pwm1")
    return round(v / 255 * 100, 1) if v is not None else None

def _cpu_freq():
    v = _read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    return round(v / 1000, 0) if v is not None else None  # MHz

def _cpu_throttle():
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"],
                           capture_output=True, text=True, timeout=3)
        val = r.stdout.strip().split("=")[-1]
        code = int(val, 16)
        flags = []
        if code & 0x1:     flags.append("undervoltage detected")
        if code & 0x2:     flags.append("frequency capped")
        if code & 0x4:     flags.append("throttled")
        if code & 0x8:     flags.append("soft temperature limit active")
        if code & 0x10000: flags.append("undervoltage has occurred")
        if code & 0x20000: flags.append("frequency capping has occurred")
        if code & 0x40000: flags.append("throttling has occurred")
        return flags if flags else ["healthy"]
    except Exception:
        return None

def _uptime():
    v = _read("/proc/uptime")
    if v:
        secs = float(v.split()[0])
        td = timedelta(seconds=int(secs))
        hours, rem = divmod(td.seconds, 3600)
        mins, _ = divmod(rem, 60)
        parts = []
        if td.days:  parts.append(f"{td.days}d")
        if hours:    parts.append(f"{hours}h")
        parts.append(f"{mins}m")
        return " ".join(parts)
    return None

def _disk_usage():
    try:
        s = os.statvfs(os.path.expanduser("~"))
        total = s.f_blocks * s.f_frsize
        free  = s.f_bfree  * s.f_frsize
        used  = total - free
        return {
            "total_gb": round(total / 1e9, 1),
            "used_gb":  round(used  / 1e9, 1),
            "free_gb":  round(free  / 1e9, 1),
            "used_pct": round(used  / total * 100, 1)
        }
    except Exception:
        return None

def _ram_usage():
    try:
        lines = {}
        with open("/proc/meminfo") as f:
            for l in f:
                if ":" in l:
                    k, v = l.split(":", 1)
                    lines[k.strip()] = int(v.strip().split()[0])
        total = lines.get("MemTotal", 0)
        free  = lines.get("MemAvailable", 0)
        used  = total - free
        return {
            "total_mb": round(total / 1024, 0),
            "used_mb":  round(used  / 1024, 0),
            "free_mb":  round(free  / 1024, 0),
            "used_pct": round(used  / total * 100, 1)
        }
    except Exception:
        return None

def _network():
    try:
        r = subprocess.run(["ip", "-o", "route", "get", "8.8.8.8"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return {"connected": False}
        parts = r.stdout.split()
        iface = parts[parts.index("dev") + 1] if "dev" in parts else None
        ip    = parts[parts.index("src") + 1] if "src" in parts else None
        return {"connected": True, "interface": iface, "ip": ip}
    except Exception:
        return {"connected": False}

def _gpio_state():
    try:
        r = subprocess.run(["pinctrl", "get"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None

def _pisugur_battery():
    # Placeholder — PiSugar 3 communicates via I2C
    return None

# Map query keywords to handler functions
_QUERY_MAP = {
    "datetime":    lambda: f"Date/time: {datetime.now().strftime('%A %B %d %Y, %I:%M:%S %p')}",
    "date":        lambda: f"Date/time: {datetime.now().strftime('%A %B %d %Y, %I:%M:%S %p')}",
    "time":        lambda: f"Date/time: {datetime.now().strftime('%A %B %d %Y, %I:%M:%S %p')}",
    "uptime":      lambda: f"Uptime: {_uptime()}" if _uptime() else None,
    "temperature": lambda: "\n".join(filter(None, [
                       f"CPU temperature: {_cpu_temp()}°C" + (" ⚠ running hot" if _cpu_temp() and _cpu_temp() > 75 else "") if _cpu_temp() is not None else None,
                       f"NVMe temperature: {_nvme_temp()}°C" if _nvme_temp() is not None else None
                   ])),
    "temp":        None,  # alias, set below
    "fan":         lambda: f"Fan speed: {_fan_rpm()} RPM ({_fan_pwm()}% PWM)" if _fan_rpm() is not None else None,
    "fan_speed":   None,  # alias
    "cpu":         lambda: "\n".join(filter(None, [
                       f"CPU frequency: {_cpu_freq()} MHz" if _cpu_freq() else None,
                       f"CPU status: {', '.join(_cpu_throttle())}" if _cpu_throttle() else None
                   ])),
    "disk":        lambda: f"Disk: {_disk_usage()['used_gb']}GB used of {_disk_usage()['total_gb']}GB ({_disk_usage()['used_pct']}% full, {_disk_usage()['free_gb']}GB free)" if _disk_usage() else None,
    "storage":     None,  # alias
    "ram":         lambda: f"RAM: {_ram_usage()['used_mb']}MB used of {_ram_usage()['total_mb']}MB ({_ram_usage()['used_pct']}% used)" if _ram_usage() else None,
    "memory":      None,  # alias
    "network":     lambda: f"Network: connected via {_network().get('interface')} ({_network().get('ip')})" if _network().get('connected') else "Network: no connectivity",
    "connectivity":None,  # alias
    "gpio":        lambda: f"GPIO state:\n{_gpio_state()}" if _gpio_state() else "GPIO: unable to read state",
    "battery":     lambda: f"Battery: {_pisugur_battery()}" if _pisugur_battery() else "Battery: PiSugar not yet connected",
}
# Set aliases
_QUERY_MAP["temp"]         = _QUERY_MAP["temperature"]
_QUERY_MAP["fan_speed"]    = _QUERY_MAP["fan"]
_QUERY_MAP["storage"]      = _QUERY_MAP["disk"]
_QUERY_MAP["memory"]       = _QUERY_MAP["ram"]
_QUERY_MAP["connectivity"] = _QUERY_MAP["network"]

def get_system_info(query=None):
    """
    Query system state. Returns a human-readable string.
    Accepts single queries or comma/space separated multiple queries.
    query examples: "datetime", "fan", "temperature", "fan,datetime", "all"
    """
    if not query or query.strip().lower() == "all":
        # Run all queries
        queries = ["datetime", "uptime", "temperature", "fan", "cpu", "disk", "ram", "network"]
    else:
        # Split on comma, semicolon or space and clean up
        import re
        queries = [q.strip().lower() for q in re.split(r'[,;\s]+', query) if q.strip()]

    results = []
    seen = set()
    for q in queries:
        # Normalise aliases
        if q in ("date", "time"):
            q = "datetime"
        if q in ("temp",):
            q = "temperature"
        if q in ("fan_speed",):
            q = "fan"
        if q in ("storage",):
            q = "disk"
        if q in ("memory",):
            q = "ram"
        if q in ("connectivity",):
            q = "network"

        if q in seen:
            continue
        seen.add(q)

        fn = _QUERY_MAP.get(q)
        if fn:
            try:
                result = fn()
                if result:
                    results.append(result)
            except Exception as e:
                results.append(f"{q}: error ({e})")

    return "\n".join(results) if results else "No information available for that query."

# Self-register
tools.register(
    name        = "get_system_info",
    description = (
        "Query the Pi's system state — Aether's body awareness. "
        "Use for: current date/time, CPU temperature, NVMe temperature, "
        "fan speed, CPU frequency and throttle status, disk space, RAM, "
        "network connectivity, uptime, GPIO state, or battery level. "
        "Always call this for time/date questions instead of guessing. "
        "You can query multiple things at once by passing comma-separated values."
    ),
    parameters  = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What to query. Single value or comma-separated list. "
                    "Options: datetime, temperature, fan, cpu, disk, ram, "
                    "network, uptime, gpio, battery, all. "
                    "Example: 'fan,datetime' or 'all'"
                )
            }
        },
        "required": []
    },
    function    = get_system_info,
    permission  = tools.FREE
)
