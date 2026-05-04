#!/usr/bin/env python3
"""AURA GTK4 interface — animated Fluent Emoji character UI"""

import sys
import os
import glob

# gi (PyGObject) lives in the system Python; STT packages live in the venv.
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


SOCKET_PATH = "/tmp/aura.sock"
DB_PATH     = os.path.expanduser("~/aura/aura.db")
EMOJI_DIR   = os.path.expanduser("~/aura/aura/assets/emoji")
FLUENT_BASE = "https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets"

# Character state → emoji, display label, Fluent Emoji asset
AURA_STATES = {
    "idle":          {"emoji": "🙂",    "label": "",           "file": "slightly_smiling_face_3d.png",          "folder": "Slightly smiling face"},
    "thinking":      {"emoji": "🤔",    "label": "Thinking…",  "file": "thinking_face_3d.png",                  "folder": "Thinking face"},
    "speaking":      {"emoji": "😄",    "label": "Speaking…",  "file": "grinning_face_with_big_eyes_3d.png",    "folder": "Grinning face with big eyes"},
    "listening":     {"emoji": "🤨",    "label": "Listening…", "file": "face_with_raised_eyebrow_3d.png",       "folder": "Face with raised eyebrow"},
    "sleeping":      {"emoji": "😴",    "label": "Dreaming…",  "file": "sleeping_face_3d.png",                  "folder": "Sleeping face"},
    "loading":       {"emoji": "🙂",    "label": "Loading…",   "file": "slightly_smiling_face_3d.png",          "folder": "Slightly smiling face"},
    "tools":         {"emoji": "🧐",    "label": "Working…",   "file": "face_with_monocle_3d.png",              "folder": "Face with monocle"},
    "output_muted":  {"emoji": "🤐",    "label": "",           "file": "zipper-mouth_face_3d.png",              "folder": "Zipper-mouth face"},
    "input_muted":   {"emoji": "🙉",    "label": "",           "file": "hear-no-evil_monkey_3d.png",            "folder": "Hear-no-evil monkey"},
    "both_muted":    {"emoji": "😶‍🌫️", "label": "",     "file": "face_in_clouds_3d.png",                 "folder": "Face in clouds"},
    "partying":      {"emoji": "🥳",    "label": "Speaking…",  "file": "partying_face_3d.png",                  "folder": "Partying face"},
}

# keyword → state for contextual emoji during responses
_CONTEXT_EMOJI_MAP = {
    "happy birthday": "partying",
    "birthday":       "partying",
}

# States with specific named CSS animation classes.  Dynamic emoji use "aura-state-expression".
_STATIC_STATES = frozenset(AURA_STATES)

# States that STT events may override (not thinking/speaking — those are higher priority)
_STT_OVERRIDABLE = {"idle", "sleeping", "listening", "loading", "output_muted"}

# CLDR short name overrides: where Python unicodedata.name() ≠ Fluent Emoji folder name
_FLUENT_NAME_OVERRIDES = {
    "😁": "Beaming face with smiling eyes",
    "😃": "Grinning face with big eyes",
    "😄": "Grinning face with smiling eyes",
    "😅": "Grinning face with sweat",
    "😆": "Grinning squinting face",
    "😋": "Face savoring food",
    "😍": "Smiling face with heart-eyes",
    "😓": "Downcast face with sweat",
    "😘": "Face blowing a kiss",
    "😛": "Face with tongue",
    "😜": "Winking face with tongue",
    "😝": "Squinting face with tongue",
    "😡": "Enraged face",
    "😥": "Sad but relieved face",
    "😰": "Anxious face with sweat",
    "😵": "Face with crossed-out eyes",
    "😸": "Grinning cat with smiling eyes",
    "🤗": "Smiling face with open hands",
    "🤠": "Cowboy hat face",
    "🤩": "Star-struck",
    "🤪": "Zany face",
    "🤫": "Shushing face",
    "🤬": "Face with symbols on mouth",
    "🤭": "Face with hand over mouth",
    "🤮": "Face vomiting",
    "🤯": "Exploding head",
    "🥰": "Smiling face with hearts",
    # Objects / symbols where Unicode name has extra words Fluent dropped
    "👏": "Clapping hands",
    "👍": "Thumbs up",
    "👎": "Thumbs down",
    "💯": "Hundred points",
    "💡": "Light bulb",
    "⭐": "Star",
    "🌟": "Glowing star",
    "🔥": "Fire",
    "❤️": "Red heart",
    "💔": "Broken heart",
    "🦊": "Fox",
    "🦁": "Lion",
    "🐺": "Wolf",
    "🦝": "Raccoon",
}


def _fluent_info_for_emoji(char):
    """Return AURA_STATES-compatible info dict for any single Unicode emoji char, or None."""
    import unicodedata

    # ZWJ sequences / multi-codepoint emoji: try the base character
    base = char.rstrip("️")          # strip variation selector
    base = base.split("‍")[0]        # take first part of ZWJ sequence

    if base in _FLUENT_NAME_OVERRIDES:
        folder = _FLUENT_NAME_OVERRIDES[base]
    else:
        try:
            raw = unicodedata.name(base)
        except ValueError:
            return None
        # Fluent Emoji uses sentence case: first word capitalised, rest lowercase
        words = raw.lower().split()
        folder = words[0].capitalize() + (" " + " ".join(words[1:]) if len(words) > 1 else "")

    fname = folder.lower().replace(" ", "_") + "_3d.png"
    return {"emoji": char, "label": "", "file": fname, "folder": folder}


# ──────────────────────────────────────────────── CSS ──────────────────────────

_EMOJI_ANIM_CSS = b"""
@keyframes aura-breathe {
    0%   { opacity: 1.0;  margin-top: 0px; }
    50%  { opacity: 0.88; margin-top: -10px; }
    100% { opacity: 1.0;  margin-top: 0px; }
}
@keyframes aura-bounce {
    0%,100% { margin-top: 0px; }
    35%     { margin-top: -14px; }
    55%     { margin-top: -7px; }
}
@keyframes aura-pulse {
    0%   { opacity: 1.0; }
    50%  { opacity: 0.45; }
    100% { opacity: 1.0; }
}
@keyframes aura-slow-breathe {
    0%   { opacity: 0.65; margin-top: 0px; }
    50%  { opacity: 0.45; margin-top: -8px; }
    100% { opacity: 0.65; margin-top: 0px; }
}
.aura-state-idle         { animation: aura-breathe 4s ease-in-out infinite; }
.aura-state-thinking     { animation: aura-bounce 1.4s ease-in-out infinite; }
.aura-state-speaking     { animation: aura-pulse 0.7s ease-in-out infinite; }
.aura-state-listening    { animation: aura-pulse 1.1s ease-in-out infinite; }
.aura-state-sleeping     { animation: aura-slow-breathe 7s ease-in-out infinite; }
.aura-state-loading      { animation: aura-pulse 2.5s ease-in-out infinite; }
.aura-state-tools        { animation: aura-bounce 0.9s ease-in-out infinite; }
.aura-state-output_muted { animation: aura-breathe 4s ease-in-out infinite; }
.aura-state-input_muted  { animation: aura-breathe 4s ease-in-out infinite; }
.aura-state-both_muted   { animation: aura-slow-breathe 7s ease-in-out infinite; }
.aura-state-partying     { animation: aura-bounce 0.8s ease-in-out infinite; }
.aura-state-expression   { animation: aura-breathe 3s ease-in-out infinite; }
"""

