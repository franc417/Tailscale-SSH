"""`screen-phone`, `screen-normal`, `sunshine-unpair-all`: the order of the steps, what gets backed up and restored, and what is said when it fails.
No real program is started: a fake computer plays Sunshine, the virtual-monitor holder, pgrep and pkill."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402


class Done:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


class Process:
    def __init__(self, pid, exited=False):
        self.pid, self._exited = pid, exited

    def poll(self):
        return 0 if self._exited else None


class Computer:
    """Plays the parts of the computer these commands talk to, and records what they did, in order."""

    def __init__(self, config_dir, sunshine_running=True, service=False, holder_log="new monitor(s): Meta-0 1600x720@60.00 at (1920,0)\n",
                 holder_exits=False, sunshine_log="[x]: Info: Configuration UI available at [https://localhost:47990]\n",
                 sunshine_refuses_to_die=False, port_owner="", fail_with_token=None, agent_running=True):
        self.config_dir, self.sunshine_running, self.service = config_dir, sunshine_running, service
        self.holder_log, self.holder_exits, self.sunshine_log, self.refuses = holder_log, holder_exits, sunshine_log, sunshine_refuses_to_die
        self.events, self.now, self.kills = [], 0.0, []
        self.sunshine_writes_token = True
        self.port_owner = port_owner
        self.fail_with_token = fail_with_token
        self.agent_running = agent_running

    # time
    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    # commands
    def runner(self, cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "cat"]:
            return Done("[Unit]\n", 0) if self.service else Done("", 1)
        if cmd[:2] == ["systemctl", "--user"]:
            self.events.append("systemctl " + cmd[2])
            return Done()
        if cmd[:2] == ["pgrep", "-f"]:
            return Done("999\n", 0) if self.agent_running else Done("", 1)
        if cmd[0] == "ss":
            return Done(self.port_owner, 0)
        if cmd == ["pgrep", "-x", "sunshine"]:
            return Done("4242\n", 0) if self.sunshine_running else Done("", 1)
        if cmd[0] == "pkill":
            self.events.append("pkill " + " ".join(cmd[1:]))
            if not self.refuses or "-9" in cmd:
                self.sunshine_running = False
            return Done()
        raise AssertionError(cmd)

    def popen(self, cmd, stdout=None, **kwargs):
        if cmd[0] == "sunshine":
            self.events.append("start sunshine")
            self.sunshine_running = True
            token_path = os.path.join(self.config_dir, "portal_token")
            stale = bool(self.fail_with_token) and os.path.exists(token_path) and open(token_path).read() == self.fail_with_token
            log_text = ("[x]: Error: [portalgrab] RemoteDesktop Start: no streams in response\n"
                        "[x]: Fatal: Unable to find display or encoder during startup.\n") if stale else self.sunshine_log
            stdout.write(log_text.encode())
            stdout.flush()
            in_portal_mode = "capture = portal" in open(os.path.join(self.config_dir, "sunshine.conf")).read()
            if self.sunshine_writes_token and in_portal_mode and "Fatal" not in log_text:       # only portal capture saves a token
                with open(os.path.join(self.config_dir, "portal_token"), "w") as f:
                    f.write("token-from-this-run")
            return Process(4242)
        if cmd[0] == "gjs":
            self.events.append("start holder " + " ".join(cmd[3:]))
            stdout.write(self.holder_log.encode())
            stdout.flush()
            return Process(777, exited=self.holder_exits)
        raise AssertionError(cmd)

    def kill(self, pid, sig):
        self.kills.append((pid, sig))
        self.events.append("kill holder")

    def tools(self):
        return mi.ProcessTools(runner=self.runner, popen=self.popen, which=lambda n: "/usr/bin/" + n, sleep=self.sleep, clock=self.clock,
                               environ={"XDG_CONFIG_HOME": os.path.dirname(self.config_dir), "XDG_RUNTIME_DIR": "/nonexistent"}, kill=self.kill,
                               read_cmdline=lambda pid: "gjs -m /x/phone-screen/holder.mjs 1600 720 86400")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(mi, "default_dir", lambda: str(tmp_path / "agent"))
    config = tmp_path / "cfg" / "sunshine"
    config.mkdir(parents=True)
    (config / "sunshine.conf").write_text("encoder = vaapi\ncsrf_allowed_origins = http://localhost:47990\n")
    return config


def read(path):
    with open(path) as f:
        return f.read()


# ------------------------------------------------------------------ unpairing

def test_every_paired_phone_is_removed_and_the_rest_of_the_state_is_kept():
    state = json.dumps({"root": {"uniqueid": "ABC", "named_devices": [{"name": "a"}, {"name": "b"}], "devices": [{"cert": "c"}]}, "other": 1})
    cleaned, removed = mi.clear_paired_devices(state)
    assert removed == 3 and json.loads(cleaned) == {"root": {"uniqueid": "ABC"}, "other": 1}
    assert mi.clear_paired_devices('{"root": "x"}') == ('{"root": "x"}', 0)
    with pytest.raises(ValueError):
        mi.clear_paired_devices("{oops")


def test_unpair_all_stops_sunshine_first_backs_up_clears_and_starts_it_again(world):
    (world / "sunshine_state.json").write_text(json.dumps({"root": {"uniqueid": "ABC", "named_devices": [{"name": "Mesh"}, {"name": "Mesh"}]}}))
    pc = Computer(str(world))
    code, text = mi.sunshine_unpair_all(pc.tools())
    assert code == 0 and "removed 2 paired device record(s)" in text and "pairs again by itself" in text
    assert pc.events == ["pkill -x sunshine", "start sunshine"]
    assert json.loads(read(world / "sunshine_state.json")) == {"root": {"uniqueid": "ABC"}}
    assert "Mesh" in read(world / "sunshine_state.json.mesh-backup")


def test_unpair_all_with_no_state_file_changes_nothing_and_still_leaves_sunshine_running(world):
    pc = Computer(str(world))
    code, text = mi.sunshine_unpair_all(pc.tools())
    assert code == 0 and "nothing is paired" in text and pc.events[-1] == "start sunshine"


def test_unpair_all_with_a_broken_state_file_changes_nothing_but_restarts_sunshine(world):
    (world / "sunshine_state.json").write_text("{oops")
    pc = Computer(str(world))
    code, text = mi.sunshine_unpair_all(pc.tools())
    assert code == 1 and "not valid JSON" in text and read(world / "sunshine_state.json") == "{oops"
    assert pc.events[-1] == "start sunshine"


# ------------------------------------------------------------------ stopping Sunshine

def test_a_stubborn_sunshine_is_force_stopped(world):
    pc = Computer(str(world), sunshine_refuses_to_die=True)
    assert "force-stopped" in pc.tools().stop_sunshine() and "pkill -9 -x sunshine" in pc.events


def test_a_sunshine_service_is_stopped_through_systemd(world):
    pc = Computer(str(world), service=True)
    pc.tools().stop_sunshine()
    assert pc.events[0] == "systemctl stop"


# ------------------------------------------------------------------ the phone screen

def test_screen_phone_starts_the_monitor_before_sunshine_and_backs_up_what_it_changes(world):
    (world / "portal_token").write_text("token-of-the-real-screen")
    pc = Computer(str(world))
    shown = []
    code, text = mi.screen_phone("1600x720", pc.tools(), progress=shown.append)
    assert code == 0, text
    assert pc.events == ["pkill -x sunshine", "start holder 1600 720 86400", "start sunshine"]       # monitor first, then Sunshine
    conf = read(world / "sunshine.conf")
    assert "capture = portal" in conf and "output_name = Meta-0" in conf and "encoder = vaapi" in conf and "csrf_allowed_origins" in conf
    assert read(world / "sunshine.conf.mesh-normal") == "encoder = vaapi\ncsrf_allowed_origins = http://localhost:47990\n"
    assert read(world / "portal_token.mesh-normal") == "token-of-the-real-screen"                    # the real screen's approval is kept safe
    assert read(world / "portal_token.mesh-phone") == "token-from-this-run"                          # the new approval is remembered for next time
    assert any("choose the MetaVendor" in line for line in shown)                                    # GNOME will ask the first time
    assert "screen-normal" in text and "Sunshine is ready" in text


def test_screen_phone_the_second_time_reuses_the_approval_and_does_not_ask(world):
    (world / "portal_token").write_text("real")
    (world / "portal_token.mesh-phone").write_text("virtual-approval")
    pc = Computer(str(world))
    pc.sunshine_writes_token = False
    shown = []
    code, _ = mi.screen_phone("1600x720", pc.tools(), progress=shown.append)
    assert code == 0 and read(world / "portal_token") == "virtual-approval" and not any("MetaVendor" in line for line in shown)


def test_screen_phone_run_twice_keeps_the_original_backups(world):
    pc = Computer(str(world))
    mi.screen_phone("1600x720", pc.tools())
    mi.screen_phone("2400x1080", pc.tools())
    assert read(world / "sunshine.conf.mesh-normal") == "encoder = vaapi\ncsrf_allowed_origins = http://localhost:47990\n"
    assert "start holder 2400 1080 86400" in pc.events


def test_screen_phone_that_cannot_make_the_monitor_does_not_touch_sunshine_s_settings(world):
    pc = Computer(str(world), holder_log="GNOME accepted\nRESULT: NO: the stream exists but no new monitor appeared\n", holder_exits=True)
    before = read(world / "sunshine.conf")
    code, text = mi.screen_phone("1600x720", pc.tools())
    assert code == 1 and "could not start" in text and "no new monitor appeared" in text
    assert read(world / "sunshine.conf") == before and not (world / "sunshine.conf.mesh-normal").exists()
    assert "start sunshine" not in pc.events


def test_screen_phone_whose_sunshine_will_not_start_says_why_and_how_to_undo(world):
    pc = Computer(str(world), sunshine_log="[x]: Fatal: Unable to find display or encoder during startup.\n")
    code, text = mi.screen_phone("1600x720", pc.tools())
    assert code == 1 and "Sunshine did NOT start" in text and "Unable to find display" in text and "screen-normal" in text


def test_screen_phone_rejects_a_bad_size_and_missing_programs_before_doing_anything(world):
    pc = Computer(str(world))
    assert mi.screen_phone("banana", pc.tools())[0] == 2 and pc.events == []
    tools = pc.tools()
    tools.which = lambda name: None if name == "gjs" else "/usr/bin/" + name
    code, text = mi.screen_phone("1600x720", tools)
    assert code == 1 and "gjs" in text and pc.events == []


def test_screen_normal_puts_everything_back_and_remembers_the_virtual_approval(world):
    (world / "portal_token").write_text("real")
    pc = Computer(str(world))
    mi.screen_phone("1600x720", pc.tools())
    pc.events.clear()
    code, text = mi.screen_normal(pc.tools())
    assert code == 0, text
    assert read(world / "sunshine.conf") == "encoder = vaapi\ncsrf_allowed_origins = http://localhost:47990\n"
    assert read(world / "portal_token") == "real" and not (world / "portal_token.mesh-normal").exists()
    assert read(world / "portal_token.mesh-phone") == "token-from-this-run" and not (world / "sunshine.conf.mesh-normal").exists()
    assert pc.events[0] == "pkill -x sunshine" and "kill holder" in pc.events and pc.events[-1] == "start sunshine"
    assert pc.kills == [(777, 15)]


def test_screen_normal_when_nothing_was_changed_just_restarts_sunshine(world):
    pc = Computer(str(world))
    code, text = mi.screen_normal(pc.tools())
    assert code == 0 and "already the normal ones" in text and pc.events[-1] == "start sunshine"
    assert read(world / "sunshine.conf") == "encoder = vaapi\ncsrf_allowed_origins = http://localhost:47990\n"


def test_screen_normal_with_no_real_screen_approval_leaves_no_virtual_one_behind(world):
    pc = Computer(str(world))
    mi.screen_phone("1600x720", pc.tools())
    mi.screen_normal(pc.tools())
    assert not (world / "portal_token").exists()                         # nothing of the virtual screen is left where normal mode looks


# ------------------------------------------------------------------ undoing a hand-made phone screen

def test_config_lines_can_be_removed_without_touching_the_others():
    text = "encoder = vaapi\ncapture = portal\n  output_name = Meta-0\ncapture_extra = 1\n"
    assert mi.remove_config_option(mi.remove_config_option(text, "capture"), "output_name") == "encoder = vaapi\ncapture_extra = 1\n"
    assert mi.remove_config_option("", "capture") == ""


def test_screen_normal_undoes_a_phone_screen_that_was_set_up_by_hand(world):
    (world / "sunshine.conf").write_text("encoder = vaapi\ncapture = portal\noutput_name = Meta-0\n")
    (world / "portal_token").write_text("meta-0-approval")
    (world / "portal_token.old").write_text("real-screen-approval")
    pc = Computer(str(world))
    code, text = mi.screen_normal(pc.tools())
    assert code == 0 and "removed leftover phone-screen settings" in text
    assert read(world / "sunshine.conf") == "encoder = vaapi\n"
    assert read(world / "portal_token") == "real-screen-approval" and not (world / "portal_token.old").exists()
    assert read(world / "portal_token.mesh-phone") == "meta-0-approval"
    assert pc.events[-1] == "start sunshine"


def test_screen_normal_does_not_touch_a_portal_setup_that_is_not_the_phone_screen(world):
    (world / "sunshine.conf").write_text("capture = portal\noutput_name = HDMI-1\n")
    pc = Computer(str(world))
    mi.screen_normal(pc.tools())
    assert read(world / "sunshine.conf") == "capture = portal\noutput_name = HDMI-1\n"


def test_a_failed_normal_start_points_to_diagnose(world):
    pc = Computer(str(world), sunshine_log="[x]: Fatal: Unable to find display or encoder during startup.\n")
    code, text = mi.screen_normal(pc.tools())
    assert code == 1 and "python3 mesh_input.py diagnose" in text


def test_a_port_still_held_by_another_program_stops_the_start_and_names_the_holder(world):
    pc = Computer(str(world), port_owner='LISTEN 0 4096 0.0.0.0:48010 0.0.0.0:* users:(("sunshine",pid=999,fd=40))')
    code, text = mi.screen_normal(pc.tools())
    assert code == 1 and "port 48010 is still held" in text and "pid=999" in text and "kill -9" in text
    assert "start sunshine" not in pc.events


# ------------------------------------------------------------------ a saved approval that no longer works

def test_screen_phone_drops_a_saved_approval_that_does_not_work_and_lets_gnome_ask_again(world):
    (world / "portal_token.mesh-phone").write_text("stale")
    pc = Computer(str(world), fail_with_token="stale")
    shown = []
    code, text = mi.screen_phone("1920x864", pc.tools(), progress=shown.append)
    assert code == 0, text
    assert pc.events.count("start sunshine") == 2 and "the saved approval for the virtual screen did not work" in text
    assert read(world / "portal_token.mesh-phone") == "token-from-this-run"             # the fresh approval replaces the stale one
    assert any("choose the MetaVendor" in line for line in shown)


def test_screen_phone_failure_shows_what_sunshine_s_log_says_about_the_screen_share(world):
    pc = Computer(str(world), sunshine_log="[x]: Error: [portalgrab] RemoteDesktop Start: no streams in response\n"
                                           "[x]: Info: [wayland] Found interface: wl_output(3) version 4\n"
                                           "[x]: Error: [pipewire] something about a stream\n"
                                           "[x]: Fatal: Unable to find display or encoder during startup.\n")
    code, text = mi.screen_phone("1920x864", pc.tools())
    assert code == 1 and "What Sunshine's log says about it:" in text
    assert "RemoteDesktop Start: no streams in response" in text and "something about a stream" in text
    assert "Found interface" not in text and "screen-normal" in text


def test_a_working_saved_approval_is_used_once_and_not_dropped(world):
    (world / "portal_token.mesh-phone").write_text("good")
    pc = Computer(str(world), fail_with_token="stale")
    code, _ = mi.screen_phone("1920x864", pc.tools())
    assert code == 0 and pc.events.count("start sunshine") == 1


def test_screen_phone_warns_when_the_agent_that_pairs_the_phone_is_not_running(world):
    pc = Computer(str(world), agent_running=False)
    code, text = mi.screen_phone("1920x864", pc.tools())
    assert code == 0 and "WARNING: the Mesh agent is not running" in text and "python3 mesh_input.py run" in text


def test_no_warning_when_the_agent_is_running(world):
    pc = Computer(str(world))
    code, text = mi.screen_phone("1920x864", pc.tools())
    assert code == 0 and "WARNING" not in text


def test_unpair_all_also_warns_about_a_missing_agent(world):
    (world / "sunshine_state.json").write_text(json.dumps({"root": {"named_devices": [{"name": "Mesh"}]}}))
    pc = Computer(str(world), agent_running=False)
    code, text = mi.sunshine_unpair_all(pc.tools())
    assert code == 0 and "WARNING: the Mesh agent is not running" in text


# ------------------------------------------------------------------ screen-status

MONITORS_TEXT = """Mesh diagnostics:

