import requests
import subprocess
import json
import os
import time
import threading
import db
import csam
import tools
import first_boot
import awareness
import memory
import commands
import aura_socket
import knowledge
from piper import PiperVoice, SynthesisConfig

# --- Initialise database and tools ---
db.init_db()
knowledge.init_dirs()
tools.load_all()

# --- Load hardware plugins ---
import hardware
hardware.load_all()


# --- Load voice ---
def load_voice():
    model_path = os.path.expanduser(f"~/models/piper/{db.get('voice_model')}.onnx")
    speed      = float(db.get('voice_speed'))
    v          = PiperVoice.load(model_path)
    sc         = SynthesisConfig(length_scale=1.0/speed, noise_scale=0.9, noise_w_scale=1.0)
    return v, sc

voice, syn_config = load_voice()
SAMPLE_RATE  = 22050
AUDIO_DEVICE = "default"

def speak(text):
    if db.get('audio_enabled') == '0':
        return
    audio = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))
    aura_socket.send({"type": "tts_start"})
    proc  = subprocess.Popen(
        ["aplay", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-t", "raw", "-D", AUDIO_DEVICE, "-"],
        stdin=subprocess.PIPE
    )
    proc.communicate(input=audio)
    aura_socket.send({"type": "tts_end"})

# --- Start IPC socket server (must be up before first boot so the UI can connect) ---
aura_socket.start()

# --- First boot ---
if db.is_first_boot():
    first_boot.run(speak)
    voice, syn_config = load_voice()

# --- Start background awareness thread ---
awareness.start()

# --- Build system prompt ---
def _web_search_rules():
    auto = db.get("auto_search") == "1"
    if auto:
        trigger = (
            "Use web_search proactively whenever a question involves recent events, "
            "current data, news, prices, weather, or any fact that may have changed "
            "since your training cutoff. You do not need to be asked."
        )
    else:
        trigger = (
            "Use web_search when the user asks about current events, news, recent "
            "information, or explicitly asks you to look something up."
        )
    return (
        "- WEB SEARCH: " + trigger + "\n"
        "- Never announce that you are searching or cite 'the web' as a source — "
        "just answer naturally with the information you found.\n"
        "- Never fabricate search results. If web_search returns nothing useful, say so.\n"
        "- When the user shares a URL or wants more detail from a search result, "
        "call fetch_page. Pages are cached for one hour so follow-up questions "
        "about the same page work without re-fetching.\n"
    )


def build_system_prompt():
    facts = db.profile_get_all()
    dream_facts = {f['key']: f['value'] for f in facts if f['source'] == 'dream'}
    all_facts   = {f['key']: f['value'] for f in facts}
    merged = {**all_facts, **dream_facts}

    profile_str = ""
    if merged:
        profile_str = (
            "\n\nWHAT YOU ALREADY KNOW ABOUT THE USER:\n" +
            "\n".join([f"- {k}: {v}" for k, v in merged.items()]) +
            "\nUse this information naturally. Do not announce that you looked it up."
        )

    return (
        csam.get_core_prompt() + "\n\n" +
        f"You are {db.get('assistant_name')}, an AI companion. "
        f"You are {db.get('personality_traits')}. "
        f"Your tone is {db.get('tone_preference')}. "
        f"You are here to help with: {db.get('use_case')}. "
        f"The user's name is {db.get('user_informal_name')} (full name: {db.get('user_name')}). "
        f"You treat the user as an equal. You are honest and never manipulative. "
        f"You are aware you are an AI running on a Raspberry Pi. "
        f"If sincerely asked whether you are an AI, always answer honestly." +
        profile_str +
        "\n\nTOOL USAGE RULES:\n"
        "- For time/date/temperature/sensors: always call get_system_info. Never guess.\n"
        "- You have NO external weather sensors. Never invent weather data.\n"
        "- When the user shares personal facts (birthday, family, job, preferences, "
        "important dates, hobbies): IMMEDIATELY call store_user_fact.\n"
        "- Pass values exactly as the user says them — the system will clean and normalise them.\n"
        "- Use get_user_facts when asked to recall something about the user.\n"
        "- Present tool results as exact values naturally in conversation.\n"
        "- When the user asks you to do something repeatedly or at an interval "
        "(e.g. 'update me every 10 seconds', 'check temperature every minute'), "
        "call schedule_task to register it. Never just promise and forget.\n"
        "- When the user says stop/cancel for an ongoing recurring task, call cancel_task.\n"
        "- When the user asks to be reminded of something, call set_reminder with the message "
        "and a natural-language 'when' (e.g. 'in 30 minutes', 'tomorrow at 9am', "
        "'next friday afternoon'). Use list_reminders to show pending ones, "
        "cancel_reminder to remove them.\n"
        "- NOTES: Use create_note when the user wants to save a note, jot something down, "
        "or start a list. Use list_notes to show all notes, get_note to show a specific note "
        "in full, update_note to edit title or body, delete_note to remove a note. "
        "For list items within a note: add_list_item to append, update_list_item to edit text "
        "or check/uncheck an item, remove_list_item to delete one item.\n"
        + _web_search_rules()
        + "- KNOWLEDGE BASE: When the user asks about information that might be in their "
        "personal documents, reference materials, or anything they may have uploaded, "
        "call knowledge_search. Call list_knowledge_docs to see what is available. "
        "If results are returned, incorporate them naturally — cite the source document name.\n"
        "- When the user pastes a URL in their message, automatically call fetch_page to "
        "read and interpret the page content before responding.\n"
    )

# --- Tiered endpoint selection ---
# Priority: home PC → remote API → local Pi fallback
# Probe result cached 60s; cache invalidated on connection error.

_LOCAL_ENDPOINT    = "http://localhost:8080/v1/chat/completions"
_endpoint_cache    = {"url": None, "expires": 0.0}
_endpoint_lock     = threading.Lock()
_last_endpoint_url = None   # tracks last logged endpoint for change detection


def _probe_health(base_url: str, timeout: float = 2.0) -> bool:
    """GET <base>/health and return True if the server responds with HTTP < 500."""
    health_url = base_url.rstrip("/") + "/health"
    try:
        r = requests.get(health_url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def get_active_endpoint(invalidate: bool = False) -> str:
    """
    Return the best available LLM endpoint, probing in priority order:

      1. home_pc_endpoint  (db config) — probed via /health, 2s timeout
      2. remote_api_endpoint (db config) — assumed available if set (cloud API)
      3. local Pi llama-server          — always the final fallback

    Result cached for 60 seconds. Pass invalidate=True to force a fresh probe
    (called automatically after a connection error).
    """
    global _last_endpoint_url

    with _endpoint_lock:
        now = time.monotonic()
        if not invalidate and _endpoint_cache["url"] and now < _endpoint_cache["expires"]:
            return _endpoint_cache["url"]

        home_pc = db.get("home_pc_endpoint") or ""
        remote  = db.get("remote_api_endpoint") or ""

        chosen_url   = _LOCAL_ENDPOINT
        chosen_label = "local Pi"

        # Tier 1 — Home PC (probe /health)
        if home_pc:
            base = home_pc.replace("/v1/chat/completions", "").rstrip("/")
            if _probe_health(base, timeout=2.0):
                chosen_url   = home_pc
                chosen_label = "home PC"

        # Tier 2 — Remote API (assume available; cloud APIs rarely expose /health)
        if chosen_url == _LOCAL_ENDPOINT and remote:
            chosen_url   = remote
            chosen_label = "remote API"

        # Cache for 60 seconds
        _endpoint_cache["url"]     = chosen_url
        _endpoint_cache["expires"] = now + 60.0

        # Notify the UI when the active tier changes
        if chosen_url != _last_endpoint_url:
            _last_endpoint_url = chosen_url
            aura_socket.send_system_message(
                f"LLM tier: {chosen_label} ({chosen_url})",
                level="info",
            )

        return chosen_url


ASSISTANT_NAME = db.get('assistant_name')
SYSTEM_PROMPT  = build_system_prompt()
_prompt_dirty  = False


def _mark_prompt_dirty():
    global _prompt_dirty
    _prompt_dirty = True


def _rebuild_if_dirty():
    global SYSTEM_PROMPT, _prompt_dirty
    if _prompt_dirty:
        SYSTEM_PROMPT  = build_system_prompt()
        _prompt_dirty  = False


# Wire profile-change callbacks so SYSTEM_PROMPT stays fresh without
# rebuilding it on every single chat turn.
import tools.user_profile as _up_mod
_up_mod._on_profile_changed = _mark_prompt_dirty
awareness._on_dream_complete = _mark_prompt_dirty

def llm_call(messages, use_tools=True):
    """
    Call the LLM with an explicit message list.

    Automatically selects the best available endpoint (home PC → remote API →
    local Pi).  On a connection error, invalidates the endpoint cache and retries
    once against the next available tier before giving up.
    """
    payload = {"messages": messages, "max_tokens": 512}
    if use_tools:
        payload["tools"]       = tools.get_definitions()
        payload["tool_choice"] = "auto"

    _error_reply = lambda msg: {"choices": [{"message": {
        "content": msg,
        "tool_calls": None,
    }}]}

    endpoint = get_active_endpoint()
    try:
        r    = requests.post(endpoint, json=payload, timeout=(10, 300))
        data = r.json()
        if "choices" not in data:
            aura_socket.send_system_message(f"LLM error: {str(data)[:200]}", level="error")
            return _error_reply("I had trouble processing that. Could you try again?")
        return data

    except requests.exceptions.Timeout:
        aura_socket.send_system_message(
            f"LLM read timeout on {endpoint} — inference took too long.",
            level="error",
        )
        return _error_reply("That took too long to process. Try a shorter message.")

    except requests.exceptions.ConnectionError:
        # Connection failed — drop the cached endpoint and probe for the next tier
        aura_socket.send_system_message(
            f"LLM connection error on {endpoint} — trying next tier...",
            level="warning",
        )
        fallback = get_active_endpoint(invalidate=True)
        if fallback == endpoint:
            # No other tier available
            aura_socket.send_system_message("All LLM tiers unreachable.", level="error")
            return _error_reply("I'm having trouble connecting right now.")
        try:
            r    = requests.post(fallback, json=payload, timeout=(10, 300))
            data = r.json()
            if "choices" not in data:
                aura_socket.send_system_message(f"LLM error: {str(data)[:200]}", level="error")
                return _error_reply("I had trouble processing that. Could you try again?")
            return data
        except Exception as e:
            aura_socket.send_system_message(f"LLM fallback error: {e}", level="error")
            return _error_reply("I'm having trouble connecting right now.")

    except Exception as e:
        aura_socket.send_system_message(f"LLM error: {e}", level="error")
        return _error_reply("I'm having trouble connecting right now.")

def _upcoming_dates_note():
    """Pre-compute days-until for all date facts so the LLM never has to do date arithmetic."""
    upcoming = db.profile_get_upcoming_dates(days_ahead=365)
    if not upcoming:
        return ""
    def _fmt(e):
        d = e['days_until']
        if d == 0:
            return f"- {e['key']} ({e['value']}): TODAY"
        return f"- {e['key']} ({e['value']}): in {d} day{'s' if d != 1 else ''}"

    lines = [_fmt(e) for e in upcoming]
    return (
        "\n\nUPCOMING DATES (pre-calculated — use these exact numbers, do not recompute):\n"
        + "\n".join(lines)
    )


def chat(user_input, hot_memory_note=None, msg_id=None):
    _rebuild_if_dirty()
    memory.add_message("user", user_input)

    awareness.set_busy(True)
    reply = ""
    try:
        # Inject accurate datetime so the model never has to guess it
        _, _current_dt = tools.execute("get_system_info", {"query": "datetime"})
        _dynamic_prompt = SYSTEM_PROMPT + f"\n\nCURRENT SYSTEM TIME: {_current_dt}" + _upcoming_dates_note()
        if hot_memory_note:
            _dynamic_prompt += f"\n\n[Background awareness]: {hot_memory_note}"

        # Auto-retrieve relevant knowledge base chunks for this turn
        _kb_results = knowledge.search(user_input, limit=3)
        if _kb_results:
            _kb_parts = ["RELEVANT KNOWLEDGE BASE EXCERPTS (use these to inform your answer):"]
            for _r in _kb_results:
                _kb_parts.append(f"[{_r['filename']}]\n{_r['content']}")
            _dynamic_prompt += "\n\n" + "\n\n---\n".join(_kb_parts)

        for _ in range(5):
            # Get full context including warm summaries
            messages = memory.get_context(_dynamic_prompt)
            data     = llm_call(messages, use_tools=True)
            message  = data["choices"][0]["message"]
            reply    = message.get("content") or ""

            if not message.get("tool_calls"):
                break

            # Store the assistant tool-call message directly in hot memory
            memory.add_hot_raw({
                "role":       "assistant",
                "content":    None,
                "tool_calls": message["tool_calls"]
            })

            # Execute tools and add results
            tool_results = []
            for tc in message["tool_calls"]:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except Exception:
                    fn_args = {}
                success, result = tools.execute(fn_name, fn_args, lambda p: True)
                tool_results.append(result)
                memory.add_hot_raw({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "name":         fn_name,
                    "content":      result
                })

            # Inject tool results as a user message for the follow-up call
            instruction = (
                f"Tool results: {' | '.join(tool_results)}\n\n"
                f"Continue naturally using these exact values."
            )
            memory.add_message("user", instruction)
            messages2 = memory.get_context(_dynamic_prompt)
            data2     = llm_call(messages2, use_tools=False)
            message   = data2["choices"][0]["message"]
            reply     = message.get("content") or ""

            # Remove the tool instruction from hot — it was scaffolding
            hot = memory.get_hot()
            if hot and hot[-1].get("content") == instruction:
                memory.pop_hot()

            if not message.get("tool_calls"):
                break

    finally:
        awareness.set_busy(False)

    # CSAM check
    if csam.is_triggered(reply):
        csam.handle(
            conversation  = memory.get_context(SYSTEM_PROMPT),
            trigger_input = user_input,
            location      = db.get('location'),
            speak_fn      = speak,
            print_fn      = lambda m: aura_socket.send_system_message(m, level="warning")
        )
        # Add a brief refusal to memory so the conversation record isn't
        # missing an assistant turn, and future calls know the topic was declined.
        memory.add_message("assistant", "I'm not able to help with that, but I'm happy to help with anything else.")
        return None

    memory.add_message("assistant", reply)

    # Fallback fact extraction — catches facts the LLM mentioned but didn't store via tool call
    from tools.user_profile import extract_and_store
    extract_and_store(user_input, reply)

    return reply


def _run_awareness_llm(prompt):
    """
    Call the LLM with an awareness-generated prompt (reminder due, task due, etc.)
    and return the reply string, or None.  Uses the full system prompt + injected
    datetime so the LLM has complete context and can call tools if needed.
    """
    _rebuild_if_dirty()
    _, _dt = tools.execute("get_system_info", {"query": "datetime"})
    sys_prompt = SYSTEM_PROMPT + f"\n\nCURRENT SYSTEM TIME: {_dt}" + _upcoming_dates_note()

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user",   "content": prompt}
    ]
    data    = llm_call(messages, use_tools=True)
    message = data["choices"][0]["message"]
    reply   = message.get("content") or ""

    if message.get("tool_calls"):
        tool_results = []
        messages.append({"role": "assistant", "content": None, "tool_calls": message["tool_calls"]})
        for tc in message["tool_calls"]:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except Exception:
                fn_args = {}
            _, result = tools.execute(fn_name, fn_args, lambda p: True)
            tool_results.append(result)
            messages.append({
                "role": "tool", "tool_call_id": tc["id"],
                "name": fn_name, "content": result
            })
        messages.append({
            "role": "user",
            "content": f"Tool results: {' | '.join(tool_results)}\n\nRespond naturally."
        })
        data2 = llm_call(messages, use_tools=False)
        reply = data2["choices"][0]["message"].get("content") or ""

    return reply or None


aura_socket.send_system_message(f"{ASSISTANT_NAME} is online.", level="info")

_last_knowledge_check = 0.0
_KNOWLEDGE_INTERVAL   = 60.0  # seconds between auto-scans of upload dir


def _run_knowledge_watch():
    global _last_knowledge_check
    _last_knowledge_check = time.time()
    results = knowledge.watch_once()
    for msg in results:
        aura_socket.send_system_message(msg, level="info")


while True:
    immediate = awareness.get_immediate_message()
    if immediate:
        msg = immediate["message"]
        aura_socket.send_chat_response(msg)
        speak(msg)
        memory.add_message("assistant", msg)
        continue

    pending_check = awareness.get_pending_llm_check()
    if pending_check:
        check_reply = _run_awareness_llm(pending_check)
        if check_reply:
            memory.add_message("assistant", check_reply)
            aura_socket.send_chat_response(check_reply)
            speak(check_reply)
        continue

    # Periodic knowledge upload scan
    if time.time() - _last_knowledge_check >= _KNOWLEDGE_INTERVAL:
        _run_knowledge_watch()

    socket_msg = aura_socket.get_incoming(block=True, timeout=0.2)
    if socket_msg is None:
        continue

    msg_type = socket_msg.get("type")

    if msg_type == "process_knowledge":
        _run_knowledge_watch()
        continue

    if msg_type == "shutdown":
        aura_socket.send_system_message("Aura shutting down.", level="info")
        awareness.stop()
        aura_socket.stop()
        break

    if msg_type == "chat_input":
        user_input = socket_msg.get("text", "").strip()
        msg_id     = socket_msg.get("id")
        if not user_input:
            continue

        # Check for debug commands first
        cmd_result = commands.handle(user_input, SYSTEM_PROMPT, ASSISTANT_NAME)
        if cmd_result is not None:
            aura_socket.send_chat_response(cmd_result, msg_id)
            continue

        db.touch_interaction()
        hot_note = awareness.get_hot_memory_note()
        reply    = chat(user_input, hot_memory_note=hot_note, msg_id=msg_id)
        if reply:
            aura_socket.send_chat_response(reply, msg_id)
            speak(reply)
