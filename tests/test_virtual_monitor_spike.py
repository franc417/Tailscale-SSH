"""`spike-virtual-monitor`: the size parser, the checks before running, how the gjs script is launched, and that the embedded script is well formed."""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402


class Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def have_all(name):
    return "/usr/bin/" + name


def run_spike(tmp_path, runner, size="1600x720", seconds=8, which=have_all, environ=None):
    def make_temp(text):
        path = tmp_path / "spike.mjs"
        path.write_text(text)
        return str(path)
    return mi.spike_virtual_monitor(size, seconds, runner=runner, which=which, environ=environ if environ is not None else {"XDG_RUNTIME_DIR": "/run/user/1000"},
                                    listdir=lambda d: ["wayland-0"] if d.startswith("/run") else [], exists=lambda p: True, uid=lambda: 1000, make_temp=make_temp)


def test_sizes_are_read_and_checked():
    assert mi.parse_size("1600x720") == (1600, 720) and mi.parse_size(" 2400 X 1080 ") == (2400, 1080)
    for bad in ("", "1600", "1600x", "axb", "1601x720", "100x100", "9000x720", "1600x720x2"):
        with pytest.raises(ValueError):
            mi.parse_size(bad)


def test_a_bad_size_is_reported_without_running_anything(tmp_path):
    def runner(*a, **k):
        raise AssertionError("must not run")
    code, text = run_spike(tmp_path, runner, size="banana")
    assert code == 2 and "1600x720" in text


def test_missing_tools_are_named_with_the_package_to_install(tmp_path):
    code, text = run_spike(tmp_path, lambda *a, **k: Done(), which=lambda n: None)
    assert code == 1 and "gjs (sudo pacman -S gjs)" in text and "gst-plugin-pipewire" in text


def test_the_script_is_run_by_gjs_as_a_module_with_the_size_and_the_desktop_environment(tmp_path):
    seen = {}

    def runner(cmd, **kwargs):
        seen["cmd"], seen["env"], seen["script"] = cmd, kwargs["env"], open(cmd[2]).read()
        return Done(stdout="before: eDP-1 1920x1080@60.01 primary at (0,0)\nRESULT: YES: a virtual monitor appeared at exactly 1600x720\n")
    code, text = run_spike(tmp_path, runner, size="1600x720", seconds=5)
    assert code == 0 and seen["cmd"][:2] == ["gjs", "-m"] and seen["cmd"][3:] == ["1600", "720", "5"]
    assert seen["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"        # found even over SSH
    assert seen["script"] == mi.VIRTUAL_MONITOR_SPIKE_JS
    assert text.startswith("Mesh virtual-monitor spike (paste all of this):") and "RESULT: YES" in text
    assert not os.path.exists(seen["cmd"][2])                                                # the temporary script is removed


def test_the_hold_time_is_kept_within_sane_limits(tmp_path):
    held = []
    run_spike(tmp_path, lambda cmd, **k: (held.append(cmd[-1]), Done())[1], seconds=0)
    run_spike(tmp_path, lambda cmd, **k: (held.append(cmd[-1]), Done())[1], seconds=9999)
    assert held == ["2", "60"]


def test_a_run_that_never_finishes_says_so(tmp_path):
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)
    code, text = run_spike(tmp_path, runner)
    assert code == 1 and "did not finish" in text


def test_errors_from_gjs_are_included_for_pasting(tmp_path):
    code, text = run_spike(tmp_path, lambda cmd, **k: Done(stderr="Error: unexpected token", returncode=1))
    assert code == 1 and "unexpected token" in text


def test_the_embedded_script_uses_the_gnome_calls_the_plan_relies_on():
    js = mi.VIRTUAL_MONITOR_SPIKE_JS
    for needed in ("CreateSession", "RecordVirtual", "PipeWireStreamAdded", "GetCurrentState", "pipewiresrc", "'Stop'"):
        assert needed in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is only used to check the script's syntax")
def test_the_embedded_script_is_valid_javascript(tmp_path):
    path = tmp_path / "spike.mjs"
    path.write_text(mi.VIRTUAL_MONITOR_SPIKE_JS)
    done = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
