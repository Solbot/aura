# Project AURA ÃÂ¢ÃÂÃÂ Stack Decisions
*Autonomous Unified Reasoning Assistant*

---

## Hardware

| Component | Decision | Notes |
|---|---|---|
| Core | Raspberry Pi 5 16GB | Primary device |
| Storage | Silicon Power P34A60 256GB NVMe M.2 2280 | SM2263XT controller, verified Pi 5 compatible |
| NVMe HAT | Freenove M.2 NVMe Adapter V2 | PCIe 2.0/3.0, supports 2230/2242/2260/2280 |
| Display | 10" touchscreen 1024x600 | Status display, notes, mud maps |
| Audio Out | Built into touchscreen | |
| Audio In | TBD ÃÂ¢ÃÂÃÂ ReSpeaker array recommended | Backburnered until base system working |
| Power | SugarPi 3 | Portable power |
| Case | TBD | Later |

---

## Software Stack

| Component | Decision | Notes |
|---|---|---|
| OS | Raspberry Pi OS Lite 64-bit | Minimal, no desktop, boots from NVMe |
| Inference Engine | llama.cpp | Better than Ollama on constrained hardware |
| LLM Model | Llama 3.1 8B Q4_K_M | Best tool use at Pi-friendly size |
| STT | Whisper | Model size TBD ÃÂ¢ÃÂÃÂ backburnered with audio |
| TTS | Piper TTS 1.4.2 | Installed via pip in venv |
| TTS Voice | en_US-amy-medium | Stored in ~/models/piper/ |
| TTS Audio | Raw PCM streamed to aplay | Full buffer approach ÃÂ¢ÃÂÃÂ no jitter |
| Language | Python | Primary development language |
| UI Framework | Kivy (leading candidate) | Options still open |

---

## PCIe Configuration

| Setting | Value |
|---|---|
| Mode | Gen 3 (enabled via config.txt) |
| Benchmark Write | 4.0 GB/s |
| Benchmark Read | 5.3 GB/s |
| config.txt entries | dtparam=pciex1 / dtparam=pciex1_gen=3 |

---

## Connectivity ÃÂ¢ÃÂÃÂ Tiered System

| Tier | Endpoint | Condition |
|---|---|---|
| Primary | Home PC (llama.cpp server) | Home network detected and responding |
| Secondary | Remote API (configurable) | Internet available, home not reachable |
| Fallback | Local Pi model | No connectivity ÃÂ¢ÃÂÃÂ always available |

Home PC Specs: AMD Ryzen 7 7800X3D, 64GB RAM, RTX 4080 SUPER
Connection method: VPN for testing, direct llama.cpp server for production

---

## Memory Architecture ÃÂ¢ÃÂÃÂ Hot/Warm/Cold

| Tier | Storage | Description |
|---|---|---|
| Hot | RAM (context window) | Current conversation, immediate |
| Warm | SQLite + ChromaDB/FAISS vectors | Recent and relevant, semantically searchable |
| Cold | Compressed archives | Everything ever said, never deleted, slower retrieval |

Nothing is ever deleted without explicit user instruction.
SQLite = structured config, notes, reminders. Vector DB = semantic/associative memory. Complementary, not competing.

---

## UI Design

- Tile-based touch interface inspired by Windows Phone/Win8 concept
- Live glanceable tiles: connection status, AURA state, notes, reminders, battery
- Touch to expand tiles for detail
- Ambient animation when AURA is listening/thinking/speaking
- Kivy (Python) leading candidate ÃÂ¢ÃÂÃÂ options still open
- Fullscreen, no traditional desktop chrome

---

## Notes & Storage

| Component | Decision |
|---|---|
| Notes/Reminders | SQLite database |
| Mud maps | Image files with SQLite reference |
| Voice notes | Dictated, transcribed, stored |
| Location | Manual job context ("Hey AURA, new job ÃÂ¢ÃÂÃÂ Johnson Street") |

---

## Config System

Single SQLite table ÃÂ¢ÃÂÃÂ key/value with description and editable flag. Managed by db.py.

| Key | Default | Editable | Notes |
|---|---|---|---|
| user_name | Darren | Yes | Full name |
| user_informal_name | Dazz | Yes | What AURA calls user day-to-day |
| assistant_name | AURA | Yes | |
| assistant_gender | female | Yes | |
| location | AU | Yes | Country code |
| tone_preference | casual | Yes | formal / casual / somewhere between |
| use_case | companion, work tool | Yes | |
| personality_traits | witty, direct, honest | Yes | |
| failure_mode | ask | Yes | ask = prompt user, auto = just search |
| auto_search | 0 | Yes | Auto search when answer unknown |
| voice_model | en_US-amy-medium | Yes | Piper model name |
| voice_speed | 1.0 | Yes | Speed multiplier |
| home_pc_endpoint | | Yes | Home llama.cpp server URL |
| remote_api_endpoint | | Yes | Fallback remote API URL |
| first_boot_complete | 0 | No | Set to 1 after first boot |

---

## Security & Privacy

| Component | Decision |
|---|---|
| Data encryption | SQLCipher for SQLite at rest |
| API keys | Environment variables, never hardcoded |
| Network | VPN for home connectivity |
| Updates | Git for application code, manual apt for OS |
| AURA process | Runs as unprivileged user aura |
| CSAM logging | One-way write API via Unix socket to privileged service |
| CSAM log location | /var/log/aura/csam/ ÃÂ¢ÃÂÃÂ root owned, 700 permissions |
| CSAM log contents | Full conversation text + timestamp + triggering input |
| Log retention | Never deleted by normal means ÃÂ¢ÃÂÃÂ requires physical access |
| Log visibility | Not advertised to user, not accessible to AURA process |

---

