# AURA — Codebase Guide

AURA is an AI companion running on a Raspberry Pi 5. It combines local LLM inference, persistent memory, hardware awareness, a GTK4 UI, and always-on speech-to-text.

## Architecture

```
systemd
└── aura.service      → venv/bin/python aura.py

labwc autostart (~/.config/labwc/autostart)
└── launch_ui.sh      → python3 aura_gtk.py

aura.py          (core — LLM loop, tool execution, memory, TTS, STT)
│   ├── aura_socket  (Unix socket IPC to UI)
│   ├── awareness    (background thread — reminders, temperature, battery, dream, audio monitoring)
│   ├── memory       (hot/warm/cold three-tier memory)
│   ├── stt          (BackgroundListener — always-on wake-word STT, daemon thread)
│   ├── tools/       (function-calling tool registry)
│   ├── hardware/    (hardware device registry + drivers)
│   └── db           (SQLite — all persistence)
aura_gtk.py      (GTK4 desktop app — connects to aura via /tmp/aura.sock)
│   └── display/input only — no AURA logic lives here
```

Two separate processes communicate over a Unix domain socket at `/tmp/aura.sock`. `aura.py` owns all LLM logic; `aura_gtk.py` is a GTK4 desktop application with pure display and input responsibility.

`aura.service` (systemd) manages the backend and starts on boot. The UI is a desktop application launched by labwc at session start via `~/.config/labwc/autostart`. If the backend socket isn't ready yet, the UI shows "Connecting…" and reconnects automatically every 2 s.

## LLM Endpoint Selection

Priority order, probed each call (cached 60 s):
1. `home_pc_endpoint` (db config) — probed via `/health`, 2 s timeout
2. `remote_api_endpoint` (db config) — assumed available if set
3. `http://localhost:8080` — local llama.cpp fallback

## Memory System

| Tier | Storage | When used |
|------|---------|-----------|
| Hot  | RAM (`memory._hot`) | Active context window, last ~20 messages |
| Warm | SQLite `conversation_summaries` | Keyword-searched summaries of pruned chunks |
| Cold | SQLite `conversation_archive` | Raw append-only archive; fallback search |

`add_message(role, content)` writes to hot + cold. Messages with `content=None` (tool-call scaffolding) must use `add_hot_raw()` instead. `pop_hot()` removes the last hot entry.

The dream cycle (`dream.py`) flushes hot → warm, then consolidates warm summaries into `user_profile` via an LLM call, and clears warm.

## System Prompt Freshness

`SYSTEM_PROMPT` is built once at startup and lazily rebuilt whenever the user profile changes:

- `tools/user_profile._on_profile_changed` callback is wired to `_mark_prompt_dirty()` in `aura.py`. It fires after every `store_fact()` call.
- `awareness._on_dream_complete` is wired to the same function, so the prompt also rebuilds after each dream cycle.
- `_rebuild_if_dirty()` is called at the top of `chat()` and `_run_awareness_llm()` — it rebuilds `SYSTEM_PROMPT` only when the flag is set.

Do not call `build_system_prompt()` directly from inside a chat turn; always go through `_rebuild_if_dirty()`.

## Tool System

Tools self-register at import time via `tools.register()`. `tools.load_all()` in `aura.py` triggers the imports.

Permission tiers:
- `FREE` — executes silently (reads, fact storage, reminders, web search)
- `CONFIRM` — prompts user unless `tool_confirm_skip_<name> = 1` in db
- `LOCKED` — always refused (CSAM-related)

Tool calls go through `tools.execute(name, args, confirm_fn)` which returns `(success: bool, result: str)`.

## Background Awareness Thread

`awareness.py` runs a daemon thread that fires every 10 s tick:

- **Every tick**: checks reminders and scheduled tasks; due items are queued to `llm_check_queue` for LLM delivery.
- **Full check** (configurable interval, default 5 min): temperature alert, dream trigger, memory/temp status push to UI.
- **Date events**: birthday/anniversary notes — only outside quiet hours. Reminders and scheduled tasks fire regardless of quiet hours (user-initiated, user's responsibility).

Three queues bridge the awareness thread to the main loop:
- `immediate_queue` — hardware alerts delivered as fixed strings (no LLM latency)
- `hot_memory_queue` — context notes injected into the next `chat()` call via `_dynamic_prompt`
- `llm_check_queue` — prompts handed to `_run_awareness_llm()`

## Hot Memory Note Injection

Background awareness notes (date changes, birthdays) are retrieved from `hot_memory_queue` by the main loop and passed to `chat()` as `hot_memory_note`. Inside `chat()`, the note is appended to `_dynamic_prompt` (the per-turn system prompt), **not** added to hot memory via `add_message()`. System-role messages added via `add_message()` are filtered out by `get_context()` and would never reach the LLM.

## Knowledge Base (RAG)

`knowledge.py` watches `~/knowledge/upload/`, extracts text, chunks it, and indexes it in SQLite FTS5. Files are moved to `~/knowledge/processed/` after indexing.

**Supported formats:** `.pdf` (pypdf), `.docx` (python-docx, including table cells), and plain-text formats (`.txt`, `.md`, `.rst`, `.csv`, `.json`, `.html`, `.htm`, `.xml`). Image-based (scanned) PDFs are not supported — pypdf cannot extract text from raster images; pre-process them externally before ingestion.

**Chunking:** 400-word chunks with 40-word overlap, indexed via FTS5 BM25.

**Retrieval — automatic pre-retrieval (not LLM-initiated):** At the start of every `chat()` call, `knowledge.search(user_input, limit=3)` runs automatically. If results are found they are injected into `_dynamic_prompt` for that turn under the heading `RELEVANT KNOWLEDGE BASE EXCERPTS`. The LLM receives the content directly without needing to decide to call a tool. FTS5 is keyword-based, so specific queries retrieve better than vague ones.

**LLM-initiated fallback:** `knowledge_search` and `list_knowledge_docs` tools remain registered (FREE tier) so the LLM can still search explicitly when needed.

**Ingest trigger:** `watch_once()` is called on a periodic interval (`_KNOWLEDGE_INTERVAL`) and also via the `process_knowledge` socket message.

## Key Design Decisions

- **`reminder_cancel` deletes rows**; `reminder_mark_fired` marks them (used internally after a one-shot reminder fires). Cancelled reminders do not persist.
- **Tool-call scaffolding messages** (assistant messages with `content=None`, tool result messages) use `add_hot_raw()` to go into hot memory without cold-archiving. The temporary "Tool results: …" user instruction added before the follow-up LLM call is removed via `pop_hot()` after the call.
- **Quiet hours** suppress date-event notes only. Reminders and scheduled tasks are always delivered.
- **`birth_year`** is handled both via `_DURATION_TO_DATE` (merging it into birthday when birthday lacks a year) and via a special case lower in `store_fact`. The `_DURATION_TO_DATE` path returns early, so the special case is only reached when no birthday fact exists yet.

## Audio Architecture

TTS (Piper) and STT (faster-whisper BackgroundListener) live in `aura.py`, not in
the UI. Hearing and speaking are core AURA functions, not UI features. An alternative
UI or headless operation gets full audio capability automatically.

### Device Selection
Audio devices are stored by name in the config table:
- `tts_speaker` — output device name; empty = system default
- `stt_microphone` — input device name; empty = first available input device

Device names are resolved at startup via PipeWire/PortAudio enumeration.
Named devices are more stable than card numbers across reboots.

### Fallback Chain
If a configured device becomes unavailable (e.g. bluetooth headset battery dies):
1. Primary — configured device (`tts_speaker` / `stt_microphone`)
2. Secondary — any available wired/built-in alternative
3. Emergency (output only) — HDMI/display audio; always present while display connected

On primary device loss:
- awareness thread detects disconnection via PipeWire device events
- AURA speaks notification through fallback output immediately
- UI receives `system_message` with warning level
- STT switches to text-input-only mode if microphone lost (cannot fabricate input)
- State restores automatically when primary device reconnects

### Fallback Device Configuration
Fallback devices are configured in Settings, not during first boot.
First boot is for relationship calibration only — not technical contingency planning.

### What Belongs Where
First boot: name, tone, use case, proactive suggestion frequency calibration
Settings: audio devices, fallback devices, wake word prefix, voice model,
          voice speed, quiet hours, dream delay, connectivity endpoints,
          STT model size, battery thresholds, all technical configuration

---

## Socket Protocol

`/tmp/aura.sock` is the public interface contract between backend and any frontend.
Treat it as a stable API. Do not make breaking changes without updating this document.

### Aura → UI messages
| Type | Fields | Effect |
|------|--------|--------|
| `chat_response` | `text`, `id` | Append AURA message |
| `stt_transcript` | `text` | Display user's spoken words in chat box |
| `system_message` | `text`, `level` | Status notification (info/warning/error) |
| `status_update` | `key`, `value` | Update header widget (cpu_temp, memory, battery, etc) |
| `tts_start` | — | TTS beginning — mute STT if UI manages audio |
| `tts_end` | — | TTS complete — unmute STT |
| `set_aura_state` | `state` | Override character emoji state (e.g. `sleeping` during dream) |
| `set_expression` | `emotion` | Show a transient emotional expression; held during next TTS then reverts |
| `ui_command` | `tool`, `args` | Execute a UI-side action |
| `ui_query` | `filter`, `request_id` | Request tile/device state from UI |
| `device_list` | `device_type`, `devices`, `request_id` | Response to device enumeration request |

### UI → Aura messages
| Type | Fields | Effect |
|------|--------|--------|
| `chat_input` | `text`, `id` | User message |
| `set_privacy_mode` | `enabled` | Start (`false`) or stop (`true`) STT listener at runtime |
| `ping` | — | Keepalive |
| `ui_response` | `request_id`, data | Response to ui_query |
| `shutdown` | — | Graceful shutdown request |
| `device_query` | `device_type`, `request_id` | Request available audio devices of type (input/output) |

---

## Wake Word

Wake phrase format: `"{prefix} {assistant_name}"` e.g. "Hey AURA", "OK AURA"

- Default prefixes: "Hey" and "OK" (both active until user configures preference)
- Prefix is configurable in Settings after first boot
- Assistant name comes from `assistant_name` db config (set during first boot)
- Wake phrase matching: case-insensitive, allows up to 15 chars leading noise
- On first boot completion, AURA informs the user:
  "You can get my attention by saying Hey {name} or OK {name}.
   You can change that in settings."

Current implementation: faster-whisper (sliding window, 50% overlap)
Planned: Vosk for always-on wake detection; faster-whisper retained for transcription

---

## Speech-to-Text (`stt.py`)

Always-on wake-word listener running as a daemon thread inside `aura.py`.

**Dependencies** (installed in venv): `faster-whisper`, `sounddevice`. System package: `libportaudio2`.

**ALSA configuration** — `/etc/asound.conf` must exist and route `pcm.!default` capture to the USB mic (`hw:2,0`). Without it, PortAudio cannot enumerate the USB capture device. Card assignments on this machine: 0 = vc4-hdmi-0, 1 = vc4-hdmi-1, 2 = QuickCam Pro 9000.

**Config keys** (db):

| Key | Default | Description |
|-----|---------|-------------|
| `stt_enabled` | `1` | STT on/off |
| `stt_microphone` | `""` | Device name; empty = first available input device |
| `stt_model` | `tiny` | Whisper model size (`tiny`/`base`/`small`) |

**`BackgroundListener` state machine:**

- **idle** — maintains a rolling 2 s audio ring buffer (deque). Every 1 s (50% overlap) the window is evaluated: if RMS > `ENERGY_FLOOR` (0.015) it is transcribed with Whisper and checked for a wake phrase.
- **active** — records continuously once wake phrase detected; after `SILENCE_NEEDED` (2.5 s) of consecutive RMS < `ENERGY_FLOOR`, transcribes the full buffer, strips the wake phrase, and fires `on_transcript(text)`.

The 50% sliding window prevents wake words from being missed when they straddle a window boundary.

**Audio capture** — uses `sd.InputStream` in **callback mode**: the PortAudio callback puts every 0.5 s block into a `queue.Queue`, while the main STT thread drains the queue. This eliminates XRUN audio loss that occurred with blocking `stream.read()` during long Whisper inference.

**Whisper settings** — `language="en"`, `initial_prompt="Hey {name},"`, `condition_on_previous_text=False`. The initial prompt primes the model to spell the assistant's name correctly; disabling context conditioning prevents hallucinations from one window leaking into the next. `vad_filter=True` for both idle and active passes.

**Wake phrases** are built from `assistant_name` (DB config): `"aura"`, `"hey aura"`, `"hey, aura"`. Matching is case-insensitive and allows up to 15 chars of leading noise.

**TTS muting** — `BackgroundListener` exposes `mute()` / `unmute()`. `aura.py` sends `{"type": "tts_start"}` before `aplay` and `{"type": "tts_end"}` after; `aura_gtk.py` calls the appropriate method on the active listener. This prevents AURA's own voice from triggering wake detection. `unmute()` also resets the state machine to `idle` so any audio captured during TTS is discarded.

**Device selection** — `resolve_device("")` falls back to the first enumerated input device. With `/etc/asound.conf` in place, `sysdefault` routes capture to the USB mic and is enumerated as an input device.

**Model storage** — Whisper model downloaded on first use to `~/models/whisper/` (~40 MB for tiny).

**Backend integration** — `BackgroundListener.on_transcript` puts transcribed text directly into `aura_socket.incoming_queue` as a `chat_input` message, so it enters the main loop exactly like typed input.

**UI indicator** — header bar shows `🎤 [device dropdown]`; state label shows `loading…` during model load and `● listening` (green) while in active phase.

## Hardware Plugin System

Hardware devices live in `hardware/`. Each module self-registers at import time via `hardware.register(device_id, device)`. `aura.py` calls `hardware.load_all()` at startup after `tools.load_all()`.

**Device interface** — every registered device must expose:
- `device_id: str`
- `name: str`
- `is_available() -> bool`
- `get_state() -> dict`

**PiSugar 3 Plus** (`hardware/pisugar3.py`):
- Communicates with `pisugar-server` daemon via Unix socket at `/tmp/pisugar-server.sock` (override with `pisugar3_socket` DB config)
- State keys: `available`, `battery_level` (int %), `is_charging`, `is_power_plugged`, `battery_voltage`
- Registers `battery_status` tool (FREE tier) so AURA can answer battery questions
- 30-second cache on state reads

**Awareness integration** — `awareness._check_battery()` runs every full check cycle:
- Sends `status_update battery "⚡ 85%"` to push live level to UI header bar
- Queues LLM warning via `llm_check_queue` at `battery_warning_threshold` % (default 20%)
- Fires critical `immediate_queue` alert at `battery_critical_threshold` % (default 10%)
- Warning flags reset when charging resumes or level recovers 5% above warning threshold

**Battery tile** (`tiles/pisugar3_tile.py`):
- Category `hardware`; available when pisugar3 daemon responds
- `DataSource.get_state()` returns `battery_level`, `is_charging`, `status`, `icon`, `display`
- `aura_context` template injects current level into AURA's system prompt

**Adding a new hardware device:**
1. Create `hardware/<device>.py` — implement `is_available()`, `get_state()`, register tool(s), call `hardware.register()`
2. Add `from hardware import <device>` in `hardware/load_all()`
3. Optionally add a tile in `tiles/<device>_tile.py`

## Database

Single SQLite file at `~/aura/aura.db`. All access goes through `db.py` — never open the DB directly from other modules.

Tables: `config`, `user_profile`, `reminders`, `scheduled_tasks`, `conversation_summaries`, `conversation_archive`, `web_searches`, `web_cache`.

## GTK4 UI (`aura_gtk.py`)

Connects to `/tmp/aura.sock` as a client. All socket messages are JSON lines.

**Socket protocol (Aura → UI):**

| Type | Fields | Effect |
|------|--------|--------|
| `chat_response` | `text`, `id` | Append AURA message bubble |
| `system_message` | `text`, `level` | Append status line |
| `status_update` | `key`, `value` | Update header bar widget |
| `tts_start` | — | Mute STT wake detection |
| `tts_end` | — | Unmute STT; discard active buffer |
| `ui_command` | `tool`, `args` | Execute a UI tool |
| `ui_query` | `filter`, `request_id` | Query tile state |
| `set_expression` | `emoji` | Show a transient Fluent Emoji expression; held during next TTS then reverts to idle |

**Emoji character panel:**

The left panel displays the current AURA state as a Fluent Emoji 3D PNG (`assets/emoji/`). States are defined in `AURA_STATES` (module-level dict); dynamic expressions from `express_emotion` tool calls are registered at runtime using the emoji character itself as the key.

PNG images are loaded via `Gdk.Texture.new_from_filename()` + `Gtk.Picture.set_paintable()` — the GTK 4.12+ API. Do **not** use the deprecated `GdkPixbuf` + `set_pixbuf()` path; `GdkPixbuf` is not imported. `_STATIC_STATES` is a frozen snapshot of the built-in operational states; dynamic emoji states use the generic `aura-state-expression` CSS animation class instead of a named one.

Layout (top to bottom):
1. **Header bar** — assistant name · `🎤` mic selector · connection status · clock · CPU temp · RAM
2. **Chat scroll area** — `Gtk.ScrolledWindow` with a `Gtk.Box` child; an expanding spacer at the top pushes messages to the bottom. Auto-scrolls via `vadjustment.connect("changed", ...)`.
3. **Input bar** — `Gtk.TextView` (wrapping, Shift+Enter for newline, Enter to send) in a `Gtk.Frame`, with a placeholder label overlaid via `Gtk.Overlay`.

All chat labels have `set_hexpand(True)` and `set_wrap(True)` so long messages reflow within the column width.

The mic dropdown uses `Gtk.DropDown.new_from_strings()` (not `Gtk.DropDown(model=...)`) because only `new_from_strings` sets up the `PropertyExpression` needed to render `StringObject` items.

## Python Path

The UI runs under system `python3` (which owns `gi`/GTK) but needs venv packages for STT. `launch_ui.sh` sets `PYTHONPATH` to the venv site-packages before exec. `aura_gtk.py` also injects the same path at the top of the file as a fallback for manual invocation.

## System Package Dependencies

Required system packages (must be installed once on a fresh OS, not in the venv):

```bash
# GTK4 Python introspection bindings — UI won't start without this
sudo apt-get install -y gir1.2-gtk-4.0

# PortAudio — required by sounddevice (STT/audio device enumeration)
sudo apt-get install -y libportaudio2
```

`gir1.2-gtk-4.0` is the most critical: its absence causes `launch_ui.sh` to fail silently
at session start (no window, no error visible to the user).

## Running

```bash
# Normal operation:
sudo systemctl start aura        # backend (systemd)
# UI launches automatically at desktop login via ~/.config/labwc/autostart

# Manual (development):
cd ~/aura/aura
venv/bin/python3 aura.py         # backend
./launch_ui.sh                   # UI (injects venv PYTHONPATH, then exec python3 aura_gtk.py)
```

First boot runs `first_boot.py` which requires a reachable LLM endpoint to complete the onboarding conversation.
