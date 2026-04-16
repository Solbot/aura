#!/usr/bin/env python3
"""
check_aura_server.py — Quick reachability check for the AURA home PC LLM server.

The Pi can call this before committing to the home PC as its primary endpoint.

Usage:
    python3 check_aura_server.py <endpoint> [timeout_seconds]

    endpoint — the full /v1/chat/completions URL, or just the base URL
    timeout  — connection timeout in seconds (default: 2)

Examples:
    python3 check_aura_server.py http://192.168.1.100:8081/v1/chat/completions
    python3 check_aura_server.py http://192.168.1.100:8081
    python3 check_aura_server.py http://192.168.1.100:8081 5

Exit codes:
    0 — server reachable and healthy
    1 — server unreachable or unhealthy
"""

import sys
import json

# ---------------------------------------------------------------------------
# HTTP backend — requests if available, urllib as fallback
# ---------------------------------------------------------------------------

try:
    import requests as _requests

    def _get(url: str, timeout: float) -> tuple[int, dict]:
        r = _requests.get(url, timeout=timeout)
        return r.status_code, r.json()

except ImportError:
    import urllib.request
    import urllib.error

    def _get(url: str, timeout: float) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def check(endpoint: str, timeout: float = 2.0) -> bool:
    """
    Check whether the AURA LLM server at `endpoint` is reachable and healthy.

    Parameters
    ----------
    endpoint : str
        Full /v1/chat/completions URL **or** bare base URL.
    timeout : float
        Connection timeout in seconds.

    Returns
    -------
    bool
        True if the server is up and reports status "ok".
    """
    base       = endpoint.replace("/v1/chat/completions", "").rstrip("/")
    health_url = f"{base}/health"

    try:
        status_code, data = _get(health_url, timeout)
    except Exception as e:
        print(f"✗  AURA server not reachable at {base}")
        print(f"   {e}")
        return False

    if status_code == 200:
        server_status = data.get("status", "unknown")
        print(f"✓  AURA server reachable at {base}")
        print(f"   Status:     {server_status}")

        llama = data.get("llama_server", {})
        print(f"   llama.cpp:  {llama.get('status', 'unknown')}")

        model = data.get("model_path", "")
        if model:
            print(f"   Model:      {model}")

        gpu = data.get("gpu_memory")
        if gpu:
            print(f"   GPU memory: {gpu}")

        aura_info = data.get("aura_server", {})
        uptime = aura_info.get("uptime_seconds")
        if uptime is not None:
            m, s = divmod(uptime, 60)
            h, m = divmod(m, 60)
            uptime_str = (f"{h}h " if h else "") + (f"{m}m " if m else "") + f"{s}s"
            print(f"   Uptime:     {uptime_str}")

        req_count = aura_info.get("request_count")
        if req_count is not None:
            print(f"   Requests:   {req_count}")

        return server_status == "ok"

    else:
        print(f"✗  AURA server returned HTTP {status_code} from {health_url}")
        return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    endpoint = sys.argv[1]
    timeout  = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

    ok = check(endpoint, timeout)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
