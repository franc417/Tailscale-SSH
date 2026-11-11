#!/usr/bin/env python3
"""meshd - Mesh Host: the computer's side of the Mesh phone app, as one command.

    meshd                 what is running, in one screen (same as `meshd status`)
    meshd install         install what is missing for your Linux distribution, then set everything up
    meshd login           Sunshine's web account: create it (new user) or enter it (you already have one)
    meshd enable          run the input agent, Sunshine and a supervisor in the background: at login, at boot, after a crash
    meshd disable         stop and unregister the background services
    meshd start|stop|restart [agent|sunshine|supervisor|all]
    meshd logs [-f]       what the background services printed
    meshd pair            what to type, scan (QR code) or paste on the phone to add this computer
    meshd supervise       the supervisor itself (the service runs this; you do not need to)

Standard library only. It drives mesh_input.py (the input agent, doctor/setup, Sunshine helpers) which sits next to this file.
Everything is a systemd *user* service: no root once the packages are installed.
"""

import argparse
import base64
import getpass
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mesh_input as mi  # noqa: E402
import meshqr  # noqa: E402

APP = "meshd"
TITLE = "Mesh Host"          # the name shown at the top of every screen; change it here
VERSION = "0.1"
SUPERVISOR_UNIT = "meshd.service"
UNITS = {"agent": mi.SERVICE_NAME, "sunshine": mi.SUNSHINE_SERVICE_NAME, "supervisor": SUPERVISOR_UNIT}
LABELS = {"agent": "Input agent", "sunshine": "Sunshine", "supervisor": "Supervisor"}
STATE_FILE = "supervisor.json"


# ---------------------------------------------------------------- look: the Mesh app's black and white, in a terminal

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


class Style:
    """Bold white, dim grey, one green and one red, like the app. No colour at all when the output is not a terminal."""

    def __init__(self, color: bool) -> None:
        self.color = color

    def _w(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def bold(self, text: str) -> str:
        return self._w("1", text)

    def dim(self, text: str) -> str:
        return self._w("2", text)

    def ok(self, text: str) -> str:
        return self._w("38;5;114", text)

    def bad(self, text: str) -> str:
        return self._w("38;5;210", text)


def use_color(stream=None, environ=os.environ) -> bool:
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)()) and "NO_COLOR" not in environ and environ.get("TERM") != "dumb"


def box(lines: List[str], style: Style, width: int = 0) -> str:
    """Lines inside a rounded hairline box (the app's cards)."""
    inner = max([visible_len(line) for line in lines] + [width - 4, 0])
    out = [style.dim("╭" + "─" * (inner + 2) + "╮")]
    for line in lines:
        out.append(style.dim("│") + " " + line + " " * (inner - visible_len(line)) + " " + style.dim("│"))
    out.append(style.dim("╰" + "─" * (inner + 2) + "╯"))
    return "\n".join(out)


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


@dataclass
class Row:
    name: str
    level: str      # ok | bad | wait   (wait = not running yet, or running but not kept alive)
    word: str       # running, stopped, stuck, by hand ...
    detail: str = ""


def render_status(rows: List[Row], header: List[str], style: Style) -> str:
    marks = {"ok": style.ok("●"), "bad": style.bad("●"), "wait": style.dim("○")}
    lines = [box(header, style, width=56), ""]
    for row in rows:
        word = {"ok": style.ok, "bad": style.bad, "wait": style.dim}[row.level](f"{row.word:<11}")
        lines.append(f" {marks[row.level]} {style.bold(f'{row.name:<16}')}{word} {style.dim(row.detail)}")
    bad = sum(1 for r in rows if r.level == "bad")
    wait = sum(1 for r in rows if r.level == "wait")
    lines.append("")
    if not bad and not wait:
        lines.append(" " + style.ok("Everything is running."))
    else:
        parts = ([f"{bad} problem{'s' if bad != 1 else ''}"] if bad else []) + ([f"{wait} not running yet"] if wait else [])
        lines.append(" " + style.bold(", ".join(parts)) + style.dim("   try: meshd restart all   ·   meshd logs   ·   meshd enable"))
    return "\n".join(lines)


# ---------------------------------------------------------------- the machine, in one place so tests can replace it

def _tcp_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_uptime() -> float:
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


class Env:
    def __init__(self, directory: Optional[str] = None, port: int = mi.DEFAULT_PORT, runner=subprocess.run, which=shutil.which,
                 environ=os.environ, tools=None, link=None, connect=None, uptime=None, unit_directory: Optional[str] = None,
                 wall=time.time, sleep=time.sleep, user: Optional[str] = None, access=os.access, mesh_probe=None) -> None:
        self.directory = directory or mi.default_dir()
        self.port, self.runner, self.which, self.environ = port, runner, which, environ
        self.tools = tools or mi.ProcessTools(runner=runner, which=which, environ=environ)
        self.link = link or mi.SunshineLink(self.directory)
        self.connect = connect or _tcp_connect
        self.uptime = uptime or _read_uptime
        self.unit_directory = unit_directory or mi.unit_dir()
        self.wall, self.sleep, self.access = wall, sleep, access
        self.mesh_state = mesh_probe or (lambda: detect_mesh_state(environ))
        self.user = user or getpass.getuser()

    def run(self, args: List[str], timeout: float = 20.0) -> Tuple[int, str]:
        try:
            done = self.runner(args, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)
        return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()

    def sctl(self, *args: str, timeout: float = 20.0) -> Tuple[int, str]:
        return self.run(["systemctl", "--user", *args], timeout=timeout)


