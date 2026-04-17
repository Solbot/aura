# tools/reminders.py
# Reminder tools — lets the LLM set, list, and cancel reminders.
# Includes a natural-language datetime parser so the LLM can pass
# expressions like "in 30 minutes", "tomorrow at 9am", "next Friday afternoon".

import re
import tools
import db
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Natural-language datetime parser
# ---------------------------------------------------------------------------

_TIME_NAMES = {
    'midnight':  (0,  0),
    'morning':   (9,  0),
    'noon':      (12, 0),
    'afternoon': (14, 0),
    'evening':   (18, 0),
    'night':     (20, 0),
}

_DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday',
              'friday', 'saturday', 'sunday']

_MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
    'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def _parse_time(token):
    """
    Parse a clock-time token into (hour, minute).
    Accepts: "3pm", "3:30pm", "15:00", "9am", or a named time.
    Raises ValueError on failure.
    """
    token = token.strip().lower()
    if token in _TIME_NAMES:
        return _TIME_NAMES[token]
    # HH:MM [am/pm]
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)?$', token)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        ap = m.group(3)
        if ap == 'pm' and h != 12:
            h += 12
        if ap == 'am' and h == 12:
            h = 0
        return h, mn
    # H [am/pm]
    m = re.match(r'^(\d{1,2})\s*(am|pm)$', token)
    if m:
        h = int(m.group(1))
        ap = m.group(2)
        if ap == 'pm' and h != 12:
            h += 12
        if ap == 'am' and h == 12:
            h = 0
        return h, 0
    raise ValueError(f"Cannot parse time: '{token}'")


def parse_when(when_str):
    """
    Parse a natural-language time expression into an absolute datetime.

    Supported forms (case-insensitive):
      - "in 30 minutes" / "in 2 hours" / "in 3 days" / "in 1 week"
      - "X minutes/hours/days/weeks from now"
      - "today at 3pm" / "tomorrow at 9am" / "tomorrow morning"
      - "monday at 10am" / "next friday at 2:30pm"
      - "april 20 at 9am" / "april 20 2026 at 9am"
      - "at 3pm" / "3pm" / "15:00"  (today; tomorrow if already past)
      - "morning" / "afternoon" / "evening" / "midnight" (today or tomorrow)
      - ISO: "2026-04-20 15:00" / "2026-04-20T15:00"

    Returns: datetime
    Raises:  ValueError if the expression can't be parsed.
    """
    s = when_str.strip().lower()
    now = datetime.now()

    # --- Relative: "in X unit[s]" or "X unit[s] from now" ---
    m = re.match(r'in\s+(\d+(?:\.\d+)?)\s+(second|minute|hour|day|week)s?$', s)
    if not m:
        m = re.match(r'(\d+(?:\.\d+)?)\s+(second|minute|hour|day|week)s?\s+from\s+now$', s)
    if m:
        amount = float(m.group(1))
        unit   = m.group(2)
        delta  = {
            'second': timedelta(seconds=amount),
            'minute': timedelta(minutes=amount),
            'hour':   timedelta(hours=amount),
            'day':    timedelta(days=amount),
            'week':   timedelta(weeks=amount),
        }[unit]
        return now + delta

    # --- today / tomorrow [at TIME] ---
    m = re.match(r'(today|tomorrow)\s+(?:at\s+)?(.+)$', s)
    if m:
        base = now if m.group(1) == 'today' else now + timedelta(days=1)
        h, mn = _parse_time(m.group(2))
        result = base.replace(hour=h, minute=mn, second=0, microsecond=0)
        if m.group(1) == 'today' and result <= now:
            result += timedelta(days=1)
        return result

    # --- [next] weekday [at TIME] ---
    for i, day in enumerate(_DAY_NAMES):
        m = re.match(rf'(?:next\s+)?{day}\s*(?:at\s+)?(.+)?$', s)
        if m:
            days_ahead = (i - now.weekday()) % 7 or 7
            target = now + timedelta(days=days_ahead)
            time_token = (m.group(1) or '').strip()
            h, mn = _parse_time(time_token) if time_token else (9, 0)
            return target.replace(hour=h, minute=mn, second=0, microsecond=0)

    # --- Month DD [YYYY] [at TIME] ---
    for mname, mnum in _MONTHS.items():
        m = re.match(
            rf'{mname}\s+(\d{{1,2}})(?:\s+(\d{{4}}))?\s*(?:at\s+)?(.+)?$', s
        )
        if m:
            day   = int(m.group(1))
            year  = int(m.group(2)) if m.group(2) else now.year
            time_token = (m.group(3) or '').strip()
            h, mn = _parse_time(time_token) if time_token else (9, 0)
            result = now.replace(year=year, month=mnum, day=day,
                                 hour=h, minute=mn, second=0, microsecond=0)
            if result <= now and not m.group(2):
                result = result.replace(year=year + 1)
            return result

    # --- ISO datetime: 2026-04-20[ T]HH:MM ---
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})[t\s](\d{2}):(\d{2})', s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        int(m.group(4)), int(m.group(5)))

    # --- bare time or named time: "at 3pm" / "3pm" / "afternoon" ---
    time_candidate = re.sub(r'^at\s+', '', s)
    try:
        h, mn = _parse_time(time_candidate)
        result = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result
    except ValueError:
        pass

    raise ValueError(f"Could not parse time expression: '{when_str}'")


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def set_reminder(message, when, repeat="none"):
    """
    Schedule a reminder.
    `when` is a natural-language time expression.
    `repeat` is one of: none, hourly, daily, weekly.
    """
    try:
        due_dt = parse_when(when)
    except ValueError as e:
        return f"Could not understand the time '{when}': {e}"

    due_iso = due_dt.isoformat()
    db.reminder_add(message, due_iso, repeat=repeat)

    # Human-readable confirmation
    friendly = due_dt.strftime("%A %B %d %Y at %I:%M %p").replace(" 0", " ")
    repeat_str = f" (repeating {repeat})" if repeat != "none" else ""
    return f"Reminder set: '{message}' on {friendly}{repeat_str}."


