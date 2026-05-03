# AURA LLM Server — Home PC Setup

Primary inference tier for Project AURA.  
Runs on your home PC and offloads inference from the Pi when on the home network.

---

## Model Tiers

AURA selects the best available endpoint automatically on each LLM call.  
Think of it as three engine sizes:

| Tier | Label | Where | Suggested model |
|------|-------|-------|-----------------|
| **V6** | Pi Tier | Raspberry Pi 5 | 7B Q4 (local llama-server) |
| **V8** | Home Tier | Home PC with GPU | 13–34B Q5/Q6 (this server) |
| **V12** | Remote API | Cloud endpoint | Claude / GPT-4 |

The Pi probes `/health` on each tier before committing to it. On connection failure the next tier is tried immediately. The result is cached for 60 seconds.

Set tier endpoints on the Pi:
```
/set home_pc_endpoint   http://192.168.1.100:8081/v1/chat/completions
/set remote_api_endpoint https://api.openai.com/v1/chat/completions
```

---

## Architecture

```
Pi (aura.py)
    │
    ├─ home network? → aura_llm_server.py :8081  →  llama-server :8080  →  GPU
    ├─ internet?     → remote API (OpenAI / Anthropic compatible)
    └─ fallback      → local Pi llama-server :8080
```

`aura_llm_server.py` manages the `llama-server` child process and proxies
OpenAI-compatible requests to it.

---

## Files

| File | Purpose |
|------|---------|
| `aura_llm_server.py` | PC-side proxy + llama-server manager (copy this to your PC) |
| `aura_server_config.json` | Server configuration — edit before running |
| `aura_tray.py` | System tray controller (Linux / Windows / macOS) |
| `install_service.py` | Install the server as a system service |
| `uninstall_service.py` | Remove the system service |
| `check_aura_server.py` | Reachability checker |

---

## Quick Start

### Prerequisites

