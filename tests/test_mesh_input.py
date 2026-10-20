"""Mesh input channel (host side): protocol, authentication, injection, key table."""

import asyncio
import contextlib
import json
import os
import shutil
import ssl
import struct
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")


# ----------------------------------------------------------------- framing and parsing

def test_frame_layout_is_length_type_payload():
    frame = mi.encode_frame(0x07, b"\x00\x00\x00\x2a")
    assert frame == b"\x00\x05\x07\x00\x00\x00\x2a"


def test_oversized_frames_are_refused_when_building():
    with pytest.raises(mi.ProtocolError):
        mi.encode_frame(0x06, b"x" * mi.MAX_FRAME)


def test_read_frame_round_trip_and_bounds():
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(mi.msg_key("KeyA", True) + mi.msg_ping(7))
        assert await mi.read_frame(r) == (mi.T_KEY, bytes([1, 4]) + b"KeyA")
        assert await mi.read_frame(r) == (mi.T_PING, struct.pack(">I", 7))
        bad = asyncio.StreamReader()
        bad.feed_data(b"\x00\x00")  # size 0 is invalid
        with pytest.raises(mi.ProtocolError):
            await mi.read_frame(bad)
        huge = asyncio.StreamReader()
        huge.feed_data(struct.pack(">H", mi.MAX_FRAME + 1))
        with pytest.raises(mi.ProtocolError):
            await mi.read_frame(huge)
    asyncio.run(go())


def test_known_byte_vectors():
    # These exact bytes are also checked by the phone app's tests (MipCodecTest).
    assert mi.msg_move(1.5, -2.0).hex() == "0009023fc00000c0000000"
    assert mi.msg_scroll(-0.25, 0.0).hex() == "00090 3be80000000000000".replace(" ", "")
    assert mi.msg_button(1, True).hex() == "000304" + "0101"
    assert mi.msg_key("KeyA", True).hex() == "0007050104" + "4b657941"
    assert mi.msg_text("hé").hex() == "0004" + "06" + "68c3a9"
    assert mi.msg_ping(7).hex() == "000507" + "00000007"
    assert mi.msg_release_all().hex() == "000108"
    assert mi.msg_hello(b"\xaa\xbb", "ph").hex() == "000801" + "01" + "02aabb" + "027068"


def test_parsers_accept_what_the_builders_make():
    assert mi.parse_move(mi.msg_move(3.25, -1.5)[3:]) == (3.25, -1.5)
    assert mi.parse_scroll(mi.msg_scroll(0.5, -0.5)[3:]) == (0.5, -0.5)
    assert mi.parse_button(mi.msg_button(3, False)[3:]) == (3, False)
    assert mi.parse_key(mi.msg_key("ArrowUp", True)[3:]) == ("ArrowUp", True)
    assert mi.parse_text(mi.msg_text("héllo ✓")[3:]) == "héllo ✓"
    assert mi.parse_hello(mi.msg_hello(b"tok", "my phone")[3:]) == (1, b"tok", "my phone")
    assert mi.parse_id(mi.msg_ping(99)[3:]) == 99


@pytest.mark.parametrize("payload", [b"", b"\x00" * 7, b"\x00" * 9, struct.pack(">ff", float("nan"), 0), struct.pack(">ff", float("inf"), 0),
                                      struct.pack(">ff", 1e9, 0)])
def test_bad_moves_are_rejected(payload):
    with pytest.raises(mi.ProtocolError):
        mi.parse_move(payload)


