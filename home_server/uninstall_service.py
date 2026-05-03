#!/usr/bin/env python3
"""
uninstall_service.py — Remove the AURA Home LLM Server system service

Stops and fully removes the service entry for the current platform:
    Linux   — disables and removes the systemd user service unit file
    macOS   — unloads and removes the launchd user agent plist
    Windows — deletes the Task Scheduler task

Usage:
    python uninstall_service.py
"""

import sys
from pathlib import Path

# Pull uninstall logic from the installer in the same directory
sys.path.insert(0, str(Path(__file__).parent))
from install_service import (
    SYSTEM,
    linux_uninstall,
    mac_uninstall,
    windows_uninstall,
)

_UNINSTALLERS = {
    "Linux":   linux_uninstall,
    "Darwin":  mac_uninstall,
    "Windows": windows_uninstall,
}


def main() -> None:
    fn = _UNINSTALLERS.get(SYSTEM)
    if fn is None:
        print(f"Unsupported platform: {SYSTEM}")
        sys.exit(1)
    fn()


if __name__ == "__main__":
    main()
