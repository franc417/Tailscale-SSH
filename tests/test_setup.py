"""`doctor` / `setup`: the checklist for a computer that streams to the phone, and the repairs."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402

RUNNING = {"BackendState": "Running", "Self": {"UserID": 7, "DNSName": "mkz.tail1234.ts.net.", "TailscaleIPs": ["100.67.163.21", "fd7a:115c:a1e0::1"]},
           "User": {"7": {"LoginName": "franc@example.com"}}}


class Done:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class FakeTools:
    def __init__(self, service=True, running=True, agent=True, answers=True):
        self.service, self.running, self.agent, self.answers = service, running, agent, answers
        self.calls = []

    def sunshine_is_service(self):
        return self.service

    def sunshine_running(self):
        return self.running

    def agent_running(self):
        return self.agent

    def sunshine_https_answers(self):
        return self.answers, "the secure port 47984 answers" if self.answers else "the secure port 47984 accepts connections but never answers"

    def stop_sunshine(self):
        self.calls.append("stop sunshine")
        self.running = False
        return "stopped"


class FakeLink:
    def __init__(self, has_login=True, login_ok=True, first=False):
        self._has, self.login_ok, self.first, self.created = has_login, login_ok, first, 0

    def has_login(self):
        return self._has

    def check_login(self):
        if not self.login_ok:
            raise mi.SunshineError(mi.S_LOGIN_REFUSED, "Sunshine did not accept the saved login")

    def needs_first_login(self):
        return self.first

    def create_login(self, username="mesh"):
        self.created += 1
        self._has, self.first = True, False
        return "secret"


class Computer:
    """A fake computer: what is installed and running, and a runner that answers the few commands setup issues."""

    def __init__(self, tmp_path, tailscale=RUNNING, tailscale_installed=True, sunshine=True, uinput=True, identity=True, lspci="", vainfo=False,
                 encoder="vaapi", tools=None, link=None, agent_unit_active=True):
        self.tmp = tmp_path
        self.tailscale, self.tailscale_installed, self.sunshine, self.uinput = tailscale, tailscale_installed, sunshine, uinput
        self.lspci, self.vainfo = lspci, vainfo
        self.tools = tools or FakeTools()
        self.link = link or FakeLink()
        self.commands = []
        self.agent_unit_active = agent_unit_active
        self.config = tmp_path / "cfg"
        (self.config / "sunshine").mkdir(parents=True, exist_ok=True)
        if encoder:
            (self.config / "sunshine" / "sunshine.conf").write_text(f"encoder = {encoder}\n")
        self.agent_dir = tmp_path / "agent"
        self.agent_dir.mkdir(exist_ok=True)
        self.identity_made = 0
        if identity:
            for name in ("cert.pem", "key.pem", "token"):
                (self.agent_dir / name).write_text("x")
        self.agent_service_installs = 0
        self.bench_runs = 0

    def which(self, name):
        present = {"tailscale": self.tailscale_installed, "sunshine": self.sunshine, "systemctl": True, "vainfo": self.vainfo, "lspci": True}
        return f"/usr/bin/{name}" if present.get(name) else None

    def runner(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd[:3] == ["tailscale", "status", "--json"]:
            return Done(json.dumps(self.tailscale)) if self.tailscale is not None else Done("", 1)
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return Done("active\n" if self.agent_unit_active else "inactive\n", 0 if self.agent_unit_active else 3)
        if cmd[:2] == ["systemctl", "--user"]:
            return Done()
        if cmd == ["lspci"]:
            return Done(self.lspci)
        raise AssertionError(cmd)

    def env(self):
        def make_identity():
            self.identity_made += 1
            for name in ("cert.pem", "key.pem", "token"):
                (self.agent_dir / name).write_text("x")

        def install_agent():
            self.agent_service_installs += 1
            self.tools.agent = True
            return "installed"

        def bench():
            self.bench_runs += 1
            (self.config / "sunshine" / "sunshine.conf").write_text("encoder = vaapi\n")
            return "set encoder = vaapi"

        return mi.SetupEnv(str(self.agent_dir), tools=self.tools, runner=self.runner, which=self.which,
                           environ={"XDG_CONFIG_HOME": str(self.config)}, access=lambda path, mode: self.uinput, exists=os.path.exists,
                           link=self.link, unit_directory=str(self.tmp / "units"), make_identity=make_identity,
                           install_agent_service=install_agent, bench=bench)


def doctor(computer, **kwargs):
    lines = []
    code = mi.run_doctor(computer.env(), out=lines.append, **kwargs)
    return code, "\n".join(lines)


# ------------------------------------------------------------------ small pieces

def test_the_tailscale_summary_reads_login_name_and_address():
    ts = mi.tailscale_summary(lambda cmd, **k: Done(json.dumps(RUNNING)), lambda n: "/usr/bin/tailscale")
    assert ts == {"state": "Running", "login": "franc@example.com", "name": "mkz", "ip": "100.67.163.21"}
    assert mi.tailscale_summary(which=lambda n: None) == {"state": "missing"}
    assert mi.tailscale_summary(lambda cmd, **k: Done("", 1), lambda n: "x") == {"state": "error"}
    assert mi.tailscale_summary(lambda cmd, **k: Done("not json"), lambda n: "x") == {"state": "error"}


def test_the_graphics_vendor_is_read_from_lspci_and_intel_wins_on_a_laptop_with_two_gpus():
    intel = "00:02.0 VGA compatible controller: Intel Corporation Kaby Lake-R GT2 [UHD Graphics 620]"
    nvidia = "01:00.0 3D controller: NVIDIA Corporation GP108M [GeForce MX150]"
    amd = "03:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Navi 23"
    assert mi.gpu_vendor(intel) == "intel" and mi.gpu_vendor(nvidia) == "nvidia" and mi.gpu_vendor(amd) == "amd"
    assert mi.gpu_vendor(intel + "\n" + nvidia) == "intel" and mi.gpu_vendor("00:1f.3 Audio device: Intel") == ""


def test_the_sunshine_unit_runs_it_in_the_desktop_session_and_restarts_it():
    unit = mi.sunshine_unit("/usr/bin/sunshine")
    assert "ExecStart=/usr/bin/sunshine" in unit and "Restart=on-failure" in unit and "WantedBy=graphical-session.target" in unit


# ------------------------------------------------------------------ doctor: reading the situation

def test_a_computer_that_is_ready_gets_a_clean_bill_and_the_phone_instructions(tmp_path):
    code, text = doctor(Computer(tmp_path))
    assert code == 0 and "[FIX ]" not in text and "[todo]" not in text
    assert "signed in as franc@example.com; this computer is mkz (100.67.163.21)" in text
    assert "Everything is in place." in text and "Addresses: mkz" in text


def test_a_missing_tailscale_is_a_problem_with_the_install_commands(tmp_path):
    code, text = doctor(Computer(tmp_path, tailscale_installed=False))
    assert code == 1 and "[FIX ] Tailscale: not installed" in text and "sudo tailscale up" in text and "Addresses:" not in text


def test_a_stopped_tailscale_says_how_to_start_it(tmp_path):
    code, text = doctor(Computer(tmp_path, tailscale={"BackendState": "Stopped"}))
    assert code == 1 and "Tailscale: installed but Stopped" in text


def test_missing_input_permission_is_explained(tmp_path):
    code, text = doctor(Computer(tmp_path, uinput=False))
    assert code == 1 and "this user may not write to it" in text and "lsmod | grep uinput" in text


def test_a_missing_sunshine_points_to_its_install_guide_and_skips_the_checks_that_need_it(tmp_path):
    code, text = doctor(Computer(tmp_path, sunshine=False))
    assert code == 1 and "[FIX ] Sunshine: not installed" in text and "docs.lizardbyte.dev" in text
    assert "Sunshine service" not in text and "Video encoder" not in text


def test_a_sunshine_started_by_hand_is_a_todo_with_a_fix(tmp_path):
    code, text = doctor(Computer(tmp_path, tools=FakeTools(service=False)))
    assert code == 1 and "[todo] Sunshine service: running now, started by hand" in text


def test_the_graphics_driver_hint_names_the_package_for_the_chip(tmp_path):
    intel = "00:02.0 VGA compatible controller: Intel Corporation UHD Graphics 620"
    code, text = doctor(Computer(tmp_path, lspci=intel))
    assert "[FIX ] Hardware video encoding" in text and "intel-media-driver libva-utils" in text
    code, text = doctor(Computer(tmp_path, lspci=intel, vainfo=True))
    assert "[ ok ] Hardware video encoding" in text


def test_an_unknown_existing_sunshine_login_points_to_sunshine_login(tmp_path):
    code, text = doctor(Computer(tmp_path, link=FakeLink(has_login=False)))
    assert code == 1 and "Sunshine already has a login the agent does not know: run python3 mesh_input.py sunshine-login" in text


def test_a_saved_login_that_sunshine_refuses_is_a_problem(tmp_path):
    code, text = doctor(Computer(tmp_path, link=FakeLink(login_ok=False)))
    assert code == 1 and "saved, but Sunshine did not accept the saved login" in text


def test_a_secure_port_that_never_answers_is_flagged(tmp_path):
    code, text = doctor(Computer(tmp_path, tools=FakeTools(answers=False)))
    assert code == 1 and "[FIX ] Sunshine's secure port: the secure port 47984 accepts connections but never answers" in text


def test_doctor_changes_nothing(tmp_path):
    computer = Computer(tmp_path, tools=FakeTools(service=False), encoder=None, identity=False, link=FakeLink(has_login=False, first=True), agent_unit_active=False)
    computer.tools.agent = False
    doctor(computer)
    assert computer.identity_made == 0 and computer.agent_service_installs == 0 and computer.bench_runs == 0 and computer.link.created == 0
    assert not (tmp_path / "units").exists() and not any(c[:3] == ["systemctl", "--user", "enable"] for c in computer.commands)


# ------------------------------------------------------------------ setup: the repairs

def brand_new(tmp_path):
    computer = Computer(tmp_path, tools=FakeTools(service=False, agent=False), encoder=None, identity=False,
                        link=FakeLink(has_login=False, first=True), agent_unit_active=False)
    return computer


def test_setup_fixes_a_brand_new_computer_step_by_step_and_checks_again(tmp_path):
    computer = brand_new(tmp_path)
    original_runner = computer.runner

    def runner(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "enable"] and mi.SUNSHINE_SERVICE_NAME in cmd:
            computer.tools.service = True
            computer.tools.running = True
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return Done("active\n" if computer.agent_service_installs else "inactive\n")
        return original_runner(cmd, **kwargs)
    computer.runner = runner
    code, text = doctor(computer, fix=True, yes=True)
    assert computer.identity_made == 1 and computer.agent_service_installs == 1 and computer.bench_runs == 1 and computer.link.created == 1
    assert (tmp_path / "units" / mi.SUNSHINE_SERVICE_NAME).read_text() == mi.sunshine_unit("/usr/bin/sunshine")
    assert "stop sunshine" in computer.tools.calls                       # a Sunshine started by hand is stopped before the service takes over
    assert "Checking again:" in text and code == 0 and "Everything is in place." in text and "Addresses: mkz" in text


def test_setup_asks_before_each_fix_and_a_no_skips_it(tmp_path):
    computer = Computer(tmp_path, encoder=None)
    answers = iter(["n"])
    asked = []
    lines = []
    code = mi.run_doctor(computer.env(), fix=True, ask=lambda prompt: (asked.append(prompt), next(answers))[1], out=lines.append)
    assert len(asked) == 1 and "Video encoder" in asked[0] and computer.bench_runs == 0 and code == 1
    assert "Run setup" not in "\n".join(lines)


def test_yes_means_no_questions(tmp_path):
    computer = Computer(tmp_path, encoder=None)

    def never(prompt):
        raise AssertionError("must not ask")
    code = mi.run_doctor(computer.env(), fix=True, yes=True, ask=never, out=lambda line: None)
    assert code == 0 and computer.bench_runs == 1


def test_a_fix_that_fails_is_reported_and_the_run_goes_on(tmp_path):
    computer = Computer(tmp_path, encoder=None, identity=False)
    env = computer.env()

    def broken():
        raise RuntimeError("the `openssl` command is needed")
    env.make_identity = broken
    lines = []
    code = mi.run_doctor(env, fix=True, yes=True, out=lines.append)
    text = "\n".join(lines)
    assert "-> could not do it: the `openssl` command is needed" in text and computer.bench_runs == 1 and code == 1


def test_nothing_to_fix_means_no_second_check(tmp_path):
    lines = []
    code = mi.run_doctor(Computer(tmp_path).env(), fix=True, yes=True, out=lines.append)
    assert code == 0 and "Checking again:" not in "\n".join(lines)


def test_problems_are_never_offered_as_fixes(tmp_path):
    computer = Computer(tmp_path, tailscale_installed=False, uinput=False)
    asked = []
    code = mi.run_doctor(computer.env(), fix=True, ask=lambda p: (asked.append(p), "y")[1], out=lambda line: None)
    assert asked == [] and code == 1
