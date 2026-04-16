# AURA LLM Server — Home PC primary inference tier
#
# Requirements:
#   - llama.cpp built with CUDA support (for GPU inference)
#   - Python 3.8+
#   - pip install requests   (optional, falls back to urllib)
#
# Quick start (Windows):
#   1. Copy this file to your llama.cpp directory (or anywhere convenient)
#   2. Edit aura_server_config.json — set model_path and llama_server_bin
#   3. python aura_llm_server.py
#   4. On the Pi: /set home_pc_endpoint http://<your-pc-ip>:8081/v1/chat/completions
#
# Quick start (Linux/macOS):
#   Same steps. llama_server_bin is typically "./llama-server" or "llama-server"
#
# To run at Windows login (Task Scheduler):
#   Task Scheduler → Create Task → Trigger: At log on
#   Action: Start a program → python → Arguments: C:\path\to\aura_llm_server.py
#   Or use NSSM (Non-Sucking Service Manager) to wrap it as a Windows service.
#
# To run as a systemd service on Linux:
#   See README_llm_server.md for the service unit example.

import os
import sys
import json
import time
import platform
import threading
import subprocess
import socket
import logging
from pathlib import Path
from datetime import datetime

# http.server — ThreadingHTTPServer available since Python 3.7
from http.server import BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer as _HTTPServer
except ImportError:
    from http.server import HTTPServer as _HTTPServer

# Optional requests, fall back to urllib
try:
    import requests as _requests
    _USE_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    _USE_REQUESTS = False

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
_config:        dict         = {}
_llama_proc:    object       = None   # subprocess.Popen or None
_llama_lock:    threading.Lock = threading.Lock()
_running:       bool         = True
_request_count: int          = 0
_request_lock:  threading.Lock = threading.Lock()
_start_time:    float        = time.time()
_logger:        logging.Logger = None  # set up in main()

# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "llama_server_bin":      "llama-server",
    "model_path":            "",
    "host":                  "0.0.0.0",
    "port":                  8081,
    "llama_server_port":     8080,
    "context_size":          4096,
    "gpu_layers":            -1,
    "threads":               -1,
    "log_file":              "aura_server.log",
    "auto_restart":          True,
    "restart_delay_seconds": 5,
}

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "aura_server_config.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_file: str) -> None:
    """Configure logging to both stdout and a rotating log file."""
    global _logger
    _logger = logging.getLogger("aura_server")
    _logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console — INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    _logger.addHandler(ch)

    # File — DEBUG and above (captures llama output details)
    try:
        log_path = SCRIPT_DIR / log_file
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        _logger.addHandler(fh)
    except Exception as e:
        print(f"Warning: could not open log file '{log_file}': {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """
    Load config from aura_server_config.json.
    Creates the file with defaults if it doesn't exist.
    """
    global _config

    if not CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
                f.write("\n")
            print(f"Created default config: {CONFIG_PATH}")
            print("Edit it to set model_path and llama_server_bin, then restart.")
        except OSError as e:
            print(f"Warning: could not write default config: {e}", file=sys.stderr)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_config = json.load(f)
    except FileNotFoundError:
        user_config = {}
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {CONFIG_PATH}: {e}", file=sys.stderr)
        user_config = {}

    _config = {**DEFAULT_CONFIG, **user_config}
    return _config


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def get_local_ip() -> str:
    """
    Detect the most likely LAN IP address (non-loopback IPv4).
    Connects a UDP socket to a public address to find the active interface.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def get_llama_health() -> dict | None:
    """Query llama-server's own /health. Returns parsed JSON or None."""
    url = f"http://127.0.0.1:{_config['llama_server_port']}/health"
    try:
        if _USE_REQUESTS:
            r = _requests.get(url, timeout=3)
            return r.json()
        else:
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.loads(r.read())
    except Exception:
        return None


def get_gpu_memory_info() -> str | None:
    """Query nvidia-smi for GPU memory usage. Returns a string or None."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().splitlines()[0].split(",")
            if len(parts) == 2:
                used, total = parts[0].strip(), parts[1].strip()
                return f"{used} MB / {total} MB"
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

def proxy_request(
    method: str,
    path: str,
    headers: dict,
    body: bytes,
) -> tuple[int, dict, bytes]:
    """
    Forward a request to the internal llama-server.
    Returns (http_status, response_headers, response_body).
    """
    llama_url = f"http://127.0.0.1:{_config['llama_server_port']}{path}"

    # Strip hop-by-hop headers before forwarding
    _hop_by_hop = {"host", "connection", "transfer-encoding", "keep-alive",
                   "proxy-authenticate", "proxy-authorization", "te", "trailers",
                   "upgrade"}
    fwd_headers = {k: v for k, v in headers.items()
                   if k.lower() not in _hop_by_hop}

    if _USE_REQUESTS:
        try:
            resp = _requests.request(
                method=method,
                url=llama_url,
                headers=fwd_headers,
                data=body,
                timeout=120,
                stream=False,
            )
            return resp.status_code, dict(resp.headers), resp.content
        except _requests.exceptions.ConnectionError:
            err = json.dumps({"error": {"message": "llama-server not reachable", "type": "server_error"}})
            return 503, {"Content-Type": "application/json"}, err.encode()
        except Exception as e:
            err = json.dumps({"error": {"message": str(e), "type": "server_error"}})
            return 500, {"Content-Type": "application/json"}, err.encode()
    else:
        try:
            req = urllib.request.Request(llama_url, data=body, method=method)
            for k, v in fwd_headers.items():
                try:
                    req.add_header(k, v)
                except Exception:
                    pass
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except urllib.error.URLError as e:
            err = json.dumps({"error": {"message": str(e.reason), "type": "server_error"}})
            return 503, {"Content-Type": "application/json"}, err.encode()
        except Exception as e:
            err = json.dumps({"error": {"message": str(e), "type": "server_error"}})
            return 500, {"Content-Type": "application/json"}, err.encode()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class AuraHandler(BaseHTTPRequestHandler):
    """Proxy handler for AURA LLM Server."""

    server_version = "AuraLLMServer/1.0"

    # Suppress the default access log — we write our own
    def log_message(self, format, *args):  # noqa: A002
        pass

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        clean_path = self.path.rstrip("/").split("?")[0]
        if clean_path == "/health":
            self._handle_health()
        else:
            self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")

    # ---- /health ----

    def _handle_health(self) -> None:
        llama_health = get_llama_health()
        gpu_mem      = get_gpu_memory_info()

        with _request_lock:
            req_count = _request_count

        payload = {
            "status": "ok" if llama_health else "degraded",
            "aura_server": {
                "uptime_seconds": int(time.time() - _start_time),
                "request_count":  req_count,
                "proxy_port":     _config["port"],
                "using_requests": _USE_REQUESTS,
            },
            "llama_server": llama_health if llama_health else {"status": "unreachable"},
            "model_path": str(_config.get("model_path", "") or ""),
        }
        if gpu_mem:
            payload["gpu_memory"] = gpu_mem

        body        = json.dumps(payload, indent=2).encode("utf-8")
        http_status = 200 if llama_health else 503

        self.send_response(http_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    # ---- proxy ----

    def _proxy(self, method: str) -> None:
        global _request_count

        t0             = time.time()
        content_length = int(self.headers.get("Content-Length", 0))
        body           = self.rfile.read(content_length) if content_length > 0 else b""

        # Parse body for logging metadata
        approx_tokens = None
        uses_tools    = False
        try:
            if body:
                req_data      = json.loads(body)
                msgs          = req_data.get("messages", [])
                # Rough token estimate: ~4 chars per token
                approx_tokens = sum(
                    len(str(m.get("content") or "")) // 4 for m in msgs
                )
                uses_tools = bool(req_data.get("tools"))
        except Exception:
            pass

        # Build forwarded headers
        fwd = {k: self.headers[k] for k in self.headers}

        status_code, resp_headers, resp_body = proxy_request(method, self.path, fwd, body)

        # Parse response for token counts
        resp_tokens = None
        try:
            rd = json.loads(resp_body)
            usage = rd.get("usage", {})
            if usage:
                resp_tokens = usage.get("completion_tokens") or usage.get("total_tokens")
        except Exception:
            pass

        latency_ms = int((time.time() - t0) * 1000)

        with _request_lock:
            _request_count += 1
            req_num = _request_count

        # Build log line
        tok_str   = ""
        if approx_tokens is not None:
            tok_str = f" ~{approx_tokens}in"
        if resp_tokens is not None:
            tok_str += f"/{resp_tokens}out tok"
        tools_str = " [tools]" if uses_tools else ""
        _logger.info(
            f"#{req_num} {method} {self.path} → {status_code}"
            f" ({latency_ms}ms){tok_str}{tools_str}"
        )

        # Send response
        self.send_response(status_code)

        _skip = {"connection", "transfer-encoding", "content-encoding", "keep-alive"}
        sent_content_length = False
        for k, v in resp_headers.items():
            if k.lower() in _skip:
                continue
            try:
                self.send_header(k, v)
                if k.lower() == "content-length":
                    sent_content_length = True
            except Exception:
                pass

        if not sent_content_length:
            self.send_header("Content-Length", str(len(resp_body)))

        self._cors_headers()
        self.end_headers()
        self.wfile.write(resp_body)


# ---------------------------------------------------------------------------
# llama-server process management
# ---------------------------------------------------------------------------

def _build_llama_args() -> list[str]:
    """Assemble the command-line arguments for llama-server."""
    cfg      = _config
    bin_name = str(cfg["llama_server_bin"])
    bin_path = Path(bin_name)

    # On Windows, append .exe if no extension given and file not found as-is
    if platform.system() == "Windows" and not bin_path.suffix:
        candidate = bin_path.with_suffix(".exe")
        if candidate.exists():
            bin_path = candidate

    args = [str(bin_path)]

    model = cfg.get("model_path", "")
    if model:
        args += ["--model", str(Path(model))]

    args += ["--port",    str(cfg["llama_server_port"])]
    args += ["--host",    "127.0.0.1"]
    args += ["--ctx-size", str(cfg["context_size"])]

    gpu = cfg.get("gpu_layers")
    if gpu is not None and gpu != 0:
        args += ["--n-gpu-layers", str(gpu)]

    threads = cfg.get("threads", -1)
    if threads and threads > 0:
        args += ["--threads", str(threads)]

    return args


def _drain_llama_output(proc: subprocess.Popen) -> None:
    """Read llama-server's stdout/stderr and forward to the log at DEBUG."""
    try:
        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            if line:
                _logger.debug("[llama] %s", line)
    except Exception:
        pass


def start_llama_server() -> bool:
    """
    Spawn llama-server as a child process.
    Returns True on success, False on failure.
    If model_path is empty, does nothing (assumes external server).
    """
    global _llama_proc

    model = _config.get("model_path", "")
    if not model:
        _logger.info("model_path not set — assuming llama-server is running externally.")
        return True

    args = _build_llama_args()
    _logger.info("Starting llama-server: %s", " ".join(args))

    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }

    if platform.system() == "Windows":
        # Avoid a console popup window for the child process
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        with _llama_lock:
            _llama_proc = subprocess.Popen(args, **popen_kwargs)

        t = threading.Thread(
            target=_drain_llama_output,
            args=(_llama_proc,),
            daemon=True,
            name="llama-out",
        )
        t.start()
        return True

    except FileNotFoundError:
        _logger.error("llama-server binary not found: %s", args[0])
        _logger.error("Set 'llama_server_bin' in aura_server_config.json to the correct path.")
        return False
    except PermissionError:
        _logger.error("Permission denied executing: %s", args[0])
        return False
    except Exception as e:
        _logger.error("Failed to start llama-server: %s", e)
        return False


