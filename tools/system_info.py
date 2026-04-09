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
    # Try glob in case hwmon index shifts
    paths = glob.glob("/sys/devices/platform/cooling_fan/hwmon/*/fan1_input")
    if paths:
        v = _read_int(paths[0])
        return v
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
        if code & 0x1:   flags.append("undervoltage detected")
        if code & 0x2:   flags.append("frequency capped")
        if code & 0x4:   flags.append("throttled")
        if code & 0x8:   flags.append("soft temperature limit")
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
        if td.days:    parts.append(f"{td.days}d")
        if hours:      parts.append(f"{hours}h")
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
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    except Exception:
        return None

def _pisugur_battery():
    # Placeholder — PiSugar 3 communicates via I2C
    # Will be implemented when hardware arrives
    return None

def get_system_info(query=None):
    """
    Query system state. Returns a human-readable string.
    query: datetime | temperature | fan | disk | ram | network |
           cpu | gpio | uptime | battery | all
    """
    query   = (query or "all").lower()
    results = []

    if query in ("datetime", "date", "time", "all"):
        now = datetime.now()
        results.append(f"Date/time: {now.strftime('%A %B %d %Y, %I:%M:%S %p')}")

    if query in ("uptime", "all"):
        u = _uptime()
        if u:
            results.append(f"Uptime: {u}")

    if query in ("temperature", "temp", "all"):
        ct = _cpu_temp()
        nt = _nvme_temp()
        if ct is not None:
            warn = " ⚠ running hot" if ct > 75 else ""
            results.append(f"CPU temperature: {ct}°C{warn}")
        if nt is not None:
            results.append(f"NVMe temperature: {nt}°C")

    if query in ("fan", "all"):
        rpm = _fan_rpm()
        pwm = _fan_pwm()
        if rpm is not None:
            results.append(f"Fan speed: {rpm} RPM ({pwm}% PWM)")

    if query in ("cpu", "all"):
        freq = _cpu_freq()
        throttle = _cpu_throttle()
        if freq:
            results.append(f"CPU frequency: {freq} MHz")
        if throttle:
            results.append(f"CPU status: {', '.join(throttle)}")

    if query in ("disk", "storage", "all"):
        d = _disk_usage()
        if d:
            results.append(
                f"Disk: {d['used_gb']}GB used of {d['total_gb']}GB "
                f"({d['used_pct']}% full, {d['free_gb']}GB free)"
            )

    if query in ("ram", "memory", "all"):
        r = _ram_usage()
        if r:
            results.append(
                f"RAM: {r['used_mb']}MB used of {r['total_mb']}MB "
                f"({r['used_pct']}% used)"
            )

    if query in ("network", "connectivity", "all"):
        n = _network()
        if n["connected"]:
            results.append(f"Network: connected via {n.get('interface')} ({n.get('ip')})")
        else:
            results.append("Network: no connectivity")

    if query in ("gpio",):
        g = _gpio_state()
        if g:
            results.append(f"GPIO state:\n{g}")
        else:
            results.append("GPIO: unable to read state")

    if query in ("battery",):
        b = _pisugur_battery()
        if b:
            results.append(f"Battery: {b}")
        else:
            results.append("Battery: PiSugar not yet connected")

    return "\n".join(results) if results else "No information available for that query."

# Self-register
tools.register(
    name        = "get_system_info",
    description = (
        "Query the Pi's system state — Aether's body awareness. "
        "Use for: current date/time, CPU temperature, NVMe temperature, "
        "fan speed, CPU frequency and throttle status, disk space, RAM, "
        "network connectivity, uptime, GPIO state, or battery level. "
        "Always call this for time/date questions instead of guessing."
    ),
    parameters  = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to query",
                "enum": ["datetime", "temperature", "fan", "cpu", "disk",
                         "ram", "network", "uptime", "gpio", "battery", "all"]
            }
        },
        "required": []
    },
    function    = get_system_info,
    permission  = tools.FREE
)