- [llama.cpp](https://github.com/ggml-org/llama.cpp) built with CUDA/Metal (`llama-server` binary)
- Python 3.10+
- A GGUF model file

### 1 — Edit `aura_server_config.json`

```json
{
    "llama_server_bin": "llama-server",
    "model_path": "/path/to/your-model-Q5_K_M.gguf",
    "model_tier": "home",
    "host": "0.0.0.0",
    "port": 8081,
    "llama_server_port": 8080,
    "context_size": 4096,
    "gpu_layers": -1,
    "threads": -1,
    "log_file": "aura_server.log",
    "auto_restart": true,
    "restart_delay_seconds": 5
}
```

- `llama_server_bin` — full path to the binary, or just `"llama-server"` if it's on PATH
- `model_path` — full path to your GGUF model file
- `gpu_layers: -1` — offload all layers to GPU (recommended)
- `model_tier` — label only (`"pi"` / `"home"` / `"remote"`); does not affect inference

### 2 — Install dependencies (optional but recommended)

```bash
pip install requests        # better proxy performance
pip install pystray pillow  # only needed for the tray icon
```

### 3 — Run

**Option A — System tray (recommended for desktop use)**

The tray auto-starts the server and gives you a GUI to start/stop and change settings:

```bash
python aura_tray.py
```

The tray icon is **green** when the server is running, **red** when stopped.  
Right-click for the menu: Start Server · Stop Server · Settings · Exit.

**Option B — Run headlessly in a terminal**

```bash
python aura_llm_server.py
```

**Option C — Run as a system service (starts at boot/login)**

```bash
python install_service.py          # install + start now
python install_service.py status   # check it's running
python uninstall_service.py        # remove the service
```

See the [Service Installation](#service-installation) section for platform details.

### 4 — Configure the Pi

```
/set home_pc_endpoint http://192.168.1.100:8081/v1/chat/completions
```

Replace `192.168.1.100` with the IP shown in the startup output (or run `check_aura_server.py`).

---

## System Tray (`aura_tray.py`)

Cross-platform tray controller that manages the server as a subprocess.

**Dependencies:**
```bash
pip install pystray pillow
# Linux also needs one of:
sudo apt install gir1.2-ayatana-appindicator3-0.1   # Ubuntu 22.04+
sudo apt install gir1.2-appindicator3-0.1           # older Debian/Ubuntu
```

**Icon states:**

| Colour | Meaning |
|--------|---------|
| Green circle | Server is running |
| Red circle | Server is stopped |

**Menu items:**

| Item | Action |
|------|--------|
| ● Running / ○ Stopped | Status indicator (non-clickable) |
| Start Server | Spawn `aura_llm_server.py` |
| Stop Server | Terminate the server gracefully |
| Settings… | Open the settings dialog |
| Exit | Stop the server and close the tray |

**Settings dialog** (right-click → Settings…):

- **Proxy Port** — port the Pi connects to (default 8081)
- **Model (.gguf)** — path to the GGUF model file, with a Browse button
- **llama-server** — path to the llama-server binary, with a Browse button
- **Server Tier** — V6 / V8 / V12 label with description (informational only)

Settings are saved to `aura_server_config.json`. Restart the server after saving for changes to take effect (Stop → Start from the menu, or Exit and relaunch).

> **Note:** Do not run the tray and the system service at the same time.
> Both try to bind the same port and the second will fail to start.

---

## Service Installation (`install_service.py` / `uninstall_service.py`)

Headless alternative to the tray — the server runs automatically at login/boot with no visible window.

```bash
python install_service.py            # install and start
python install_service.py status     # check current state
python install_service.py uninstall  # stop and remove

# Shorthand for removal:
python uninstall_service.py
```

### Linux — systemd user service

Installs to `~/.config/systemd/user/aura-home-server.service`.  
Runs as your user account; no root required.

```bash
# After install_service.py, useful commands:
systemctl --user status  aura-home-server
systemctl --user stop    aura-home-server
systemctl --user restart aura-home-server
journalctl --user -u aura-home-server -f
```

To also auto-start the tray icon at desktop login, create  
`~/.config/autostart/aura-tray.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=AURA Home Server Tray
Exec=python3 /path/to/home_server/aura_tray.py
X-GNOME-Autostart-enabled=true
```

### macOS — launchd user agent

Installs to `~/Library/LaunchAgents/com.aura.homeserver.plist`.  
Log file: `~/Library/Logs/aura_home_server.log`.

```bash
launchctl list com.aura.homeserver          # check status
tail -f ~/Library/Logs/aura_home_server.log # watch logs
```

To also auto-start the tray at login: **System Settings → General → Login Items**, add `aura_tray.py`.

### Windows — Task Scheduler

Creates a task that runs the server at login with a hidden window.

```bat
:: After install, to start/stop manually:
schtasks /Run /TN "AURA Home Server"
schtasks /End /TN "AURA Home Server"
```

To also auto-start the tray: place a shortcut to `aura_tray.py` in  
`shell:startup` (Win+R → `shell:startup`).

> **Firewall:** Allow inbound TCP on port 8081:
> ```
> netsh advfirewall firewall add rule name="AURA LLM Server" dir=in action=allow protocol=TCP localport=8081
> ```
> Or via GUI: Windows Defender Firewall → Advanced Settings → Inbound Rules → New Rule → Port → 8081.
>
> The internal llama-server port (8080) only binds to `127.0.0.1` and is not exposed externally.

---

## Config Reference

| Key | Default | Description |
|-----|---------|-------------|
| `llama_server_bin` | `"llama-server"` | Path to llama-server binary |
| `model_path` | `""` | Path to GGUF model. Empty = use external llama-server |
| `model_url` | `""` | Auto-download URL (`hf://owner/repo/file.gguf` or HTTPS) |
| `hf_token` | `""` | HuggingFace token for gated/private model downloads |
| `model_tier` | `"home"` | Tier label: `"pi"` / `"home"` / `"remote"` (informational) |
| `host` | `"0.0.0.0"` | Address to bind the proxy on |
| `port` | `8081` | Port the Pi connects to |
| `llama_server_port` | `8080` | Internal llama-server port |
| `context_size` | `4096` | Context window size in tokens |
| `gpu_layers` | `-1` | GPU layers to offload. `-1` = all |
| `threads` | `-1` | CPU threads. `-1` = auto |
| `log_file` | `"aura_server.log"` | Log file path (relative to script) |
| `auto_restart` | `true` | Restart llama-server if it crashes |
| `restart_delay_seconds` | `5` | Initial restart delay (doubles on repeated failures, caps at 2 min) |

---

## External llama-server mode

If `model_path` is left empty, `aura_llm_server.py` skips launching llama-server
and just proxies to whatever is already running on `llama_server_port`.
Useful if you manage llama-server separately (e.g. via LM Studio or Ollama).

---

## API Endpoints

| Path | Method | Description |
|------|--------|-------------|
| `/health` | GET | Server status, llama-server health, GPU memory, uptime, request count |
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions (proxied) |
| `/v1/models` | GET | Model list (proxied to llama-server) |
| All other `/v1/*` | any | Proxied transparently to llama-server |

---

## Checking reachability from the Pi

```bash
python3 check_aura_server.py http://192.168.1.100:8081
```

Expected output when healthy:
```
✓  AURA server reachable at http://192.168.1.100:8081
   Status:     ok
   llama.cpp:  ok
   Model:      /path/to/model-Q5_K_M.gguf
   GPU memory: 8142 MB / 16376 MB
   Uptime:     2h 14m 3s
   Requests:   47
```
