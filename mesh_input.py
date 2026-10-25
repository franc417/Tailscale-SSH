#!/usr/bin/env python3
"""Mesh input channel, host side (protocol "MIP" v1).

The phone sends touches and keys to the computer over its own small protocol
instead of borrowing Sunshine's input path. Why: no dependence on another
project's input quirks, true key down/up (hold-to-repeat, Shift-click, Ctrl-drag),
exact text, and it works on every distro with nothing to install: this file uses
only the Python standard library and talks to the kernel's /dev/uinput directly.

Security (an input channel is full control of the computer):
  * TLS (1.2 or newer; Android 8 and 9 cannot do 1.3). The agent makes a self-signed certificate once; the phone learns its
    SHA-256 fingerprint over the SSH pairing step and PINS it, so nobody can sit
    in the middle even on an open LAN.
  * A random 32-byte pairing token, sent inside the encrypted channel in HELLO and
    compared in constant time. Without it the connection is closed.
  * Only one phone at a time; a new authenticated phone replaces the old one.
  * Every key and button the phone pressed is released when it disconnects or goes
    quiet, so a dropped connection can never leave a key stuck down.

Wire format: frame = u16 length (of type + payload, big-endian) | u8 type | payload.
See docs/MESH-INPUT-PROTOCOL.md.

Commands:
  python3 mesh_input.py init                 create the certificate and token
  python3 mesh_input.py pair-info            print what the phone needs, as JSON
  python3 mesh_input.py sunshine-login       save Sunshine's web login once, so the phone pairs its desktop view with no PIN
  python3 mesh_input.py run [--port N]       serve (needs /dev/uinput access)
  python3 mesh_input.py install-service      start at every login and restart on failure (systemd user service)
  python3 mesh_input.py uninstall-service    stop and remove that service
"""

import argparse
import asyncio
import base64
import fcntl
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

VERSION = 1
DEFAULT_PORT = 47600
MAX_FRAME = 4096          # type + payload
MAX_TEXT_BYTES = 1024
HELLO_TIMEOUT = 3.0       # seconds to send HELLO after connecting
IDLE_TIMEOUT = 15.0       # seconds of silence before the phone is considered gone
TOKEN_BYTES = 32

# ---- message types: phone -> host
T_HELLO = 0x01
T_MOVE = 0x02
T_SCROLL = 0x03
T_BUTTON = 0x04
T_KEY = 0x05
T_TEXT = 0x06
T_PING = 0x07
T_RELEASE_ALL = 0x08
T_BYE = 0x09
T_SUNSHINE_PAIR = 0x0A    # "pair my desktop view with this PIN" (see SunshineLink)
# ---- host -> phone
T_HELLO_OK = 0x81
T_ERROR = 0x82
T_PONG = 0x87
T_SUNSHINE_RESULT = 0x8A

# error codes
E_BAD_TOKEN = 1
E_BAD_VERSION = 2
E_REPLACED = 3
E_BAD_FRAME = 4
E_NO_HELLO = 5

FEATURE_TEXT = 0x01
FEATURE_HIRES_SCROLL = 0x02
FEATURE_SUNSHINE_PAIR = 0x04

# T_SUNSHINE_RESULT status codes
S_OK = 0                # the phone is paired with Sunshine
S_NOT_CONFIGURED = 1    # this agent has no saved Sunshine login yet
S_UNREACHABLE = 2       # Sunshine's web interface does not answer
S_LOGIN_REFUSED = 3     # the saved login is not accepted (changed in Sunshine?)
S_FAILED = 4            # no pairing request arrived, the PIN was wrong, or it timed out
S_BAD_REQUEST = 5       # the PIN is not 4 digits

BUTTON_NAMES = {1: "left", 2: "middle", 3: "right", 4: "back", 5: "forward"}


class ProtocolError(Exception):
    pass


# ---------------------------------------------------------------- framing

def encode_frame(msg_type: int, payload: bytes = b"") -> bytes:
    size = 1 + len(payload)
    if size > MAX_FRAME:
        raise ProtocolError(f"frame too large ({size} bytes)")
    return struct.pack(">HB", size, msg_type) + payload


async def read_frame(reader: asyncio.StreamReader) -> Tuple[int, bytes]:
    header = await reader.readexactly(2)
    (size,) = struct.unpack(">H", header)
    if size < 1 or size > MAX_FRAME:
        raise ProtocolError(f"bad frame size {size}")
    body = await reader.readexactly(size)
    return body[0], body[1:]


def _short_bytes(data: bytes, what: str) -> bytes:
    if len(data) > 255:
        raise ProtocolError(f"{what} too long")
    return bytes([len(data)]) + data


def _take_short(payload: bytes, offset: int, what: str) -> Tuple[bytes, int]:
    if offset >= len(payload):
        raise ProtocolError(f"{what}: truncated")
    n = payload[offset]
    end = offset + 1 + n
    if end > len(payload):
        raise ProtocolError(f"{what}: truncated")
    return payload[offset + 1:end], end


# ---- builders (the real phone app has the same ones in Kotlin; tests use these)

def msg_hello(token: bytes, name: str, version: int = VERSION) -> bytes:
    return encode_frame(T_HELLO, bytes([version]) + _short_bytes(token, "token") + _short_bytes(name.encode(), "name"))


def msg_move(dx: float, dy: float) -> bytes:
    return encode_frame(T_MOVE, struct.pack(">ff", dx, dy))


def msg_scroll(vertical: float, horizontal: float = 0.0) -> bytes:
    return encode_frame(T_SCROLL, struct.pack(">ff", vertical, horizontal))


def msg_button(button: int, down: bool) -> bytes:
    return encode_frame(T_BUTTON, bytes([button, 1 if down else 0]))


