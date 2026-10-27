"""`diagnose` (also `display-info`): find the desktop over SSH, read GNOME's monitors, Sunshine's settings and log, the hardware."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402

MUTTER = (
    "(uint32 3, [(('eDP-1', 'AUO', '0x239d', '0x00000000'), [('1920x1080@60.002', 1920, 1080, 60.002, 1.0, [1.0, 2.0], "
    "{'is-current': <true>, 'is-preferred': <true>}), ('1920x1080@48.001', 1920, 1080, 48.001, 1.0, [1.0, 2.0], {}), "
    "('1680x1050@60.000', 1680, 1050, 60.0, 1.0, [1.0], {}), ('1280x720@60.000', 1280, 720, 60.0, 1.0, [1.0], {})], "
    "{'is-builtin': <true>, 'display-name': <'Built-in display'>}), (('HDMI-1', 'DEL', 'DELL U2720Q', 'ABC'), "
    "[('3840x2160@59.997', 3840, 2160, 59.997, 1.0, [1.0, 2.0], {'is-current': <true>})], {})], "
    "[(0, 0, 1.0, uint32 0, true, [('eDP-1', 'AUO', '0x239d', '0x00000000')], {}), "
    "(1920, 0, 1.0, uint32 0, false, [('HDMI-1', 'DEL', 'DELL U2720Q', 'ABC')], {})], "
    "{'layout-mode': <uint32 1>, 'supports-changing-layout-mode': <false>})"
)


class Done:
    def __init__(self, stdout="", stderr=""):
        self.stdout, self.stderr = stdout, stderr


# ------------------------------------------------------------------ finding the desktop

def test_the_desktop_is_found_from_its_sockets_when_nothing_is_set():
    listing = {"/run/user/1000": ["bus", "pipewire-0", "wayland-0", "wayland-0.lock", "wayland-1"], "/tmp/.X11-unix": ["X1", "X0"]}
    found = mi.graphical_environment(
        environ={}, listdir=lambda d: listing[d], exists=lambda p: True, uid=lambda: 1000)
    assert found == {"XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0",
                     "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "DISPLAY": ":0"}


def test_what_is_already_set_is_never_overridden():
    listing = {"/run/user/1000": ["wayland-0"], "/tmp/.X11-unix": ["X0"]}
    found = mi.graphical_environment(
        environ={"XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-7", "DISPLAY": ":9",
                 "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"},
        listdir=lambda d: listing[d], exists=lambda p: True, uid=lambda: 1000)
    assert found == {}


def test_a_session_with_no_desktop_sockets_finds_nothing_and_does_not_fail():
    def listdir(path):
        raise FileNotFoundError(path)

    assert mi.graphical_environment(environ={}, listdir=listdir, exists=lambda p: False, uid=lambda: 1000) == {}
    assert mi.graphical_environment(environ={}, listdir=listdir, exists=lambda p: True, uid=lambda: 1000) == {
        "XDG_RUNTIME_DIR": "/run/user/1000", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}


def test_a_lock_file_or_a_pipewire_socket_is_not_mistaken_for_the_display():
    found = mi.graphical_environment(
        environ={"XDG_RUNTIME_DIR": "/r"}, listdir=lambda d: ["wayland-0.lock", "pipewire-0"] if d == "/r" else [],
        exists=lambda p: p == "/r", uid=lambda: 1)
    assert "WAYLAND_DISPLAY" not in found and "DISPLAY" not in found


# ------------------------------------------------------------------ GNOME monitors

def test_the_gnome_monitor_state_is_summarised():
    lines = mi.summarize_mutter_state(MUTTER)
    assert lines[0] == ("monitor eDP-1 (AUO): now 1920x1080 at 60.00 Hz, scale 1.0, scales offered [1.0, 2.0]; "
                        "4 modes; sizes offered: 1920x1080, 1680x1050, 1280x720")
    assert lines[1].startswith("monitor HDMI-1 (DEL): now 3840x2160 at 60.00 Hz")
    assert lines[1].endswith("1 modes; sizes offered: 3840x2160")
    assert lines[2] == "logical monitor at (0, 0) scale 1.0, primary"
    assert lines[3] == "logical monitor at (1920, 0) scale 1.0"
    assert len(lines) == 4


def test_a_monitor_without_a_current_mode_is_still_listed():
    text = "(uint32 1, [(('DP-1', 'X', 'Y', 'Z'), [('800x600@60.000', 800, 600, 60.0, 1.0, [1.0], {})], {})], [], {})"
    lines = mi.summarize_mutter_state(text)
    assert lines == ["monitor DP-1 (X): current mode not found; 1 modes; sizes offered: 800x600"]


def test_text_that_is_not_gnomes_answer_gives_nothing_and_does_not_fail():
    assert mi.summarize_mutter_state("") == []
    assert mi.summarize_mutter_state("Error: GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown") == []


# ------------------------------------------------------------------ Sunshine's log and settings

def test_only_the_informative_log_lines_are_kept_and_the_newest_win():
    log = "\n".join(
        ["[info] nothing to see"] * 3
        + [f"[info] Encoder: vaapi attempt {i}" for i in range(80)]
        + ["[warning] Frame capture took too long", "[info] something dull"]
    )
    kept = mi.filter_sunshine_log(log, keep=5)
    assert len(kept) == 5
    assert kept[-1] == "[warning] Frame capture took too long"
    assert kept[0].endswith("attempt 76")
    assert "nothing to see" not in "\n".join(mi.filter_sunshine_log(log, keep=500))


def test_secrets_in_the_config_are_hidden_and_comments_dropped():
    conf = "# a comment\n\nencoder = vaapi\nsunshine_password = hunter2\ncapture=kms\napi_token = abc\n"
    assert mi._redact_config(conf) == ["encoder = vaapi", "sunshine_password = (hidden)", "capture=kms", "api_token = (hidden)"]


# ------------------------------------------------------------------ the whole report

FILES = {
    "/proc/cpuinfo": "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz\n",
    "/proc/meminfo": "MemTotal:       16273848 kB\nMemFree: 1 kB\n",
    "/proc/loadavg": "0.52 0.40 0.31 1/500 1234\n",
}


def fake_world(tools, conf="encoder = vaapi\ncapture = kms\n", log="[info] Encoder: vaapi\n[warning] slow\n"):
    def runner(cmd, **kwargs):
        assert "env" in kwargs                                    # tools are run against the found desktop
        out = tools[cmd[0]]
        if isinstance(out, Exception):
            raise out
        return Done(out(cmd) if callable(out) else out)

    def read_file(path):
        if path.endswith("sunshine.conf"):
            if conf is None:
                raise FileNotFoundError(path)
            return conf
        if path.endswith("sunshine.log"):
            return log
        if path in FILES:
            return FILES[path]
        raise FileNotFoundError(path)

    return runner, (lambda c: "/usr/bin/" + c if c in tools else None), read_file


def report(tools, **kwargs):
    runner, which, read_file = fake_world(tools, **kwargs)
    return mi.display_info(runner=runner, which=which, read_file=read_file, environ={"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME", "HOME": "/home/x", "XDG_CONFIG_HOME": "/home/x/.config"},
                           listdir=lambda d: ["wayland-0"] if d.startswith("/run") else ["X0"], exists=lambda p: True, uid=lambda: 1000)


def test_the_report_covers_session_monitors_sunshine_and_hardware():
    text = report({
        "loginctl": "2 1000 mkz seat0 1234 user tty2",
        "gdbus": MUTTER,
        "gsettings": "1.0",
        "sunshine": "[info] noise\n[2026-10-26]: Info: Sunshine version: 2026.914.233613 commit: abc\n",
        "journalctl": "[info] Encoder: vaapi\n[info] boring\n[warning] Frame capture took too long\n",
        "lspci": "00:02.0 VGA compatible controller: Intel UHD Graphics 620\n00:1f.3 Audio device: Intel",
        "vainfo": "vainfo: Driver version: Intel iHD driver\n      VAProfileH264Main : VAEntrypointEncSlice\n      VAProfileNone : VAEntrypointVideoProc",
    })
    assert "XDG_CURRENT_DESKTOP=GNOME" in text
    assert "found the running desktop" in text and "WAYLAND_DISPLAY=wayland-0" in text
    assert "[loginctl sessions]" in text and "mkz seat0" in text
    assert "[GNOME monitors]" in text and "monitor eDP-1 (AUO): now 1920x1080 at 60.00 Hz" in text
    assert "org.gnome.desktop.interface text-scaling-factor = 1.0" in text
    assert "Sunshine version: 2026.914.233613" in text and "noise" not in text
    assert "[sunshine.conf (secrets hidden)]" in text and "encoder = vaapi" in text and "capture = kms" in text
    assert "Frame capture took too long" in text and "boring" not in text
    assert "Intel(R) Core(TM) i5-8250U" in text and "MemTotal:" in text and "load average: 0.52" in text
    assert "Intel UHD Graphics 620" in text and "Audio device" not in text
    assert "Intel iHD driver" in text and "VAEntrypointEncSlice" in text and "VideoProc" not in text


def test_missing_tools_are_named_and_nothing_fails():
    text = report({})
    for name in ("loginctl", "gdbus", "gsettings", "xrandr", "wlr-randr", "sunshine", "lspci", "journalctl"):
        assert f"[{name}] not installed" in text
    assert "(no log found)" in text or "sunshine log" in text


def test_a_failing_tool_is_reported_and_the_rest_still_runs():
    text = report({"gdbus": OSError("no bus"), "gsettings": "1.25", "lspci": "00:02.0 VGA compatible controller: X"})
    assert "[gdbus] failed: no bus" in text
    assert "text-scaling-factor = 1.25" in text
    assert "[hardware]" in text


def test_an_unreadable_config_is_mentioned_and_an_empty_one_means_defaults():
    assert "[sunshine.conf] not readable" in report({}, conf=None)
    assert "(empty: all defaults)" in report({}, conf="# only comments\n")


def test_gnome_output_that_is_not_understood_is_shown_raw_rather_than_dropped():
    text = report({"gdbus": "something unexpected from this gnome version"})
    assert "(not understood) something unexpected" in text


def test_the_log_is_read_from_the_file_when_there_is_no_journal():
    text = report({}, log="[info] Encoder: software x264\n")
    assert "Encoder: software x264" in text


def test_both_command_names_print_the_report_and_succeed(capsys):
    for name in ("diagnose", "display-info"):
        assert mi.main([name]) == 0
        assert "Mesh diagnostics" in capsys.readouterr().out
