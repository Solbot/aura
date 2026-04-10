import sqlite3
import os

DB_PATH = os.path.expanduser("~/aura/aura.db")

DEFAULTS = {
    "user_name":              ("Darren",              "User's full name",                        1),
    "user_informal_name":     ("Dazz",                "What AURA calls the user day-to-day",     1),
    "assistant_name":         ("AURA",                "Assistant's name",                        1),
    "assistant_gender":       ("female",              "Assistant's presented gender",             1),
    "location":               ("AU",                  "User's country code",                     1),
    "tone_preference":        ("casual",              "Formal, casual, or somewhere between",    1),
    "use_case":               ("companion, work tool","Primary use cases",                       1),
    "personality_traits":     ("witty, direct, honest","Personality descriptors",               1),
    "failure_mode":           ("ask",                 "ask = prompt user, auto = just do it",    1),
    "auto_search":            ("0",                   "Auto search when answer unknown",         1),
    "voice_model":            ("en_US-amy-medium",    "Piper voice model name",                  1),
    "voice_speed":            ("1.0",                 "TTS speed multiplier",                    1),
    "home_pc_endpoint":       ("",                    "Home llama.cpp server URL",               1),
    "remote_api_endpoint":    ("",                    "Fallback remote API URL",                 1),
    "first_boot_complete":    ("0",                   "Set to 1 after first boot completes",     0),
    "quiet_hours_start":      ("22:00",               "Quiet hours start time (HH:MM)",          1),
    "quiet_hours_end":        ("07:00",               "Quiet hours end time (HH:MM)",            1),
    "awareness_interval":     ("5",                   "Background awareness check interval (mins)", 1),
    "critical_temp_threshold":("80",                  "CPU temp threshold for immediate alert (C)", 1),
}

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                description TEXT,
                editable    INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS violations_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                trigger_input   TEXT NOT NULL,
                conversation    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                learned_at  TEXT NOT NULL,
                source      TEXT DEFAULT 'conversation',
                confidence  TEXT DEFAULT 'high'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message     TEXT NOT NULL,
                due_at      TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                fired       INTEGER DEFAULT 0
            )
        """)
        for key, (value, description, editable) in DEFAULTS.items():
            conn.execute("""
                INSERT OR IGNORE INTO config (key, value, description, editable)
                VALUES (?, ?, ?, ?)
            """, (key, value, description, editable))
        conn.commit()

def get(key):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

def set(key, value):
    with get_connection() as conn:
        editable = conn.execute(
            "SELECT editable FROM config WHERE key = ?", (key,)
        ).fetchone()
        if editable and editable[0] == 0:
            raise PermissionError(f"Config key '{key}' is locked and cannot be changed.")
        conn.execute(
            "UPDATE config SET value = ? WHERE key = ?", (value, key)
        )
        conn.commit()

def is_first_boot():
    return get("first_boot_complete") == "0"

def complete_first_boot():
    with get_connection() as conn:
        conn.execute(
            "UPDATE config SET value = '1' WHERE key = 'first_boot_complete'"
        )
        conn.commit()

# --- User profile ---

def profile_set(key, value, source="conversation", confidence="high"):
    import re
    from datetime import datetime
    with get_connection() as conn:
        # Check if a more complete value already exists for this key
        # A value with a 4-digit year is more complete than one without
        existing = conn.execute("""
            SELECT value FROM user_profile WHERE key = ?
            ORDER BY learned_at DESC LIMIT 1
        """, (key,)).fetchone()
        if existing:
            existing_val = existing[0]
            existing_has_year = bool(re.search(r'\d{4}', existing_val))
            new_has_year = bool(re.search(r'\d{4}', str(value)))
            # If existing already has year and new value doesn't, skip
            if existing_has_year and not new_has_year:
                return
        conn.execute("""
            INSERT INTO user_profile (key, value, learned_at, source, confidence)
            VALUES (?, ?, ?, ?, ?)
        """, (key, value, datetime.now().isoformat(), source, confidence))
        conn.commit()

def profile_get(key):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT value, learned_at, confidence FROM user_profile
            WHERE key = ? ORDER BY learned_at DESC LIMIT 1
        """, (key,)).fetchone()
        return {"value": row[0], "learned_at": row[1], "confidence": row[2]} if row else None

def profile_get_all():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT key, value, learned_at, source, confidence
            FROM user_profile ORDER BY learned_at DESC
        """).fetchall()
        return [{"key": r[0], "value": r[1], "learned_at": r[2],
                 "source": r[3], "confidence": r[4]} for r in rows]

def profile_get_upcoming_dates(days_ahead=7):
    """Return profile entries that look like dates coming up within days_ahead."""
    from datetime import datetime, timedelta
    import re
    upcoming = []
    all_facts = profile_get_all()
    now = datetime.now()
    for fact in all_facts:
        # Look for month/day patterns like "April 10" or "10 April"
        match = re.search(
            r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
            r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|'
            r'Dec(?:ember)?)[\s]+(d{1,2})|(d{1,2})[\s]+'
            r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
            r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)',
            fact["value"], re.IGNORECASE
        )
        if match:
            try:
                date_str = match.group(0)
                # Try this year first, then next
                for year in [now.year, now.year + 1]:
                    candidate = datetime.strptime(f"{date_str} {year}", "%B %d %Y")
                    delta = (candidate - now).days
                    if 0 <= delta <= days_ahead:
                        upcoming.append({**fact, "days_until": delta, "date": candidate})
                        break
            except Exception:
                pass
    return upcoming

# --- Reminders ---

def reminder_add(message, due_at):
    from datetime import datetime
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO reminders (message, due_at, created_at)
            VALUES (?, ?, ?)
        """, (message, due_at, datetime.now().isoformat()))
        conn.commit()

def reminder_get_due():
    from datetime import datetime
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, message, due_at FROM reminders
            WHERE fired = 0 AND due_at <= ?
        """, (datetime.now().isoformat(),)).fetchall()
        return [{"id": r[0], "message": r[1], "due_at": r[2]} for r in rows]

def reminder_mark_fired(reminder_id):
    with get_connection() as conn:
        conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))
        conn.commit()

if __name__ == "__main__":
    init_db()
    print("Database initialised.")
    print(f"First boot: {is_first_boot()}")