[GNOME monitors]
monitor eDP-1 (IVO): now 1920x1080 at 60.01 Hz
monitor Meta-0 (MetaVendor): now 1920x864 at 60.00 Hz
logical monitor at (0, 0) scale 1.0, primary

[sunshine version]
x
"""


def test_the_status_says_the_monitor_holder_is_gone_when_its_process_is_gone(world, tmp_path):
    pc = Computer(str(world))
    pc.sunshine_running = False
    holder_dir = tmp_path / "agent" / "phone-screen"
    holder_dir.mkdir(parents=True)
    (holder_dir / "holder.pid").write_text("777")
    (holder_dir / "holder.log").write_text("GNOME accepted\nnew monitor(s): Meta-0 1920x864@60.00 at (1920,0)\n")
    tools = pc.tools()
    tools.read_cmdline = lambda pid: ""                       # the process no longer exists
    text = mi.screen_status(tools, monitors_text=MONITORS_TEXT)
    assert "virtual monitor holder: NOT running" in text and "holder log: new monitor(s): Meta-0" in text
    assert "sunshine running: NO" in text


def test_the_status_lists_monitors_settings_saved_files_pairing_and_the_log(world, tmp_path):
    (world / "sunshine.conf").write_text("encoder = vaapi\ncapture = portal\noutput_name = Meta-0\n")
    (world / "portal_token").write_text("t")
    (world / "portal_token.mesh-normal").write_text("n")
    (world / "sunshine_state.json").write_text(json.dumps({"root": {"named_devices": [{"name": "25128RN17A", "cert": "X", "uuid": "abcdefgh"}]}}))
    (world / mi.SUNSHINE_LOG_NAME).write_text("--- started by mesh_input.py at x ---\n[x]: Info: [wayland] Found interface: wl_output\n"
                                              "[x]: Error: [portalgrab] RemoteDesktop Start: no streams in response\n[x]: Info: CLIENT CONNECTED\n")
    holder_dir = tmp_path / "agent" / "phone-screen"
    holder_dir.mkdir(parents=True)
    (holder_dir / "holder.pid").write_text("777")
    pc = Computer(str(world))
    text = mi.screen_status(pc.tools(), monitors_text=MONITORS_TEXT)
    assert "virtual monitor holder: running (pid 777)" in text
    assert "monitor Meta-0 (MetaVendor): now 1920x864" in text and "logical monitor" in text
    assert "capture = portal, output_name = Meta-0, encoder = vaapi" in text
    assert "saved files: portal_token, portal_token.mesh-normal" in text
    assert "mesh agent running: yes" in text and "1 paired device record(s)" in text and "name='25128RN17A'" in text
    assert "RemoteDesktop Start: no streams in response" in text and "CLIENT CONNECTED" in text and "Found interface" not in text


def test_the_status_works_on_a_machine_where_nothing_was_ever_set_up(world):
    pc = Computer(str(world))
    pc.sunshine_running = False
    pc.agent_running = False
    text = mi.screen_status(pc.tools(), monitors_text="[GNOME monitors]\nmonitor eDP-1 (IVO): now 1920x1080 at 60.01 Hz\n")
    assert "never started" in text and "mesh agent running: NO" in text and "no sunshine_state.json" in text and "monitor eDP-1" in text
