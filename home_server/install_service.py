#!/usr/bin/env python3
"""
install_service.py — Install AURA Home LLM Server as a system service

Installs the server to run automatically at login/boot on Linux, macOS,
or Windows.  Uses the current Python interpreter and the server script
in the same directory as this file.

Usage:
    python install_service.py [install | uninstall | status]

Supported platforms
-------------------
Linux   — systemd user service  (~/.config/systemd/user/)
macOS   — launchd user agent    (~/Library/LaunchAgents/)
Windows — Task Scheduler        (runs at login, hidden window)

Notes
-----
- The service runs aura_llm_server.py headlessly (no tray icon).
- If you prefer tray control, use aura_tray.py instead — it manages
  the server as a subprocess and does not need a system service.
- To also auto-start the tray, see the platform-specific notes printed
  after a successful install.
"""

import os
import sys
import platform
import subprocess
import textwrap
from pathlib import Path

SCRIPT_DIR    = Path(__file__).parent.resolve()
SERVER_SCRIPT = SCRIPT_DIR / "aura_llm_server.py"
PYTHON_EXE    = sys.executable
SYSTEM        = platform.system()

SERVICE_NAME  = "aura-home-server"       # Linux systemd
PLIST_LABEL   = "com.aura.homeserver"    # macOS launchd
TASK_NAME     = "AURA Home Server"       # Windows Task Scheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list, *, check: bool = True) -> "subprocess.CompletedProcess":
    try:
        result = subprocess.run(cmd, check=False)
        if check and result.returncode != 0:
            print(f"  Warning: command returned {result.returncode}")
        return result
    except FileNotFoundError:
        print(f"  Command not found: {cmd[0]}")
        class _Stub:
            returncode = 127
        return _Stub()  # type: ignore[return-value]


def _banner(text: str) -> None:
    print()
    print("─" * 60)
    print(f"  {text}")
    print("─" * 60)

# ---------------------------------------------------------------------------
# Linux — systemd user service
# ---------------------------------------------------------------------------

_SYSTEMD_UNIT = """\
[Unit]
Description=AURA Home LLM Server
After=network.target

[Service]
Type=simple
ExecStart={python} {server}
WorkingDirectory={workdir}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def _linux_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def linux_install() -> None:
    _banner("Installing systemd user service")

    path = _linux_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _SYSTEMD_UNIT.format(
            python=PYTHON_EXE,
            server=SERVER_SCRIPT,
            workdir=SCRIPT_DIR,
        ),
        encoding="utf-8",
    )
    print(f"  Unit file : {path}")

    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", SERVICE_NAME])

    print()
    print(f"  Service '{SERVICE_NAME}' is enabled and started.")
    print()
    print("  Useful commands:")
    print(f"    systemctl --user status  {SERVICE_NAME}")
    print(f"    systemctl --user stop    {SERVICE_NAME}")
    print(f"    systemctl --user restart {SERVICE_NAME}")
    print(f"    journalctl --user -u {SERVICE_NAME} -f")
    print()
    print("  To also auto-start the tray icon at desktop login, add this")
    print("  to ~/.config/autostart/aura-tray.desktop:")
    print()
    print("    [Desktop Entry]")
    print("    Type=Application")
    print("    Name=AURA Home Server Tray")
    print(f"    Exec={PYTHON_EXE} {SCRIPT_DIR / 'aura_tray.py'}")
    print("    Hidden=false")
    print("    X-GNOME-Autostart-enabled=true")
    print()
    print("  NOTE: if using the tray, stop the systemd service first to")
    print("  avoid running two server instances on the same port.")


def linux_uninstall() -> None:
    _banner("Removing systemd user service")
    _run(["systemctl", "--user", "stop",    SERVICE_NAME], check=False)
    _run(["systemctl", "--user", "disable", SERVICE_NAME], check=False)
    path = _linux_unit_path()
    if path.exists():
        path.unlink()
        print(f"  Removed: {path}")
    else:
        print("  Unit file not found — nothing to remove.")
    _run(["systemctl", "--user", "daemon-reload"])
    print(f"  Service '{SERVICE_NAME}' removed.")


def linux_status() -> None:
    _banner(f"Status: {SERVICE_NAME}")
    _run(["systemctl", "--user", "status", SERVICE_NAME], check=False)

# ---------------------------------------------------------------------------
# macOS — launchd user agent
# ---------------------------------------------------------------------------

_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{server}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def mac_install() -> None:
    _banner("Installing launchd user agent")

    log_dir  = Path.home() / "Library" / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "aura_home_server.log"

    plist_path = _mac_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        _PLIST.format(
            label=PLIST_LABEL,
            python=PYTHON_EXE,
            server=SERVER_SCRIPT,
            workdir=SCRIPT_DIR,
            log=log_file,
        ),
        encoding="utf-8",
    )
    print(f"  Plist  : {plist_path}")
    print(f"  Log    : {log_file}")

    _run(["launchctl", "load", "-w", str(plist_path)])

    print()
    print(f"  Agent '{PLIST_LABEL}' loaded and will start at login.")
    print()
    print("  Useful commands:")
    print(f"    launchctl unload {plist_path}   # stop + disable")
    print(f"    launchctl load   {plist_path}   # re-enable")
    print(f"    tail -f {log_file}")
    print()
    print("  To auto-start the tray icon at login, add aura_tray.py to")
    print("  System Settings → General → Login Items.")
    print()
    print("  NOTE: if using the tray, unload the launchd agent first to")
    print("  avoid running two server instances on the same port.")


def mac_uninstall() -> None:
    _banner("Removing launchd user agent")
    plist_path = _mac_plist_path()
    if plist_path.exists():
        _run(["launchctl", "unload", "-w", str(plist_path)], check=False)
        plist_path.unlink()
        print(f"  Removed: {plist_path}")
    else:
        print("  Plist not found — nothing to remove.")


def mac_status() -> None:
    _banner(f"Status: {PLIST_LABEL}")
    _run(["launchctl", "list", PLIST_LABEL], check=False)

# ---------------------------------------------------------------------------
# Windows — Task Scheduler
# ---------------------------------------------------------------------------

# UTF-16 because schtasks /XML requires it on most Windows versions
_TASK_XML = """\
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2"
      xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>"{server}"</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
  </Settings>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
