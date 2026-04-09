import requests
import subprocess
import os
from piper import PiperVoice, SynthesisConfig

# --- TTS Configuration (will move to config system later) ---
VOICE_MODEL = os.path.expanduser("~/models/piper/en_US-amy-medium.onnx")
VOICE_SPEED = 1.0        # 0.5 (slow) to 2.0 (fast)
AUDIO_DEVICE = "default" # aplay device string
SAMPLE_RATE = 22050      # must match voice model

# --- Endpoint ---
ENDPOINT = "http://localhost:8080/v1/chat/completions"

SYSTEM_PROMPT = """You are AURA, an AI companion. You are intelligent,
witty, direct and honest. You treat the user as an equal."""

# Load voice model once at startup
voice = PiperVoice.load(VOICE_MODEL)
syn_config = SynthesisConfig(length_scale=1.0/VOICE_SPEED, noise_scale=0.9, noise_w_scale=1.0)

conversation = [{"role": "system", "content": SYSTEM_PROMPT}]

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

print("AURA is online. Type 'quit' to exit.")
while True:
    user_input = input("\nYou: ").strip()
    if user_input.lower() == "quit":
        break
    reply = chat(user_input)
    print(f"\nAURA: {reply}")
    speak(reply)