@dataclass
class UnitInfo:
    installed: bool = False
    active: str = ""          # active, inactive, failed, activating ...
    restarts: int = 0
    uptime_s: Optional[float] = None
    enabled: str = ""


def unit_status(env: Env, name: str) -> UnitInfo:
    code, text = env.sctl("show", name, "-p", "LoadState", "-p", "ActiveState", "-p", "NRestarts",
                          "-p", "ActiveEnterTimestampMonotonic", "-p", "UnitFileState")
    data = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    info = UnitInfo(installed=data.get("LoadState") == "loaded", active=data.get("ActiveState", ""), enabled=data.get("UnitFileState", ""))
    try:
        info.restarts = int(data.get("NRestarts", "0") or 0)
    except ValueError:
        pass
    try:
        mono = int(data.get("ActiveEnterTimestampMonotonic", "0") or 0)
        if info.active == "active" and mono > 0:
            info.uptime_s = env.uptime() - mono / 1e6
    except (ValueError, OSError):
        pass
    return info


def graphical_session_active(env: Env) -> bool:
    return env.sctl("is-active", "graphical-session.target")[1].strip() == "active"


def network_route(env: Env) -> Tuple[bool, str]:
    """(is there a way out, what it looks like). The route line changes when you switch Wi-Fi, so it doubles as a fingerprint."""
    code, text = env.run(["ip", "-o", "route", "get", "1.1.1.1"], timeout=5.0)
    if code != 0 or "dev " not in text:
        return False, ""
    return True, " ".join(re.findall(r"(?:via|dev|src) \S+", text.split("\n")[0]))


# ---------------------------------------------------------------- status

def _by_hand(env: Env, pattern: str) -> bool:
    return env.run(["pgrep", "-f", pattern], timeout=5.0)[0] == 0


def agent_row(env: Env) -> Row:
    info = unit_status(env, UNITS["agent"])
    answers = env.connect("127.0.0.1", env.port)
    if not info.installed:
        if answers or _by_hand(env, "mesh_input.py run"):
            return Row(LABELS["agent"], "wait", "by hand", "nothing restarts it: run  meshd enable")
        return Row(LABELS["agent"], "bad", "not set up", "run  meshd enable")
    if info.active != "active":
        return Row(LABELS["agent"], "bad", "failed" if info.active == "failed" else "stopped", "run  meshd start agent   (meshd logs)")
    if not answers:
        return Row(LABELS["agent"], "bad", "stuck", f"running, but port {env.port} does not answer: meshd restart agent")
    extra = f" · restarted {info.restarts}x" if info.restarts else ""
    return Row(LABELS["agent"], "ok", "running", f"up {fmt_duration(info.uptime_s)} · port {env.port} answers{extra}")


def sunshine_row(env: Env) -> Row:
    info = unit_status(env, UNITS["sunshine"])
    if not info.installed:
        if env.tools.sunshine_running():
            return Row(LABELS["sunshine"], "wait", "by hand", "nothing restarts it: run  meshd enable")
        if env.which("sunshine") is None:
            return Row(LABELS["sunshine"], "bad", "missing", "not installed: run  meshd install")
        return Row(LABELS["sunshine"], "bad", "not set up", "run  meshd enable")
    if info.active != "active":
        if not graphical_session_active(env):
            return Row(LABELS["sunshine"], "wait", "waiting", "starts when you log in to your desktop (auto-login makes that part of boot)")
        return Row(LABELS["sunshine"], "bad", "failed" if info.active == "failed" else "stopped", "run  meshd start sunshine   (meshd logs)")
    answers, why = env.tools.sunshine_https_answers()
    if not answers:
        return Row(LABELS["sunshine"], "bad", "stuck", why + ": meshd restart sunshine")
    extra = f" · restarted {info.restarts}x" if info.restarts else ""
    return Row(LABELS["sunshine"], "ok", "running", f"up {fmt_duration(info.uptime_s)} · {why}{extra}")


def login_row(env: Env, sunshine: Row) -> Row:
    name = "Sunshine login"
    if sunshine.level != "ok":
        return Row(name, "wait", "waiting", "needs Sunshine running")
    if not env.link.has_login():
        return Row(name, "wait", "not set", "run  meshd login")
    try:
        env.link.check_login()
    except mi.SunshineError as exc:
        return Row(name, "bad", "refused", "Sunshine does not accept the saved login: run  meshd login")
    return Row(name, "ok", "ready", "phones pair their desktop view with no PIN")


def tailscale_row(env: Env) -> Tuple[Row, dict]:
    ts = mi.tailscale_summary(env.runner, env.which)
    if ts["state"] == "missing":
        return Row("Tailscale", "bad", "missing", "not installed: run  meshd install"), ts
    if ts["state"] != "Running":
        return Row("Tailscale", "bad", "off", f"{ts['state'] or 'not running'}: run  sudo tailscale up"), ts
    return Row("Tailscale", "ok", "connected", f"{ts['name'] or ts['ip']} · {ts['login'] or 'your account'}"), ts


