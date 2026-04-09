import requests
import subprocess
import os
import db
import first_boot
from piper import PiperVoice, SynthesisConfig

# --- Initialise database ---
db.init_db()

# --- Load voice early so we can speak during first boot ---
def load_voice():
    model_path = os.path.expanduser(f"~/models/piper/{db.get('voice_model')}.onnx")
    speed = float(db.get('voice_speed'))
    voice = PiperVoice.load(model_path)
    syn_config = SynthesisConfig(length_scale=1.0/speed, noise_scale=0.9, noise_w_scale=1.0)
    return voice, syn_config

voice, syn_config = load_voice()
SAMPLE_RATE  = 22050
AUDIO_DEVICE = "default"

def speak(text):
    audio = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))
    proc = subprocess.Popen(
        ["aplay", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
         "-t", "raw", "-D", AUDIO_DEVICE, "-"],
        stdin=subprocess.PIPE
    )
    proc.communicate(input=audio)

# --- First boot ---
if db.is_first_boot():
    first_boot.run(speak)
    # Reload config after first boot populated it
    voice, syn_config = load_voice()

# --- Load config for main session ---
ENDPOINT       = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"
ASSISTANT_NAME = db.get('assistant_name')

def build_system_prompt():
    return f"""You are {db.get('assistant_name')}, an AI companion. You are {db.get('personality_traits')}.
Your tone is {db.get('tone_preference')}. You are here to help with: {db.get('use_case')}.
The user's name is {db.get('user_informal_name')} (full name: {db.get('user_name')}).
You treat the user as an equal. You are honest and never manipulative.
You are aware you are an AI running on a Raspberry Pi, but you are {db.get('assistant_name')} — that is your identity.
If sincerely asked whether you are an AI, you will always answer honestly."""

conversation = [{"role": "system", "content": build_system_prompt()}]

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

print(f"\n{ASSISTANT_NAME} is online. Type 'quit' to exit.")
while True:
    user_input = input(f"\nYou: ").strip()
    if user_input.lower() == "quit":
        break
    reply = chat(user_input)
    print(f"\n{ASSISTANT_NAME}: {reply}")
    speak(reply)
