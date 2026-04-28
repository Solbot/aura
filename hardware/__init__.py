# hardware/__init__.py
# Hardware device registry for AURA.
# Devices self-register at import time via register().
#
# Each device object must expose:
#   device_id : str
#   name      : str
#   is_available() -> bool
#   get_state()    -> dict
#
# Public API:
#   register(device_id, device)
#   get(device_id)       -> device | None
#   get_all()            -> {device_id: device}
#   get_available()      -> {device_id: device}
#   load_all()           — imports all hardware modules to trigger registration

import threading

_registry = {}
_lock     = threading.Lock()


def register(device_id, device):
    """Register a hardware device. Called by each hardware module at import time."""
    with _lock:
        _registry[device_id] = device


def get(device_id):
    """Return a registered device by id, or None."""
    with _lock:
        return _registry.get(device_id)


def get_all():
    """Return all registered devices."""
    with _lock:
        return dict(_registry)


def get_available():
    """Return only devices that are currently available."""
    with _lock:
        snapshot = dict(_registry)
    return {k: v for k, v in snapshot.items() if v.is_available()}


def load_all():
    """Import all hardware modules so they self-register."""
    from hardware import pisugar3  # noqa: F401
