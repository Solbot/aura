# AURA — Codebase Guide

AURA is an AI companion running on a Raspberry Pi 5. It combines local LLM inference, persistent memory, hardware awareness, and a GTK4 UI.

## Architecture

```
start_ui.sh
├── aura.py          (core — LLM loop, tool execution, memory management)
│   ├── aura_socket  (Unix socket IPC to UI)
│   ├── awareness    (background thread — reminders, temperature, dream)
│   ├── memory       (hot/warm/cold three-tier memory)
│   ├── tools/       (function-calling tool registry)
│   └── db           (SQLite — all persistence)
└── aura_gtk.py      (GTK4 UI — connects to aura via /tmp/aura.sock)
```

Two separate processes communicate over a Unix domain socket at `/tmp/aura.sock`. `aura.py` owns all LLM logic; `aura_gtk.py` is pure display and input.

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

## Database

Single SQLite file at `~/aura/aura.db`. All access goes through `db.py` — never open the DB directly from other modules.

Tables: `config`, `user_profile`, `reminders`, `scheduled_tasks`, `conversation_summaries`, `conversation_archive`, `web_searches`, `web_cache`.

## GTK4 UI (`aura_gtk.py`)

Connects to `/tmp/aura.sock` as a client. All socket messages are JSON lines.

Layout (top to bottom):
1. **Header bar** — assistant name, connection status, clock, CPU temp, RAM
2. **Chat scroll area** — `Gtk.ScrolledWindow` with a `Gtk.Box` child; an expanding spacer at the top of the box pushes messages to the bottom when content is sparse. Auto-scrolls to bottom via `vadjustment.connect("changed", ...)`.
3. **Input bar** — `Gtk.TextView` (wrapping, Shift+Enter for newline, Enter to send) in a `Gtk.Frame`, with a placeholder label overlaid via `Gtk.Overlay`.

All chat labels have `set_hexpand(True)` and `set_wrap(True)` so long messages reflow within the column width rather than expanding horizontally.

## Running

```bash
cd ~/aura
./start_ui.sh        # starts aura.py + aura_gtk.py
# or individually:
python3 aura.py      # core (blocks)
python3 aura_gtk.py  # UI (blocks)
```

First boot runs `first_boot.py` which requires a reachable LLM endpoint to complete the onboarding conversation.