def test_bad_scrolls_buttons_keys_text_hellos_are_rejected():
    with pytest.raises(mi.ProtocolError):
        mi.parse_scroll(struct.pack(">ff", 5000, 0))
    for payload in (b"", b"\x09\x01", b"\x01\x02", b"\x01"):
        with pytest.raises(mi.ProtocolError):
            mi.parse_button(payload)
    for payload in (b"", b"\x01", b"\x02\x01A", b"\x01\x05AB", b"\x01\x01AX"):
        with pytest.raises(mi.ProtocolError):
            mi.parse_key(payload)
    with pytest.raises(mi.ProtocolError):
        mi.parse_text(b"\xff\xfe")
    for payload in (b"", b"\x01", b"\x01\x05ab", b"\x01\x01a"):
        with pytest.raises(mi.ProtocolError):
            mi.parse_hello(payload)
    with pytest.raises(mi.ProtocolError):
        mi.parse_id(b"\x00")


# ----------------------------------------------------------------- key table

def test_every_key_code_matches_the_kernels_name_for_it():
    evdev = pytest.importorskip("evdev")
    ec = evdev.ecodes
    expected = {"Escape": "KEY_ESC", "Enter": "KEY_ENTER", "Backspace": "KEY_BACKSPACE", "Tab": "KEY_TAB",
                "Space": "KEY_SPACE", "ArrowUp": "KEY_UP", "ArrowDown": "KEY_DOWN", "ArrowLeft": "KEY_LEFT",
                "ArrowRight": "KEY_RIGHT", "Home": "KEY_HOME", "End": "KEY_END", "PageUp": "KEY_PAGEUP",
                "PageDown": "KEY_PAGEDOWN", "Insert": "KEY_INSERT", "Delete": "KEY_DELETE",
                "PrintScreen": "KEY_SYSRQ", "Meta": "KEY_LEFTMETA", "MetaLeft": "KEY_LEFTMETA", "MetaRight": "KEY_RIGHTMETA",
                "ContextMenu": "KEY_COMPOSE", "CapsLock": "KEY_CAPSLOCK",
                "Minus": "KEY_MINUS", "Equal": "KEY_EQUAL", "BracketLeft": "KEY_LEFTBRACE", "BracketRight": "KEY_RIGHTBRACE",
                "Semicolon": "KEY_SEMICOLON", "Quote": "KEY_APOSTROPHE", "Backquote": "KEY_GRAVE", "Backslash": "KEY_BACKSLASH",
                "Comma": "KEY_COMMA", "Period": "KEY_DOT", "Slash": "KEY_SLASH",
                "ControlLeft": "KEY_LEFTCTRL", "ControlRight": "KEY_RIGHTCTRL", "ShiftLeft": "KEY_LEFTSHIFT",
                "ShiftRight": "KEY_RIGHTSHIFT", "AltLeft": "KEY_LEFTALT", "AltRight": "KEY_RIGHTALT",
                "ctrl": "KEY_LEFTCTRL", "shift": "KEY_LEFTSHIFT", "alt": "KEY_LEFTALT", "meta": "KEY_LEFTMETA"}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        expected[f"Key{letter}"] = f"KEY_{letter}"
    for d in range(10):
        expected[f"Digit{d}"] = f"KEY_{d}"
    for n in range(1, 13):
        expected[f"F{n}"] = f"KEY_F{n}"
    assert set(expected) == set(mi.KEY_CODES), set(expected) ^ set(mi.KEY_CODES)
    for name, kernel_name in expected.items():
        assert mi.KEY_CODES[name] == getattr(ec, kernel_name), f"{name} -> {mi.KEY_CODES[name]} but {kernel_name} is {getattr(ec, kernel_name)}"


def test_buttons_and_axes_match_the_kernel():
    evdev = pytest.importorskip("evdev")
    ec = evdev.ecodes
    assert mi.BUTTON_CODES == {1: ec.BTN_LEFT, 2: ec.BTN_MIDDLE, 3: ec.BTN_RIGHT, 4: ec.BTN_SIDE, 5: ec.BTN_EXTRA}
    assert (mi.REL_X, mi.REL_Y, mi.REL_WHEEL, mi.REL_HWHEEL) == (ec.REL_X, ec.REL_Y, ec.REL_WHEEL, ec.REL_HWHEEL)
    assert (mi.REL_WHEEL_HI_RES, mi.REL_HWHEEL_HI_RES) == (ec.REL_WHEEL_HI_RES, ec.REL_HWHEEL_HI_RES)
    assert (mi.EV_SYN, mi.EV_KEY, mi.EV_REL) == (ec.EV_SYN, ec.EV_KEY, ec.EV_REL)


