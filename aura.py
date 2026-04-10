import requests
import subprocess
import json
import os
import db
import csam
import tools
import first_boot
import awareness
from tools import user_profile
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
        f"You have access to tools. Use get_system_info for time/date/temperature/sensors. "
        f"Use store_user_fact whenever the user shares personal information worth remembering. "
        f"Use get_user_facts when you need to recall something about the user. "
        f"IMPORTANT: When you receive tool results, present the exact numbers and values naturally."
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

def chat(user_input, hot_memory_note=None):
    global _csam_locked

    # Inject hot memory note as a hidden system context if present
    if hot_memory_note:
        conversation.append({
            "role":    "system",
            "content": f"[Background awareness note — act on this naturally if relevant]: {hot_memory_note}"
        })

    conversation.append({"role": "user", "content": user_input})

    # First LLM call
    data    = llm_call(conversation)
    message = data["choices"][0]["message"]
    reply   = message.get("content") or ""

    # Handle tool calls
    if message.get("tool_calls"):
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
        instruction = (
            f"Tool results: {' | '.join(tool_results_summary)}\n\n"
            f"Using these exact values, answer the user's question naturally. "
            f"Include specific numbers and units."
        )
        conversation.append({"role": "user", "content": instruction})
        data2 = llm_call(conversation, use_tools=False)
        reply = data2["choices"][0]["message"].get("content") or ""
        conversation.pop()  # Remove instruction

    # CSAM check
    if csam.is_triggered(reply):
        _csam_locked = True
        # Remove hot memory note and user input from history
        if hot_memory_note:
            conversation.pop()  # remove note
        csam.handle(
            conversation  = conversation,
            trigger_input = user_input,
            location      = db.get('location'),
            speak_fn      = speak,
            print_fn      = lambda m: print(f"\n{ASSISTANT_NAME}: {m}")
        )
        conversation.pop()  # Remove user input
        return None

    if _csam_locked:
        short = "I've already shared resources that can help. I'm not able to discuss this further."
        conversation.append({"role": "assistant", "content": short})
        return short

    conversation.append({"role": "assistant", "content": reply})

    # Background fact extraction — runs silently after each turn
    recent = conversation[-4:]  # Last 2 exchanges
    transcript = "\n".join([f"{m['role'].upper()}: {m.get('content','')}"
                             for m in recent if m.get('content') and m['role'] != 'system'])
    if transcript:
        threading.Thread(
            target=user_profile.extract_and_store,
            args=(transcript,),
            daemon=True
        ).start()

    return reply

# Need threading for background extraction
import threading

print(f"\n{ASSISTANT_NAME} is online. Type 'quit' to exit.")
while True:
    # Check for immediate messages (reminders, thermal alerts) before waiting for input
    immediate = awareness.get_immediate_message()
    if immediate:
        msg = immediate["message"]
        print(f"\n{ASSISTANT_NAME}: {msg}")
        speak(msg)
        # Add to conversation so Aether has context
        conversation.append({"role": "assistant", "content": msg})
        continue

    user_input = input(f"\nYou: ").strip()
    if user_input.lower() == "quit":
        awareness.stop()
        break

    # Get any queued hot memory notes
    hot_note = awareness.get_hot_memory_note()

    reply = chat(user_input, hot_memory_note=hot_note)
    if reply:
        print(f"\n{ASSISTANT_NAME}: {reply}")
        speak(reply)
