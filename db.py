# db.py
# Database layer for AURA.
# Manages: config, user_profile, reminders, conversation_summaries (warm), conversation_archive (cold)

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.expanduser("~/aura/aura.db")

DEFAULTS = {
    "assistant_name":         ("Aura",                 "Name of the assistant",                   1),
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
    "battery_warning_threshold":  ("20",              "Battery % for low-battery warning",       1),
    "battery_critical_threshold": ("10",              "Battery % for critical alert",            1),
    "pisugar3_socket":            ("",                "PiSugar 3 server socket path (empty=default)", 1),
    "audio_enabled":          ("1",                    "TTS audio output enabled (1=on, 0=off)",  1),
    "debug_tools":            ("0",                    "Print tool call args and results to console (1=on)", 1),
    "stt_enabled":            ("1",                    "STT voice input enabled (1=on, 0=off)",   1),
    "stt_microphone":         ("",                     "STT default microphone device name (empty=first available)", 1),
    "tts_speaker":            ("",                     "TTS output device name; empty = system default",            1),
    "audio_fallback_speaker": ("",                     "Fallback output device if primary unavailable",             1),
    "stt_model":              ("tiny",                 "Whisper model size: tiny/base/small",     1),
    "vosk_model_path":        ("/home/aura/models/vosk/small-en-us", "Path to Vosk model directory for wake word detection", 1),
    "wake_prefix":            ("",                     "Wake word prefix (e.g. 'hey', 'ok'); empty = hey and ok both active", 1),
    "clock_format":           ("24",                   "Clock display format: 24 or 12 hour",     1),
    "theme":                  ("dark",                 "UI colour theme: dark or light",           1),
    "privacy_mode":           ("0",                    "Privacy mode — STT disabled until toggled off", 0),
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

        # Scheduled tasks: recurring LLM-executed actions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                description      TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL,
                next_due         TEXT NOT NULL,
                active           INTEGER DEFAULT 1,
                created_at       TEXT NOT NULL
            )
        """)

        # Web search result cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_searches (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                query        TEXT NOT NULL,
                results_json TEXT NOT NULL,
                searched_at  TEXT NOT NULL
            )
        """)

        # Fetched page content cache
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_cache (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                url        TEXT NOT NULL,
                title      TEXT,
                content    TEXT,
                fetched_at TEXT NOT NULL
            )
        """)

        # Notes: freeform items with optional list items
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                body       TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS note_items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id    INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                position   INTEGER NOT NULL DEFAULT 0,
                text       TEXT NOT NULL,
                checked    INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        # Knowledge base: imported documents and their text chunks
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_docs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                filename      TEXT NOT NULL,
                original_path TEXT,
                chunk_count   INTEGER DEFAULT 0,
                added_at      TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id      INTEGER NOT NULL REFERENCES knowledge_docs(id),
                chunk_index INTEGER NOT NULL,
                content     TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
            USING fts5(content, content='knowledge_chunks', content_rowid='id')
        """)

        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS kc_ai AFTER INSERT ON knowledge_chunks BEGIN
                INSERT INTO knowledge_chunks_fts(rowid, content) VALUES (new.id, new.content);
            END
        """)

        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS kc_ad AFTER DELETE ON knowledge_chunks BEGIN
                INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            END
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

def reminder_cancel(reminder_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()

def reminder_cancel_all():
    with get_connection() as conn:
        conn.execute("DELETE FROM reminders WHERE fired = 0")
        conn.commit()

def reminder_get_pending():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM reminders WHERE fired = 0 ORDER BY due_at ASC
        """).fetchall()
        return [dict(r) for r in rows]

def reminder_reschedule(reminder_id, repeat):
    """Advance a repeating reminder to its next occurrence."""
    from datetime import timedelta
    delta = {
        'hourly': timedelta(hours=1),
        'daily':  timedelta(days=1),
        'weekly': timedelta(weeks=1),
    }.get(repeat)
    if not delta:
        reminder_mark_fired(reminder_id)
        return
    next_due = (datetime.now() + delta).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE reminders SET due_at = ?, fired = 0 WHERE id = ?",
            (next_due, reminder_id)
        )
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

