"""Tests for tailscale_ssh. Run: python3 -m unittest discover -s tests -v

Uses a fake `tailscale` and `ssh` on PATH plus real local TCP listeners that speak an SSH banner,
so the whole pipeline (status -> probe -> render -> connect) runs for real without a tailnet.
"""
import http.server
import json
import os
import pty
import re
import select
import socket
import struct
import fcntl
import termios
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import tailscale_ssh as ts  # noqa: E402

MOCKBIN = ROOT / "tests" / "mockbin"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip(s):
    return ANSI.sub("", s)


class Listener(threading.Thread):
    """Accepts connections on ip:0 and sends an SSH banner (or junk)."""

    def __init__(self, ip, banner=b"SSH-2.0-mock\r\n"):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((ip, 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.banner = banner
        self.start()

    def run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            try:
                c.sendall(self.banner)
            except OSError:
                pass
            c.close()


def peer(name, ip, online=True, uid=1, os_="linux", cur="", relay="", tags=None, seen="2026-09-24T10:00:00.123456789Z", active=None):
    d = {"HostName": name.upper(), "DNSName": f"{name}.tail1234.ts.net.", "TailscaleIPs": [ip],
         "OS": os_, "UserID": uid, "CurAddr": cur, "Relay": relay, "LastSeen": seen}
    if online is not None:
        d["Online"] = online
    if active is not None:
        d["Active"] = active
    if tags:
        d["Tags"] = tags
    return d


class Env:
    """A throwaway tailnet: mock CLI, listeners, config dir."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.pixel = Listener("127.0.0.2")            # ssh on an odd port (like Termux 8022)
        self.mint = Listener("127.0.0.3")             # ssh on another port
        self.web = Listener("127.0.0.5", b"HTTP/1.1 200 OK\r\n")  # something else, not ssh
        self.status = {
            "BackendState": "Running",
            "CurrentTailnet": {"Name": "franc@example.com"},
            "Self": {"HostName": "arch", "DNSName": "arch.tail1234.ts.net.", "TailscaleIPs": ["127.0.0.1"], "UserID": 1, "OS": "linux", "Online": True},
            "Peer": {
                "k1": peer("pixel-7", "127.0.0.2", os_="android", cur="192.168.1.9:41641"),
                "k2": peer("mint-desktop", "127.0.0.3", relay="nbo"),
                "k3": peer("nas", "127.0.0.4"),                       # online, nothing listening
                "k4": peer("old-laptop", "127.0.0.6", online=False, os_="windows", seen="2026-09-20T08:00:00Z"),
                "k5": peer("web-box", "127.0.0.5"),                   # online, port answers but not SSH
                "k6": peer("friend-pc", "127.0.0.7", uid=99),         # shared in from someone else
                "k7": peer("build-srv", "127.0.0.8", uid=77, tags=["tag:server"], online=None, active=True),
            },
        }
        self.json_path = t / "status.json"
        self.write_status()
        self.conf = t / "conf"
        self.conf.mkdir()
        ports = sorted({self.pixel.port, self.mint.port, self.web.port})
        (self.conf / "config.json").write_text(json.dumps({"probe_ports": ports, "configured": True}))
        self.ssh_out = t / "ssh_args.txt"

    def write_status(self):
        self.json_path.write_text(json.dumps(self.status))

    def env(self, **extra):
        e = dict(os.environ)
        e.update(PATH=f"{MOCKBIN}:{e['PATH']}", MOCK_TS_JSON=str(self.json_path), MOCK_SSH_OUT=str(self.ssh_out),
                 TSSH_CONFIG_DIR=str(self.conf), HOME=self.tmp.name, TERM="xterm-256color", NO_COLOR="1")
        e.update(extra)
        return e

    def run(self, *args, **kw):
        return subprocess.run([sys.executable, str(ROOT / "tailscale_ssh.py"), *args], capture_output=True, text=True,
                              env=self.env(), timeout=30, **kw)


class TestParsing(unittest.TestCase):
    def test_parse_ts(self):
        self.assertIsNotNone(ts.parse_ts("2026-09-24T10:00:00.123456789Z"))   # Go nanosecond precision
        self.assertIsNotNone(ts.parse_ts("2026-09-24T10:00:00+03:00"))
        self.assertIsNotNone(ts.parse_ts("2026-09-24T10:00:00.5Z"))
        self.assertIsNone(ts.parse_ts("0001-01-01T00:00:00Z"))               # Tailscale's "never"
        self.assertIsNone(ts.parse_ts(""))
        self.assertIsNone(ts.parse_ts("garbage"))

    def test_ago(self):
        now = time.time()
        self.assertEqual(ts.ago(now - 5), "just now")
        self.assertEqual(ts.ago(now - 300), "5m ago")
        self.assertEqual(ts.ago(now - 7200), "2h ago")
        self.assertEqual(ts.ago(now - 3 * 86400), "3d ago")
        self.assertEqual(ts.ago(None), "never")

    def test_parse_cli(self):
        e = Env()
        nodes, meta = ts.parse_cli(e.status)
        names = [n.name for n in nodes]
        self.assertNotIn("friend-pc", names)                    # someone else's shared device is hidden
        self.assertIn("build-srv", names)                       # tagged device belongs to the tailnet
        self.assertEqual(meta["tailnet"], "franc@example.com")
        self.assertEqual(meta["self"].name, "arch")
        by = {n.name: n for n in nodes}
        self.assertEqual(by["pixel-7"].link, "direct")
        self.assertEqual(by["mint-desktop"].link, "relay nbo")
        self.assertTrue(by["build-srv"].online)                 # Online missing -> falls back to Active
        self.assertFalse(by["old-laptop"].online)
        self.assertEqual(by["pixel-7"].name, "pixel-7")         # DNS label, not the raw HostName
        allnodes, _ = ts.parse_cli(e.status, show_all=True)
        self.assertIn("friend-pc", [n.name for n in allnodes])

    def test_states(self):
        n = ts.Node("a", ips=["100.1.1.1"], online=True)
        self.assertEqual(n.state, "checking")
        n.probed = True
        self.assertEqual(n.state, "nossh")
        n.ssh_port = 22
        self.assertEqual(n.state, "ready")
        n.online = False
        self.assertEqual(n.state, "offline")

    def test_ip_prefers_v4(self):
        self.assertEqual(ts.Node("a", ips=["fd7a::1", "100.9.9.9"]).ip, "100.9.9.9")


class TestProbe(unittest.TestCase):
    def test_probe(self):
        ssh, web = Listener("127.0.0.2"), Listener("127.0.0.3", b"HTTP/1.1 200\r\n")
        r = ts.probe_ssh("127.0.0.2", [web.port, ssh.port])
        self.assertEqual(r[0], ssh.port)
        self.assertGreaterEqual(r[1], 0)
        self.assertIsNone(ts.probe_ssh("127.0.0.3", [web.port]))              # answers, but not SSH
        self.assertIsNone(ts.probe_ssh("127.0.0.4", [ssh.port]))              # nothing there
        self.assertIsNone(ts.probe_ssh("", [22]))


class TestApiBackend(unittest.TestCase):
    """The Termux path: no CLI, device list from the Tailscale HTTP API."""

    @classmethod
    def setUpClass(cls):
        cls.calls = []
        devices = {"devices": [
            {"name": "pixel-7.tail1234.ts.net", "hostname": "Pixel 7", "addresses": ["100.64.0.10", "fd7a::10"], "os": "android", "user": "franc@example.com", "connectedToControl": True, "lastSeen": "2026-09-24T10:00:00Z"},
            {"name": "arch.tail1234.ts.net", "hostname": "arch", "addresses": ["100.64.0.11"], "os": "linux", "user": "franc@example.com", "connectedToControl": True, "lastSeen": "2026-09-24T10:00:00Z"},
            {"name": "nas.tail1234.ts.net", "hostname": "nas", "addresses": ["100.64.0.12"], "os": "linux", "user": "franc@example.com", "connectedToControl": False, "lastSeen": "2026-09-01T10:00:00Z"},
            {"name": "other.tail1234.ts.net", "hostname": "other", "addresses": ["100.64.0.13"], "os": "linux", "user": "someone@else.com", "connectedToControl": True},
        ]}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(h):
                cls.calls.append((h.path, h.headers.get("Authorization")))
                if h.headers.get("Authorization") != "Bearer tskey-api-good":
                    h.send_response(401); h.end_headers(); return
                body = json.dumps(devices).encode()
                h.send_response(200); h.send_header("Content-Type", "application/json"); h.end_headers(); h.wfile.write(body)

            def log_message(h, *a):
                pass

        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_api_flow(self):
        old = ts.API_BASE
        ts.API_BASE = self.base
        os.environ["TSSH_LOCAL_IP"] = "100.64.0.10"
        try:
            nodes, meta = ts.fetch_api("tskey-api-good", False)
            self.assertEqual(sorted(n.name for n in nodes), ["arch", "nas"])   # self and other-user devices excluded
            self.assertEqual(meta["self"].name, "pixel-7")
            self.assertEqual(meta["tailnet"], "tail1234.ts.net")
            by = {n.name: n for n in nodes}
            self.assertTrue(by["arch"].online)
            self.assertFalse(by["nas"].online)
            self.assertIn("/api/v2/tailnet/-/devices", self.calls[-1][0])
            with self.assertRaises(ts.TSError) as cm:
                ts.fetch_api("tskey-api-bad", False)
            self.assertIn("rejected", str(cm.exception))
            del os.environ["TSSH_LOCAL_IP"]
            os.environ["TSSH_LOCAL_IP"] = ""
            os.environ.pop("TSSH_LOCAL_IP")
        finally:
            ts.API_BASE = old
            os.environ.pop("TSSH_LOCAL_IP", None)


class TestCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Env()

    def test_list_json(self):
        r = self.e.run("list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        by = {x["name"]: x for x in d["devices"]}
        self.assertEqual(by["pixel-7"]["state"], "ready")
        self.assertEqual(by["pixel-7"]["ssh_port"], self.e.pixel.port)
        self.assertEqual(by["mint-desktop"]["state"], "ready")
        self.assertEqual(by["mint-desktop"]["ssh_port"], self.e.mint.port)
        self.assertEqual(by["nas"]["state"], "nossh")
        self.assertEqual(by["web-box"]["state"], "nossh")          # port open but not SSH
        self.assertEqual(by["old-laptop"]["state"], "offline")
        self.assertNotIn("friend-pc", by)
        self.assertEqual(by["mint-desktop"]["path"], "relay nbo")

    def test_list_table(self):
        r = self.e.run("list", "--plain")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = strip(r.stdout)
        self.assertIn("pixel-7", out)
        self.assertRegex(out, rf"ready :{self.e.pixel.port}")
        self.assertIn("online · no ssh", out)
        self.assertRegex(out, r"offline · \d+d ago")
        self.assertIn("this device: arch", out)

    def test_not_logged_in(self):
        st = dict(self.e.status, BackendState="NeedsLogin")
        p = Path(self.e.tmp.name) / "s2.json"
        p.write_text(json.dumps(st))
        r = subprocess.run([sys.executable, str(ROOT / "tailscale_ssh.py"), "list", "--json"], capture_output=True, text=True,
                           env=self.e.env(MOCK_TS_JSON=str(p)), timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("isn't signed in", json.loads(r.stdout)["error"])

    def test_ip_change_is_picked_up(self):
        """No IPs are stored: change a device's address and the next run uses the new one."""
        e = Env()
        new = Listener("127.0.0.9")
        e.status["Peer"]["k1"]["TailscaleIPs"] = ["127.0.0.9"]
        e.write_status()
        (e.conf / "config.json").write_text(json.dumps({"probe_ports": [new.port], "configured": True}))
        r = e.run("list", "--json")
        by = {x["name"]: x for x in json.loads(r.stdout)["devices"]}
        self.assertEqual(by["pixel-7"]["ip"], "127.0.0.9")
        self.assertEqual(by["pixel-7"]["state"], "ready")

    def test_direct_connect(self):
        e = Env()
        r = e.run("mint", "-u", "franc", "--", "tmux", "attach")
        self.assertEqual(r.returncode, 0, r.stderr)
        args = e.ssh_out.read_text().split("\n")
        self.assertEqual(args[args.index("-p") + 1], str(e.mint.port))
        self.assertIn("HostKeyAlias=ts-mint-desktop", args)
        self.assertIn("StrictHostKeyChecking=accept-new", args)
        self.assertIn("-t", args)
        self.assertIn("franc@127.0.0.3", args)
        self.assertEqual(args[-2:], ["tmux", "attach"])
        saved = json.loads((e.conf / "devices.json").read_text())
        self.assertEqual(saved["mint-desktop"]["user"], "franc")     # remembered for next time
        r = e.run("mint")                                            # second time: no prompt needed
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("franc@127.0.0.3", e.ssh_out.read_text())

    def test_user_at_host_and_prefix(self):
        e = Env()
        r = e.run("u0_a327@pix")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("u0_a327@127.0.0.2", e.ssh_out.read_text())

    def test_ambiguous_and_missing(self):
        r = self.e.run("zzz")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No device matches", r.stderr)
        e = Env()
        e.status["Peer"]["k8"] = peer("mint-laptop", "127.0.0.10")
        e.write_status()
        r = e.run("mint")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ambiguous", r.stderr)

    def test_offline_refuses_without_tty(self):
        r = self.e.run("old-laptop", "-u", "x")
        self.assertEqual(r.returncode, 1)                            # "Try anyway?" defaults to No
        self.assertIn("offline", r.stdout)

    def test_forget(self):
        e = Env()
        e.run("mint", "-u", "franc")
        self.assertEqual(e.run("forget", "mint-desktop").returncode, 0)
        self.assertNotIn("mint-desktop", json.loads((e.conf / "devices.json").read_text()))

    def test_version_help_config(self):
        self.assertIn(ts.__version__, self.e.run("--version").stdout)
        self.assertIn("SSH into any device", self.e.run("--help").stdout)
        out = self.e.run("config").stdout
        self.assertIn("probe_ports", out)


class TestRender(unittest.TestCase):
    def nodes(self):
        e = Env()
        nodes, meta = ts.parse_cli(e.status)
        for n in nodes:
            n.probed = True
            if n.name == "pixel-7":
                n.ssh_port, n.latency = 8022, 12.0
            if n.name == "mint-desktop":
                n.ssh_port, n.latency = 22, 180.0
        return nodes, meta

    def test_fits_every_width(self):
        nodes, meta = self.nodes()
        ts.S.enabled, ts.S.unicode = True, True
        try:
            for width in (30, 38, 45, 60, 80, 120):
                for interactive in (True, False):
                    lines = ts.render(nodes, meta, None, True, "pixel-7", interactive, width, "sshph", tick=3)
                    for l in lines:
                        self.assertLessEqual(len(strip(l)), width - 1, f"width {width}: {strip(l)!r}")
        finally:
            ts.S.enabled = False

    def test_wide_has_all_columns_narrow_drops_some(self):
        nodes, meta = self.nodes()
        ts.S.enabled, ts.S.unicode = False, True
        wide = "\n".join(ts.render(nodes, meta, None, False, None, False, 120, "sshph"))
        narrow = "\n".join(ts.render(nodes, meta, None, False, None, False, 44, "sshph"))
        for col in ("ADDRESS", "PATH", "LATENCY", "OS", "STATUS"):
            self.assertIn(col, wide)
        self.assertNotIn("ADDRESS", narrow)
        self.assertIn("STATUS", narrow)

    def test_ascii_mode_has_no_unicode(self):
        nodes, meta = self.nodes()
        ts.S.enabled, ts.S.unicode = False, False
        try:
            out = "\n".join(ts.render(nodes, meta, None, False, "pixel-7", True, 100, "sshph"))
            out.encode("ascii")
        finally:
            ts.S.unicode = True

    def test_error_and_empty(self):
        ts.S.enabled = False
        err = ts.TSError("Tailscale isn't signed in", "Run: sshph setup")
        self.assertIn("Run: sshph setup", "\n".join(ts.render([], {}, err, False, None, False, 80, "sshph")))
        self.assertIn("No other devices", "\n".join(ts.render([], {}, None, False, None, False, 80, "sshph")))


class TestPicker(unittest.TestCase):
    """Drive the real interactive UI through a pseudo-terminal."""

    def drive(self, e, keys, wait_for=b"ready", cols=100, rows=30):
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(e.env())
            os.environ.pop("NO_COLOR", None)
            os.execv(sys.executable, [sys.executable, str(ROOT / "tailscale_ssh.py")])
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        buf = b""
        deadline = time.time() + 15

        def pump(t=0.2):
            nonlocal buf
            r, _, _ = select.select([fd], [], [], t)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    return False
            return True

        while wait_for not in buf and time.time() < deadline:
            pump()
        time.sleep(0.6)
        pump(0.2)
        screen = buf
        for k in keys:
            os.write(fd, k)
            time.sleep(0.25)
            pump(0.2)
        end = time.time() + 8
        while time.time() < end:
            if not pump(0.2):
                break
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                break
        try:
            os.kill(pid, 9)        # never let a stuck UI hang the suite
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        os.close(fd)
        return screen, buf

    def test_arrow_select_and_connect(self):
        e = Env()
        e.env()
        Path(e.conf / "devices.json").write_text(json.dumps({
            "mint-desktop": {"user": "franc", "port": 1, "last_used": 1},
            "pixel-7": {"user": "u0_a327", "port": 1, "last_used": 0},
        }))
        # sorted order (online first, most-recently-used first, then name): mint-desktop, build-srv,
        # nas, pixel-7, web-box, old-laptop(offline) -> 3 ArrowDowns from mint-desktop lands on pixel-7
        screen, full = self.drive(e, [b"\x1b[B", b"\x1b[B", b"\x1b[B", b"\r"])
        text = strip(screen.decode(errors="ignore"))
        self.assertIn("pixel-7", text)
        self.assertIn("ready", text)
        self.assertIn("\x1b[?1049h".encode(), full)      # used the alternate screen ...
        self.assertIn("\x1b[?1049l".encode(), full)      # ... and restored the terminal
        self.assertTrue(e.ssh_out.exists(), "ssh was not launched")
        args = e.ssh_out.read_text().split("\n")
        self.assertTrue(any(a.endswith("@127.0.0.2") for a in args), args)

    def test_number_key_and_quit(self):
        e = Env()
        Path(e.conf / "devices.json").write_text(json.dumps({"pixel-7": {"user": "u0_a1", "port": 1, "last_used": 5}}))
        _, _ = self.drive(e, [b"1"])
        self.assertIn("u0_a1@127.0.0.2", e.ssh_out.read_text())
        e2 = Env()
        _, full = self.drive(e2, [b"q"])
        self.assertFalse(e2.ssh_out.exists())
        self.assertIn("\x1b[?25h".encode(), full)         # cursor restored


if __name__ == "__main__":
    unittest.main()
