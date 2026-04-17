import requests
import json
import db


BOOTSTRAP_PROMPT = """You are an AI assistant meeting a new user for the very first time.
You have no name yet — the user may give you one, or you can suggest Aura as a default.
Your goal is to have a warm, natural conversation that feels like meeting someone for the first time.
Through this conversation you want to learn:
- What to call the user (first name or nickname)
- What they would like to call you
- Roughly where they are based (country or region is enough)
- What they primarily want you for (companion, work assistant, reminders, all of the above, etc)
- What tone they prefer (casual and friendly, or more formal and professional)

Important rules:
- Never make the user feel interrogated — let things emerge naturally
- Never push if they decline to share something — just move on
- Keep responses concise — this is a conversation, not a speech
- Once you feel you have enough to get started, wrap up warmly"""

COMPLETE_CHECK_PROMPT = """Given this conversation transcript, has the assistant gathered enough
information to get started? Specifically: has the user shared their name and the conversation
has reached a natural conclusion point?
Reply with only YES or NO."""

EXTRACT_PROMPT = """You are a data extraction assistant. Given a conversation transcript,
extract any personal configuration values the user has shared.
Return ONLY a valid JSON object with these keys (omit any you cannot determine):
- user_name: full name if given
- user_informal_name: nickname or preferred name
- assistant_name: what the user wants to call the assistant
- assistant_gender: male/female/neutral if indicated
- location: country or region code (AU, GB, US, NZ, CA, etc)
- tone_preference: casual or formal
- use_case: brief description of primary use
- personality_traits: comma separated traits if mentioned

Return only the JSON object, no other text."""

def llm(messages, max_tokens=256):
    endpoint = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"
    response = requests.post(endpoint, json={
        "messages": messages,
        "max_tokens": max_tokens
    })
    return response.json()["choices"][0]["message"]["content"]

def is_complete(conversation):
    transcript = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in conversation if m['role'] != 'system'])
    result = llm([
        {"role": "system", "content": COMPLETE_CHECK_PROMPT},
        {"role": "user", "content": transcript}
    ], max_tokens=10)
    return result.strip().upper().startswith("YES")

def extract_config(conversation):
    transcript = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in conversation if m['role'] != 'system'])
    result = llm([
        {"role": "system", "content": EXTRACT_PROMPT},
        {"role": "user", "content": transcript}
    ], max_tokens=512)
    try:
        clean = result.strip()
        if "```" in clean:
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(clean)
    except Exception as e:
        print(f"[Config extraction failed: {e}]")
        return {}

USER_FACTS = {"user_name", "user_informal_name", "location", "tone_preference", "use_case"}

def save_config(extracted):
    mapping = {
        "user_name":          "user_name",
        "user_informal_name": "user_informal_name",
        "assistant_name":     "assistant_name",
        "assistant_gender":   "assistant_gender",
        "location":           "location",
        "tone_preference":    "tone_preference",
        "use_case":           "use_case",
        "personality_traits": "personality_traits",
    }
    saved = []
    for key, dbkey in mapping.items():
        if key in extracted and extracted[key]:
            db.set(dbkey, str(extracted[key]))
            saved.append(f"{dbkey} = {extracted[key]}")
            if key in USER_FACTS:
                db.profile_set(dbkey, str(extracted[key]), source="first_boot")
    return saved

def run(speak_fn):
    import aura_socket

    print("[First boot] Waiting for UI to connect...")
    aura_socket.wait_for_client()
    print("[First boot] UI connected. Starting setup.")

    opening = ("Hi there. I'm an AI assistant, and this is the first time we've met. "
               "Before we get started properly, I'd love to get to know you a little. "
               "What should I call you?")
    aura_socket.send_chat_response(opening)
    speak_fn(opening)

    conversation = [
        {"role": "system", "content": BOOTSTRAP_PROMPT},
        {"role": "assistant", "content": opening}
    ]

    turn = 0
    while True:
        # Wait for user input from the UI via socket
        user_input = ""
        while not user_input:
            msg = aura_socket.get_incoming(block=True, timeout=0.5)
            if msg and msg.get("type") == "chat_input":
                user_input = msg.get("text", "").strip()

        conversation.append({"role": "user", "content": user_input})
        reply = llm(conversation)
        conversation.append({"role": "assistant", "content": reply})

        aura_socket.send_chat_response(reply)
        speak_fn(reply)

        turn += 1
        # Check for completion after at least 3 turns
        if turn >= 3 and is_complete(conversation):
            extracted = extract_config(conversation)
            saved = save_config(extracted)
            if saved:
                print(f"[First boot config saved: {', '.join(saved)}]")
            else:
                print("[First boot: no config extracted — keeping defaults]")
            db.set_first_boot_complete()
            print("[First boot complete — starting main session]")
            break

    return True
