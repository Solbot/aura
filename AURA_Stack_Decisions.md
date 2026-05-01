# Project AURA — Stack Decisions
*Autonomous Unified Reasoning Assistant*

---

## Hardware

| Component | Decision | Notes |
|---|---|---|
| Core | Raspberry Pi 5 16GB | Primary device |
| Storage | Silicon Power P34A60 256GB NVMe M.2 2280 | SM2263XT controller, verified Pi 5 compatible |
| NVMe HAT | Freenove M.2 NVMe Adapter V2 | PCIe 2.0/3.0, supports 2230/2242/2260/2280 |
| Display | 10" touchscreen 1024x600 | Status display, notes, apps |
| Audio Out | User-configured (default: built-in display audio) | Named device stored in config; fallback chain on device loss |
| Audio In | User-configured (default: first available input device) | Named device stored in config; fallback chain on device loss |
| Power | PiSugar 3 Plus | Portable power; managed via pisugar-server daemon |
| Case | Custom design - 3D printed | Later |

---

## Software Stack

| Component | Decision | Notes |
|---|---|---|
| OS | Raspberry Pi OS with Desktop 64-bit | Full desktop environment; AURA runs as a desktop application |
| Inference Engine | llama.cpp | Better than Ollama on constrained hardware |
| LLM Model | Llama 3.1 8B Q4_K_M | Best tool use at Pi-friendly size |
| STT — Wake Word | Vosk (small-en-us) | Always-on, lightweight; fires on wake phrase only |
| STT — Transcription | faster-whisper tiny | Triggered by Vosk; accurate full transcription |
| TTS | Piper TTS | en_US-amy-medium, female default |
| Language | Python | Primary development language |
| UI Framework | GTK4 | Desktop application; resizable, minimisable window — not fullscreen kiosk |

---

## PCIe Configuration

| Setting | Value |
|---|---|
| Mode | Gen 3 (enabled via config.txt) |
| Benchmark Write | 4.0 GB/s |
| Benchmark Read | 5.3 GB/s |
| config.txt entries | dtparam=pciex1 / dtparam=pciex1_gen=3 |

---

## Connectivity — Tiered System

| Tier | Endpoint | Condition |
|---|---|---|
| Primary | Home PC (llama.cpp server) | Home network detected and responding |
| Secondary | Remote API (configurable) | Internet available, home not reachable |
| Fallback | Local Pi model | No connectivity — always available |

Home PC Specs: AMD Ryzen 7 7800X3D, 64GB RAM, RTX 4080 SUPER
Connection method: VPN for testing, direct llama.cpp server for production

---

## Memory Architecture — Hot/Warm/Cold

| Tier | Storage | Description |
|---|---|---|
| Hot | RAM (context window, rolling 20 messages) | Current conversation — pruned when full |
| Warm | SQLite `conversation_summaries` | LLM-generated summaries of pruned hot chunks |
| Cold | SQLite `conversation_archive` | Append-only raw log of every message ever sent |

**Flow:** User speaks → hot + cold. Hot exceeds 20 messages → oldest 10 summarised → warm. On startup → warm summaries injected into context between system prompt and recent messages.

**Dream cycle:** Runs after N minutes of silence (default 10 min). Consolidates hot profile facts + warm summaries into clean canonical `source=dream` profile entries. Dream-sourced facts take priority over raw conversation facts in the system prompt.

**Busy lock:** Dream and memory summarisation will not run while the LLM is actively processing a message. 10-second cooldown after each LLM call before dream can fire.

Nothing is ever deleted without explicit user instruction (cold archive is permanent).

---

## UI Design

### Philosophy
AURA is a desktop application, not a kiosk. The window is resizable, minimisable,
and closable. The user can run other applications alongside AURA without being trapped.

- Animated character (Microsoft Fluent Emoji) as primary visual element
- Character states drive animation:
  | Character State | AURA State |
  |---|---|
  | Idle/ambient | Waiting for input |
  | Thinking | LLM processing |
  | Speaking | TTS active |
  | Using tools | Tool execution in progress |
  | Sleeping | Dream cycle running |
  | Listening | Wake word detected, STT active |
- Chat history and status information secondary to character
- Normal GTK4 window decorations — title bar, minimise, maximise, close
- Touch and mouse input supported
- Resizable — useful content at any reasonable window size