def supervisor_row(env: Env) -> Row:
    info = unit_status(env, UNITS["supervisor"])
    if not info.installed:
        return Row(LABELS["supervisor"], "wait", "not set up", "keeps the two above alive: run  meshd enable")
    if info.active != "active":
        return Row(LABELS["supervisor"], "bad", "stopped", "run  meshd start supervisor")
    state = read_state(env)
    age = env.wall() - state["time"] if state and "time" in state else None
    if age is None or age > 90:
        return Row(LABELS["supervisor"], "bad", "silent", "running but has not checked in: meshd restart supervisor")
    return Row(LABELS["supervisor"], "ok", "watching", f"last check {fmt_duration(age)} ago · fixes a crash, a hang, a reconnect")


def boot_row(env: Env, agent_enabled: str) -> Row:
    linger = "Linger=yes" in env.run(["loginctl", "show-user", env.user, "-p", "Linger"], timeout=5.0)[1]
    if linger and agent_enabled == "enabled":
        return Row("Starts at boot", "ok", "yes", "agent + supervisor at boot · Sunshine with your desktop session")
    return Row("Starts at boot", "wait", "at login", "run  meshd enable  to start at boot, before anyone logs in")


def network_row(env: Env) -> Row:
    online, sig = network_route(env)
    if not online:
        return Row("Network", "bad", "offline", "the supervisor checks everything again when it is back")
    dev = re.search(r"dev (\S+)", sig)
    internet = env.connect("1.1.1.1", 443, 2.0)
    return Row("Network", "ok", "online", f"via {dev.group(1) if dev else '?'}" + ("" if internet else " · no internet (local network only)"))


def gather_status(env: Env) -> Tuple[str, List[Row]]:
    """(the 'this computer' line, the rows)."""
    agent = agent_row(env)
    sunshine = sunshine_row(env)
    ts_row, ts = tailscale_row(env)
    rows = [agent, sunshine, login_row(env, sunshine), supervisor_row(env), ts_row, network_row(env),
            boot_row(env, unit_status(env, UNITS["agent"]).enabled)]
    where = ts.get("name") or ts.get("ip") or socket.gethostname()
    return f"This computer: {where}" + (f" · {ts['ip']}" if ts.get("ip") and ts.get("name") else ""), rows


def status_text(env: Env, style: Style) -> str:
    where, rows = gather_status(env)
    right = f"{APP} {VERSION}"
    header = [style.bold(TITLE) + " " * max(2, 52 - len(TITLE) - len(right)) + style.dim(right), style.dim(where)]
    return render_status(rows, header, style)


def status_json(env: Env) -> str:
    where, rows = gather_status(env)
    return json.dumps({"rows": [r.__dict__ for r in rows], "ok": all(r.level == "ok" for r in rows)}, indent=2)


# ---------------------------------------------------------------- the supervisor