</Task>
"""


def windows_install() -> None:
    _banner("Installing Windows Task Scheduler task")

    xml_path = SCRIPT_DIR / "_aura_task_tmp.xml"
    xml_path.write_text(
        _TASK_XML.format(
            python=PYTHON_EXE,
            server=SERVER_SCRIPT,
            workdir=SCRIPT_DIR,
        ),
        encoding="utf-16",
    )

    result = _run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
        check=False,
    )
    xml_path.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"  Task '{TASK_NAME}' created — will run at next login.")
        print()
        print("  To start now:")
        print(f'    schtasks /Run /TN "{TASK_NAME}"')
        print()
        print("  To stop:")
        print(f'    schtasks /End /TN "{TASK_NAME}"')
        print()
        print("  To auto-start the tray icon instead, add aura_tray.py to")
        print("  the Windows Startup folder:")
        print(r"    shell:startup  →  create a shortcut to aura_tray.py")
        print()
        print("  NOTE: if using the tray, delete this task first to")
        print("  avoid running two server instances on the same port.")
    else:
        print()
        print("  Task Scheduler returned an error.  If you see 'Access is")
        print("  denied', try running this script as Administrator.")


def windows_uninstall() -> None:
    _banner("Removing Windows Task Scheduler task")
    result = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
    if result.returncode == 0:
        print(f"  Task '{TASK_NAME}' deleted.")
    else:
        print(f"  Task '{TASK_NAME}' not found or could not be removed.")


def windows_status() -> None:
    _banner(f"Status: {TASK_NAME}")
    _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"], check=False)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "install").lower()

    if action not in ("install", "uninstall", "status"):
        print(__doc__)
        print(f"Unknown action '{action}'. Use: install | uninstall | status")
        sys.exit(1)

    if SYSTEM == "Linux":
        fns = dict(install=linux_install, uninstall=linux_uninstall, status=linux_status)
    elif SYSTEM == "Darwin":
        fns = dict(install=mac_install, uninstall=mac_uninstall, status=mac_status)
    elif SYSTEM == "Windows":
        fns = dict(install=windows_install, uninstall=windows_uninstall, status=windows_status)
    else:
        print(f"Unsupported platform: {SYSTEM}")
        sys.exit(1)

    fns[action]()


if __name__ == "__main__":
    main()
