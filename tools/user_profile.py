# tools/user_profile.py
# User profile tool — stores and retrieves facts Aether learns about the user.
# Facts are persisted in SQLite with timestamps and confidence levels.

import tools
import db
import requests
import json
from datetime import datetime

ENDPOINT = None  # Set at runtime from db

EXTRACT_PROMPT = """You are a fact extraction assistant. Given a conversation excerpt,
extract any personal facts about the user that would be useful to remember long-term.
Focus on: name, birthday, family members, job, location, preferences, important dates,
hobbies, health, and any other personal details shared.

For dates mentioned relatively (e.g. "next Friday", "last Tuesday"), calculate the
absolute date using today's date which will be provided.

Return ONLY a valid JSON array of objects, each with:
- key: short snake_case label (e.g. "birthday", "wife_name", "job_title")
- value: the fact value (e.g. "April 10", "Joanne", "Security Installer")
- confidence: "high" if stated directly, "inferred" if calculated or implied

If no new facts are found, return an empty array [].
Return only the JSON array, no other text."""

def _get_endpoint():
    return db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"

def _llm(messages, max_tokens=512):
    r = requests.post(_get_endpoint(), json={"messages": messages, "max_tokens": max_tokens})
    return r.json()["choices"][0]["message"]["content"]

def store_fact(key, value, confidence="high", source="conversation"):
    """Store a fact about the user."""
    db.profile_set(key, value, source=source, confidence=confidence)
    return f"Stored: {key} = {value}"

def get_fact(key):
    """Retrieve a specific fact about the user."""
    fact = db.profile_get(key)
    if fact:
        return f"{key}: {fact['value']} (learned {fact['learned_at'][:10]}, confidence: {fact['confidence']})"
    return f"No information stored for '{key}'"

def get_all_facts():
    """Retrieve all stored facts about the user."""
    facts = db.profile_get_all()
    if not facts:
        return "No profile facts stored yet."
    return "\n".join([f"{f['key']}: {f['value']} ({f['confidence']})" for f in facts])

def extract_and_store(conversation_text):
    """
    Run fact extraction on a conversation excerpt and store any new facts found.
    Called automatically after each conversation turn.
    Returns list of newly stored facts.
    """
    now = datetime.now().strftime("%A %B %d %Y")
    prompt = f"Today's date is {now}.\n\nConversation:\n{conversation_text}"
    try:
        result = _llm([
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user",   "content": prompt}
        ])
        clean = result.strip()
        if "```" in clean:
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
        facts = json.loads(clean)
        stored = []
        for fact in facts:
            if "key" in fact and "value" in fact:
                confidence = fact.get("confidence", "high")
                db.profile_set(fact["key"], fact["value"],
                               source="conversation", confidence=confidence)
                stored.append(f"{fact['key']} = {fact['value']}")
        return stored
    except Exception:
        return []

def get_relevant_facts(topic):
    """Get facts relevant to a given topic or question."""
    facts = db.profile_get_all()
    if not facts:
        return "No profile information available."
    # Simple keyword matching — good enough for now
    topic_lower = topic.lower()
    relevant = [f for f in facts if
                topic_lower in f['key'].lower() or
                topic_lower in f['value'].lower()]
    if relevant:
        return "\n".join([f"{f['key']}: {f['value']}" for f in relevant])
    return "No specific information found for that topic."

# Self-register — two tools: read and write
tools.register(
    name        = "store_user_fact",
    description = (
        "Store a fact you've learned about the user during conversation. "
        "Use this when the user shares personal information worth remembering: "
        "birthdays, family members, job, location, preferences, important dates, hobbies. "
        "For relative dates like 'next Friday', calculate and store the absolute date."
    ),
    parameters  = {
        "type": "object",
        "properties": {
            "key":        {"type": "string", "description": "Short label e.g. birthday, wife_name, job_title"},
            "value":      {"type": "string", "description": "The fact value"},
            "confidence": {"type": "string", "description": "high or inferred", "enum": ["high", "inferred"]}
        },
        "required": ["key", "value"]
    },
    function    = lambda key, value, confidence="high": store_fact(key, value, confidence),
    permission  = tools.FREE
)

tools.register(
    name        = "get_user_facts",
    description = (
        "Retrieve facts stored about the user. "
        "Use when you need to recall something about the user, "
        "or when asked 'do you remember' type questions."
    ),
    parameters  = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Specific fact key to retrieve, or omit for all facts"}
        },
        "required": []
    },
    function    = lambda key=None: get_fact(key) if key else get_all_facts(),
    permission  = tools.FREE
)
