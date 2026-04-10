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
    return (
        csam.get_core_prompt() + "\n\n" +
        f"You are {db.get('assistant_name')}, an AI companion. "
        f"You are {db.get('personality_traits')}. "
        f"Your tone is {db.get('tone_preference')}. "
        f"You are here to help with: {db.get('use_case')}. "
        f"The user's name is {db.get('user_informal_name')} (full name: {db.get('user_name')}). "
        f"You treat the user as an equal. You are honest and never manipulative. "
        f"You are aware you are an AI running on a Raspberry Pi. "
        f"If sincerely asked whether you are an AI, always answer honestly.\n\n"

        f"TOOL USAGE RULES:\n"
        f"- For time/date/temperature/sensors: always call get_system_info — never guess.\n"
        f"- You have NO external weather or environmental sensors. Never invent weather data.\n"
        f"- When the user shares personal facts worth remembering (birthday, family, job, "
        f"preferences, important dates, hobbies): IMMEDIATELY call store_user_fact.\n"
        f"- For dates: ALWAYS call get_system_info first to get today's absolute date, "
        f"then store the resolved absolute date. Never store relative words like 'today' "
        f"or 'tomorrow' — always convert to 'Month DD' format e.g. 'April 10'.\n"
        f"- Use get_user_facts when you need to recall something about the user.\n"
        f"- When you receive tool results, present the exact values naturally in conversation."
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
    r = requests.post(ENDPOINT, json=payload)
    return r.json()

def process_tool_calls(message):
    """Execute all tool calls in a message, return results summary."""
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

    # LLM call loop — handles chained tool calls (e.g. get date then store fact)
    max_iterations = 5
    reply = ""
    for _ in range(max_iterations):
        data    = llm_call(conversation)
        message = data["choices"][0]["message"]
        reply   = message.get("content") or ""

        if not message.get("tool_calls"):
            break  # No more tool calls — we have the final reply

        results = process_tool_calls(message)

        # Ask LLM to continue with tool results
        instruction = (
            f"Tool results: {' | '.join(results)}\n\n"
            f"Continue naturally using these exact values."
        )
        conversation.append({"role": "user", "content": instruction})
        data2  = llm_call(conversation, use_tools=True)  # Allow further tool calls
        message = data2["choices"][0]["message"]
        reply   = message.get("content") or ""
        conversation.pop()  # Remove instruction

        if not message.get("tool_calls"):
            break  # Final reply reached

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
    # Check for immediate messages before waiting for input
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