def read_state(env: Env) -> Optional[dict]:
    try:
        with open(os.path.join(env.directory, STATE_FILE)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


class Supervisor:
    """Keeps the agent and Sunshine up. systemd already restarts a process that exits; this also catches one that is alive but
    stuck (its port does not answer), starts one that stopped, and re-checks both a few seconds after the network comes back or
    changes. It never restarts anything more than BUDGET times in a window, so a process that cannot work does not loop."""

    BUDGET = (4, 600.0)      # at most 4 restarts per 10 minutes, per service
    SETTLE = 8.0             # seconds to let the network settle before judging anything
    NEEDS_CONSECUTIVE = 2    # unhealthy checks in a row before a restart (1 right after a reconnect)

    def __init__(self, env: Env, say: Callable[[str], None] = lambda t: print(t, flush=True), clock=time.monotonic) -> None:
        self.env, self.say, self.clock = env, say, clock
        self.last_online: Optional[bool] = None
        self.last_sig = ""
        self.settle_until = 0.0
        self.after_reconnect = False
        self.unhealthy = {"agent": 0, "sunshine": 0}
        self.history: Dict[str, Deque[float]] = {"agent": deque(), "sunshine": deque()}
        self.recent: Deque[str] = deque(maxlen=6)

    def _budget_ok(self, name: str) -> bool:
        now = self.clock()
        window = self.history[name]
        while window and now - window[0] > self.BUDGET[1]:
            window.popleft()
        return len(window) < self.BUDGET[0]

    def _act(self, name: str, verb: str, reason: str, actions: List[str]) -> None:
        if not self._budget_ok(name):
            note = f"not restarting {LABELS[name]} again for a while: it was restarted {self.BUDGET[0]} times in {int(self.BUDGET[1] // 60)} minutes (meshd logs)"
            if note not in self.recent:
                self.say(note)
                self.recent.append(note)
            return
        self.history[name].append(self.clock())
        code, text = self.env.sctl(verb, UNITS[name])
        line = f"{verb} {LABELS[name]}: {reason}" + ("" if code == 0 else f" (failed: {text})")
        self.say(line)
        self.recent.append(line)
        actions.append(f"{verb} {name}")

    def healthy(self, name: str) -> bool:
        if name == "agent":
            return self.env.connect("127.0.0.1", self.env.port)
        return self.env.tools.sunshine_https_answers()[0]

    def tick(self) -> List[str]:
        actions: List[str] = []
        online, sig = network_route(self.env)
        changed = self.last_online is not None and (online != self.last_online or (online and sig != self.last_sig))
        if changed and online:
            self.say("the network is back or changed: checking everything in a few seconds")
            self.settle_until = self.clock() + self.SETTLE
            self.after_reconnect = True
        elif changed:
            self.say("the network went away")
        self.last_online, self.last_sig = online, sig
        if self.clock() >= self.settle_until:
            self._check(online, actions)
        self._write_state(online)
        return actions

    def _check(self, online: bool, actions: List[str]) -> None:
        for name in ("agent", "sunshine"):
            info = unit_status(self.env, UNITS[name])
            if not info.installed:
                continue
            if name == "sunshine" and not graphical_session_active(self.env):
                continue      # nothing to capture before the desktop session exists
            if info.active != "active":
                self.unhealthy[name] = 0
                self._act(name, "start", "it was not running", actions)
                continue
            if self.healthy(name):
                self.unhealthy[name] = 0
                continue
            if not online:
                continue      # no network: a restart would not help and would only drop a stream that may come back
            self.unhealthy[name] += 1
            needed = 1 if self.after_reconnect else self.NEEDS_CONSECUTIVE
            if self.unhealthy[name] >= needed:
                self.unhealthy[name] = 0
                self._act(name, "restart", "it is running but does not answer", actions)
        self.after_reconnect = False

    def _write_state(self, online: bool) -> None:
        try:
            os.makedirs(self.env.directory, mode=0o700, exist_ok=True)
            path = os.path.join(self.env.directory, STATE_FILE)
            with open(path + ".tmp", "w") as f:
                json.dump({"time": self.env.wall(), "online": online, "recent": list(self.recent)}, f)
            os.replace(path + ".tmp", path)
        except OSError:
            pass

    def run(self, interval: float = 10.0, stop: Callable[[], bool] = lambda: False) -> None:
        self.say(f"{TITLE} supervisor started: checking the agent and Sunshine every {int(interval)} s")
        while not stop():
            try:
                self.tick()
            except Exception as exc:    # never let one bad check end the supervisor
                self.say(f"check failed: {exc}")
            self.env.sleep(interval)


def supervisor_unit(python: str, script: str, directory: Optional[str], port: int) -> str:
    command = f"{python} {script} supervise"
    if directory and directory != mi.default_dir():
        command += f" --dir {directory}"
    if port != mi.DEFAULT_PORT:
        command += f" --port {port}"
    return ("[Unit]\nDescription=Mesh Host supervisor (keeps the input agent and Sunshine running)\nAfter=network.target\n\n"
            f"[Service]\nExecStart={command}\nRestart=always\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n")


# ---------------------------------------------------------------- enable / disable / start / stop

def _step(say: Callable[[str], None], ok: bool, text: str) -> bool:
    say(("  ✓ " if ok else "  ✗ ") + text)
    return ok


def enable(env: Env, say: Callable[[str], None] = print) -> int:
    """Registers the three background services and starts them. Returns 0 when everything that can be done was done."""
    failed = 0
    # 1. the agent
    try:
        mi.install_service(env.port, env.directory, start=True, target_dir=env.unit_directory, runner=env.runner)
        _step(say, True, "input agent: runs in the background, starts at login and boot")
    except (RuntimeError, OSError) as exc:
        failed += 1
        _step(say, False, f"input agent: {exc}")
    # 2. Sunshine
    sunshine = env.which("sunshine")
    if sunshine is None:
        failed += 1
        _step(say, False, "Sunshine is not installed: run  meshd install")
    else:
        try:
            if not env.tools.sunshine_is_service():
                env.tools.stop_sunshine()
                os.makedirs(env.unit_directory, exist_ok=True)
                with open(os.path.join(env.unit_directory, mi.SUNSHINE_SERVICE_NAME), "w") as f:
                    f.write(mi.sunshine_unit(sunshine))
            for args in (["daemon-reload"], ["enable", "--now", mi.SUNSHINE_SERVICE_NAME]):
                code, text = env.sctl(*args)
                if code != 0:
                    raise RuntimeError(f"`systemctl --user {' '.join(args)}` failed: {text}")
            _step(say, True, "Sunshine: runs in the background and starts with your desktop session")
        except (RuntimeError, OSError) as exc:
            failed += 1
            _step(say, False, f"Sunshine: {exc}")
    # 3. the supervisor
    try:
        os.makedirs(env.unit_directory, exist_ok=True)
        with open(os.path.join(env.unit_directory, SUPERVISOR_UNIT), "w") as f:
            f.write(supervisor_unit(sys.executable, os.path.join(HERE, "meshd.py"), env.directory, env.port))
        for args in (["daemon-reload"], ["enable", "--now", SUPERVISOR_UNIT]):
            code, text = env.sctl(*args)
            if code != 0:
                raise RuntimeError(f"`systemctl --user {' '.join(args)}` failed: {text}")
        _step(say, True, "supervisor: restarts either one if it stops, hangs, or the network comes back")
    except (RuntimeError, OSError) as exc:
        failed += 1
        _step(say, False, f"supervisor: {exc}")
    # 4. start at boot, before anyone logs in
    code, text = env.run(["loginctl", "enable-linger", env.user])
    if code == 0:
        _step(say, True, "start at boot: on (the agent and supervisor start with the computer; Sunshine with your desktop session)")
    else:
        _step(say, False, f"start at boot needs an administrator once: sudo loginctl enable-linger {env.user}")
    return 1 if failed else 0


def disable(env: Env, say: Callable[[str], None] = print) -> int:
    for key in ("supervisor", "agent", "sunshine"):     # the supervisor first, or it would start the others again
        code, text = env.sctl("disable", "--now", UNITS[key])
        _step(say, code == 0, f"{LABELS[key]}: stopped and not started automatically any more" if code == 0 else f"{LABELS[key]}: {text}")
    return 0


def resolve_targets(target: str, stopping: bool) -> List[str]:
    if target in ("all", ""):
        return ["supervisor", "agent", "sunshine"] if stopping else ["agent", "sunshine", "supervisor"]
    return [target]


def control(env: Env, verb: str, target: str, say: Callable[[str], None] = print) -> int:
    code_all = 0
    for key in resolve_targets(target, stopping=verb == "stop"):
        code, text = env.sctl(verb, UNITS[key])
        _step(say, code == 0, f"{LABELS[key]}: {verb} " + ("done" if code == 0 else f"failed ({text})"))
        code_all = code_all or code
    return code_all


# ---------------------------------------------------------------- the Sunshine account

def wait_for_sunshine(env: Env, seconds: float = 25.0) -> Optional[str]:
    """'new' when Sunshine has no web account yet, 'existing' when it has one, None when it does not answer (it is started if it can be)."""
    started = False
    deadline = env.wall() + seconds
    while True:
        try:
            return "new" if env.link.needs_first_login() else "existing"
        except mi.SunshineError:
            if not started:
                started = True
                env.sctl("start", UNITS["sunshine"])
            if env.wall() >= deadline:
                return None
            env.sleep(1.0)


def login(env: Env, say: Callable[[str], None] = print, ask: Callable[[str], str] = input,
          ask_secret: Callable[[str], str] = getpass.getpass, username: Optional[str] = None,
          password: Optional[str] = None, force: bool = False) -> int:
    """Gives the agent a Sunshine web account to use. A new Sunshine gets the account you choose; one that already has an account
    asks for it (and checks it) instead. Both cases are handled: the tool asks Sunshine which one this is."""
    link = env.link
    state = wait_for_sunshine(env)
    if state is None:
        say("Sunshine does not answer, so its account cannot be set up yet. Start it (meshd enable, or meshd start sunshine) and run  meshd login  again.")
        return 1
    try:
        if state == "new":
            say("Sunshine has no account yet: choose one now. It is also the login for Sunshine's own web page (https://localhost:47990).")
            username = username or (ask("  Choose a username [mesh]: ").strip() or "mesh")
            for attempt in range(3):
                if password is None:
                    first = ask_secret("  Choose a password (at least 8 characters, not shown): ")
                    if len(first) < 8:
                        say("  That is too short.")
                        continue
                    if ask_secret("  Type it again: ") != first:
                        say("  The two did not match.")
                        continue
                    password = first
                break
            else:
                say("error: no usable password was given")
                return 1
            if len(password) < 8:
                say("error: the password needs at least 8 characters")
                return 1
            link.create_login(username, password)
            say(f"Created the Sunshine account '{username}' and saved it for the agent.")
        else:
            if not force and link.has_login():
                try:
                    link.check_login()
                    say("Sunshine already has an account and the agent is signed in to it. Nothing to do.  (meshd login --force to change it)")
                    state = "done"
                except mi.SunshineError:
                    say("The saved login no longer works. Sign in again:")
            if state != "done":
                say("Sunshine already has an account. Enter it so the agent can approve phone pairings for you.")
                for attempt in range(3):
                    user = username or ask("  Sunshine username: ").strip()
                    pw = password if password is not None else ask_secret("  Sunshine password (not shown): ")
                    if not user or not pw:
                        say("  Both a username and a password are needed.")
                        continue
                    link.save_login(user, pw)
                    try:
                        link.check_login()
                        say("Sunshine accepted it. Saved privately, for the agent only.")
                        break
                    except mi.SunshineError:
                        link.forget_login()
                        say("  Sunshine did not accept that username and password.")
                        username, password = None, None
                else:
                    say("Could not sign in. Forgot it? Sunshine can set a new one (per its documentation): stop Sunshine, run")
                    say("  sunshine --creds NEWNAME NEWPASSWORD")
                    say("then start it again and run  meshd login.")
                    return 1
        try:
            if link.ensure_desktop_app():
                say("Added a Desktop application to Sunshine.")
        except mi.SunshineError as exc:
            say(f"note: could not check Sunshine's applications ({exc.text})")
    except mi.SunshineError as exc:
        say(f"error: {exc.text}")
        return 1
    say("Done: phones now pair their desktop view with this computer with no PIN to type.")
    return 0


# ---------------------------------------------------------------- install

ARCH_SUNSHINE = """set -e
if ! grep -q '^\\[lizardbyte\\]' /etc/pacman.conf; then
  printf '\\n[lizardbyte]\\nSigLevel = Optional\\nServer = https://github.com/LizardByte/pacman-repo/releases/latest/download\\n' | sudo tee -a /etc/pacman.conf >/dev/null
fi
sudo pacman -Sy --noconfirm
sudo pacman -S --needed --noconfirm lizardbyte/sunshine"""

UINPUT_SETUP = """set -e
sudo tee /etc/udev/rules.d/85-sunshine.rules >/dev/null <<'RULE'
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
RULE
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG input "$(whoami)\""""

TAILSCALE_INSTALL = "curl -fsSL https://tailscale.com/install.sh | sh"
SUNSHINE_DOCS = "https://docs.lizardbyte.dev/projects/sunshine/latest/about/installation.html"
DRIVER_CMD = {"intel": "sudo pacman -S --needed --noconfirm intel-media-driver libva-utils",
              "amd": "sudo pacman -S --needed --noconfirm libva-mesa-driver libva-utils"}


def detect_distro(os_release: str) -> Tuple[str, str]:
    """(family, nice name): arch, debian, fedora, suse or unknown, from the text of /etc/os-release (ID_LIKE covers the derivatives)."""
    fields = {}
    for line in os_release.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip().strip('"')
    ids = [fields.get("ID", "")] + fields.get("ID_LIKE", "").split()
    families = {"arch": ("arch", "manjaro", "endeavouros", "cachyos", "garuda"), "debian": ("debian", "ubuntu", "linuxmint", "pop", "raspbian"),
                "fedora": ("fedora", "rhel", "centos", "nobara"), "suse": ("opensuse", "suse", "sles", "opensuse-leap", "opensuse-tumbleweed")}
    for family, names in families.items():
        if any(i in names for i in ids):
            return family, fields.get("PRETTY_NAME", family)
    return "unknown", fields.get("PRETTY_NAME", "this Linux")


@dataclass
class InstallStep:
    title: str
    command: str = ""        # a shell command to run (sudo asks for the password itself); empty = a manual step
    note: str = ""
    optional: bool = False   # declining or failing it does not make `meshd install` fail


def detect_mesh_state(environ=os.environ, home: Optional[str] = None) -> str:
    """The shell-access tool (`mesh`, tailscale_ssh.py) on this computer: 'missing' (not installed), 'unconfigured' (installed, its setup never
    finished: no SSH server or key prepared) or 'ok'."""
    home = home or os.path.expanduser("~")
    if not os.path.exists(os.path.join(home, ".local", "share", "tailscale-ssh", "tailscale_ssh.py")):
        return "missing"
    conf_dir = environ.get("TSSH_CONFIG_DIR") or os.path.join(home, ".config", "tailscale-ssh")
    try:
        with open(os.path.join(conf_dir, "config.json")) as f:
            return "ok" if json.load(f).get("configured") else "unconfigured"
    except (OSError, ValueError):
        return "unconfigured"


def mesh_steps(state: str, assume_yes: bool = False, directory: str = HERE) -> List[InstallStep]:
    """Installing `mesh` from THIS folder (nothing is downloaded) and running its setup. Tailscale is already signed in by then, so mesh's own
    sign-in step sees 'already connected': one sign-in for both tools."""
    steps: List[InstallStep] = []
    if state == "missing":
        steps.append(InstallStep("Install mesh: a terminal on this computer from your phone (Termux), and a live list of your devices",
                                 f"bash {shlex.quote(os.path.join(directory, 'install.sh'))}",
                                 "installed from this folder; optional, the Mesh app does not need it", optional=True))
    if state in ("missing", "unconfigured"):
        steps.append(InstallStep("Prepare this computer for mesh (SSH server and key)",
                                 f"TSSH_BRAND=mesh python3 {shlex.quote(os.path.join(directory, 'tailscale_ssh.py'))} setup" + (" -y" if assume_yes else ""),
                                 "uses the Tailscale account you just signed in with: no second sign-in", optional=True))
    return steps


def install_plan(family: str, has_tailscale: bool, tailscale_running: bool, has_sunshine: bool, uinput_ok: bool, gpu: str,
                 mesh_state: str = "ok", assume_yes: bool = False) -> List[InstallStep]:
    steps: List[InstallStep] = []
    if not has_tailscale:
        steps.append(InstallStep("Install Tailscale (the private network between your phone and this computer)", TAILSCALE_INSTALL,
                                 "Tailscale's own installer, for every distribution"))
    if not tailscale_running:
        steps.append(InstallStep("Sign this computer in to Tailscale (use the same account as on the phone)", f"sudo tailscale up --operator={getpass.getuser()}",
                                 "it prints a link to open in a browser"))
    if not has_sunshine:
        if family == "arch":
            steps.append(InstallStep("Install Sunshine (the screen and sound streamer)", ARCH_SUNSHINE, "from LizardByte's own pacman repository"))
        elif family == "fedora":
            steps.append(InstallStep("Install Sunshine (the screen and sound streamer)", "sudo dnf copr enable -y lizardbyte/stable && sudo dnf install -y Sunshine",
                                     "from Sunshine's documented COPR; not tested by Mesh"))
        else:
            steps.append(InstallStep("Install Sunshine yourself (the screen and sound streamer)", "",
                                     f"download the package for your system from its releases page and install it: {SUNSHINE_DOCS}"))
    if not uinput_ok:
        steps.append(InstallStep("Allow the phone's keyboard and mouse (access to /dev/uinput)", UINPUT_SETUP,
                                 "adds you to the 'input' group: log out and back in once afterwards"))
    if gpu in DRIVER_CMD and family == "arch":
        steps.append(InstallStep("Install the video driver that lets your graphics chip encode the stream (much faster than the CPU)", DRIVER_CMD[gpu]))
    elif gpu and family != "arch":
        steps.append(InstallStep("Install your graphics chip's VA-API video driver (package names differ per distribution)", "",
                                 "then run  meshd setup  to check the encoder"))
    steps.extend(mesh_steps(mesh_state, assume_yes))
    return steps


def install_launcher(bin_dir: str, python: str = sys.executable, script: Optional[str] = None, environ=os.environ) -> Tuple[str, bool]:
    """Writes ~/.local/bin/meshd so the command works from anywhere. Returns (path, bin_dir is on PATH)."""
    script = script or os.path.join(HERE, "meshd.py")
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, APP)
    with open(path, "w") as f:
        f.write(f'#!/bin/sh\nexec "{python}" "{script}" "$@"\n')
    os.chmod(path, 0o755)
    return path, bin_dir in environ.get("PATH", "").split(os.pathsep)


