import requests
import subprocess
import json
import os
import db
import csam
import tools
import first_boot
import awareness
import threading
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

# --- Build system prompt ---
def build_system_prompt():
    # Load and deduplicate profile facts — most recent value per key
    facts = db.profile_get_all()
    seen = {}
    for f in facts:
        seen[f['key']] = f['value']

    profile_str = ""
    if seen:
        profile_str = (
            "\n\nWHAT YOU ALREADY KNOW ABOUT THE USER:\n" +
            "\n".join([f"- {k}: {v}" for k, v in seen.items()]) +
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
        "- DATE STORAGE: Always store dates in 'Month DD YYYY' format when the year is known, "
        "or 'Month DD' when only the day/month is known. Never store relative words.\n"
        "- DATE INFERENCE: When you can calculate a year from duration information, do so. "
        "Examples: 'anniversary is April 23, married 21 years' -> store 'April 23 2005'. "
        "'born in 1974, birthday April 10' -> store 'April 10 1974'. "
        "Always call get_system_info for today's date first when you need to calculate years.\n"
        "- UPDATING FACTS: If you learn new information that completes or corrects an existing "
        "stored fact, call store_user_fact again with the complete updated value.\n"
        "- Use get_user_facts when asked to recall something about the user.\n"
        "- Present tool results as exact values naturally in conversation."
    )

ENDPOINT       = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"
ASSISTANT_NAME = db.get('assistant_name')
conversation   = [{"role": "system", "content": build_system_prompt()}]

def confirm_fn(prompt):
    response = input(prompt).strip().lower()
    return response in ("yes", "y")

def llm_call(messages, use_tools=True):
    payload = {"messages": messages, "max_tokens": 512}
    if use_tools:
        payload["tools"]       = tools.get_definitions()
        payload["tool_choice"] = "auto"
    try:
        r    = requests.post(ENDPOINT, json=payload, timeout=120)
        data = r.json()
        if "choices" not in data:
            print(f"[LLM error: {str(data)[:200]}]")
            return {"choices": [{"message": {
                "content": "I had trouble processing that. Could you try again?",
                "tool_calls": None
            }}]}
        return data
    except Exception as e:
        print(f"[LLM error: {e}]")
        return {"choices": [{"message": {
            "content": "I'm having trouble connecting right now.",
            "tool_calls": None
        }}]}

def process_tool_calls(message):
    if not message.get("tool_calls"):
        return None
    conversation.append({
        "role":       "assistant",
        "content":    None,
        "tool_calls": message["tool_calls"]
    })
    tool_results_summary = []
    for tc in message["tool_calls"]:
        fn_name = tc["function"]["name"]
        try:
            fn_args = json.loads(tc["function"]["arguments"])
        except Exception:
            fn_args = {}
        success, result = tools.execute(fn_name, fn_args, confirm_fn)
        tool_results_summary.append(result)
        conversation.append({
            "role":         "tool",
            "tool_call_id": tc["id"],
            "name":         fn_name,
            "content":      result
        })
    return tool_results_summary

def chat(user_input, hot_memory_note=None):
    global _csam_locked

    if hot_memory_note:
        conversation.append({
            "role":    "system",
            "content": f"[Background awareness — act on this naturally if relevant]: {hot_memory_note}"
        })

    conversation.append({"role": "user", "content": user_input})

    # LLM loop — handles chained tool calls
    max_iterations = 5
    reply = ""
    for _ in range(max_iterations):
        data    = llm_call(conversation)
        message = data["choices"][0]["message"]
        reply   = message.get("content") or ""

        if not message.get("tool_calls"):
            break

        results = process_tool_calls(message)
        instruction = (
            f"Tool results: {' | '.join(results)}\n\n"
            f"Continue naturally using these exact values."
        )
        conversation.append({"role": "user", "content": instruction})
        data2   = llm_call(conversation, use_tools=True)
        message = data2["choices"][0]["message"]
        reply   = message.get("content") or ""
        conversation.pop()

        if not message.get("tool_calls"):
            break

    # CSAM check
    if csam.is_triggered(reply):
        _csam_locked = True
        if hot_memory_note:
            conversation.pop()
        csam.handle(
            conversation  = conversation,
            trigger_input = user_input,
            location      = db.get('location'),
            speak_fn      = speak,
            print_fn      = lambda m: print(f"\n{ASSISTANT_NAME}: {m}")
        )
        conversation.pop()
        return None

    if _csam_locked:
        short = "I've already shared resources that can help. I'm not able to discuss this further."
        conversation.append({"role": "assistant", "content": short})
        return short

    conversation.append({"role": "assistant", "content": reply})
    return reply

print(f"\n{ASSISTANT_NAME} is online. Type 'quit' to exit.")
while True:
    immediate = awareness.get_immediate_message()
    if immediate:
        msg = immediate["message"]
        print(f"\n{ASSISTANT_NAME}: {msg}")
        speak(msg)
        conversation.append({"role": "assistant", "content": msg})
        continue

    user_input = input(f"\nYou: ").strip()
    if user_input.lower() == "quit":
        awareness.stop()
        break

    hot_note = awareness.get_hot_memory_note()
    reply    = chat(user_input, hot_memory_note=hot_note)
    if reply:
        print(f"\n{ASSISTANT_NAME}: {reply}")
        speak(reply)