def test_the_ioctl_numbers_are_the_kernels():
    # Values from <linux/uinput.h> on 64-bit Linux, computed independently of mesh_input's _ioc().
    assert mi.UI_SET_EVBIT == 0x40045564
    assert mi.UI_SET_KEYBIT == 0x40045565
    assert mi.UI_SET_RELBIT == 0x40045566
    assert mi.UI_DEV_SETUP == 0x405C5503
    assert mi.UI_DEV_CREATE == 0x5501
    assert mi.UI_DEV_DESTROY == 0x5502
    assert mi.INPUT_EVENT.size in (16 + 8, 8 + 8)  # 24 on 64-bit, 16 on 32-bit
    assert struct.calcsize("<HHHH80sI") == 92       # struct uinput_setup


# ----------------------------------------------------------------- text typing

def _typed(events):
    """Collapses events into 'KeyA' / 'Shift+KeyA' taps for readable assertions."""
    out, shift = [], False
    for code, down in events:
        if code == "ShiftLeft":
            shift = down
        elif down:
            out.append(("Shift+" if shift else "") + code)
    return out


def test_ascii_text_is_typed_with_real_keys():
    assert _typed(mi.text_events("hi 5")) == ["KeyH", "KeyI", "Space", "Digit5"]
    assert _typed(mi.text_events("Hi!")) == ["Shift+KeyH", "KeyI", "Shift+Digit1"]
    assert _typed(mi.text_events("a\nb\tc")) == ["KeyA", "Enter", "KeyB", "Tab", "KeyC"]
    assert _typed(mi.text_events("{}|:\"<>?~_+")) == ["Shift+BracketLeft", "Shift+BracketRight", "Shift+Backslash", "Shift+Semicolon",
                                                      "Shift+Quote", "Shift+Comma", "Shift+Period", "Shift+Slash", "Shift+Backquote",
                                                      "Shift+Minus", "Shift+Equal"]


def test_every_ascii_character_is_typeable_and_balanced():
    printable = "".join(chr(c) for c in range(32, 127)) + "\n\t"
    events = mi.text_events(printable)
    assert all(code in mi.KEY_CODES for code, _ in events)
    held = {}
    for code, down in events:
        held[code] = held.get(code, 0) + (1 if down else -1)
        assert held[code] in (0, 1)
    assert all(v == 0 for v in held.values())  # every key pressed was released, shift included


def test_non_ascii_uses_the_unicode_compose_sequence():
    events = mi.text_events("é")
    codes = [(c, d) for c, d in events]
    assert codes[:6] == [("ControlLeft", True), ("ShiftLeft", True), ("KeyU", True), ("KeyU", False),
                         ("ShiftLeft", False), ("ControlLeft", False)]
    assert _typed(events[6:]) == ["KeyE", "Digit9", "Space"]  # U+00E9
    assert _typed(mi.text_events("€"))[-5:] == ["Digit2", "Digit0", "KeyA", "KeyC", "Space"]  # U+20AC


def test_carriage_returns_are_dropped():
    assert _typed(mi.text_events("a\r\nb")) == ["KeyA", "Enter", "KeyB"]


# ----------------------------------------------------------------- uinput injection (real pipe as /dev/uinput)

def _drain(read_fd):
    os.set_blocking(read_fd, False)
    events = []
    try:
        data = os.read(read_fd, 1 << 16)
    except BlockingIOError:
        data = b""
    size = mi.INPUT_EVENT.size
    for i in range(0, len(data), size):
        _s, _us, t, c, v = mi.INPUT_EVENT.unpack(data[i:i + size])
        events.append((t, c, v))
    return events


