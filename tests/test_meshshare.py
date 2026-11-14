"""meshshare: the Mesh Guest link (rules, gateway in front of a real web app, Funnel commands, control socket, CLI)."""

import hashlib
import http.client
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import meshshare as ms  # noqa: E402

ORIGIN = "https://mkz.tail1234.ts.net"
HOST = "mkz.tail1234.ts.net"
BIG = os.urandom(3 * 1024 * 1024 + 17)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------- a small web app standing in for XAMPP

class App(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    stop_stream = threading.Event()

    def log_message(self, *a):
        pass

    def send(self, status, body=b"", ctype="text/html", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == "/":
            page = (f'<a href="http://localhost:{port}/page">x</a><form action="http://127.0.0.1:{port}/save"></form>'
                    f'<a href="http://localhost:9/other">o</a><a href="http://{HOST}/self">s</a>').encode()
            return self.send(200, page)
        if self.path == "/json":
            return self.send(200, json.dumps({"u": f"http:\\/\\/localhost:{port}\\/x"}).replace("\\\\", "\\").encode(), "application/json")
        if self.path == "/redirect":
            return self.send(302, b"", headers=[("Location", f"http://localhost:{port}/target?a=1")])
        if self.path == "/cookie":
            return self.send(200, b"ok", headers=[("Set-Cookie", "PHPSESSID=abc; Path=/; Domain=localhost; HttpOnly")])
        if self.path == "/big":
            return self.send(200, BIG, "application/octet-stream")
        if self.path == "/chunked":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for part in (b"hello ", b"chunked ", b"world"):
                self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while not App.stop_stream.is_set():
                    self.wfile.write(b"5\r\nhello\r\n")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass
            return
        return self.echo()

    def do_HEAD(self):
        self.send(200, b"", headers=[])

    def do_POST(self):
        self.echo()

    do_PUT = do_POST

    def echo(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        data = {"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body.decode("latin1")}
        self.send(200, json.dumps(data).encode(), "application/json")


class Quiet(http.server.ThreadingHTTPServer):
    daemon_threads = True


@pytest.fixture()
def app():
    App.stop_stream.clear()
    server = Quiet(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield server
    App.stop_stream.set()
    server.shutdown()
    server.server_close()


class Rig:
    def __init__(self, app, read_only=False, ttl=1800, idle=900):
        self.clock = Clock()
        self.events = []
        self.manager = ms.ShareManager(clock=self.clock, listener=self.events.append)
        self.gateway = ms.Gateway(self.manager)
        self.gateway.set_public_origin(ORIGIN)
        self.port = self.gateway.start()
        self.app_port = app.server_address[1]
        self.share, self.secret = self.manager.create("web", self.app_port, ttl, idle, read_only)

    def http(self, method, path, body=None, headers=None, cookie=None, host=HOST):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host, "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"}
        h.update(headers or {})
        if cookie:
            h["Cookie"] = f"{ms.COOKIE}={cookie}" + (f"; {h['Cookie']}" if "Cookie" in h else "")
        if isinstance(body, dict):
            body = json.dumps(body)
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def claim(self, secret=None, cookie=None, ip="203.0.113.9"):
        resp, data = self.http("POST", f"/_mesh/g/{self.share.id}/claim", {"secret": self.secret if secret is None else secret},
                               headers={"X-Forwarded-For": ip}, cookie=cookie)
        token = ""
        for k, v in resp.getheaders():
            if k.lower() == "set-cookie" and v.startswith(ms.COOKIE + "="):
                token = v.split("=", 1)[1].split(";", 1)[0]
        return resp, json.loads(data or b"{}"), token

    def enter(self, ip="203.0.113.9"):
        resp, body, token = self.claim(ip=ip)
        assert resp.status == 200 and body["state"] == "pending"
        ok, _ = self.manager.approve(self.share.id, body["code"])
        assert ok
        return token

    def close(self):
        self.gateway.stop()


@pytest.fixture()
def rig(app):
    r = Rig(app)
    yield r
    r.close()


# ---------------------------------------------------------------- small helpers

def test_parse_duration():
    assert ms.parse_duration("30m") == 1800 and ms.parse_duration("2h") == 7200 and ms.parse_duration("90") == 5400 and ms.parse_duration("45s") == 45
    with pytest.raises(ValueError):
        ms.parse_duration("soon")


def test_describe_agent():
    assert ms.describe_agent("Mozilla/5.0 (Windows NT 10.0) AppleWebKit Chrome/130 Safari/537 Edg/130") == "Edge on Windows"
    assert ms.describe_agent("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0") == "Firefox on Linux"
    assert ms.describe_agent("") == "a browser"


def test_client_ip_only_trusts_the_tunnel_hop():
    assert ms.client_ip("127.0.0.1", "1.2.3.4, 198.51.100.7") == "198.51.100.7"     # last entry: the one the tunnel added
    assert ms.client_ip("127.0.0.1", "not-an-ip") == "127.0.0.1"
    assert ms.client_ip("203.0.113.5", "198.51.100.7") == "203.0.113.5"             # a direct caller cannot claim another address


def test_cookie_helpers():
    assert ms.cookie_value("a=1; mesh_guest=tok; b=2", "mesh_guest") == "tok"
    assert ms.strip_cookie("a=1; mesh_guest=tok; b=2", "mesh_guest") == "a=1; b=2"
    assert ms.strip_cookie("mesh_guest=tok", "mesh_guest") == ""


def test_rewrite_text_port_80_and_other_ports():
    page = b'<a href="http://localhost/a">1</a> http://127.0.0.1:80/b http://localhost:8080/c http://localhostile/d http://mkz.tail1234.ts.net/e'
    out = ms.rewrite_text(page, ORIGIN, 80)
    assert b'href="https://mkz.tail1234.ts.net/a"' in out and b"https://mkz.tail1234.ts.net/b" in out and b"https://mkz.tail1234.ts.net/e" in out
    assert b"http://localhost:8080/c" in out and b"http://localhostile/d" in out        # other ports and look-alikes are left alone
    out = ms.rewrite_text(b"http://localhost:8080/x http://localhost/y", ORIGIN, 8080)
    assert out == b"https://mkz.tail1234.ts.net/x http://localhost/y"
    out = ms.rewrite_text(b'{"u":"http:\\/\\/localhost:8080\\/x"}', ORIGIN, 8080)
    assert out == b'{"u":"https:\\/\\/mkz.tail1234.ts.net\\/x"}'


def test_rewrite_location():
    assert ms.rewrite_location("http://localhost:8080/a?b=1#c", ORIGIN, 8080) == "/a?b=1#c"
    assert ms.rewrite_location("http://mkz.tail1234.ts.net/a", ORIGIN, 80) == "/a"
    assert ms.rewrite_location("https://example.com/a", ORIGIN, 80) == "https://example.com/a"
    assert ms.rewrite_location("/relative", ORIGIN, 80) == "/relative"


# ---------------------------------------------------------------- the rules, without any network

def test_secret_is_stored_only_as_a_hash():
    m = ms.ShareManager()
    share, secret = m.create("web", 80)
    assert secret not in json.dumps(share.__dict__, default=str)
    assert share.secret_hash == ms.digest(secret) and len(secret) >= 40


def test_ttl_is_clamped():
    m = ms.ShareManager()
    assert m.create("web", 80, ttl=10 ** 9)[0].expires - m.clock() <= ms.MAX_TTL + 1
    assert m.create("web", 80, ttl=1)[0].expires - m.clock() >= 59


def test_one_visitor_at_a_time_and_leave_needs_a_new_approval():
    clock = Clock()
    m = ms.ShareManager(clock=clock)
    share, secret = m.create("web", 80)
    status, body, token = m.claim(share.id, secret, "", "1.1.1.1", "Chrome on Windows")
    assert status == 200 and body["state"] == "pending" and len(body["code"]) == 4 and token
    assert m.claim(share.id, secret, "", "2.2.2.2", "Firefox on Linux")[0] == 409           # busy while someone waits
    assert m.touch(token) is None                                                            # not in until approved
    assert m.approve(share.id, "0000" if body["code"] != "0000" else "1111")[0] is False     # wrong code
    assert m.approve(share.id, body["code"])[0] is True
    assert m.touch(token) is not None
    assert m.claim(share.id, secret, "", "2.2.2.2", "Firefox on Linux")[0] == 409           # busy while someone is in
    assert m.leave(token) is True and m.touch(token) is None
    status, body2, token2 = m.claim(share.id, secret, "", "2.2.2.2", "Firefox on Linux")     # the link is still open...
    assert status == 200 and body2["state"] == "pending" and token2 != token                 # ...but a new visit needs approval again


def test_reload_with_cookie_keeps_the_same_request():
    m = ms.ShareManager()
    share, secret = m.create("web", 80)
    _, body, token = m.claim(share.id, secret, "", "1.1.1.1", "x")
    status, again, new_token = m.claim(share.id, "", token, "1.1.1.1", "x")                  # fragment gone after reload: cookie alone
    assert status == 200 and again["code"] == body["code"] and new_token is None


def test_guest_close_revokes_for_good():
    m = ms.ShareManager()
    share, secret = m.create("web", 80)
    _, body, token = m.claim(share.id, secret, "", "1.1.1.1", "x")
    m.approve(share.id)
    assert m.close(token) is True
    assert m.claim(share.id, secret, "", "1.1.1.1", "x")[0] == 410
    assert m.status(token)["state"] == "ended"


def test_expiry_idle_and_unanswered_request():
    clock = Clock()
    m = ms.ShareManager(clock=clock)
    share, secret = m.create("web", 80, ttl=600, idle=120)
    _, body, token = m.claim(share.id, secret, "", "1.1.1.1", "x")
    clock.advance(ms.PENDING_TTL + 1)
    m.reap()
    assert m.status(token) == {"state": "ended", "message": ms.REASONS["timeout"]}            # nobody answered: the slot is free again
    _, _, token = m.claim(share.id, secret, "", "1.1.1.1", "x")
    m.approve(share.id)
    clock.advance(121)
    assert m.touch(token) is None and m.status(token)["message"] == ms.REASONS["idle"]
    _, _, token = m.claim(share.id, secret, "", "1.1.1.1", "x")
    m.approve(share.id)
    clock.advance(60)
    assert m.touch(token) is not None                                                         # activity keeps it alive...
    clock.advance(600)                                                                         # ...until the link's own deadline
    assert m.touch(token) is None
    assert m.claim(share.id, secret, "", "1.1.1.1", "x")[0] == 410


def test_wrong_secrets_lock_an_address_then_the_share():
    clock = Clock()
    m = ms.ShareManager(clock=clock)
    share, secret = m.create("web", 80)
    for _ in range(ms.FAIL_LIMIT):
        assert m.claim(share.id, "nope", "", "9.9.9.9", "x")[0] == 403
    assert m.claim(share.id, secret, "", "9.9.9.9", "x")[0] == 429                            # even the right secret waits out the lock
    assert m.claim(share.id, secret, "", "8.8.8.8", "x")[0] == 200                             # another address is not affected
    m2 = ms.ShareManager(clock=clock)
    share2, _ = m2.create("web", 80)
    for i in range(ms.SHARE_FAIL_LIMIT):
        m2.claim(share2.id, "nope", "", f"10.0.{i // 200}.{i % 200}", "x")
    assert share2.state == "ended" and share2.reason == "lockout"


def test_unknown_share_looks_like_a_wrong_secret():
    m = ms.ShareManager()
    assert m.claim("nosuchid", "whatever", "", "1.1.1.1", "x")[:2] == (403, {"error": "no", "message": "This link is not valid."})


def test_approve_deny_describe():
    m = ms.ShareManager()
    assert m.approve()[0] is False
    share, secret = m.create("web", 80)
    m.claim(share.id, secret, "", "1.1.1.1", "Chrome on Windows")
    row = m.describe()[0]
    assert row["visitor"]["state"] == "pending" and row["visitor"]["code"] and "secret" not in json.dumps(row)
    assert m.deny()[0] is True
    assert m.describe()[0]["visitor"] is None


# ---------------------------------------------------------------- the gateway in front of a real web app

def test_landing_page_never_carries_the_secret_and_is_locked_down(rig):
    resp, data = rig.http("GET", f"/_mesh/g/{rig.share.id}")
    assert resp.status == 200 and rig.secret.encode() not in data
    headers = {k.lower(): v for k, v in resp.getheaders()}
    assert "script-src 'self'" in headers["content-security-policy"] and headers["referrer-policy"] == "no-referrer" and headers["cache-control"] == "no-store"
    js = rig.http("GET", "/_mesh/landing.js")[1].decode()
    assert "history.replaceState" in js                                                       # the secret leaves the address bar at once


def test_nothing_of_the_app_is_visible_without_a_session(rig):
    resp, data = rig.http("GET", "/", headers={"Accept": "text/html"})
    assert resp.status == 401 and b"localhost" not in data
    assert rig.http("GET", "/anything")[0].status == 401
    assert rig.http("GET", "/_mesh/other")[0].status == 404


def test_wrong_secret_is_refused(rig):
    resp, body, token = rig.claim(secret="wrong")
    assert resp.status == 403 and not token


def test_claim_sets_a_hardened_cookie_and_waits_for_approval(rig):
    resp, body, token = rig.claim()
    cookie = [v for k, v in resp.getheaders() if k.lower() == "set-cookie"][0]
    assert all(flag in cookie for flag in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/"))
    assert body["state"] == "pending"
    page, _ = rig.http("GET", "/", headers={"Accept": "text/html"}, cookie=token)
    assert page.status == 302 and dict(page.getheaders())["Location"] == f"/_mesh/g/{rig.share.id}"      # waiting: back to the waiting page
    assert rig.http("GET", "/", cookie=token)[0].status == 401                                  # and not a byte of the app
    status = json.loads(rig.http("GET", "/_mesh/status", cookie=token)[1])
    assert status == {"state": "pending", "code": body["code"]}
    assert any(e["event"] == "request" and e["code"] == body["code"] and e["ip"] == "203.0.113.9" and e["agent"] == "Chrome on Windows" for e in rig.events)


def test_after_approval_the_app_works_and_localhost_links_are_rewritten(rig):
    token = rig.enter()
    assert json.loads(rig.http("GET", "/_mesh/status", cookie=token)[1])["state"] == "active"
    resp, data = rig.http("GET", "/", cookie=token)
    assert resp.status == 200
    text = data.decode()
    assert f'href="{ORIGIN}/page"' in text and f'action="{ORIGIN}/save"' in text and f'href="{ORIGIN}/self"' in text
    assert "http://localhost:9/other" in text                                                  # another service is not ours to rewrite
    assert int(dict(resp.getheaders())["Content-Length"]) == len(data)
    resp, data = rig.http("GET", "/json", cookie=token)
    assert b"\\/\\/localhost" not in data


def test_the_session_cookie_never_reaches_the_app_and_forwarded_headers_cannot_be_forged(rig):
    token = rig.enter()
    resp, data = rig.http("GET", "/echo?x=1", cookie=token,
                          headers={"Cookie": "PHPSESSID=keepme", "X-Forwarded-For": "6.6.6.6", "X-Real-IP": "6.6.6.6", "Accept-Encoding": "gzip"})
    seen = json.loads(data)
    assert seen["path"] == "/echo?x=1"
    assert seen["headers"]["cookie"] == "PHPSESSID=keepme" and token not in json.dumps(seen)
    assert seen["headers"]["x-forwarded-for"] == "6.6.6.6"                                      # the tunnel hop's value (rightmost), set by us
    assert "x-real-ip" not in seen["headers"] and seen["headers"]["x-forwarded-proto"] == "https"
    assert seen["headers"]["accept-encoding"] == "identity" and seen["headers"]["host"] == HOST


def test_post_body_goes_through_and_view_only_blocks_it(app):
    r = Rig(app)
    try:
        token = r.enter()
        resp, data = r.http("POST", "/save", body="a=1&b=2", headers={"Content-Type": "application/x-www-form-urlencoded"}, cookie=token)
        assert json.loads(data)["body"] == "a=1&b=2" and json.loads(data)["method"] == "POST"
    finally:
        r.close()
    r = Rig(app, read_only=True)
    try:
        token = r.enter()
        assert r.http("GET", "/echo", cookie=token)[0].status == 200
        resp, data = r.http("POST", "/save", body="a=1", cookie=token)
        assert resp.status == 405 and b"View only" in data
    finally:
        r.close()


def test_redirects_and_cookies_are_made_relative_to_the_public_address(rig):
    token = rig.enter()
    resp, _ = rig.http("GET", "/redirect", cookie=token)
    assert resp.status == 302 and dict(resp.getheaders())["Location"] == "/target?a=1"
    resp, _ = rig.http("GET", "/cookie", cookie=token)
    set_cookie = dict(resp.getheaders())["Set-Cookie"]
    assert "PHPSESSID=abc" in set_cookie and "Domain" not in set_cookie and "HttpOnly" in set_cookie


def test_big_chunked_and_head_responses(rig):
    token = rig.enter()
    resp, data = rig.http("GET", "/big", cookie=token)
    assert hashlib.sha256(data).digest() == hashlib.sha256(BIG).digest()
    resp, data = rig.http("GET", "/chunked", cookie=token)
    assert data == b"hello chunked world"
    resp, data = rig.http("HEAD", "/", cookie=token)
    assert resp.status == 200 and data == b""


def test_app_down_is_a_clear_502(app):
    r = Rig(app)
    try:
        token = r.enter()
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        port = dead.getsockname()[1]
        dead.close()
        r.share.target = port
        resp, data = r.http("GET", "/", cookie=token)
        assert resp.status == 502 and b"not answering" in data
    finally:
        r.close()


def test_revoke_ends_everything_at_once(rig):
    token = rig.enter()
    assert rig.http("GET", "/echo", cookie=token)[0].status == 200
    rig.manager.revoke(rig.share.id)
    assert rig.http("GET", "/echo", cookie=token)[0].status == 401
    resp, body, _ = rig.claim()
    assert resp.status == 410 and body["error"] == "ended"
    assert json.loads(rig.http("GET", "/_mesh/status", cookie=token)[1])["state"] == "ended"


def test_revoke_cuts_a_page_that_is_streaming_right_now(rig):
    token = rig.enter()
    conn = http.client.HTTPConnection("127.0.0.1", rig.port, timeout=5)
    conn.request("GET", "/stream", headers={"Host": HOST, "Cookie": f"{ms.COOKIE}={token}"})
    resp = conn.getresponse()
    assert resp.status == 200 and resp.read(5) == b"hello"
    rig.manager.revoke(rig.share.id)
    deadline = time.time() + 5
    ended = False
    while time.time() < deadline:
        try:
            if not resp.read(5):
                ended = True
                break
        except (http.client.HTTPException, OSError):
            ended = True
            break
    assert ended, "the stream kept going after the share was revoked"
    conn.close()


def test_expiry_through_the_gateway(rig):
    token = rig.enter()
    assert rig.http("GET", "/echo", cookie=token)[0].status == 200
    rig.clock.advance(1801)
    assert rig.http("GET", "/echo", cookie=token)[0].status == 401


def test_leave_and_close_endpoints(rig):
    token = rig.enter()
    resp, _ = rig.http("POST", "/_mesh/leave", body="{}", cookie=token)
    assert resp.status == 200 and "Max-Age=0" in dict(resp.getheaders())["Set-Cookie"]
    assert rig.http("GET", "/echo", cookie=token)[0].status == 401
    resp, body, token2 = rig.claim()
    assert resp.status == 200 and body["state"] == "pending"                                    # back in only with a new approval
    rig.manager.approve(rig.share.id)
    assert rig.http("POST", "/_mesh/close", body="{}", cookie=token2)[0].status == 200
    assert rig.share.state == "ended" and rig.claim()[0].status == 410


def test_oversize_and_chunked_request_bodies_are_refused(rig):
    token = rig.enter()
    resp, _ = rig.http("POST", "/save", body="x", headers={"Transfer-Encoding": "chunked", "Content-Length": "1"}, cookie=token)
    assert resp.status == 411


# ---------------------------------------------------------------- Funnel, with a fake tailscale

TS_OK = json.dumps({"BackendState": "Running", "Self": {"DNSName": "mkz.tail1234.ts.net."}})


class FakeTailscale:
    def __init__(self, status=TS_OK, funnel=(0, "Available on the internet:\n\nhttps://mkz.tail1234.ts.net/\n|-- proxy http://127.0.0.1:5555\n"), timeout=False):
        self.calls, self.status, self.funnel, self.timeout = [], status, funnel, timeout

    def __call__(self, args, capture_output=True, text=True, timeout=None):
        self.calls.append(list(args[1:]))
        sub = args[1:]
        if sub[:1] == ["status"]:
            return subprocess.CompletedProcess(args, 0, self.status, "")
        if sub[:1] == ["funnel"] and "off" not in sub and "status" not in sub:
            if self.timeout:
                raise subprocess.TimeoutExpired(args, timeout, output=self.funnel[1].encode())
            code, text_out = self.funnel
            return subprocess.CompletedProcess(args, code, text_out, "")
        return subprocess.CompletedProcess(args, 0, "", "")


def test_funnel_start_and_stop_commands():
    fake = FakeTailscale()
    tunnel = ms.FunnelTunnel(443, runner=fake, which=lambda n: "/usr/bin/tailscale")
    assert tunnel.start(5555) == "https://mkz.tail1234.ts.net"
    assert ["funnel", "--bg", "--yes", "--https=443", "http://127.0.0.1:5555"] in fake.calls
    tunnel.stop()
    assert fake.calls[-1] == ["funnel", "--https=443", "http://127.0.0.1:5555", "off"]


def test_funnel_other_port_in_the_address():
    tunnel = ms.FunnelTunnel(8443, runner=FakeTailscale(), which=lambda n: "tailscale")
    assert tunnel.start(5555) == "https://mkz.tail1234.ts.net:8443"
    with pytest.raises(ms.TunnelError):
        ms.FunnelTunnel(8080)


def test_funnel_never_touches_other_shares_when_it_never_started():
    fake = FakeTailscale()
    ms.FunnelTunnel(443, runner=fake, which=lambda n: "tailscale").stop()
    assert fake.calls == []


def test_funnel_needs_approval_link():
    out = "Funnel is not enabled on your tailnet.\nTo enable, visit:\n\n         https://login.tailscale.com/f/funnel?node=nABC123\n"
    fake = FakeTailscale(funnel=(1, out))
    with pytest.raises(ms.TunnelError) as err:
        ms.FunnelTunnel(443, runner=fake, which=lambda n: "tailscale").start(5555)
    assert err.value.url == "https://login.tailscale.com/f/funnel?node=nABC123" and "approve" in str(err.value)


def test_funnel_waiting_for_approval_is_reported_not_hung():
    out = "Funnel is not enabled on your tailnet.\nTo enable, visit: https://login.tailscale.com/f/funnel?node=nABC123\n"
    with pytest.raises(ms.TunnelError) as err:
        ms.FunnelTunnel(443, runner=FakeTailscale(funnel=(1, out), timeout=True), which=lambda n: "tailscale").start(5555)
    assert err.value.url


def test_funnel_slow_first_time_and_failures():
    with pytest.raises(ms.TunnelError) as err:
        ms.FunnelTunnel(443, runner=FakeTailscale(funnel=(0, "setting up"), timeout=True), which=lambda n: "tailscale").start(5555)
    assert "again" in str(err.value)
    with pytest.raises(ms.TunnelError) as err:
        ms.FunnelTunnel(443, runner=FakeTailscale(funnel=(1, "boom")), which=lambda n: "tailscale").start(5555)
    assert "boom" in str(err.value)


def test_funnel_when_tailscale_is_down_or_missing():
    down = json.dumps({"BackendState": "Stopped"})
    with pytest.raises(ms.TunnelError, match="Stopped"):
        ms.FunnelTunnel(443, runner=FakeTailscale(status=down), which=lambda n: "tailscale").start(1)
    nodns = json.dumps({"BackendState": "Running", "Self": {"DNSName": ""}})
    with pytest.raises(ms.TunnelError, match="MagicDNS"):
        ms.FunnelTunnel(443, runner=FakeTailscale(status=nodns), which=lambda n: "tailscale").start(1)

    def missing(args, **kw):
        raise FileNotFoundError("tailscale")
    with pytest.raises(ms.TunnelError, match="not installed"):
        ms.FunnelTunnel(443, runner=missing, which=lambda n: None).start(1)


def test_funnel_old_cli_without_yes_flag_retries():
    calls = []

    def runner(args, **kw):
        calls.append(list(args[1:]))
        if args[1:2] == ["status"]:
            return subprocess.CompletedProcess(args, 0, TS_OK, "")
        if "--yes" in args:
            return subprocess.CompletedProcess(args, 2, "", "flag provided but not defined: -yes")
        return subprocess.CompletedProcess(args, 0, "ok", "")
    assert ms.FunnelTunnel(443, runner=runner, which=lambda n: "tailscale").start(5) == "https://mkz.tail1234.ts.net"
    assert ["funnel", "--bg", "--https=443", "http://127.0.0.1:5"] in calls


# ---------------------------------------------------------------- the service, the control socket and the CLI

@pytest.fixture()
def home(tmp_path):
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    return {"dir": str(tmp_path / "cfg"), "environ": {"XDG_RUNTIME_DIR": str(runtime)}}


def test_service_end_to_end_with_control_socket(app, home):
    lines = []
    service = ms.ShareService(home["dir"], ms.NoTunnel(), say=lines.append, environ=home["environ"])
    info = service.open_web(app.server_address[1], ttl=600)
    try:
        assert info["link"].startswith("http://127.0.0.1:") and "/_mesh/g/" in info["link"] and "#" in info["link"]
        assert os.stat(ms.control_path(home["dir"], home["environ"])).st_mode & 0o777 == 0o600
        link_path, secret = info["link"].split("#")
        gw_port = int(link_path.split(":")[2].split("/")[0])
        share_id = link_path.rsplit("/", 1)[1]
        # a visitor opens the link
        conn = http.client.HTTPConnection("127.0.0.1", gw_port, timeout=5)
        conn.request("POST", f"/_mesh/g/{share_id}/claim", body=json.dumps({"secret": secret}), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        token = resp.getheader("Set-Cookie").split("=", 1)[1].split(";")[0]
        # the owner sees and approves it from another terminal
        listed = ms.control_request(home["dir"], {"cmd": "list"}, home["environ"])
        assert listed["shares"][0]["visitor"]["state"] == "pending" and listed["shares"][0]["visitor"]["code"] == body["code"]
        assert any("The code on their screen" in line for line in lines)
        assert ms.control_request(home["dir"], {"cmd": "approve", "code": "bad"}, home["environ"])["ok"] is False
        assert ms.control_request(home["dir"], {"cmd": "approve", "code": body["code"]}, home["environ"])["ok"] is True
        conn.request("GET", "/echo", headers={"Cookie": f"{ms.COOKIE}={token}"})
        assert conn.getresponse().status == 200
        # a second share is refused while this one is open
        second = ms.ShareService(home["dir"], ms.NoTunnel(), say=lambda s: None, environ=home["environ"])
        with pytest.raises(ms.ShareError, match="already open"):
            second.open_web(app.server_address[1])
        # the owner revokes it from the CLI
        done = threading.Event()
        threading.Thread(target=lambda: (service.wait(), done.set()), daemon=True).start()
        out = []
        assert ms.run_control(ms.build_parser(home["dir"]).parse_args(["revoke"]), home["environ"], say=out.append) == 0
        assert done.wait(5)
    finally:
        service.close()
    assert ms.control_request(home["dir"], {"cmd": "list"}, home["environ"]) is None
    assert not os.path.exists(service.state_path)
    log = open(service.log_path).read()
    assert "request" in log and "approved" in log and "revoked" in log and secret not in log


def test_service_refuses_when_nothing_listens(home):
    service = ms.ShareService(home["dir"], ms.NoTunnel(), say=lambda s: None, environ=home["environ"])
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(ms.ShareError, match="Nothing answers"):
        service.open_web(port)


def test_funnel_is_closed_again_when_the_share_ends(app, home):
    fake = FakeTailscale()
    tunnel = ms.FunnelTunnel(443, runner=fake, which=lambda n: "tailscale")
    service = ms.ShareService(home["dir"], tunnel, say=lambda s: None, environ=home["environ"])
    info = service.open_web(app.server_address[1])
    assert info["link"].startswith("https://mkz.tail1234.ts.net/_mesh/g/")
    service.close()
    assert fake.calls[-1][:2] == ["funnel", "--https=443"] and fake.calls[-1][-1] == "off"


def test_a_failed_funnel_leaves_nothing_open(app, home):
    out = "visit: https://login.tailscale.com/f/funnel?node=n1"
    fake = FakeTailscale(funnel=(1, out))
    service = ms.ShareService(home["dir"], ms.FunnelTunnel(443, runner=fake, which=lambda n: "tailscale"), say=lambda s: None, environ=home["environ"])
    with pytest.raises(ms.TunnelError):
        service.open_web(app.server_address[1])
    assert ms.control_request(home["dir"], {"cmd": "list"}, home["environ"]) is None and not os.path.exists(service.state_path)


def test_stale_public_address_from_a_killed_share_is_closed(home):
    os.makedirs(home["dir"], exist_ok=True)
    state = os.path.join(home["dir"], "share.json")
    with open(state, "w") as f:
        json.dump({"pid": 2 ** 22 + 12345, "tunnel": "funnel", "https_port": 443, "local_port": 4242, "started": 1}, f)
    fake = FakeTailscale()
    service = ms.ShareService(home["dir"], ms.FunnelTunnel(443, runner=fake, which=lambda n: "tailscale"), say=lambda s: None, environ=home["environ"])
    service.cleanup_stale()
    assert ["funnel", "--https=443", "http://127.0.0.1:4242", "off"] in fake.calls and not os.path.exists(state)


def test_revoke_with_no_share_closes_leftovers(home):
    out = []
    os.makedirs(home["dir"], exist_ok=True)
    assert ms.run_control(ms.build_parser(home["dir"]).parse_args(["revoke"]), home["environ"], say=out.append) == 0
    assert out == ["No share is open."]


def test_cli_list_and_approve_when_nothing_is_open(home):
    out = []
    parser = ms.build_parser(home["dir"])
    assert ms.run_control(parser.parse_args(["list"]), home["environ"], say=out.append) == 0 and out[-1] == "No share is open."
    assert ms.run_control(parser.parse_args(["approve"]), home["environ"], say=out.append) == 1
    assert ms.run_control(parser.parse_args(["deny"]), home["environ"], say=out.append) == 1


def test_cli_approve_asks_first(app, home):
    service = ms.ShareService(home["dir"], ms.NoTunnel(), say=lambda s: None, environ=home["environ"])
    info = service.open_web(app.server_address[1])
    try:
        link_path, secret = info["link"].split("#")
        gw_port = int(link_path.split(":")[2].split("/")[0])
        conn = http.client.HTTPConnection("127.0.0.1", gw_port, timeout=5)
        conn.request("POST", f"/_mesh/g/{link_path.rsplit('/', 1)[1]}/claim", body=json.dumps({"secret": secret}), headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        parser = ms.build_parser(home["dir"])
        out = []
        assert ms.run_control(parser.parse_args(["approve"]), home["environ"], say=out.append, ask=lambda q: "n") == 1
        assert any("The code on their screen should be" in line for line in out) and service.manager.pending()
        assert ms.run_control(parser.parse_args(["approve"]), home["environ"], say=out.append, ask=lambda q: "y") == 0
        assert not service.manager.pending()
    finally:
        service.close()


def test_run_web_prints_the_link_and_ends_on_revoke(app, home, capsys):
    args = ms.build_parser(home["dir"]).parse_args(["web", str(app.server_address[1]), "--tunnel", "none", "--ttl", "5m", "--no-qr"])
    out = []
    result = {}

    def go():
        result["code"] = ms.run_web(args, environ=home["environ"], say=out.append, interactive=False)
    t = threading.Thread(target=go)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and ms.control_request(home["dir"], {"cmd": "list"}, home["environ"]) is None:
        time.sleep(0.05)
    assert ms.control_request(home["dir"], {"cmd": "revoke"}, home["environ"])["ok"]
    t.join(5)
    assert result["code"] == 0
    text = "\n".join(out)
    assert "/_mesh/g/" in text and "The share is closed" in text


def test_run_web_reports_a_funnel_that_is_not_approved(app, home):
    out = []
    fake = FakeTailscale(funnel=(1, "visit: https://login.tailscale.com/f/funnel?node=n1"))
    args = ms.build_parser(home["dir"]).parse_args(["web", str(app.server_address[1])])
    code = ms.run_web(args, environ=home["environ"], tunnel=ms.FunnelTunnel(443, runner=fake, which=lambda n: "tailscale"), say=out.append, interactive=False)
    assert code == 1 and any("https://login.tailscale.com/f/funnel" in line for line in out)


def test_meshd_dispatches_share(home, capsys, monkeypatch):
    import meshd
    monkeypatch.setenv("XDG_CONFIG_HOME", home["dir"])
    monkeypatch.setenv("XDG_RUNTIME_DIR", home["environ"]["XDG_RUNTIME_DIR"])
    assert meshd.main(["share", "list"]) == 0
    assert "No share is open." in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        meshd.main(["share", "-h"])
    assert stop.value.code == 0 and "web" in capsys.readouterr().out
    assert "meshd share web" in meshd.__doc__
