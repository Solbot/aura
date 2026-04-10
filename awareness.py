# awareness.py
# Background awareness thread.
# Handles: reminders (always autonomous), thermal/hardware alerts (always autonomous),
# date-based events (queued to hot memory), periodic time awareness.

import threading
import queue
import time
import db
from datetime import datetime, timedelta

# Message types
IMMEDIATE = "immediate"   # Speak now regardless of quiet hours
QUEUED    = "queued"      # Inject into hot memory at next interaction

# Global queues — aura.py reads from these
immediate_queue = queue.Queue()
hot_memory_queue = queue.Queue()

_stop_event = threading.Event()
_last_date = None   # Track date changes mid-session

def _is_quiet_hours():
    try:
        now = datetime.now()
        start_str = db.get("quiet_hours_start") or "22:00"
        end_str   = db.get("quiet_hours_end")   or "07:00"
        start_h, start_m = map(int, start_str.split(":"))
        end_h,   end_m   = map(int, end_str.split(":"))
        start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end   = now.replace(hour=end_h,   minute=end_m,   second=0, microsecond=0)
        # Handle overnight quiet hours (e.g. 22:00 to 07:00)
        if start > end:
            return now >= start or now <= end
        return start <= now <= end
    except Exception:
        return False

def _check_temperature():
    try:
        threshold = float(db.get("critical_temp_threshold") or "80")
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = int(f.read().strip()) / 1000
        if temp >= threshold:
            immediate_queue.put({
                "type":    IMMEDIATE,
                "message": f"Warning — I'm running hot. My CPU temperature is {temp}°C. "
                           f"You may want to check my cooling.",
                "source":  "thermal"
            })
    except Exception:
        pass

def _check_reminders():
    try:
        due = db.reminder_get_due()
        for reminder in due:
            immediate_queue.put({
                "type":    IMMEDIATE,
                "message": f"Reminder: {reminder['message']}",
                "source":  "reminder",
                "id":      reminder["id"]
            })
            db.reminder_mark_fired(reminder["id"])
    except Exception:
        pass

def _check_date_events():
    global _last_date
    try:
        now  = datetime.now()
        today = now.date()

        # Check for midnight crossing
        if _last_date and _last_date != today:
            hot_memory_queue.put({
                "type":    QUEUED,
                "message": f"Note: The date has changed. It is now {now.strftime('%A %B %d %Y')}.",
                "source":  "date_change"
            })

        _last_date = today

        # Check for upcoming dates in user profile
        upcoming = db.profile_get_upcoming_dates(days_ahead=0)  # Today only
        for event in upcoming:
            if event["days_until"] == 0:
                key = event["key"]
                value = event["value"]
                if "birthday" in key.lower():
                    name = db.get("user_informal_name") or "there"
                    hot_memory_queue.put({
                        "type":    QUEUED,
                        "message": f"Note: Today is {name}'s birthday! ({value})",
                        "source":  "profile_date"
                    })
                else:
                    hot_memory_queue.put({
                        "type":    QUEUED,
                        "message": f"Note: Today is a significant date — {key}: {value}",
                        "source":  "profile_date"
                    })
    except Exception:
        pass

def _awareness_loop():
    global _last_date
    _last_date = datetime.now().date()

    while not _stop_event.is_set():
        try:
            interval = int(db.get("awareness_interval") or "5")
        except Exception:
            interval = 5

        # Always check reminders and temperature regardless of quiet hours
        _check_reminders()
        _check_temperature()

        # Only queue date events if not in quiet hours
        if not _is_quiet_hours():
            _check_date_events()

        # Wait for next check, but wake up every 30s to check reminders
        # (reminders need sub-interval precision)
        checks = max(1, interval * 2)  # interval minutes in 30s chunks
        for _ in range(checks):
            if _stop_event.is_set():
                break
            time.sleep(30)
            _check_reminders()   # Always check reminders every 30s

def start():
    """Start the background awareness thread."""
    _stop_event.clear()
    t = threading.Thread(target=_awareness_loop, daemon=True, name="awareness")
    t.start()
    return t

def stop():
    """Stop the background awareness thread."""
    _stop_event.set()

def get_hot_memory_note():
    """
    Return a combined note from all queued hot memory items.
    Called before each LLM interaction to inject awareness context.
    Returns None if nothing queued.
    """
    notes = []
    while not hot_memory_queue.empty():
        try:
            item = hot_memory_queue.get_nowait()
            notes.append(item["message"])
        except queue.Empty:
            break
    return "\n".join(notes) if notes else None

def get_immediate_message():
    """
    Return the next immediate message if one is queued.
    Returns None if nothing pending.
    """
    try:
        return immediate_queue.get_nowait()
    except queue.Empty:
        return None
