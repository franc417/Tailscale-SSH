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
import shutil
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


class TestTermuxCliGuard(unittest.TestCase):
    """Regression test for a real bug: `pkg install tailscale` in Termux creates a separate,
    never-signed-in tailscale instance with no relation to the Android Tailscale app. Finding
    that binary on PATH must never be treated as this device's real tailnet status."""

    def _termux_env(self, path_extra=None):
        saved = {k: os.environ.get(k) for k in ("PATH", "PREFIX", "TERMUX_VERSION")}
        os.environ["PATH"] = f"{path_extra}:{saved['PATH']}" if path_extra else saved["PATH"]
        os.environ["PREFIX"] = "/data/data/com.termux/files/usr"
        os.environ["TERMUX_VERSION"] = "0.118"
        return saved

    def _restore_env(self, saved):
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_stray_termux_binary_is_ignored(self):
        saved = self._termux_env(path_extra=str(MOCKBIN))  # mockbin/tailscale is right there on PATH
        try:
            self.assertIsNotNone(shutil.which("tailscale"))   # sanity: it really is findable
            self.assertIsNone(ts.ts_cli())                    # ...but ts_cli() must refuse it on Termux
        finally:
            self._restore_env(saved)

    def test_non_termux_still_finds_it(self):
        saved = {k: os.environ.get(k) for k in ("PATH", "PREFIX", "TERMUX_VERSION")}
        os.environ["PATH"] = f"{MOCKBIN}:{saved['PATH']}"
        os.environ.pop("PREFIX", None)
        os.environ.pop("TERMUX_VERSION", None)
        try:
            self.assertIsNotNone(ts.ts_cli())
        finally:
            self._restore_env(saved)

    def test_mask_secret(self):
        s = "tskey-api-abcdefghij-0123456789"
        m = ts.mask_secret(s)
        self.assertEqual(len(m), len(s))
        self.assertTrue(m.startswith("tske") and m.endswith("6789"))
        self.assertNotIn(s[5:-4], m)                       # the middle is actually hidden
        self.assertEqual(len(ts.mask_secret("short")), 5)  # too short to partially reveal -> fully masked


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

    def test_fetch_nodes_falls_back_to_api_when_cli_is_broken(self):
        """Defense in depth beyond the Termux-specific fix: if a `tailscale` binary exists but
        can't actually produce a status (crashed daemon, broken install, etc.) and an API key
        is configured, fetch_nodes should still return a usable list via the API instead of
        hard-failing with the CLI's error."""
        old_api_base, old_ts_cli = ts.API_BASE, ts.ts_cli
        ts.API_BASE = self.base
        ts.ts_cli = lambda: "/nonexistent/tailscale"  # "found" on PATH, but running it will fail
        os.environ["TSSH_LOCAL_IP"] = "100.64.0.10"
        try:
            with self.assertRaises(ts.TSError):
                ts.fetch_cli(False)  # confirm the CLI path really is broken on its own
            cfg = {"accounts": {"default": {"api_key": "tskey-api-good"}}, "active_account": "default"}
            nodes, meta = ts.fetch_nodes(cfg, False)
            self.assertEqual(meta["backend"], "api")
            self.assertEqual(sorted(n.name for n in nodes), ["arch", "nas"])
        finally:
            ts.API_BASE, ts.ts_cli = old_api_base, old_ts_cli
            os.environ.pop("TSSH_LOCAL_IP", None)

    def _connect_subprocess_env(self, extra=None):
        t = Path(tempfile.mkdtemp())
        (t / "conf").mkdir()
        (t / "conf" / "config.json").write_text(json.dumps({"api_key": "tskey-api-good", "probe_ports": [22]}))
        env = dict(os.environ, HOME=str(t), TSSH_CONFIG_DIR=str(t / "conf"),
                   PATH=f"{MOCKBIN}:{os.environ['PATH']}", MOCK_SSH_OUT=str(t / "ssh_args.txt"),
                   TSSH_API_BASE=self.base, TSSH_LOCAL_IP="100.64.0.10", NO_COLOR="1")
        env.update(extra or {})
        return t, env

    def test_no_username_default_from_termux_to_non_android(self):
        """Regression: calling from Termux, our own username (u0_a123-style) must never be
        offered as a default for a non-Android target ('arch' here) -- it's meaningless there.
        With no default and no stdin to answer the prompt, this must fail loudly, not silently
        guess a wrong username."""
        t, env = self._connect_subprocess_env({"TERMUX_VERSION": "0.118",
                                                "PREFIX": "/data/data/com.termux/files/usr"})
        r = subprocess.run([sys.executable, str(ROOT / "tailscale_ssh.py"), "arch"],
                           capture_output=True, text=True, env=env, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("username is required", (r.stdout + r.stderr).lower())

    def test_username_default_still_offered_outside_termux(self):
        """Same scenario without Termux in the picture: defaulting to the local username is a
        reasonable convenience between two normal machines, so it must still work."""
        t, env = self._connect_subprocess_env()
        r = subprocess.run([sys.executable, str(ROOT / "tailscale_ssh.py"), "arch"],
                           capture_output=True, text=True, env=env, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        args = (t / "ssh_args.txt").read_text()
        self.assertIn("@100.64.0.11", args)


@unittest.skipUnless(shutil.which("sshd") and shutil.which("ssh-keygen"), "no local sshd available")
class TestInstallPubkey(unittest.TestCase):
    """Regression test for a real bug: Termux's ssh-copy-id has a long-standing scratch-dir
    bug that hangs/errors instead of installing the key. install_pubkey() replaces it with a
    plain ssh round-trip, verified here against a real local sshd rather than a mock -- a fake
    'ssh' can't tell us whether the remote shell pipeline (the actual bug-prone part) works."""

    TESTUSER = "tsshtest"

    @classmethod
    def setUpClass(cls):
        Path("/run/sshd").mkdir(parents=True, exist_ok=True)  # sshd's privsep dir; not always pre-created
        subprocess.run(["userdel", "-r", cls.TESTUSER], capture_output=True)  # in case a prior run left it
        r = subprocess.run(["useradd", "-m", "-s", "/bin/bash", cls.TESTUSER], capture_output=True, text=True)
        cls.have_user = r.returncode == 0
        if not cls.have_user:
            return  # environment can't create local users (e.g. no root) -- test will skip itself
        cls.home = Path(f"/home/{cls.TESTUSER}")
        # useradd -m leaves the account password-locked, and sshd refuses ANY login (pubkey
        # included) for a locked account regardless of PasswordAuthentication -- give it a
        # throwaway password just to unlock it; it's never actually usable for login since
        # password auth stays off in sshd_config below.
        subprocess.run(["chpasswd"], input=f"{cls.TESTUSER}:not-used-{os.urandom(8).hex()}\n",
                       text=True, check=True)

        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        hostkey = t / "hostkey"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(hostkey)], check=True)
        cls.bootstrap = t / "bootstrap"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(cls.bootstrap)], check=True)
        # Set up ~testuser/.ssh as that user, via su, so ownership matches what sshd/StrictModes expect --
        # not just what a root-owned mkdir would produce -- and so the real login shell's own ~ (which
        # the remote script in install_pubkey() relies on) is exactly what we're inspecting afterward.
        subprocess.run(["su", cls.TESTUSER, "-c",
                        "mkdir -m 700 -p ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"],
                       check=True)
        (cls.home / ".ssh" / "authorized_keys").write_text(cls.bootstrap.with_suffix(".pub").read_text())

        cls.sock = socket.socket()
        cls.sock.bind(("127.0.0.1", 0))
        cls.port = cls.sock.getsockname()[1]
        cls.sock.close()  # just claiming a free port; sshd binds it next

        cfg = t / "sshd_config"
        cfg.write_text(
            f"Port {cls.port}\nListenAddress 127.0.0.1\nHostKey {hostkey}\n"
            f"PubkeyAuthentication yes\nPasswordAuthentication no\nUsePAM no\n"
            f"PidFile {t}/sshd.pid\nLogLevel ERROR\n"
        )
        cls.proc = subprocess.Popen(["/usr/sbin/sshd", "-f", str(cfg), "-D", "-e"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 10
        cls.up = False
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.5).close()
                cls.up = True
                break
            except OSError:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        if not cls.have_user:
            return
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.tmp.cleanup()
        subprocess.run(["userdel", "-r", cls.TESTUSER], capture_output=True)

    def _base_args(self):
        return ["-p", str(self.port), "-i", str(self.bootstrap), "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes"]

    def test_installs_key_and_is_idempotent(self):
        if not self.have_user:
            self.skipTest("can't create a local test user in this environment")
        self.assertTrue(self.up, "local sshd never came up")
        new_key = Path(self.tmp.name) / "new_key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(new_key)], check=True)
        pub = new_key.with_suffix(".pub")

        ok1 = ts.install_pubkey(pub, self._base_args(), self.TESTUSER, "127.0.0.1")
        self.assertTrue(ok1)
        auth_text = (self.home / ".ssh" / "authorized_keys").read_text()
        self.assertEqual(auth_text.count(pub.read_text().strip()), 1)
        self.assertIn(self.bootstrap.with_suffix(".pub").read_text().strip(), auth_text)  # untouched

        # the newly-installed key must itself now be able to log in, unassisted
        r = subprocess.run(["ssh", "-p", str(self.port), "-i", str(new_key), "-o", "StrictHostKeyChecking=no",
                            "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes",
                            f"{self.TESTUSER}@127.0.0.1", "echo", "logged-in-with-new-key"],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("logged-in-with-new-key", r.stdout)

        ok2 = ts.install_pubkey(pub, self._base_args(), self.TESTUSER, "127.0.0.1")  # again: no duplicate
        self.assertTrue(ok2)
        auth_text2 = (self.home / ".ssh" / "authorized_keys").read_text()
        self.assertEqual(auth_text2.count(pub.read_text().strip()), 1)


@unittest.skipUnless(shutil.which("sshd") and shutil.which("ssh-keygen"), "no local sshd available")
class TestGui(unittest.TestCase):
    """mesh gui's remote-orchestration plumbing (check_sunshine, remote_platform), tested
    against a real local sshd and a real throwaway account -- same reasoning as
    TestInstallPubkey: a fake `ssh` can't tell us whether the actual remote shell pipeline
    (the part with real bugs) works. What this class can't test -- Sunshine's actual screen
    capture, or Moonlight's actual rendering -- isn't ours to test; that's mesh's own
    orchestration of them, which is what's covered here."""

    TESTUSER = "tsshtest2"

    @classmethod
    def setUpClass(cls):
        Path("/run/sshd").mkdir(parents=True, exist_ok=True)
        subprocess.run(["pkill", "-KILL", "-u", cls.TESTUSER], capture_output=True)
        subprocess.run(["userdel", "-r", cls.TESTUSER], capture_output=True)
        r = subprocess.run(["useradd", "-m", "-s", "/bin/bash", cls.TESTUSER], capture_output=True, text=True)
        cls.have_user = r.returncode == 0
        if not cls.have_user:
            return
        cls.home = Path(f"/home/{cls.TESTUSER}")
        subprocess.run(["chpasswd"], input=f"{cls.TESTUSER}:not-used-{os.urandom(8).hex()}\n",
                       text=True, check=True)  # unlock the account; see TestInstallPubkey for why

        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        hostkey = t / "hostkey"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(hostkey)], check=True)
        cls.key = t / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(cls.key)], check=True)
        subprocess.run(["su", cls.TESTUSER, "-c",
                        "mkdir -m 700 -p ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"],
                       check=True)
        (cls.home / ".ssh" / "authorized_keys").write_text(cls.key.with_suffix(".pub").read_text())

        cls.sock = socket.socket()
        cls.sock.bind(("127.0.0.1", 0))
        cls.port = cls.sock.getsockname()[1]
        cls.sock.close()
        cfg = t / "sshd_config"
        cfg.write_text(f"Port {cls.port}\nListenAddress 127.0.0.1\nHostKey {hostkey}\n"
                       f"PubkeyAuthentication yes\nPasswordAuthentication no\nUsePAM no\n"
                       f"PidFile {t}/sshd.pid\nLogLevel ERROR\n")
        cls.proc = subprocess.Popen(["/usr/sbin/sshd", "-f", str(cfg), "-D", "-e"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 10
        cls.up = False
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.5).close()
                cls.up = True
                break
            except OSError:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        if not cls.have_user:
            return
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.tmp.cleanup()
        subprocess.run(["rm", "-f", "/usr/local/bin/sunshine"])
        # the fake sunshine spawns a `sleep` that outlives `pkill -x sunshine`; userdel refuses a
        # user that still owns a process, which would leave the account behind and make the next
        # run's useradd fail (and these tests silently skip)
        subprocess.run(["pkill", "-KILL", "-u", cls.TESTUSER], capture_output=True)
        time.sleep(0.3)
        subprocess.run(["userdel", "-r", cls.TESTUSER], capture_output=True)

    def _base(self):
        return ["-p", str(self.port), "-i", str(self.key), "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes"]

    def test_check_sunshine_transitions_through_real_ssh(self):
        if not self.have_user:
            self.skipTest("can't create a local test user in this environment")
        self.assertTrue(self.up, "local sshd never came up")
        base = self._base()

        installed, running, err = ts.check_sunshine(base, self.TESTUSER, "127.0.0.1")
        self.assertEqual((installed, running), (False, False))
        self.assertIsNone(err)

        sunshine_bin = Path("/usr/local/bin/sunshine")
        sunshine_bin.write_text("#!/bin/bash\nsleep 30\n")
        sunshine_bin.chmod(0o755)
        try:
            installed, running, err = ts.check_sunshine(base, self.TESTUSER, "127.0.0.1")
            self.assertEqual((installed, running), (True, False))
            self.assertIsNone(err)

            r = subprocess.run(["ssh", *base, f"{self.TESTUSER}@127.0.0.1",
                                "nohup sunshine >/dev/null 2>&1 & disown; sleep 0.3; echo started"],
                               capture_output=True, text=True, timeout=10)
            self.assertIn("started", r.stdout, r.stderr)

            installed, running, err = ts.check_sunshine(base, self.TESTUSER, "127.0.0.1")
            self.assertEqual((installed, running), (True, True))
            self.assertIsNone(err)
        finally:
            subprocess.run(["ssh", *base, f"{self.TESTUSER}@127.0.0.1", "pkill", "-x", "sunshine"],
                           capture_output=True, timeout=10)
            sunshine_bin.unlink(missing_ok=True)

    def test_check_sunshine_unreachable_host(self):
        installed, running, err = ts.check_sunshine(
            ["-p", "1", "-o", "BatchMode=yes", "-o", "ConnectTimeout=1"], "nobody", "127.0.0.1")
        self.assertIsNone(installed)
        self.assertIsNone(running)
        self.assertTrue(err)  # some non-empty reason, not swallowed into a bare (None, None)

    def test_remote_platform_real_ssh(self):
        if not self.have_user:
            self.skipTest("can't create a local test user in this environment")
        self.assertTrue(self.up, "local sshd never came up")
        # this sandbox really is Debian-based -- a genuine assertion, not a fake /etc/os-release
        self.assertEqual(ts.remote_platform(self._base(), self.TESTUSER, "127.0.0.1"), "debian")

    def test_arch_install_script_is_valid_bash(self):
        r = subprocess.run(["bash", "-n"], input=ts.ARCH_SUNSHINE_INSTALL, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lizardbyte/sunshine", ts.ARCH_SUNSHINE_INSTALL)
        self.assertIn("pacman-repo/releases/latest/download", ts.ARCH_SUNSHINE_INSTALL)

    def test_uinput_setup_script_is_valid_bash(self):
        r = subprocess.run(["bash", "-n"], input=ts.UINPUT_SETUP, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GROUP=\"input\"", ts.UINPUT_SETUP)
        self.assertIn("usermod -aG input", ts.UINPUT_SETUP)

    def test_uinput_group_ok_real_ssh(self):
        """A real check against a real account that is (and isn't) in a group named 'input' --
        not just parsing canned text."""
        if not self.have_user:
            self.skipTest("can't create a local test user in this environment")
        self.assertTrue(self.up, "local sshd never came up")
        base = self._base()
        self.assertFalse(ts.uinput_group_ok(base, self.TESTUSER, "127.0.0.1"))  # fresh user: not in it yet
        subprocess.run(["groupadd", "-f", "input"], check=True)
        subprocess.run(["usermod", "-aG", "input", self.TESTUSER], check=True)
        try:
            self.assertTrue(ts.uinput_group_ok(base, self.TESTUSER, "127.0.0.1"))
        finally:
            subprocess.run(["gpasswd", "-d", self.TESTUSER, "input"], capture_output=True)


class TestGuiFlow(unittest.TestCase):
    """cmd_gui's decision logic -- which remote commands run, in what order, and when it gives
    up -- with the plumbing (ssh, device lookup) patched out. The plumbing itself is covered
    for real in TestGui; this is about the branching."""

    def _run_gui(self, checks, platform="arch", call_rcs=(0, 0), termux=False,
                confirm_yes=True, key_install_ok=True, uinput_ok=True):
        """checks: successive check_sunshine results, as (installed, running) or the full
        (installed, running, error). Returns (exit_code, ssh_-t_calls, handoff_ips, key_installs).
        """
        import types
        node = ts.Node("arch", ips=["100.64.0.11"], online=True, os="linux")
        ns = types.SimpleNamespace(words=["gui", "arch"], all=False)

        def repeating_last(items):  # state persists in reality: once it stops changing, it stays that way
            items = [x if len(x) == 3 else (*x, None) for x in items]
            for x in items:
                yield x
            while True:
                yield items[-1]

        check_iter = repeating_last(checks)
        ssh_calls, handoffs, key_installs = [], [], []
        rc_iter = iter(call_rcs)

        def fake_call(cmd, *a, **kw):
            ssh_calls.append(cmd)
            return next(rc_iter, 0)

        saved = {n: getattr(ts, n) for n in ("fetch_nodes", "resolve_connection", "check_sunshine",
                                            "remote_platform", "termux_handoff", "detect_platform",
                                            "confirm", "ensure_key", "install_pubkey", "uinput_group_ok")}
        saved_call, saved_sleep = ts.subprocess.call, ts.time.sleep
        ts.time.sleep = lambda s: None  # the post-start poll shouldn't make the suite wait for real
        ts.fetch_nodes = lambda cfg, show_all: ([node], {})
        ts.resolve_connection = lambda n, ns_, cfg, devices: ("franc", 22, ["-p", "22"])
        ts.check_sunshine = lambda base, user, ip: next(check_iter)
        ts.remote_platform = lambda base, user, ip: platform
        ts.termux_handoff = lambda ip: handoffs.append(ip)
        ts.detect_platform = lambda: "termux" if termux else "arch"
        ts.confirm = lambda *a, **kw: confirm_yes
        ts.ensure_key = lambda: Path("/fake/id_ed25519")
        ts.install_pubkey = lambda pub, base, user, ip: key_installs.append(ip) or key_install_ok
        ts.uinput_group_ok = lambda base, user, ip: uinput_ok
        ts.subprocess.call = fake_call
        try:
            try:
                rc = ts.cmd_gui(ns, {}, {}, "mesh")
            except SystemExit as e:  # die() -- same exit code a real subprocess run would show
                rc = e.code
        finally:
            for n, v in saved.items():
                setattr(ts, n, v)
            ts.subprocess.call, ts.time.sleep = saved_call, saved_sleep
        return rc, ssh_calls, handoffs, key_installs

    def test_already_running_touches_nothing(self):
        rc, calls, handoffs, _kinst = self._run_gui([(True, True)], termux=True)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])                 # no install, no start
        self.assertEqual(handoffs, ["100.64.0.11"])  # straight to the Moonlight handoff

    def test_uinput_already_set_up_is_silent(self):
        rc, calls, handoffs, _ = self._run_gui([(True, True)], termux=True, uinput_ok=True)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])  # no extra ssh -t call when nothing needs fixing

    def test_uinput_missing_runs_setup_and_warns(self):
        rc, calls, handoffs, _ = self._run_gui([(True, True)], termux=True, uinput_ok=False)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["ssh", "-t"])
        self.assertEqual(calls[0][-1], ts.UINPUT_SETUP)
        self.assertEqual(handoffs, ["100.64.0.11"])  # still finishes -- this is a warning, not a failure

    def test_uinput_check_unreachable_does_not_block_success(self):
        """Can't confirm uinput access (None) is advisory -- must not fail the whole run."""
        rc, calls, handoffs, _ = self._run_gui([(True, True)], termux=True, uinput_ok=None)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertEqual(handoffs, ["100.64.0.11"])

    def test_permission_denied_offers_key_install_and_recovers(self):
        """The exact bug from the field: a device mesh has used before but never actually got
        a key onto (install declined/failed the first time) must not be a permanent dead end."""
        rc, calls, handoffs, kinst = self._run_gui(
            [(None, None, "Permission denied (publickey)."), (True, True)], termux=True)
        self.assertEqual(rc, 0)
        self.assertEqual(kinst, ["100.64.0.11"])      # install_pubkey was actually invoked
        self.assertEqual(handoffs, ["100.64.0.11"])   # ...and gui carried on to finish normally

    def test_permission_denied_declined_fails_with_the_real_reason(self):
        rc, calls, handoffs, kinst = self._run_gui(
            [(None, None, "Permission denied (publickey).")], confirm_yes=False)
        self.assertEqual(rc, 1)
        self.assertEqual(kinst, [])                   # declined -- never even tried
        self.assertEqual(handoffs, [])

    def test_unreachable_host_does_not_try_to_install_a_key(self):
        """A non-auth failure (host down, wrong port...) must not trigger the key-install
        flow -- that's specific to 'permission denied', not every kind of failure."""
        rc, calls, handoffs, kinst = self._run_gui(
            [(None, None, "ssh: connect to host 100.64.0.11 port 22: Connection timed out")])
        self.assertEqual(rc, 1)
        self.assertEqual(kinst, [])

    def test_not_installed_on_non_arch_stops_without_installing(self):
        rc, calls, handoffs, _kinst = self._run_gui([(False, False)], platform="debian")
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])
        self.assertEqual(handoffs, [])

    def test_arch_installs_then_starts_then_hands_off(self):
        # check #1: not installed. after install: installed, not running. after start: running.
        rc, calls, handoffs, _kinst = self._run_gui([(False, False), (True, False), (True, True)], termux=True)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:2], ["ssh", "-t"])       # -t: sudo may need to prompt on a real tty
        self.assertEqual(calls[0][-1], ts.ARCH_SUNSHINE_INSTALL)
        self.assertEqual(calls[1][-1], ts.SUNSHINE_START)
        self.assertEqual(handoffs, ["100.64.0.11"])

    def test_failed_install_does_not_go_on_to_start(self):
        rc, calls, handoffs, _kinst = self._run_gui([(False, False)], call_rcs=(1,))
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)             # only the install attempt
        self.assertEqual(handoffs, [])

    def test_install_that_exits_cleanly_but_leaves_nothing_is_not_blamed_on_the_display(self):
        rc, calls, handoffs, _kinst = self._run_gui([(False, False)])   # still not installed after the install ran
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)             # the install only -- never tries to start what isn't there
        self.assertEqual(handoffs, [])

    def test_service_that_takes_a_moment_to_appear_is_still_caught(self):
        # not running for the first two polls after start, then up -- must not be reported as a failure
        rc, calls, handoffs, _kinst = self._run_gui([(True, False), (True, False), (True, False), (True, True)],
                                            termux=True)
        self.assertEqual(rc, 0)
        self.assertEqual(handoffs, ["100.64.0.11"])

    def test_start_that_does_not_take_reports_display_hint_and_fails(self):
        rc, calls, handoffs, _kinst = self._run_gui([(True, False), (True, False)], termux=True)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], ts.SUNSHINE_START)
        self.assertEqual(handoffs, [])              # never claims success it doesn't have