def install(env: Env, say: Callable[[str], None] = print, ask: Callable[[str], str] = input, yes: bool = False,
            os_release: Optional[str] = None, shell: Optional[Callable[[str], int]] = None, bin_dir: Optional[str] = None) -> int:
    """`meshd install`: find the distribution, run what is missing (asking first), put `meshd` on the PATH."""
    if os_release is None:
        try:
            with open("/etc/os-release") as f:
                os_release = f.read()
        except OSError:
            os_release = ""
    family, pretty = detect_distro(os_release)
    shell = shell or (lambda cmd: subprocess.run(["bash", "-c", cmd]).returncode)
    say(f"Detected: {pretty}" + ("" if family != "unknown" else "  (not one Mesh knows: the steps below are general)"))
    ts = mi.tailscale_summary(env.runner, env.which)
    uinput_ok = env.access("/dev/uinput", os.W_OK)
    code, lspci = env.run(["lspci"], timeout=8.0)
    plan = install_plan(family, ts["state"] != "missing", ts["state"] == "Running", env.which("sunshine") is not None,
                        uinput_ok, mi.gpu_vendor(lspci if code == 0 else ""), env.mesh_state(), yes)
    problems = 0
    if not plan:
        say("Nothing to install: everything needed is already here.")
    for number, step in enumerate(plan, 1):
        say(f"\n{number}. {step.title}")
        if step.note:
            say(f"   {step.note}")
        if not step.command:
            say("   (a manual step: do it, then run  meshd install  again)")
            problems += 1
            continue
        for line in step.command.splitlines():
            say(f"   $ {line}")
        if not yes and ask("   Run it? [Y/n] ").strip().lower() in ("n", "no"):
            say("   skipped" + (" (optional)" if step.optional else ""))
            problems += 0 if step.optional else 1
            continue
        if shell(step.command) != 0:
            say("   that did not finish; fix it and run  meshd install  again" + ("  (optional: everything else is unaffected)" if step.optional else ""))
            problems += 0 if step.optional else 1
    path, on_path = install_launcher(bin_dir or os.path.join(os.path.expanduser("~"), ".local", "bin"))
    say(f"\nThe command is now {path}" + ("" if on_path else "   (add its folder to your PATH, or run it by that path)"))
    return 1 if problems else 0