@pytest.fixture
def uinput():
    r, w = os.pipe()
    inj = mi.UinputInjector(fd=w)
    yield inj, r
    os.close(r)
    os.close(w)


def test_a_key_press_writes_down_then_up_each_with_a_sync(uinput):
    inj, r = uinput
    inj.key("KeyA", True)
    inj.key("KeyA", False)
    assert _drain(r) == [(1, 30, 1), (0, 0, 0), (1, 30, 0), (0, 0, 0)]


def test_unknown_keys_and_buttons_are_skipped_not_fatal(uinput):
    inj, r = uinput
    inj.key("NoSuchKey", True)
    inj.button(99, True)
    assert _drain(r) == []


def test_mouse_buttons_use_the_btn_codes(uinput):
    inj, r = uinput
    inj.button(1, True)
    inj.button(3, True)
    inj.button(1, False)
    assert [(t, c, v) for t, c, v in _drain(r) if t == 1] == [(1, 0x110, 1), (1, 0x111, 1), (1, 0x110, 0)]


def test_slow_fractional_motion_still_moves_and_nothing_is_lost(uinput):
    inj, r = uinput
    for _ in range(50):
        inj.move(0.3, -0.2)
    rel = [(c, v) for t, c, v in _drain(r) if t == 2]
    assert sum(v for c, v in rel if c == mi.REL_X) in (14, 15)
    assert sum(v for c, v in rel if c == mi.REL_Y) in (-10, -9)


def test_a_sub_count_move_writes_nothing(uinput):
    inj, r = uinput
    inj.move(0.2, 0.2)
    assert _drain(r) == []


def test_scroll_is_smooth_hi_res_plus_the_legacy_notch(uinput):
    inj, r = uinput
    for _ in range(40):
        inj.scroll(0.1, 0.0)  # four notches in tiny steps
    rel = [(c, v) for t, c, v in _drain(r) if t == 2]
    assert abs(sum(v for c, v in rel if c == mi.REL_WHEEL_HI_RES) - 480) <= 1
    assert sum(v for c, v in rel if c == mi.REL_WHEEL) in (3, 4)


def test_horizontal_scroll_uses_the_horizontal_axes(uinput):
    inj, r = uinput
    inj.scroll(0.0, 1.0)
    rel = {c: v for t, c, v in _drain(r) if t == 2}
    assert rel[mi.REL_HWHEEL_HI_RES] == 120 and rel[mi.REL_HWHEEL] == 1 and mi.REL_WHEEL not in rel


def test_release_all_lets_go_of_everything_held(uinput):
    inj, r = uinput
    inj.key("ControlLeft", True)
    inj.key("KeyC", True)
    inj.button(1, True)
    _drain(r)
    inj.release_all()
    ups = [(c, v) for t, c, v in _drain(r) if t == 1]
    assert sorted(ups) == sorted([(29, 0), (46, 0), (0x110, 0)])
    inj.release_all()
    assert _drain(r) == []  # nothing left to release


def test_text_is_typed_through_the_injector(uinput):
    inj, r = uinput
    inj.text("a")
    assert [(t, c, v) for t, c, v in _drain(r) if t == 1] == [(1, 30, 1), (1, 30, 0)]


