#!/usr/bin/env python3
# csam_logger.py — privileged CSAM log writer (standalone server)
# Runs as root via systemd. Creates and owns /run/aura-csam.sock.
# Receives JSON log entries from aura.py and writes to a root-only log file.

import os
import sys
import json
import socket
import stat
import threading
import grp
from datetime import datetime

SOCKET_PATH = "/run/aura-csam.sock"
LOG_DIR     = "/var/log/aura/csam"
LOG_FILE    = os.path.join(LOG_DIR, "csam.log")


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.chmod(LOG_DIR, stat.S_IRWXU)            # 700 — root only


def _write_entry(entry: dict):
    _ensure_log_dir()
    entry["logged_at"] = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    os.chmod(LOG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600 — root only


def _handle_client(conn: socket.socket):
    buf = ""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    _write_entry(entry)
                except Exception as e:
                    print(f"[csam_logger] entry error: {e}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[csam_logger] client error: {e}", file=sys.stderr, flush=True)
    finally:
        conn.close()


def main():
    # Remove stale socket from a previous run
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)

    # Permissions: root owns socket; aura group can connect (660)
    try:
        aura_gid = grp.getgrnam("aura").gr_gid
        os.chown(SOCKET_PATH, 0, aura_gid)
    except KeyError:
        pass  # aura group doesn't exist yet — fall back to world-writable
    os.chmod(SOCKET_PATH, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)

    srv.listen(10)
    print(f"[csam_logger] listening on {SOCKET_PATH}", flush=True)

    while True:
        conn, _ = srv.accept()
        threading.Thread(
            target=_handle_client, args=(conn,), daemon=True
        ).start()


if __name__ == "__main__":
    main()
