# tools/user_profile.py
# User profile tool -- stores and retrieves facts Aether learns about the user.
# Facts are persisted in SQLite with timestamps and confidence levels.

import tools
import db
import requests
import json
from datetime import datetime

ENDPOINT = None  # Set at runtime from db

# Callback wired by aura.py so it can rebuild SYSTEM_PROMPT when the profile changes.
_on_profile_changed = None

EXTRACT_PROMPT = """Extract ALL personal facts from the user's statement. Return one JSON object per fact — no limit on how many.
Example: "I'm 40, live in Bristol with my wife Kate and our 3 kids, and I'm a plumber."
Returns: [{"key":"age","value":"40"},{"key":"location","value":"Bristol"},{"key":"spouse_name","value":"Kate"},{"key":"num_children","value":"3"},{"key":"job_title","value":"plumber"}]
Use absolute dates (e.g. "April 23 2005" not "last year"). Return [] if no personal facts. Return ONLY the JSON array, no other text."""

def _get_endpoint():
    return db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"

def _llm(messages, max_tokens=512):
    r = requests.post(_get_endpoint(), json={"messages": messages, "max_tokens": max_tokens})
    return r.json()["choices"][0]["message"]["content"]

def _resolve_date(value):
    """
    Resolve relative date references to absolute Month DD format.
    Catches: today, tomorrow, yesterday, day names without months.
    """
    from datetime import datetime, timedelta
    import re
    v = value.strip().lower()
    now = datetime.now()

    if v == "today":
        return now.strftime("%B %d")
    if v == "tomorrow":
        return (now + timedelta(days=1)).strftime("%B %d")
    if v == "yesterday":
        return (now - timedelta(days=1)).strftime("%B %d")

    # Day names like "monday", "next friday" without a month
    day_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for day in day_names:
        if day in v and not re.search(
            r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec', v, re.IGNORECASE
        ):
            # Calculate the next occurrence of that day
            target = ["monday","tuesday","wednesday","thursday",
                      "friday","saturday","sunday"].index(day)
            current = now.weekday()
            days_ahead = (target - current) % 7
            if days_ahead == 0:
                days_ahead = 7 if "next" in v else 0
            resolved = now + timedelta(days=days_ahead)
            return resolved.strftime("%B %d")

    return value  # Already absolute, return unchanged

# Duration key -> date key mappings for automatic year inference
_DURATION_TO_DATE = {
    "birth_year":       "birthday",
    "years_married":    "wedding_anniversary",
    "married_years":    "wedding_anniversary",
    "years_together":   "anniversary",
    "years_at_job":     "job_start",
    "years_employed":   "job_start",
    "years_in_home":    "moved_in",
}

def _infer_year_from_duration(years_str):
    """Calculate event year from duration: current_year - years_ago."""
    from datetime import datetime
    import re
    match = re.search(r'\d+', str(years_str))
    if match:
        years = int(match.group())
        return str(datetime.now().year - years)
    return None

def store_fact(key, value, confidence="high", source="conversation"):
    """Store a fact about the user. Resolves dates, merges duration+date pairs."""
    result = _store_fact_inner(key, value, confidence, source)
    if _on_profile_changed:
        _on_profile_changed()
    return result

