# tools/user_profile.py
# User profile tool — stores and retrieves facts Aether learns about the user.
# Facts are persisted in SQLite with timestamps and confidence levels.
# Values are normalised via a small LLM call before storage.

import tools
import db
import requests
import json
import re
from datetime import datetime, timedelta

ENDPOINT = None  # Set at runtime

EXTRACT_PROMPT = """You are a fact extraction assistant. Given a conversation excerpt,
extract any personal facts about the user that would be useful to remember long-term.
Focus on: name, birthday, family members, job, location, preferences, important dates,
hobbies, health, and any other personal details shared.

CRITICAL DATE RULE: Never store relative date words like "today", "tomorrow", "next Friday".
Always resolve relative dates to absolute dates using the current date provided.
Store dates in "Month DD" or "Month DD YYYY" format e.g. "April 10", "April 10 1974".

Return ONLY a valid JSON array of objects, each with:
- key: short snake_case label (e.g. "birthday", "wife_name", "job_title")
- value: the fact value — always use absolute dates, never relative ones
- confidence: "high" if stated directly, "inferred" if calculated or implied

If no new facts are found, return an empty array [].
Return only the JSON array, no other text."""

# Duration key -> corresponding date key for automatic year inference
DURATION_TO_DATE = {
    "birth_year":       "birthday",
    "years_married":    "anniversary",
    "married_years":    "anniversary",
    "wedding_years":    "anniversary",
    "years_together":   "anniversary",
    "years_at_job":     "job_start",
    "years_employed":   "job_start",
    "years_in_home":    "moved_in",
}

def _get_endpoint():
    return db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"

def _llm(messages, max_tokens=512):
    r = requests.post(_get_endpoint(), json={"messages": messages, "max_tokens": max_tokens})
    return r.json()["choices"][0]["message"]["content"]