DARK_CSS = _EMOJI_ANIM_CSS + b"""
window { background-color: #14141e; }
.header {
    background-color: #0f0f19;
    padding: 8px 14px;
    border-bottom: 1px solid #252538;
}
.emoji-panel {
    background-color: #0d0d18;
    border-right: 1px solid #252538;
    min-width: 200px;
}
.emoji-char {
    font-size: 96px;
    font-family: "Noto Color Emoji", sans-serif;
}
.emoji-state-lbl {
    color: #1ab380;
    font-size: 13px;
    font-style: italic;
}
.emoji-name-lbl {
    color: #6868a0;
    font-size: 12px;
    letter-spacing: 2px;
    margin-top: 4px;
}
.chat-scroll  { background-color: #14141e; }
.chat-box     { background-color: #14141e; }
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
.input-frame:focus-within { border-color: #3380e8; }
textview {
    background-color: #1a1a2e;
    color: #f0f0f0;
    font-size: 15px;
    padding: 8px 12px;
}
textview text { background-color: #1a1a2e; color: #f0f0f0; }
button.send-btn {
    background-color: #3380e8;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 22px;
    font-weight: bold;
    font-size: 14px;
}
button.send-btn:hover { background-color: #4490f8; }
label { color: #f0f0f0; }
.title-lbl   { color: #1ab380; font-size: 15px; }
.connecting  { color: #e69910; }
.connected   { color: #1ab380; }
.disconnected{ color: #e63333; }
.dim         { color: #8888aa; }
.warn        { color: #e69910; }
.err         { color: #e63333; }
.msg-user    { color: #d0d0ff; font-size: 14px; }
.msg-aura    { color: #f0f0f0; font-size: 14px; }
.status-msg  { color: #6868a0; font-size: 12px; font-style: italic; }
.stt-icon         { color: #8888aa; font-size: 18px; }
.stt-icon.ready   { color: #1ab380; }
.stt-icon.privacy { color: #e63333; }
.stt-state   { color: #1ab380; font-size: 11px; font-style: italic; margin-left: 2px; }
.stt-loading { color: #8888aa; font-size: 11px; font-style: italic; margin-left: 2px; }
.mic-selector label { color: #000000; font-size: 13px; }
dropdown label { color: #000000; }
button label   { color: #000000; }
button.privacy-btn label       { color: #6868a0; }
button.privacy-btn:hover label { color: #f0f0f0; }
button.privacy-btn:checked label       { color: #e63333; }
button.privacy-btn:checked:hover label { color: #ff5555; }
button.header-btn label       { color: #6868a0; }
button.header-btn:hover label { color: #f0f0f0; }
button.settings-close-btn label       { color: #6868a0; }
button.settings-close-btn:hover label { color: #f0f0f0; }
.battery-ok       { color: #8888aa; font-size: 12px; }
.battery-warning  { color: #e69910; font-size: 12px; }
.battery-critical { color: #e63333; font-size: 12px; }
button.privacy-btn {
    background: none; border: none; color: #6868a0;
    font-size: 16px; padding: 2px 8px; min-height: 0; min-width: 0; border-radius: 4px;
}
button.privacy-btn:hover   { color: #f0f0f0; background-color: #252538; }
button.privacy-btn:checked { color: #e63333; }
button.privacy-btn:checked:hover { color: #ff5555; background-color: #252538; }
button.header-btn {
    background: none; border: none; color: #6868a0;
    font-size: 16px; padding: 2px 8px; min-height: 0; min-width: 0; border-radius: 4px;
}
button.header-btn:hover { color: #f0f0f0; background-color: #252538; }
.settings-dialog { background-color: #14141e; }
.settings-panel  { background-color: #14141e; }
.settings-header {
    background-color: #0f0f19;
    padding: 12px 16px;
    border-bottom: 1px solid #252538;
}
.settings-title { color: #f0f0f0; font-size: 15px; font-weight: bold; }
.settings-close-btn {
    background: none; border: none; color: #6868a0;
    font-size: 15px; padding: 2px 8px; min-height: 0; min-width: 0;
}
.settings-close-btn:hover { color: #f0f0f0; }
.settings-section {
    color: #1ab380; font-size: 11px; font-weight: bold; letter-spacing: 1px;
}
.settings-row {
    background-color: #1a1a2e; border-radius: 6px; padding: 10px 14px; margin-bottom: 2px;
}
.settings-row-label { color: #f0f0f0; font-size: 14px; }
.settings-row-desc  { color: #6868a0; font-size: 12px; }
.settings-footer {
    background-color: #0f0f19; padding: 10px 16px; border-top: 1px solid #252538;
}
button.settings-btn-save {
    background-color: #3380e8; color: #ffffff; border: none;
    border-radius: 6px; padding: 8px 24px; font-weight: bold; font-size: 14px;
}
button.settings-btn-save:hover { background-color: #4490f8; }
button.settings-btn-cancel {
    background-color: #252538; color: #c0c0d0; border: none;
    border-radius: 6px; padding: 8px 18px; font-size: 14px;
}
button.settings-btn-cancel:hover { background-color: #2e2e4a; }
entry.settings-entry {
    background-color: #252538; color: #f0f0f0;
    border: 1px solid #3a3a58; border-radius: 4px; font-size: 13px;
}
entry.settings-entry:focus { border-color: #3380e8; }
"""

