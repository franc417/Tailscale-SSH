#!/usr/bin/env python3
"""tailscale-ssh: browse your tailnet and SSH into any device, from any network.

One engine, one command, installed under whatever name you pick (default: mesh).
Every device on your tailnet runs the identical picker — there's no per-device
role to remember.

No IPs or ports are hardcoded anywhere. Every run asks Tailscale who is on your
tailnet right now, probes which of them answer SSH, and lets you pick one.
Tailscale IPs and MagicDNS names follow a device across WiFi, mobile data and
CGNAT, so a connection that works at home keeps working anywhere.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import copy
import datetime as dt
import getpass
import ipaddress
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:  # POSIX only; Windows falls back to a numbered prompt
    import termios
    import tty
except ImportError:  # pragma: no cover
    termios = tty = None

__version__ = "0.2.1"
REPO = "franc417/Tailscale-SSH"
REPO_FILE = "tailscale_ssh.py"
BRAND = "tailscale-ssh"  # set for real in main(); module default for direct imports

CONF_DIR = Path(os.environ.get("TSSH_CONFIG_DIR") or Path.home() / ".config" / "tailscale-ssh")
CONF_FILE = CONF_DIR / "config.json"
DEV_FILE = CONF_DIR / "devices.json"
DEFAULTS = {
    "probe_ports": [22, 8022, 2222],  # ports checked for an SSH banner on every device
    "refresh_seconds": 3,             # live-view refresh interval
    "api_key": None,                  # only needed where no `tailscale` CLI exists (Termux)
    "identity": None,                 # optional path to a private key
    "configured": False,
}
API_BASE = os.environ.get("TSSH_API_BASE", "https://api.tailscale.com")


# ───────────────────────────── styling ─────────────────────────────

class Style:
    enabled = True
    unicode = True


S = Style()
CODES = {
    "accent": "38;5;80", "ok": "38;5;78", "warn": "38;5;215", "bad": "38;5;203",
    "dim": "38;5;244", "bold": "1", "title": "1;38;5;80", "selbg": "48;5;237",
}
GLYPH_U = dict(logo="◈", dot="●", half="◐", ring="○", cur="▸", up="↑", down="↓", enter="⏎",
               rule="─", ok="✓", warn="!", bad="✗", sep="·", ell="…", spin="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
               live="●", arrow="→")
GLYPH_A = dict(logo="*", dot="*", half="o", ring=".", cur=">", up="^", down="v", enter="Enter",
               rule="-", ok="+", warn="!", bad="x", sep="|", ell="~", spin="|/-\\",
               live="*", arrow="->")


def g(name: str) -> str:
    return (GLYPH_U if S.unicode else GLYPH_A)[name]


def paint(text: str, *names: str) -> str:
    if not S.enabled or not names:
        return text
    return "\033[" + ";".join(CODES[n] for n in names) + "m" + text + "\033[0m"


def init_style(plain: bool = False) -> None:
    tty_out = sys.stdout.isatty()
    S.enabled = tty_out and not plain and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    S.unicode = enc.startswith("utf") and not os.environ.get("TSSH_ASCII")


def eprint(*a) -> None:
    print(*a, file=sys.stderr)


def die(msg: str, hint: str | None = None, code: int = 1):
    eprint(paint(f"{g('bad')} ", "bad") + msg)
    if hint:
        eprint(paint(f"  {g('arrow')} {hint}", "dim"))
    sys.exit(code)


# ───────────────────────────── config ─────────────────────────────

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path: Path, data, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(load_json(CONF_FILE, {}))
    return cfg


def save_config(cfg: dict) -> None:
    save_json(CONF_FILE, cfg, private=True)  # may hold an API key


# ───────────────────────────── helpers ─────────────────────────────

_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?$")


def parse_ts(s) -> float | None:
    """RFC3339 (Go flavour, up to 9 fractional digits) -> epoch seconds. Zero dates -> None."""
    if not s:
        return None
    m = _TS_RE.match(s)
    if not m:
        return None
    base, frac, tz = m.groups()
    frac = ((frac or ".") + "000000")[:7]
    tz = "+00:00" if tz in (None, "Z") else tz
    try:
        d = dt.datetime.fromisoformat(base + frac + tz)
    except ValueError:
        return None
    return None if d.year < 2000 else d.timestamp()


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    d = max(0, time.time() - ts)
    if d < 60:
        return "just now"
    for unit, sec in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= sec:
            return f"{int(d // sec)}{unit} ago"
    return "just now"


def detect_platform() -> str:
    if "com.termux" in os.environ.get("PREFIX", "") or os.environ.get("TERMUX_VERSION"):
        return "termux"
    if sys.platform == "darwin":
        return "macos"
    ids: set[str] = set()
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            k, _, v = line.partition("=")
            if k in ("ID", "ID_LIKE"):
                ids.update(v.strip('"').split())
    except OSError:
        pass
    if "arch" in ids:
        return "arch"
    if ids & {"debian", "ubuntu", "linuxmint"}:
        return "debian"
    if ids & {"fedora", "rhel", "centos"}:
        return "fedora"
    return "linux"


def local_tailscale_ip() -> str | None:
    """Our own tailnet address, found by asking the kernel which source IP it would use to reach
    Tailscale's 100.100.100.100. Works with the Android app (no CLI needed) and on Linux."""
    forced = os.environ.get("TSSH_LOCAL_IP")
    if forced:
        return forced
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("100.100.100.100", 53))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    try:
        return ip if ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10") else None
    except ValueError:
        return None


# ───────────────────────────── tailnet model ─────────────────────────────

@dataclass
class Node:
    name: str
    fqdn: str = ""
    ips: list = field(default_factory=list)
    os: str = ""
    online: bool = False
    is_self: bool = False
    mine: bool = True
    link: str = ""
    last_seen: float | None = None
    probed: bool = False
    ssh_port: int | None = None
    latency: float | None = None

    @property
    def ip(self) -> str:
        v4 = [i for i in self.ips if ":" not in i]
        return (v4 or self.ips or [""])[0]

    @property
    def state(self) -> str:
        if self.is_self:
            return "self"
        if not self.online:
            return "offline"
        if not self.probed:
            return "checking"
        return "ready" if self.ssh_port else "nossh"


