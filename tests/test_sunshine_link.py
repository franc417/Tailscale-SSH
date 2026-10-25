"""The Sunshine link: a phone that holds the agent's token pairs its desktop view with Sunshine without typing a PIN.

Sunshine is replaced by a small fake web interface that behaves as Sunshine 2026.914 does (checked against its source):
GET /api/pin lists pending pairings, POST /api/pin approves one, HTTP Basic login, a redirect to /welcome while no login exists.
"""

import asyncio
import base64
import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import mesh_input as mi  # noqa: E402
from test_mesh_input import connect, needs_openssl, paired, running, settle  # noqa: E402


class FakeSunshine:
    """Just enough of Sunshine's web interface."""

    def __init__(self, login=("franc", "secret")):
        self.login = login                      # None = a fresh install with no web login yet
        self.pending = []                       # [{"id", "name", "address"}]
        self.pin_result = True                  # what POST /api/pin answers
        self.apps = [{"name": "Steam Big Picture"}]
        self.requests = []                      # (method, path, body)
        self.created_login = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else None

            def _send(self, code, payload=None, headers=None):
                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self):
                if fake.login is None:
                    self._send(302, None, {"Location": "/welcome"})
                    return False
                header = self.headers.get("Authorization", "")
                expected = "Basic " + base64.b64encode(f"{fake.login[0]}:{fake.login[1]}".encode()).decode()
                if header != expected:
                    self._send(401, {"status_code": 401, "error": "Unauthorized"})
                    return False
                return True

            def do_GET(self):
                fake.requests.append(("GET", self.path, None))
                if not self._authorized():
                    return
                if self.path == "/api/pin":
                    self._send(200, {"pairings": list(fake.pending)})
                elif self.path == "/api/apps":
                    self._send(200, {"env": {}, "apps": list(fake.apps)})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                body = self._body()
                fake.requests.append(("POST", self.path, body))
                if self.path == "/api/password":
                    if fake.login is not None and not self._authorized():
                        return
                    fake.login = (body["newUsername"], body["newPassword"])
                    fake.created_login = fake.login
                    self._send(200, {"status": True})
                    return
                if not self._authorized():
                    return
                if self.path == "/api/pin":
                    self._send(200, {"status": fake.pin_result})
                elif self.path == "/api/apps":
                    fake.apps.append({"name": body["name"]})
                    self._send(200, {"status": True})
                else:
                    self._send(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add_pending(self, address="127.0.0.1", name="phone", pairing_id="a" * 32):
        self.pending.append({"id": pairing_id, "name": name, "address": address})


@pytest.fixture
def sunshine():
    fake = FakeSunshine()
    yield fake
    fake.close()


def link_for(tmp_path, fake, with_login=True, **kwargs):
    link = mi.SunshineLink(str(tmp_path / "agent"), url=fake.url, sleep=lambda s: time.sleep(0.02), **kwargs)
    if with_login:
        link.save_login("franc", "secret")
    return link


def posts_to(fake, path):
    return [body for (method, p, body) in fake.requests if method == "POST" and p == path]


# ------------------------------------------------------------------ protocol bytes

def test_the_new_messages_have_fixed_byte_vectors():
    # The phone app's MipCodecTest checks the same bytes.
    assert mi.msg_sunshine_pair("1234", "pixel").hex() == "000c" + "0a" + "04" + "31323334" + "05" + "706978656c"
    assert mi.msg_sunshine_result(mi.S_OK, "paired").hex() == "0009" + "8a" + "00" + "06" + "706169726564"


def test_sunshine_pair_parses_what_it_builds_and_rejects_truncation():
    frame = mi.msg_sunshine_pair("0042", "Pixel 8")
    assert mi.parse_sunshine_pair(frame[3:]) == ("0042", "Pixel 8")
    with pytest.raises(mi.ProtocolError):
        mi.parse_sunshine_pair(b"")
    with pytest.raises(mi.ProtocolError):
        mi.parse_sunshine_pair(b"\x04123")          # promises 4 digits, has 3
    with pytest.raises(mi.ProtocolError):
        mi.parse_sunshine_pair(b"\x04" + b"1234")   # no name field


def test_hello_ok_advertises_the_sunshine_feature():
    frame = mi.msg_hello_ok("host")
    assert frame[4] & mi.FEATURE_SUNSHINE_PAIR      # [len2][type][version][features]
    assert frame[4] & mi.FEATURE_TEXT and frame[4] & mi.FEATURE_HIRES_SCROLL


# ------------------------------------------------------------------ the saved login

def test_the_login_is_saved_privately_and_read_back(tmp_path):
    link = mi.SunshineLink(str(tmp_path / "agent"))
    assert link.load_login() is None and not link.has_login()
    link.save_login("franc", "p@ss word")
    assert link.load_login() == ("franc", "p@ss word")
    assert stat.S_IMODE(os.stat(link.login_path).st_mode) == 0o600
    assert not os.path.exists(link.login_path + ".tmp")


def test_a_damaged_login_file_counts_as_not_configured(tmp_path):
    link = mi.SunshineLink(str(tmp_path))
    with open(link.login_path, "w") as f:
        f.write("{not json")
    assert link.load_login() is None
    with open(link.login_path, "w") as f:
        json.dump({"username": "x"}, f)
    assert link.load_login() is None


def test_forgetting_the_login_is_harmless_when_there_is_none(tmp_path):
    link = mi.SunshineLink(str(tmp_path))
    link.forget_login()
    link.save_login("a", "b")
    link.forget_login()
    assert not link.has_login()


# ------------------------------------------------------------------ pairing

def test_the_pending_pairing_is_approved_with_the_phones_pin_and_name(tmp_path, sunshine):
    sunshine.add_pending(address="127.0.0.1", name="whatever the phone called itself", pairing_id="b" * 32)
    link_for(tmp_path, sunshine).pair("1234", "Pixel 8", peer="127.0.0.1")
    assert posts_to(sunshine, "/api/pin") == [{"pairing_id": "b" * 32, "pin": "1234", "name": "Pixel 8"}]


def test_it_waits_for_the_phones_request_to_appear(tmp_path, sunshine):
    threading.Timer(0.3, lambda: sunshine.add_pending()).start()   # the phone's request arrives a moment later
    link_for(tmp_path, sunshine).pair("4321", "phone", peer="127.0.0.1", wait=5)
    assert len(posts_to(sunshine, "/api/pin")) == 1


def test_a_request_from_another_address_is_never_approved(tmp_path, sunshine):
    sunshine.add_pending(address="192.168.1.99", pairing_id="c" * 32)
    with pytest.raises(mi.SunshineError) as exc:
        link_for(tmp_path, sunshine).pair("1234", "phone", peer="192.168.1.20", wait=0.3)
    assert exc.value.code == mi.S_FAILED and "another device" in exc.value.text
    assert posts_to(sunshine, "/api/pin") == []


def test_our_request_is_picked_when_a_stranger_has_one_too(tmp_path, sunshine):
    sunshine.add_pending(address="192.168.1.99", pairing_id="c" * 32)
    sunshine.add_pending(address="192.168.1.20", pairing_id="d" * 32)
    link_for(tmp_path, sunshine).pair("1234", "phone", peer="192.168.1.20", wait=2)
    assert posts_to(sunshine, "/api/pin")[0]["pairing_id"] == "d" * 32


def test_addresses_are_compared_in_their_normal_form():
    assert mi._normalize_address("::FFFF:192.168.1.20") == "192.168.1.20"
    assert mi._normalize_address("fe80::1%wlan0") == "fe80::1"
    assert mi._normalize_address(" 100.64.0.7 ") == "100.64.0.7"
    assert mi._normalize_address("") == ""


def test_without_a_known_peer_any_pending_request_is_used(tmp_path, sunshine):
    sunshine.add_pending(address="10.1.2.3")
    link_for(tmp_path, sunshine).pair("1234", "phone", peer="", wait=2)
    assert len(posts_to(sunshine, "/api/pin")) == 1


def test_nothing_pending_gives_a_clear_failure(tmp_path, sunshine):
    with pytest.raises(mi.SunshineError) as exc:
        link_for(tmp_path, sunshine).pair("1234", "phone", peer="127.0.0.1", wait=0.2)
    assert exc.value.code == mi.S_FAILED and "never reached Sunshine" in exc.value.text
    assert posts_to(sunshine, "/api/pin") == []


def test_sunshine_refusing_the_pin_is_a_failure(tmp_path, sunshine):
    sunshine.add_pending()
    sunshine.pin_result = False
    with pytest.raises(mi.SunshineError) as exc:
        link_for(tmp_path, sunshine).pair("1234", "phone", peer="127.0.0.1")
    assert exc.value.code == mi.S_FAILED and "wrong PIN" in exc.value.text


@pytest.mark.parametrize("pin", ["", "123", "12345", "12a4", "١٢٣٤", " 123"])
def test_a_pin_that_is_not_four_ascii_digits_never_reaches_sunshine(tmp_path, sunshine, pin):
    with pytest.raises(mi.SunshineError) as exc:
        link_for(tmp_path, sunshine).pair(pin, "phone")
    assert exc.value.code == mi.S_BAD_REQUEST
    assert sunshine.requests == []


def test_a_missing_name_gets_a_default_and_a_long_one_is_cut_on_a_character_boundary(tmp_path, sunshine):
    sunshine.add_pending()
    link = link_for(tmp_path, sunshine)
    link.pair("1234", "   ", peer="127.0.0.1")
    assert posts_to(sunshine, "/api/pin")[-1]["name"] == "Mesh phone"
    sunshine.requests.clear()
    link.pair("1234", "é" * 200, peer="127.0.0.1")
    name = posts_to(sunshine, "/api/pin")[-1]["name"]
    assert len(name.encode()) <= 128 and set(name) == {"é"}


# ------------------------------------------------------------------ failures that need the person to do something

def test_no_saved_login_says_how_to_make_one_and_asks_nothing_of_sunshine(tmp_path, sunshine):
    with pytest.raises(mi.SunshineError) as exc:
        link_for(tmp_path, sunshine, with_login=False).pair("1234", "phone")
    assert exc.value.code == mi.S_NOT_CONFIGURED and "sunshine-login" in exc.value.text
    assert sunshine.requests == []


def test_a_login_sunshine_does_not_accept_is_reported_as_such(tmp_path, sunshine):
    link = link_for(tmp_path, sunshine, with_login=False)
    link.save_login("franc", "wrong")
    with pytest.raises(mi.SunshineError) as exc:
        link.pair("1234", "phone")
    assert exc.value.code == mi.S_LOGIN_REFUSED and "sunshine-login" in exc.value.text


def test_a_sunshine_with_no_login_yet_is_reported_as_not_configured(tmp_path):
    fake = FakeSunshine(login=None)
    try:
        link = link_for(tmp_path, fake)
        with pytest.raises(mi.SunshineError) as exc:
            link.pair("1234", "phone")
        assert exc.value.code == mi.S_NOT_CONFIGURED
    finally:
        fake.close()


def test_a_sunshine_that_is_not_running_is_reported_as_unreachable(tmp_path):
    fake = FakeSunshine()
    url = fake.url
    fake.close()                                   # nothing listens any more
    link = mi.SunshineLink(str(tmp_path), url=url, sleep=lambda s: None)
    link.save_login("franc", "secret")
    with pytest.raises(mi.SunshineError) as exc:
        link.pair("1234", "phone", wait=0.1)
    assert exc.value.code == mi.S_UNREACHABLE and "Is Sunshine running" in exc.value.text


def test_an_unexpected_sunshine_error_is_reported(tmp_path, sunshine):
    link = link_for(tmp_path, sunshine)
    link.url = sunshine.url + "/nothing-here"       # every path now answers 404
    with pytest.raises(mi.SunshineError) as exc:
        link.pair("1234", "phone", wait=0.1)
    assert exc.value.code == mi.S_UNREACHABLE and "404" in exc.value.text


# ------------------------------------------------------------------ setting the login up

def test_a_fresh_sunshine_gets_a_login_made_by_mesh(tmp_path):
    fake = FakeSunshine(login=None)
    try:
        link = mi.SunshineLink(str(tmp_path), url=fake.url)
        assert link.needs_first_login()
        password = link.create_login()
        assert fake.created_login == ("mesh", password) and len(password) >= 20
        assert link.load_login() == ("mesh", password)
        assert not link.needs_first_login()
        link.check_login()                          # and the saved login works
    finally:
        fake.close()


def test_an_existing_login_is_never_overwritten_by_create(tmp_path, sunshine):
    link = mi.SunshineLink(str(tmp_path), url=sunshine.url)
    assert not link.needs_first_login()
    with pytest.raises(mi.SunshineError):
        link.create_login()                         # needs the current login, which it does not have
    assert sunshine.login == ("franc", "secret")


def test_the_desktop_app_is_added_only_when_missing(tmp_path, sunshine):
    link = link_for(tmp_path, sunshine)
    assert link.ensure_desktop_app() is True
    assert posts_to(sunshine, "/api/apps")[0]["name"] == "Desktop"
    assert posts_to(sunshine, "/api/apps")[0]["index"] == -1
    assert link.ensure_desktop_app() is False
    sunshine.apps = [{"name": " desktop "}]
    sunshine.requests.clear()
    assert link.ensure_desktop_app() is False       # any capitalisation counts
    assert posts_to(sunshine, "/api/apps") == []


# ------------------------------------------------------------------ the sunshine-login command

def run_login(tmp_path, fake, **kwargs):
    lines = []
    code = mi.sunshine_login(str(tmp_path / "agent"), url=fake.url, out=lines.append, **kwargs)
    return code, "\n".join(lines)


def test_sunshine_login_saves_a_working_login_and_adds_the_desktop_app(tmp_path, sunshine):
    code, text = run_login(tmp_path, sunshine, username="franc", password="secret")
    assert code == 0 and "accepted" in text and "no PIN" in text and "Added a Desktop" in text
    assert mi.SunshineLink(str(tmp_path / "agent")).load_login() == ("franc", "secret")


def test_sunshine_login_asks_for_what_it_was_not_given(tmp_path, sunshine):
    code, _ = run_login(tmp_path, sunshine, ask=lambda prompt: "franc", ask_secret=lambda prompt: "secret")
    assert code == 0


def test_sunshine_login_does_not_keep_a_wrong_password(tmp_path, sunshine):
    code, text = run_login(tmp_path, sunshine, username="franc", password="nope")
    assert code == 1 and "did not accept" in text
    assert not mi.SunshineLink(str(tmp_path / "agent")).has_login()


def test_sunshine_login_refuses_empty_answers(tmp_path, sunshine):
    code, text = run_login(tmp_path, sunshine, ask=lambda p: "", ask_secret=lambda p: "")
    assert code == 1 and "both needed" in text
    assert sunshine.requests[-1][1] != "/api/pin" or sunshine.requests[-1][0] == "GET"   # nothing was approved or changed


def test_sunshine_login_on_a_fresh_sunshine_makes_a_login_itself(tmp_path):
    fake = FakeSunshine(login=None)
    try:
        code, text = run_login(tmp_path, fake)
        assert code == 0 and "made one for itself" in text
        assert mi.SunshineLink(str(tmp_path / "agent")).has_login()
    finally:
        fake.close()


def test_sunshine_login_reports_sunshine_not_running(tmp_path):
    fake = FakeSunshine()
    url = fake.url
    fake.close()
    lines = []
    code = mi.sunshine_login(str(tmp_path), url=url, username="a", password="b", out=lines.append)
    assert code == 1 and "does not answer" in "\n".join(lines)


def test_pair_info_hints_at_sunshine_login_on_stderr_only(tmp_path, capsys):
    if not __import__("shutil").which("openssl"):
        pytest.skip("needs openssl")
    assert mi.main(["pair-info", "--dir", str(tmp_path / "agent")]) == 0
    out, err = capsys.readouterr()
    json.loads(out)                                  # the JSON on stdout stays clean to copy
    assert "sunshine-login" in err
    mi.SunshineLink(str(tmp_path / "agent")).save_login("a", "b")
    mi.main(["pair-info", "--dir", str(tmp_path / "agent")])
    assert "sunshine-login" not in capsys.readouterr().err


# ------------------------------------------------------------------ over the real encrypted channel

async def sunshine_result(client, timeout=8.0):
    """Reads frames until the Sunshine result arrives; returns (status, text)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg_type, payload = await client.recv(timeout=timeout)
        if msg_type == mi.T_SUNSHINE_RESULT:
            return payload[0], payload[2:2 + payload[1]].decode()
    raise AssertionError("no Sunshine result")


@needs_openssl
def test_a_phone_pairs_through_the_channel_and_the_channel_stays_responsive(tmp_path, sunshine):
    async def go():
        link = link_for(tmp_path, sunshine)
        async with running(tmp_path, sunshine=link) as (port, _inj, token, _fp):
            c = await paired(port, token, "Pixel 8")
            await c.send(mi.msg_sunshine_pair("2468", "Pixel 8"))
            # While the agent waits for the phone's pairing request, pings are still answered at once.
            await c.send(mi.msg_ping(7))
            msg_type, payload = await c.recv(timeout=2)
            assert (msg_type, payload) == (mi.T_PONG, b"\x00\x00\x00\x07")
            sunshine.add_pending(address="127.0.0.1", name="Pixel 8")
            assert await sunshine_result(c) == (mi.S_OK, "paired")
            assert posts_to(sunshine, "/api/pin") == [{"pairing_id": "a" * 32, "pin": "2468", "name": "Pixel 8"}]
    asyncio.run(go())


class QuickLink(mi.SunshineLink):
    """The same link, but it gives up after a second instead of twenty, so a test that expects a failure is fast."""

    def pair(self, pin, name, peer="", wait=1.0):
        return super().pair(pin, name, peer, wait=wait)


@needs_openssl
def test_the_phones_address_is_what_the_agent_matches_against(tmp_path, sunshine):
    async def go():
        sunshine.add_pending(address="203.0.113.9")   # a different device is pairing, not this phone (127.0.0.1)
        link = QuickLink(str(tmp_path / "agent"), url=sunshine.url, sleep=lambda s: time.sleep(0.02))
        link.save_login("franc", "secret")
        async with running(tmp_path, sunshine=link) as (port, _inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_sunshine_pair("1111", "phone"))
            status, text = await sunshine_result(c, timeout=10)
            assert status == mi.S_FAILED and "another device" in text
            assert posts_to(sunshine, "/api/pin") == []
    asyncio.run(go())


@needs_openssl
def test_an_agent_without_the_link_answers_not_configured(tmp_path):
    async def go():
        async with running(tmp_path) as (port, _inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_sunshine_pair("1234", "phone"))
            status, _text = await sunshine_result(c)
            assert status == mi.S_NOT_CONFIGURED
    asyncio.run(go())


@needs_openssl
def test_a_bad_pin_gets_bad_request_over_the_channel(tmp_path, sunshine):
    async def go():
        async with running(tmp_path, sunshine=link_for(tmp_path, sunshine)) as (port, _inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_sunshine_pair("12", "phone"))
            status, text = await sunshine_result(c)
            assert status == mi.S_BAD_REQUEST and "4 digits" in text
            assert sunshine.requests == []
    asyncio.run(go())


@needs_openssl
def test_a_bug_in_the_link_never_takes_the_channel_down(tmp_path):
    class Broken:
        def pair(self, *args, **kwargs):
            raise RuntimeError("boom")

    async def go():
        async with running(tmp_path, sunshine=Broken()) as (port, _inj, token, _fp):
            c = await paired(port, token)
            await c.send(mi.msg_sunshine_pair("1234", "phone"))
            status, text = await sunshine_result(c)
            assert status == mi.S_FAILED and "boom" in text
            await c.send(mi.msg_ping(1))
            assert (await c.recv())[0] == mi.T_PONG        # still alive
    asyncio.run(go())


@needs_openssl
def test_an_unauthenticated_connection_cannot_start_a_pairing(tmp_path, sunshine):
    async def go():
        sunshine.add_pending()
        async with running(tmp_path, sunshine=link_for(tmp_path, sunshine)) as (port, _inj, _token, _fp):
            c = await connect(port)
            await c.send(mi.msg_sunshine_pair("1234", "intruder"))   # no HELLO first
            msg_type, _payload = await c.recv()
            assert msg_type == mi.T_ERROR
            await settle()
            assert posts_to(sunshine, "/api/pin") == []
    asyncio.run(go())