def msg_key(code: str, down: bool) -> bytes:
    return encode_frame(T_KEY, bytes([1 if down else 0]) + _short_bytes(code.encode("ascii"), "key code"))


def msg_text(text: str) -> bytes:
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise ProtocolError("text too long")
    return encode_frame(T_TEXT, data)


def msg_ping(ping_id: int) -> bytes:
    return encode_frame(T_PING, struct.pack(">I", ping_id))


def msg_release_all() -> bytes:
    return encode_frame(T_RELEASE_ALL)


def msg_bye() -> bytes:
    return encode_frame(T_BYE)


def msg_sunshine_pair(pin: str, name: str) -> bytes:
    return encode_frame(T_SUNSHINE_PAIR, _short_bytes(pin.encode("ascii"), "pin") + _short_bytes(name.encode()[:128], "name"))


def msg_sunshine_result(code: int, text: str) -> bytes:
    return encode_frame(T_SUNSHINE_RESULT, bytes([code]) + _short_bytes(text.encode()[:200], "result text"))


def msg_hello_ok(host_name: str, features: int = FEATURE_TEXT | FEATURE_HIRES_SCROLL | FEATURE_SUNSHINE_PAIR) -> bytes:
    return encode_frame(T_HELLO_OK, bytes([VERSION, features]) + _short_bytes(host_name.encode(), "host name"))


def msg_error(code: int, text: str) -> bytes:
    return encode_frame(T_ERROR, bytes([code]) + _short_bytes(text.encode()[:200], "error text"))


def msg_pong(ping_id: int) -> bytes:
    return encode_frame(T_PONG, struct.pack(">I", ping_id))


# ---- parsers

def parse_hello(payload: bytes) -> Tuple[int, bytes, str]:
    if len(payload) < 1:
        raise ProtocolError("hello: truncated")
    version = payload[0]
    token, end = _take_short(payload, 1, "hello token")
    name, end = _take_short(payload, end, "hello name")
    return version, token, name.decode("utf-8", "replace")


def _two_floats(payload: bytes, what: str) -> Tuple[float, float]:
    if len(payload) != 8:
        raise ProtocolError(f"{what}: expected 8 bytes, got {len(payload)}")
    a, b = struct.unpack(">ff", payload)
    if not (math.isfinite(a) and math.isfinite(b)):
        raise ProtocolError(f"{what}: not a finite number")
    return a, b


def parse_move(payload: bytes) -> Tuple[float, float]:
    dx, dy = _two_floats(payload, "move")
    if abs(dx) > 20000 or abs(dy) > 20000:
        raise ProtocolError("move: delta out of range")
    return dx, dy


def parse_scroll(payload: bytes) -> Tuple[float, float]:
    v, h = _two_floats(payload, "scroll")
    if abs(v) > 1000 or abs(h) > 1000:
        raise ProtocolError("scroll: delta out of range")
    return v, h


def parse_button(payload: bytes) -> Tuple[int, bool]:
    if len(payload) != 2 or payload[0] not in BUTTON_NAMES or payload[1] not in (0, 1):
        raise ProtocolError("button: bad payload")
    return payload[0], bool(payload[1])


def parse_key(payload: bytes) -> Tuple[str, bool]:
    if len(payload) < 2 or payload[0] not in (0, 1):
        raise ProtocolError("key: bad payload")
    code, end = _take_short(payload, 1, "key code")
    if end != len(payload):
        raise ProtocolError("key: trailing bytes")
    return code.decode("ascii", "replace"), bool(payload[0])


def parse_sunshine_pair(payload: bytes) -> Tuple[str, str]:
    pin, end = _take_short(payload, 0, "pin")
    name, end = _take_short(payload, end, "name")
    try:
        return pin.decode("ascii"), name.decode("utf-8", "replace")
    except UnicodeDecodeError as exc:
        raise ProtocolError("pin: not ASCII") from exc


def parse_text(payload: bytes) -> str:
    if len(payload) > MAX_TEXT_BYTES:
        raise ProtocolError("text too long")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("text: not valid UTF-8") from exc


def parse_id(payload: bytes) -> int:
    if len(payload) != 4:
        raise ProtocolError("ping: bad payload")
    return struct.unpack(">I", payload)[0]


# ---------------------------------------------------------------- keys

# Linux input-event-codes.h (KEY_*, BTN_*). Names follow the web KeyboardEvent.code
# vocabulary the phone keyboard already uses, so one vocabulary runs end to end.
KEY_CODES: Dict[str, int] = {
    "Escape": 1, "Digit1": 2, "Digit2": 3, "Digit3": 4, "Digit4": 5, "Digit5": 6,
    "Digit6": 7, "Digit7": 8, "Digit8": 9, "Digit9": 10, "Digit0": 11,
    "Minus": 12, "Equal": 13, "Backspace": 14, "Tab": 15,
    "KeyQ": 16, "KeyW": 17, "KeyE": 18, "KeyR": 19, "KeyT": 20, "KeyY": 21,
    "KeyU": 22, "KeyI": 23, "KeyO": 24, "KeyP": 25, "BracketLeft": 26, "BracketRight": 27,
    "Enter": 28, "ControlLeft": 29,
    "KeyA": 30, "KeyS": 31, "KeyD": 32, "KeyF": 33, "KeyG": 34, "KeyH": 35,
    "KeyJ": 36, "KeyK": 37, "KeyL": 38, "Semicolon": 39, "Quote": 40, "Backquote": 41,
    "ShiftLeft": 42, "Backslash": 43,
    "KeyZ": 44, "KeyX": 45, "KeyC": 46, "KeyV": 47, "KeyB": 48, "KeyN": 49, "KeyM": 50,
    "Comma": 51, "Period": 52, "Slash": 53, "ShiftRight": 54,
    "AltLeft": 56, "Space": 57, "CapsLock": 58,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66,
    "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "ControlRight": 97, "PrintScreen": 99, "AltRight": 100,
    "Home": 102, "ArrowUp": 103, "PageUp": 104, "ArrowLeft": 105, "ArrowRight": 106,
    "End": 107, "ArrowDown": 108, "PageDown": 109, "Insert": 110, "Delete": 111,
    "Meta": 125, "MetaLeft": 125, "MetaRight": 126, "ContextMenu": 127,
}
# The sticky-modifier names the phone's keyboard uses.
KEY_CODES.update({"ctrl": 29, "shift": 42, "alt": 56, "meta": 125})

