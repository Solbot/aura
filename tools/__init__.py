# tools/__init__.py
# Tool registry — discovers, registers and executes tools.
# Permission tiers:
#   FREE    — read-only, executes silently
#   CONFIRM — writes/launches, asks user unless they've disabled confirmation
#   LOCKED  — hardcoded refusal, cannot be unlocked

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
    confirm_fn: callable(prompt) -> bool — used for CONFIRM tier tools.
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
            prompt = f"Aether wants to run '{name}' with {arguments}. Allow? (yes/no): "
            if not confirm_fn(prompt):
                return False, "Cancelled by user."

    try:
        result = tool["function"](**arguments)
        return True, str(result)
    except Exception as e:
        return False, f"Tool error: {e}"

def load_all():
    """Import all tool modules so they self-register."""
    from tools import system_info  # noqa: F401
    # Add new tool imports here as they are created
