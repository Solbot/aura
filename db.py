# db.py
# Database layer for Aether.
# Manages: config, user_profile, reminders, conversation_summaries (warm), conversation_archive (cold)

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.expanduser("~/aura/aura.db")

DEFAULTS = {
    "assistant_name":         ("Aether",               "Name of the assistant",                   1),
    "assistant_gender":       ("female",               "Gender of the assistant",                 1),
    "user_name":              ("",                     "Full name of the user",                   1),
    "user_informal_name":     ("",                     "Informal name/nickname of the user",      1),
    "location":               ("",                     "User's location",                         1),
    "tone_preference":        ("warm and direct",      "Preferred conversation tone",             1),
    "use_case":               ("general assistance",   "Primary use case",                        1),
    "personality_traits":     ("intelligent, witty, direct and honest", "Personality traits",    1),
    "failure_mode":           ("gentle",               "How to handle user frustration",          1),
    "auto_search":            ("0",                    "Auto web search when connected",          1),
    "voice_model":            ("en_US-amy-medium",     "Piper TTS voice model",                   1),
    "voice_speed":            ("1.0",                  "TTS playback speed multiplier",           1),
    "home_pc_endpoint":       ("",                     "Home PC llama.cpp server endpoint",       1),
    "remote_api_endpoint":    ("",                     "Remote API endpoint",                     1),
    "first_boot_complete":    ("0",                    "Set to 1 after first boot completes",     0),
    "last_interaction":       ("",                     "Timestamp of last user interaction",      0),
    "dream_pending":          ("0",                    "1 if new interactions since last dream",  0),
    "dream_delay":            ("10",                   "Minutes of silence before dream cycle",   1),
    "quiet_hours_start":      ("22:00",                "Start of quiet hours (HH:MM)",            1),
    "quiet_hours_end":        ("07:00",                "End of quiet hours (HH:MM)",              1),
    "awareness_interval":     ("5",                    "Awareness check interval in minutes",     1),
    "critical_temp_threshold":("80",                   "CPU temp threshold for warnings (C)",     1),
    "audio_enabled":          ("1",                    "TTS audio output enabled (1=on, 0=off)",  1),
}

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        # Config table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                description TEXT,
                user_editable INTEGER DEFAULT 1
            )
        """)
        # Insert defaults if not present
        for key, (value, description, user_editable) in DEFAULTS.items():
            conn.execute("""
                INSERT OR IGNORE INTO config (key, value, description, user_editable)
                VALUES (?, ?, ?, ?)
            """, (key, value, description, user_editable))

        # User profile table
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

        # Reminders table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message    TEXT NOT NULL,
                due_at     TEXT NOT NULL,
                repeat     TEXT DEFAULT 'none',
                fired      INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        # Warm memory: conversation summaries
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                summary       TEXT NOT NULL,
                message_count INTEGER DEFAULT 0,
                session_id    TEXT,
                created_at    TEXT NOT NULL
            )
        """)

        # Cold memory: raw conversation archive
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_archive (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                session_id TEXT,
                timestamp  TEXT NOT NULL
            )
        """)

        conn.commit()

# --- Config ---

def get(key):
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

def set(key, value):
    with get_connection() as conn:
        conn.execute("UPDATE config SET value = ? WHERE key = ?", (value, key))
        conn.commit()

def is_first_boot():
    return get("first_boot_complete") != "1"

def set_first_boot_complete():
    set("first_boot_complete", "1")

# --- Dream scheduling ---

def touch_interaction():
    """Update last_interaction timestamp and set dream_pending."""
    with get_connection() as conn:
        now = datetime.now().isoformat()
        conn.execute("UPDATE config SET value = ? WHERE key = 'last_interaction'", (now,))
        conn.execute("UPDATE config SET value = '1' WHERE key = 'dream_pending'")
        conn.commit()

def dream_complete():
    """Mark dream as done — clear dream_pending."""
    with get_connection() as conn:
        conn.execute("UPDATE config SET value = '0' WHERE key = 'dream_pending'")
        conn.commit()

def should_dream():
    """Return True if dream_pending and silence >= dream_delay minutes."""
    if get("dream_pending") != "1":
        return False
    last = get("last_interaction")
    if not last:
        return False
    try:
        delay   = float(get("dream_delay") or "10")
        elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
        return elapsed >= delay
    except Exception:
        return False

# --- User profile ---

def profile_set(key, value, source="conversation", confidence="high"):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO user_profile (key, value, learned_at, source, confidence)
            VALUES (?, ?, ?, ?, ?)
        """, (key, value, datetime.now().isoformat(), source, confidence))
        conn.commit()