BUTTON_CODES = {1: 0x110, 2: 0x112, 3: 0x111, 4: 0x113, 5: 0x114}  # BTN_LEFT/MIDDLE/RIGHT/SIDE/EXTRA

EV_SYN, EV_KEY, EV_REL = 0, 1, 2
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x0B, 0x0C

# US-layout characters typed with key events (text on other layouts, and everything
# outside ASCII, goes through the Unicode compose sequence below).
_PLAIN = {c: f"Key{c.upper()}" for c in "abcdefghijklmnopqrstuvwxyz"}
_PLAIN.update({d: f"Digit{d}" for d in "0123456789"})
_PLAIN.update({
    " ": "Space", "\n": "Enter", "\t": "Tab", "-": "Minus", "=": "Equal", "[": "BracketLeft",
    "]": "BracketRight", "\\": "Backslash", ";": "Semicolon", "'": "Quote", "`": "Backquote",
    ",": "Comma", ".": "Period", "/": "Slash",
})
_SHIFTED = {c.upper(): f"Key{c.upper()}" for c in "abcdefghijklmnopqrstuvwxyz"}
_SHIFTED.update({
    "!": "Digit1", "@": "Digit2", "#": "Digit3", "$": "Digit4", "%": "Digit5", "^": "Digit6",
    "&": "Digit7", "*": "Digit8", "(": "Digit9", ")": "Digit0", "_": "Minus", "+": "Equal",
    "{": "BracketLeft", "}": "BracketRight", "|": "Backslash", ":": "Semicolon", '"': "Quote",
    "~": "Backquote", "<": "Comma", ">": "Period", "?": "Slash",
})


def text_events(text: str) -> List[Tuple[str, bool]]:
    """The key events that type `text`: [(code, down), ...].

    ASCII goes out as real key presses (US layout). Anything else uses the Unicode
    entry sequence Ctrl+Shift+U, hex digits, Space, which GTK/Qt apps with an input
    method (IBus, fcitx) understand. That path depends on the desktop's input
    method; it is the same compromise Sunshine's own text input makes on Linux."""
    events: List[Tuple[str, bool]] = []

    def tap(code: str, shift: bool = False) -> None:
        if shift:
            events.append(("ShiftLeft", True))
        events.append((code, True))
        events.append((code, False))
        if shift:
            events.append(("ShiftLeft", False))

    for ch in text:
        if ch in _PLAIN:
            tap(_PLAIN[ch])
        elif ch in _SHIFTED:
            tap(_SHIFTED[ch], shift=True)
        elif ch == "\r":
            continue
        else:
            events.append(("ControlLeft", True))
            events.append(("ShiftLeft", True))
            events.append(("KeyU", True))
            events.append(("KeyU", False))
            events.append(("ShiftLeft", False))
            events.append(("ControlLeft", False))
            for digit in format(ord(ch), "x"):
                tap(f"Digit{digit}" if digit.isdigit() else f"Key{digit.upper()}")
            tap("Space")
    return events


class Carry:
    """Turns fractional deltas into whole counts without losing the remainder, so a
    slow drag made of sub-pixel steps still moves the cursor."""

    def __init__(self) -> None:
        self._rest = 0.0

    def take(self, value: float) -> int:
        total = self._rest + value
        whole = int(total)  # toward zero, so the remainder keeps its sign
        self._rest = total - whole
        return whole


# ---------------------------------------------------------------- injectors

class Injector:
    """What the server drives. UinputInjector is the real one; tests use a recorder."""

    def key(self, code: str, down: bool) -> None: ...
    def button(self, button: int, down: bool) -> None: ...
    def move(self, dx: float, dy: float) -> None: ...
    def scroll(self, vertical: float, horizontal: float) -> None: ...
    def text(self, text: str) -> None: ...
    def release_all(self) -> None: ...
    def close(self) -> None: ...


class StatefulInjector(Injector):
    """Shared bookkeeping: remembers what is held down so release_all can let go of it."""

    def __init__(self) -> None:
        self.keys_down = set()
        self.buttons_down = set()

    def key(self, code: str, down: bool) -> None:
        if code not in KEY_CODES:
            return  # unknown key: skip, never crash (matches the desktop client's choice)
        (self.keys_down.add if down else self.keys_down.discard)(code)
        self._key(code, down)

    def button(self, button: int, down: bool) -> None:
        if button not in BUTTON_CODES:
            return
        (self.buttons_down.add if down else self.buttons_down.discard)(button)
        self._button(button, down)

    def text(self, text: str) -> None:
        for code, down in text_events(text):
            self._key(code, down)

    def release_all(self) -> None:
        for code in sorted(self.keys_down):
            self._key(code, False)
        for button in sorted(self.buttons_down):
            self._button(button, False)
        self.keys_down.clear()
        self.buttons_down.clear()

    def _key(self, code: str, down: bool) -> None:
        raise NotImplementedError

    def _button(self, button: int, down: bool) -> None:
        raise NotImplementedError


