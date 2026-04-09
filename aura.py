import requests
import subprocess
import os
import sys
from piper import PiperVoice, SynthesisConfig
import db

# --- Initialise database ---
db.init_db()

# --- Load config ---
VOICE_MODEL   = os.path.expanduser(f"~/models/piper/{db.get('voice_model')}.onnx")
VOICE_SPEED   = float(db.get('voice_speed'))
AUDIO_DEVICE  = "default"
SAMPLE_RATE   = 22050
ENDPOINT      = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"
ASSISTANT_NAME = db.get('assistant_name')
USER_NAME     = db.get('user_informal_name')

# --- Build system prompt from config ---
def build_system_prompt():
    name       = db.get('assistant_name')
    gender     = db.get('assistant_gender')
    traits     = db.get('personality_traits')
    tone       = db.get('tone_preference')
    use_case   = db.get('use_case')
    user_name  = db.get('user_informal_name')
    user_full  = db.get('user_name')

    return f"""You are {name}, an AI companion. You are {traits}.
Your tone is {tone}. You are here to help with: {use_case}.
The user's name is {user_name} (full name: {user_full}).
You treat the user as an equal. You are honest and never manipulative.
You are aware you are an AI running on a Raspberry Pi, but you are {name} — that is your identity.
If sincerely asked whether you are an AI, you will always answer honestly."""

# --- Load voice model ---
voice = PiperVoice.load(VOICE_MODEL)
syn_config = SynthesisConfig(length_scale=1.0/VOICE_SPEED, noise_scale=0.9, noise_w_scale=1.0)

conversation = [{"role": "system", "content": build_system_prompt()}]

def speak(text):
    audio = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))
    proc = subprocess.Popen(
        ["aplay", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
         "-t", "raw", "-D", AUDIO_DEVICE, "-"],
        stdin=subprocess.PIPE
    )
    proc.communicate(input=audio)

def chat(user_input):
    conversation.append({"role": "user", "content": user_input})
    response = requests.post(ENDPOINT, json={
        "messages": conversation,
        "max_tokens": 256
    })
    data = response.json()
    reply = data["choices"][0]["message"]["content"]
    conversation.append({"role": "assistant", "content": reply})
    return reply

# --- First boot check ---
if db.is_first_boot():
    print("First boot detected — first boot conversation not yet implemented.")
    print("Running with defaults for now.")

print(f"{ASSISTANT_NAME} is online. Type 'quit' to exit.")
while True:
    user_input = input(f"\nYou: ").strip()
    if user_input.lower() == "quit":
        break
    reply = chat(user_input)
    print(f"\n{ASSISTANT_NAME}: {reply}")
    speak(reply)
