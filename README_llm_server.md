# AURA LLM Server — Home PC Setup

Primary inference tier for Project AURA.  
Runs on your home PC (RTX 4080 SUPER), offloads inference from the Pi when on the home network.

---

## Architecture

```
Pi (aura.py)
    │
    ├─ home network? → aura_llm_server.py :8081  →  llama-server :8080  →  RTX 4080 SUPER
    ├─ internet?     → remote API (OpenAI / Anthropic compatible)
    └─ fallback      → local Pi llama-server :8080
```

`aura_llm_server.py` manages the `llama-server` process and proxies OpenAI-compatible requests to it.  
The Pi's `aura.py` probes `/health` before each endpoint selection, auto-switching tiers as needed.

---

## Files

| File | Purpose |
|------|---------|
| `aura_llm_server.py` | PC-side proxy server (copy this to your PC) |
| `aura_server_config.json` | Server configuration (edit before running) |
| `check_aura_server.py` | Reachability checker script |

---

## Quick Start — Windows

### Prerequisites
- [llama.cpp](https://github.com/ggml-org/llama.cpp) built with CUDA (`llama-server.exe`)
- Python 3.8+
- A GGUF model file (e.g. `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`)

### Steps

1. **Copy files to your PC** — grab `aura_llm_server.py`, `aura_server_config.json`, and `check_aura_server.py` from the repo.

2. **Edit `aura_server_config.json`**:
   ```json
   {
       "llama_server_bin": "C:\\llama.cpp\\llama-server.exe",
       "model_path": "D:\\models\\Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
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
   - `llama_server_bin` — full path to `llama-server.exe`, or just `"llama-server"` if it's on PATH
   - `model_path` — full path to your GGUF model
   - `gpu_layers: -1` — offload all layers to GPU (recommended for RTX 4080 SUPER)

3. **Optional: install requests** for slightly better proxy performance:
   ```
   pip install requests
   ```

4. **Run the server**:
   ```
   python aura_llm_server.py
   ```
   You'll see output like:
   ```
   AURA LLM Server starting...
   Model:      D:\models\Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
   GPU layers: all
   Context:    4096 tokens
   Starting llama-server on port 8080...
   llama-server ready.
   AURA proxy listening on  http://0.0.0.0:8081
   Pi endpoint:             http://192.168.1.100:8081/v1/chat/completions
   Health check:            http://192.168.1.100:8081/health
   Press Ctrl+C to stop.
   ```

5. **Configure the Pi**:
   ```
   /set home_pc_endpoint http://192.168.1.100:8081/v1/chat/completions
   ```
   Replace `192.168.1.100` with the IP shown in the startup output.

---

## Quick Start — Linux / macOS

Same steps. Key differences:

```json
{
    "llama_server_bin": "./llama-server",
    "model_path": "/home/user/models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
}
```

Run:
```bash
python3 aura_llm_server.py
```

---

## Config Reference

| Key | Default | Description |
|-----|---------|-------------|
| `llama_server_bin` | `"llama-server"` | Path to llama-server binary |
| `model_path` | `""` | Path to GGUF model. Empty = use external llama-server |
| `host` | `"0.0.0.0"` | Address to bind the proxy on |
| `port` | `8081` | Port the Pi connects to |
| `llama_server_port` | `8080` | Internal llama-server port |
| `context_size` | `4096` | Context window size in tokens |
| `gpu_layers` | `-1` | GPU layers to offload. `-1` = all |
| `threads` | `-1` | CPU threads. `-1` = auto |
| `log_file` | `"aura_server.log"` | Log file path (relative to script) |
| `auto_restart` | `true` | Restart llama-server if it crashes |
| `restart_delay_seconds` | `5` | Initial restart delay (doubles on repeated failures) |

---

## External llama-server mode

If `model_path` is left empty, `aura_llm_server.py` skips launching llama-server and just acts as a proxy to whatever is already running on `llama_server_port`. Useful if you manage llama-server separately.

---

## Firewall

The proxy listens on port **8081**. Allow inbound TCP 8081 in Windows Defender Firewall:

```
netsh advfirewall firewall add rule name="AURA LLM Server" dir=in action=allow protocol=TCP localport=8081
```

Or via GUI: Windows Defender Firewall → Advanced Settings → Inbound Rules → New Rule → Port → 8081.

The internal llama-server port (8080) only binds to `127.0.0.1` — it's not exposed externally.

---

## Run at Windows Login (Task Scheduler)

1. Open **Task Scheduler** → Create Task
2. **General** tab: Name = "AURA LLM Server", check "Run only when user is logged on"
3. **Triggers** tab: New → At log on → your user account
4. **Actions** tab: New → Start a program
   - Program: `python`
   - Arguments: `C:\path\to\aura_llm_server.py`
   - Start in: `C:\path\to\` (directory containing the script)
5. **Settings** tab: check "If the task is already running, do not start a new instance"

Or use **NSSM** for a proper Windows service:
```
nssm install AuraLLMServer python C:\path\to\aura_llm_server.py
nssm set AuraLLMServer AppDirectory C:\path\to\
nssm start AuraLLMServer
```

---

## Run as systemd service (Linux)

Create `/etc/systemd/system/aura-llm-server.service`:

```ini
[Unit]
Description=AURA LLM Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/aura-server
ExecStart=/usr/bin/python3 /home/youruser/aura-server/aura_llm_server.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable aura-llm-server
sudo systemctl start aura-llm-server
sudo journalctl -u aura-llm-server -f
```

---

## Checking reachability from the Pi

```bash
python3 check_aura_server.py http://192.168.1.100:8081/v1/chat/completions
```

Expected output when healthy:
```
✓  AURA server reachable at http://192.168.1.100:8081
   Status:     ok
   llama.cpp:  ok
   Model:      D:\models\Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
   GPU memory: 8142 MB / 16376 MB
   Uptime:     2h 14m 3s
   Requests:   47
```

---

## Endpoints

| Path | Method | Description |
|------|--------|-------------|
| `/health` | GET | Server status, llama-server health, GPU memory, uptime |
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions (proxied) |
| `/v1/models` | GET | Model list (proxied to llama-server) |
| All other `/v1/*` | any | Proxied transparently to llama-server |

---

## Tiered connectivity on the Pi

`aura.py` auto-selects the best available endpoint on each LLM call:

1. **Home PC** (`home_pc_endpoint` in config) — probed via `/health` with 2s timeout
2. **Remote API** (`remote_api_endpoint` in config) — used if home PC unreachable (assumed available)
3. **Local Pi** (`http://localhost:8080/v1/chat/completions`) — always available fallback

The result is cached for 60 seconds. On connection failure the cache is invalidated and the next tier is tried immediately. Tier switches are logged as system messages in the UI.

Set endpoints on the Pi:
```
/set home_pc_endpoint http://192.168.1.100:8081/v1/chat/completions
/set remote_api_endpoint https://api.openai.com/v1/chat/completions
```
