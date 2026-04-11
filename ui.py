# ui.py
# AURA UI — Kivy-based tile interface
# Connects to Aura via Unix socket (/tmp/aura.sock)
# Responsive tile grid, toggleable on-screen keyboard

import os
import sys
import json
import socket
import threading
import queue

sys.path.insert(0, '/home/aura/aura')
import db
db.init_db()

# Tell Kivy to use Wayland/SDL2
os.environ.setdefault('KIVY_WINDOW', 'sdl2')
os.environ.setdefault('KIVY_GL_BACKEND', 'sdl2')
# os.environ.setdefault('SDL_VIDEODRIVER', 'wayland')

from kivy.config import Config
Config.set('graphics', 'fullscreen', 'auto')
Config.set('graphics', 'show_cursor', '1')
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')

from kivy.app import App
from kivy.uix.widget import Widget
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.vkeyboard import VKeyboard
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.relativelayout import RelativeLayout
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.core.window import Window
from kivy.animation import Animation

# --- Colour palette ---
C_BG        = (0.08, 0.08, 0.12, 1)   # Dark background
C_TILE      = (0.13, 0.13, 0.20, 1)   # Tile background
C_TILE_HL   = (0.18, 0.18, 0.28, 1)   # Tile highlighted
C_ACCENT    = (0.20, 0.50, 0.90, 1)   # Blue accent
C_ACCENT2   = (0.10, 0.70, 0.50, 1)   # Green accent
C_TEXT      = (0.95, 0.95, 0.95, 1)   # Primary text
C_TEXT_DIM  = (0.55, 0.55, 0.65, 1)   # Dimmed text
C_WARN      = (0.90, 0.60, 0.10, 1)   # Warning amber
C_ERROR     = (0.90, 0.20, 0.20, 1)   # Error red
C_INPUT_BG  = (0.10, 0.10, 0.16, 1)   # Input background
C_BAR_BG    = (0.06, 0.06, 0.10, 1)   # Bottom bar background

SOCKET_PATH = "/tmp/aura.sock"

# --- Socket client ---
class SocketClient:
    def __init__(self, on_message):
        self.on_message  = on_message
        self._sock       = None
        self._running    = False
        self._send_queue = queue.Queue()
        self._buf        = ""
        self.connected   = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="ui-socket").start()

    def stop(self):
        self._running = False

    def send(self, msg):
        self._send_queue.put(msg)

    def _loop(self):
        while self._running:
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.connect(SOCKET_PATH)
                self.connected = True
                Clock.schedule_once(lambda dt: self.on_message({"type": "connected"}))
                self._sock.settimeout(0.2)
                self._buf = ""
                while self._running:
                    # Send queued messages
                    while not self._send_queue.empty():
                        try:
                            m = self._send_queue.get_nowait()
                            self._sock.sendall((json.dumps(m) + "\n").encode())
                        except queue.Empty:
                            break
                    # Receive
                    try:
                        data = self._sock.recv(4096)
                        if not data:
                            break
                        self._buf += data.decode("utf-8", errors="replace")
                        while "\n" in self._buf:
                            line, self._buf = self._buf.split("\n", 1)
                            line = line.strip()
                            if line:
                                try:
                                    msg = json.loads(line)
                                    Clock.schedule_once(lambda dt, m=msg: self.on_message(m))
                                except json.JSONDecodeError:
                                    pass
                    except socket.timeout:
                        pass
            except Exception as e:
                self.connected = False
                Clock.schedule_once(lambda dt: self.on_message({"type": "disconnected"}))
            finally:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self.connected = False
            # Retry after 2s
            import time
            time.sleep(2)