# ---------------------------------------------------------------- pair

def copy_to_clipboard(env: Env, text: str) -> Optional[str]:
    for tool, args in (("wl-copy", []), ("xclip", ["-selection", "clipboard"]), ("xsel", ["-b", "-i"])):
        if env.which(tool):
            try:
                if env.runner([tool, *args], input=text, text=True, timeout=5).returncode == 0:
                    return tool
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def pair_uri(info: dict, max_addresses: int = 3) -> str:
    """The pairing info as one short link, for the QR code: mesh://pair?v=1&p=PORT&f=FINGERPRINT&t=TOKEN&h=NAME&a=ADDR,ADDR.
    The fingerprint and the token travel as base64url (43 characters each instead of 64), and only IPv4 addresses are listed,
    so the code stays small (a small code is the difference between scanning at once and not at all with a weak camera).
    The app's PairInfo.parse reads this and the long JSON alike."""
    def b64(hexstring: str) -> str:
        return base64.urlsafe_b64encode(bytes.fromhex(hexstring)).rstrip(b"=").decode()

    addresses = [a for a in info.get("addresses", []) if ":" not in a][:max_addresses]
    parts = [f"v={info['version']}", f"p={info['port']}", f"f={b64(info['fingerprint'])}", f"t={b64(info['token'])}"]
    if info.get("host"):
        parts.append("h=" + urllib.parse.quote(str(info["host"]), safe=""))
    if addresses:
        parts.append("a=" + ",".join(urllib.parse.quote(a, safe=".") for a in addresses))
    return "mesh://pair?" + "&".join(parts)


