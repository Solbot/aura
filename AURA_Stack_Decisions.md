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
| Audio Out | Built into touchscreen | |
| Audio In | TBD — ReSpeaker array recommended | Backburnered until base system working |
| Power | SugarPi 3 | Portable power |
| Case | Custom design - 3D printed | Later |

---

## Software Stack

| Component | Decision | Notes |
|---|---|---|
| OS | Raspberry Pi OS Lite 64-bit | Minimal, no desktop, boots from NVMe |
| Inference Engine | llama.cpp | Better than Ollama on constrained hardware |
| LLM Model | Llama 3.1 8B Q4_K_M | Best tool use at Pi-friendly size |
| STT | Whisper | Model size TBD — backburnered with audio |
| TTS | Piper TTS | en_US-amy-medium, female default |
| Language | Python | Primary development language |
| UI Framework | Kivy | Selected — in development |

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

- Tile-based touch interface inspired by Windows Phone/Win8 concept
- Live glanceable tiles: connection status, AURA state, notes, reminders, battery
- Touch to expand tiles for detail
- Ambient animation when AURA is listening/thinking/speaking
- Kivy (Python) — selected, in development
- Fullscreen, no traditional desktop chrome

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

---

## Core Values (Non-Negotiable)

- No harm to user or others
- CSAM: flat refusal + compassionate referral to location-specific help groups (Lucy Faithfull Foundation / Stop It Now)
- Never lies to the user
- Never manipulates or fosters unhealthy dependence
- User data stays local always
- Transparent about being an AI if sincerely asked
- Session logging of violations for developer reference

---

## First Boot Philosophy

Natural conversation, not a configuration wizard. AURA introduces herself, explains the purpose of the conversation, makes clear sharing is optional. Name exchange first, then organic chat surfaces location, use case, and personality calibration. Never pushes if user declines. Goal: feels like meeting someone. All settings revisable anytime.

---

## Tool API Architecture

Each tool is a self-contained Python module that registers with a standard interface:
- Name, description, parameters, callable function
- Permission tier: FREE (silent), CONFIRM (asks user), LOCKED (hardcoded refusal)
- AURA discovers registered tools at startup
- LLM decides when and which tool to call
- Results feed back into conversation naturally

**Implemented tools:** get_system_info (Pi sensors: temp, fan, disk, RAM, network, datetime), store_user_fact, get_user_facts

**Planned tools:** Notes, Reminders, Mud map sketch, Weather (connected), Web search (connected), Timer

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
| `db.py` | SQLite — config, user_profile, reminders, warm/cold memory |
| `memory.py` | Three-tier hot/warm/cold memory manager |
| `dream.py` | Sleep/dream memory consolidation cycle |
| `awareness.py` | Background thread: reminders, thermal, dream trigger, busy lock |
| `commands.py` | Debug/utility slash commands |
| `csam.py` | Hardcoded CSAM safety — never configurable |
| `first_boot.py` | First-date conversation, populates config |
| `tools/__init__.py` | Tool registry with FREE/CONFIRM/LOCKED tiers |
| `tools/system_info.py` | Pi sensor tool (date/time, temp, fan, disk, RAM, network) |
| `tools/user_profile.py` | store_user_fact / get_user_facts tools |
| `systemd/llama-server.service` | Systemd service file |

---

## Current Status

- [x] Pi OS Lite 64-bit installed and booting from NVMe
- [x] PCIe Gen 3 configured and verified
- [x] llama.cpp compiled with Cortex-A76 optimisations
- [x] Llama 3.1 8B Q4_K_M downloaded
- [x] llama-server running on port 8080 as systemd service
- [x] Piper TTS — smooth audio, en_US-amy-medium
- [x] Git repository — github.com/Solbot/aura
- [x] Config system (SQLite)
- [x] System prompt built from config
- [x] First boot "first date" conversation
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
- [ ] Tile UI (Kivy) — in development
- [ ] Tiered connectivity (Home PC → Remote API → Local fallback)
- [ ] STT/wake word (backburnered)
- [ ] CSAM privileged logging service (systemd socket)
- [ ] Notes/Reminders tool
- [ ] Web search tool (connected mode)

---

*Project started: April 10, 2026*