class RecordingInjector(StatefulInjector):
    """Records every event instead of injecting it (tests, and --dry-run, where
    `on_event` prints each one as a JSON line so another program can check it)."""

    def __init__(self, on_event: Optional[Callable[[tuple], None]] = None) -> None:
        super().__init__()
        self.events: List[tuple] = []
        self._x, self._y = Carry(), Carry()
        self._on_event = on_event

    def _record(self, event: tuple) -> None:
        self.events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def _key(self, code: str, down: bool) -> None:
        self._record(("key", code, down))

    def _button(self, button: int, down: bool) -> None:
        self._record(("button", button, down))

    def move(self, dx: float, dy: float) -> None:
        ix, iy = self._x.take(dx), self._y.take(dy)
        if ix or iy:
            self._record(("move", ix, iy))

    def scroll(self, vertical: float, horizontal: float) -> None:
        self._record(("scroll", vertical, horizontal))

    def close(self) -> None:
        pass


def _ioc(direction: int, type_char: str, nr: int, size: int) -> int:
    """Linux _IOC(): direction in bits 30-31, size 16-29, type 8-15, number 0-7."""
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


UI_SET_EVBIT = _ioc(1, "U", 100, 4)
UI_SET_KEYBIT = _ioc(1, "U", 101, 4)
UI_SET_RELBIT = _ioc(1, "U", 102, 4)
UI_DEV_SETUP = _ioc(1, "U", 3, 92)   # struct uinput_setup: input_id(8) + name[80] + ff_effects_max(4)
UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)

INPUT_EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux (timeval + type, code, value)