def pair_text(env: Env, say: Callable[[str], None] = print, qr: bool = False, png: Optional[str] = None, columns: int = 80) -> int:
    ts = mi.tailscale_summary(env.runner, env.which)
    try:
        info = mi.pair_info(env.directory, env.port)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        say(f"error: {exc}")
        return 1
    say("Add this computer in the Mesh app:  Add desktop")
    if ts["state"] == "Running" and (ts["name"] or ts["ip"]):
        say(f"\n  On the same Tailscale account as the phone (usual case):")
        say(f"    Addresses:     {ts['name'] or ts['ip']}")
        say( "    Pairing info:  leave empty")
    else:
        say("\n  Tailscale is not running here, so use the QR code or the pairing info below (the phone must reach this computer's address, for example on the same Wi-Fi).")
    if qr or png:
        matrix = meshqr.encode(pair_uri(info).encode())
        if qr:
            say("\n  No Tailscale, or another account: in the app tap Add desktop, then Scan QR code, and point the camera at this.")
            say("  (Make the terminal text bigger if it will not scan. Anyone who can see this code can use this computer's keyboard and mouse: do not share a screenshot.)\n")
            say(meshqr.render_terminal(matrix, scale=2 if columns >= 2 * (len(matrix) + 4) else 1))
        if png:
            try:
                meshqr.write_png(png, matrix)
                say(f"\n  The code is also saved as {png} (open it on a screen; delete it afterwards, it holds the pairing secret).")
            except OSError as exc:
                say(f"\n  could not write {png}: {exc}")
    line = json.dumps(info)
    tool = copy_to_clipboard(env, line)
    say("\n  Or paste this into the Pairing info box:")
    say(f"    {line}")
    if tool:
        say(f"    (already copied with {tool})")
    if ts["state"] == "Running" and (ts["name"] or ts["ip"]):
        say("\n  A terminal on this computer from Termux (optional):")
        say("    on the phone:  pkg install python curl")
        say("                   curl -fsSL https://raw.githubusercontent.com/franc417/Tailscale-SSH/main/install.sh | bash")
        say(f"                   mesh add {ts['name'] or ts['ip']}" + (f"      (if that name does not resolve: mesh add {ts['ip']})" if ts["name"] and ts["ip"] else ""))
        if env.mesh_state() != "ok":
            say("    on this computer first:  meshd install   (sets up mesh and its SSH server)")
    return 0


