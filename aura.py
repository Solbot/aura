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
from piper import PiperVoice, SynthesisConfig

# --- Initialise database and tools ---
db.init_db()
tools.load_all()

# --- CSAM session lock ---
_csam_locked = False

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
    proc  = subprocess.Popen(
        ["aplay", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-t", "raw", "-D", AUDIO_DEVICE, "-"],
        stdin=subprocess.PIPE
    )
    proc.communicate(input=audio)

# --- Start IPC socket server (must be up before first boot so the UI can connect) ---
aura_socket.start()

# --- First boot ---
if db.is_first_boot():
    first_boot.run(speak)
    voice, syn_config = load_voice()

# --- Start background awareness thread ---
awareness.start()

# --- Build system prompt ---
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
        "cancel_reminder to remove them."
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
        r    = requests.post(endpoint, json=payload, timeout=120)
        data = r.json()
        if "choices" not in data:
            aura_socket.send_system_message(f"LLM error: {str(data)[:200]}", level="error")
            return _error_reply("I had trouble processing that. Could you try again?")
        return data

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
            r    = requests.post(fallback, json=payload, timeout=120)
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

def chat(user_input, hot_memory_note=None, msg_id=None):
    global _csam_locked

    if hot_memory_note:
        memory.add_message("system", f"[Background awareness]: {hot_memory_note}")

    memory.add_message("user", user_input)

    awareness.set_busy(True)
    reply = ""
    try:
        # Inject accurate datetime so the model never has to guess it
        _, _current_dt = tools.execute("get_system_info", {"query": "datetime"})
        _dynamic_prompt = SYSTEM_PROMPT + f"\n\nCURRENT SYSTEM TIME: {_current_dt}"

        for _ in range(5):
            # Get full context including warm summaries
            messages = memory.get_context(_dynamic_prompt)
            data     = llm_call(messages, use_tools=True)
            message  = data["choices"][0]["message"]
            reply    = message.get("content") or ""

            if not message.get("tool_calls"):
                break

            # Store the assistant tool-call message directly in hot memory
            memory.get_hot().append({
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
                memory.get_hot().append({
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
                hot.pop()

            if not message.get("tool_calls"):
                break

    finally:
        awareness.set_busy(False)

    # CSAM check
    if csam.is_triggered(reply):
        _csam_locked = True
        csam.handle(
            conversation  = memory.get_context(SYSTEM_PROMPT),
            trigger_input = user_input,
            location      = db.get('location'),
            speak_fn      = speak,
            print_fn      = lambda m: aura_socket.send_system_message(m, level="warning")
        )
        return None

    if _csam_locked:
        reply = "I've already shared resources that can help. I'm not able to discuss this further."

    memory.add_message("assistant", reply)

    # Fallback fact extraction — catches facts the LLM mentioned but didn't store via tool call
    from tools.user_profile import extract_and_store
    extract_and_store(f"User: {user_input}\nAssistant: {reply}")

    return reply


def _run_awareness_llm(prompt):
    """
    Call the LLM with an awareness-generated prompt (reminder due, task due, etc.)
    and return the reply string, or None.  Uses the full system prompt + injected
    datetime so the LLM has complete context and can call tools if needed.
    """
    if _csam_locked:
        return None

    _, _dt = tools.execute("get_system_info", {"query": "datetime"})
    sys_prompt = SYSTEM_PROMPT + f"\n\nCURRENT SYSTEM TIME: {_dt}"

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

    socket_msg = aura_socket.get_incoming(block=True, timeout=0.2)
    if socket_msg is None:
        continue

    msg_type = socket_msg.get("type")

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
