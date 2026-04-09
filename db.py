import sqlite3
import os

DB_PATH = os.path.expanduser("~/aura/aura.db")

DEFAULTS = {
    "user_name":          ("Darren",              "User's full name",                    1),
    "user_informal_name": ("Dazz",                "What AURA calls the user day-to-day", 1),
    "assistant_name":     ("AURA",                "Assistant's name",                    1),
    "assistant_gender":   ("female",              "Assistant's presented gender",         1),
    "location":           ("AU",                  "User's country code",                 1),
    "tone_preference":    ("casual",              "Formal, casual, or somewhere between", 1),
    "use_case":           ("companion, work tool","Primary use cases",                   1),
    "personality_traits": ("witty, direct, honest","Personality descriptors",            1),
    "failure_mode":       ("ask",                 "ask = prompt user, auto = just do it",1),
    "auto_search":        ("0",                   "Auto search when answer unknown",     1),
    "voice_model":        ("en_US-amy-medium",    "Piper voice model name",              1),
    "voice_speed":        ("1.0",                 "TTS speed multiplier",                1),
    "home_pc_endpoint":   ("",                    "Home llama.cpp server URL",           1),
    "remote_api_endpoint":("",                    "Fallback remote API URL",             1),
    "first_boot_complete":("0",                   "Set to 1 after first boot completes", 0),
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

if __name__ == "__main__":
    init_db()
    print("Database initialised.")
    print(f"First boot: {is_first_boot()}")