def _store_fact_inner(key, value, confidence, source):
    import re

    # Resolve relative date references for date-related keys
    date_keys = ["birthday", "anniversary", "date", "born", "due", "start", "moved"]
    if any(dk in key.lower() for dk in date_keys):
        value = _resolve_date(value)

    # --- Duration key handling ---
    # If this is a duration key (e.g. years_married), try to merge with corresponding date
    key_lower = key.lower()
    if key_lower in _DURATION_TO_DATE:
        date_key = _DURATION_TO_DATE[key_lower]
        existing_date = db.profile_get(date_key)
        if existing_date:
            date_val = existing_date["value"]
            # Only merge if the date doesn't already have a year
            if not re.search(r'\d{4}', date_val):
                year = _infer_year_from_duration(value)
                if year:
                    merged = f"{date_val} {year}"
                    db.profile_set(date_key, merged, source=source, confidence="inferred")
                    db.profile_set(key, value, source=source, confidence=confidence)
                    return f"Updated {date_key} to: {merged}"
        db.profile_set(key, value, source=source, confidence=confidence)
        return f"Stored: {key} = {value}"

    # --- Date key handling ---
    # If storing a date key, check if a corresponding duration key exists to infer year
    for dur_key, date_key in _DURATION_TO_DATE.items():
        if key_lower == date_key:
            # Check if we already have a year
            if not re.search(r'\d{4}', value):
                existing_dur = db.profile_get(dur_key)
                if existing_dur:
                    year = _infer_year_from_duration(existing_dur["value"])
                    if year:
                        value = f"{value} {year}"
                        confidence = "inferred"
            break

    # Special case: birth_year key merges into birthday
    if key_lower == "birth_year":
        existing = db.profile_get("birthday")
        if existing and not re.search(r'\d{4}', existing["value"]):
            merged = f"{existing['value']} {value}"
            db.profile_set("birthday", merged, source=source, confidence=confidence)
            db.profile_set(key, value, source=source, confidence=confidence)
            return f"Updated birthday to: {merged}"

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

def extract_and_store(user_input, assistant_reply=""):
    """
    Extract and store user facts from a conversation turn.
    Accepts user_input and optional assistant_reply separately so the
    assistant's rambling ("I'll store that...") doesn't confuse the extractor.
    Strategy:
      1. Scan assistant_reply for "Stored user fact: X" patterns — pass X to LLM
      2. If no such patterns, extract from user_input directly
    Returns list of newly stored facts.
    """
    import re as _re
    now = datetime.now().strftime("%A %B %d %Y")
    stored = []

    def _run_extraction(text):
        prompt = f"Today's date is {now}.\nUser said: \"{text}\"\nExtract personal facts."
        try:
            result = _llm([
                {"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user",   "content": prompt}
            ])
            clean = result.strip()
            if "```" in clean:
                lines = clean.split("\n")
                clean = "\n".join(l for l in lines if not l.startswith("```")).strip()
            facts = json.loads(clean)
            for fact in facts:
                if "key" in fact and "value" in fact:
                    confidence = fact.get("confidence", "high")
                    store_fact(fact["key"], fact["value"], confidence=confidence, source="conversation")
                    stored.append(f"{fact['key']} = {fact['value']}")
        except Exception as e:
            print(f"\r[extract_and_store error: {e}]")

    # Strategy 1: look for facts the assistant already identified in its reply
    # Matches: "store that fact: X", "I'll update the fact: X", "stored user fact: X" etc.
    if assistant_reply:
        matches = _re.findall(
            r'(?:store(?:d)?(?:\s+that)?(?:\s+user)?\s+fact|update(?:d)?(?:\s+the)?\s+fact)[s]?:?\s+["\']?(.+?)["\']?(?=\.(?:\s|$)|\n|$)',
            assistant_reply,
            _re.IGNORECASE
        )
        for m in matches:
            m = m.strip()
            if m:
                _run_extraction(m)

    # Strategy 2: extract from the user's own input (only if strategy 1 found nothing)
    if not stored and user_input.strip():
        _run_extraction(user_input.strip())

    if stored:
        print(f"\r[Facts extracted: {', '.join(stored)}]")
    return stored


def get_relevant_facts(topic):
    """Get facts relevant to a given topic or question."""
    facts = db.profile_get_all()
    if not facts:
        return "No profile information available."
    # Simple keyword matching -- good enough for now
    topic_lower = topic.lower()
    relevant = [f for f in facts if
                topic_lower in f['key'].lower() or
                topic_lower in f['value'].lower()]
    if relevant:
        return "\n".join([f"{f['key']}: {f['value']}" for f in relevant])
    return "No specific information found for that topic."

# Self-register -- two tools: read and write
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
