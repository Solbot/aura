# aura_socket.py
# Unix domain socket server for Aura IPC.
# Runs in a background thread, handles JSON messages from the UI process.
# Message protocol: newline-delimited JSON.
#
# Message types (UI → Aura):
#   {"type": "chat_input", "text": "...", "id": "..."}
#   {"type": "ui_response", "request_id": "...", "tiles": [...]}
#   {"type": "ping"}
#
# Message types (Aura → UI):
#   {"type": "chat_response", "text": "...", "id": "..."}
#   {"type": "status_update", "key": "...", "value": "..."}
#   {"type": "ui_command", "tool": "...", "args": {...}}
#   {"type": "ui_query", "filter": {...}, "request_id": "..."}
#   {"type": "tts_start"}   — mute STT wake detection while speaking
#   {"type": "tts_end"}     — unmute STT wake detection
#   {"type": "pong"}

import socket
import os
import json
import threading
import queue
import time

SOCKET_PATH = "/tmp/aura.sock"

# Thread-safe queues for message passing
# UI → Aura
incoming_queue = queue.Queue()
# Aura → UI
outgoing_queue = queue.Queue()
# Pending UI query responses (request_id → response event + data)
_pending_queries = {}
_pending_lock   = threading.Lock()

_clients     = []
_clients_lock = threading.Lock()
_server_thread = None
_running = False

def _handle_client(conn, addr):
    """Handle a single client connection."""
    buf = ""
    try:
        while _running:
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
                    msg = json.loads(line)
                    _dispatch_incoming(msg, conn)
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    finally:
        with _clients_lock:
            if conn in _clients:
                _clients.remove(conn)
        try:
            conn.close()
        except Exception:
            pass

def _dispatch_incoming(msg, conn):
    """Route incoming messages."""
    msg_type = msg.get("type")

    if msg_type == "ping":
        _send_to(conn, {"type": "pong"})
        return

    if msg_type == "ui_response":
        # Response to a ui_query we sent
        request_id = msg.get("request_id")
        if request_id:
            with _pending_lock:
                if request_id in _pending_queries:
                    _pending_queries[request_id]["data"] = msg
                    _pending_queries[request_id]["event"].set()
        return

    # Everything else goes to the incoming queue for aura.py to process
    incoming_queue.put(msg)

def _send_to(conn, msg):
    """Send a message to a specific client."""
    try:
        data = json.dumps(msg) + "\n"
        conn.sendall(data.encode("utf-8"))
    except Exception:
        pass

def _outgoing_worker():
    """Drain the outgoing queue and broadcast to all clients."""
    while _running:
        try:
            msg = outgoing_queue.get(timeout=0.1)
            with _clients_lock:
                clients = list(_clients)
            for conn in clients:
                _send_to(conn, msg)
        except queue.Empty:
            pass

def _server_loop():
    """Main server loop — accept connections."""
    global _running
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)
    srv.listen(5)
    srv.settimeout(1.0)

    # Start outgoing worker
    t = threading.Thread(target=_outgoing_worker, daemon=True, name="socket-out")
    t.start()

    while _running:
        try:
            conn, addr = srv.accept()
            with _clients_lock:
                _clients.append(conn)
            t = threading.Thread(target=_handle_client, args=(conn, addr),
                                 daemon=True, name="socket-client")
            t.start()
        except socket.timeout:
            pass
        except Exception:
            break

    srv.close()
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

def start():
    """Start the socket server in a background thread."""
    global _server_thread, _running
    _running = True
    _server_thread = threading.Thread(target=_server_loop, daemon=True, name="socket-server")
    _server_thread.start()

def stop():
    """Stop the socket server."""
    global _running
    _running = False

def send(msg):
    """Send a message to all connected UI clients."""
    outgoing_queue.put(msg)

def send_chat_response(text, msg_id=None):
    """Send a chat response to the UI."""
    send({"type": "chat_response", "text": text, "id": msg_id})

def send_status(key, value):
    """Send a status update to the UI."""
    send({"type": "status_update", "key": key, "value": value})

def send_system_message(text, level="info"):
    """Send a system/status message to all UI clients.
    level: 'info' | 'warning' | 'error'
    """
    send({"type": "system_message", "text": text, "level": level})

def query_ui(filter_dict=None, timeout=3.0):
    """
    Send a ui_query to the UI and wait for the response.
    Returns the tile list or None on timeout.
    """
    import uuid
    request_id = str(uuid.uuid4())[:8]
    event = threading.Event()
    with _pending_lock:
        _pending_queries[request_id] = {"event": event, "data": None}

    send({"type": "ui_query", "filter": filter_dict or {}, "request_id": request_id})

    event.wait(timeout=timeout)
    with _pending_lock:
        result = _pending_queries.pop(request_id, {}).get("data")
    return result

def ui_command(tool, args=None):
    """Send a UI command (fire and forget)."""
    send({"type": "ui_command", "tool": tool, "args": args or {}})

def wait_for_client(timeout=None):
    """Block until at least one UI client is connected, or timeout expires.
    Returns True if a client connected, False if timed out."""
    deadline = (time.monotonic() + timeout) if timeout is not None else None
    while True:
        with _clients_lock:
            if _clients:
                return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(0.2)

def get_incoming(block=False, timeout=0.1):
    """Get next incoming message from UI, or None."""
    try:
        return incoming_queue.get(block=block, timeout=timeout)
    except queue.Empty:
        return None
