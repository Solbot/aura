import requests
import subprocess
import json
import os
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

# --- First boot ---
if db.is_first_boot():
    first_boot.run(speak)
    voice, syn_config = load_voice()

# --- Start background awareness thread ---
awareness.start()

# --- Start IPC socket server ---
aura_socket.start()

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
        "- Present tool results as exact values naturally in conversation."
    )

ENDPOINT       = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"
ASSISTANT_NAME = db.get('assistant_name')
SYSTEM_PROMPT  = build_system_prompt()

def llm_call(messages, use_tools=True):
    """Call LLM with an explicit message list."""
    payload = {"messages": messages, "max_tokens": 512}
    if use_tools:
        payload["tools"]       = tools.get_definitions()
        payload["tool_choice"] = "auto"
    try:
        r    = requests.post(ENDPOINT, json=payload, timeout=120)
        data = r.json()
        if "choices" not in data:
            aura_socket.send_system_message(f"LLM error: {str(data)[:200]}", level="error")
            return {"choices": [{"message": {
                "content": "I had trouble processing that. Could you try again?",
                "tool_calls": None
            }}]}
        return data
    except Exception as e:
        aura_socket.send_system_message(f"LLM error: {e}", level="error")
        return {"choices": [{"message": {
            "content": "I'm having trouble connecting right now.",
            "tool_calls": None
        }}]}

def chat(user_input, hot_memory_note=None, msg_id=None):
    global _csam_locked

    if hot_memory_note:
        memory.add_message("system", f"[Background awareness]: {hot_memory_note}")

    memory.add_message("user", user_input)

    awareness.set_busy(True)
    reply = ""
    try:
        for _ in range(5):
            # Get full context including warm summaries
            messages = memory.get_context(SYSTEM_PROMPT)
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
            messages2 = memory.get_context(SYSTEM_PROMPT)
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
    return reply


aura_socket.send_system_message(f"{ASSISTANT_NAME} is online.", level="info")

while True:
    immediate = awareness.get_immediate_message()
    if immediate:
        msg = immediate["message"]
        aura_socket.send_chat_response(msg)
        speak(msg)
        memory.add_message("assistant", msg)
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