class UinputInjector(StatefulInjector):
    """Injects through /dev/uinput with plain ioctls: no evdev package, nothing to compile."""

    def __init__(self, path: str = "/dev/uinput", fd: Optional[int] = None) -> None:
        super().__init__()
        self._x, self._y = Carry(), Carry()
        self._wheel_hi, self._wheel_notch = Carry(), Carry()
        self._hwheel_hi, self._hwheel_notch = Carry(), Carry()
        self.sensitivity = _env_float("MESH_SENSITIVITY", 1.0)
        self.scroll_scale = _env_float("MESH_SCROLL_SCALE", 1.0)
        self._owns_fd = fd is None
        if fd is None:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            except PermissionError as exc:
                raise PermissionError(
                    f"no permission to open {path}. Add your user to the 'input' group and allow it with a udev rule "
                    f"(KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"), then log out and in once. "
                    f"`mesh gui` does this for you."
                ) from exc
            self._setup(fd)
        self.fd = fd

    @staticmethod
    def _setup(fd: int) -> None:
        for ev in (EV_KEY, EV_REL, EV_SYN):
            fcntl.ioctl(fd, UI_SET_EVBIT, ev)
        for code in set(KEY_CODES.values()) | set(BUTTON_CODES.values()):
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        for rel in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES):
            fcntl.ioctl(fd, UI_SET_RELBIT, rel)
        name = b"Mesh input"
        setup = struct.pack("<HHHH80sI", 0x03, 0x1234, 0x5678, 1, name, 0)  # BUS_USB, vendor, product, version
        fcntl.ioctl(fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(fd, UI_DEV_CREATE)
        time.sleep(0.15)  # let the desktop notice the new device before the first event

    def _emit(self, etype: int, code: int, value: int) -> None:
        now = time.time()
        os.write(self.fd, INPUT_EVENT.pack(int(now), int((now % 1) * 1_000_000), etype, code, value))

    def _sync(self) -> None:
        self._emit(EV_SYN, 0, 0)

    def _key(self, code: str, down: bool) -> None:
        self._emit(EV_KEY, KEY_CODES[code], 1 if down else 0)
        self._sync()

    def _button(self, button: int, down: bool) -> None:
        self._emit(EV_KEY, BUTTON_CODES[button], 1 if down else 0)
        self._sync()

    def move(self, dx: float, dy: float) -> None:
        ix = self._x.take(dx * self.sensitivity)
        iy = self._y.take(dy * self.sensitivity)
        if not ix and not iy:
            return
        if ix:
            self._emit(EV_REL, REL_X, ix)
        if iy:
            self._emit(EV_REL, REL_Y, iy)
        self._sync()

    def scroll(self, vertical: float, horizontal: float) -> None:
        wrote = False
        for amount, hi_code, notch_code, hi, notch in (
            (vertical, REL_WHEEL_HI_RES, REL_WHEEL, self._wheel_hi, self._wheel_notch),
            (horizontal, REL_HWHEEL_HI_RES, REL_HWHEEL, self._hwheel_hi, self._hwheel_notch),
        ):
            units = hi.take(amount * self.scroll_scale * 120)  # 120 hi-res units per notch
            if not units:
                continue
            self._emit(EV_REL, hi_code, units)
            whole = notch.take(units / 120.0)  # the legacy axis ticks once per whole notch
            if whole:
                self._emit(EV_REL, notch_code, whole)
            wrote = True
        if wrote:
            self._sync()

    def close(self) -> None:
        try:
            self.release_all()
            if self._owns_fd:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        finally:
            if self._owns_fd:
                try:
                    os.close(self.fd)
                except OSError:
                    pass


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- identity (certificate, token)

def default_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mesh", "agent")


def _paths(directory: str) -> Tuple[str, str, str]:
    return (os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem"), os.path.join(directory, "token"))


def ensure_identity(directory: str) -> Tuple[str, str, bytes]:
    """Creates (once) and returns (cert_path, key_path, token). The certificate is a
    self-signed P-256 certificate valid for ten years; the phone pins its fingerprint,
    so there is nothing to renew or trust-chain. Needs the `openssl` command, which is
    present on practically every Linux."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    cert, key, token_file = _paths(directory)
    if not (os.path.exists(cert) and os.path.exists(key)):
        if shutil.which("openssl") is None:
            raise RuntimeError("the `openssl` command is needed once to create the agent's certificate; please install it")
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
             "-keyout", key, "-out", cert, "-days", "3650", "-subj", "/CN=mesh-agent"],
            check=True, capture_output=True,
        )
        os.chmod(key, stat.S_IRUSR | stat.S_IWUSR)
    if not os.path.exists(token_file):
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(os.urandom(TOKEN_BYTES).hex())
    with open(token_file) as f:
        token = bytes.fromhex(f.read().strip())
    if len(token) != TOKEN_BYTES:
        raise RuntimeError(f"{token_file} is damaged; delete it to create a new token")
    return cert, key, token


def cert_fingerprint(cert_path: str) -> str:
    """SHA-256 of the certificate's DER bytes, lowercase hex: what the phone pins."""
    with open(cert_path) as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return hashlib.sha256(der).hexdigest()


# ---------------------------------------------------------------- finding this computer's addresses

_IGNORED_INTERFACES = ("lo", "docker", "br-", "veth", "virbr", "vmnet", "vboxnet", "podman", "cni", "flannel")


def parse_ip_addr(text: str) -> List[Tuple[str, str]]:
    """(interface, address) pairs from `ip -4 -o addr show scope global`, skipping loopback, link-local and the
    virtual interfaces of containers and VMs (a phone can never reach those)."""
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or "inet" not in parts:
            continue
        name = parts[1].rstrip(":")
        address = parts[parts.index("inet") + 1].split("/")[0]
        if name.startswith(_IGNORED_INTERFACES) or address.startswith(("127.", "169.254.")):
            continue
        found.append((name, address))
    return found


def local_addresses(runner=subprocess.run) -> List[str]:
    """The addresses a phone could use to reach this computer, best first: the Tailscale address (works from
    anywhere), then the ordinary LAN addresses. Never 0.0.0.0, which only means "listen everywhere"."""
    tailscale: List[str] = []
    lan: List[str] = []
    try:
        result = runner(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            tailscale = [a.strip() for a in result.stdout.split() if a.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        result = runner(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            lan = [address for _name, address in parse_ip_addr(result.stdout) if address not in tailscale]
    except (OSError, subprocess.SubprocessError):
        pass
    ordered: List[str] = []
    for address in tailscale + lan:
        if usable_address(address) and address not in ordered:
            ordered.append(address)
    return ordered


def usable_address(address: str) -> bool:
    """A dotted IPv4 address a phone could actually connect to: not the wildcard 0.0.0.0, not loopback, not link-local."""
    parts = address.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return False
    return not (address == "0.0.0.0" or address.startswith(("127.", "169.254.")))


def pair_info(directory: str, port: int = DEFAULT_PORT) -> dict:
    cert, _key, token = ensure_identity(directory)
    return {"version": VERSION, "port": port, "fingerprint": cert_fingerprint(cert), "token": token.hex(),
            "host": socket.gethostname(), "addresses": local_addresses()}


def reset_token(directory: str) -> None:
    """Revokes every paired phone: the next `init`/`pair-info` makes a fresh token."""
    try:
        os.remove(_paths(directory)[2])
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- Sunshine link (desktop view pairing without a PIN)

SUNSHINE_URL = "https://127.0.0.1:47990"   # Sunshine's own web interface, on this computer only
SUNSHINE_LOGIN_FILE = "sunshine.json"
SUNSHINE_PIN_WAIT = 20.0                    # seconds to wait for the phone's pairing request to show up in Sunshine
NOT_CONFIGURED_TEXT = "Mesh has no Sunshine login yet. On the computer run: python3 mesh_input.py sunshine-login"


class SunshineError(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Sunshine answers "no login set yet" with a redirect to /welcome; that must be seen, not followed."""

    def redirect_request(self, *args, **kwargs):  # noqa: D102
        return None


def _normalize_address(address: str) -> str:
    """Makes the phone's address (from our socket) comparable to the one Sunshine reports for the same phone."""
    a = (address or "").strip().lower()
    if "%" in a:
        a = a.split("%", 1)[0]
    if a.startswith("::ffff:"):
        a = a[len("::ffff:"):]
    return a


class SunshineLink:
    """Lets a phone that already has this agent's pairing token pair its desktop view with Sunshine without anyone typing a PIN.

    Sunshine holds a pairing request from a phone until a PIN is entered on its web page. This agent enters it instead:
    the phone picks a PIN, tells it to the agent over the already authenticated, encrypted channel, and starts the
    normal pairing with Sunshine; the agent finds that pending request through Sunshine's local web interface and
    approves it with the PIN. Whoever holds the pairing token could already control this computer's keyboard and mouse,
    so letting the same token also authorise a desktop viewer adds no new kind of access.

    It talks to Sunshine only on this computer (127.0.0.1), with a login Sunshine's web interface accepts. The login is
    kept in a private file next to the agent's certificate and token (see save_login).
    """

    def __init__(self, directory: str, url: Optional[str] = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.directory = directory
        self.url = (url or os.environ.get("MESH_SUNSHINE_URL") or SUNSHINE_URL).rstrip("/")
        self._sleep = sleep
        self._clock = clock
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False   # Sunshine's certificate is self-signed and this never leaves the computer
        ctx.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))

    # -- the saved login

    @property
    def login_path(self) -> str:
        return os.path.join(self.directory, SUNSHINE_LOGIN_FILE)

    def load_login(self) -> Optional[Tuple[str, str]]:
        try:
            with open(self.login_path) as f:
                data = json.load(f)
            return str(data["username"]), str(data["password"])
        except (OSError, ValueError, KeyError, TypeError):
            return None   # missing or damaged: both mean "not configured"

    def has_login(self) -> bool:
        return self.load_login() is not None

    def save_login(self, username: str, password: str) -> None:
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        tmp = self.login_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # private from the first byte
        with os.fdopen(fd, "w") as f:
            json.dump({"username": username, "password": password}, f)
        os.replace(tmp, self.login_path)
        os.chmod(self.login_path, 0o600)

    def forget_login(self) -> None:
        try:
            os.remove(self.login_path)
        except FileNotFoundError:
            pass

    # -- talking to Sunshine

    @staticmethod
    def _json(raw: bytes):
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return None

    def _call(self, method: str, path: str, body=None, auth: bool = True, timeout: float = 10.0):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if auth:
            login = self.load_login()
            if login is None:
                raise SunshineError(S_NOT_CONFIGURED, NOT_CONFIGURED_TEXT)
            headers["Authorization"] = "Basic " + base64.b64encode(f"{login[0]}:{login[1]}".encode()).decode()
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return response.status, self._json(response.read())
        except urllib.error.HTTPError as exc:   # also carries the 3xx answers, because redirects are not followed
            return exc.code, self._json(exc.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SunshineError(S_UNREACHABLE, f"Sunshine's web interface at {self.url} does not answer ({exc}). Is Sunshine running?")

    @staticmethod
    def _expect_ok(status: int) -> None:
        if status == 200:
            return
        if status in (301, 302, 303, 307, 308):
            raise SunshineError(S_NOT_CONFIGURED, "Sunshine has no web login yet. On the computer run: python3 mesh_input.py sunshine-login")
        if status in (401, 403):
            raise SunshineError(S_LOGIN_REFUSED, "Sunshine did not accept Mesh's saved login. On the computer run: python3 mesh_input.py sunshine-login")
        raise SunshineError(S_UNREACHABLE, f"Sunshine's web interface answered with an error ({status})")

    def check_login(self) -> None:
        """Raises SunshineError unless Sunshine accepts the saved login."""
        status, _ = self._call("GET", "/api/pin")
        self._expect_ok(status)

    def needs_first_login(self) -> bool:
        """True when Sunshine has no web login at all yet (a fresh install), so one can be created."""
        status, _ = self._call("GET", "/api/pin", auth=False)
        return status in (301, 302, 303, 307, 308)

    def create_login(self, username: str = "mesh") -> str:
        """Sets Sunshine's first web login (only possible while it has none) and saves it. Returns the password."""
        password = secrets.token_urlsafe(18)
        status, data = self._call("POST", "/api/password", auth=False, body={
            "newUsername": username, "newPassword": password, "confirmNewPassword": password})
        if status != 200 or not (isinstance(data, dict) and data.get("status") is True):
            raise SunshineError(S_FAILED, "Sunshine did not accept a new login")
        self.save_login(username, password)
        return password

    def ensure_desktop_app(self) -> bool:
        """Makes sure Sunshine has an application called Desktop (streams the whole screen). True if it was added."""
        status, data = self._call("GET", "/api/apps")
        self._expect_ok(status)
        apps = data.get("apps", []) if isinstance(data, dict) else []
        if any(isinstance(a, dict) and str(a.get("name", "")).strip().lower() == "desktop" for a in apps):
            return False
        status, data = self._call("POST", "/api/apps", body={"name": "Desktop", "output": "", "cmd": "", "index": -1, "image-path": "desktop.png"})
        if status != 200:
            raise SunshineError(S_FAILED, f"Sunshine would not add a Desktop application ({status})")
        return True

    # -- pairing

    def pair(self, pin: str, name: str, peer: str = "", wait: float = SUNSHINE_PIN_WAIT) -> None:
        """Approves the phone's pending pairing request with [pin]. Blocks; raises SunshineError with a reason a person can act on.

        [peer] is the phone's address as this agent sees it: a pending request from any other address is ignored, so a
        stranger's request that happens to arrive at the same moment is never approved."""
        if not re.fullmatch(r"[0-9]{4}", pin or ""):
            raise SunshineError(S_BAD_REQUEST, "the PIN must be exactly 4 digits")
        name = (name or "").strip().encode()[:128].decode("utf-8", "ignore") or "Mesh phone"
        peer = _normalize_address(peer)
        deadline = self._clock() + wait
        pairing_id = None
        while pairing_id is None:
            status, data = self._call("GET", "/api/pin")
            self._expect_ok(status)
            pending = data.get("pairings", []) if isinstance(data, dict) else []
            ours = [p for p in pending if isinstance(p, dict) and p.get("id")
                    and (not peer or _normalize_address(str(p.get("address", ""))) == peer)]
            if ours:
                pairing_id = str(ours[-1]["id"])
                break
            if self._clock() >= deadline:
                if pending:
                    raise SunshineError(S_FAILED, "Sunshine only has a pairing request from another device, which was left alone")
                raise SunshineError(S_FAILED, "the phone's pairing request never reached Sunshine (is the phone able to reach Sunshine's port 47989?)")
            self._sleep(0.25)
        status, data = self._call("POST", "/api/pin", timeout=35.0,
                                  body={"pairing_id": pairing_id, "pin": pin, "name": name})
        if status == 200 and isinstance(data, dict) and data.get("status") is True:
            return
        if status in (401, 403):
            raise SunshineError(S_LOGIN_REFUSED, "Sunshine did not accept Mesh's saved login. On the computer run: python3 mesh_input.py sunshine-login")
        raise SunshineError(S_FAILED, "Sunshine did not complete the pairing (wrong PIN or it timed out)")


def sunshine_login(directory: str, url: Optional[str] = None, username: Optional[str] = None,
                   password: Optional[str] = None, ask: Callable[[str], str] = input,
                   ask_secret: Callable[[str], str] = getpass.getpass, out: Callable[[str], None] = print) -> int:
    """The `sunshine-login` command: lets the agent approve this computer's Sunshine pairings. Returns an exit code."""
    link = SunshineLink(directory, url)
    try:
        if link.needs_first_login():
            link.create_login()
            out("Sunshine had no web login yet, so Mesh made one for itself (username: mesh).")
            out(f"The password is in {link.login_path}; use it if you ever want Sunshine's own web page.")
        else:
            username = username or ask("Sunshine web username: ").strip()
            password = password or ask_secret("Sunshine web password (not shown): ")
            if not username or not password:
                out("error: a username and a password are both needed")
                return 1
            link.save_login(username, password)
            try:
                link.check_login()
            except SunshineError:
                link.forget_login()   # never keep a login that does not work
                raise
            out("Sunshine accepted the login. It is saved privately, for this agent only.")
        try:
            if link.ensure_desktop_app():
                out("Added a Desktop application to Sunshine.")
        except SunshineError as exc:
            out(f"note: could not check Sunshine's applications ({exc.text})")
    except SunshineError as exc:
        out(f"error: {exc.text}")
        return 1
    out("Done: from now on the phone pairs its desktop view with this computer automatically, with no PIN to type.")
    return 0


# ---------------------------------------------------------------- server

class Server:
    def __init__(self, injector: Injector, token: bytes, host_name: str = "",
                 hello_timeout: float = HELLO_TIMEOUT, idle_timeout: float = IDLE_TIMEOUT,
                 log: Callable[[str], None] = lambda s: None, sunshine: Optional["SunshineLink"] = None) -> None:
        self.sunshine = sunshine
        self._tasks: set = set()          # running Sunshine pairings, kept so they are not garbage collected
        self._active_peer = ""            # the authenticated phone's address, as this agent sees it
        self.injector = injector
        self.token = token
        self.host_name = host_name or socket.gethostname()
        self.hello_timeout = hello_timeout
        self.idle_timeout = idle_timeout
        self.log = log
        self._active: Optional[asyncio.StreamWriter] = None
        self._active_name = ""

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        authenticated = False
        try:
            authenticated = await self._handshake(reader, writer, peer)
            if not authenticated:
                return
            await self._serve(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        except ProtocolError as exc:
            self.log(f"{peer}: protocol error: {exc}")
            await self._send(writer, msg_error(E_BAD_FRAME, str(exc)))
        finally:
            if authenticated and self._active is writer:
                self._active = None
                self.injector.release_all()  # never leave a key held down
                self.log(f"{self._active_name} disconnected")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handshake(self, reader, writer, peer) -> bool:
        try:
            msg_type, payload = await asyncio.wait_for(read_frame(reader), self.hello_timeout)
        except asyncio.TimeoutError:
            await self._send(writer, msg_error(E_NO_HELLO, "no HELLO received"))
            return False
        if msg_type != T_HELLO:
            await self._send(writer, msg_error(E_NO_HELLO, "the first message must be HELLO"))
            return False
        version, token, name = parse_hello(payload)
        if version != VERSION:
            await self._send(writer, msg_error(E_BAD_VERSION, f"protocol version {version} not supported"))
            return False
        if not hmac.compare_digest(token, self.token):
            self.log(f"{peer}: wrong token")
            await asyncio.sleep(1.0)  # makes guessing impractical
            await self._send(writer, msg_error(E_BAD_TOKEN, "wrong pairing token"))
            return False
        old = self._active
        if old is not None and old is not writer:
            await self._send(old, msg_error(E_REPLACED, "another phone connected"))
            try:
                old.close()
            except Exception:
                pass
            self.injector.release_all()
        self._active = writer
        self._active_peer = _normalize_address(str(peer[0])) if peer else ""
        self._active_name = name or str(peer)
        self.log(f"{self._active_name} connected")
        await self._send(writer, msg_hello_ok(self.host_name))
        return True

    async def _serve(self, reader, writer) -> None:
        while True:
            msg_type, payload = await asyncio.wait_for(read_frame(reader), self.idle_timeout)
            if self._active is not writer:
                return  # replaced by a newer phone
            if msg_type == T_MOVE:
                self.injector.move(*parse_move(payload))
            elif msg_type == T_SCROLL:
                self.injector.scroll(*parse_scroll(payload))
            elif msg_type == T_BUTTON:
                self.injector.button(*parse_button(payload))
            elif msg_type == T_KEY:
                self.injector.key(*parse_key(payload))
            elif msg_type == T_TEXT:
                self.injector.text(parse_text(payload))
            elif msg_type == T_PING:
                await self._send(writer, msg_pong(parse_id(payload)))
            elif msg_type == T_RELEASE_ALL:
                self.injector.release_all()
            elif msg_type == T_SUNSHINE_PAIR:
                pin, name = parse_sunshine_pair(payload)
                # Runs beside the normal traffic: pairing can take many seconds and pings must keep being answered.
                task = asyncio.ensure_future(self._sunshine_pair(writer, pin, name, self._active_peer))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            elif msg_type == T_BYE:
                return
            # unknown types are ignored so a newer phone can talk to an older agent

    async def _sunshine_pair(self, writer, pin: str, name: str, peer: str) -> None:
        if self.sunshine is None:
            await self._send(writer, msg_sunshine_result(S_NOT_CONFIGURED, "this agent was started without the Sunshine link"))
            return
        link = self.sunshine
        try:
            await asyncio.get_running_loop().run_in_executor(None, lambda: link.pair(pin, name, peer))
            code, text = S_OK, "paired"
        except SunshineError as exc:
            code, text = exc.code, exc.text
        except Exception as exc:   # a bug here must never take the input channel down
            code, text = S_FAILED, f"unexpected error: {exc}"
        self.log(f"sunshine pairing for {self._active_name or peer}: {text}")
        await self._send(writer, msg_sunshine_result(code, text))

    @staticmethod
    async def _send(writer, frame: bytes) -> None:
        try:
            writer.write(frame)
            await writer.drain()
        except (ConnectionError, OSError):
            pass


def make_ssl_context(cert: str, key: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # Android 8/9 stop at 1.2; the pinned certificate is the real protection
    ctx.load_cert_chain(cert, key)
    return ctx


def _log(message: str) -> None:
    print(message, flush=True)  # flushed so a service manager or a test harness sees it at once


async def serve(directory: str, port: int, injector: Injector, log=_log, host: str = "0.0.0.0") -> None:
    cert, key, token = ensure_identity(directory)
    server = Server(injector, token, log=log, sunshine=SunshineLink(directory))
    tls = await asyncio.start_server(server.handle, host, port, ssl=make_ssl_context(cert, key))
    log(f"mesh input channel listening on port {port}  (certificate {cert_fingerprint(cert)[:16]}...)")
    addresses = local_addresses()
    if addresses:
        log("phones can reach this computer at: " + ", ".join(addresses) + "   (pair-info lists them; 0.0.0.0 is NOT an address)")
    else:
        log("could not work out this computer's address; use its IP from `ip addr` when adding it on the phone")
    async with tls:
        await tls.serve_forever()


# ---------------------------------------------------------------- service (start at login, restart on failure)

SERVICE_NAME = "mesh-input.service"


def service_unit(python: str, script: str, port: int, directory: Optional[str] = None) -> str:
    """The systemd user unit. Runs as you (it needs your /dev/uinput access, like Sunshine), starts at login,
    and comes back by itself if it ever stops."""
    command = f"{python} {script} run --port {port}"
    if directory and directory != default_dir():
        command += f" --dir {directory}"
    return (
        "[Unit]\n"
        "Description=Mesh input channel (phone keyboard and trackpad)\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def unit_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "systemd", "user")


def install_service(port: int, directory: Optional[str] = None, start: bool = True,
                    target_dir: Optional[str] = None, runner=subprocess.run) -> str:
    """Writes the unit and (unless start is False) enables and starts it. Makes the certificate and token
    first, so the very first start already has them. Returns a message for the user."""
    ensure_identity(directory or default_dir())
    target = target_dir or unit_dir()
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, SERVICE_NAME)
    with open(path, "w") as f:
        f.write(service_unit(sys.executable, os.path.abspath(__file__), port, directory))
    if not start:
        return f"service written to {path} (not started)"
    for args in (["daemon-reload"], ["enable", "--now", SERVICE_NAME]):
        result = runner(["systemctl", "--user", *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"`systemctl --user {' '.join(args)}` failed: {(result.stderr or result.stdout).strip()}")
    return f"service installed and started ({path}); check it with: systemctl --user status {SERVICE_NAME}"


def uninstall_service(target_dir: Optional[str] = None, runner=subprocess.run) -> str:
    path = os.path.join(target_dir or unit_dir(), SERVICE_NAME)
    if target_dir is None:
        runner(["systemctl", "--user", "disable", "--now", SERVICE_NAME], capture_output=True, text=True)
    try:
        os.remove(path)
    except FileNotFoundError:
        return "no service was installed"
    if target_dir is None:
        runner(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    return f"service removed ({path})"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mesh input channel (host side)")
    ap.add_argument("command", choices=["init", "pair-info", "run", "reset-token", "install-service", "uninstall-service", "sunshine-login"])
    ap.add_argument("--dir", default=default_dir())
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--dry-run", action="store_true", help="record events instead of injecting them")
    ap.add_argument("--no-start", action="store_true", help="install-service: write the unit but do not start it")
    ap.add_argument("--sunshine-url", default=None, help="sunshine-login: Sunshine's web address (default https://127.0.0.1:47990)")
    ap.add_argument("--sunshine-user", default=None, help="sunshine-login: the username, instead of being asked")
    ap.add_argument("--password-stdin", action="store_true", help="sunshine-login: read the password from standard input")
    args = ap.parse_args(argv)

    if args.command == "sunshine-login":
        password = sys.stdin.readline().rstrip("\n") if args.password_stdin else None
        return sunshine_login(args.dir, args.sunshine_url, args.sunshine_user, password)

    if args.command in ("install-service", "uninstall-service"):
        try:
            if args.command == "install-service":
                print(install_service(args.port, args.dir, start=not args.no_start))
            else:
                print(uninstall_service())
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "reset-token":
        reset_token(args.dir)
        print("token removed; run `init` or `pair-info` to create a new one (every phone must pair again)")
        return 0
    if args.command in ("init", "pair-info"):
        try:
            info = pair_info(args.dir, args.port)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(info) if args.command == "pair-info" else f"ready: certificate {info['fingerprint'][:16]}...")
        if args.command == "pair-info" and not SunshineLink(args.dir).has_login():
            # On stderr, so the JSON above can still be copied cleanly.
            print("tip: run `python3 mesh_input.py sunshine-login` once so the phone can pair its desktop view with no PIN to type",
                  file=sys.stderr)
        return 0

    try:
        injector: Injector = (
            RecordingInjector(on_event=lambda e: print("EVENT " + json.dumps(list(e)), flush=True))
            if args.dry_run else UinputInjector()
        )
    except (PermissionError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        asyncio.run(serve(args.dir, args.port, injector))
    except KeyboardInterrupt:
        pass
    finally:
        injector.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