### UI as Optional Layer
`aura.py` is a fully independent process. It operates without any UI attached.
`aura_gtk.py` connects to the backend via Unix socket and is purely presentational.
The UI can be closed, minimised, or replaced entirely without affecting AURA's operation.
AURA continues to listen, think, remember, and speak regardless of UI state.

### Alternative UI Policy
The Unix socket protocol at `/tmp/aura.sock` is the public interface contract.
Any process that speaks the protocol can act as a frontend:
- Terminal client (`terminal_client.py`) — already implemented
- GTK4 UI (`aura_gtk.py`) — primary UI
- Future: web UI, alternative GTK themes, third-party UIs
- Backend owns all intelligence. No AURA logic may live in the UI layer.
- UI developers get a fully functional talking, listening AURA out of the box.

---

## Notes & Storage

| Component | Decision |
|---|---|
| Notes/Reminders | SQLite database |
| Mud maps | Image files with SQLite reference |
| Voice notes | Dictated, transcribed, stored |
| Location | Manual job context ("Hey AURA, new job — Johnson Street") |

---

## Security & Privacy

| Component | Decision |
|---|---|
| Data encryption | SQLCipher for SQLite at rest |
| API keys | Environment variables, never hardcoded |
| Network | VPN for home connectivity |
| Updates | Git for application code, manual apt for OS |
| Local data | `aura.db` and `logs/` are in `.gitignore` — never committed to GitHub |

---

## Core Values (Non-Negotiable)

- No harm to user or others
- CSAM: flat refusal + compassionate referral to location-specific help groups (Lucy Faithfull Foundation / Stop It Now)
- Never lies to the user
- Never manipulates or fosters unhealthy dependence
- User data stays local always
- Transparent about being an AI if sincerely asked
- Session logging of violations for developer reference
- Transparent about how it works: if AURA cannot explain a behaviour in plain
  language to the user, that behaviour should not exist in the system.
  If AURA can't explain it, it shouldn't do it.

---

## First Boot Philosophy

Natural conversation, not a configuration wizard. AURA introduces herself, explains the purpose of the conversation, makes clear sharing is optional. Name exchange first, then organic chat surfaces location, use case, and personality calibration. Never pushes if user declines. Goal: feels like meeting someone. All settings revisable anytime.

Gathered facts are written to two places:
- `config` table — for runtime use (assistant name, tone, use case, etc.)
- `user_profile` table with `source='first_boot'` — so they participate in dream consolidation and are treated as known facts from the start

---

## Tool API Architecture

Each tool is a self-contained Python module that registers with a standard interface:
- Name, description, parameters, callable function
- Permission tier: FREE (silent), CONFIRM (asks user), LOCKED (hardcoded refusal)
- AURA discovers registered tools at startup
- LLM decides when and which tool to call
- Results feed back into conversation naturally

**Implemented tools:** get_system_info (Pi sensors: temp, fan, disk, RAM, network, datetime), store_user_fact, get_user_facts, battery_status, web_search, fetch_page, knowledge_search, list_knowledge_docs, notes (create/list/delete), reminders (set/list/cancel), scheduled_tasks (create/list/delete/run)

**Planned tools:** Mud map sketch, Weather (connected), Timer

---

## Debug Commands

All commands start with `/` and are intercepted before reaching the LLM (instant, no inference cost):

| Command | Function |
|---|---|
| `/help` | List all commands |
| `/status` | DB stats, hot count, dream pending state |
| `/prompt` | Show current system prompt |
| `/hot` | Show hot context messages |
| `/memory` | Show all stored profile facts (★ = dream-consolidated) |
| `/warm` | Show warm memory summaries |
| `/cold [N]` | Show last N cold archive entries (default 10) |
| `/dream` | Trigger dream cycle manually |
| `/clear memory` | Delete all profile facts |
| `/clear warm` | Delete all warm summaries |
| `/clear hot` | Clear hot context |
| `/set key value` | Set a config value |
| `/config` | Show all config settings |

---

## Development Credentials (Local Dev Only)

| | |
|---|---|
| Username | aura |
| Password | companion |
| Hostname | aura |

*Throwaway credentials — local development machine only*

---

## File Structure

