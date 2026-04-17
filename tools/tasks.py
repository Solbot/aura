# tools/tasks.py
# Scheduled task tools — lets the LLM schedule and cancel recurring background actions.

import tools
import db


def schedule_task(description, interval_seconds):
    """Register a recurring task the awareness system will execute via LLM."""
    task_id = db.task_add(description, interval_seconds)
    return (
        f"Task scheduled (id={task_id}): '{description}' "
        f"every {interval_seconds} seconds."
    )


def cancel_task(task_id=None):
    """Cancel a scheduled task by ID, or all active tasks if no ID given."""
    if task_id is not None:
        db.task_cancel(task_id)
        return f"Task {task_id} cancelled."
    active = db.task_get_active()
    if not active:
        return "No active scheduled tasks to cancel."
    db.task_cancel_all()
    return f"All {len(active)} active scheduled task(s) cancelled."


tools.register(
    name="schedule_task",
    description=(
        "Schedule a recurring background task. The awareness system will invoke you "
        "every interval_seconds seconds to execute the task. "
        "Use this whenever the user asks you to do something repeatedly at an interval — "
        "e.g. 'give me time updates every 10 seconds', 'check the temperature every minute'. "
        "Always tell the user what you've scheduled after calling this."
    ),
    parameters={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "What to do each time the task fires. "
                    "Write it as an instruction to yourself, e.g. "
                    "'Report the current time to the user' or "
                    "'Check CPU temperature and report it to the user'."
                )
            },
            "interval_seconds": {
                "type": "integer",
                "description": "How often to execute the task, in seconds."
            }
        },
        "required": ["description", "interval_seconds"]
    },
    function=schedule_task,
    permission=tools.FREE
)

tools.register(
    name="cancel_task",
    description=(
        "Cancel a scheduled recurring task by its ID, or cancel ALL active tasks if no ID given. "
        "Use this when the user says 'stop', 'cancel', or asks you to end recurring updates."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "ID of the task to cancel. Omit to cancel all active tasks."
            }
        },
        "required": []
    },
    function=cancel_task,
    permission=tools.FREE
)
