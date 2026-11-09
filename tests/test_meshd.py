"""meshd: the host command line (status screen, install plan, Sunshine account, services, supervisor, pair)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402
import meshd  # noqa: E402

TS_RUNNING = {"BackendState": "Running", "Self": {"UserID": 7, "DNSName": "mkz.tail1234.ts.net.", "TailscaleIPs": ["100.67.163.21"]},
              "User": {"7": {"LoginName": "franc@example.com"}}}


class Done:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class FakeSystem:
    """Stands in for every command meshd runs: systemctl --user, ip, loginctl, pgrep, tailscale, lspci, clipboard tools."""

    def __init__(self):
        self.units = {}
        self.calls = []
        self.graphical = True
        self.route = "1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.5 uid 1000 \\    cache"
        self.linger = True
        self.linger_enable_ok = True
        self.tailscale = TS_RUNNING
        self.pgrep_hit = False
        self.lspci = ""
        self.fail = set()

    def unit(self, name, active="active", enabled="enabled", mono=7_800_000_000, restarts=0):
        self.units[name] = {"LoadState": "loaded", "ActiveState": active, "NRestarts": str(restarts),
                            "ActiveEnterTimestampMonotonic": str(mono), "UnitFileState": enabled}

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        if args[:3] == ["systemctl", "--user", "show"]:
            u = self.units.get(args[3], {"LoadState": "not-found", "ActiveState": "inactive"})
            return Done("\n".join(f"{k}={v}" for k, v in u.items()))
        if args[:3] == ["systemctl", "--user", "is-active"]:
            return Done("active" if self.graphical else "inactive")
        if args[:2] == ["systemctl", "--user"]:
            verb = args[2]
            if verb in self.fail:
                return Done("", 1, "boom")
            if verb in ("start", "restart", "enable") and len(args) > 3:
                name = args[-1]
                if name in self.units:
                    self.units[name]["ActiveState"] = "active"
            return Done()
        if args[0] == "ip":
            return Done(self.route) if self.route else Done("", 2, "unreachable")
        if args[0] == "loginctl" and args[1] == "show-user":
            return Done("Linger=yes" if self.linger else "Linger=no")
        if args[0] == "loginctl" and args[1] == "enable-linger":
            return Done("", 0 if self.linger_enable_ok else 1)
        if args[0] == "pgrep":
            return Done("", 0 if self.pgrep_hit else 1)
        if args[0] == "tailscale":
            return Done(json.dumps(self.tailscale))
        if args[0] == "lspci":
            return Done(self.lspci)
        if args[0] in ("wl-copy", "xclip", "xsel"):
            self.copied = kw.get("input")
            return Done()
        return Done("", 127, "unknown")


class FakeTools:
    def __init__(self):
        self.running, self.service, self.answers, self.why = True, True, True, "the secure port 47984 answers"
        self.stopped = 0

    def sunshine_running(self):
        return self.running

    def sunshine_is_service(self):
        return self.service

    def stop_sunshine(self):
        self.stopped += 1
        return "stopped"

    def sunshine_https_answers(self):
        return self.answers, self.why


class FakeLink:
    def __init__(self, new=False, accepted=("alice", "pw-alice-1")):
        self.new, self.accepted, self.saved, self.down = new, accepted, None, False
        self.created, self.apps_added = None, False
        self.login_path = "/x/sunshine-login.json"

    def _check_up(self):
        if self.down:
            raise mi.SunshineError(mi.S_UNREACHABLE, "does not answer")

    def needs_first_login(self):
        self._check_up()
        return self.new

    def has_login(self):
        return self.saved is not None

    def save_login(self, u, p):
        self.saved = (u, p)

    def forget_login(self):
        self.saved = None

    def check_login(self):
        self._check_up()
        if self.saved != self.accepted:
            raise mi.SunshineError(mi.S_LOGIN_REFUSED, "refused")

    def create_login(self, username="mesh", password=None):
        self.created = (username, password)
        self.saved = (username, password)
        return password

    def ensure_desktop_app(self):
        self.apps_added = True
        return True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def sleep(self, seconds):
        self.now += seconds

    def wall(self):
        return self.now


@pytest.fixture
def world(tmp_path):
    system, tools, link, clock = FakeSystem(), FakeTools(), FakeLink(), Clock()
    ports = {47600, ("1.1.1.1", 443)}
    system.unit(mi.SERVICE_NAME)
    system.unit(mi.SUNSHINE_SERVICE_NAME)
    system.unit(meshd.SUPERVISOR_UNIT)
    env = meshd.Env(directory=str(tmp_path / "agent"), port=47600, runner=system, which=lambda n: "/usr/bin/" + n, environ={}, tools=tools,
                    link=link, connect=lambda h, p, t=2.0: p in ports or (h, p) in ports, uptime=lambda: 10000.0,
                    unit_directory=str(tmp_path / "units"), wall=clock.wall, sleep=clock.sleep, user="mkz", access=lambda p, m: True)
    os.makedirs(env.directory)
    with open(os.path.join(env.directory, meshd.STATE_FILE), "w") as f:
        json.dump({"time": clock.now - 4}, f)
    link.saved = link.accepted
    env.ports, env.system, env.tools_, env.link_, env.clock = ports, system, tools, link, clock
    return env


def rows(env):
    return {r.name: r for r in meshd.gather_status(env)[1]}


# ---- look

def test_boxes_line_up_with_and_without_colour():
    for color in (False, True):
        style = meshd.Style(color)
        text = meshd.box([style.bold("Mesh Host"), style.dim("a longer second line")], style, width=40)
        widths = {meshd.visible_len(line) for line in text.splitlines()}
        assert len(widths) == 1 and widths.pop() >= 40


def test_no_colour_codes_when_colour_is_off():
    assert "\x1b" not in meshd.Style(False).ok("x")
    assert "\x1b" in meshd.Style(True).ok("x")


def test_durations_read_naturally():
    assert meshd.fmt_duration(5) == "5 s"
    assert meshd.fmt_duration(300) == "5 min"
    assert meshd.fmt_duration(2 * 3600 + 14 * 60) == "2h 14m"
    assert meshd.fmt_duration(3 * 86400 + 4 * 3600) == "3d 4h"
    assert meshd.fmt_duration(None) == "?"


# ---- status

def test_everything_healthy_is_all_ok(world):
    r = rows(world)
    assert all(row.level == "ok" for row in r.values()), {k: (v.level, v.word, v.detail) for k, v in r.items()}
    assert "36 min" in r["Input agent"].detail          # 10000 s uptime - 7800 s since boot at start
    text = meshd.status_text(world, meshd.Style(False))
    assert "Everything is running." in text and "Mesh Host" in text and "mkz" in text


def test_a_stopped_agent_is_a_problem_that_names_the_fix(world):
    world.system.unit(mi.SERVICE_NAME, active="inactive")
    row = rows(world)["Input agent"]
    assert (row.level, row.word) == ("bad", "stopped") and "meshd start agent" in row.detail


def test_an_agent_whose_port_does_not_answer_is_stuck(world):
    world.ports.discard(47600)
    assert rows(world)["Input agent"].word == "stuck"


def test_an_agent_started_by_hand_is_flagged_as_not_kept_alive(world):
    world.system.units.pop(mi.SERVICE_NAME)
    row = rows(world)["Input agent"]
    assert (row.level, row.word) == ("wait", "by hand") and "meshd enable" in row.detail


def test_no_agent_anywhere_means_not_set_up(world):
    world.system.units.pop(mi.SERVICE_NAME)
    world.ports.discard(47600)
    assert rows(world)["Input agent"].word == "not set up"


def test_sunshine_waits_for_the_desktop_session_instead_of_failing(world):
    world.system.unit(mi.SUNSHINE_SERVICE_NAME, active="inactive")
    world.system.graphical = False
    row = rows(world)["Sunshine"]
    assert (row.level, row.word) == ("wait", "waiting") and "log in" in row.detail


def test_sunshine_that_accepts_connections_but_never_answers_is_stuck(world):
    world.tools_.answers, world.tools_.why = False, "the secure port 47984 accepts connections but never answers"
    row = rows(world)["Sunshine"]
    assert row.word == "stuck" and "never answers" in row.detail
    assert rows(world)["Sunshine login"].level == "wait"       # not judged while Sunshine is down


def test_missing_sunshine_says_install(world):
    world.system.units.pop(mi.SUNSHINE_SERVICE_NAME)
    world.tools_.running = False
    world.which = lambda n: None if n == "sunshine" else "/usr/bin/" + n
    row = rows(world)["Sunshine"]
    assert (row.word, "meshd install" in row.detail) == ("missing", True)


def test_login_row_states(world):
    assert rows(world)["Sunshine login"].word == "ready"
    world.link_.saved = None
    assert rows(world)["Sunshine login"].word == "not set"
    world.link_.saved = ("alice", "wrong")
    assert rows(world)["Sunshine login"].word == "refused"


def test_supervisor_that_has_not_checked_in_is_silent(world):
    with open(os.path.join(world.directory, meshd.STATE_FILE), "w") as f:
        json.dump({"time": world.clock.now - 500}, f)
    assert rows(world)["Supervisor"].word == "silent"
    world.system.unit(meshd.SUPERVISOR_UNIT, active="inactive")
    assert rows(world)["Supervisor"].word == "stopped"


def test_boot_row_needs_linger_and_an_enabled_agent(world):
    assert rows(world)["Starts at boot"].level == "ok"
    world.system.linger = False
    assert rows(world)["Starts at boot"].word == "at login"


def test_network_row_offline_and_local_only(world):
    world.system.route = ""
    assert rows(world)["Network"].word == "offline"
    world.system.route = "1.1.1.1 via 192.168.43.1 dev wlan0 src 192.168.43.9 uid 1000"
    world.ports.discard(("1.1.1.1", 443))
    row = rows(world)["Network"]
    assert row.level == "ok" and "no internet" in row.detail


def test_status_json_says_whether_all_is_well(world):
    assert json.loads(meshd.status_json(world))["ok"] is True
    world.ports.discard(47600)
    assert json.loads(meshd.status_json(world))["ok"] is False


# ---- install

OS = {
    "arch": 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\n',
    "manjaro": 'NAME="Manjaro Linux"\nID=manjaro\nID_LIKE=arch\nPRETTY_NAME="Manjaro Linux"\n',
    "ubuntu": 'PRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\nID_LIKE=debian\n',
    "fedora": 'PRETTY_NAME="Fedora Linux 41"\nID=fedora\n',
    "suse": 'PRETTY_NAME="openSUSE Tumbleweed"\nID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
}


def test_distribution_families():
    assert meshd.detect_distro(OS["arch"])[0] == "arch"
    assert meshd.detect_distro(OS["manjaro"])[0] == "arch"
    assert meshd.detect_distro(OS["ubuntu"])[0] == "debian"
    assert meshd.detect_distro(OS["fedora"])[0] == "fedora"
    assert meshd.detect_distro(OS["suse"])[0] == "suse"
    assert meshd.detect_distro("ID=plan9\n")[0] == "unknown"
    assert meshd.detect_distro("")[0] == "unknown"


def test_a_bare_arch_machine_gets_every_step_with_real_commands():
    plan = meshd.install_plan("arch", False, False, False, False, "intel")
    commands = [s.command for s in plan]
    assert any("tailscale.com/install.sh" in c for c in commands)
    assert "sudo tailscale up" in commands
    assert any("lizardbyte/sunshine" in c for c in commands)
    assert any("/dev/udev" not in c and "85-sunshine.rules" in c for c in commands)
    assert any("intel-media-driver" in c for c in commands)


def test_nothing_is_planned_when_everything_is_there():
    assert meshd.install_plan("arch", True, True, True, True, "") == []


def test_sunshine_per_family():
    fedora = meshd.install_plan("fedora", True, True, False, True, "")
    assert "copr enable" in fedora[0].command and "not tested" in fedora[0].note
    debian = meshd.install_plan("debian", True, True, False, True, "")
    assert debian[0].command == "" and "docs.lizardbyte.dev" in debian[0].note       # honest: a manual step


def test_other_distributions_get_a_manual_driver_step():
    plan = meshd.install_plan("debian", True, True, True, True, "amd")
    assert len(plan) == 1 and plan[0].command == ""


def test_install_runs_the_steps_after_asking_and_writes_the_launcher(world, tmp_path):
    world.system.tailscale = {"BackendState": "Stopped", "Self": {}}
    ran, said = [], []
    code = meshd.install(world, say=said.append, ask=lambda q: "y", os_release=OS["arch"], shell=lambda c: ran.append(c) or 0,
                         bin_dir=str(tmp_path / "bin"))
    assert code == 0
    assert "sudo tailscale up" in ran
    launcher = tmp_path / "bin" / "meshd"
    assert launcher.exists() and os.access(launcher, os.X_OK) and "meshd.py" in launcher.read_text()
    assert any("Detected: Arch Linux" in line for line in said)


def test_install_skips_what_the_person_declines_and_says_so(world, tmp_path):
    world.system.tailscale = {"BackendState": "Stopped", "Self": {}}
    ran = []
    code = meshd.install(world, say=lambda t: None, ask=lambda q: "n", os_release=OS["arch"], shell=lambda c: ran.append(c) or 0,
                         bin_dir=str(tmp_path / "bin"))
    assert ran == [] and code == 1


def test_install_with_yes_never_asks(world, tmp_path):
    world.system.tailscale = {"BackendState": "Stopped", "Self": {}}

    def no_asking(question):
        raise AssertionError("asked: " + question)

    assert meshd.install(world, say=lambda t: None, ask=no_asking, yes=True, os_release=OS["arch"], shell=lambda c: 0, bin_dir=str(tmp_path / "bin")) == 0


def test_a_failing_step_is_reported_not_hidden(world, tmp_path):
    world.system.tailscale = {"BackendState": "Stopped", "Self": {}}
    said = []
    code = meshd.install(world, say=said.append, yes=True, os_release=OS["arch"], shell=lambda c: 1, bin_dir=str(tmp_path / "bin"))
    assert code == 1 and any("did not finish" in line for line in said)


# ---- the Sunshine account

def answers(*given):
    items = list(given)
    return lambda prompt: items.pop(0)


def test_new_sunshine_creates_the_account_the_person_chooses(world):
    world.link_.new = True
    said = []
    code = meshd.login(world, say=said.append, ask=answers("franc"), ask_secret=answers("longenough1", "longenough1"))
    assert code == 0 and world.link_.created == ("franc", "longenough1") and world.link_.apps_added
    assert any("Created the Sunshine account 'franc'" in line for line in said)


def test_new_sunshine_defaults_the_username_and_rejects_short_or_mismatched_passwords(world):
    world.link_.new = True
    code = meshd.login(world, say=lambda t: None, ask=answers(""), ask_secret=answers("short", "longenough1", "different-1", "longenough1", "longenough1"))
    assert code == 0 and world.link_.created == ("mesh", "longenough1")


def test_new_sunshine_gives_up_after_three_bad_passwords(world):
    world.link_.new = True
    code = meshd.login(world, say=lambda t: None, ask=answers("franc"), ask_secret=answers("a", "b", "c"))
    assert code == 1 and world.link_.created is None


def test_existing_account_is_asked_for_and_checked(world):
    world.link_.saved = None
    said = []
    code = meshd.login(world, say=said.append, ask=answers("alice"), ask_secret=answers("pw-alice-1"))
    assert code == 0 and world.link_.saved == ("alice", "pw-alice-1")
    assert any("already has an account" in line for line in said)


def test_a_wrong_password_is_forgotten_and_asked_again(world):
    world.link_.saved = None
    code = meshd.login(world, say=lambda t: None, ask=answers("alice", "alice"), ask_secret=answers("nope", "pw-alice-1"))
    assert code == 0 and world.link_.saved == ("alice", "pw-alice-1")


def test_three_wrong_attempts_leave_no_saved_login_and_explain_the_reset(world):
    world.link_.saved = None
    said = []
    code = meshd.login(world, say=said.append, ask=answers("a", "a", "a"), ask_secret=answers("x", "y", "z"))
    assert code == 1 and world.link_.saved is None
    assert any("sunshine --creds" in line for line in said)


def test_already_signed_in_does_nothing_unless_forced(world):
    said = []
    assert meshd.login(world, say=said.append, ask=answers(), ask_secret=answers()) == 0
    assert any("Nothing to do" in line for line in said)
    world.link_.saved = None
    assert meshd.login(world, say=lambda t: None, ask=answers("alice"), ask_secret=answers("pw-alice-1"), force=True) == 0


def test_a_saved_login_that_stopped_working_asks_again(world):
    world.link_.saved = ("alice", "old-password")
    assert meshd.login(world, say=lambda t: None, ask=answers("alice"), ask_secret=answers("pw-alice-1")) == 0
    assert world.link_.saved == ("alice", "pw-alice-1")


def test_login_with_flags_asks_nothing(world):
    world.link_.saved = None
    assert meshd.login(world, say=lambda t: None, ask=answers(), ask_secret=answers(), username="alice", password="pw-alice-1") == 0


def test_login_when_sunshine_does_not_answer_starts_it_then_gives_up(world):
    world.link_.down = True
    said = []
    code = meshd.login(world, say=said.append, ask=answers(), ask_secret=answers())
    assert code == 1 and any("does not answer" in line for line in said)
    assert ["systemctl", "--user", "start", mi.SUNSHINE_SERVICE_NAME] in world.system.calls


# ---- services

def test_enable_registers_all_three_and_turns_on_linger(world, monkeypatch):
    monkeypatch.setattr(mi, "ensure_identity", lambda d: None)
    world.tools_.service = False
    said = []
    assert meshd.enable(world, say=said.append) == 0
    assert world.tools_.stopped == 1                      # a hand-started Sunshine is stopped first
    calls = world.system.calls
    assert ["systemctl", "--user", "enable", "--now", mi.SERVICE_NAME] in calls
    assert ["systemctl", "--user", "enable", "--now", mi.SUNSHINE_SERVICE_NAME] in calls
    assert ["systemctl", "--user", "enable", "--now", meshd.SUPERVISOR_UNIT] in calls
    assert ["loginctl", "enable-linger", "mkz"] in calls
    units = os.listdir(world.unit_directory)
    assert {mi.SERVICE_NAME, mi.SUNSHINE_SERVICE_NAME, meshd.SUPERVISOR_UNIT} <= set(units)
    assert "supervise" in open(os.path.join(world.unit_directory, meshd.SUPERVISOR_UNIT)).read()


def test_enable_without_sunshine_says_install_and_fails(world, monkeypatch):
    monkeypatch.setattr(mi, "ensure_identity", lambda d: None)
    world.which = lambda n: None if n == "sunshine" else "/usr/bin/" + n
    said = []
    assert meshd.enable(world, say=said.append) == 1
    assert any("meshd install" in line for line in said)


def test_enable_explains_when_boot_start_needs_an_administrator(world, monkeypatch):
    monkeypatch.setattr(mi, "ensure_identity", lambda d: None)
    world.system.linger_enable_ok = False
    said = []
    meshd.enable(world, say=said.append)
    assert any("sudo loginctl enable-linger mkz" in line for line in said)


def test_disable_stops_the_supervisor_first(world):
    meshd.disable(world, say=lambda t: None)
    order = [c[-1] for c in world.system.calls if c[2:3] == ["disable"]]
    assert order == [meshd.SUPERVISOR_UNIT, mi.SERVICE_NAME, mi.SUNSHINE_SERVICE_NAME]


def test_stop_all_stops_the_supervisor_before_the_others(world):
    meshd.control(world, "stop", "all", say=lambda t: None)
    order = [c[-1] for c in world.system.calls if c[2:3] == ["stop"]]
    assert order[0] == meshd.SUPERVISOR_UNIT
    meshd.control(world, "start", "all", say=lambda t: None)
    order = [c[-1] for c in world.system.calls if c[2:3] == ["start"]]
    assert order[-1] == meshd.SUPERVISOR_UNIT


# ---- the supervisor

def supervisor(world):
    said = []
    return meshd.Supervisor(world, say=said.append, clock=world.clock.wall), said


def test_a_healthy_machine_gets_no_action(world):
    sup, said = supervisor(world)
    for _ in range(3):
        assert sup.tick() == []
    state = json.load(open(os.path.join(world.directory, meshd.STATE_FILE)))
    assert state["online"] is True and state["time"] == world.clock.now


def test_a_stopped_service_is_started(world):
    world.system.unit(mi.SERVICE_NAME, active="inactive")
    sup, said = supervisor(world)
    assert sup.tick() == ["start agent"]
    assert world.system.units[mi.SERVICE_NAME]["ActiveState"] == "active"


def test_sunshine_is_left_alone_before_the_desktop_session_exists(world):
    world.system.unit(mi.SUNSHINE_SERVICE_NAME, active="inactive")
    world.system.graphical = False
    sup, _ = supervisor(world)
    assert sup.tick() == []


def test_a_hung_service_is_restarted_after_two_bad_checks_in_a_row(world):
    world.ports.discard(47600)
    sup, _ = supervisor(world)
    assert sup.tick() == []                 # one bad check could be a blip
    assert sup.tick() == ["restart agent"]


def test_one_bad_check_followed_by_a_good_one_restarts_nothing(world):
    sup, _ = supervisor(world)
    world.ports.discard(47600)
    assert sup.tick() == []
    world.ports.add(47600)
    assert sup.tick() == []
    world.ports.discard(47600)
    assert sup.tick() == []                 # the count started over


def test_a_service_that_cannot_work_is_not_restarted_forever(world):
    world.tools_.answers = False
    sup, said = supervisor(world)
    restarts = 0
    for _ in range(40):
        restarts += sup.tick().count("restart sunshine")
        world.clock.sleep(10)
    assert restarts == meshd.Supervisor.BUDGET[0]
    assert sum("not restarting Sunshine again" in line for line in said) == 1


def test_after_the_network_returns_it_waits_then_checks_and_restarts_at_once(world):
    sup, said = supervisor(world)
    sup.tick()
    world.system.route = ""
    sup.tick()
    assert any("went away" in line for line in said)
    world.system.route = "1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.5 uid 1000"
    world.tools_.answers = False                 # Sunshine did not survive the reconnection
    assert sup.tick() == []                      # settling: nothing is judged yet
    world.clock.sleep(meshd.Supervisor.SETTLE + 1)
    assert sup.tick() == ["restart sunshine"]    # no need for a second bad check this time


def test_switching_networks_counts_as_a_reconnection(world):
    sup, said = supervisor(world)
    sup.tick()
    world.system.route = "1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.7 uid 1000"
    sup.tick()
    assert any("back or changed" in line for line in said)


def test_nothing_is_restarted_while_offline(world):
    world.system.route = ""
    world.ports.discard(47600)
    sup, _ = supervisor(world)
    for _ in range(4):
        assert sup.tick() == []


def test_the_route_signature_ignores_noise():
    env = meshd.Env(runner=lambda a, **k: Done("1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.5 uid 1000 \\    cache expires 300sec"), which=lambda n: n)
    assert meshd.network_route(env) == (True, "via 192.168.1.1 dev wlan0 src 192.168.1.5")


def test_the_supervisor_unit_restarts_always_and_keeps_a_custom_port():
    unit = meshd.supervisor_unit("/usr/bin/python3", "/opt/meshd.py", mi.default_dir(), 47601)
    assert "Restart=always" in unit and "supervise --port 47601" in unit and "WantedBy=default.target" in unit
    assert "--dir" not in unit


def test_a_crash_in_one_check_does_not_end_the_supervisor(world, monkeypatch):
    sup, said = supervisor(world)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bad check")
        return []

    monkeypatch.setattr(sup, "tick", boom)
    sup.run(interval=1, stop=lambda: calls["n"] >= 2)
    assert calls["n"] == 2 and any("check failed: bad check" in line for line in said)


# ---- pair

def test_pair_tells_tailscale_users_to_type_only_the_name(world, monkeypatch):
    monkeypatch.setattr(mi, "pair_info", lambda d, p: {"hosts": ["100.67.163.21"], "port": 47600, "fingerprint": "ab" * 32, "token": "cd" * 16})
    said = []
    assert meshd.pair_text(world, say=said.append) == 0
    text = "\n".join(said)
    assert "mkz" in text and "leave empty" in text and '"fingerprint"' in text


def test_pair_copies_the_info_when_a_clipboard_tool_exists(world, monkeypatch):
    monkeypatch.setattr(mi, "pair_info", lambda d, p: {"port": 47600})
    said = []
    meshd.pair_text(world, say=said.append)
    assert world.system.copied == '{"port": 47600}' and any("already copied with wl-copy" in line for line in said)


def test_pair_without_tailscale_shows_the_info_only(world, monkeypatch):
    monkeypatch.setattr(mi, "pair_info", lambda d, p: {"port": 47600})
    world.system.tailscale = {"BackendState": "Stopped", "Self": {}}
    said = []
    meshd.pair_text(world, say=said.append)
    assert "Tailscale is not running" in "\n".join(said) and "leave empty" not in "\n".join(said)