class TSError(Exception):
    def __init__(self, msg: str, hint: str | None = None):
        super().__init__(msg)
        self.hint = hint


class NeedsLogin(TSError):
    pass


def ts_cli() -> str | None:
    if detect_platform() == "termux":
        # A `tailscale` binary can exist here (`pkg install tailscale`), but it's a separate,
        # disconnected instance from the Android Tailscale app -- it has no bearing on whether
        # this device is actually on the tailnet, so never trust it for status.
        return None
    return shutil.which("tailscale")


def ts_status_json() -> dict | None:
    exe = ts_cli()
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=10)
        return json.loads(p.stdout)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def parse_cli(data: dict, show_all: bool = False):
    me = data.get("Self") or {}
    my_uid = me.get("UserID")

    def mk(n: dict, is_self: bool = False) -> Node:
        dns = (n.get("DNSName") or "").rstrip(".")
        online = n.get("Online")
        if online is None:
            online = bool(n.get("Active")) or is_self
        link = ""
        if not is_self:
            if n.get("CurAddr"):
                link = "direct"
            elif n.get("Relay"):
                link = "relay " + str(n["Relay"])
        return Node(
            name=(dns.split(".")[0] if dns else n.get("HostName") or "unknown"),
            fqdn=dns, ips=list(n.get("TailscaleIPs") or []), os=(n.get("OS") or ""),
            online=bool(online), is_self=is_self,
            mine=(n.get("UserID") == my_uid) or bool(n.get("Tags")),
            link=link, last_seen=parse_ts(n.get("LastSeen")),
        )

    nodes = [mk(p) for p in (data.get("Peer") or {}).values()]
    if not show_all:
        nodes = [n for n in nodes if n.mine]
    tailnet = (data.get("CurrentTailnet") or {}).get("Name") or data.get("MagicDNSSuffix") or ""
    return nodes, {"backend": "cli", "tailnet": tailnet, "self": mk(me, True) if me else None}


def fetch_cli(show_all: bool):
    data = ts_status_json()
    if data is None:
        raise TSError("Can't read Tailscale status",
                      "Is tailscaled running? If you get 'access denied': sudo tailscale set --operator=$USER")
    state = data.get("BackendState")
    if state in ("NeedsLogin", "NeedsMachineAuth", "NoState"):
        raise NeedsLogin(f"Tailscale isn't signed in ({state})", f"Run: {BRAND} setup")
    if state != "Running":
        raise TSError(f"Tailscale is {state or 'not running'}", f"Run: sudo tailscale up   (or: {BRAND} setup)")
    return parse_cli(data, show_all)