class TestTermuxHandoff(unittest.TestCase):
    """termux_handoff()'s command construction, against mocked termux-api/pm/monkey binaries
    (these are Android-only tools that can't exist for real in this sandbox)."""

    def _env(self, t, have_moonlight):
        (t / "clip.txt").write_text("")
        (t / "monkey_calls.jsonl").write_text("")
        package = "com.limelight" if have_moonlight else "com.other"
        (t / "pm").write_text(f"#!/usr/bin/env python3\nprint('package:{package}')\n")
        (t / "pm").chmod(0o755)
        (t / "termux-clipboard-set").write_text(
            "#!/usr/bin/env python3\nimport sys\nopen(sys.argv[0].rsplit('/',1)[0]+'/clip.txt','w')"
            ".write(sys.stdin.read())\n")
        (t / "termux-clipboard-set").chmod(0o755)
        (t / "monkey").write_text(
            "#!/usr/bin/env python3\nimport json,sys,os\n"
            "d=os.path.dirname(sys.argv[0])\n"
            "open(d+'/monkey_calls.jsonl','a').write(json.dumps(sys.argv[1:])+'\\n')\n")
        (t / "monkey").chmod(0o755)
        return dict(os.environ, PATH=f"{t}:{os.environ['PATH']}")

    def test_moonlight_installed_copies_clip_and_launches(self):
        t = Path(tempfile.mkdtemp())
        env = self._env(t, have_moonlight=True)
        old_path = os.environ.get("PATH")
        os.environ["PATH"] = env["PATH"]
        try:
            ts.termux_handoff("100.64.0.11")
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual((t / "clip.txt").read_text(), "100.64.0.11")
        calls = [json.loads(l) for l in (t / "monkey_calls.jsonl").read_text().splitlines()]
        self.assertIn(["-p", "com.limelight", "-c", "android.intent.category.LAUNCHER", "1"], calls)

    def test_moonlight_missing_does_not_launch(self):
        t = Path(tempfile.mkdtemp())
        env = self._env(t, have_moonlight=False)
        old_path = os.environ.get("PATH")
        os.environ["PATH"] = env["PATH"]
        try:
            ts.termux_handoff("100.64.0.11")
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual((t / "clip.txt").read_text(), "100.64.0.11")  # still copies the address
        self.assertEqual((t / "monkey_calls.jsonl").read_text(), "")   # but never launches


