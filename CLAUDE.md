# AURA — Codebase Guide

AURA is an AI companion running on a Raspberry Pi 5. It combines local LLM inference, persistent memory, hardware awareness, a GTK4 UI, and always-on speech-to-text.

## Architecture

```
systemd
├── aura.service      → venv/bin/python aura.py
└── aura-ui.service   → launch_ui.sh → python3 aura_gtk.py

aura.py          (core — LLM loop, tool execution, memory management)
│   ├── aura_socket  (Unix socket IPC to UI)
│   ├── awareness    (background thread — reminders, temperature, dream)
│   ├── memory       (hot/warm/cold three-tier memory)
│   ├── tools/       (function-calling tool registry)
│   └── db           (SQLite — all persistence)
aura_gtk.py      (GTK4 UI — connects to aura via /tmp/aura.sock)
│   └── stt.py       (BackgroundListener — always-on wake-word STT)
```

Two separate processes communicate over a Unix domain socket at `/tmp/aura.sock`. `aura.py` owns all LLM logic; `aura_gtk.py` is pure display and input.

Both processes are managed by systemd (`aura.service`, `aura-ui.service`) and start automatically on boot. `aura-ui.service` requires `aura.service` and polls for `/tmp/aura.sock` before launching the UI.

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

## Key Design Decisions

- **`reminder_cancel` deletes rows**; `reminder_mark_fired` marks them (used internally after a one-shot reminder fires). Cancelled reminders do not persist.
- **Tool-call scaffolding messages** (assistant messages with `content=None`, tool result messages) use `add_hot_raw()` to go into hot memory without cold-archiving. The temporary "Tool results: …" user instruction added before the follow-up LLM call is removed via `pop_hot()` after the call.
- **Quiet hours** suppress date-event notes only. Reminders and scheduled tasks are always delivered.
- **`birth_year`** is handled both via `_DURATION_TO_DATE` (merging it into birthday when birthday lacks a year) and via a special case lower in `store_fact`. The `_DURATION_TO_DATE` path returns early, so the special case is only reached when no birthday fact exists yet.

## Speech-to-Text (`stt.py`)

Always-on wake-word listener running as a daemon thread inside `aura_gtk.py`.

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

**GTK integration** — all `BackgroundListener` callbacks are invoked from the background thread; callers wrap them in `GLib.idle_add`. The `_mic_populating` flag suppresses spurious `notify::selected` signals during programmatic dropdown setup.

**UI indicator** — header bar shows `🎤 [device dropdown]`; state label shows `loading…` during model load and `● listening` (green) while in active phase.

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

Layout (top to bottom):
1. **Header bar** — assistant name · `🎤` mic selector · connection status · clock · CPU temp · RAM
2. **Chat scroll area** — `Gtk.ScrolledWindow` with a `Gtk.Box` child; an expanding spacer at the top pushes messages to the bottom. Auto-scrolls via `vadjustment.connect("changed", ...)`.
3. **Input bar** — `Gtk.TextView` (wrapping, Shift+Enter for newline, Enter to send) in a `Gtk.Frame`, with a placeholder label overlaid via `Gtk.Overlay`.

All chat labels have `set_hexpand(True)` and `set_wrap(True)` so long messages reflow within the column width.

The mic dropdown uses `Gtk.DropDown.new_from_strings()` (not `Gtk.DropDown(model=...)`) because only `new_from_strings` sets up the `PropertyExpression` needed to render `StringObject` items.

## Python Path

The UI runs under system `python3` (which owns `gi`/GTK) but needs venv packages for STT. `launch_ui.sh` sets `PYTHONPATH` to the venv site-packages before exec. `aura_gtk.py` also injects the same path at the top of the file as a fallback for manual invocation.

## Running

```bash
# Managed by systemd (normal operation):
sudo systemctl start aura        # backend
sudo systemctl start aura-ui     # UI (waits for backend socket)

# Manual (development):
cd ~/aura/aura
venv/bin/python3 aura.py         # backend
./launch_ui.sh                   # UI (sets env, then exec python3 aura_gtk.py)
```

First boot runs `first_boot.py` which requires a reachable LLM endpoint to complete the onboarding conversation.