# --- Tile base ---
class Tile(RelativeLayout):
    def __init__(self, title="", **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self._bg_color = list(C_TILE)
        with self.canvas.before:
            self._color_inst = Color(*self._bg_color)
            self._rect = RoundedRectangle(pos=(0,0), size=self.size, radius=[dp(8)])
        self.bind(size=self._update_rect, pos=self._update_rect)
        # Title label
        if title:
            self._title_lbl = Label(
                text=title.upper(),
                font_size=sp(10),
                color=C_TEXT_DIM,
                bold=True,
                size_hint=(1, None),
                height=dp(20),
                pos_hint={"top": 1},
                halign="left",
                padding_x=dp(10)
            )
            self._title_lbl.bind(size=self._title_lbl.setter('text_size'))
            self.add_widget(self._title_lbl)

    def _update_rect(self, *a):
        self._rect.pos  = (0, 0)
        self._rect.size = self.size

    def set_color(self, color):
        self._bg_color = list(color)
        self._color_inst.rgba = color

# --- Conversation tile ---
class ConversationTile(Tile):
    def __init__(self, **kwargs):
        self._assistant_name = db.get('assistant_name') or 'Aura'
        self._user_name      = db.get('user_informal_name') or 'You'
        super().__init__(title=self._assistant_name, **kwargs)
        self._messages = []
        layout = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[dp(8), dp(24), dp(8), dp(8)],
            spacing=dp(4)
        )
        self._scroll = ScrollView(size_hint=(1, 1))
        self._msg_layout = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(6),
            padding=[0, dp(4)]
        )
        self._msg_layout.bind(minimum_height=self._msg_layout.setter("height"))
        self._scroll.add_widget(self._msg_layout)
        layout.add_widget(self._scroll)
        self.add_widget(layout)

    def add_message(self, role, text):
        is_user = role == "user"
        color   = C_TEXT if not is_user else (0.85, 0.85, 1.0, 1)
        prefix  = f"{self._user_name}: " if is_user else f"{self._assistant_name}: "
        lbl = Label(
            text=f"[b]{prefix}[/b]{text}" if is_user else text,
            markup=True,
            font_size=sp(14),
            color=color,
            size_hint=(1, None),
            halign="left",
            valign="top",
            text_size=(None, None),
            padding=[dp(4), dp(2)]
        )
        lbl.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        lbl.bind(texture_size=lambda inst, ts: setattr(inst, 'height', ts[1]))
        self._msg_layout.add_widget(lbl)
        Clock.schedule_once(lambda dt: setattr(self._scroll, 'scroll_y', 0), 0.1)

    def set_status(self, text, color=None):
        lbl = Label(
            text=text,
            font_size=sp(12),
            color=color or C_TEXT_DIM,
            size_hint=(1, None),
            height=dp(20),
            halign="center",
            italic=True
        )
        self._msg_layout.add_widget(lbl)

# --- Status tile ---
class StatusTile(Tile):
    def __init__(self, key="", unit="", icon="", **kwargs):
        super().__init__(**kwargs)
        self.key  = key
        self.unit = unit
        inner = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[dp(8), dp(24), dp(8), dp(8)]
        )
        self._value_lbl = Label(
            text="--",
            font_size=sp(28),
            bold=True,
            color=C_ACCENT,
            size_hint=(1, 0.6),
            halign="center"
        )
        self._unit_lbl = Label(
            text=unit,
            font_size=sp(11),
            color=C_TEXT_DIM,
            size_hint=(1, 0.4),
            halign="center"
        )
        inner.add_widget(self._value_lbl)
        inner.add_widget(self._unit_lbl)
        self.add_widget(inner)

    def update(self, value, warn=False, error=False):
        self._value_lbl.text = str(value)
        if error:
            self._value_lbl.color = C_ERROR
        elif warn:
            self._value_lbl.color = C_WARN
        else:
            self._value_lbl.color = C_ACCENT

# --- Clock tile ---
class ClockTile(Tile):
    def __init__(self, **kwargs):
        super().__init__(title="Time", **kwargs)
        inner = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[dp(8), dp(24), dp(8), dp(4)]
        )
        self._time_lbl = Label(
            text="--:--",
            font_size=sp(32),
            bold=True,
            color=C_TEXT,
            size_hint=(1, 0.6),
            halign="center"
        )
        self._date_lbl = Label(
            text="---",
            font_size=sp(11),
            color=C_TEXT_DIM,
            size_hint=(1, 0.4),
            halign="center"
        )
        inner.add_widget(self._time_lbl)
        inner.add_widget(self._date_lbl)
        self.add_widget(inner)
        Clock.schedule_interval(self._tick, 1)

    def _tick(self, dt):
        from datetime import datetime
        now = datetime.now()
        self._time_lbl.text = now.strftime("%H:%M")
        self._date_lbl.text = now.strftime("%a %d %b")

