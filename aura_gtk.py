#!/usr/bin/env python3
"""AURA GTK4 interface — connects to /tmp/aura.sock"""

import sys
import os
import glob

# gi (PyGObject) lives in the system Python; STT packages live in the venv.
# Inject the venv site-packages so both are available under system python3.
for _sp in glob.glob(
    os.path.expanduser("~/aura/venv/lib/python3*/site-packages")
):
    if _sp not in sys.path:
        sys.path.insert(0, _sp)
import json
import socket
import sqlite3
import threading
import queue
from datetime import datetime

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk, Pango, Gio

try:
    import stt as _stt
    _STT_AVAILABLE = _stt.available()
except Exception:
    _STT_AVAILABLE = False

SOCKET_PATH = "/tmp/aura.sock"
DB_PATH     = os.path.expanduser("~/aura/aura.db")

CSS = b"""
window {
    background-color: #14141e;
}
.header {
    background-color: #0f0f19;
    padding: 8px 14px;
    border-bottom: 1px solid #252538;
}
.chat-scroll {
    background-color: #14141e;
}
.chat-box {
    background-color: #14141e;
}
.input-bar {
    background-color: #0f0f19;
    padding: 8px;
    border-top: 1px solid #252538;
}
.input-frame {
    border: 1px solid #3a3a58;
    border-radius: 6px;
    background-color: #1a1a2e;
}
.input-frame:focus-within {
    border-color: #3380e8;
}
textview {
    background-color: #1a1a2e;
    color: #f0f0f0;
    font-size: 15px;
    padding: 8px 12px;
}
textview text {
    background-color: #1a1a2e;
    color: #f0f0f0;
}
button.send-btn {
    background-color: #3380e8;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 22px;
    font-weight: bold;
    font-size: 14px;
}
button.send-btn:hover {
    background-color: #4490f8;
}
label {
    color: #f0f0f0;
}
.title-lbl {
    color: #1ab380;
    font-size: 15px;
}
.connecting  { color: #e69910; }
.connected   { color: #1ab380; }
.disconnected{ color: #e63333; }
.dim         { color: #8888aa; }
.warn        { color: #e69910; }
.err         { color: #e63333; }
.msg-user    { color: #d0d0ff; font-size: 14px; }
.msg-aura    { color: #f0f0f0; font-size: 14px; }
.status-msg  { color: #6868a0; font-size: 12px; font-style: italic; }
.stt-icon    { color: #8888aa; font-size: 13px; }
.stt-icon.listening { color: #1ab380; }
.stt-state   { color: #1ab380; font-size: 11px; font-style: italic; margin-left: 2px; }
.stt-loading { color: #8888aa; font-size: 11px; font-style: italic; margin-left: 2px; }
.mic-selector label { color: #000000; font-size: 13px; }
"""


class SocketClient:
    def __init__(self, on_message):
        self._on_msg  = on_message
        self._sock    = None
        self._running = False
        self._q       = queue.Queue()
        self.connected = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="aura-sock").start()

    def stop(self):
        self._running = False

    def send(self, msg):
        self._q.put(msg)

    def _loop(self):
        import time
        while self._running:
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.connect(SOCKET_PATH)
                self._sock.settimeout(0.2)
                self.connected = True
                GLib.idle_add(self._on_msg, {"type": "connected"})
                buf = ""
                while self._running:
                    while not self._q.empty():
                        try:
                            m = self._q.get_nowait()
                            self._sock.sendall((json.dumps(m) + "\n").encode())
                        except queue.Empty:
                            break
                    try:
                        data = self._sock.recv(4096)
                        if not data:
                            break
                        buf += data.decode("utf-8", errors="replace")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if line:
                                try:
                                    msg = json.loads(line)
                                    GLib.idle_add(self._on_msg, msg)
                                except json.JSONDecodeError:
                                    pass
                    except socket.timeout:
                        pass
            except Exception:
                pass
            finally:
                self.connected = False
                GLib.idle_add(self._on_msg, {"type": "disconnected"})
                try:
                    self._sock.close()
                except Exception:
                    pass
            time.sleep(2)