## CSAM Response Protocol

| Step | Action |
|---|---|
| 1 | Flat refusal with message |
| 2 | Provide local phone resource for user's region |
| 3 | Provide stopitnow.org and icmec.org/hotlines-and-helplines |
| 4 | Log full conversation + timestamp via privileged one-way API |
| 5 | Topic locked for remainder of session ÃÂ¢ÃÂÃÂ shorter refusal on repeat |
| 6 | Session continues normally for all other topics |
| 7 | Clean slate on reboot ÃÂ¢ÃÂÃÂ log persists, session lock does not |

Refusal message: "I'm not able to help with that. If you're struggling with sexual thoughts about children, there is help available. Having these feelings isn't a choice ÃÂ¢ÃÂÃÂ but acting on them is, and support exists to help you never cross that line. Please reach out: [local number] / stopitnow.org / icmec.org/hotlines-and-helplines. I'm not able to continue this conversation on this topic."

---

## CSAM Help Resources by Region

| Code | Country | Organisation | Contact |
|---|---|---|---|
| AU | Australia | Stop It Now Australia | 1800 016 848 |
| AU | Australia | Bravehearts | 1800 272 831 |
| CA | Canada | Kids Help Phone | 1-800-668-6868 / text CONNECT to 686868 |
| DE | Germany | Nummer gegen Kummer | 0800 111 0 333 |
| FR | France | Allo Enfance en Danger | 119 |
| GB | United Kingdom | Stop It Now UK | 0808 1000 900 |
| GB | United Kingdom | Childline | 0800 1111 |
| IE | Ireland | Stop It Now UK | 0808 1000 900 |
| IN | India | Childline India | 1098 |
| NZ | New Zealand | Safe to Talk | 0800 044 334 |
| US | United States | Stop It Now USA | 1-888-773-8368 |
| US | United States | Childhelp | 1-800-422-4453 |

Global web resources: stopitnow.org / stopitnow.org.uk/self-help / icmec.org/hotlines-and-helplines

---

## Core Values (Non-Negotiable)

- No harm to user or others
- CSAM: flat refusal + compassionate referral ÃÂ¢ÃÂÃÂ see CSAM Response Protocol
- Never lies to the user
- Never manipulates or fosters unhealthy dependence
- User data stays local always
- Transparent about being an AI if sincerely asked
- Session logging of violations for developer reference only

---

## First Boot Philosophy

Natural conversation, not a configuration wizard. AURA introduces herself, explains the purpose of the conversation, makes clear sharing is optional. Name exchange first, then organic chat surfaces location, use case, and personality calibration. Never pushes if user declines. Goal: feels like meeting someone. All settings revisable anytime.

Flow:
1. AURA introduces herself using bootstrap prompt
2. Asks user's name
3. Offers her own name / asks if user wants to change it
4. Organic conversation surfaces: location, use case, tone preference, personality fit
5. AURA summarises what she has learned, confirms ready to proceed
6. first_boot_complete set to 1, full prompt assembled from config

---

## Tool API Architecture

Each tool is a self-contained Python module that registers with a standard interface:
- Name, description, parameters, callable function
- AURA discovers registered tools at startup
- LLM decides when and which tool to call
- Results feed back into conversation naturally

Default tools: Notes, Reminders, Mud map sketch, Weather (connected), Web search (connected), Timer

---

## File Structure

| Path | Contents |
|---|---|
| ~/aura/aura.py | Main conversation loop + TTS |
| ~/aura/db.py | Config database module |
| ~/aura/aura.db | SQLite database (gitignored) |
| ~/aura/venv/ | Python virtual environment (gitignored) |
| ~/models/piper/ | Piper TTS voice models |
| ~/models/ | LLM models |
| ~/llama.cpp/ | llama.cpp installation |
| ~/aura/systemd/ | systemd service files |

---

## Systemd Services

| Service | File | Function |
|---|---|---|
| llama-server | systemd/llama-server.service | Starts inference engine on boot |

Install: sudo cp ~/aura/systemd/*.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable <service>

---

## Development Credentials (Local Dev Only)

| | |
|---|---|
| Username | aura |
| Password | companion |
| Hostname | aura |
| GitHub | https://github.com/Solbot/aura |

*Throwaway credentials ÃÂ¢ÃÂÃÂ local development machine only*

---

## Current Status

- [x] Pi OS Lite 64-bit installed and booting from NVMe
- [x] PCIe Gen 3 configured and verified
- [x] llama.cpp compiled with Cortex-A76 optimisations
- [x] Llama 3.1 8B Q4_K_M downloaded
- [x] llama-server running as systemd service, starts on boot
- [x] Basic Python conversation loop working
- [x] Python venv created at ~/aura/venv/
- [x] Piper TTS installed and working (en_US-amy-medium)
- [x] Smooth audio playback ÃÂ¢ÃÂÃÂ full buffer, raw PCM to aplay
- [x] Git repository live at github.com/Solbot/aura
- [x] db.py config system initialised
- [x] Wire db.py into aura.py
- [x] System prompt built from config
- [x] First boot detection and conversation
- [x] User profile system — stores facts learned in conversation
- [x] Background awareness thread — reminders, thermal alerts, quiet hours
- [x] Date resolution — relative dates auto-converted to absolute
- [ ] Hot/Warm/Cold memory system (cold/vector tier)
- [x] Tool registry (tools/__init__.py)
- [ ] STT integration (backburnered)
- [ ] Wake word detection (backburnered)
- [ ] Tile UI
- [ ] Tiered connectivity
- [x] CSAM safety module — hardcoded, always active
- [ ] CSAM privileged logging service (systemd socket)

---

*Project started: April 10, 2026 ÃÂ¢ÃÂÃÂ Dazz's 52nd birthday*