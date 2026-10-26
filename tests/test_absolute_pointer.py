"""Touch mode needs the host to put the pointer at an exact spot: MOVE_ABS, the mapping onto monitors, and the second uinput device."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import mesh_input as mi  # noqa: E402
from test_mesh_input import needs_openssl, paired, running, settle  # noqa: E402


# ------------------------------------------------------------------ protocol

def test_move_abs_has_a_fixed_byte_vector():
    # The phone app's MipCodecTest checks the same bytes.
    assert mi.msg_move_abs(0x1234, 0xFFFF).hex() == "0005" + "0b" + "1234" + "ffff"


def test_positions_outside_the_range_are_clamped_not_wrapped():
    assert mi.msg_move_abs(-5, 70000).hex() == "0005" + "0b" + "0000" + "ffff"


def test_move_abs_parses_what_it_builds_and_rejects_bad_sizes():
    assert mi.parse_move_abs(mi.msg_move_abs(100, 40000)[3:]) == (100, 40000)
    for bad in (b"", b"\x00\x01\x00", b"\x00\x01\x00\x02\x00"):
        with pytest.raises(mi.ProtocolError):
            mi.parse_move_abs(bad)


def test_the_uinput_ioctl_numbers_are_the_kernels():
    assert mi.UI_SET_ABSBIT == 0x40045567
    assert mi.UI_ABS_SETUP == 0x401C5504


# ------------------------------------------------------------------ mapping onto monitors

def test_without_a_monitor_setting_the_streamed_screen_is_the_whole_desktop():
    m = mi.AbsoluteMap()
    assert m.map(0, 0) == (0, 0)
    assert m.map(12345, 54321) == (12345, 54321)
    assert m.map(70000, -1) == (65535, 0)


def test_a_monitor_on_the_right_of_two_lands_in_the_right_half():
    # Two 1920x1080 monitors side by side; the stream shows the right one.
    m = mi.AbsoluteMap(monitor=(1920, 0, 1920, 1080), desktop=(3840, 1080))
    assert m.map(0, 0) == (32768, 0)
    assert m.map(65535, 65535) == (65535, 65535)
    assert m.map(32768, 32768)[0] == pytest.approx(49152, abs=2)   # the middle of the right monitor
    assert m.map(32768, 32768)[1] == pytest.approx(32768, abs=2)


def test_a_monitor_below_another_is_offset_vertically():
    m = mi.AbsoluteMap(monitor=(0, 1080, 1920, 1080), desktop=(1920, 2160))
    assert m.map(0, 0) == (0, 32768)
    assert m.map(65535, 65535) == (65535, 65535)


def test_a_smaller_monitor_inside_a_bigger_desktop_is_scaled():
    m = mi.AbsoluteMap(monitor=(0, 0, 1280, 720), desktop=(2560, 1440))
    assert m.map(65535, 65535) == (32768, 32768)


@pytest.mark.parametrize("monitor,desktop", [
    ((0, 0, 0, 1080), (1920, 1080)),       # zero width
    ((0, 0, 1920, 1080), (0, 1080)),       # zero desktop width
    ((0, 0, 1920), (1920, 1080)),          # wrong number of values
    (None, (1920, 1080)),                  # only half the information
    ((0, 0, 1920, 1080), None),
])
def test_unusable_monitor_settings_mean_the_whole_desktop(monitor, desktop):
    assert mi.AbsoluteMap(monitor, desktop).map(100, 200) == (100, 200)


def test_the_environment_settings_are_read_and_bad_ones_ignored():
    good = mi.AbsoluteMap.from_env({"MESH_MONITOR": "1920,0,1920,1080", "MESH_DESKTOP": "3840,1080"})
    assert good.monitor == (1920, 0, 1920, 1080) and good.desktop == (3840, 1080)
    for env in ({}, {"MESH_MONITOR": "a,b,c,d", "MESH_DESKTOP": "1,2"}, {"MESH_MONITOR": "1,2,3", "MESH_DESKTOP": "1,2"},
                {"MESH_MONITOR": "0,0,10,10"}):
        assert mi.AbsoluteMap.from_env(env).map(7, 9) == (7, 9)


# ------------------------------------------------------------------ the injectors

def read_events(fd):
    data = os.read(fd, 4096)
    size = mi.INPUT_EVENT.size
    return [mi.INPUT_EVENT.unpack_from(data, i)[2:] for i in range(0, len(data), size)]


def test_the_recording_injector_records_mapped_positions():
    inj = mi.RecordingInjector()
    assert inj.supports_absolute
    inj.abs_map = mi.AbsoluteMap((0, 0, 1280, 720), (2560, 1440))
    inj.move_abs(65535, 0)
    assert inj.events == [("moveabs", 32768, 0)]


def test_the_uinput_pointer_writes_x_then_y_then_a_sync_to_its_own_device():
    r, w = os.pipe()
    main = os.open(os.devnull, os.O_WRONLY)
    try:
        inj = mi.UinputInjector(fd=main, abs_fd=w)
        assert inj.supports_absolute
        inj.move_abs(1000, 2000)
        assert read_events(r) == [(mi.EV_ABS, mi.ABS_X, 1000), (mi.EV_ABS, mi.ABS_Y, 2000), (mi.EV_SYN, 0, 0)]
    finally:
        for fd in (r, w, main):
            os.close(fd)


def test_the_uinput_pointer_applies_the_monitor_mapping():
    r, w = os.pipe()
    main = os.open(os.devnull, os.O_WRONLY)
    try:
        inj = mi.UinputInjector(fd=main, abs_fd=w)
        inj.abs_map = mi.AbsoluteMap((1920, 0, 1920, 1080), (3840, 1080))
        inj.move_abs(0, 65535)
        assert read_events(r)[:2] == [(mi.EV_ABS, mi.ABS_X, 32768), (mi.EV_ABS, mi.ABS_Y, 65535)]
    finally:
        for fd in (r, w, main):
            os.close(fd)


def test_without_the_second_device_absolute_moves_are_off_and_harmless():
    main = os.open(os.devnull, os.O_WRONLY)
    try:
        inj = mi.UinputInjector(fd=main)
        assert not inj.supports_absolute
        inj.move_abs(1, 2)   # nothing to write to, nothing breaks
    finally:
        os.close(main)


# ------------------------------------------------------------------ over the channel

class NoAbsolute(mi.RecordingInjector):
    supports_absolute = False


@needs_openssl
def test_the_agent_advertises_the_feature_only_when_it_can_do_it(tmp_path):
    from test_mesh_input import connect

    async def features(directory, injector):
        async with running(directory, injector=injector) as (port, _inj, token, _fp):
            c = await connect(port)
            await c.send(mi.msg_hello(token, "phone"))
            msg_type, payload = await c.recv()
            assert msg_type == mi.T_HELLO_OK
            return payload[1]

    with_abs = asyncio.run(features(tmp_path / "with", mi.RecordingInjector()))
    assert with_abs & mi.FEATURE_ABS_POINTER
    assert with_abs & mi.FEATURE_TEXT and with_abs & mi.FEATURE_SUNSHINE_PAIR
    without = asyncio.run(features(tmp_path / "without", NoAbsolute()))
    assert not without & mi.FEATURE_ABS_POINTER


@needs_openssl
def test_a_move_abs_from_the_phone_reaches_the_injector(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_move_abs(4000, 60000))
            await c.send(mi.msg_move_abs(0, 0))
            await settle()
            assert [e for e in inj.events if e[0] == "moveabs"] == [("moveabs", 4000, 60000), ("moveabs", 0, 0)]
    asyncio.run(go())


@needs_openssl
def test_a_move_abs_is_ignored_when_the_injector_cannot_do_it(tmp_path):
    async def go():
        async with running(tmp_path, injector=NoAbsolute()) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_move_abs(4000, 60000))
            await c.send(mi.msg_ping(3))
            assert (await c.recv())[0] == mi.T_PONG      # still alive
            assert inj.events == []
    asyncio.run(go())


@needs_openssl
def test_a_damaged_move_abs_is_a_protocol_error_not_a_crash(tmp_path):
    async def go():
        async with running(tmp_path) as (port, inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.encode_frame(mi.T_MOVE_ABS, b"\x00\x01"))
            msg_type, _ = await c.recv()
            assert msg_type == mi.T_ERROR
            assert inj.events == []
    asyncio.run(go())


# ------------------------------------------------------------------ display-info

class Done:
    def __init__(self, stdout="", stderr=""):
        self.stdout, self.stderr = stdout, stderr


def test_display_info_lists_the_session_and_what_the_tools_say():
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd[0])
        return Done("HDMI-1 connected primary 1920x1080+0+0\n   1920x1080 60.00*+" if cmd[0] == "xrandr" else "")

    text = mi.display_info(runner=runner, which=lambda c: "/usr/bin/" + c if c in ("xrandr", "sunshine") else None,
                           environ={"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"})
    assert "XDG_SESSION_TYPE=x11" in text and "XDG_CURRENT_DESKTOP=KDE" in text
    assert "[xrandr]" in text and "HDMI-1 connected primary 1920x1080+0+0" in text
    assert "[wlr-randr] not installed" in text and "[kscreen-doctor] not installed" in text
    assert calls == ["xrandr", "sunshine"]       # only tools that exist are run


def test_display_info_survives_a_tool_that_fails_or_prints_a_lot():
    def runner(cmd, **kwargs):
        if cmd[0] == "xrandr":
            raise OSError("boom")
        return Done("\n".join(f"line {i}" for i in range(100)))

    text = mi.display_info(runner=runner, which=lambda c: "/x/" + c, environ={})
    assert "[xrandr] failed: boom" in text
    assert "line 39" in text and "line 40" not in text and "60 more lines" in text


def test_display_info_prefers_stdout_but_falls_back_to_stderr():
    text = mi.display_info(runner=lambda cmd, **k: Done("", "warning text") if cmd[0] == "xrandr" else Done(""),
                           which=lambda c: "/x/" + c if c == "xrandr" else None, environ={})
    assert "warning text" in text


def test_the_display_info_command_prints_and_succeeds(capsys):
    assert mi.main(["display-info"]) == 0
    assert "Mesh display info" in capsys.readouterr().out