class TestAccounts(unittest.TestCase):
    """Multiple Tailscale accounts on one device: real `tailscale switch` passthrough where a
    CLI exists, named API-key profiles (our own bookkeeping) where it doesn't (Termux)."""

    def test_active_api_key_resolution(self):
        self.assertIsNone(ts.active_api_key({"accounts": {}, "active_account": None}))
        cfg = {"accounts": {"work": {"api_key": "tskey-work"}, "home": {"api_key": "tskey-home"}},
               "active_account": "home"}
        self.assertEqual(ts.active_api_key(cfg), "tskey-home")
        # active_account points nowhere (stale/removed) -- falls back to whatever exists
        cfg["active_account"] = "gone"
        self.assertIn(ts.active_api_key(cfg), ("tskey-work", "tskey-home"))

    def test_legacy_config_migrates_on_load(self):
        """load_config() is monkeypatched to point at an isolated dir here (in-process); the
        subprocess-based tests elsewhere exercise the same migration via TSSH_CONFIG_DIR."""
        t = Path(tempfile.mkdtemp())
        (t / "config.json").write_text(json.dumps({"api_key": "tskey-old", "probe_ports": [22]}))
        old_conf_dir = ts.CONF_DIR, ts.CONF_FILE, ts.DEV_FILE
        ts.CONF_DIR = t
        ts.CONF_FILE = t / "config.json"
        ts.DEV_FILE = t / "devices.json"
        try:
            cfg = ts.load_config()
            self.assertNotIn("api_key", cfg)
            self.assertEqual(cfg["accounts"], {"default": {"api_key": "tskey-old"}})
            self.assertEqual(cfg["active_account"], "default")
            self.assertEqual(ts.active_api_key(cfg), "tskey-old")
        finally:
            ts.CONF_DIR, ts.CONF_FILE, ts.DEV_FILE = old_conf_dir

    def _cli_env(self, t):
        (t / "s.json").write_text(json.dumps({"BackendState": "Running", "Self": {}, "Peer": {}}))
        (t / "conf").mkdir()
        (t / "conf" / "config.json").write_text(json.dumps({"probe_ports": [22], "configured": True}))
        return dict(os.environ, HOME=str(t), TSSH_CONFIG_DIR=str(t / "conf"),
                   PATH=f"{MOCKBIN}:{os.environ['PATH']}", MOCK_TS_JSON=str(t / "s.json"),
                   MOCK_TS_CALLS=str(t / "calls.jsonl"), NO_COLOR="1")

    def _run(self, env, *args):
        return subprocess.run([sys.executable, str(ROOT / "tailscale_ssh.py"), *args],
                              capture_output=True, text=True, env=env, timeout=30,
                              stdin=subprocess.DEVNULL)

    def _calls(self, t):
        p = t / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_cli_passthrough_list_use_add(self):
        t = Path(tempfile.mkdtemp())
        env = self._cli_env(t)

        r = self._run(env, "accounts")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alice@example.com", r.stdout)          # the mock's --list output came through
        self.assertIn(["switch", "--list"], self._calls(t))

        r = self._run(env, "accounts", "use", "bob@work.example.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["switch", "bob@work.example.com"], self._calls(t))

        r = self._run(env, "accounts", "add", "personal")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self._calls(t)
        self.assertIn(["login"], calls)
        self.assertIn(["set", "--nickname=personal"], calls)

        r = self._run(env, "accounts", "use")  # no name given
        self.assertEqual(r.returncode, 1)
        self.assertIn("Usage", r.stderr)

    def test_termux_profiles_list_use_reject_unknown(self):
        t = Path(tempfile.mkdtemp())
        (t / "conf").mkdir()
        (t / "conf" / "config.json").write_text(json.dumps({
            "probe_ports": [22], "configured": True,
            "accounts": {"personal": {"api_key": "tskey-p"}, "work": {"api_key": "tskey-w"}},
            "active_account": "personal",
        }))
        env = dict(os.environ, HOME=str(t), TSSH_CONFIG_DIR=str(t / "conf"),
                   TERMUX_VERSION="0.118", PREFIX="/data/data/com.termux/files/usr", NO_COLOR="1")

        r = self._run(env, "accounts")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("personal", r.stdout)
        self.assertIn("work", r.stdout)
        self.assertIn("active", r.stdout)

        r = self._run(env, "accounts", "use", "work")
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = json.loads((t / "conf" / "config.json").read_text())
        self.assertEqual(saved["active_account"], "work")

        r = self._run(env, "accounts", "use", "nonexistent")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No saved profile", r.stderr)
        # a rejected switch must not have silently changed anything
        saved2 = json.loads((t / "conf" / "config.json").read_text())
        self.assertEqual(saved2["active_account"], "work")

    def test_setup_creates_named_profile_without_disturbing_others(self):
        """mesh setup --account NAME --api-key KEY -y adds a new profile and makes it active,
        while leaving an existing one alone -- this is the actual two-tailnets-on-one-phone
        scenario, end to end."""
        calls = []
        devices = {"devices": [{"name": "arch.tail1234.ts.net", "hostname": "arch",
                                 "addresses": ["100.64.0.11"], "os": "linux",
                                 "user": "franc@example.com", "connectedToControl": True}]}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(h):
                calls.append(h.headers.get("Authorization"))
                body = json.dumps(devices).encode()
                h.send_response(200); h.send_header("Content-Type", "application/json"); h.end_headers()
                h.wfile.write(body)

            def log_message(h, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            t = Path(tempfile.mkdtemp())
            (t / "conf").mkdir()
            (t / "conf" / "config.json").write_text(json.dumps({
                "probe_ports": [22], "configured": True,
                "accounts": {"personal": {"api_key": "tskey-existing"}},
                "active_account": "personal",
            }))
            env = dict(os.environ, HOME=str(t), TSSH_CONFIG_DIR=str(t / "conf"),
                       TERMUX_VERSION="0.118", PREFIX="/data/data/com.termux/files/usr",
                       TSSH_API_BASE=base, TSSH_LOCAL_IP="100.64.0.11", NO_COLOR="1")
            r = self._run(env, "setup", "--account", "work", "--api-key", "tskey-api-new", "-y")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Bearer tskey-api-new", calls)

            saved = json.loads((t / "conf" / "config.json").read_text())
            self.assertEqual(saved["accounts"]["work"]["api_key"], "tskey-api-new")
            self.assertEqual(saved["accounts"]["personal"]["api_key"], "tskey-existing")  # untouched
            self.assertEqual(saved["active_account"], "work")
        finally:
            srv.shutdown()


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

    def test_gui_is_a_subcommand_not_a_device_name(self):
        """'gui' must reach cmd_gui, not fall through to 'connect to a device called gui'. An
        unknown device given to it should be reported by gui's own lookup, same as connect's."""
        r = self.e.run("gui", "zzz-not-a-device")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No device matches 'zzz-not-a-device'", r.stderr)
        self.assertNotIn("No device matches 'gui'", r.stderr)
        self.assertIn("gui [device]", self.e.run("--help").stdout)

    def test_gui_ambiguous_device(self):
        e = Env()
        e.status["Peer"]["k8"] = peer("mint-laptop", "127.0.0.10")
        e.write_status()
        r = e.run("gui", "mint")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ambiguous", r.stderr)

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