def profile_get(key):
    """Get most recent value for a key, preferring dream-sourced entries."""
    with get_connection() as conn:
        # Prefer dream source
        row = conn.execute("""
            SELECT key, value, learned_at, source, confidence
            FROM user_profile WHERE key = ? AND source = 'dream'
            ORDER BY learned_at DESC LIMIT 1
        """, (key,)).fetchone()
        if not row:
            row = conn.execute("""
                SELECT key, value, learned_at, source, confidence
                FROM user_profile WHERE key = ?
                ORDER BY learned_at DESC LIMIT 1
            """, (key,)).fetchone()
        return dict(row) if row else None

def profile_get_all():
    """Get all profile entries ordered by key and recency."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT key, value, learned_at, source, confidence
            FROM user_profile
            ORDER BY learned_at ASC
        """).fetchall()
        return [dict(r) for r in rows]

def profile_get_upcoming_dates(days_ahead=7):
    """Return profile entries that look like dates coming up within days_ahead days."""
    import re
    results = []
    try:
        facts = profile_get_all()
        seen  = {}
        for f in facts:
            seen[f['key']] = f
        today = datetime.now()
        for key, fact in seen.items():
            val = fact['value']
            m = re.match(r'([A-Za-z]+)\s+(\d{1,2})(?:\s+\d{4})?', val)
            if m:
                try:
                    month_str, day_str = m.group(1), m.group(2)
                    event = datetime.strptime(f"{month_str} {day_str} {today.year}", "%B %d %Y")
                    if event.date() < today.date():
                        event = event.replace(year=today.year + 1)
                    days_until = (event.date() - today.date()).days
                    if 0 <= days_until <= days_ahead:
                        results.append({"key": key, "value": val, "days_until": days_until})
                except Exception:
                    pass
    except Exception:
        pass
    return results

# --- Reminders ---

def reminder_add(message, due_at, repeat="none"):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO reminders (message, due_at, repeat, fired, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (message, due_at, repeat, datetime.now().isoformat()))
        conn.commit()

def reminder_get_due():
    with get_connection() as conn:
        now  = datetime.now().isoformat()
        rows = conn.execute("""
            SELECT * FROM reminders WHERE due_at <= ? AND fired = 0
        """, (now,)).fetchall()
        return [dict(r) for r in rows]

def reminder_mark_fired(reminder_id):
    with get_connection() as conn:
        conn.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))
        conn.commit()

# --- Warm memory (conversation summaries) ---

def warm_append(summary, message_count=0, session_id=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO conversation_summaries (summary, message_count, session_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (summary, message_count, session_id, datetime.now().isoformat()))
        conn.commit()

def warm_get_recent(limit=5):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, summary, message_count, session_id, created_at
            FROM conversation_summaries
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

def warm_get_all():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, summary, message_count, created_at
            FROM conversation_summaries
            ORDER BY created_at ASC
        """).fetchall()
        return [dict(r) for r in rows]

# --- Cold memory (raw archive) ---

def cold_append(role, content, session_id=None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO conversation_archive (role, content, session_id, timestamp)
            VALUES (?, ?, ?, ?)
        """, (role, content, session_id, datetime.now().isoformat()))
        conn.commit()

def cold_get_session(session_id):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT role, content, timestamp FROM conversation_archive
            WHERE session_id = ? ORDER BY timestamp ASC
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]

if __name__ == "__main__":
    init_db()
    print("Database initialised.")
    print(f"Config keys: {len(DEFAULTS)}")