def wait_for_llama_ready(timeout: int = 30) -> bool:
    """
    Poll llama-server's /health endpoint until it responds or timeout expires.
    """
    _logger.info(
        "Waiting for llama-server on port %s (up to %ss)...",
        _config["llama_server_port"],
        timeout,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        # If the child exited already, stop waiting
        with _llama_lock:
            proc = _llama_proc
        if proc and proc.poll() is not None:
            _logger.error("llama-server exited during startup (code %s).", proc.poll())
            return False

        if get_llama_health() is not None:
            _logger.info("llama-server ready.")
            return True

        time.sleep(1)

    _logger.error("llama-server did not become ready within %ss.", timeout)
    return False


def _monitor_llama() -> None:
    """
    Background thread: watch llama-server and restart it on crash (with backoff).
    """
    global _llama_proc, _running

    base_delay = float(_config.get("restart_delay_seconds", 5))
    delay      = base_delay

    while _running:
        time.sleep(2)

        if not _config.get("model_path", ""):
            continue  # External server — don't manage

        with _llama_lock:
            proc = _llama_proc

        if proc is None:
            continue

        exit_code = proc.poll()
        if exit_code is not None and _running:
            _logger.warning(
                "llama-server exited (code %s). Restarting in %.0fs...",
                exit_code, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 120)  # exponential backoff, cap at 2 min

            if _running:
                ok = start_llama_server()
                if ok and wait_for_llama_ready(30):
                    delay = base_delay  # reset backoff on successful restart
        else:
            delay = base_delay  # process running fine — keep reset


def stop_llama_server() -> None:
    """Gracefully terminate the llama-server child process."""
    global _llama_proc

    with _llama_lock:
        proc       = _llama_proc
        _llama_proc = None

    if proc and proc.poll() is None:
        _logger.info("Stopping llama-server...")
        try:
            proc.terminate()
            proc.wait(timeout=10)
            _logger.info("llama-server stopped.")
        except subprocess.TimeoutExpired:
            _logger.warning("llama-server did not stop — killing.")
            proc.kill()
        except Exception as e:
            _logger.warning("Error stopping llama-server: %s", e)


# ---------------------------------------------------------------------------
# Shutdown / signal handling
# ---------------------------------------------------------------------------

def setup_signal_handlers(httpd) -> None:
    """Register OS signal and (on Windows) console control handlers."""
    import signal

    def _graceful_shutdown(signame: str) -> None:
        global _running
        if _running:
            _logger.info("Shutdown requested (%s).", signame)
            _running = False
            threading.Thread(target=httpd.shutdown, daemon=True).start()

    def _sig_handler(signum, frame):
        _graceful_shutdown(f"signal {signum}")

    signal.signal(signal.SIGINT,  _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    if platform.system() == "Windows":
        try:
            import ctypes
            import ctypes.wintypes

            HandlerRoutine = ctypes.WINFUNCTYPE(
                ctypes.wintypes.BOOL,
                ctypes.wintypes.DWORD,
            )

            def _win_handler(ctrl_type: int) -> bool:
                _graceful_shutdown("Windows console event")
                time.sleep(3)  # Give server time to shut down
                return True

            # Keep a module-level reference to prevent GC
            setup_signal_handlers._win_ctrl_handler = HandlerRoutine(_win_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(
                setup_signal_handlers._win_ctrl_handler, True
            )
        except Exception as e:
            _logger.warning("Could not register Windows console handler: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _running

    load_config()
    setup_logging(_config.get("log_file", "aura_server.log"))

    _logger.info("=" * 60)
    _logger.info("AURA LLM Server starting...")
    _logger.info("=" * 60)

    model = _config.get("model_path", "")
    if model:
        gpu_str = "all" if _config.get("gpu_layers") == -1 else str(_config.get("gpu_layers"))
        _logger.info("Model:      %s", model)
        _logger.info("GPU layers: %s", gpu_str)
        _logger.info("Context:    %s tokens", _config["context_size"])
        _logger.info("Proxy port: %s  →  llama-server port: %s",
                     _config["port"], _config["llama_server_port"])
    else:
        _logger.info("Model:      (external — using pre-running llama-server)")
        _logger.info("Proxy port: %s  →  llama-server port: %s",
                     _config["port"], _config["llama_server_port"])

    # Start llama-server child process (if model_path is set)
    if model:
        _logger.info("Starting llama-server on port %s...", _config["llama_server_port"])
        if not start_llama_server():
            sys.exit(1)
        if not wait_for_llama_ready(timeout=30):
            _logger.error("Giving up — check binary path and model path in aura_server_config.json.")
            stop_llama_server()
            sys.exit(1)

    # Auto-restart monitor
    if _config.get("auto_restart", True) and model:
        monitor = threading.Thread(
            target=_monitor_llama,
            daemon=True,
            name="llama-monitor",
        )
        monitor.start()

    # Bind the proxy HTTP server
    host = _config.get("host", "0.0.0.0")
    port = int(_config.get("port", 8081))

    try:
        httpd = _HTTPServer((host, port), AuraHandler)
    except OSError as e:
        _logger.error("Could not bind to %s:%s — %s", host, port, e)
        stop_llama_server()
        sys.exit(1)

    setup_signal_handlers(httpd)

    local_ip = get_local_ip()
    _logger.info("-" * 60)
    _logger.info("AURA proxy listening on  http://%s:%s", host, port)
    _logger.info("Pi endpoint:             http://%s:%s/v1/chat/completions", local_ip, port)
    _logger.info("Health check:            http://%s:%s/health", local_ip, port)
    _logger.info("Press Ctrl+C to stop.")
    _logger.info("-" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        stop_llama_server()
        _logger.info("AURA LLM Server stopped.")


if __name__ == "__main__":
    main()