def test_a_missing_uinput_permission_gives_instructions(monkeypatch):
    def deny(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(mi.os, "open", deny)
    with pytest.raises(PermissionError) as err:
        mi.UinputInjector("/dev/uinput")
    assert "input" in str(err.value) and "udev" in str(err.value)


def test_sensitivity_and_bad_tuning_values(monkeypatch):
    r, w = os.pipe()
    monkeypatch.setenv("MESH_SENSITIVITY", "2")
    monkeypatch.setenv("MESH_SCROLL_SCALE", "nonsense")
    inj = mi.UinputInjector(fd=w)
    assert inj.sensitivity == 2.0 and inj.scroll_scale == 1.0
    inj.move(1.0, 0.0)
    assert [(c, v) for t, c, v in _drain(r) if t == 2] == [(mi.REL_X, 2)]
    os.close(r)
    os.close(w)


# ----------------------------------------------------------------- identity

@needs_openssl
def test_identity_is_created_once_and_stays_the_same(tmp_path):
    d = str(tmp_path / "agent")
    cert1, key1, token1 = mi.ensure_identity(d)
    fp1 = mi.cert_fingerprint(cert1)
    cert2, key2, token2 = mi.ensure_identity(d)
    assert (cert1, key1, token1) == (cert2, key2, token2)
    assert mi.cert_fingerprint(cert2) == fp1 and len(fp1) == 64
    assert len(token1) == mi.TOKEN_BYTES


@needs_openssl
def test_secrets_are_private_to_the_user(tmp_path):
    d = str(tmp_path / "agent")
    mi.ensure_identity(d)
    for name in ("key.pem", "token"):
        assert os.stat(os.path.join(d, name)).st_mode & 0o077 == 0, name
    assert os.stat(d).st_mode & 0o077 == 0


@needs_openssl
def test_a_damaged_token_is_reported_not_used(tmp_path):
    d = str(tmp_path / "agent")
    mi.ensure_identity(d)
    open(os.path.join(d, "token"), "w").write("abcd")
    with pytest.raises(RuntimeError):
        mi.ensure_identity(d)


@needs_openssl
def test_reset_token_revokes_by_making_a_new_one_but_keeps_the_certificate(tmp_path):
    d = str(tmp_path / "agent")
    cert, _k, old = mi.ensure_identity(d)
    fp = mi.cert_fingerprint(cert)
    mi.reset_token(d)
    _c, _k, new = mi.ensure_identity(d)
    assert new != old and mi.cert_fingerprint(cert) == fp


@needs_openssl
def test_pair_info_and_the_cli(tmp_path, capsys):
    d = str(tmp_path / "agent")
    assert mi.main(["pair-info", "--dir", d, "--port", "47611"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["port"] == 47611 and len(info["fingerprint"]) == 64 and len(bytes.fromhex(info["token"])) == 32
    assert mi.main(["init", "--dir", d]) == 0
    assert mi.main(["reset-token", "--dir", d]) == 0


def test_missing_openssl_has_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(mi.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError) as err:
        mi.ensure_identity(str(tmp_path / "a"))
    assert "openssl" in str(err.value)


# ----------------------------------------------------------------- the real server over real TLS

class Client:
    def __init__(self, reader, writer, fingerprint):
        self.reader, self.writer, self.fingerprint = reader, writer, fingerprint

    async def send(self, frame):
        self.writer.write(frame)
        await self.writer.drain()

    async def recv(self, timeout=3.0):
        return await asyncio.wait_for(mi.read_frame(self.reader), timeout)

    async def closed(self, timeout=3.0):
        """True once the host has closed the connection."""
        try:
            while True:
                await asyncio.wait_for(mi.read_frame(self.reader), timeout)
        except (asyncio.IncompleteReadError, ConnectionError):
            return True
        except asyncio.TimeoutError:
            return False


_open_clients = []


async def connect(port, tls_version=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if tls_version is not None:
        ctx.minimum_version = tls_version
        ctx.maximum_version = tls_version
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
    import hashlib
    der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    client = Client(reader, writer, hashlib.sha256(der).hexdigest())
    _open_clients.append(client)
    return client


@contextlib.asynccontextmanager
async def running(tmp_path, injector=None, **server_kwargs):
    d = str(tmp_path / "agent")
    cert, key, token = mi.ensure_identity(d)
    injector = injector or mi.RecordingInjector()
    server = mi.Server(injector, token, host_name="testhost", **server_kwargs)
    tls = await asyncio.start_server(server.handle, "127.0.0.1", 0, ssl=mi.make_ssl_context(cert, key))
    port = tls.sockets[0].getsockname()[1]
    try:
        yield port, injector, token, mi.cert_fingerprint(cert)
    finally:
        # Close the phones first: the server (correctly) waits for open connections,
        # which would otherwise cost a full idle timeout at the end of every test.
        for client in _open_clients:
            client.writer.close()
        _open_clients.clear()
        tls.close()
        await asyncio.wait_for(tls.wait_closed(), 5)


async def paired(port, token, name="test phone"):
    c = await connect(port)
    await c.send(mi.msg_hello(token, name))
    msg_type, payload = await c.recv()
    assert msg_type == mi.T_HELLO_OK, (msg_type, payload)
    return c


async def settle():
    await asyncio.sleep(0.15)


@needs_openssl
def test_a_paired_phone_gets_hello_ok_and_the_certificate_matches_the_pinned_fingerprint(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, fp):
            c = await connect(port)
            assert c.fingerprint == fp                      # what the phone pins is what the host presents
            await c.send(mi.msg_hello(token, "pixel"))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_HELLO_OK and payload[0] == mi.VERSION
            assert payload[1] & mi.FEATURE_TEXT and payload[1] & mi.FEATURE_HIRES_SCROLL
            assert payload[3:].decode() == "testhost"
    asyncio.run(go())


@needs_openssl
def test_input_events_reach_the_injector(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_move(2, 3) + mi.msg_key("ControlLeft", True) + mi.msg_key("KeyC", True)
                         + mi.msg_key("KeyC", False) + mi.msg_key("ControlLeft", False)
                         + mi.msg_button(1, True) + mi.msg_button(1, False) + mi.msg_scroll(1.0, 0) + mi.msg_text("ok"))
            await settle()
            assert inj.events[0] == ("move", 2, 3)
            assert ("key", "ControlLeft", True) in inj.events and ("key", "KeyC", False) in inj.events
            assert ("button", 1, True) in inj.events and ("scroll", 1.0, 0.0) in inj.events
            assert ("key", "KeyO", True) in inj.events and ("key", "KeyK", False) in inj.events
    asyncio.run(go())


@needs_openssl
def test_a_wrong_token_is_refused_and_injects_nothing(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(b"\x00" * 32, "intruder"))
            await c.send(mi.msg_key("KeyA", True))           # even if it keeps talking
            msg_type, payload = await c.recv(5)
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_BAD_TOKEN
            assert await c.closed()
            assert inj.events == []
    asyncio.run(go())


@needs_openssl
def test_commands_before_hello_are_refused(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_key("KeyA", True))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_NO_HELLO
            assert inj.events == []
    asyncio.run(go())


@needs_openssl
def test_a_silent_connection_is_dropped(tmp_path):
    async def go():
        async with running(tmp_path, hello_timeout=0.3) as (port, inj, token, _fp):
            c = await connect(port)
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_NO_HELLO
            assert await c.closed()
    asyncio.run(go())


@needs_openssl
def test_a_different_protocol_version_is_refused_clearly(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(token, "future phone", version=9))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_BAD_VERSION
    asyncio.run(go())


@needs_openssl
def test_ping_gets_pong(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_ping(1234))
            assert await c.recv() == (mi.T_PONG, struct.pack(">I", 1234))
    asyncio.run(go())


@needs_openssl
def test_a_new_phone_replaces_the_old_one_and_the_old_ones_keys_are_released(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            first = await paired(port, token, "first")
            await first.send(mi.msg_key("ShiftLeft", True))
            await settle()
            second = await paired(port, token, "second")
            msg_type, payload = await first.recv()
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_REPLACED
            assert ("key", "ShiftLeft", False) in inj.events        # released, not left stuck
            await second.send(mi.msg_key("KeyA", True))
            await settle()
            assert ("key", "KeyA", True) in inj.events
    asyncio.run(go())


@needs_openssl
def test_disconnecting_releases_held_keys_and_buttons(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_key("ControlLeft", True) + mi.msg_button(1, True))
            await settle()
            c.writer.close()                                         # phone vanishes
            await settle()
            assert ("key", "ControlLeft", False) in inj.events and ("button", 1, False) in inj.events
            assert not inj.keys_down and not inj.buttons_down
    asyncio.run(go())


@needs_openssl
def test_a_phone_that_goes_quiet_is_dropped_and_released(tmp_path):
    async def go():
        async with running(tmp_path, idle_timeout=0.4) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_key("KeyA", True))
            assert await c.closed(3)
            assert ("key", "KeyA", False) in inj.events
    asyncio.run(go())


@needs_openssl
def test_keepalive_pings_keep_a_quiet_phone_connected(tmp_path):
    async def go():
        async with running(tmp_path, idle_timeout=0.5) as (port, inj, token, _fp):
            c = await paired(port, token)
            for i in range(5):
                await asyncio.sleep(0.25)
                await c.send(mi.msg_ping(i))
                assert (await c.recv())[0] == mi.T_PONG
    asyncio.run(go())


@needs_openssl
def test_release_all_and_bye(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_key("KeyA", True) + mi.msg_release_all())
            await settle()
            assert ("key", "KeyA", False) in inj.events
            await c.send(mi.msg_bye())
            assert await c.closed()
    asyncio.run(go())


@needs_openssl
def test_malformed_frames_are_answered_and_closed_without_crashing_the_server(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.encode_frame(mi.T_MOVE, b"\x00\x01"))     # too short
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_ERROR and payload[0] == mi.E_BAD_FRAME
            assert await c.closed()
            d = await paired(port, token)                              # and the server still works
            await d.send(mi.msg_key("KeyB", True))
            await settle()
            assert ("key", "KeyB", True) in inj.events
    asyncio.run(go())


@needs_openssl
def test_unknown_message_types_are_ignored_for_forward_compatibility(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.encode_frame(0x6F, b"from the future") + mi.msg_key("KeyA", True))
            await settle()
            assert ("key", "KeyA", True) in inj.events
    asyncio.run(go())


@needs_openssl
def test_tls_1_2_is_accepted_for_old_android_and_older_versions_are_refused(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await connect(port, tls_version=ssl.TLSVersion.TLSv1_2)    # Android 8 and 9
            assert c.writer.get_extra_info("ssl_object").version() == "TLSv1.2"
            await c.send(mi.msg_hello(token, "old android"))
            assert (await c.recv())[0] == mi.T_HELLO_OK
            with pytest.raises((ssl.SSLError, ConnectionError, OSError, ValueError)):
                await connect(port, tls_version=ssl.TLSVersion.TLSv1_1)
    asyncio.run(go())


@needs_openssl
def test_plain_text_connections_are_not_served(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(mi.msg_hello(token, "no tls") + mi.msg_key("KeyA", True))
            await writer.drain()
            try:
                data = await asyncio.wait_for(reader.read(100), 2)
            except (ConnectionError, asyncio.TimeoutError):
                data = b""
            assert mi.T_HELLO_OK not in data[2:3]
            await settle()
            assert inj.events == []
    asyncio.run(go())


@needs_openssl
def test_many_moves_are_handled_quickly(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            start = time.perf_counter()
            await c.send(b"".join(mi.msg_move(1, 1) for _ in range(2000)))
            await c.send(mi.msg_ping(1))
            assert (await c.recv(5))[0] == mi.T_PONG                  # PONG means all 2000 were processed first
            assert time.perf_counter() - start < 2.0
            assert sum(e[1] for e in inj.events if e[0] == "move") == 2000
    asyncio.run(go())