def cancel_reminder(reminder_id=None):
    """Cancel a reminder by ID, or all pending reminders if no ID given."""
    if reminder_id is not None:
        db.reminder_cancel(reminder_id)
        return f"Reminder {reminder_id} cancelled."
    pending = db.reminder_get_pending()
    if not pending:
        return "No pending reminders to cancel."
    db.reminder_cancel_all()
    return f"All {len(pending)} pending reminder(s) cancelled."


def list_reminders():
    """List all pending reminders."""
    pending = db.reminder_get_pending()
    if not pending:
        return "No pending reminders."
    lines = []
    for r in pending:
        try:
            due_dt   = datetime.fromisoformat(r['due_at'])
            due_str  = due_dt.strftime("%a %b %d at %I:%M %p")
        except Exception:
            due_str = r['due_at']
        repeat_str = f" [{r['repeat']}]" if r.get('repeat', 'none') != 'none' else ""
        lines.append(f"  id={r['id']}: '{r['message']}' — {due_str}{repeat_str}")
    return "Pending reminders:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-register
# ---------------------------------------------------------------------------

tools.register(
    name="set_reminder",
    description=(
        "Set a reminder that will fire at a specific time. "
        "The `when` parameter accepts natural-language expressions: "
        "'in 30 minutes', 'tomorrow at 9am', 'next friday afternoon', "
        "'april 20 at 10am', 'at 3pm', 'morning'. "
        "Optionally repeat: none (default), hourly, daily, weekly."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The reminder text to deliver to the user."
            },
            "when": {
                "type": "string",
                "description": (
                    "When to fire the reminder. Natural language accepted: "
                    "'in 30 minutes', 'tomorrow at 9am', 'next friday at 2pm', "
                    "'april 20 at 10am', 'at 3pm'."
                )
            },
            "repeat": {
                "type": "string",
                "description": "Repeat cadence: none, hourly, daily, weekly.",
                "enum": ["none", "hourly", "daily", "weekly"]
            }
        },
        "required": ["message", "when"]
    },
    function=lambda message, when, repeat="none": set_reminder(message, when, repeat),
    permission=tools.FREE
)

tools.register(
    name="cancel_reminder",
    description=(
        "Cancel a pending reminder by its ID, or cancel ALL pending reminders if no ID given. "
        "Use list_reminders to find IDs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "reminder_id": {
                "type": "integer",
                "description": "ID of the reminder to cancel. Omit to cancel all."
            }
        },
        "required": []
    },
    function=lambda reminder_id=None: cancel_reminder(reminder_id),
    permission=tools.FREE
)

tools.register(
    name="list_reminders",
    description="List all pending reminders with their IDs and scheduled times.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    function=lambda: list_reminders(),
    permission=tools.FREE
)
