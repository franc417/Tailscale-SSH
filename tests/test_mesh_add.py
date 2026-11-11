"""`mesh add`: reaching a device by name when there is no Tailscale API key (a phone's usual situation)."""

import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import tailscale_ssh as ts  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "CONF_DIR", tmp_path)
    monkeypatch.setattr(ts, "CONF_FILE", tmp_path / "config.json")
    monkeypatch.setattr(ts, "DEV_FILE", tmp_path / "devices.json")
    monkeypatch.setattr(ts, "ASSUME_YES", True)
    ts._RESOLVED.clear()
    monkeypatch.delenv("TSSH_DNS", raising=False)
    monkeypatch.setattr(ts, "ts_cli", lambda: None)             # a phone: no tailscale command
    monkeypatch.setattr(ts, "local_tailscale_ip", lambda: "100.64.0.9")
    yield


def cfg(**extra):
    base = copy.deepcopy(ts.DEFAULTS)
    base.update(extra)
    return base


class SshBanner:
    """A real local listener that greets with an SSH banner, like sshd."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.sendall(b"SSH-2.0-test\r\n")
            finally:
                conn.close()

    def close(self):
        self.sock.close()


@pytest.fixture
def sshd():
    s = SshBanner()
    yield s
    s.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeDns:
    """A one-answer DNS server on loopback."""

    def __init__(self, answers=None, rcode=0, wrong_id=False, compress=True):
        self.answers, self.rcode, self.wrong_id, self.compress = answers or {}, rcode, wrong_id, compress
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                return
            pos = 12
            labels = []
            while data[pos]:
                labels.append(data[pos + 1:pos + 1 + data[pos]].decode())
                pos += 1 + data[pos]
            qend = pos + 5
            name = ".".join(labels)
            ip = self.answers.get(name)
            txid = b"\x00\x00" if self.wrong_id else data[:2]
            if ip is None or self.rcode:
                reply = txid + bytes([0x81, 0x80 | (self.rcode or 3)]) + data[4:6] + b"\x00\x00\x00\x00\x00\x00" + data[12:qend]
            else:
                owner = b"\xc0\x0c" if self.compress else data[12:pos + 1]
                rdata = bytes(int(p) for p in ip.split("."))
                reply = (txid + b"\x81\x80" + data[4:6] + b"\x00\x01\x00\x00\x00\x00" + data[12:qend] + owner
                         + b"\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + rdata)
            self.sock.sendto(reply, addr)

    def close(self):
        self.sock.close()


@pytest.fixture
def dns():
    servers = []

    def make(**kw):
        d = FakeDns(**kw)
        servers.append(d)
        return d

    yield make
    for d in servers:
        d.close()


# ---- resolving a name

def test_an_ip_address_is_used_as_it_is():
    assert ts.resolve_host("100.67.163.21") == "100.67.163.21"
    assert ts.is_ip("100.67.163.21") and not ts.is_ip("mkz")


def test_the_system_resolver_is_tried_first(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("100.1.2.3", 0))])
    assert ts.resolve_host("mkz") == "100.1.2.3"


def test_when_the_phones_resolver_cannot_find_a_tailnet_name_tailscales_own_is_asked(monkeypatch, dns):
    def nope(*a, **k):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", nope)
    server = dns(answers={"mkz.tail1234.ts.net": "100.67.163.21"})
    monkeypatch.setenv("TSSH_DNS", f"127.0.0.1:{server.port}")
    assert ts.resolve_host("mkz.tail1234.ts.net") == "100.67.163.21"
    assert ts.resolve_host("mkz.tail1234.ts.net.") == "100.67.163.21"          # a trailing dot is fine
    assert ts.resolve_host("unknown.tail1234.ts.net") is None


def test_dns_answers_with_and_without_name_compression(dns):
    for compress in (True, False):
        server = dns(answers={"mkz": "100.9.8.7"}, compress=compress)
        assert ts.dns_lookup_a("mkz", "127.0.0.1", server.port) == "100.9.8.7"


def test_a_dns_error_or_a_reply_for_another_question_gives_nothing(dns):
    assert ts.dns_lookup_a("mkz", "127.0.0.1", dns(answers={"mkz": "1.2.3.4"}, rcode=2).port) is None
    assert ts.dns_lookup_a("mkz", "127.0.0.1", dns(answers={"mkz": "1.2.3.4"}, wrong_id=True).port, timeout=0.5) is None


def test_a_silent_dns_server_times_out_quickly():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    try:
        assert ts.dns_lookup_a("mkz", "127.0.0.1", s.getsockname()[1], timeout=0.3) is None
    finally:
        s.close()


def test_nonsense_names_are_refused_without_a_lookup():
    assert ts.dns_lookup_a("", "127.0.0.1", 9) is None
    assert ts.dns_lookup_a("a" * 64 + ".net", "127.0.0.1", 9) is None
    assert ts.dns_lookup_a("naïve.net", "127.0.0.1", 9) is None
    assert ts.resolve_host("   ") is None


def test_resolutions_are_remembered_briefly(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(ts.time, "time", lambda: clock[0])
    monkeypatch.setattr(ts, "resolve_host", lambda h: calls.append(h) or ("100.1.1.1" if h == "good" else None))
    assert ts.resolve_cached("good") == "100.1.1.1" and ts.resolve_cached("good") == "100.1.1.1"
    assert calls == ["good"]
    assert ts.resolve_cached("bad") is None and ts.resolve_cached("bad") is None
    assert calls == ["good", "bad"]
    clock[0] += 9                                   # a failure is retried soon, a success is kept for a minute
    ts.resolve_cached("bad")
    ts.resolve_cached("good")
    assert calls == ["good", "bad", "bad"]


# ---- the device list

def test_a_phone_with_no_key_lists_the_devices_it_added(monkeypatch):
    monkeypatch.setattr(ts, "resolve_host", lambda h: {"mkz": "100.67.163.21"}.get(h))
    nodes, meta = ts.fetch_nodes(cfg(manual_devices={"mkz": {"host": "mkz"}}), False)
    assert [(n.name, n.ip, n.online, n.manual) for n in nodes] == [("mkz", "100.67.163.21", True, True)]
    assert meta["backend"] == "manual" and meta["self"] is None


def test_a_name_that_does_not_resolve_is_listed_offline(monkeypatch):
    monkeypatch.setattr(ts, "resolve_host", lambda h: None)
    nodes, _ = ts.fetch_nodes(cfg(manual_devices={"mkz": {"host": "mkz"}}), False)
    assert nodes[0].online is False and nodes[0].ips == []


def test_without_tailscale_running_the_error_says_so_instead_of_listing_unreachable_devices(monkeypatch):
    monkeypatch.setattr(ts, "local_tailscale_ip", lambda: None)
    with pytest.raises(ts.TSError) as e:
        ts.fetch_nodes(cfg(manual_devices={"mkz": {"host": "100.1.1.1"}}), False)
    assert "isn't connected" in str(e.value)


def test_nothing_configured_still_says_to_run_setup():
    with pytest.raises(ts.TSError) as e:
        ts.fetch_nodes(cfg(), False)
    assert "isn't set up" in str(e.value)


def api_devices(*names):
    return [{"name": f"{n}.tail1.ts.net.", "addresses": [f"100.0.0.{i + 1}"], "os": "linux", "connectedToControl": True, "user": "me"}
            for i, n in enumerate(names)] + [{"name": "phone.tail1.ts.net.", "addresses": ["100.0.0.99"], "os": "android",
                                                "connectedToControl": True, "user": "me"}]


def test_devices_added_by_name_join_the_api_list_without_duplicates(monkeypatch):
    monkeypatch.setattr(ts, "api_get_devices", lambda key: api_devices("mkz"))
    monkeypatch.setattr(ts, "local_tailscale_ip", lambda: "100.0.0.99")
    monkeypatch.setattr(ts, "resolve_host", lambda h: {"mkz": "100.0.0.1", "extra": "100.0.0.50", "100.0.0.1": "100.0.0.1"}.get(h))
    config = cfg(accounts={"a": {"api_key": "k"}}, active_account="a",
                 manual_devices={"mkz": {"host": "mkz"}, "again": {"host": "100.0.0.1"}, "extra": {"host": "extra"}})
    nodes, _ = ts.fetch_nodes(config, False)
    assert sorted(n.name for n in nodes) == ["extra", "mkz"]          # 'mkz' by name and 'again' by address are the API's own device


def test_an_expired_key_falls_back_to_the_added_devices_and_says_why(monkeypatch):
    def rejected(key):
        raise ts.TSError("Tailscale API key was rejected or has expired", "make a new one")

    monkeypatch.setattr(ts, "api_get_devices", rejected)
    monkeypatch.setattr(ts, "resolve_host", lambda h: "100.0.0.5")
    config = cfg(accounts={"a": {"api_key": "k"}}, active_account="a", manual_devices={"mkz": {"host": "mkz"}})
    nodes, meta = ts.fetch_nodes(config, False)
    assert [n.name for n in nodes] == ["mkz"] and "expired" in meta["notice"] and "added by name" in meta["notice"]


def test_an_expired_key_with_nothing_added_is_still_an_error(monkeypatch):
    def rejected(key):
        raise ts.TSError("Tailscale API key was rejected or has expired")

    monkeypatch.setattr(ts, "api_get_devices", rejected)
    with pytest.raises(ts.TSError):
        ts.fetch_nodes(cfg(accounts={"a": {"api_key": "k"}}, active_account="a"), False)


def test_on_a_computer_with_the_cli_a_broken_tailscale_is_not_hidden_by_added_devices(monkeypatch):
    monkeypatch.setattr(ts, "ts_cli", lambda: "/usr/bin/tailscale")

    def broken(show_all):
        raise ts.TSError("Tailscale is stopped")

    monkeypatch.setattr(ts, "fetch_cli", broken)
    monkeypatch.setattr(ts, "resolve_host", lambda h: "100.0.0.5")
    with pytest.raises(ts.TSError):
        ts.fetch_nodes(cfg(manual_devices={"x": {"host": "x"}}), False)


def test_the_cli_list_gets_the_added_devices_too(monkeypatch):
    monkeypatch.setattr(ts, "ts_cli", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(ts, "fetch_cli", lambda show_all: ([ts.Node(name="a", ips=["100.0.0.1"], online=True)], {"backend": "cli"}))
    monkeypatch.setattr(ts, "resolve_host", lambda h: "100.0.0.7")
    nodes, _ = ts.fetch_nodes(cfg(manual_devices={"shared": {"host": "shared"}}), False)
    assert sorted(n.name for n in nodes) == ["a", "shared"]


# ---- the live monitor

def test_the_monitor_probes_a_device_added_by_address_and_marks_it_ready(sshd):
    config = cfg(probe_ports=[sshd.port], manual_devices={"box": {"host": "127.0.0.1"}})
    mon = ts.Monitor(config, {})
    mon.refresh_once()
    _, nodes, _, error, _ = mon.snapshot()
    assert error is None and [(n.name, n.state, n.ssh_port) for n in nodes] == [("box", "ready", sshd.port)]


def test_an_unreachable_added_device_shows_offline_and_stays_that_way_between_refreshes():
    config = cfg(probe_ports=[free_port()], manual_devices={"gone": {"host": "127.0.0.1"}})
    mon = ts.Monitor(config, {})
    for _ in range(3):
        mon.refresh_once()
        assert [n.state for n in mon.snapshot()[1]] == ["offline"]


def test_an_added_device_that_comes_up_turns_ready(sshd):
    config = cfg(probe_ports=[free_port()], manual_devices={"box": {"host": "127.0.0.1"}})
    mon = ts.Monitor(config, {})
    mon.refresh_once()
    assert mon.snapshot()[1][0].state == "offline"
    config["probe_ports"] = [sshd.port]
    mon.refresh_once()
    assert mon.snapshot()[1][0].state == "ready"


def test_the_screen_shows_the_notice_only_when_there_is_one():
    nodes = [ts.Node(name="a", ips=["100.0.0.1"], online=True)]
    with_notice = "\n".join(ts.render(nodes, {"notice": "the key expired"}, None, False, None, False, 100, "mesh"))
    without = "\n".join(ts.render(nodes, {}, None, False, None, False, 100, "mesh"))
    assert "the key expired" in with_notice and "the key expired" not in without


# ---- the add command

def ns(*words, name=None):
    return argparse.Namespace(words=list(words), name=name)


def saved():
    return json.loads(ts.CONF_FILE.read_text())


def test_adding_by_name_saves_it_and_reports_ssh(sshd, monkeypatch, capsys):
    monkeypatch.setattr(ts, "resolve_host", lambda h: "127.0.0.1")
    config = cfg(probe_ports=[sshd.port])
    assert ts.cmd_add(ns("add", "mkz.tail1234.ts.net"), config, {}, "mesh") == 0
    assert saved()["manual_devices"] == {"mkz": {"host": "mkz.tail1234.ts.net"}}
    out = capsys.readouterr().out
    assert "Added mkz" in out and "answers SSH" in out


def test_a_label_can_be_chosen(monkeypatch):
    monkeypatch.setattr(ts, "resolve_host", lambda h: "127.0.0.1")
    ts.cmd_add(ns("add", "100.67.163.21", name="laptop"), cfg(probe_ports=[free_port()]), {}, "mesh")
    assert saved()["manual_devices"] == {"laptop": {"host": "100.67.163.21"}}


def test_an_address_without_a_label_is_listed_under_the_address_when_nobody_can_be_asked(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    ts.cmd_add(ns("add", "100.67.163.21"), cfg(probe_ports=[free_port()]), {}, "mesh")
    assert list(saved()["manual_devices"]) == ["100.67.163.21"]


def test_a_device_that_does_not_answer_yet_is_still_saved_with_a_warning(monkeypatch, capsys):
    monkeypatch.setattr(ts, "resolve_host", lambda h: "127.0.0.1")
    assert ts.cmd_add(ns("add", "mkz"), cfg(probe_ports=[free_port()]), {}, "mesh") == 0
    out = capsys.readouterr().out
    assert "mkz" in saved()["manual_devices"] and "Nothing answers SSH" in out and "mesh setup" in out


def test_an_unknown_name_is_refused_and_not_saved_and_the_hint_names_meshd_pair(monkeypatch, capsys):
    monkeypatch.setattr(ts, "resolve_host", lambda h: None)
    assert ts.cmd_add(ns("add", "nope"), cfg(), {}, "mesh") == 1
    out = capsys.readouterr().out
    assert not ts.CONF_FILE.exists() and "full Tailscale name" in out and "meshd pair" in out


def test_add_without_a_name_and_without_a_terminal_prints_usage(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        ts.cmd_add(ns("add"), cfg(), {}, "mesh")


def test_add_asks_when_run_by_hand_with_no_name(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ts, "ask", lambda prompt, default=None, secret=False: "mkz")
    monkeypatch.setattr(ts, "resolve_host", lambda h: "127.0.0.1")
    assert ts.cmd_add(ns("add"), cfg(probe_ports=[free_port()]), {}, "mesh") == 0
    assert "mkz" in saved()["manual_devices"]


def test_forget_removes_an_added_device_and_its_saved_user(tmp_path):
    config = cfg(manual_devices={"mkz": {"host": "mkz"}})
    ts.save_config(config)
    devices = {"mkz": {"user": "franc"}}
    assert ts.cmd_forget(ns("forget", "mkz"), devices, config) == 0
    assert devices == {} and saved()["manual_devices"] == {}


def test_forget_still_works_for_a_device_with_only_a_saved_user():
    devices = {"nas": {"user": "x"}}
    assert ts.cmd_forget(ns("forget", "nas"), devices, cfg()) == 0 and devices == {}


def test_forget_of_something_unknown_is_an_error():
    with pytest.raises(SystemExit):
        ts.cmd_forget(ns("forget", "ghost"), {}, cfg())


# ---- the first-run wizard on a phone

def test_the_phone_wizard_offers_adding_a_device_before_asking_for_an_api_key(monkeypatch):
    answers = iter(["1", "mkz"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ts, "ask", lambda prompt, default=None, secret=False: next(answers))
    monkeypatch.setattr(ts, "resolve_host", lambda h: "127.0.0.1")
    monkeypatch.setattr(ts, "probe_ssh", lambda ip, ports: (22, 5.0))
    monkeypatch.setattr(ts, "prompt_api_key", lambda plat: pytest.fail("asked for an API key"))
    config = cfg()
    ts.step_termux_tailscale(argparse.Namespace(account=None, api_key=None), config)
    assert config["manual_devices"] == {"mkz": {"host": "mkz"}} and not config["accounts"]


def test_the_phone_wizard_still_offers_the_api_key_route(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ts, "ask", lambda prompt, default=None, secret=False: "2")
    asked = []
    monkeypatch.setattr(ts, "prompt_api_key", lambda plat: asked.append(plat))
    config = cfg()
    ts.step_termux_tailscale(argparse.Namespace(account=None, api_key=None), config)
    assert asked == ["termux"] and "manual_devices" not in config


def test_a_phone_that_already_added_a_device_is_not_nagged_for_a_key(monkeypatch):
    monkeypatch.setattr(ts, "prompt_api_key", lambda plat: pytest.fail("asked for an API key"))
    config = cfg(manual_devices={"mkz": {"host": "mkz"}})
    ts.step_termux_tailscale(argparse.Namespace(account=None, api_key=None), config)


def test_an_api_key_given_on_the_command_line_skips_the_question(monkeypatch):
    monkeypatch.setattr(ts, "ask", lambda *a, **k: pytest.fail("asked a question"))
    monkeypatch.setattr(ts, "api_get_devices", lambda key: [{}, {}])
    config = cfg()
    ts.step_termux_tailscale(argparse.Namespace(account=None, api_key="tskey-api-xyz"), config)
    assert config["accounts"]["default"]["api_key"] == "tskey-api-xyz"


def test_doctor_counts_added_devices_as_a_working_setup(monkeypatch, capsys):
    monkeypatch.setattr(ts, "resolve_host", lambda h: "127.0.0.1")
    ts.cmd_doctor(cfg(manual_devices={"mkz": {"host": "mkz"}}), {})
    out = capsys.readouterr().out
    assert "1 device(s) you added by name" in out and "no tailscale CLI and no API key" not in out


# ---- the real command, start to finish

def run_mesh(tmp_path, *args, local_ip="100.64.0.9"):
    env = dict(os.environ, TSSH_CONFIG_DIR=str(tmp_path), TSSH_LOCAL_IP=local_ip, PATH="/usr/bin:/bin", TSSH_BRAND="mesh", NO_COLOR="1")
    return subprocess.run([sys.executable, str(ROOT / "tailscale_ssh.py"), *args], capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)


def test_add_then_list_end_to_end_with_no_key(tmp_path, sshd):
    (tmp_path / "config.json").write_text(json.dumps({"probe_ports": [sshd.port], "configured": True}))
    added = run_mesh(tmp_path, "add", "127.0.0.1", "--name", "box")
    assert added.returncode == 0, added.stdout + added.stderr
    listed = run_mesh(tmp_path, "list", "--json")
    rows = json.loads(listed.stdout)["devices"]
    assert [(r["name"], r["state"], r["ssh_port"]) for r in rows] == [("box", "ready", sshd.port)]
    forgotten = run_mesh(tmp_path, "forget", "box")
    assert forgotten.returncode == 0
    assert "manual_devices" in json.loads((tmp_path / "config.json").read_text())
    assert json.loads((tmp_path / "config.json").read_text())["manual_devices"] == {}


def test_help_mentions_add():
    out = subprocess.run([sys.executable, str(ROOT / "tailscale_ssh.py"), "help"], capture_output=True, text=True, env=dict(os.environ, TSSH_BRAND="mesh")).stdout
    assert "add <name-or-address>" in out
