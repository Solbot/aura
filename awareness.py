# awareness.py
# Background awareness thread.
# Handles: reminders, scheduled tasks, thermal/hardware alerts, date-based events,
# dream cycle trigger.
#
# Reminders and scheduled tasks both fire via llm_check_queue so the LLM generates
# the delivery naturally.  Hardware alerts (thermal) use immediate_queue for
# instant, unconditional delivery without an LLM call.

import threading
import queue
import time
import db
import aura_socket
from datetime import datetime

# Message types
IMMEDIATE = "immediate"
QUEUED    = "queued"

# immediate_queue  — hardware alerts delivered as fixed strings (no LLM)
# hot_memory_queue — context notes injected into the next chat turn
# llm_check_queue  — prompts for the main loop to hand to the LLM
immediate_queue  = queue.Queue()
hot_memory_queue = queue.Queue()
llm_check_queue  = queue.Queue()

_stop_event    = threading.Event()
_last_date     = None
_dream_running = False
_aether_busy   = False  # Set True while the LLM is active
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
    """Thermal alerts go straight to immediate_queue — no LLM latency."""
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
    """
    For each due reminder: advance/expire it in the DB, then queue an LLM prompt
    so the LLM delivers the reminder naturally rather than as a raw string.
    """
    try:
        due = db.reminder_get_due()
        for reminder in due:
            repeat = reminder.get('repeat', 'none') or 'none'
            if repeat != 'none':
                db.reminder_reschedule(reminder['id'], repeat)
            else:
                db.reminder_mark_fired(reminder['id'])
            llm_check_queue.put(
                f"Your scheduled reminder is now due: \"{reminder['message']}\". "
                f"Deliver it to the user naturally and concisely."
            )
    except Exception:
        pass


def _check_scheduled_tasks():
    """
    For each due scheduled task: reschedule it, then queue an LLM prompt
    so the LLM executes the task and responds naturally.
    """
    try:
        due = db.task_get_due()
        for task in due:
            db.task_reschedule(task['id'], task['interval_seconds'])
            llm_check_queue.put(
                f"Execute your scheduled task now: \"{task['description']}\". "
                f"Respond naturally and concisely as if proactively messaging the user."
            )
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
                        "message": f"Note: Today is a significant date — {key}: {value}",
                        "source":  "profile_date"
                    })
    except Exception:
        pass


def _check_dream():
    """Trigger dream cycle only when idle and silence threshold reached."""
    global _dream_running
    if _dream_running:
        return
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

    _next_full_check = time.time()  # run full checks immediately on first tick

    while not _stop_event.is_set():
        now = time.time()

        # Every tick: reminders and tasks — each due item queues an LLM prompt
        _check_reminders()
        _check_scheduled_tasks()

        # Full checks at the configured interval
        try:
            interval_sec = int(db.get("awareness_interval") or "5") * 60
        except Exception:
            interval_sec = 300

        if now >= _next_full_check:
            _next_full_check = now + interval_sec

            _check_temperature()
            _check_dream()

            try:
                import psutil
                mem = psutil.virtual_memory()
                aura_socket.send_status("memory", str(int(mem.used / 1024 / 1024)))
            except Exception:
                pass

            try:
                temp = _get_cpu_temp()
                if temp is not None:
                    aura_socket.send_status("cpu_temp", f"{temp:.1f}")
            except Exception:
                pass

            if not _is_quiet_hours():
                _check_date_events()

        _stop_event.wait(timeout=10)


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


def get_pending_llm_check():
    """Return the next queued LLM prompt (reminder or task), or None."""
    try:
        return llm_check_queue.get_nowait()
    except queue.Empty:
        return None