# --- Connection indicator tile ---
class ConnectionTile(Tile):
    def __init__(self, **kwargs):
        super().__init__(title="Aura", **kwargs)
        inner = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[dp(8), dp(24), dp(8), dp(4)],
            spacing=dp(4)
        )
        self._status_lbl = Label(
            text="Connecting...",
            font_size=sp(12),
            color=C_WARN,
            size_hint=(1, 0.5),
            halign="center",
            valign="middle"
        )
        self._sys_msg_lbl = Label(
            text="",
            font_size=sp(11),
            color=C_TEXT_DIM,
            size_hint=(1, 0.5),
            halign="center",
            valign="top",
            italic=True
        )
        self._sys_msg_lbl.bind(size=self._sys_msg_lbl.setter('text_size'))
        inner.add_widget(self._status_lbl)
        inner.add_widget(self._sys_msg_lbl)
        self.add_widget(inner)

    def set_connected(self, connected):
        if connected:
            self._status_lbl.text  = "Connected"
            self._status_lbl.color = C_ACCENT2
            self.set_color(C_TILE)
        else:
            self._status_lbl.text  = "Disconnected"
            self._status_lbl.color = C_ERROR
            self.set_color((0.18, 0.08, 0.08, 1))

    def add_system_message(self, text, level="info"):
        color_map = {
            "info":    C_TEXT_DIM,
            "warning": C_WARN,
            "error":   C_ERROR,
        }
        self._sys_msg_lbl.text  = text
        self._sys_msg_lbl.color = color_map.get(level, C_TEXT_DIM)

# --- Input bar ---
class InputBar(BoxLayout):
    def __init__(self, on_send, on_keyboard_toggle, **kwargs):
        super().__init__(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(52),
            padding=[dp(8), dp(6)],
            spacing=dp(6),
            **kwargs
        )
        with self.canvas.before:
            Color(*C_BAR_BG)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)

        self._input = TextInput(
            hint_text="Say something...",
            multiline=False,
            size_hint=(1, 1),
            background_color=C_INPUT_BG,
            foreground_color=C_TEXT,
            hint_text_color=list(C_TEXT_DIM),
            cursor_color=C_ACCENT,
            font_size=sp(15),
            padding=[dp(10), dp(10)]
        )
        self._input.bind(on_text_validate=lambda inst: on_send(inst.text))

        send_btn = Button(
            text="Send",
            size_hint=(None, 1),
            width=dp(70),
            background_color=C_ACCENT,
            background_normal="",
            color=C_TEXT,
            font_size=sp(14),
            bold=True
        )
        send_btn.bind(on_press=lambda inst: on_send(self._input.text))

        kbd_btn = Button(
            text="⌨",
            size_hint=(None, 1),
            width=dp(44),
            background_color=C_TILE_HL,
            background_normal="",
            color=C_TEXT,
            font_size=sp(18)
        )
        kbd_btn.bind(on_press=lambda inst: on_keyboard_toggle())

        self.add_widget(self._input)
        self.add_widget(send_btn)
        self.add_widget(kbd_btn)

    def _upd(self, *a):
        self._rect.pos  = self.pos
        self._rect.size = self.size

    def clear(self):
        self._input.text = ""

    def get_text(self):
        return self._input.text