| File | Purpose |
|---|---|
| `aura.py` | Main loop, TTS, tool calls, CSAM check |
| `aura_gtk.py` | GTK4 UI — tile layout, chat area, header bar, STT integration |
| `db.py` | SQLite — config, user_profile, reminders, tasks, notes, warm/cold memory, knowledge, web cache |
| `memory.py` | Three-tier hot/warm/cold memory manager |
| `dream.py` | Sleep/dream memory consolidation cycle |
| `awareness.py` | Background thread: reminders, thermal, battery, dream trigger, busy lock |
| `commands.py` | Debug/utility slash commands |
| `csam.py` | Hardcoded CSAM safety — never configurable |
| `first_boot.py` | First-date conversation, populates config and user_profile (source='first_boot') |
| `knowledge.py` | RAG engine: watches ~/knowledge/upload, chunks + indexes via SQLite FTS5 |
| `stt.py` | BackgroundListener — always-on wake-word STT (faster-whisper) |
| `hardware/__init__.py` | Hardware device registry |
| `hardware/pisugar3.py` | PiSugar 3 Plus driver; registers battery_status tool |
| `tiles/__init__.py` | Tile registry |
| `tiles/pisugar3_tile.py` | Battery tile (category: hardware) |
| `tools/__init__.py` | Tool registry with FREE/CONFIRM/LOCKED tiers |
| `tools/system_info.py` | Pi sensor tool (date/time, temp, fan, disk, RAM, network) |
| `tools/user_profile.py` | store_user_fact / get_user_facts tools |
| `tools/web_search.py` | web_search / fetch_page tools |
| `tools/knowledge.py` | knowledge_search / list_knowledge_docs tools |
| `tools/notes.py` | Notes create/list/delete tools |
| `tools/reminders.py` | Reminder set/list/cancel tools |
| `tools/tasks.py` | Scheduled task create/list/delete/run tools |
| `systemd/` | Systemd service files (aura.service, aura-ui.service, llama-server.service) |

---

## Current Status

- [x] Pi OS with Desktop 64-bit installed and booting from NVMe
- [x] PCIe Gen 3 configured and verified
- [x] llama.cpp compiled with Cortex-A76 optimisations
- [x] Llama 3.1 8B Q4_K_M downloaded
- [x] llama-server running on port 8080 as systemd service
- [x] Piper TTS — smooth audio, en_US-amy-medium
- [x] Git repository — github.com/Solbot/aura
- [x] Config system (SQLite)
- [x] System prompt built from config
- [x] First boot "first date" conversation — stores facts in both config and user_profile
- [x] CSAM safety module (hardcoded, non-configurable)
- [x] Tool registry (FREE/CONFIRM/LOCKED tiers)
- [x] System info tool (full Pi 5 sensor coverage)
- [x] User profile store + get tools
- [x] Background awareness thread (reminders, thermal, date events, quiet hours)
- [x] Hot/Warm/Cold memory system with automatic pruning
- [x] Dream cycle — automatic memory consolidation after silence
- [x] Dream scheduling via interaction flag (fires after 10 min silence)
- [x] Busy lock — dream waits for LLM to finish + 10s cooldown
- [x] Debug command system (/help, /memory, /warm, /cold, /hot, /prompt, /status, /dream, /clear, /set, /config)
- [x] GTK4 tile UI — header bar, chat area, tile grid, touch input
- [x] Tiered connectivity (Home PC → Remote API → Local fallback)
- [x] STT/wake word — Vosk always-on + faster-whisper transcription on demand
- [x] TTS muting during AURA speech (prevents self-wake)
- [x] Hardware plugin system (pisugar3 driver + battery tool)
- [x] Battery awareness — live UI header, LLM warnings, critical alerts
- [x] Battery tile (pisugar3_tile.py)
- [x] Web search tool (web_search + fetch_page)
- [x] Notes tool (create/list/delete)
- [x] Reminders tool (set/list/cancel)
- [x] Scheduled tasks tool (create/list/delete/run)
- [x] Knowledge base / RAG (FTS5 index, upload folder watch, knowledge_search tool)
- [x] CSAM privileged logging service (systemd socket + csam_logger.py)
- [x] STT moved to aura.py (operates independently of UI)
- [x] Audio device configuration (tts_speaker, stt_microphone, fallback)
- [x] Audio fallback chain (primary → fallback → system default)
- [ ] PipeWire device disconnect monitoring (future enhancement)
- [x] Vosk wake word detection (replaces faster-whisper for always-on)
- [ ] Fluent Emoji animated character UI
- [ ] Proactive agency engine
- [ ] Engagement velocity model
- [ ] Suppressed suggestions store
- [ ] Weather tool
- [ ] Mud map sketch tool

---

*Project started: April 10, 2026*
