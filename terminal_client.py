#!/usr/bin/env python3
# terminal_client.py
# Thin terminal client for Aura — connects to /tmp/aura.sock

import socket
import json
import threading
import sys

SOCKET_PATH = "/tmp/aura.sock"

_sock    = None
_running = True
_msg_id  = 0

LEVEL_PREFIX = {
    "info":    "[INFO]",
    "warning": "[WARN]",
    "error":   "[ERROR]",
}

def _receive_loop(sock):
    buf = ""
    while _running:
        try:
            data = sock.recv(4096)
            if not data:
                print("\n[Aura disconnected]")
                break
            buf += data.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")
                if msg_type == "chat_response":
                    text = msg.get("text", "")
                    print(f"\nAura: {text}\nYou: ", end="", flush=True)
                elif msg_type == "system_message":
                    text  = msg.get("text", "")
                    level = msg.get("level", "info")
                    prefix = LEVEL_PREFIX.get(level, "[INFO]")
                    print(f"\n{prefix} {text}\nYou: ", end="", flush=True)
                # ignore pong and other types
        except socket.timeout:
            continue
        except Exception:
            break

def _send(sock, msg):
    try:
        sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
    except Exception as e:
        print(f"\n[Send error: {e}]")

def main():
    global _sock, _running, _msg_id

    try:
        _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _sock.connect(SOCKET_PATH)
        _sock.settimeout(0.5)
    except Exception as e:
        print(f"[Could not connect to Aura at {SOCKET_PATH}: {e}]")
        sys.exit(1)

    recv_thread = threading.Thread(target=_receive_loop, args=(_sock,), daemon=True)
    recv_thread.start()

    print("Connected to Aura. Type 'quit' or 'exit' to disconnect.\n")
    print("You: ", end="", flush=True)

    try:
        while True:
            try:
                line = input()
            except EOFError:
                break

            if line.strip().lower() in ("quit", "exit"):
                break

            if not line.strip():
                print("You: ", end="", flush=True)
                continue

            _msg_id += 1
            _send(_sock, {"type": "chat_input", "text": line, "id": str(_msg_id)})
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        try:
            _sock.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
