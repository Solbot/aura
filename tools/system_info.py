# tools/system_info.py
# System awareness tool — date/time, Pi sensors, disk, RAM, network.
# Registers itself with the tool registry at import.

import tools
from datetime import datetime
import subprocess
import os

def _cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None

def _disk_usage():
    try:
        s = os.statvfs(os.path.expanduser("~"))
        total = s.f_blocks * s.f_frsize
        free  = s.f_bfree  * s.f_frsize
        used  = total - free
        pct   = round(used / total * 100, 1)
        return {
            "total_gb": round(total / 1e9, 1),
            "used_gb":  round(used  / 1e9, 1),
            "free_gb":  round(free  / 1e9, 1),
            "used_pct": pct
        }
    except Exception:
        return None

def _ram_usage():
    try:
        with open("/proc/meminfo") as f:
            lines = {l.split(":")[0]: int(l.split(":")[1].strip().split()[0])
                     for l in f.readlines() if ":" in l}
        total = lines.get("MemTotal", 0)
        free  = lines.get("MemAvailable", 0)
        used  = total - free
        pct   = round(used / total * 100, 1)
        return {
            "total_mb": round(total / 1024, 0),
            "used_mb":  round(used  / 1024, 0),
            "free_mb":  round(free  / 1024, 0),
            "used_pct": pct
        }
    except Exception:
        return None

def _network():
    try:
        result = subprocess.run(
            ["ip", "-o", "route", "get", "8.8.8.8"],
            capture_output=True, text=True, timeout=3
        )
        connected = result.returncode == 0
        iface = None
        ip    = None
        if connected:
            parts = result.stdout.split()
            if "dev" in parts:
                iface = parts[parts.index("dev") + 1]
            if "src" in parts:
                ip = parts[parts.index("src") + 1]
        return {"connected": connected, "interface": iface, "ip": ip}
    except Exception:
        return {"connected": False, "interface": None, "ip": None}

def get_system_info(query=None):
    """
    Query system state.
    query: datetime | temperature | disk | ram | network | all
    Returns a human-readable string.
    """
    now     = datetime.now()
    query   = (query or "all").lower()
    results = []

    if query in ("datetime", "date", "time", "all"):
        results.append(f"Date/time: {now.strftime('%A %B %d %Y, %I:%M %p')}")

    if query in ("temperature", "temp", "all"):
        t = _cpu_temp()
        if t is not None:
            warn = " (running hot)" if t > 75 else ""
            results.append(f"CPU temperature: {t}°C{warn}")

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
            results.append(f"Network: connected via {n['interface']} ({n['ip']})")
        else:
            results.append("Network: no connectivity")

    return "\n".join(results) if results else "No information available for that query."

# Self-register with the tool registry
tools.register(
    name        = "get_system_info",
    description = (
        "Query the Pi's system state. Use this to answer questions about the current "
        "date or time, CPU temperature, disk space, RAM usage, or network connectivity. "
        "Always call this when the user asks what time or date it is."
    ),
    parameters  = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to query: datetime, temperature, disk, ram, network, or all",
                "enum": ["datetime", "temperature", "disk", "ram", "network", "all"]
            }
        },
        "required": []
    },
    function    = get_system_info,
    permission  = tools.FREE
)