LIGHT_CSS = _EMOJI_ANIM_CSS + b"""
window { background-color: #f5f5fa; }
.header {
    background-color: #eaeaf2;
    padding: 8px 14px;
    border-bottom: 1px solid #d0d0e0;
}
.emoji-panel {
    background-color: #e4e4ef;
    border-right: 1px solid #d0d0e0;
    min-width: 200px;
}
.emoji-char {
    font-size: 96px;
    font-family: "Noto Color Emoji", sans-serif;
}
.emoji-state-lbl {
    color: #0a7a55;
    font-size: 13px;
    font-style: italic;
}
.emoji-name-lbl {
    color: #6868a0;
    font-size: 12px;
    letter-spacing: 2px;
    margin-top: 4px;
}
.chat-scroll  { background-color: #f5f5fa; }
.chat-box     { background-color: #f5f5fa; }
.input-bar {
    background-color: #eaeaf2;
    padding: 8px;
    border-top: 1px solid #d0d0e0;
}
.input-frame {
    border: 1px solid #b0b0cc;
    border-radius: 6px;
    background-color: #ffffff;
}
.input-frame:focus-within { border-color: #3380e8; }
textview {
    background-color: #ffffff;
    color: #1a1a2e;
    font-size: 15px;
    padding: 8px 12px;
}
textview text { background-color: #ffffff; color: #1a1a2e; }
button.send-btn {
    background-color: #3380e8; color: #ffffff; border: none;
    border-radius: 6px; padding: 8px 22px; font-weight: bold; font-size: 14px;
}
button.send-btn:hover { background-color: #4490f8; }
label { color: #1a1a2e; }
.title-lbl   { color: #0a7a55; font-size: 15px; }
.connecting  { color: #c07000; }
.connected   { color: #0a7a55; }
.disconnected{ color: #cc2222; }
.dim         { color: #6868a0; }
.warn        { color: #c07000; }
.err         { color: #cc2222; }
.msg-user    { color: #2020aa; font-size: 14px; }
.msg-aura    { color: #1a1a2e; font-size: 14px; }
.status-msg  { color: #8888aa; font-size: 12px; font-style: italic; }
.stt-icon         { color: #6868a0; font-size: 18px; }
.stt-icon.ready   { color: #0a7a55; }
.stt-icon.privacy { color: #cc2222; }
.stt-state   { color: #0a7a55; font-size: 11px; font-style: italic; margin-left: 2px; }
.stt-loading { color: #6868a0; font-size: 11px; font-style: italic; margin-left: 2px; }
.mic-selector label { color: #000000; font-size: 13px; }
.battery-ok       { color: #6868a0; font-size: 12px; }
.battery-warning  { color: #c07000; font-size: 12px; }
.battery-critical { color: #cc2222; font-size: 12px; }
button.privacy-btn {
    background: none; border: none; color: #6868a0;
    font-size: 16px; padding: 2px 8px; min-height: 0; min-width: 0; border-radius: 4px;
}
button.privacy-btn:hover   { color: #1a1a2e; background-color: #d8d8ea; }
button.privacy-btn:checked { color: #cc2222; }
button.privacy-btn:checked:hover { color: #ff3333; background-color: #d8d8ea; }
button.header-btn {
    background: none; border: none; color: #6868a0;
    font-size: 16px; padding: 2px 8px; min-height: 0; min-width: 0; border-radius: 4px;
}
button.header-btn:hover { color: #1a1a2e; background-color: #d8d8ea; }
.settings-dialog { background-color: #f5f5fa; }
.settings-panel  { background-color: #f5f5fa; }
.settings-header {
    background-color: #eaeaf2; padding: 12px 16px; border-bottom: 1px solid #d0d0e0;
}
.settings-title { color: #1a1a2e; font-size: 15px; font-weight: bold; }
.settings-close-btn {
    background: none; border: none; color: #6868a0;
    font-size: 15px; padding: 2px 8px; min-height: 0; min-width: 0;
}
.settings-close-btn:hover { color: #1a1a2e; }
.settings-section {
    color: #0a7a55; font-size: 11px; font-weight: bold; letter-spacing: 1px;
}
.settings-row {
    background-color: #ffffff; border-radius: 6px; padding: 10px 14px; margin-bottom: 2px;
}
.settings-row-label { color: #1a1a2e; font-size: 14px; }
.settings-row-desc  { color: #6868a0; font-size: 12px; }
.settings-footer {
    background-color: #eaeaf2; padding: 10px 16px; border-top: 1px solid #d0d0e0;
}
button.settings-btn-save {
    background-color: #3380e8; color: #ffffff; border: none;
    border-radius: 6px; padding: 8px 24px; font-weight: bold; font-size: 14px;
}
button.settings-btn-save:hover { background-color: #4490f8; }
button.settings-btn-cancel {
    background-color: #d0d0e0; color: #404060; border: none;
    border-radius: 6px; padding: 8px 18px; font-size: 14px;
}
button.settings-btn-cancel:hover { background-color: #c0c0d0; }
entry.settings-entry {
    background-color: #ffffff; color: #1a1a2e;
    border: 1px solid #b0b0cc; border-radius: 4px; font-size: 13px;
}
entry.settings-entry:focus { border-color: #3380e8; }
"""

_css_provider = None


def _apply_theme(mode):
    css = LIGHT_CSS if mode == "light" else DARK_CSS
    _css_provider.load_from_data(css)


# ──────────────────────────────────────────────── Socket ───────────────────────

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


# ──────────────────────────────────────────────── Settings ─────────────────────