def _resolve_date(value):
    """Resolve relative date references to absolute Month DD format."""
    v = str(value).strip().lower()
    now = datetime.now()

    if v == "today":
        return now.strftime("%B %d")
    if v == "tomorrow":
        return (now + timedelta(days=1)).strftime("%B %d")
    if v == "yesterday":
        return (now - timedelta(days=1)).strftime("%B %d")

    # "in N days" or "N days from now"
    m = re.match(r'in (\d+) days?', v) or re.match(r'(\d+) days? from now', v)
    if m:
        return (now + timedelta(days=int(m.group(1)))).strftime("%B %d")

    # Day names without a month
    day_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for day in day_names:
        if day in v and not re.search(r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec', v, re.IGNORECASE):
            target = day_names.index(day)
            current = now.weekday()
            days_ahead = (target - current) % 7
            if days_ahead == 0:
                days_ahead = 7 if "next" in v else 0
            return (now + timedelta(days=days_ahead)).strftime("%B %d")

    return value  # Already absolute

def _infer_year_from_duration(years_str):
    """Calculate event year: current_year - years_ago."""
    match = re.search(r'\d+', str(years_str))
    if match:
        return str(datetime.now().year - int(match.group()))
    return None

def _normalize_value(key, value):
    """
    Use a small LLM call to clean the value before storing.
    Skips normalization if value already looks clean.
    """
    v = str(value).strip()

    # Already clean — skip LLM call
    if re.match(r'^\d+$', v):
        return v  # Pure number
    if re.match(r'^[A-Za-z]+ \d{1,2}( \d{4})?$', v):
        return v  # "Month DD" or "Month DD YYYY"
    if re.match(r'^[A-Za-z][a-zA-Z ]{0,30}$', v) and len(v.split()) <= 3:
        return v  # Short name

    # Needs cleaning
    today = datetime.now().strftime("%B %d %Y")
    prompt = (
        f"Today is {today}.\n"
        f"Key: {key}\n"
        f"Raw value: {v}\n\n"
        f"Extract ONLY the clean value appropriate for this key.\n"
        f"- For date keys: return 'Month DD' or 'Month DD YYYY' format\n"
        f"- For name keys: return just the name\n"
        f"- For number/age/year keys: return just the number\n"
        f"- Resolve relative dates like 'today' to the actual date\n"
        f"Return only the cleaned value, nothing else. No explanation."
    )
    try:
        result = _llm([
            {"role": "system", "content": "You are a data cleaning assistant. Return only the cleaned value, nothing else."},
            {"role": "user",   "content": prompt}
        ], max_tokens=20)
        cleaned = result.strip().strip('"').strip("'")
        return cleaned if cleaned else v
    except Exception:
        return v

def store_fact(key, value, confidence="high", source="conversation"):
    """Store a fact. Normalises value, resolves dates, merges duration+date pairs."""

    # Normalise first
    value = _normalize_value(key, value)

    # Resolve relative dates for date-type keys
    date_keys = ["birthday", "anniversary", "date", "born", "due", "start", "moved"]
    if any(dk in key.lower() for dk in date_keys):
        value = _resolve_date(value)

    key_lower = key.lower()

    # Age -> infer birth_year and merge with birthday
    if key_lower == "age":
        birth_year = str(datetime.now().year - int(re.search(r'\d+', str(value)).group()))
        existing_bday = db.profile_get("birthday")
        if existing_bday and not re.search(r'\d{4}', existing_bday["value"]):
            merged = f"{existing_bday['value']} {birth_year}"
            db.profile_set("birthday", merged, source=source, confidence="inferred")
        db.profile_set("birth_year", birth_year, source=source, confidence="inferred")
        db.profile_set(key, value, source=source, confidence=confidence)
        return f"Stored age: {value}, inferred birth year: {birth_year}"

    # birth_year -> merge into birthday
    if key_lower == "birth_year":
        existing = db.profile_get("birthday")
        if existing and not re.search(r'\d{4}', existing["value"]):
            merged = f"{existing['value']} {value}"
            db.profile_set("birthday", merged, source=source, confidence=confidence)
        db.profile_set(key, value, source=source, confidence=confidence)
        return f"Stored birth_year: {value}"

    # Duration key -> merge with corresponding date key
    if key_lower in DURATION_TO_DATE:
        date_key = DURATION_TO_DATE[key_lower]
        existing_date = db.profile_get(date_key)
        if existing_date and not re.search(r'\d{4}', existing_date["value"]):
            year = _infer_year_from_duration(value)
            if year:
                merged = f"{existing_date['value']} {year}"
                db.profile_set(date_key, merged, source=source, confidence="inferred")
                db.profile_set(key, value, source=source, confidence=confidence)
                return f"Updated {date_key} to: {merged}"
        db.profile_set(key, value, source=source, confidence=confidence)
        return f"Stored: {key} = {value}"

    # Date key -> check if duration key exists to infer year
    for dur_key, date_key in DURATION_TO_DATE.items():
        if key_lower == date_key and not re.search(r'\d{4}', value):
            existing_dur = db.profile_get(dur_key)
            if existing_dur:
                year = _infer_year_from_duration(existing_dur["value"])
                if year:
                    value = f"{value} {year}"
                    confidence = "inferred"
            break

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
    seen = {}
    for f in facts:
        seen[f['key']] = f['value']
    return "\n".join([f"{k}: {v}" for k, v in seen.items()])

def extract_and_store(conversation_text):
    """Run fact extraction on a conversation excerpt and store any new facts found."""
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
                store_fact(fact["key"], fact["value"],
                          confidence=fact.get("confidence", "high"))
                stored.append(f"{fact['key']} = {fact['value']}")
        return stored
    except Exception:
        return []

# Self-register
tools.register(
    name        = "store_user_fact",
    description = (
        "Store a fact you've learned about the user during conversation. "
        "Use this when the user shares personal information worth remembering: "
        "birthdays, family members, job, location, preferences, important dates, hobbies. "
        "For relative dates like 'today' or 'next Friday', pass them as-is — they will be resolved automatically."
    ),
    parameters  = {
        "type": "object",
        "properties": {
            "key":        {"type": "string", "description": "Short label e.g. birthday, wife_name, job_title, age"},
            "value":      {"type": "string", "description": "The fact value as spoken by the user"},
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