# ---------------------------------------------------------------- command line

def main(argv: Optional[List[str]] = None, env: Optional[Env] = None) -> int:
    ap = argparse.ArgumentParser(prog=APP, description=f"{TITLE}: the computer's side of the Mesh phone app")
    ap.add_argument("--dir", default=mi.default_dir(), help="where the agent keeps its certificate and token")
    ap.add_argument("--port", type=int, default=mi.DEFAULT_PORT)
    sub = ap.add_subparsers(dest="command")
    p = sub.add_parser("status", help="what is running")
    p.add_argument("--watch", action="store_true", help="keep refreshing (Ctrl+C to leave)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("install", help="install what is missing, then set up")
    p.add_argument("--yes", action="store_true", help="do not ask before each step")
    p.add_argument("--install-only", action="store_true", help="stop after installing packages")
    p = sub.add_parser("login", help="Sunshine's web account")
    p.add_argument("--user", default=None)
    p.add_argument("--password-stdin", action="store_true")
    p.add_argument("--force", action="store_true", help="change the account even if the agent is signed in")
    sub.add_parser("enable", help="run everything in the background, at login and boot")
    sub.add_parser("disable", help="stop and unregister the background services")
    for verb in ("start", "stop", "restart"):
        p = sub.add_parser(verb)
        p.add_argument("target", nargs="?", default="all", choices=["all", "agent", "sunshine", "supervisor"])
    p = sub.add_parser("logs", help="what the services printed")
    p.add_argument("-n", type=int, default=60)
    p.add_argument("-f", action="store_true")
    p = sub.add_parser("pair", help="what to type, scan or paste on the phone")
    p.add_argument("--qr", action="store_true", help="draw the QR code even when the output is not a terminal")
    p.add_argument("--no-qr", action="store_true", help="do not draw the QR code")
    p.add_argument("--png", metavar="FILE", default=None, help="also save the QR code as a picture")
    p = sub.add_parser("supervise", help="the supervisor loop (run by its service)")
    p.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args(argv)

    env = env or Env(args.dir, args.port)
    style = Style(use_color())
    command = args.command or "status"

    if command == "status":
        if getattr(args, "json", False):
            print(status_json(env))
            return 0
        if getattr(args, "watch", False):
            try:
                while True:
                    sys.stdout.write("\x1b[H\x1b[2J" + status_text(env, style) + "\n")
                    sys.stdout.flush()
                    time.sleep(2)
            except KeyboardInterrupt:
                return 0
        print(status_text(env, style))
        return 0
    if command == "install":
        code = install(env, yes=args.yes)
        if args.install_only:
            return code
        print(f"\n{style.bold('Setting up the background services')}")
        code = enable(env) or code
        print(f"\n{style.bold('Sunshine account')}")
        code = login(env) or code
        print()
        pair_text(env, qr=sys.stdout.isatty(), columns=shutil.get_terminal_size().columns)
        print("\n" + status_text(env, style))
        return code
    if command == "login":
        password = sys.stdin.readline().rstrip("\n") if args.password_stdin else None
        return login(env, username=args.user, password=password, force=args.force)
    if command == "enable":
        code = enable(env)
        print("\n" + status_text(env, style))
        return code
    if command == "disable":
        return disable(env)
    if command in ("start", "stop", "restart"):
        return control(env, command, args.target)
    if command == "logs":
        cmd = ["journalctl", "--user", "-u", UNITS["agent"], "-u", UNITS["sunshine"], "-u", UNITS["supervisor"], "-n", str(args.n), "--no-pager"]
        if args.f:
            cmd = [c for c in cmd if c != "--no-pager"] + ["-f"]
        try:
            return subprocess.run(cmd).returncode
        except KeyboardInterrupt:
            return 0
    if command == "pair":
        return pair_text(env, qr=(args.qr or sys.stdout.isatty()) and not args.no_qr, png=args.png, columns=shutil.get_terminal_size().columns)
    if command == "supervise":
        try:
            Supervisor(env).run(args.interval)
        except KeyboardInterrupt:
            pass
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