class SettingsPage(Gtk.Box):
    """In-window settings panel — overlaid on the main area, never a separate window."""

    def __init__(self, parent, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._parent   = parent
        self._on_close = on_close
        self._getters  = {}

        self.add_css_class("settings-panel")
        self._build_header()
        try:
            self._build_body()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            err = Gtk.Label(label=f"Settings failed to load:\n{exc}")
            err.set_wrap(True)
            err.set_vexpand(True)
            err.add_css_class("err")
            self.append(err)
        self._build_footer()

    def _build_header(self):
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        hdr.add_css_class("settings-header")
        lbl = Gtk.Label(label="Settings")
        lbl.add_css_class("settings-title")
        lbl.set_hexpand(True)
        lbl.set_xalign(0.0)
        hdr.append(lbl)
        close = Gtk.Button(label="✕")
        close.add_css_class("settings-close-btn")
        close.connect("clicked", lambda *_: self._on_close())
        hdr.append(close)
        self.append(hdr)

    def _build_body(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(16)
        box.set_margin_end(16)

        g = self._parent._db_get

        self._section(box, "Identity")
        self._text_row(box, "assistant_name",        "Assistant Name",        "The name AURA calls herself",                        g("assistant_name")          or "Aura")
        self._drop_row(box, "assistant_gender",       "Assistant Gender",       "Affects pronouns in self-descriptions",               ["female", "male", "neutral"], g("assistant_gender")  or "female")
        self._text_row(box, "user_name",              "Your Full Name",         "Used in formal contexts",                            g("user_name")               or "")
        self._text_row(box, "user_informal_name",     "Your Nickname",          "How AURA addresses you casually",                    g("user_informal_name")      or "")
        self._text_row(box, "location",               "Location",               "City or region for weather and context",              g("location")                or "")

        self._section(box, "Personality")
        self._text_row(box, "tone_preference",        "Conversation Tone",      "e.g. warm and direct, professional, casual",         g("tone_preference")         or "warm and direct")
        self._text_row(box, "personality_traits",     "Personality Traits",     "Comma-separated traits",                             g("personality_traits")      or "")
        self._text_row(box, "use_case",               "Primary Use Case",       "e.g. general assistance, coding, research",          g("use_case")                or "general assistance")
        self._drop_row(box, "failure_mode",           "On Frustration",         "How AURA responds when things go wrong",             ["gentle", "firm", "playful", "direct"], g("failure_mode") or "gentle")

        self._section(box, "Voice & Audio")
        self._switch_row(box, "audio_enabled",        "Audio Output",           "Enable text-to-speech voice output",                 g("audio_enabled"))
        self._text_row(box,   "voice_model",          "Voice Model",            "Piper TTS model name (e.g. en_US-amy-medium)",       g("voice_model")             or "en_US-amy-medium")
        self._scale_row(box,  "voice_speed",          "Voice Speed",            "TTS playback speed multiplier",                      g("voice_speed")             or "1.0", 0.5, 2.0, 0.1)

        self._section(box, "Speech Input")
        self._switch_row(box, "stt_enabled",          "Voice Input",            "Enable always-on wake-word detection",               g("stt_enabled"))
        self._text_row(box,   "wake_prefix",          "Wake Prefix",            "Leave empty for both 'Hey' and 'OK'",                g("wake_prefix")             or "")
        self._drop_row(box,   "stt_model",            "Whisper Model",          "Larger = more accurate, slower to load",             ["tiny", "base", "small"],   g("stt_model")         or "tiny")
        self._text_row(box,   "vosk_model_path",      "Vosk Model Path",        "Path to Vosk model directory (advanced)",            g("vosk_model_path")         or "/home/aura/models/vosk/small-en-us")
        self._scale_row(box,  "stt_energy_floor",     "Energy Floor",           "RMS silence threshold — raise if TV/background noise triggers wake detection", g("stt_energy_floor") or "0.015", 0.001, 0.1, 0.001, 3)
        self._scale_row(box,  "stt_silence_needed",   "Silence Duration",       "Seconds of silence before utterance ends — raise if speech gets cut off early", g("stt_silence_needed") or "2.5", 0.5, 5.0, 0.1, 1)

        self._section(box, "Web & Search")
        self._switch_row(box, "auto_search",          "Auto Web Search",        "Automatically search the web when relevant",         g("auto_search"))

        self._section(box, "LLM Endpoints")
        self._text_row(box, "home_pc_endpoint",       "Home PC Endpoint",       "Local llama.cpp server URL",                         g("home_pc_endpoint")        or "")
        self._text_row(box, "remote_api_endpoint",    "Remote API Endpoint",    "Fallback cloud API (OpenAI-compatible URL)",          g("remote_api_endpoint")     or "")

        self._section(box, "Background & Memory")
        self._spin_row(box, "dream_delay",            "Dream Delay",            "Minutes of silence before memory consolidation",     g("dream_delay")             or "10",  1, 120)
        self._spin_row(box, "awareness_interval",     "Awareness Interval",     "Background check interval in minutes",               g("awareness_interval")      or "5",   1,  60)
        self._text_row(box, "quiet_hours_start",      "Quiet Hours Start",      "Suppress non-urgent alerts after this time (HH:MM)", g("quiet_hours_start")       or "22:00")
        self._text_row(box, "quiet_hours_end",        "Quiet Hours End",        "Resume normal alerts after this time (HH:MM)",       g("quiet_hours_end")         or "07:00")

        self._section(box, "Display")
        self._drop_row(box, "theme",                  "Theme",                  "UI colour scheme",                                   ["dark", "light"],           g("theme")             or "dark")
        self._drop_row(box, "clock_format",           "Clock Format",           "12-hour (1:30 PM) or 24-hour (13:30) time display",  ["24", "12"],                g("clock_format")      or "24")

        self._section(box, "Hardware")
        self._spin_row(box, "critical_temp_threshold",    "Critical Temperature",   "CPU temperature warning threshold (°C)",          g("critical_temp_threshold")   or "80",  50, 100)
        self._spin_row(box, "battery_warning_threshold",  "Battery Warning Level",  "Warn when battery drops below this % (0=off)",   g("battery_warning_threshold") or "20",   0,  50)
        self._spin_row(box, "battery_critical_threshold", "Battery Critical Level", "Critical alert when battery drops below this %", g("battery_critical_threshold") or "10",  0,  30)
        self._text_row(box, "pisugar3_socket",            "PiSugar 3 Socket Path",  "Leave blank for default (/tmp/pisugar-server.sock)", g("pisugar3_socket")        or "")

        self._section(box, "Debug")
        self._switch_row(box, "debug_tools",          "Debug Tool Calls",       "Print tool arguments and results to console",        g("debug_tools"))

        scroll.set_child(box)
        self.append(scroll)

    def _build_footer(self):
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("settings-footer")
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("settings-btn-cancel")
        cancel.connect("clicked", lambda *_: self._on_close())
        footer.append(cancel)
        save = Gtk.Button(label="Save Changes")
        save.add_css_class("settings-btn-save")
        save.connect("clicked", self._save)
        footer.append(save)
        self.append(footer)

    def _section(self, parent, title):
        lbl = Gtk.Label(label=title.upper())
        lbl.add_css_class("settings-section")
        lbl.set_xalign(0.0)
        lbl.set_margin_top(16)
        lbl.set_margin_bottom(4)
        parent.append(lbl)

    def _make_row(self, parent, label, desc, widget):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("settings-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        lbl = Gtk.Label(label=label)
        lbl.add_css_class("settings-row-label")
        lbl.set_xalign(0.0)
        text.append(lbl)
        if desc:
            dl = Gtk.Label(label=desc)
            dl.add_css_class("settings-row-desc")
            dl.set_xalign(0.0)
            dl.set_wrap(True)
            dl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            text.append(dl)
        row.append(text)
        widget.set_valign(Gtk.Align.CENTER)
        row.append(widget)
        parent.append(row)

    def _text_row(self, parent, key, label, desc, value):
        entry = Gtk.Entry()
        entry.set_text(value)
        entry.add_css_class("settings-entry")
        entry.set_width_chars(22)
        self._make_row(parent, label, desc, entry)
        self._getters[key] = entry.get_text

    def _drop_row(self, parent, key, label, desc, options, current):
        dd = Gtk.DropDown.new_from_strings(options)
        if current in options:
            dd.set_selected(options.index(current))
        self._make_row(parent, label, desc, dd)
        def _get(d=dd, opts=options):
            idx = d.get_selected()
            return opts[idx] if idx != Gtk.INVALID_LIST_POSITION else opts[0]
        self._getters[key] = _get

    def _switch_row(self, parent, key, label, desc, value):
        sw = Gtk.Switch()
        sw.set_active((value or "0") == "1")
        self._make_row(parent, label, desc, sw)
        self._getters[key] = lambda s=sw: "1" if s.get_active() else "0"

    def _spin_row(self, parent, key, label, desc, value, lo, hi):
        try:
            cur = float(value)
        except (TypeError, ValueError):
            cur = float(lo)
        spin = Gtk.SpinButton.new_with_range(lo, hi, 1)
        spin.set_value(cur)
        spin.set_width_chars(5)
        self._make_row(parent, label, desc, spin)
        self._getters[key] = lambda s=spin: str(int(s.get_value()))

    def _scale_row(self, parent, key, label, desc, value, lo, hi, step, digits=1):
        try:
            cur = float(value)
        except (TypeError, ValueError):
            cur = float(lo)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_value(cur)
        scale.set_size_request(150, -1)
        scale.set_draw_value(True)
        scale.set_digits(digits)
        self._make_row(parent, label, desc, scale)
        fmt = f"{{:.{digits}f}}"
        self._getters[key] = lambda s=scale, f=fmt: f.format(s.get_value())

    def _save(self, *_):
        changed = {}
        for key, getter in self._getters.items():
            new_val = getter()
            old_val = self._parent._db_get(key) or ""
            if new_val != old_val:
                self._parent._db_set(key, new_val)
                changed[key] = new_val
        if changed:
            self._parent._on_settings_saved(changed)
        self._on_close()


# ──────────────────────────────────────────────── Main window ──────────────────

class AuraWindow(Gtk.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title="AURA", **kwargs)
        self._msg_id        = 0
        self._is_fullscreen = False
        self._aura_state    = "none"  # forces first _set_aura_state to apply

        self._assistant_name = self._db_get("assistant_name")     or "Aura"
        self._user_name      = self._db_get("user_informal_name") or "You"
        self._clock_format   = self._db_get("clock_format")       or "24"

        self._cpu_prev       = None
        self._mic_options    = [("Default", "")]
        self._mic_dropdown   = None
        self._mic_select_box = None
        self._stt_icon_lbl     = None
        self._stt_state_lbl    = None
        self._mic_populating   = False
        self._privacy_btn      = None
        self._privacy_mode     = (self._db_get("privacy_mode") or "0") == "1"
        self._tts_active       = False
        self._stt_current_state = "idle"

        # Emoji panel widgets (set in _build_emoji_panel)
        self._emoji_container = None
        self._emoji_picture   = None
        self._emoji_label     = None
        self._state_lbl       = None
        self._char_name_lbl   = None
        self._context_emoji   = None  # contextual state override for next tts_start

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        self._build_header(root)
        self._build_main_area(root)

        self._sock = SocketClient(self._on_socket_msg)
        self._sock.start()

        GLib.timeout_add_seconds(1, self._tick_clock)
        GLib.timeout_add_seconds(5, self._tick_sysinfo)
        self._tick_clock()
        self._tick_sysinfo()

        fs_action = Gio.SimpleAction.new("toggle-fullscreen", None)
        fs_action.connect("activate", lambda *_: self._toggle_fullscreen())
        self.add_action(fs_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.get_application().quit())
        self.get_application().add_action(quit_action)

        self.get_application().set_accels_for_action("win.toggle-fullscreen", ["F11"])
        self.get_application().set_accels_for_action("app.quit", ["<Ctrl>q"])

        self.set_default_size(960, 580)
        self._set_aura_state(self._idle_state())

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

        self._title_lbl = Gtk.Label()
        self._title_lbl.set_markup(f'<b>{GLib.markup_escape_text(self._assistant_name.upper())}</b>')
        self._title_lbl.add_css_class("title-lbl")
        bar.append(self._title_lbl)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        self._stt_icon_lbl = Gtk.Label(label="●")
        self._stt_icon_lbl.add_css_class("stt-icon")
        bar.append(self._stt_icon_lbl)

        self._stt_state_lbl = Gtk.Label(label="loading…")
        self._stt_state_lbl.add_css_class("stt-loading")
        self._stt_state_lbl.set_visible(False)
        bar.append(self._stt_state_lbl)

        self._mic_select_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._mic_select_box.set_visible(False)
        sep_mic = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep_mic.set_margin_start(6)
        sep_mic.set_margin_end(6)
        self._mic_select_box.append(sep_mic)
        self._mic_dropdown = Gtk.DropDown.new_from_strings(["Default"])
        self._mic_dropdown.set_hexpand(False)
        self._mic_dropdown.add_css_class("mic-selector")
        self._mic_dropdown.connect("notify::selected", self._on_mic_selected)
        self._mic_select_box.append(self._mic_dropdown)
        bar.append(self._mic_select_box)

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

        self._batt_lbl = Gtk.Label(label="🔋 --%")
        self._batt_lbl.add_css_class("battery-ok")
        self._batt_lbl.set_margin_start(6)
        self._batt_lbl.set_visible(False)
        bar.append(self._batt_lbl)

        self._cpu_lbl = Gtk.Label(label="--%")
        self._cpu_lbl.add_css_class("dim")
        self._cpu_lbl.set_margin_start(6)
        bar.append(self._cpu_lbl)

        self._privacy_btn = Gtk.ToggleButton(label="🔒")
        self._privacy_btn.add_css_class("privacy-btn")
        self._privacy_btn.set_tooltip_text("Privacy mode — stops listening")
        self._privacy_btn.set_active(self._privacy_mode)
        self._privacy_btn.connect("toggled", self._on_privacy_toggled)
        self._privacy_btn.set_margin_start(4)
        bar.append(self._privacy_btn)

        knowledge_btn = Gtk.Button(label="📚")
        knowledge_btn.add_css_class("header-btn")
        knowledge_btn.set_tooltip_text("Import document to knowledge base")
        knowledge_btn.connect("clicked", self._open_knowledge_dialog)
        knowledge_btn.set_margin_start(4)
        bar.append(knowledge_btn)

        settings_btn = Gtk.Button(label="⚙")
        settings_btn.add_css_class("header-btn")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._open_settings)
        settings_btn.set_margin_start(4)
        bar.append(settings_btn)

        parent.append(bar)

    def _build_main_area(self, parent):
        """Overlay → Paned (emoji left | chat+input right)."""
        self._settings_overlay = Gtk.Overlay()
        self._settings_overlay.set_vexpand(True)
        self._settings_overlay.set_hexpand(True)
        self._active_settings  = None

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_hexpand(True)
        paned.set_position(260)

        emoji_panel = self._build_emoji_panel()
        paned.set_start_child(emoji_panel)
        paned.set_shrink_start_child(False)

        chat_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._build_chat(chat_area)
        self._build_input(chat_area)
        paned.set_end_child(chat_area)
        paned.set_shrink_end_child(False)

        self._settings_overlay.set_child(paned)
        parent.append(self._settings_overlay)

    def _build_emoji_panel(self):
        """Left pane: animated character display."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("emoji-panel")
        panel.set_vexpand(True)

        self._emoji_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._emoji_container.set_halign(Gtk.Align.FILL)
        self._emoji_container.set_valign(Gtk.Align.CENTER)
        self._emoji_container.set_vexpand(True)
        self._emoji_container.set_hexpand(True)
        self._emoji_container.set_margin_start(12)
        self._emoji_container.set_margin_end(12)
        self._emoji_container.set_margin_top(16)
        self._emoji_container.set_margin_bottom(16)

        # Fluent Emoji PNG — resizes with the pane via ContentFit.CONTAIN
        self._emoji_picture = Gtk.Picture()
        self._emoji_picture.set_can_shrink(True)
        self._emoji_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._emoji_picture.set_halign(Gtk.Align.CENTER)
        self._emoji_picture.set_valign(Gtk.Align.CENTER)
        self._emoji_picture.set_hexpand(True)
        self._emoji_picture.set_vexpand(True)
        self._emoji_picture.set_visible(False)
        self._emoji_container.append(self._emoji_picture)

        # Unicode emoji fallback (shown when PNG not available)
        self._emoji_label = Gtk.Label(label="🙂")
        self._emoji_label.add_css_class("emoji-char")
        self._emoji_label.set_halign(Gtk.Align.CENTER)
        self._emoji_container.append(self._emoji_label)

        self._state_lbl = Gtk.Label(label="")
        self._state_lbl.add_css_class("emoji-state-lbl")
        self._state_lbl.set_halign(Gtk.Align.CENTER)
        self._emoji_container.append(self._state_lbl)

        self._char_name_lbl = Gtk.Label()
        self._char_name_lbl.set_markup(
            f'<b>{GLib.markup_escape_text(self._assistant_name.upper())}</b>'
        )
        self._char_name_lbl.add_css_class("emoji-name-lbl")
        self._char_name_lbl.set_halign(Gtk.Align.CENTER)
        self._emoji_container.append(self._char_name_lbl)

        panel.append(self._emoji_container)

        threading.Thread(
            target=self._download_emoji_assets, daemon=True, name="emoji-dl"
        ).start()

        return panel

    # ─────────────────────────────────────── Character state machine ───────────

    def _idle_state(self):
        """Return the appropriate idle state based on current audio settings."""
        audio_out = (self._db_get("audio_enabled") or "1") == "1"
        audio_in  = (self._db_get("stt_enabled")   or "1") == "1" and not self._privacy_mode
        if not audio_out and not audio_in:
            return "both_muted"
        if not audio_out:
            return "output_muted"
        if not audio_in:
            return "input_muted"
        return "idle"

    def _detect_context_emoji(self, text):
        """Return a contextual emoji state key if the response text warrants one."""
        lower = text.lower()
        for keyword, state in _CONTEXT_EMOJI_MAP.items():
            if keyword in lower:
                return state
        return None

    def _revert_to_idle(self):
        self._set_aura_state(self._idle_state())
        return GLib.SOURCE_REMOVE

    def _set_aura_state(self, state):
        """Switch the character to a new state: update emoji, label, and animation."""
        if state not in AURA_STATES:
            state = "idle"
        if self._aura_state == state:
            return
        prev_state    = self._aura_state
        self._aura_state = state
        info = AURA_STATES[state]

        self._state_lbl.set_text(info["label"])
        self._emoji_label.set_text(info["emoji"])

        # Try Fluent Emoji PNG via Gdk.Texture (GTK 4.12+ preferred over set_pixbuf)
        png_path = os.path.join(EMOJI_DIR, info["file"]) if info["file"] else ""
        if png_path and os.path.exists(png_path):
            try:
                texture = Gdk.Texture.new_from_filename(png_path)
                self._emoji_picture.set_paintable(texture)
                self._emoji_picture.set_visible(True)
                self._emoji_label.set_visible(False)
            except Exception:
                self._emoji_picture.set_visible(False)
                self._emoji_label.set_visible(True)
        else:
            self._emoji_picture.set_visible(False)
            self._emoji_label.set_visible(True)

        # Swap animation CSS class.  Static operational states have named classes;
        # dynamic expression states share the generic aura-state-expression class.
        if prev_state in AURA_STATES:
            prev_css = f"aura-state-{prev_state}" if prev_state in _STATIC_STATES else "aura-state-expression"
            self._emoji_container.remove_css_class(prev_css)
        new_css = f"aura-state-{state}" if state in _STATIC_STATES else "aura-state-expression"
        self._emoji_container.add_css_class(new_css)

    def _download_emoji_assets(self):
        """Download missing Fluent Emoji PNGs; refresh current state when done."""
        import urllib.request
        os.makedirs(EMOJI_DIR, exist_ok=True)
        downloaded_any = False
        seen_files = set()
        for info in AURA_STATES.values():
            fname = info["file"]
            if fname in seen_files:
                continue
            seen_files.add(fname)
            path = os.path.join(EMOJI_DIR, fname)
            if os.path.exists(path):
                continue
            folder_enc = info["folder"].replace(" ", "%20")
            url = f"{FLUENT_BASE}/{folder_enc}/3D/{fname}"
            try:
                urllib.request.urlretrieve(url, path)
                downloaded_any = True
            except Exception:
                pass
        if downloaded_any:
            GLib.idle_add(self._refresh_current_emoji)

    def _download_single_emoji(self, state_key, info):
        """Download one dynamic emoji asset and refresh the panel if it's still current."""
        import urllib.request
        os.makedirs(EMOJI_DIR, exist_ok=True)
        path = os.path.join(EMOJI_DIR, info["file"])
        if os.path.exists(path):
            GLib.idle_add(self._refresh_current_emoji)
            return
        folder_enc = info["folder"].replace(" ", "%20")
        url = f"{FLUENT_BASE}/{folder_enc}/3D/{info['file']}"
        try:
            urllib.request.urlretrieve(url, path)
        except Exception:
            pass  # PNG stays missing; text emoji fallback already showing
        GLib.idle_add(self._refresh_current_emoji)

    def _refresh_current_emoji(self):
        """Reload the emoji for the current state after assets become available."""
        state = self._aura_state
        self._aura_state = "_none_"
        self._set_aura_state(state)
        return GLib.SOURCE_REMOVE

    # ─────────────────────────────────────────────── Chat area ────────────────

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

        self._chat_spacer = Gtk.Box()
        self._chat_spacer.set_vexpand(True)
        self._chat.append(self._chat_spacer)

        self._scroll.set_child(self._chat)
        parent.append(self._scroll)
        self._vadj = self._scroll.get_vadjustment()

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

    # ─────────────────────────────────────────── Mic preference ───────────────

    def _populate_mic_list(self, devices):
        if not self._mic_dropdown:
            return
        options = [("Default", "")]
        for d in devices:
            name  = d.get("name", "")
            short = name.split(":")[0].strip()
            if len(short) > 20:
                short = short[:18] + "…"
            options.append((short, name))
        self._mic_options = options

        self._mic_populating = True
        new_model = Gtk.StringList.new([o[0] for o in options])
        self._mic_dropdown.set_model(new_model)
        expr = Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        self._mic_dropdown.set_expression(expr)

        saved = self._db_get("stt_microphone") or ""
        for i, (_, full_name) in enumerate(options):
            if full_name == saved:
                self._mic_dropdown.set_selected(i)
                break
        self._mic_populating = False

        if len(options) > 1:
            self._mic_select_box.set_visible(True)

    def _on_mic_selected(self, dropdown, _pspec):
        if self._mic_populating:
            return
        idx = dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self._mic_options):
            return
        _, full_name = self._mic_options[idx]
        self._db_set("stt_microphone", full_name)

    def _on_privacy_toggled(self, btn):
        self._privacy_mode = btn.get_active()
        self._db_set("privacy_mode", "1" if self._privacy_mode else "0")
        if self._sock.connected:
            self._sock.send({"type": "set_privacy_mode", "enabled": self._privacy_mode})
        if self._privacy_mode:
            self._set_stt_state("idle")
        self._update_stt_dot()
        idle_states = {"idle", "output_muted", "input_muted", "both_muted"}
        if self._aura_state in idle_states:
            self._aura_state = "_none_"
            self._set_aura_state(self._idle_state())

    def _update_stt_dot(self):
        if not self._stt_icon_lbl:
            return
        for c in ("ready", "privacy"):
            self._stt_icon_lbl.remove_css_class(c)
        if self._privacy_mode:
            self._stt_icon_lbl.add_css_class("privacy")
        elif not self._tts_active and self._stt_current_state in ("idle", "listening"):
            self._stt_icon_lbl.add_css_class("ready")
        # else: grey (no modifier class)

    def _set_stt_state(self, state):
        if not self._stt_icon_lbl or not self._stt_state_lbl:
            return
        self._stt_current_state = state
        if state == "loading":
            self._stt_state_lbl.set_text("loading…")
            self._stt_state_lbl.remove_css_class("stt-state")
            self._stt_state_lbl.add_css_class("stt-loading")
            self._stt_state_lbl.set_visible(True)
        elif state == "listening":
            self._stt_state_lbl.set_text("listening")
            self._stt_state_lbl.remove_css_class("stt-loading")
            self._stt_state_lbl.add_css_class("stt-state")
            self._stt_state_lbl.set_visible(True)
        else:
            self._stt_state_lbl.set_visible(False)
        self._update_stt_dot()

    # ──────────────────────────────────────────────── Chat ────────────────────

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
        GLib.idle_add(self._scroll_to_bottom)

    def _add_status(self, text, css="status-msg"):
        lbl = Gtk.Label()
        lbl.set_markup(f'<i>{GLib.markup_escape_text(text)}</i>')
        lbl.set_xalign(0.5)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_hexpand(True)
        lbl.add_css_class(css)
        self._chat.append(lbl)
        GLib.idle_add(self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        self._vadj.set_value(self._vadj.get_upper() - self._vadj.get_page_size())
        return GLib.SOURCE_REMOVE

    # ──────────────────────────────────────────────── Input ───────────────────

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
            self._set_aura_state("thinking")
        else:
            self._add_status("Not connected to Aura", "err")

    # ─────────────────────────────────────────── Socket messages ──────────────

    def _on_socket_msg(self, msg):
        t = msg.get("type")

        if t == "connected":
            self._conn_lbl.set_text("Connected")
            for c in ("connecting", "disconnected"):
                self._conn_lbl.remove_css_class(c)
            self._conn_lbl.add_css_class("connected")
            self._sock.send({
                "type": "device_query",
                "device_type": "input",
                "request_id": "mic_list",
            })

        elif t == "disconnected":
            self._conn_lbl.set_text("Disconnected")
            for c in ("connecting", "connected"):
                self._conn_lbl.remove_css_class(c)
            self._conn_lbl.add_css_class("disconnected")
            self._set_aura_state(self._idle_state())

        elif t == "device_list":
            if msg.get("request_id") == "mic_list":
                self._populate_mic_list(msg.get("devices", []))

        elif t == "stt_state":
            stt = msg.get("state", "idle")
            self._set_stt_state(stt)
            # STT state changes only override low-priority emoji states
            if stt == "listening" and self._aura_state in _STT_OVERRIDABLE:
                self._set_aura_state("listening")
            elif stt == "loading" and self._aura_state in _STT_OVERRIDABLE:
                self._set_aura_state("loading")
            elif stt == "idle" and self._aura_state in {"listening", "loading"}:
                self._set_aura_state(self._idle_state())

        elif t == "set_aura_state":
            state = msg.get("state", "idle")
            if state == "idle":
                state = self._idle_state()
            self._set_aura_state(state)

        elif t == "set_expression":
            char = msg.get("emoji", "")
            if char:
                # Register in AURA_STATES on first use (key = the emoji char itself)
                if char not in AURA_STATES:
                    info = _fluent_info_for_emoji(char)
                    if info is None:
                        info = {"emoji": char, "label": "", "file": "", "folder": ""}
                    AURA_STATES[char] = info
                    # Kick off asset download in the background
                    threading.Thread(
                        target=self._download_single_emoji,
                        args=(char, AURA_STATES[char]),
                        daemon=True, name="emoji-expr-dl",
                    ).start()
                if self._tts_active or self._aura_state not in _STT_OVERRIDABLE:
                    self._context_emoji = char
                else:
                    self._set_aura_state(char)
                    GLib.timeout_add_seconds(5, self._revert_to_idle)

        elif t == "tts_start":
            self._tts_active = True
            self._update_stt_dot()
            state = self._context_emoji or "speaking"
            self._context_emoji = None
            self._set_aura_state(state)

        elif t == "tts_end":
            self._tts_active = False
            self._update_stt_dot()
            self._set_aura_state(self._idle_state())

        elif t == "stt_transcript":
            text = msg.get("text", "")
            if text:
                self._add_message("user", text)
                self._set_aura_state("thinking")

        elif t == "chat_response":
            text = msg.get("text", "")
            if text:
                self._add_message("aura", text)
                # Keyword detection; tool-set expression takes priority if no keyword match
                ctx = self._detect_context_emoji(text) or self._context_emoji
                self._context_emoji = None
                if (self._db_get("audio_enabled") or "1") != "1":
                    # No TTS — show expression briefly then revert
                    if ctx:
                        self._set_aura_state(ctx)
                        GLib.timeout_add_seconds(4, self._revert_to_idle)
                    else:
                        self._set_aura_state(self._idle_state())
                else:
                    # Store for tts_start to pick up
                    self._context_emoji = ctx

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
            elif key == "battery":
                self._update_battery(val)

        return GLib.SOURCE_REMOVE

    # ─────────────────────────────────────────── System info ──────────────────

    def _tick_clock(self):
        fmt = "%I:%M %p" if self._clock_format == "12" else "%H:%M"
        self._clock_lbl.set_text(datetime.now().strftime(fmt))
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
        try:
            cpu = self._read_cpu_percent()
            if cpu is not None:
                self._cpu_lbl.set_text(f"{cpu:.0f}%")
        except Exception:
            pass
        return GLib.SOURCE_CONTINUE

    def _update_battery(self, val):
        self._batt_lbl.set_text(val)
        self._batt_lbl.set_visible(True)
        for cls in ("battery-ok", "battery-warning", "battery-critical"):
            self._batt_lbl.remove_css_class(cls)
        try:
            digits = "".join(c for c in val if c.isdigit())
            level  = int(digits) if digits else 100
            warn   = int(self._db_get("battery_warning_threshold")  or "20")
            crit   = int(self._db_get("battery_critical_threshold") or "10")
            if level <= crit:
                self._batt_lbl.add_css_class("battery-critical")
            elif level <= warn:
                self._batt_lbl.add_css_class("battery-warning")
            else:
                self._batt_lbl.add_css_class("battery-ok")
        except (ValueError, TypeError):
            self._batt_lbl.add_css_class("battery-ok")

    def _read_cpu_percent(self):
        with open('/proc/stat') as f:
            line = f.readline()
        parts = list(map(int, line.split()[1:8]))
        idle  = parts[3]
        total = sum(parts)
        if self._cpu_prev is None:
            self._cpu_prev = (idle, total)
            return None
        d_idle  = idle  - self._cpu_prev[0]
        d_total = total - self._cpu_prev[1]
        self._cpu_prev = (idle, total)
        if d_total <= 0:
            return 0.0
        return 100.0 * (1.0 - d_idle / d_total)

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

    # ──────────────────────────────────────── Knowledge import ────────────────

    def _open_knowledge_dialog(self, *_):
        self._knowledge_native = Gtk.FileChooserNative.new(
            "Import to Knowledge Base",
            self,
            Gtk.FileChooserAction.OPEN,
            "_Import",
            "_Cancel",
        )
        self._knowledge_native.set_select_multiple(True)

        filter_docs = Gtk.FileFilter()
        filter_docs.set_name("Documents (PDF, DOCX, TXT, MD, CSV)")
        for pat in ["*.pdf", "*.PDF", "*.docx", "*.DOCX",
                    "*.txt", "*.TXT", "*.md", "*.MD", "*.rst", "*.csv"]:
            filter_docs.add_pattern(pat)
        self._knowledge_native.add_filter(filter_docs)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        self._knowledge_native.add_filter(filter_all)

        self._knowledge_native.connect("response", self._on_knowledge_file_response)
        self._knowledge_native.show()

    def _on_knowledge_file_response(self, dialog, response):
        self._knowledge_native = None
        if response != Gtk.ResponseType.ACCEPT:
            return
        import shutil
        upload_dir = os.path.expanduser("~/knowledge/upload")
        os.makedirs(upload_dir, exist_ok=True)
        files = dialog.get_files()
        n = files.get_n_items()
        imported = []
        for i in range(n):
            f = files.get_item(i)
            path = f.get_path()
            if not path:
                continue
            fname = os.path.basename(path)
            dest  = os.path.join(upload_dir, fname)
            try:
                import shutil as _sh
                _sh.copy2(path, dest)
                imported.append(fname)
            except Exception as e:
                self._add_status(f"Failed to copy {fname}: {e}", "err")
        if imported:
            preview = ", ".join(imported[:3]) + ("…" if len(imported) > 3 else "")
            self._add_status(
                f"Queued {len(imported)} file(s) for knowledge base import: {preview}",
                "status-msg",
            )
            if self._sock.connected:
                self._sock.send({"type": "process_knowledge"})

    # ──────────────────────────────────────────── Settings ────────────────────

    def _toggle_fullscreen(self):
        if self._is_fullscreen:
            self.unfullscreen()
            self._is_fullscreen = False
        else:
            self.fullscreen()
            self._is_fullscreen = True

    def _open_settings(self, *_):
        if self._active_settings:
            return
        try:
            page = SettingsPage(self, self._close_settings)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._add_status(f"Settings error: {exc}", "err")
            return
        page.set_hexpand(True)
        page.set_vexpand(True)
        page.set_halign(Gtk.Align.FILL)
        page.set_valign(Gtk.Align.FILL)
        self._settings_overlay.add_overlay(page)
        self._active_settings = page
        # Stop the chat entry from stealing focus while settings is open
        self._entry.set_focusable(False)
        GLib.idle_add(page.grab_focus)

    def _close_settings(self):
        if self._active_settings:
            self._active_settings.unparent()
            self._active_settings = None
        self._entry.set_focusable(True)
        GLib.idle_add(self._entry.grab_focus)

    def _on_settings_saved(self, changed):
        if "theme" in changed:
            _apply_theme(changed["theme"])
        if "clock_format" in changed:
            self._clock_format = changed["clock_format"]
            self._tick_clock()
        if "assistant_name" in changed:
            self._assistant_name = changed["assistant_name"]
            markup = f'<b>{GLib.markup_escape_text(self._assistant_name.upper())}</b>'
            self._title_lbl.set_markup(markup)
            self._char_name_lbl.set_markup(markup)
        if "user_informal_name" in changed:
            self._user_name = changed["user_informal_name"] or "You"
        if "audio_enabled" in changed or "stt_enabled" in changed:
            idle_states = {"idle", "output_muted", "input_muted", "both_muted"}
            if self._aura_state in idle_states:
                self._aura_state = "_none_"  # force re-apply
                self._set_aura_state(self._idle_state())

    def do_close_request(self):
        self._sock.stop()
        return False


# ──────────────────────────────────────────────── App ─────────────────────────

class AuraApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="ai.aura.interface")
        self.connect("activate", self._on_activate)

    def _on_activate(self, _):
        global _css_provider
        _css_provider = Gtk.CssProvider()
        try:
            with sqlite3.connect(DB_PATH) as _conn:
                _row = _conn.execute("SELECT value FROM config WHERE key='theme'").fetchone()
            _apply_theme(_row[0] if _row else "dark")
        except Exception:
            _apply_theme("dark")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            _css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        win = AuraWindow(application=self)
        win.present()


if __name__ == "__main__":
    sys.exit(AuraApp().run(sys.argv))