_STOP_WORDS = {
    "the","and","for","are","was","that","this","with","have","from",
    "they","will","been","were","their","what","when","your","can",
    "had","her","his","him","she","but","not","you","all","who","did",
    "how","its","our","out","one","get","use","has","him","more","also",
}

def _keywords(text, min_len=4):
    return [w for w in text.lower().split() if len(w) >= min_len and w not in _STOP_WORDS]

def warm_search(query_text, limit=3):
    """Return warm summaries relevant to query_text via keyword matching."""
    words = _keywords(query_text)
    if not words:
        return warm_get_recent(limit)
    with get_connection() as conn:
        conditions = " OR ".join(["LOWER(summary) LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words] + [limit]
        rows = conn.execute(f"""
            SELECT id, summary, message_count, created_at
            FROM conversation_summaries
            WHERE {conditions}
            ORDER BY created_at DESC LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]

def warm_clear():
    """Delete all warm summaries (called after dream consolidates them into profile)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM conversation_summaries")
        conn.commit()

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

def cold_search(query_text, limit=5):
    """Return archived messages relevant to query_text via keyword matching."""
    words = _keywords(query_text)
    if not words:
        return []
    with get_connection() as conn:
        conditions = " OR ".join(["LOWER(content) LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words] + [limit]
        rows = conn.execute(f"""
            SELECT role, content, timestamp FROM conversation_archive
            WHERE {conditions}
            ORDER BY timestamp DESC LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]

# --- Scheduled tasks ---

def task_add(description, interval_seconds):
    from datetime import timedelta
    next_due = (datetime.now() + timedelta(seconds=interval_seconds)).isoformat()
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO scheduled_tasks (description, interval_seconds, next_due, active, created_at)
            VALUES (?, ?, ?, 1, ?)
        """, (description, interval_seconds, next_due, datetime.now().isoformat()))
        conn.commit()
        return cur.lastrowid

def task_get_due():
    with get_connection() as conn:
        now = datetime.now().isoformat()
        rows = conn.execute("""
            SELECT * FROM scheduled_tasks WHERE next_due <= ? AND active = 1
        """, (now,)).fetchall()
        return [dict(r) for r in rows]

def task_reschedule(task_id, interval_seconds):
    from datetime import timedelta
    next_due = (datetime.now() + timedelta(seconds=interval_seconds)).isoformat()
    with get_connection() as conn:
        conn.execute("UPDATE scheduled_tasks SET next_due = ? WHERE id = ?", (next_due, task_id))
        conn.commit()

def task_cancel(task_id):
    with get_connection() as conn:
        conn.execute("UPDATE scheduled_tasks SET active = 0 WHERE id = ?", (task_id,))
        conn.commit()

def task_cancel_all():
    with get_connection() as conn:
        conn.execute("UPDATE scheduled_tasks SET active = 0")
        conn.commit()

def task_get_active():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM scheduled_tasks WHERE active = 1
        """).fetchall()
        return [dict(r) for r in rows]


# --- Web search cache ---

def web_search_store(query, results):
    """Store a list of search result dicts for a query."""
    import json
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO web_searches (query, results_json, searched_at)
            VALUES (?, ?, ?)
        """, (query, json.dumps(results), datetime.now().isoformat()))
        conn.commit()

def web_search_get_recent(limit=5):
    """Return the most recent search queries and their results."""
    import json
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT query, results_json, searched_at FROM web_searches
            ORDER BY searched_at DESC LIMIT ?
        """, (limit,)).fetchall()
        out = []
        for r in rows:
            out.append({
                "query":       r["query"],
                "results":     json.loads(r["results_json"]),
                "searched_at": r["searched_at"],
            })
        return out

def web_cache_store(url, title, content):
    """Store fetched page content, replacing any existing entry for the same URL."""
    with get_connection() as conn:
        conn.execute("DELETE FROM web_cache WHERE url = ?", (url,))
        conn.execute("""
            INSERT INTO web_cache (url, title, content, fetched_at)
            VALUES (?, ?, ?, ?)
        """, (url, title, content, datetime.now().isoformat()))
        conn.commit()

def web_cache_get(url, max_age_hours=1):
    """Return cached page dict if fetched within max_age_hours, else None."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    with get_connection() as conn:
        row = conn.execute("""
            SELECT title, content, fetched_at FROM web_cache
            WHERE url = ? AND fetched_at > ?
            ORDER BY fetched_at DESC LIMIT 1
        """, (url, cutoff)).fetchone()
        return dict(row) if row else None


# --- Notes ---

def note_create(title, body=""):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, body, now, now)
        )
        conn.commit()
        return cur.lastrowid

def note_list():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM notes ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

def note_get(note_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return None
        note = dict(row)
        items = conn.execute(
            "SELECT * FROM note_items WHERE note_id = ? ORDER BY position ASC, id ASC",
            (note_id,)
        ).fetchall()
        note["items"] = [dict(i) for i in items]
        return note

def note_update(note_id, title=None, body=None):
    with get_connection() as conn:
        if title is not None:
            conn.execute("UPDATE notes SET title = ?, updated_at = ? WHERE id = ?",
                         (title, datetime.now().isoformat(), note_id))
        if body is not None:
            conn.execute("UPDATE notes SET body = ?, updated_at = ? WHERE id = ?",
                         (body, datetime.now().isoformat(), note_id))
        conn.commit()

def note_delete(note_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM note_items WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()

def note_item_add(note_id, text):
    with get_connection() as conn:
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM note_items WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO note_items (note_id, position, text, checked, created_at) VALUES (?, ?, ?, 0, ?)",
            (note_id, max_pos + 1, text, datetime.now().isoformat())
        )
        conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?",
                     (datetime.now().isoformat(), note_id))
        conn.commit()
        return cur.lastrowid

def note_item_update(item_id, text=None, checked=None):
    with get_connection() as conn:
        if text is not None:
            conn.execute("UPDATE note_items SET text = ? WHERE id = ?", (text, item_id))
        if checked is not None:
            conn.execute("UPDATE note_items SET checked = ? WHERE id = ?",
                         (1 if checked else 0, item_id))
        row = conn.execute("SELECT note_id FROM note_items WHERE id = ?", (item_id,)).fetchone()
        if row:
            conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?",
                         (datetime.now().isoformat(), row["note_id"]))
        conn.commit()

def note_item_delete(item_id):
    with get_connection() as conn:
        row = conn.execute("SELECT note_id FROM note_items WHERE id = ?", (item_id,)).fetchone()
        conn.execute("DELETE FROM note_items WHERE id = ?", (item_id,))
        if row:
            conn.execute("UPDATE notes SET updated_at = ? WHERE id = ?",
                         (datetime.now().isoformat(), row["note_id"]))
        conn.commit()


# --- Knowledge base ---

def knowledge_doc_add(filename, original_path, chunk_count):
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO knowledge_docs (filename, original_path, chunk_count, added_at)
            VALUES (?, ?, ?, ?)
        """, (filename, original_path, chunk_count, datetime.now().isoformat()))
        conn.commit()
        return cur.lastrowid

def knowledge_chunk_add(doc_id, chunk_index, content):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO knowledge_chunks (doc_id, chunk_index, content)
            VALUES (?, ?, ?)
        """, (doc_id, chunk_index, content))
        conn.commit()

def knowledge_search(query, limit=5):
    """Full-text search across all knowledge chunks. Returns list of dicts."""
    safe_query = " ".join(
        '"' + w.replace('"', '') + '"'
        for w in query.split()
        if w.replace('"', '').strip()
    )
    if not safe_query:
        return []
    with get_connection() as conn:
        try:
            rows = conn.execute("""
                SELECT kc.content, kd.filename, kc.chunk_index,
                       knowledge_chunks_fts.rank AS rank
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks kc ON knowledge_chunks_fts.rowid = kc.id
                JOIN knowledge_docs kd ON kc.doc_id = kd.id
                WHERE knowledge_chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (safe_query, limit)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

def knowledge_list_docs():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, filename, chunk_count, added_at
            FROM knowledge_docs
            ORDER BY added_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

def knowledge_delete_doc(doc_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM knowledge_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM knowledge_docs WHERE id = ?", (doc_id,))
        conn.commit()


if __name__ == "__main__":
    init_db()
    print("Database initialised.")
    print(f"Config keys: {len(DEFAULTS)}")
