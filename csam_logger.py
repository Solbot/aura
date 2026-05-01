#!/usr/bin/env python3
# csam_logger.py — privileged CSAM log writer
# Runs as root via systemd socket activation.
# Receives JSON log entries from aura.py via Unix socket.
# Writes to /var/log/aura/csam/csam.log with restricted permissions.

import sys
import os
import json
import stat
from datetime import datetime

LOG_DIR  = "/var/log/aura/csam"
LOG_FILE = os.path.join(LOG_DIR, "csam.log")


def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.chmod(LOG_DIR, stat.S_IRWXU)   # 700 — root only


def write_entry(entry: dict):
    ensure_log_dir()
    entry["logged_at"] = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    os.chmod(LOG_FILE, stat.S_IRUSR | stat.S_IWUSR)   # 600 — root only


def main():
    buf = ""
    for line in sys.stdin:
        buf += line
        try:
            entry = json.loads(buf.strip())
            write_entry(entry)
            buf = ""
        except json.JSONDecodeError:
            continue   # accumulate more lines until we have valid JSON


if __name__ == "__main__":
    main()
