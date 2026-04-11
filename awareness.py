# awareness.py
# Background awareness thread.
# Handles: reminders (always autonomous), thermal/hardware alerts (always autonomous),
# date-based events (queued to hot memory), dream cycle trigger.

import threading
import queue
import time
import db
import aura_socket
from datetime import datetime

# Message types
IMMEDIATE = "immediate"
QUEUED    = "queued"

# Global queues
immediate_queue = queue.Queue()
hot_memory_queue = queue.Queue()

_stop_event    = threading.Event()
_last_date     = None
_dream_running = False
_aether_busy   = False  # Set True while Aether is processing a message
_last_busy_end = 0.0    # Timestamp when last LLM call completed
DREAM_COOLDOWN = 10     # Seconds to wait after LLM finishes before dreaming

def set_busy(busy):
    """Called by aura.py to signal when LLM is active."""
    global _aether_busy, _last_busy_end
    _aether_busy = busy
    if not busy:
        _last_busy_end = time.time()

def _is_quiet_hours():
    try:
        now       = datetime.now()
        start_str = db.get("quiet_hours_start") or "22:00"
        end_str   = db.get("quiet_hours_end")   or "07:00"
        start_h, start_m = map(int, start_str.split(":"))
        end_h,   end_m   = map(int, end_str.split(":"))
        start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end   = now.replace(hour=end_h,   minute=end_m,   second=0, microsecond=0)
        if start > end:
            return now >= start or now <= end
        return start <= now <= end
    except Exception:
        return False

def _get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000
    except Exception:
        return None

def _check_temperature():
    try:
        threshold = float(db.get("critical_temp_threshold") or "80")
        temp = _get_cpu_temp()
        if temp is None:
            return
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
        now   = datetime.now()
        today = now.date()

        if _last_date and _last_date != today:
            hot_memory_queue.put({
                "type":    QUEUED,
                "message": f"Note: The date has changed. It is now {now.strftime('%A %B %d %Y')}.",
                "source":  "date_change"
            })

        _last_date = today

        upcoming = db.profile_get_upcoming_dates(days_ahead=0)
        for event in upcoming:
            if event["days_until"] == 0:
                key   = event["key"]
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
                        "message": f"Note: Today is a significant date Ã¢ÂÂ {key}: {value}",
                        "source":  "profile_date"
                    })
    except Exception:
        pass

def _check_dream():
    """Trigger dream cycle only when Aether is idle and silence threshold reached."""
    global _dream_running
    if _dream_running:
        return
    # Don't dream while Aether is active or still cooling down
    if _aether_busy:
        return
    if time.time() - _last_busy_end < DREAM_COOLDOWN:
        return
    try:
        if db.should_dream():
            _dream_running = True
            import dream
            endpoint = db.get('home_pc_endpoint') or "http://localhost:8080/v1/chat/completions"
            dream.dream(endpoint)
            db.dream_complete()
            _dream_running = False
    except Exception:
        _dream_running = False

def _awareness_loop():
    global _last_date
    _last_date = datetime.now().date()

    while not _stop_event.is_set():
        try:
            interval = int(db.get("awareness_interval") or "5")
        except Exception:
            interval = 5

        # Always check reminders and temperature
        _check_reminders()
        _check_temperature()

        # Push status to UI
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_used_mb = int(mem.used / 1024 / 1024)
            aura_socket.send_status("memory", str(mem_used_mb))
        except Exception:
            pass

        try:
            temp = _get_cpu_temp()
            if temp is not None:
                aura_socket.send_status("cpu_temp", f"{temp:.1f}")
        except Exception:
            pass

        _check_dream()

        if not _is_quiet_hours():
            _check_date_events()

        # Sleep in 30s chunks so reminders fire promptly
        checks = max(1, interval * 2)
        for _ in range(checks):
            if _stop_event.is_set():
                break
            time.sleep(30)
            _check_reminders()
            _check_dream()

def start():
    _stop_event.clear()
    t = threading.Thread(target=_awareness_loop, daemon=True, name="awareness")
    t.start()
    return t

def stop():
    _stop_event.set()

def get_hot_memory_note():
    notes = []
    while not hot_memory_queue.empty():
        try:
            item = hot_memory_queue.get_nowait()
            notes.append(item["message"])
        except queue.Empty:
            break
    return "\n".join(notes) if notes else None

def get_immediate_message():
    try:
        return immediate_queue.get_nowait()
    except queue.Empty:
        return None