class AuraWindow(Gtk.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title="AURA", **kwargs)
        self._msg_id        = 0
        self._is_fullscreen = True

        self._assistant_name = self._db_get("assistant_name")      or "Aura"
        self._user_name      = self._db_get("user_informal_name")  or "You"

        # STT state
        self._listener        = None
        self._mic_options     = [("Default", "")]  # [(display_label, full_device_name)]
        self._mic_dropdown    = None
        self._mic_header_box  = None   # container for all header mic widgets
        self._stt_icon_lbl    = None   # 🎤 label — CSS class changes with state
        self._stt_state_lbl   = None   # "loading…" / "● listening" — hidden when idle
        self._mic_populating  = False  # suppress notify::selected during programmatic set

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        self._build_header(root)
        self._build_chat(root)
        self._build_input(root)

        self._sock = SocketClient(self._on_socket_msg)
        self._sock.start()

        GLib.timeout_add_seconds(1, self._tick_clock)
        GLib.timeout_add_seconds(5, self._tick_sysinfo)
        self._tick_clock()
        self._tick_sysinfo()

        if _STT_AVAILABLE:
            GLib.idle_add(self._populate_mic_selector)

        # F11: toggle fullscreen
        fs_action = Gio.SimpleAction.new("toggle-fullscreen", None)
        fs_action.connect("activate", lambda *_: self._toggle_fullscreen())
        self.add_action(fs_action)

        # Ctrl+Q: quit
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.get_application().quit())
        self.get_application().add_action(quit_action)

        self.get_application().set_accels_for_action("win.toggle-fullscreen", ["F11"])
        self.get_application().set_accels_for_action("app.quit", ["<Ctrl>q"])

        self.fullscreen()

    # ------------------------------------------------------------------ DB --

    def _db_get(self, key):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def _db_set(self, key, value):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE config SET value=? WHERE key=?", (value, key))
                conn.commit()
        except Exception:
            pass

    # --------------------------------------------------------------- Layout --

    def _build_header(self, parent):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        bar.add_css_class("header")

        title = Gtk.Label()
        title.set_markup(f'<b>{GLib.markup_escape_text(self._assistant_name.upper())}</b>')
        title.add_css_class("title-lbl")
        bar.append(title)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        if _STT_AVAILABLE:
            # All mic widgets in one box — hide the whole box when no device.
            # Positioned left of connection status so it's always visible.
            self._mic_header_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

            # Mic icon — CSS class changes to reflect listener state
            self._stt_icon_lbl = Gtk.Label(label="🎤")
            self._stt_icon_lbl.add_css_class("stt-icon")
            self._mic_header_box.append(self._stt_icon_lbl)

            # State text — hidden when idle; shows "loading…" / "● listening"
            self._stt_state_lbl = Gtk.Label(label="loading…")
            self._stt_state_lbl.add_css_class("stt-loading")
            self._mic_header_box.append(self._stt_state_lbl)

            # Device selector — populated by _populate_mic_selector
            # new_from_strings sets up the PropertyExpression automatically so
            # item labels are actually rendered inside the dropdown button.
            self._mic_dropdown = Gtk.DropDown.new_from_strings(["Default"])
            self._mic_dropdown.set_hexpand(False)
            self._mic_dropdown.add_css_class("mic-selector")
            self._mic_dropdown.connect("notify::selected", self._on_mic_selected)
            self._mic_header_box.append(self._mic_dropdown)

            sep_mic = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            sep_mic.set_margin_start(6)
            sep_mic.set_margin_end(6)
            self._mic_header_box.append(sep_mic)

            bar.append(self._mic_header_box)

        self._conn_lbl = Gtk.Label(label="Connecting...")
        self._conn_lbl.add_css_class("connecting")
        bar.append(self._conn_lbl)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_start(6)
        sep.set_margin_end(6)
        bar.append(sep)

        self._clock_lbl = Gtk.Label(label="--:--")
        bar.append(self._clock_lbl)

        self._temp_lbl = Gtk.Label(label="--°C")
        self._temp_lbl.add_css_class("dim")
        self._temp_lbl.set_margin_start(10)
        bar.append(self._temp_lbl)

        self._mem_lbl = Gtk.Label(label="-- MB")
        self._mem_lbl.add_css_class("dim")
        self._mem_lbl.set_margin_start(6)
        bar.append(self._mem_lbl)

        parent.append(bar)

    def _build_chat(self, parent):
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_vexpand(True)
        self._scroll.set_hexpand(True)
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.add_css_class("chat-scroll")

        self._chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._chat.add_css_class("chat-box")
        self._chat.set_margin_top(12)
        self._chat.set_margin_bottom(12)
        self._chat.set_margin_start(16)
        self._chat.set_margin_end(16)

        # Expanding spacer pushes messages to the bottom when content is sparse.
        self._chat_spacer = Gtk.Box()
        self._chat_spacer.set_vexpand(True)
        self._chat.append(self._chat_spacer)

        self._scroll.set_child(self._chat)
        parent.append(self._scroll)
        self._vadj = self._scroll.get_vadjustment()

        self._vadj.connect(
            "changed",
            lambda adj: adj.set_value(adj.get_upper() - adj.get_page_size()),
        )

    def _build_input(self, parent):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("input-bar")

        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)

        frame = Gtk.Frame()
        frame.add_css_class("input-frame")

        input_scroll = Gtk.ScrolledWindow()
        input_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        input_scroll.set_min_content_height(38)
        input_scroll.set_max_content_height(120)

        self._entry = Gtk.TextView()
        self._entry.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._entry.set_accepts_tab(False)
        self._entry.set_left_margin(4)
        self._entry.set_right_margin(4)
        self._entry.set_top_margin(4)
        self._entry.set_bottom_margin(4)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_entry_key)
        self._entry.add_controller(key_ctrl)

        self._placeholder = Gtk.Label(label="Say something…")
        self._placeholder.set_halign(Gtk.Align.START)
        self._placeholder.set_valign(Gtk.Align.START)
        self._placeholder.set_margin_start(13)
        self._placeholder.set_margin_top(10)
        self._placeholder.add_css_class("dim")
        self._placeholder.set_sensitive(False)
        self._entry.get_buffer().connect(
            "changed",
            lambda buf: self._placeholder.set_visible(buf.get_char_count() == 0),
        )

        input_scroll.set_child(self._entry)
        frame.set_child(input_scroll)
        overlay.set_child(frame)
        overlay.add_overlay(self._placeholder)
        bar.append(overlay)

        btn = Gtk.Button(label="Send")
        btn.add_css_class("send-btn")
        btn.connect("clicked", self._on_send)
        bar.append(btn)

        parent.append(bar)
        GLib.idle_add(lambda: self._entry.grab_focus() or False)

    # ----------------------------------------------------------------- STT --

    def _populate_mic_selector(self):
        """Populate device dropdown; hide mic UI if no devices; start listener."""
        if not _STT_AVAILABLE or self._mic_dropdown is None:
            return False
        try:
            devices = _stt.list_input_devices()
            if not devices:
                self._hide_mic_ui()
                return False

            options = [("Default", "")]
            for name, _ in devices:
                short = name.split(":")[0].strip()
                if len(short) > 20:
                    short = short[:18] + "…"
                options.append((short, name))
            self._mic_options = options

            # Block notify::selected for the entire model/selection setup
            self._mic_populating = True
            new_model = Gtk.StringList.new([o[0] for o in options])
            self._mic_dropdown.set_model(new_model)
            # Re-apply the expression so labels stay visible after model swap.
            expr = Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
            self._mic_dropdown.set_expression(expr)

            # Restore saved preference
            saved = self._db_get("stt_microphone") or ""
            for i, (_, full_name) in enumerate(options):
                if full_name == saved:
                    self._mic_dropdown.set_selected(i)
                    break
            self._mic_populating = False

            self._start_background_listener(saved)

        except Exception as e:
            print(f"[stt] populate_mic_selector: {e}")
            self._hide_mic_ui()
        return False

    def _hide_mic_ui(self):
        """Hide all STT header widgets — called when no microphone is available."""
        if self._mic_header_box:
            self._mic_header_box.set_visible(False)

    def _start_background_listener(self, device_name):
        """Stop any existing listener and start a fresh one for device_name."""
        if self._listener:
            self._listener.stop()
            self._listener = None

        assistant = self._db_get("assistant_name") or "Aura"
        name      = assistant.lower()
        phrases   = [name, f"hey {name}", f"hey, {name}"]
        model_sz  = self._db_get("stt_model") or "tiny"

        # Show "loading…" while the model warms up
        self._set_stt_state("loading")

        self._listener = _stt.BackgroundListener(
            wake_phrases=phrases,
            on_wake      = lambda: GLib.idle_add(self._on_stt_wake),
            on_transcript= lambda t: GLib.idle_add(self._on_stt_transcript, t),
            on_idle      = lambda: GLib.idle_add(self._on_stt_idle),
            on_ready     = lambda: GLib.idle_add(self._on_stt_ready),
            device_name  = device_name,
            model_size   = model_sz,
        )
        self._listener.start()

    # STT state callbacks (all called on the GTK main thread via GLib.idle_add)

    def _on_stt_ready(self):
        """Model loaded and stream open — show idle state."""
        self._set_stt_state("idle")
        return GLib.SOURCE_REMOVE

    def _on_stt_wake(self):
        """Wake phrase detected — show listening indicator."""
        self._set_stt_state("listening")
        return GLib.SOURCE_REMOVE

    def _on_stt_idle(self):
        """Returned to idle after sending transcript (or on startup)."""
        self._set_stt_state("idle")
        return GLib.SOURCE_REMOVE

    def _on_stt_transcript(self, text):
        """Auto-send the transcribed text as a user message."""
        if text:
            self._add_message("user", text)
            if self._sock.connected:
                self._msg_id += 1
                self._sock.send({"type": "chat_input", "text": text,
                                 "id": str(self._msg_id)})
            else:
                self._add_status("Not connected to Aura", "err")
        return GLib.SOURCE_REMOVE

    def _set_stt_state(self, state):
        """Update the header mic icon and state label for state in
        {loading, idle, listening}."""
        if not self._stt_icon_lbl or not self._stt_state_lbl:
            return
        if state == "loading":
            self._stt_icon_lbl.remove_css_class("listening")
            self._stt_state_lbl.set_text("loading…")
            self._stt_state_lbl.remove_css_class("stt-state")
            self._stt_state_lbl.add_css_class("stt-loading")
            self._stt_state_lbl.set_visible(True)
        elif state == "listening":
            self._stt_icon_lbl.add_css_class("listening")
            self._stt_state_lbl.set_text("● listening")
            self._stt_state_lbl.remove_css_class("stt-loading")
            self._stt_state_lbl.add_css_class("stt-state")
            self._stt_state_lbl.set_visible(True)
        else:  # idle
            self._stt_icon_lbl.remove_css_class("listening")
            self._stt_state_lbl.set_visible(False)

    def _on_mic_selected(self, dropdown, _pspec):
        """Save chosen device to config and restart the background listener."""
        if self._mic_populating:
            return
        idx = dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self._mic_options):
            return
        _, full_name = self._mic_options[idx]
        self._db_set("stt_microphone", full_name)
        self._start_background_listener(full_name)

    # --------------------------------------------------------------- Chat --

    def _add_message(self, role, text):
        name   = self._user_name if role == "user" else self._assistant_name
        markup = (f'<b>{GLib.markup_escape_text(name)}: </b>'
                  f'{GLib.markup_escape_text(text)}')
        lbl = Gtk.Label()
        lbl.set_markup(markup)
        lbl.set_xalign(0.0)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_hexpand(True)
        lbl.set_selectable(True)
        lbl.add_css_class("msg-user" if role == "user" else "msg-aura")
        self._chat.append(lbl)

    def _add_status(self, text, css="status-msg"):
        lbl = Gtk.Label()
        lbl.set_markup(f'<i>{GLib.markup_escape_text(text)}</i>')
        lbl.set_xalign(0.5)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_hexpand(True)
        lbl.add_css_class(css)
        self._chat.append(lbl)

    # -------------------------------------------------------------- Input --

    def _on_entry_key(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Return and not (state & Gdk.ModifierType.SHIFT_MASK):
            self._on_send()
            return True
        return False

    def _on_send(self, *_):
        buf  = self._entry.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if not text:
            return
        buf.set_text("")
        self._add_message("user", text)
        if self._sock.connected:
            self._msg_id += 1
            self._sock.send({"type": "chat_input", "text": text, "id": str(self._msg_id)})
        else:
            self._add_status("Not connected to Aura", "err")

    # ---------------------------------------------------- Socket messages --

    def _on_socket_msg(self, msg):
        t = msg.get("type")
        if t == "connected":
            self._conn_lbl.set_text("Connected")
            for c in ("connecting", "disconnected"):
                self._conn_lbl.remove_css_class(c)
            self._conn_lbl.add_css_class("connected")
        elif t == "disconnected":
            self._conn_lbl.set_text("Disconnected")
            for c in ("connecting", "connected"):
                self._conn_lbl.remove_css_class(c)
            self._conn_lbl.add_css_class("disconnected")
        elif t == "tts_start":
            if self._listener:
                self._listener.mute()
        elif t == "tts_end":
            if self._listener:
                self._listener.unmute()
        elif t == "chat_response":
            text = msg.get("text", "")
            if text:
                self._add_message("aura", text)
        elif t == "system_message":
            level = msg.get("level", "info")
            css   = {"warning": "warn", "error": "err"}.get(level, "status-msg")
            self._add_status(msg.get("text", ""), css)
        elif t == "status_update":
            key, val = msg.get("key", ""), msg.get("value", "")
            if key == "cpu_temp":
                self._set_temp(val)
            elif key == "memory":
                self._mem_lbl.set_text(f"{val} MB")
        return GLib.SOURCE_REMOVE

    # -------------------------------------------------------- System info --

    def _tick_clock(self):
        self._clock_lbl.set_text(datetime.now().strftime("%H:%M"))
        return GLib.SOURCE_CONTINUE

    def _tick_sysinfo(self):
        try:
            with open('/sys/class/thermal/thermal_zone0/temp') as f:
                temp = int(f.read().strip()) / 1000.0
            self._set_temp(f"{temp:.1f}")
        except Exception:
            pass
        try:
            used_mb = self._read_mem_mb()
            if used_mb is not None:
                self._mem_lbl.set_text(f"{used_mb} MB")
        except Exception:
            pass
        return GLib.SOURCE_CONTINUE

    def _read_mem_mb(self):
        with open('/proc/meminfo') as f:
            lines = f.readlines()
        info = {}
        for line in lines:
            k, v = line.split(':', 1)
            info[k.strip()] = int(v.strip().split()[0])
        total   = info.get('MemTotal',  0)
        free    = info.get('MemFree',   0)
        buffers = info.get('Buffers',   0)
        cached  = info.get('Cached',    0)
        return (total - free - buffers - cached) // 1024

    def _set_temp(self, value):
        try:
            v = float(value)
            self._temp_lbl.set_text(f"{v:.1f}°C")
            for c in ("dim", "warn", "err"):
                self._temp_lbl.remove_css_class(c)
            if v > 80:
                self._temp_lbl.add_css_class("err")
            elif v > 70:
                self._temp_lbl.add_css_class("warn")
            else:
                self._temp_lbl.add_css_class("dim")
        except Exception:
            pass

    def _toggle_fullscreen(self):
        if self._is_fullscreen:
            self.unfullscreen()
            self._is_fullscreen = False
        else:
            self.fullscreen()
            self._is_fullscreen = True

    def do_close_request(self):
        if self._listener:
            self._listener.stop()
        self._sock.stop()
        return False


class AuraApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="ai.aura.interface")
        self.connect("activate", self._on_activate)

    def _on_activate(self, _):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        win = AuraWindow(application=self)
        win.present()


if __name__ == "__main__":
    sys.exit(AuraApp().run(sys.argv))
