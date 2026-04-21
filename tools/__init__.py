# tools/__init__.py
# Tool registry â discovers, registers and executes tools.
# Permission tiers:
#   FREE    â read-only, executes silently
#   CONFIRM â writes/launches, asks user unless they've disabled confirmation
#   LOCKED  â hardcoded refusal, cannot be unlocked

import db

FREE    = "free"
CONFIRM = "confirm"
LOCKED  = "locked"

_registry = {}

def register(name, description, parameters, function, permission=FREE):
    """Register a tool. Called by each tool module at import time."""
    _registry[name] = {
        "name":        name,
        "description": description,
        "parameters":  parameters,
        "function":    function,
        "permission":  permission,
    }

def get_all():
    """Return all registered tools."""
    return list(_registry.values())

def get_definitions():
    """Return tool definitions in llama.cpp function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t["description"],
                "parameters":  t["parameters"],
            }
        }
        for t in _registry.values()
        if t["permission"] != LOCKED
    ]

def execute(name, arguments, confirm_fn=None):
    """
    Execute a tool by name with given arguments dict.
    confirm_fn: callable(prompt) -> bool â used for CONFIRM tier tools.
    Returns (success, result_string).
    """
    if name not in _registry:
        return False, f"Unknown tool: {name}"

    tool = _registry[name]

    if tool["permission"] == LOCKED:
        return False, "That operation is not permitted."

    if tool["permission"] == CONFIRM:
        # Check if user has disabled confirmation for this tool
        skip_confirm_key = f"tool_confirm_skip_{name}"
        skip = db.get(skip_confirm_key) == "1"
        if not skip and confirm_fn:
            prompt = f"{db.get('assistant_name')} wants to run '{name}' with {arguments}. Allow? (yes/no): "
            if not confirm_fn(prompt):
                return False, "Cancelled by user."

    try:
        result = tool["function"](**arguments)
        if db.get("debug_tools") == "1":
            print(f"[Tool] {name}({arguments}) → {str(result)[:200]}")
        return True, str(result)
    except Exception as e:
        if db.get("debug_tools") == "1":
            print(f"[Tool] {name}({arguments}) → ERROR: {e}")
        return False, f"Tool error: {e}"

def load_all():
    """Import all tool modules so they self-register."""
    from tools import system_info   # noqa: F401
    from tools import user_profile  # noqa: F401
    from tools import tasks         # noqa: F401
    from tools import reminders     # noqa: F401
    from tools import web_search    # noqa: F401
    from tools import notes         # noqa: F401