def api_get_devices(key: str) -> list:
    req = urllib.request.Request(
        f"{API_BASE}/api/v2/tailnet/-/devices?fields=all",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                 "User-Agent": f"tailscale-ssh/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r).get("devices", [])
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TSError("Tailscale API key was rejected or has expired",
                          f"Make a new one at https://login.tailscale.com/admin/settings/keys, then run: {BRAND} setup")
        raise TSError(f"Tailscale API error {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TSError(f"Can't reach the Tailscale API ({getattr(e, 'reason', e)})", "Check your internet connection")


def parse_api(devices: list, my_ip: str | None, show_all: bool = False):
    me = next((d for d in devices if my_ip and my_ip in (d.get("addresses") or [])), None)
    my_user = me.get("user") if me else None
    now = time.time()

    def mk(d: dict, is_self: bool = False) -> Node:
        fqdn = (d.get("name") or "").rstrip(".")
        seen = parse_ts(d.get("lastSeen"))
        online = d.get("connectedToControl")
        if online is None:
            online = bool(seen and now - seen < 300)
        return Node(
            name=(fqdn.split(".")[0] if fqdn else d.get("hostname") or "unknown"), fqdn=fqdn,
            ips=list(d.get("addresses") or []), os=(d.get("os") or ""), online=bool(online) or is_self,
            is_self=is_self, mine=(my_user is None or d.get("user") == my_user or bool(d.get("tags"))),
            link="", last_seen=seen)

    nodes = [mk(d) for d in devices if d is not me]
    if not show_all:
        nodes = [n for n in nodes if n.mine]
    tailnet = ""
    if me and "." in (me.get("name") or ""):
        tailnet = me["name"].rstrip(".").split(".", 1)[1]
    return nodes, {"backend": "api", "tailnet": tailnet, "self": mk(me, True) if me else None}


def fetch_api(key: str, show_all: bool):
    my_ip = local_tailscale_ip()
    if not my_ip:
        raise TSError("Tailscale isn't connected on this device", "Open the Tailscale app and switch it on")
    return parse_api(api_get_devices(key), my_ip, show_all)


def fetch_nodes(cfg: dict, show_all: bool):
    if ts_cli():
        try:
            return fetch_cli(show_all)
        except TSError:
            if not cfg.get("api_key"):
                raise  # no fallback available -- surface the real CLI error
    if cfg.get("api_key"):
        return fetch_api(cfg["api_key"], show_all)
    raise TSError("Tailscale isn't set up on this device yet", f"Run: {BRAND} setup")


# ───────────────────────────── probing ─────────────────────────────

def probe_ssh(ip: str, ports: list, timeout: float = 1.5):
    """(port, connect_ms) for the first port in `ports` that greets us with an SSH banner, else None."""
    def one(port: int):
        t0 = time.perf_counter()
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
        except OSError:
            return None
        ms = (time.perf_counter() - t0) * 1000
        try:
            s.settimeout(1.0)
            buf = b""
            while len(buf) < 4:
                chunk = s.recv(4 - len(buf))
                if not chunk:
                    break
                buf += chunk
            ok = buf == b"SSH-"
        except OSError:
            ok = False
        finally:
            s.close()
        return (port, ms) if ok else None

    if not ip or not ports:
        return None
    with cf.ThreadPoolExecutor(max_workers=len(ports)) as ex:
        results = list(ex.map(one, ports))
    return next((r for r in results if r), None)


def ports_for(node: Node, cfg: dict, devices: dict) -> list:
    remembered = (devices.get(node.name) or {}).get("port")
    ports = list(cfg.get("probe_ports") or [22])
    return ([remembered] if remembered else []) + [p for p in ports if p != remembered]


class Monitor:
    """Keeps a live picture of the tailnet: who is online and who answers SSH."""

    def __init__(self, cfg: dict, devices: dict, show_all: bool = False):
        self.cfg, self.devices, self.show_all = cfg, devices, show_all
        self.nodes: list[Node] = []
        self.meta: dict = {}
        self.error: TSError | None = None
        self.busy = True
        self.version = 0
        self._cache: dict[str, tuple] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()

    def _sorted(self, nodes):
        def key(n: Node):
            last = (self.devices.get(n.name) or {}).get("last_used", 0)
            return (0 if n.online else 1, -last, n.name.lower())
        return sorted(nodes, key=key)

    def refresh_once(self) -> None:
        with self._lock:
            self.busy = True
            self.version += 1
        try:
            nodes, meta = fetch_nodes(self.cfg, self.show_all)
        except TSError as e:
            with self._lock:
                self.error, self.nodes, self.busy = e, [], False
                self.version += 1
            return
        with self._lock:
            for n in nodes:
                c = self._cache.get(n.name)
                if c and n.online:
                    n.probed, n.ssh_port, n.latency = True, c[0], c[1]
                elif not n.online:
                    self._cache.pop(n.name, None)
            self.nodes, self.meta, self.error = self._sorted(nodes), meta, None
            self.version += 1

        def work(n: Node) -> None:
            r = probe_ssh(n.ip, ports_for(n, self.cfg, self.devices))
            with self._lock:
                n.probed = True
                n.ssh_port, n.latency = r if r else (None, None)
                self._cache[n.name] = (n.ssh_port, n.latency)
                self.version += 1

        targets = [n for n in nodes if n.online and n.ip]
        if targets:
            with cf.ThreadPoolExecutor(max_workers=min(16, len(targets))) as ex:
                list(ex.map(work, targets))
        with self._lock:
            self.busy = False
            self.version += 1

    def snapshot(self):
        with self._lock:
            return (self.version, [copy.copy(n) for n in self.nodes], dict(self.meta), self.error, self.busy)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            self._wake.wait(float(self.cfg.get("refresh_seconds") or 3))
            self._wake.clear()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def rescan(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


# ───────────────────────────── rendering ─────────────────────────────

def _trunc(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + g("ell")


def _status(n: Node):
    s = n.state
    if s == "ready":
        return f"ready :{n.ssh_port}", "ok"
    if s == "checking":
        return f"checking{g('ell')}", "dim"
    if s == "nossh":
        return f"online {g('sep')} no ssh", "warn"
    if s == "offline":
        return (f"offline {g('sep')} {ago(n.last_seen)}" if n.last_seen else "offline"), "bad"
    return "this device", "dim"


def _dot(n: Node):
    return {"ready": (g("dot"), "ok"), "checking": (g("half"), "dim"), "nossh": (g("half"), "warn"),
            "offline": (g("ring"), "bad")}.get(n.state, (g("dot"), "dim"))


def _paint_cell(segs, width: int, selected: bool, align: str = "l") -> str:
    bg = ("selbg",) if selected else ()
    plain = "".join(t for t, _ in segs)
    pad = max(0, width - len(plain))
    out = []
    if align == "r" and pad:
        out.append(paint(" " * pad, *bg))
    out += [paint(t, *(tuple(st) + bg)) for t, st in segs]
    if align == "l" and pad:
        out.append(paint(" " * pad, *bg))
    return "".join(out)


def render(nodes, meta, err, busy, sel, interactive, width, brand, tick=0):
    """Return the screen as a list of lines. Every line is at most width-1 visible characters."""
    W = max(30, width) - 1
    L: list[str] = []
    tailnet = (meta or {}).get("tailnet") or ""
    me = (meta or {}).get("self")
    head = f"{g('logo')} {brand}"
    tail = _trunc(f"  {g('sep')}  {tailnet}", max(0, W - len(head))) if tailnet else ""
    L.append(paint(head, "title") + paint(tail, "dim"))
    if me:
        L.append(paint(_trunc(f"this device: {me.name}  {g('sep')}  {me.ip}", W), "dim"))
    rule = paint(g("rule") * min(W, 96), "dim")
    L.append(rule)

    if err is not None:
        L.append("")
        L.append(paint(f" {g('bad')} ", "bad") + _trunc(str(err), W - 4))
        if getattr(err, "hint", None):
            L.append(paint(_trunc(f"   {g('arrow')} {err.hint}", W), "dim"))
        L.append("")
    elif not nodes:
        L.append("")
        L.append(paint(_trunc(" No other devices found on your tailnet yet.", W), "warn"))
        L.append(paint(_trunc(" Install on another device and sign in with the same account.", W), "dim"))
        L.append("")
    else:
        name_w = min(24, max([len(n.name) for n in nodes] + [6]))
        stat_w = max([len(_status(n)[0]) for n in nodes] + [6])
        os_w = min(9, max([len(n.os) for n in nodes] + [2]))
        path_w = max([len(n.link) for n in nodes] + [4])
        room = W - 4 - 2 - 2 - 2                 # what's left for name + status after #, dot and gaps
        if name_w + stat_w > room:               # very narrow terminal: shrink name first, then status
            name_w = max(6, min(name_w, room - stat_w))
            stat_w = max(8, min(stat_w, room - name_w))
        used = 4 + 2 + (2 + name_w) + 2 + stat_w
        extra = []
        for key, w in (("lat", 7), ("addr", 15), ("os", os_w), ("path", path_w)):
            if used + 2 + w <= W:
                used += 2 + w
                extra.append((key, w))
        keys = {k for k, _ in extra}
        widths = dict(extra)

        def row(cells, selected=False):
            gap = paint("  ", "selbg") if selected else "  "
            return gap.join(_paint_cell(c[0], c[1], selected, c[2] if len(c) > 2 else "l") for c in cells)

        hdr = [([("#", ("dim",))], 4, "r"), ([("DEVICE", ("dim",))], 2 + name_w)]
        if "os" in keys:
            hdr.append(([("OS", ("dim",))], widths["os"]))
        if "addr" in keys:
            hdr.append(([("ADDRESS", ("dim",))], widths["addr"]))
        hdr.append(([("STATUS", ("dim",))], stat_w))
        if "path" in keys:
            hdr.append(([("PATH", ("dim",))], widths["path"]))
        if "lat" in keys:
            hdr.append(([("LATENCY", ("dim",))], widths["lat"], "r"))
        L.append(row(hdr))

        for i, n in enumerate(nodes, 1):
            selected = interactive and n.name == sel
            dot, dot_style = _dot(n)
            st_text, st_style = _status(n)
            name_style = ("dim",) if n.state == "offline" else (("bold",) if n.state == "ready" else ())
            cur = g("cur") if selected else " "
            cells = [
                ([(cur + " ", ("accent",)), (str(i).rjust(2), ("dim",))], 4),
                ([(dot + " ", (dot_style,)), (_trunc(n.name, name_w), name_style)], 2 + name_w),
            ]
            if "os" in keys:
                cells.append(([(_trunc(n.os, widths["os"]), ("dim",))], widths["os"]))
            if "addr" in keys:
                cells.append(([(n.ip, ("dim",))], widths["addr"]))
            cells.append(([(_trunc(st_text, stat_w), (st_style,))], stat_w))
            if "path" in keys:
                cells.append(([(n.link, ("ok",) if n.link == "direct" else ("warn",))], widths["path"]))
            if "lat" in keys:
                lat_style = () if n.latency is None else (("ok",) if n.latency < 80 else ("warn",) if n.latency < 250 else ("bad",))
                cells.append(([(f"{n.latency:.0f} ms" if n.latency is not None and n.state == "ready" else "", lat_style)],
                              widths["lat"], "r"))
            L.append(row(cells, selected))

    L.append(rule)
    if nodes:
        ready = sum(n.state == "ready" for n in nodes)
        online = sum(n.online for n in nodes)
        spin = g("spin")
        ind_plain = (spin[tick % len(spin)] + " scanning") if busy else (g("live") + " live")
        ind = paint(ind_plain, "accent" if busy else "ok")
        for summary in (f"{ready} ready {g('sep')} {online} online {g('sep')} {len(nodes)} total",
                        f"{ready} ready {g('sep')} {online} online", f"{ready} ready"):
            if len(summary) + 1 + len(ind_plain) <= W:
                break
        L.append(paint(summary, "dim") + " " * max(1, W - len(summary) - len(ind_plain)) + ind)
    if interactive:
        up_down = g("up") + g("down")
        variants = [
            [(up_down, "select"), (g("enter"), "connect"), ("r", "rescan"), ("q", "quit")],
            [(up_down, "move"), (g("enter"), "go"), ("r", "scan"), ("q", "quit")],
            [(up_down, ""), (g("enter"), ""), ("r", ""), ("q", "")],
        ]
        for keys_ in variants:
            if sum(len(k) + (1 + len(v) if v else 0) for k, v in keys_) + 2 * (len(keys_) - 1) <= W:
                break
        L.append("  ".join(paint(k, "accent") + ((" " + paint(v, "dim")) if v else "") for k, v in keys_))
    return L


# ───────────────────────────── interactive picker ─────────────────────────────

def read_key(fd: int) -> str:
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        if select.select([fd], [], [], 0.03)[0]:
            seq = ch + os.read(fd, 8)
            return {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1bOA": "up", b"\x1bOB": "down"}.get(seq[:3], "other")
        return "esc"
    if ch in (b"\r", b"\n"):
        return "enter"
    return ch.decode(errors="ignore")


def default_sel(nodes) -> str | None:
    for wanted in ("ready", "checking", "nossh"):
        for n in nodes:
            if n.state == wanted:
                return n.name
    return nodes[0].name if nodes else None


def pick_interactive(mon: Monitor, brand: str) -> Node | None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    out = sys.stdout
    sel, touched, last = None, False, None
    mon.start()
    try:
        tty.setcbreak(fd)
        out.write("\033[?1049h\033[?25l")
        out.flush()
        while True:
            _, nodes, meta, err, busy = mon.snapshot()
            names = [n.name for n in nodes]
            if not touched or sel not in names:
                sel = default_sel(nodes)
            lines = render(nodes, meta, err, busy, sel, True, shutil.get_terminal_size((80, 24)).columns,
                           brand, tick=int(time.time() * 10))
            frame = "\033[H" + "\n".join(l + "\033[K" for l in lines) + "\033[J"
            if frame != last:
                out.write(frame)
                out.flush()
                last = frame
            if not select.select([fd], [], [], 0.15)[0]:
                continue
            key = read_key(fd)
            idx = names.index(sel) if sel in names else 0
            if key in ("up", "k") and names:
                sel, touched = names[(idx - 1) % len(names)], True
            elif key in ("down", "j") and names:
                sel, touched = names[(idx + 1) % len(names)], True
            elif key == "enter" and sel:
                return next(n for n in nodes if n.name == sel)
            elif key in ("q", "esc"):
                return None
            elif key == "r":
                mon.rescan()
            elif key.isdigit() and key != "0" and int(key) <= len(nodes):
                return nodes[int(key) - 1]
    except KeyboardInterrupt:
        return None
    finally:
        mon.stop()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.write("\033[?25h\033[?1049l")
        out.flush()


def pick_numbered(mon: Monitor, brand: str) -> Node | None:
    mon.refresh_once()
    _, nodes, meta, err, _ = mon.snapshot()
    print("\n".join(render(nodes, meta, err, False, None, False, shutil.get_terminal_size((80, 24)).columns, brand)))
    if err or not nodes:
        return None
    try:
        ans = input("Connect to # or name: ").strip()
    except EOFError:
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(nodes):
        return nodes[int(ans) - 1]
    hits = match_node(nodes, ans) if ans else []
    return hits[0] if len(hits) == 1 else None


# ───────────────────────────── prompts ─────────────────────────────

ASSUME_YES = False


def ask(prompt: str, default: str | None = None, secret: bool = False) -> str | None:
    suffix = f" [{default}]" if default else ""
    try:
        text = f"{paint('?', 'accent')} {prompt}{suffix}: "
        ans = (getpass.getpass(text) if secret else input(text)).strip()
    except EOFError:
        ans = ""
    return ans or default


def mask_secret(s: str) -> str:
    if len(s) <= 8:
        return "•" * len(s)
    return s[:4] + "•" * (len(s) - 8) + s[-4:]


def read_termux_clipboard() -> str | None:
    if not shutil.which("termux-clipboard-get"):
        return None
    try:
        out = subprocess.run(["termux-clipboard-get"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def prompt_api_key(plat: str) -> str | None:
    """Get a Tailscale API key interactively. A hidden getpass prompt gives zero feedback on
    whether a paste actually landed, which is exactly the confusing part -- so every path here
    ends by showing a masked preview + length of whatever was captured, and Termux gets a
    clipboard-read shortcut that sidesteps paste-into-hidden-field entirely."""
    if plat == "termux" and shutil.which("termux-clipboard-get"):
        info("Copy your API key (long-press it on the Tailscale page → Copy), then come back here.")
        try:
            input(f"{paint('?', 'accent')} Press Enter once it's copied: ")
        except EOFError:
            pass
        clip = read_termux_clipboard()
        if clip:
            print(paint(f"    Clipboard: {mask_secret(clip)}  ({len(clip)} chars)", "dim"))
            if not clip.startswith("tskey"):
                warn("That doesn't look like a Tailscale key (expected to start with 'tskey-').")
            if confirm("Use this?", clip.startswith("tskey")):
                return clip
            info("OK, you can paste it manually instead.")

    for attempt in range(3):
        key = ask("Paste your API key (hidden)", secret=True)
        if key:
            print(paint(f"    Got it: {mask_secret(key)}  ({len(key)} chars)", "dim"))
            if not key.startswith("tskey"):
                warn("That doesn't look like a Tailscale key (expected to start with 'tskey-') -- using it anyway.")
            return key
        warn("Nothing came through." if attempt == 0 else "Still nothing.")
        if attempt < 2:
            info("In Termux: long-press the input line → Paste, then press Enter.")
    warn("Couldn't get a key this way.")
    info(f"Add one later without retyping it here: {BRAND} setup --api-key <key>")
    return None


def confirm(prompt: str, default: bool = True) -> bool:
    if ASSUME_YES:
        return True
    if not sys.stdin.isatty():
        return default
    hint = "Y/n" if default else "y/N"
    try:
        ans = input(f"{paint('?', 'accent')} {prompt} [{hint}] ").strip().lower()
    except EOFError:
        return default
    return default if not ans else ans.startswith("y")


def ok(msg: str) -> None:
    print(paint(f"  {g('ok')} ", "ok") + msg)


def warn(msg: str) -> None:
    print(paint(f"  {g('warn')} ", "warn") + msg)


def bad(msg: str) -> None:
    print(paint(f"  {g('bad')} ", "bad") + msg)


def info(msg: str) -> None:
    print(paint(f"    {msg}", "dim"))


def head(msg: str) -> None:
    print()
    print(paint(f"{g('logo')} {msg}", "title"))


def sudo() -> list:
    if os.geteuid() == 0 or detect_platform() == "termux" or not shutil.which("sudo"):
        return []
    return ["sudo"]


def run(cmd: list, **kw) -> int:
    print(paint("    $ " + " ".join(cmd), "dim"))
    try:
        return subprocess.call(cmd, **kw)
    except OSError as e:
        bad(f"couldn't run {cmd[0]}: {e}")
        return 127


# ───────────────────────────── connecting ─────────────────────────────

def match_node(nodes, query: str):
    q = query.lower()
    exact = [n for n in nodes if q in (n.name.lower(), n.fqdn.lower()) or q in n.ips]
    if exact:
        return exact
    prefix = [n for n in nodes if n.name.lower().startswith(q)]
    return prefix or [n for n in nodes if q in n.name.lower()]


def find_key() -> Path | None:
    for name in ("id_ed25519", "id_ecdsa", "id_rsa"):
        p = Path.home() / ".ssh" / name
        if p.exists():
            return p
    return None


def ensure_key() -> Path | None:
    key = find_key()
    if key:
        return key
    if not shutil.which("ssh-keygen") or not confirm("No SSH key found. Create one now (ed25519)?", True):
        return None
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    key = ssh_dir / "id_ed25519"
    rc = run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-C", f"{getpass.getuser()}@{socket.gethostname()}"])
    return key if rc == 0 and key.exists() else None


def connect(node: Node, ns, cfg: dict, devices: dict, remote_cmd: list) -> int:
    if node.is_self:
        die("That's this device.")
    android = node.os.lower() == "android"
    rec = devices.get(node.name) or {}

    if node.online and not node.probed:
        r = probe_ssh(node.ip, ports_for(node, cfg, devices))
        node.probed, (node.ssh_port, node.latency) = True, (r if r else (None, None))

    if not node.online:
        warn(f"{node.name} is offline (last seen {ago(node.last_seen)}).")
        if not confirm("Try anyway?", False):
            return 1
    elif not node.ssh_port and not ns.port:
        warn(f"{node.name} is online but no SSH server answered on ports {', '.join(map(str, cfg['probe_ports']))}.")
        info("Start sshd there, or give the port yourself: -p PORT")
        if not confirm("Try anyway?", True):
            return 1

    user = ns.user or rec.get("user")
    new_device = not rec.get("user")
    if not user:
        if android:
            info("Termux usernames look like u0_a123. Run `whoami` in Termux to see yours.")
        user = ask(f"SSH username on {node.name}", None if android else getpass.getuser())
        if not user:
            die("A username is required.")
    port = ns.port or node.ssh_port or rec.get("port")
    if not port:
        port = int(ask("SSH port", "8022" if android else "22") or 22)

    alias = f"ts-{node.name}"
    base = ["-p", str(port), "-o", "StrictHostKeyChecking=accept-new", "-o", f"HostKeyAlias={alias}"]
    if new_device and ns.user is None and shutil.which("ssh-copy-id") and node.online:
        if confirm(f"Install your SSH key on {node.name} for password-less logins?", True):
            key = ensure_key()
            if key:
                run(["ssh-copy-id", "-i", str(key) + ".pub", *base, f"{user}@{node.ip}"])
    identity = ns.identity or cfg.get("identity")

    devices[node.name] = {"user": user, "port": int(port), "last_used": time.time()}
    save_json(DEV_FILE, devices)

    if not shutil.which("ssh"):
        hint = {"termux": "pkg install openssh", "arch": "sudo pacman -S openssh",
                "debian": "sudo apt install openssh-client"}.get(detect_platform(), "install an OpenSSH client")
        die("No `ssh` client found.", hint)
    cmd = ["ssh", *base, "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    if identity:
        cmd += ["-i", str(identity)]
    if remote_cmd:
        cmd.append("-t")
    cmd += [f"{user}@{node.ip}", *remote_cmd]

    detail = " ".join(x for x in (node.link, f"{node.latency:.0f} ms" if node.latency else "") if x)
    print(paint(f"{g('arrow')} ", "accent") + paint(node.name, "bold") + paint(f"  {user}@{node.ip}:{port}  {detail}", "dim"))
    sys.stdout.flush()
    os.execvp("ssh", cmd)


# ───────────────────────────── commands ─────────────────────────────

def node_dict(n: Node) -> dict:
    return {"name": n.name, "fqdn": n.fqdn, "ip": n.ip, "os": n.os, "state": n.state, "online": n.online,
            "ssh_port": n.ssh_port, "latency_ms": round(n.latency, 1) if n.latency else None,
            "path": n.link or None, "last_seen": n.last_seen}


def cmd_list(ns, cfg, devices, brand) -> int:
    mon = Monitor(cfg, devices, ns.all)
    mon.refresh_once()
    _, nodes, meta, err, _ = mon.snapshot()
    if ns.json:
        if err:
            print(json.dumps({"error": str(err), "hint": err.hint}))
            return 1
        print(json.dumps({"tailnet": meta.get("tailnet"), "devices": [node_dict(n) for n in nodes]}, indent=2))
        return 0
    print("\n".join(render(nodes, meta, err, False, None, False, shutil.get_terminal_size((80, 24)).columns, brand)))
    return 1 if err else 0


def cmd_watch(ns, cfg, devices, brand) -> int:
    if not (sys.stdout.isatty() and termios):
        die("watch needs a terminal.", f"Use: {BRAND} list --json")
    mon = Monitor(cfg, devices, ns.all)
    mon.start()
    out, last = sys.stdout, None
    try:
        out.write("\033[?1049h\033[?25l")
        while True:
            _, nodes, meta, err, busy = mon.snapshot()
            lines = render(nodes, meta, err, busy, None, False, shutil.get_terminal_size((80, 24)).columns,
                           brand, tick=int(time.time() * 10))
            lines.append(paint("ctrl-c to exit", "dim"))
            frame = "\033[H" + "\n".join(l + "\033[K" for l in lines) + "\033[J"
            if frame != last:
                out.write(frame)
                out.flush()
                last = frame
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        mon.stop()
        out.write("\033[?25h\033[?1049l")
        out.flush()


def cmd_forget(ns, devices) -> int:
    if len(ns.words) < 2:
        die("Usage: forget <device>")
    name = ns.words[1]
    if devices.pop(name, None) is None:
        die(f"Nothing remembered for '{name}'.")
    save_json(DEV_FILE, devices)
    ok(f"Forgot saved user/port for {name}")
    return 0


def cmd_config(cfg) -> int:
    shown = dict(cfg)
    if shown.get("api_key"):
        shown["api_key"] = shown["api_key"][:12] + "…"
    print(paint(str(CONF_FILE), "dim"))
    print(json.dumps(shown, indent=2, sort_keys=True))
    return 0


def gh_token() -> str | None:
    tok = os.environ.get("TSSH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5).stdout.strip()
            return out or None
        except (subprocess.TimeoutExpired, OSError):
            return None
    return None


def gh_fetch(path: str, ref: str = "main") -> bytes:
    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": f"tailscale-ssh/{__version__}"}
    tok = gh_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/contents/{path}?ref={ref}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        hint = "The repo is private: set TSSH_TOKEN (a GitHub token with read access)" if e.code in (401, 403, 404) else None
        die(f"GitHub returned {e.code} while fetching {path}", hint)
    except (urllib.error.URLError, OSError) as e:
        die(f"Can't reach GitHub ({getattr(e, 'reason', e)})")


def cmd_update() -> int:
    me = Path(__file__).resolve()
    if (me.parent / ".git").exists():
        die("Running from a git checkout.", f"Update with: git -C {me.parent} pull")
    src = gh_fetch(REPO_FILE, os.environ.get("TSSH_REF", "main"))
    try:
        compile(src, REPO_FILE, "exec")
    except SyntaxError as e:
        die(f"Downloaded file failed a syntax check ({e.msg}); keeping the current version.")
    m = re.search(rb'__version__\s*=\s*"([^"]+)"', src)
    new = m.group(1).decode() if m else "?"
    if src == me.read_bytes():
        ok(f"Already up to date (v{__version__})")
        return 0
    fd, tmp = tempfile.mkstemp(dir=me.parent, prefix=".tssh-")
    with os.fdopen(fd, "wb") as f:
        f.write(src)
    os.chmod(tmp, 0o755)
    os.replace(tmp, me)
    ok(f"Updated v{__version__} {g('arrow')} v{new}")
    return 0


def cmd_doctor(cfg, devices) -> int:
    plat = detect_platform()
    head("Doctor")
    print(paint(f"    platform: {plat}  {g('sep')}  python {sys.version.split()[0]}  {g('sep')}  v{__version__}", "dim"))
    ok("python 3.8+") if sys.version_info >= (3, 8) else bad("python is too old (need 3.8+)")
    ok("ssh client found") if shutil.which("ssh") else bad("no ssh client")
    ok("ssh key present") if find_key() else warn("no SSH key yet (setup can create one)")
    if ts_cli():
        ok("tailscale CLI found")
        data = ts_status_json()
        if data is None:
            bad("can't read status (daemon down, or run: sudo tailscale set --operator=$USER)")
        else:
            st = data.get("BackendState")
            (ok if st == "Running" else bad)(f"tailscale state: {st}")
    elif cfg.get("api_key"):
        ok("no CLI here, using the Tailscale API key")
    else:
        bad(f"no tailscale CLI and no API key. Run: {BRAND} setup")
    ip = local_tailscale_ip()
    ok(f"this device is on the tailnet ({ip})") if ip else warn("this device has no tailnet address right now")
    local = probe_ssh("127.0.0.1", cfg["probe_ports"])
    ok(f"SSH server running here on port {local[0]}") if local else warn("no SSH server running here (others can't connect to you)")
    try:
        nodes, _ = fetch_nodes(cfg, False)
        ok(f"{len(nodes)} other device(s) visible on the tailnet")
    except TSError as e:
        bad(str(e))
        if e.hint:
            info(e.hint)
    print()
    return 0


# ───────────────────────────── setup wizard ─────────────────────────────

def step_termux_tailscale(ns, cfg) -> None:
    head("1 · Tailscale on this phone")
    ip = local_tailscale_ip()
    if not ip:
        warn("Tailscale isn't connected on this phone.")
        info("Install the Tailscale app (Play Store or F-Droid), sign in, and switch it on.")
        info("https://play.google.com/store/apps/details?id=com.tailscale.ipn")
        if shutil.which("termux-open-url") and confirm("Open the Play Store page?", False):
            run(["termux-open-url", "https://play.google.com/store/apps/details?id=com.tailscale.ipn"])
        if sys.stdin.isatty():
            ask("Press Enter once the app says Connected")
        ip = local_tailscale_ip()
    ok(f"Tailscale is active on this phone ({ip})") if ip else warn("Still not detected. Continuing anyway.")

    key = ns.api_key or cfg.get("api_key")
    if not key:
        print()
        info("Android has no `tailscale` command, so the device list comes from Tailscale's API.")
        info("Create a key: https://login.tailscale.com/admin/settings/keys  →  Generate access token")
        info(f"(keys expire after at most 90 days; run `{BRAND} setup` again to replace it)")
        key = prompt_api_key("termux")

    while key:
        try:
            n = len(api_get_devices(key))
            cfg["api_key"] = key
            ok(f"API key works ({n} devices on your tailnet)")
            break
        except TSError as e:
            bad(str(e))
            key = prompt_api_key("termux") if confirm("Try a different key?", True) else None

    if not cfg.get("api_key"):
        warn("No working API key -- this phone won't be able to list other devices yet.")
        info(f"Run `{BRAND} setup --api-key <key>` any time to add one.")


def step_linux_tailscale(ns, cfg, plat: str) -> None:
    head("1 · Tailscale")
    if not ts_cli():
        warn("Tailscale isn't installed.")
        if plat == "macos":
            info("Install it from https://tailscale.com/download and sign in, then re-run setup.")
            return
        if not confirm("Install it now?", True):
            return
        if plat == "arch":
            run([*sudo(), "pacman", "-S", "--needed", "--noconfirm", "tailscale"])
        else:
            run(["sh", "-c", "curl -fsSL https://tailscale.com/install.sh | sh"])
        if not ts_cli():
            bad("Install didn't finish. See https://tailscale.com/download")
            return
    ok("tailscale CLI found")
    if shutil.which("systemctl"):
        run([*sudo(), "systemctl", "enable", "--now", "tailscaled"])

    data = ts_status_json() or {}
    if data.get("BackendState") == "Running":
        ok("Already signed in and connected")
        return
    print()
    info("Signing in creates your Tailscale account the first time (Google, Microsoft, GitHub or Apple).")
    info("Use the SAME login on every device so they all share one tailnet.")
    up = [*sudo(), "tailscale", "up", f"--operator={getpass.getuser()}"]
    if ns.authkey:
        up.append(f"--auth-key={ns.authkey}")
    else:
        up.append("--qr")  # prints a QR code as well as the login link
    rc = run(up)
    if rc != 0 and "--qr" in up:
        up.remove("--qr")
        rc = run(up)
    for _ in range(20):
        if (ts_status_json() or {}).get("BackendState") == "Running":
            ok("Connected to your tailnet")
            return
        time.sleep(1)
    bad(f"Not connected yet. Finish the sign-in in your browser, then run: {BRAND} setup")


def ensure_sshd(plat: str, cfg: dict) -> None:
    head("2 · Make this device reachable")
    r = probe_ssh("127.0.0.1", cfg["probe_ports"])
    if r:
        ok(f"SSH server is running here (port {r[0]}), so your other devices can connect")
        return
    warn("No SSH server is running here, so this device will show as 'no ssh' for your others.")
    if not confirm("Install and start one now?", True):
        return
    if plat == "termux":
        run(["pkg", "install", "-y", "openssh"])
        run(["sshd"])
        info(f"Termux SSH listens on port 8022. Your username here: {getpass.getuser()}")
        if confirm("Set a password for SSH logins now?", True):
            run(["passwd"])
    elif plat == "arch":
        run([*sudo(), "pacman", "-S", "--needed", "--noconfirm", "openssh"])
        run([*sudo(), "systemctl", "enable", "--now", "sshd"])
    elif plat == "debian":
        run([*sudo(), "apt-get", "install", "-y", "openssh-server"])
        run([*sudo(), "systemctl", "enable", "--now", "ssh"])
    elif plat == "fedora":
        run([*sudo(), "dnf", "install", "-y", "openssh-server"])
        run([*sudo(), "systemctl", "enable", "--now", "sshd"])
    else:
        info("Turn on Remote Login / an SSH server for this OS, then re-run setup.")
        return
    time.sleep(1)
    r = probe_ssh("127.0.0.1", cfg["probe_ports"])
    ok(f"SSH server is up on port {r[0]}") if r else warn(f"Couldn't confirm it started; check `{BRAND} doctor`")


def cmd_setup(ns, cfg, devices, brand) -> int:
    plat = detect_platform()
    print(paint(f"\n{g('logo')} {brand} setup", "title") + paint(f"  {g('sep')}  {plat}", "dim"))
    if plat == "termux":
        step_termux_tailscale(ns, cfg)
    else:
        step_linux_tailscale(ns, cfg, plat)
    ensure_sshd(plat, cfg)
    head("3 · SSH key")
    key = ensure_key()
    ok(f"Using {key}") if key else warn("No key. You can still log in with a password.")
    cfg["configured"] = True
    save_config(cfg)

    head("Done")
    try:
        nodes, meta = fetch_nodes(cfg, False)
        me = meta.get("self")
        if me:
            ok(f"This device: {me.name}  ({me.ip})")
        ok(f"{len(nodes)} other device(s) on your tailnet")
    except TSError as e:
        warn(str(e))
        if e.hint:
            info(e.hint)
    print()
    info(f"Run `{brand}` to see your devices. On each new device: install, run setup, sign in with the same account.")
    print()
    return 0


# ───────────────────────────── entry point ─────────────────────────────

HELP = """\
{brand} {version}: SSH into any device on your tailnet, from any network.

  {brand}                     pick a device from a live list
  {brand} <device>            connect directly (name, prefix or IP; user@device works)
  {brand} <device> -- <cmd>   run a command there (e.g. tmux attach)

  list [--json] [-a]          show devices once
  watch                       live view without connecting
  setup [--authkey K]         first-run wizard: Tailscale, SSH server, key
  doctor                      diagnose problems
  update                      update from GitHub
  forget <device>             drop the saved user/port for a device
  config                      show settings

options: -u USER  -p PORT  -i KEYFILE  -a/--all (include devices shared with you)
         -y (assume yes)  --plain (no colour)  -V/--version
"""

SUBS = {"list", "ls", "watch", "setup", "doctor", "update", "forget", "config", "version", "help"}


def main(argv=None) -> int:
    global ASSUME_YES
    argv = list(sys.argv[1:] if argv is None else argv)
    remote_cmd: list = []
    if "--" in argv:
        i = argv.index("--")
        argv, remote_cmd = argv[:i], argv[i + 1:]

    global BRAND
    brand = os.environ.get("TSSH_BRAND") or Path(sys.argv[0]).stem
    BRAND = brand

    p = argparse.ArgumentParser(prog=brand, add_help=False)
    p.add_argument("words", nargs="*")
    p.add_argument("-a", "--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--plain", action="store_true")
    p.add_argument("-u", "--user")
    p.add_argument("-p", "--port", type=int)
    p.add_argument("-i", "--identity")
    p.add_argument("-y", "--yes", action="store_true")
    p.add_argument("--authkey")
    p.add_argument("--api-key")
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("-V", "--version", action="store_true")
    ns = p.parse_intermixed_args(argv)

    init_style(ns.plain)
    ASSUME_YES = ns.yes
    if ns.version:
        print(f"tailscale-ssh {__version__}")
        return 0
    cmd = ns.words[0] if ns.words else None
    if ns.help or cmd in ("help", "version"):
        print(HELP.format(brand=brand, version=__version__) if cmd != "version" else f"tailscale-ssh {__version__}")
        return 0

    cfg = load_config()
    devices = load_json(DEV_FILE, {})

    if cmd == "update":
        return cmd_update()
    if cmd == "setup":
        return cmd_setup(ns, cfg, devices, brand)
    if cmd == "doctor":
        return cmd_doctor(cfg, devices)
    if cmd == "config":
        return cmd_config(cfg)
    if cmd == "forget":
        return cmd_forget(ns, devices)

    if not cfg.get("configured") and sys.stdin.isatty() and sys.stdout.isatty() and not ns.json:
        print(paint(f"\nFirst run: let's get this device ready.", "accent"))
        cmd_setup(ns, cfg, devices, brand)
        cfg = load_config()

    if cmd in ("list", "ls"):
        return cmd_list(ns, cfg, devices, brand)
    if cmd == "watch":
        return cmd_watch(ns, cfg, devices, brand)

    if cmd:  # direct connect
        target = cmd
        if "@" in target:
            ns.user, target = target.split("@", 1)
        try:
            nodes, _ = fetch_nodes(cfg, True)
        except TSError as e:
            die(str(e), e.hint)
        hits = match_node(nodes, target)
        if not hits:
            die(f"No device matches '{target}'.", f"Devices: {', '.join(n.name for n in nodes) or 'none'}")
        if len(hits) > 1:
            die(f"'{target}' is ambiguous: {', '.join(n.name for n in hits)}")
        return connect(hits[0], ns, cfg, devices, remote_cmd)

    mon = Monitor(cfg, devices, ns.all)
    interactive = termios and sys.stdin.isatty() and sys.stdout.isatty()
    node = pick_interactive(mon, brand) if interactive else pick_numbered(mon, brand)
    return connect(node, ns, cfg, devices, remote_cmd) if node else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