# --- Main app ---
class AuraUI(App):
    def build(self):
        Window.clearcolor = C_BG

        self._socket      = SocketClient(self._on_socket_message)
        self._kbd_visible = False
        self._msg_id      = 0

        # Root layout
        self._root = BoxLayout(orientation="vertical")

        # Tile grid (fills available space)
        self._grid_scroll = ScrollView(size_hint=(1, 1))
        self._grid = GridLayout(
            cols=2,
            size_hint=(1, None),
            spacing=dp(6),
            padding=dp(6)
        )
        self._grid.bind(minimum_height=self._grid.setter("height"))
        self._grid_scroll.add_widget(self._grid)
        self._root.add_widget(self._grid_scroll)

        # On-screen keyboard (hidden initially)
        self._vkbd = VKeyboard(
            size_hint=(1, None),
            height=dp(250),
            layout='qwerty'
        )
        self._vkbd_visible = False

        # Input bar
        self._input_bar = InputBar(
            on_send=self._on_send,
            on_keyboard_toggle=self._toggle_keyboard
        )
        self._root.add_widget(self._input_bar)

        # Wire OSK to the text input
        self._vkbd.target = self._input_bar._input
        self._vkbd.bind(on_key_up=self._on_vkbd_key)

        # Build tiles
        self._build_tiles()

        # Bind window resize
        Window.bind(on_resize=self._on_resize)

        # Start socket
        self._socket.start()

        # Update grid columns on start
        Clock.schedule_once(lambda dt: self._update_cols(), 0.1)

        return self._root

    def _build_tiles(self):
        self._grid.clear_widgets()

        # Conversation tile — spans full width (col span via size)
        self._conv_tile = ConversationTile(
            size_hint=(1, None),
            height=dp(320)
        )
        self._conn_tile = ConnectionTile(
            size_hint=(1, None),
            height=dp(120)
        )
        self._clock_tile = ClockTile(
            size_hint=(1, None),
            height=dp(120)
        )
        self._cpu_tile = StatusTile(
            title="CPU Temp",
            key="cpu_temp",
            unit="°C",
            size_hint=(1, None),
            height=dp(120)
        )
        self._mem_tile = StatusTile(
            title="Memory",
            key="memory",
            unit="MB used",
            size_hint=(1, None),
            height=dp(120)
        )

        self._grid.add_widget(self._conv_tile)
        self._grid.add_widget(self._conn_tile)
        self._grid.add_widget(self._clock_tile)
        self._grid.add_widget(self._cpu_tile)
        self._grid.add_widget(self._mem_tile)

    def _update_cols(self, *a):
        w = Window.width
        if w < dp(500):
            self._grid.cols = 1
        elif w < dp(800):
            self._grid.cols = 2
        else:
            self._grid.cols = 3

    def _on_resize(self, win, w, h):
        self._update_cols()

    def _on_send(self, text):
        text = text.strip()
        if not text:
            return
        self._input_bar.clear()
        self._conv_tile.add_message("user", text)
        if self._socket.connected:
            self._msg_id += 1
            self._socket.send({
                "type": "chat_input",
                "text": text,
                "id":   str(self._msg_id)
            })
        else:
# TODO - Change "Aura" to users preferred AI Name
            self._conv_tile.set_status("Not connected to Aura", C_ERROR)

    def _toggle_keyboard(self):
        if self._vkbd_visible:
            self._root.remove_widget(self._vkbd)
            self._vkbd_visible = False
        else:
            # Insert keyboard above input bar
            idx = self._root.children.index(self._input_bar)
            self._root.add_widget(self._vkbd, index=idx)
            self._vkbd_visible = True
            self._input_bar._input.focus = True

    def _on_vkbd_key(self, keyboard, keycode):
        self._input_bar._input.focus = True

    def _on_socket_message(self, msg):
        msg_type = msg.get("type")
        if msg_type == "connected":
            self._conn_tile.set_connected(True)
# TODO - Change "Aura" to users preferred AI Name
            self._conv_tile.set_status("Connected to Aura", C_ACCENT2)
        elif msg_type == "disconnected":
            self._conn_tile.set_connected(False)
            self._conn_tile.add_system_message("Aura disconnected — retrying...", "warning")
        elif msg_type == "chat_response":
            text = msg.get("text", "")
            if text:
                self._conv_tile.add_message("aura", text)
        elif msg_type == "system_message":
            text  = msg.get("text", "")
            level = msg.get("level", "info")
            self._conn_tile.add_system_message(text, level)
        elif msg_type == "status_update":
            key   = msg.get("key", "")
            value = msg.get("value", "")
            if key == "cpu_temp":
                warn  = float(value) > 70 if value.replace('.','').isdigit() else False
                error = float(value) > 80 if value.replace('.','').isdigit() else False
                self._cpu_tile.update(value, warn=warn, error=error)
            elif key == "memory":
                self._mem_tile.update(value)

    def on_stop(self):
        self._socket.stop()

if __name__ == "__main__":
    AuraUI().run()
