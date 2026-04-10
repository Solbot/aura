import requests
import subprocess
import json
import os
import db
import csam
import tools
import first_boot
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
        f"You have access to tools. When the user asks about the current time, date, "
        f"system status, temperature, fan, disk space, RAM, uptime or network — always "
        f"call the get_system_info tool rather than guessing. "
        f"When you receive tool results, present them naturally in conversation — "
        f"never say 'this information is retrieved from a tool'."
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

def chat(user_input):
    global _csam_locked
    conversation.append({"role": "user", "content": user_input})

    # First LLM call — may return tool_calls or a direct reply
    data    = llm_call(conversation)
    message = data["choices"][0]["message"]
    reply   = message.get("content") or ""

    # Handle tool calls
    if message.get("tool_calls"):
        # Append assistant message with tool_calls — use None not "" for content
        assistant_msg = {
            "role":       "assistant",
            "content":    None,
            "tool_calls": message["tool_calls"]
        }
        conversation.append(assistant_msg)

        # Execute each tool and append results
        for tc in message["tool_calls"]:
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except Exception:
                fn_args = {}
            success, result = tools.execute(fn_name, fn_args, confirm_fn)
            conversation.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "name":         fn_name,
                "content":      result
            })

        # Second LLM call — get natural language response from tool results
        data2 = llm_call(conversation, use_tools=False)
        reply = data2["choices"][0]["message"].get("content") or ""

    # CSAM check — always runs on final reply
    if csam.is_triggered(reply):
        _csam_locked = True
        csam.handle(
            conversation  = conversation,
            trigger_input = user_input,
            location      = db.get('location'),
            speak_fn      = speak,
            print_fn      = lambda m: print(f"\n{ASSISTANT_NAME}: {m}")
        )
        conversation.pop()
        return None

    # Short refusal if topic was previously locked this session
    if _csam_locked:
        short = "I've already shared resources that can help. I'm not able to discuss this further."
        conversation.append({"role": "assistant", "content": short})
        return short

    conversation.append({"role": "assistant", "content": reply})
    return reply

print(f"\n{ASSISTANT_NAME} is online. Type 'quit' to exit.")
while True:
    user_input = input(f"\nYou: ").strip()
    if user_input.lower() == "quit":
        break
    reply = chat(user_input)
    if reply:
        print(f"\n{ASSISTANT_NAME}: {reply}")
        speak(reply)
