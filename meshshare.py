#!/usr/bin/env python3
"""meshshare - Mesh Guest: share something on this computer as a link that opens in any browser, then revoke it.

    meshd share web 80                 share the web app on port 80 (XAMPP, phpMyAdmin, your own site) through Tailscale Funnel
    meshd share web 8080 --ttl 20m     ... for 20 minutes (default 30 minutes, at most 8 hours)
    meshd share list                   what is open and who is connected
    meshd share approve [CODE]         let the person who just opened the link in (the code is shown on both screens)
    meshd share deny                   refuse them
    meshd share revoke [all]           end the share now: the link stops working and the public address is closed

The person who gets the link installs nothing. Nothing is public until you run the command; everything is closed again when
the share ends (revoked, expired, Ctrl+C, the terminal closing).

How the link is protected
  * The link is  https://<this computer>.ts.net/_mesh/g/<id>#<secret>. The secret is in the part after # that a browser never
    sends to a server, so it is not in any log, and a chat app that "previews" the link cannot use it up. The page removes it
    from the address bar as soon as it has read it.
  * Only a hash of the secret is kept. Opening the link makes a request; it is let in only when YOU approve it on the computer
    or phone (a 4 digit code is shown on both screens so you can tell it is the right person).
  * One person at a time. The link expires (TTL), and a person who is idle is let out. Leaving needs a new approval to return.
  * Only the Mesh gate answers on the public address, never the app directly: everything else needs the session cookie.

Standard library only.
"""

import argparse
import base64
import hashlib
import hmac
import http.client
import http.server
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import meshguestui  # noqa: E402
import meshws  # noqa: E402

GATE = "/_mesh"
COOKIE = "mesh_guest"
DEFAULT_TTL = 30 * 60
MAX_TTL = 8 * 3600
DEFAULT_IDLE = 15 * 60
PENDING_TTL = 5 * 60            # how long a request waits for you before it is dropped
MAX_BODY = 256 * 1024 * 1024     # biggest upload through the share
REWRITE_MAX = 8 * 1024 * 1024    # biggest text page whose http://localhost links are rewritten
FAIL_WINDOW = 600.0
FAIL_LIMIT = 8                   # wrong secrets from one address within the window
SHARE_FAIL_LIMIT = 24            # wrong secrets in total: the share closes itself
MAX_CONNECTIONS = 64

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
              "transfer-encoding", "upgrade"}
REWRITE_TYPES = ("text/html", "text/css", "text/javascript", "application/javascript", "application/json", "application/xhtml+xml",
                 "text/xml", "application/xml")

REASONS = {
    "left": "You left. Ask the owner for a new link if you need to come back.",
    "denied": "The owner did not allow this.",
    "revoked": "The owner ended this share.",
    "closed": "This share was closed.",
    "expired": "This link expired.",
    "idle": "You were away for too long. Ask the owner for a new link.",
    "timeout": "The owner did not answer in time. Open the link again.",
    "lockout": "Too many wrong attempts: this link was closed.",
}


# ---------------------------------------------------------------- the share, its secret, the one visitor

def new_secret() -> str:
    return secrets.token_urlsafe(32)


def new_id() -> str:
    return secrets.token_urlsafe(9)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_duration(text: str, default_unit: str = "m") -> int:
    """'90' -> 90 minutes, '30m', '2h', '45s' -> seconds."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(text).lower())
    if not match:
        raise ValueError(f"not a duration: {text!r} (examples: 30m, 2h, 90)")
    value, unit = float(match.group(1)), match.group(2) or default_unit
    return int(value * {"s": 1, "m": 60, "h": 3600}[unit])


def fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds} s"


def describe_agent(user_agent: str) -> str:
    ua = user_agent or ""
    browser = next((name for pattern, name in ((r"Edg/", "Edge"), (r"OPR/", "Opera"), (r"Firefox/", "Firefox"), (r"Chrome/", "Chrome"),
                                                (r"Safari/", "Safari")) if re.search(pattern, ua)), "a browser")
    system = next((name for pattern, name in ((r"Windows", "Windows"), (r"Android", "Android"), (r"iPhone|iPad", "iOS"),
                                               (r"Mac OS X|Macintosh", "macOS"), (r"CrOS", "ChromeOS"), (r"Linux|X11", "Linux"))
                   if re.search(pattern, ua)), "")
    return f"{browser} on {system}" if system else browser


@dataclass
class Visitor:
    token_hash: str
    code: str
    ip: str
    agent: str
    state: str            # pending | active | ended
    since: float
    last_seen: float
    reason: str = ""


@dataclass
class Share:
    id: str
    secret_hash: str
    kind: str
    target: int
    created: float
    expires: float
    idle: int
    read_only: bool
    state: str = "open"    # open | ended
    reason: str = ""
    visitor: Optional[Visitor] = None
    failures: int = 0


class ShareManager:
    """All the rules, no networking: the gateway, the control socket and the terminal all go through here."""

    def __init__(self, clock: Callable[[], float] = time.time, listener: Optional[Callable[[dict], None]] = None) -> None:
        self.clock = clock
        self.listener = listener
        self.lock = threading.RLock()
        self.shares: Dict[str, Share] = {}
        self.fails: Dict[str, List[float]] = {}
        self.conns: Dict[str, set] = {}

    # ---- events
    def emit(self, kind: str, share: Optional[Share] = None, **info) -> None:
        event = {"time": self.clock(), "event": kind, "share": share.id if share else None, **info}
        if self.listener:
            try:
                self.listener(event)
            except Exception:  # a broken screen must never break the rules
                pass

    # ---- creating and ending
    def create(self, kind: str, target: int, ttl: int = DEFAULT_TTL, idle: int = DEFAULT_IDLE, read_only: bool = False) -> Tuple[Share, str]:
        ttl = max(60, min(int(ttl), MAX_TTL))
        secret = new_secret()
        now = self.clock()
        share = Share(new_id(), digest(secret), kind, int(target), now, now + ttl, max(60, int(idle)), bool(read_only))
        with self.lock:
            self.shares[share.id] = share
        self.emit("opened", share, ttl=ttl, read_only=read_only)
        return share, secret

    def open_shares(self) -> List[Share]:
        with self.lock:
            return [s for s in self.shares.values() if s.state == "open"]

    def revoke(self, share_id: str, reason: str = "revoked") -> bool:
        with self.lock:
            share = self.shares.get(share_id)
            if not share or share.state != "open":
                return False
            share.state, share.reason = "ended", reason
            if share.visitor and share.visitor.state != "ended":
                share.visitor.state, share.visitor.reason = "ended", reason
            sockets = list(self.conns.pop(share_id, ()))
        for sock in sockets:           # cut the connections that are open right now, not only future ones
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.emit(reason if reason in ("expired", "lockout") else "revoked", share, reason=reason)
        return True

    def revoke_all(self, reason: str = "revoked") -> int:
        return sum(1 for s in self.open_shares() if self.revoke(s.id, reason))

    def reap(self) -> None:
        """Called every second: TTL, idle visitors, requests nobody answered."""
        now = self.clock()
        for share in self.open_shares():
            if now >= share.expires:
                self.revoke(share.id, "expired")
                continue
            visitor = share.visitor
            if not visitor or visitor.state == "ended":
                continue
            if visitor.state == "pending" and now - visitor.since > PENDING_TTL:
                self._end_visitor(share, "timeout")
            elif visitor.state == "active" and now - visitor.last_seen > share.idle:
                self._end_visitor(share, "idle")

    def _end_visitor(self, share: Share, reason: str) -> None:
        with self.lock:
            visitor = share.visitor
            if not visitor or visitor.state == "ended":
                return
            visitor.state, visitor.reason = "ended", reason
            sockets = list(self.conns.pop(share.id, ()))
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.emit(reason, share, ip=visitor.ip, agent=visitor.agent)

    # ---- the guest side
    def _locked_out(self, ip: str) -> bool:
        now = self.clock()
        recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
        self.fails[ip] = recent
        return len(recent) >= FAIL_LIMIT

    def claim(self, share_id: str, secret: str, token: str, ip: str, agent: str) -> Tuple[int, dict, Optional[str]]:
        """-> (http status, json, new session token or None)"""
        locked_share = None
        with self.lock:
            if self._locked_out(ip):
                return 429, {"error": "slow", "message": "Too many attempts. Wait a few minutes."}, None
            share = self.shares.get(share_id)
            # A visitor who already has a session may come back with the cookie alone (page reload while waiting, or after approval)
            if share and token and share.visitor and hmac.compare_digest(share.visitor.token_hash, digest(token)):
                if share.visitor.state != "ended":
                    return 200, {"state": share.visitor.state, "code": share.visitor.code}, None
                if not secret:
                    return 410, {"error": "ended", "message": REASONS.get(share.visitor.reason, "This share ended.")}, None
            expected = share.secret_hash if share else digest("-")
            good = hmac.compare_digest(digest(secret or ""), expected) and share is not None
            if not good:
                self.fails.setdefault(ip, []).append(self.clock())
                if share and share.state == "open":
                    share.failures += 1
                    if share.failures >= SHARE_FAIL_LIMIT:
                        locked_share = share
        if not good:
            if locked_share:
                self.revoke(locked_share.id, "lockout")
            return 403, {"error": "no", "message": "This link is not valid."}, None
        with self.lock:
            if share.state != "open":
                return 410, {"error": "ended", "message": REASONS.get(share.reason, "This link was ended.")}, None
            if self.clock() < share.expires:
                visitor = share.visitor
                if visitor and visitor.state in ("pending", "active"):
                    return 409, {"error": "busy", "message": "Someone else is using this link right now."}, None
                code = f"{secrets.randbelow(10000):04d}"
                new_token = secrets.token_urlsafe(32)
                now = self.clock()
                share.visitor = Visitor(digest(new_token), code, ip, agent, "pending", now, now)
                self.emit("request", share, ip=ip, agent=agent, code=code)
                return 200, {"state": "pending", "code": code}, new_token
        self.revoke(share.id, "expired")
        return 410, {"error": "ended", "message": REASONS["expired"]}, None

    def session(self, token: str) -> Optional[Tuple[Share, Visitor]]:
        if not token:
            return None
        hashed = digest(token)
        with self.lock:
            for share in self.shares.values():
                visitor = share.visitor
                if visitor and hmac.compare_digest(visitor.token_hash, hashed):
                    return share, visitor
        return None

    def touch(self, token: str) -> Optional[Tuple[Share, Visitor]]:
        """For a request through the share: returns the session only while it is active and in date."""
        self.reap()
        got = self.session(token)
        if not got:
            return None
        share, visitor = got
        if share.state != "open" or visitor.state != "active":
            return None
        visitor.last_seen = self.clock()
        return got

    def note_activity(self, token: str) -> None:
        got = self.session(token)
        if got and got[1].state == "active":
            got[1].last_seen = self.clock()

    def status(self, token: str) -> dict:
        got = self.session(token)
        if not got:
            return {"state": "none", "message": "This link is not active."}
        share, visitor = got
        if share.state != "open" and visitor.state != "ended":
            return {"state": "ended", "message": REASONS.get(share.reason, "This share ended.")}
        if visitor.state == "ended":
            return {"state": "ended", "message": REASONS.get(visitor.reason, "This share ended.")}
        return {"state": visitor.state, "code": visitor.code}

    def leave(self, token: str) -> bool:
        got = self.session(token)
        if not got or got[1].state == "ended":
            return False
        self._end_visitor(got[0], "left")
        return True

    def close(self, token: str) -> bool:
        got = self.session(token)
        if not got or got[0].state != "open" or got[1].state != "active":
            return False
        return self.revoke(got[0].id, "closed")

    # ---- the owner side
    def pending(self) -> List[Tuple[Share, Visitor]]:
        with self.lock:
            return [(s, s.visitor) for s in self.shares.values() if s.state == "open" and s.visitor and s.visitor.state == "pending"]

    def approve(self, share_id: Optional[str] = None, code: Optional[str] = None) -> Tuple[bool, str]:
        self.reap()
        waiting = [(s, v) for s, v in self.pending() if share_id in (None, s.id)]
        if code:
            waiting = [(s, v) for s, v in waiting if v.code == str(code).strip()]
        if not waiting:
            return False, "Nobody is waiting for approval" + (" with that code." if code else ".")
        if len(waiting) > 1:
            return False, "More than one request is waiting: give the share id."
        share, visitor = waiting[0]
        with self.lock:
            visitor.state, visitor.last_seen = "active", self.clock()
        self.emit("approved", share, ip=visitor.ip, agent=visitor.agent)
        return True, f"{visitor.agent} ({visitor.ip}) is in."

    def deny(self, share_id: Optional[str] = None) -> Tuple[bool, str]:
        waiting = [(s, v) for s, v in self.pending() if share_id in (None, s.id)]
        if not waiting:
            return False, "Nobody is waiting for approval."
        for share, _ in waiting:
            self._end_visitor(share, "denied")
        return True, "Refused."

    def describe(self) -> List[dict]:
        now = self.clock()
        rows = []
        with self.lock:
            for share in self.shares.values():
                if share.state != "open":
                    continue
                v = share.visitor
                rows.append({"id": share.id, "kind": share.kind, "target": share.target, "read_only": share.read_only,
                             "expires_in": max(0, int(share.expires - now)),
                             "visitor": ({"state": v.state, "ip": v.ip, "agent": v.agent, "code": v.code if v.state == "pending" else "",
                                          "idle_for": int(now - v.last_seen)} if v and v.state != "ended" else None)})
        return rows

    # ---- open connections, so a revoke can cut them
    def track(self, share_id: str, sock) -> None:
        with self.lock:
            self.conns.setdefault(share_id, set()).add(sock)

    def untrack(self, share_id: str, sock) -> None:
        with self.lock:
            self.conns.get(share_id, set()).discard(sock)


# ---------------------------------------------------------------- the pages the guest sees

LANDING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Mesh</title><link rel="stylesheet" href="/_mesh/landing.css"></head>
<body><main><h1>Mesh</h1><p id="msg">Opening the shared screen...</p><p id="code"></p>
<p class="dim" id="hint">The owner has to allow this on their computer or phone.</p></main>
<script src="/_mesh/landing.js"></script></body></html>
"""

LANDING_CSS = """html,body{margin:0;height:100%;background:#000;color:#fff;font-family:ui-monospace,Menlo,Consolas,monospace}
main{max-width:30rem;margin:0 auto;padding:18vh 1.5rem 2rem;text-align:center}
h1{font-size:1.1rem;letter-spacing:.3em;text-transform:uppercase;margin:0 0 2rem}
#code{font-size:3.2rem;letter-spacing:.35em;margin:1.2rem 0;min-height:1em}
.dim{color:#888;font-size:.85rem}
"""

LANDING_JS = """(function () {
  var parts = location.pathname.split('/');
  var id = parts[parts.length - 1];
  var secret = location.hash.slice(1);
  if (secret) { history.replaceState(null, '', location.pathname); }
  var msg = document.getElementById('msg'), code = document.getElementById('code'), hint = document.getElementById('hint');
  function show(text, c, h) { msg.textContent = text; code.textContent = c || ''; hint.style.visibility = h ? 'visible' : 'hidden'; }
  function poll() {
    fetch('/_mesh/status', {credentials: 'same-origin', cache: 'no-store'})
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.state === 'active') { location.replace('/'); return; }
        if (s.state === 'pending') { show('Waiting for the owner to allow this. Tell them this code:', s.code, true); setTimeout(poll, 1200); return; }
        show(s.message || 'This link is not active.', '', false);
      })
      .catch(function () { show('Cannot reach the computer. Trying again...', '', false); setTimeout(poll, 2500); });
  }
  fetch('/_mesh/g/' + encodeURIComponent(id) + '/claim', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({secret: secret})})
    .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
    .then(function (x) { if (!x.ok) { show(x.j.message || 'This link is not valid.', '', false); return; } poll(); })
    .catch(function () { show('Cannot reach the computer.', '', false); });
})();
"""

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}


def notice_page(title: str, text: str) -> bytes:
    body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Mesh</title>"
            f"<link rel=stylesheet href=/_mesh/landing.css><main><h1>Mesh</h1><p>{title}</p><p class=dim>{text}</p></main>")
    return body.encode()


# ---------------------------------------------------------------- the gateway: gate + reverse proxy

def _loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def client_ip(peer: str, forwarded: str) -> str:
    """Behind Funnel the TCP peer is the local Tailscale daemon; the real address is the last X-Forwarded-For entry it added."""
    if _loopback(peer) and forwarded:
        last = forwarded.split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            pass
    return peer


def cookie_value(header: Optional[str], name: str) -> str:
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def strip_cookie(header: Optional[str], name: str) -> str:
    kept = [p.strip() for p in (header or "").split(";") if p.strip() and p.strip().partition("=")[0] != name]
    return "; ".join(kept)


def rewrite_text(data: bytes, public_origin: str, port: int) -> bytes:
    """Links the app hard-codes to http://localhost[:port] (common in student projects) point to the public address instead."""
    host = urllib.parse.urlsplit(public_origin).netloc
    local = r"(?:localhost|127\.0\.0\.1)"
    local_pat = rf"{local}(?::80)?(?![\w.:-])" if port == 80 else rf"{local}:{port}(?![\w.-])"
    pairs = [(rf"http://{local_pat}", public_origin), (rf"http://{re.escape(host)}(?![\w.-])", public_origin)]
    for pattern, replacement in pairs:
        plain, escaped = replacement.encode(), replacement.replace("/", "\\/").encode()
        data = re.sub(pattern.encode(), lambda _m, r=plain: r, data, flags=re.I)
        data = re.sub(pattern.replace("http://", r"http:\\/\\/").encode(), lambda _m, r=escaped: r, data, flags=re.I)
    return data


def rewrite_location(value: str, public_origin: str, port: int) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return value
    public_host = urllib.parse.urlsplit(public_origin).netloc.lower()
    host = parts.netloc.lower()
    local_hosts = {"localhost", "127.0.0.1", f"localhost:{port}", f"127.0.0.1:{port}"}
    if port == 80:
        local_hosts |= {"localhost:80", "127.0.0.1:80"}
    if host == public_host or host in local_hosts:
        return urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))
    return value


class _Limited:
    """A request body of known length, read through to the app without holding it all in memory."""

    def __init__(self, rfile, length: int) -> None:
        self.rfile, self.left = rfile, length

    def read(self, size: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        size = self.left if size is None or size < 0 else min(size, self.left)
        chunk = self.rfile.read(size)
        self.left -= len(chunk)
        return chunk


class GatewayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, handler, manager: ShareManager) -> None:
        super().__init__(address, handler)
        self.manager = manager
        self.public_origin = f"http://127.0.0.1:{address[1]}"
        self.hub = None       # the screen stream, for shares of kind "screen"
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address) -> None:
        if not self.slots.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class GatewayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30
    server_version = "mesh"
    sys_version = ""

    def log_message(self, fmt, *args) -> None:  # the audit trail is the manager's events, not request lines (they hold the app's URLs)
        pass

    # ---- plumbing
    @property
    def manager(self) -> ShareManager:
        return self.server.manager

    def reply(self, status: int, body: bytes = b"", content_type: str = "text/html; charset=utf-8", extra: Optional[List[Tuple[str, str]]] = None,
              headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response_only(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(key, value)
        if status >= 400:
            self.send_header("Connection", "close")
        for key, value in extra or []:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reply_json(self, status: int, data: dict, extra: Optional[List[Tuple[str, str]]] = None) -> None:
        self.reply(status, json.dumps(data).encode(), "application/json", extra)

    def ip(self) -> str:
        return client_ip(self.client_address[0], self.headers.get("X-Forwarded-For", ""))

    def token(self) -> str:
        return cookie_value(self.headers.get("Cookie"), COOKIE)

    def read_json(self) -> Optional[dict]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > 4096:
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # ---- the verbs all go the same way
    def do_GET(self): self.route()
    def do_HEAD(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()
    def do_PATCH(self): self.route()
    def do_DELETE(self): self.route()
    def do_OPTIONS(self): self.route()

    def route(self) -> None:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith(GATE + "/") or path == GATE:
            return self.gate(path)
        return self.proxy()

    # ---- the gate
    def gate(self, path: str) -> None:
        method = self.command
        if method in ("GET", "HEAD"):
            if path == GATE + "/landing.js":
                return self.reply(200, LANDING_JS.encode(), "text/javascript; charset=utf-8")
            if path == GATE + "/landing.css":
                return self.reply(200, LANDING_CSS.encode(), "text/css; charset=utf-8")
            if path == GATE + "/screen.js":
                return self.reply(200, meshguestui.SCREEN_JS.encode(), "text/javascript; charset=utf-8")
            if path == GATE + "/screen.css":
                return self.reply(200, meshguestui.SCREEN_CSS.encode(), "text/css; charset=utf-8")
            if path == GATE + "/ws":
                return self.websocket()
            if path == GATE + "/status":
                return self.reply_json(200, self.manager.status(self.token()))
            if re.fullmatch(re.escape(GATE) + r"/g/[A-Za-z0-9_-]{1,64}", path):
                return self.reply(200, LANDING_HTML.encode())
            return self.reply(404, notice_page("Not found", ""))
        if method == "POST":
            claim = re.fullmatch(re.escape(GATE) + r"/g/([A-Za-z0-9_-]{1,64})/claim", path)
            if claim:
                data = self.read_json()
                if data is None:
                    return self.reply_json(400, {"error": "bad", "message": "Bad request."})
                secret = data.get("secret")
                secret = secret if isinstance(secret, str) else ""
                status, body, token = self.manager.claim(claim.group(1), secret, self.token(), self.ip(), describe_agent(self.headers.get("User-Agent", "")))
                extra = []
                if token:
                    share = self.manager.shares.get(claim.group(1))
                    age = max(60, int(share.expires - self.manager.clock())) if share else 3600
                    secure = "; Secure" if self.server.public_origin.startswith("https://") else ""
                    extra.append(("Set-Cookie", f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}"))
                return self.reply_json(status, body, extra)
            if path == GATE + "/leave":
                done = self.manager.leave(self.token())
                return self.reply_json(200 if done else 403, {"ok": done}, [("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0")])
            if path == GATE + "/close":
                done = self.manager.close(self.token())
                return self.reply_json(200 if done else 403, {"ok": done}, [("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0")])
        return self.reply(404, notice_page("Not found", ""))

    # ---- the screen stream: a WebSocket the browser opens after Start
    def websocket(self) -> None:
        token = self.token()
        got = self.manager.touch(token)
        hub = self.server.hub
        if not got or got[0].kind != "screen" or hub is None:
            return self.reply(403, b"", "text/plain")
        if not meshws.is_upgrade(self.headers):
            return self.reply(400, b"", "text/plain")
        origin, host = self.headers.get("Origin"), (self.headers.get("Host") or "").lower()
        if origin and urllib.parse.urlsplit(origin).netloc.lower() != host:      # another site's page may not drive this screen
            return self.reply(403, b"", "text/plain")
        share = got[0]
        self.close_connection = True
        self.wfile.write(meshws.handshake_response(self.headers["Sec-WebSocket-Key"]))
        self.wfile.flush()
        ws = meshws.WebSocket(self.connection)
        self.manager.track(share.id, self.connection)

        def valid() -> bool:
            self.manager.reap()
            found = self.manager.session(token)
            return bool(found and found[0].state == "open" and found[1].state == "active")
        try:
            hub.serve(ws, valid, lambda: self.manager.note_activity(token))
        finally:
            self.manager.untrack(share.id, self.connection)

    # ---- the shared app
    def proxy(self) -> None:
        token = self.token()
        got = self.manager.touch(token)
        if not got:
            status = self.manager.status(token)
            wants_page = "text/html" in (self.headers.get("Accept") or "")
            if status.get("state") == "pending":
                share = self.manager.session(token)[0]
                return self.reply(302, b"", extra=[("Location", f"{GATE}/g/{share.id}")]) if wants_page else self.reply(401, b"", "text/plain")
            if wants_page:
                return self.reply(401, notice_page("This link is not active.", status.get("message", "")))
            return self.reply(401, b"", "text/plain")
        share, visitor = got
        if share.read_only and self.command not in ("GET", "HEAD", "OPTIONS"):
            return self.reply(405, notice_page("View only", "The owner shared this so it can be looked at, not changed."))
        if share.kind == "screen":
            if self.command not in ("GET", "HEAD"):
                return self.reply(405, notice_page("Not allowed", ""))
            host = self.headers.get("Host") or ""
            if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?", host):
                host = urllib.parse.urlsplit(self.server.public_origin).netloc
            return self.reply(200, meshguestui.SCREEN_HTML.encode(), headers={"Content-Security-Policy": meshguestui.screen_csp(host)})
        path = self.path
        if not path.startswith("/"):
            return self.reply(400, notice_page("Bad request", ""))
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            return self.reply(411, notice_page("Length required", "This browser sent a body without a length."))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.reply(400, notice_page("Bad request", ""))
        if length < 0 or length > MAX_BODY:
            return self.reply(413, notice_page("Too large", "That upload is bigger than this share allows."))

        self.manager.track(share.id, self.connection)
        upstream = None
        try:
            headers = {}
            for key, value in self.headers.items():
                low = key.lower()
                if low in HOP_BY_HOP or low in ("cookie", "accept-encoding", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded",
                                                "x-real-ip", "content-length", "expect"):
                    continue
                headers[key] = value
            rest = strip_cookie(self.headers.get("Cookie"), COOKIE)
            if rest:
                headers["Cookie"] = rest
            public_host = urllib.parse.urlsplit(self.server.public_origin).netloc
            headers["Host"] = self.headers.get("Host") or public_host
            headers["Accept-Encoding"] = "identity"
            headers["Connection"] = "close"
            headers["X-Forwarded-For"] = self.ip()
            headers["X-Forwarded-Proto"] = urllib.parse.urlsplit(self.server.public_origin).scheme
            headers["X-Forwarded-Host"] = headers["Host"]
            body = None
            if length:
                headers["Content-Length"] = str(length)
                body = _Limited(self.rfile, length)
            elif self.command in ("POST", "PUT", "PATCH"):
                headers["Content-Length"] = "0"
            upstream = http.client.HTTPConnection("127.0.0.1", share.target, timeout=60)
            try:
                upstream.request(self.command, path, body=body, headers=headers)
                resp = upstream.getresponse()
            except (ConnectionRefusedError, socket.timeout, OSError) as exc:
                code = 504 if isinstance(exc, socket.timeout) else 502
                return self.reply(code, notice_page("The shared app is not answering", f"Port {share.target} on the owner's computer did not respond."))
            self.relay(resp, share)
        except OSError:          # the guest went away, or the share was revoked while the page was streaming
            self.close_connection = True
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass
            self.manager.untrack(share.id, self.connection)

    def relay(self, resp: http.client.HTTPResponse, share: Share) -> None:
        origin = self.server.public_origin
        ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
        encoded = (resp.getheader("Content-Encoding") or "identity").lower() != "identity"
        no_body = self.command == "HEAD" or resp.status in (204, 304) or 100 <= resp.status < 200
        rewrite = (not no_body and not encoded and ctype in REWRITE_TYPES)
        data = None
        prefix = b""
        if rewrite:
            declared = resp.getheader("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > REWRITE_MAX:
                rewrite = False
            else:
                prefix = resp.read(REWRITE_MAX + 1)
                if len(prefix) <= REWRITE_MAX:
                    data = rewrite_text(prefix, origin, share.target)
                else:
                    rewrite = False
        self.send_response_only(resp.status, resp.reason)
        for key, value in resp.getheaders():
            low = key.lower()
            if low in HOP_BY_HOP or low == "content-length" or low == "server":
                continue
            if low == "location":
                value = rewrite_location(value, origin, share.target)
            elif low == "set-cookie":
                value = re.sub(r";\s*domain=[^;]*", "", value, flags=re.I)
            self.send_header(key, value)
        if no_body:
            declared = resp.getheader("Content-Length")
            if declared is not None and self.command == "HEAD":
                self.send_header("Content-Length", declared)
            self.end_headers()
            return
        if data is not None:
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        length = resp.getheader("Content-Length")
        if length is not None:
            self.send_header("Content-Length", length)
            self.end_headers()
            if prefix:
                self.wfile.write(prefix)
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            return
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        if prefix:
            self.wfile.write(b"%x\r\n" % len(prefix) + prefix + b"\r\n")
        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")


class Gateway:
    def __init__(self, manager: ShareManager, host: str = "127.0.0.1", port: int = 0) -> None:
        self.manager = manager
        self.server = GatewayServer((host, port), GatewayHandler, manager)
        self.thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def set_public_origin(self, origin: str) -> None:
        self.server.public_origin = origin.rstrip("/")

    def start(self) -> int:
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, name="mesh-gateway", daemon=True)
        self.thread.start()
        return self.port

    def stop(self) -> None:
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()


# ---------------------------------------------------------------- the public address

class TunnelError(Exception):
    def __init__(self, message: str, url: Optional[str] = None) -> None:
        super().__init__(message)
        self.url = url


APPROVE_URL = re.compile(r"https://login\.tailscale\.com/\S+")


class NoTunnel:
    """No public address: the link only works on this computer (for trying it out)."""
    kind = "none"

    def start(self, local_port: int) -> str:
        return f"http://127.0.0.1:{local_port}"

    def stop(self) -> None:
        pass


class FunnelTunnel:
    """Tailscale Funnel: https://<this computer>.<tailnet>.ts.net, TLS ends on this computer, open only while a share is."""
    kind = "funnel"

    def __init__(self, https_port: int = 443, runner=subprocess.run, which=shutil.which, timeout: float = 70.0) -> None:
        if https_port not in (443, 8443, 10000):
            raise TunnelError("Funnel only listens on 443, 8443 or 10000.")
        self.https_port, self.runner, self.which, self.timeout = https_port, runner, which, timeout
        self.local_port: Optional[int] = None
        self.started = False

    def ts(self, *args: str, timeout: Optional[float] = None) -> Tuple[int, str]:
        binary = self.which("tailscale") or "tailscale"
        try:
            done = self.runner([binary, *args], capture_output=True, text=True, timeout=timeout or 20.0)
        except subprocess.TimeoutExpired as exc:
            def text(value) -> str:
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            return 124, (text(exc.stdout) + text(exc.stderr)).strip()
        except OSError as exc:
            return 127, str(exc)
        return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()

    def origin(self) -> str:
        code, text = self.ts("status", "--json")
        if code == 127:
            raise TunnelError("Tailscale is not installed here. Run:  meshd install")
        try:
            data = json.loads(text)
        except ValueError:
            raise TunnelError(f"Tailscale did not answer: {text or 'no output'}") from None
        if data.get("BackendState") != "Running":
            raise TunnelError(f"Tailscale is {data.get('BackendState') or 'not running'} here. Run:  sudo tailscale up")
        dns = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
        if not dns:
            raise TunnelError("This computer has no Tailscale DNS name (MagicDNS is off). Turn on MagicDNS and HTTPS certificates in the Tailscale admin console.")
        return f"https://{dns}" + ("" if self.https_port == 443 else f":{self.https_port}")

    def start(self, local_port: int) -> str:
        origin = self.origin()
        self.local_port = local_port
        target = f"http://127.0.0.1:{local_port}"
        args = ["funnel", "--bg", "--yes", f"--https={self.https_port}", target]
        code, text = self.ts(*args, timeout=self.timeout)
        if "flag provided but not defined" in text and "yes" in text:
            code, text = self.ts("funnel", "--bg", f"--https={self.https_port}", target, timeout=self.timeout)
        link = APPROVE_URL.search(text)
        if link:
            self.stop(quiet=True)
            raise TunnelError("Tailscale needs a one-time approval before Funnel can be used. Open this link, approve, then run the command again:\n  "
                              + link.group(0).rstrip(".,)"), url=link.group(0))
        if code == 124:
            self.stop(quiet=True)
            raise TunnelError("Tailscale is still setting up the public address (the first time can take a minute). Run the command again.")
        if code != 0:
            raise TunnelError(text or f"tailscale funnel failed (exit {code})")
        self.started = True
        return origin

    def active(self) -> bool:
        code, text = self.ts("funnel", "status")
        return code == 0 and self.local_port is not None and f"127.0.0.1:{self.local_port}" in text

    def stop(self, quiet: bool = False) -> None:
        if self.local_port is None:      # never started: do not touch whatever else is shared on that port
            return
        self.ts("funnel", f"--https={self.https_port}", f"http://127.0.0.1:{self.local_port}", "off", timeout=20.0)
        self.started = False


def make_tunnel(kind: str, https_port: int = 443, **kwargs):
    if kind == "none":
        return NoTunnel()
    if kind == "funnel":
        return FunnelTunnel(https_port, **kwargs)
    raise TunnelError(f"unknown tunnel {kind!r} (funnel or none)")


# ---------------------------------------------------------------- control socket: the phone app and other terminals talk to the share through it

def control_path(directory: str, environ=os.environ) -> str:
    runtime = environ.get("XDG_RUNTIME_DIR")
    base = os.path.join(runtime, "mesh") if runtime and os.path.isdir(runtime) else directory
    os.makedirs(base, mode=0o700, exist_ok=True)
    return os.path.join(base, "share.sock")


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            if struct.unpack("3i", creds)[1] != os.getuid():
                return
        except (OSError, AttributeError):
            pass
        line = self.rfile.readline(65536)
        try:
            request = json.loads(line or b"{}")
            reply = self.server.service.control(request)
        except Exception as exc:  # noqa: BLE001 - tell the caller, never crash the share
            reply = {"ok": False, "message": f"error: {exc}"}
        self.wfile.write(json.dumps(reply).encode() + b"\n")


class ControlServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, service) -> None:
        self.service = service
        if os.path.exists(path):
            os.unlink(path)
        super().__init__(path, _ControlHandler)
        os.chmod(path, 0o600)
        self.path = path


def control_request(directory: str, payload: dict, environ=os.environ, timeout: float = 5.0) -> Optional[dict]:
    path = control_path(directory, environ)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
        sock.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf or b"{}")
    except (OSError, ValueError):
        return None
    finally:
        sock.close()


# ---------------------------------------------------------------- the service: one share, from opening to closing

class ShareError(Exception):
    pass


class ShareService:
    def __init__(self, directory: str, tunnel, say: Callable[[str], None] = print, clock: Callable[[], float] = time.time,
                 environ=os.environ) -> None:
        self.directory, self.tunnel, self.say, self.clock, self.environ = directory, tunnel, say, clock, environ
        self.manager = ShareManager(clock=clock, listener=self.on_event)
        self.gateway = Gateway(self.manager)
        self.control_server: Optional[ControlServer] = None
        self.hub = None
        self.lock_on_end = False
        self.stop_event = threading.Event()
        self.link = ""
        self.info: dict = {}
        self.closed = False
        os.makedirs(directory, mode=0o700, exist_ok=True)
        self.state_path = os.path.join(directory, "share.json")
        self.log_path = os.path.join(directory, "share.log")

    # ---- events -> screen and audit log
    def on_event(self, event: dict) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({k: v for k, v in event.items() if k != "code"}) + "\n")
            os.chmod(self.log_path, 0o600)
        except OSError:
            pass
        line = describe_event(event)
        if line:
            self.say(line)

    # ---- opening
    def open_web(self, port: int, ttl: int = DEFAULT_TTL, idle: int = DEFAULT_IDLE, read_only: bool = False) -> dict:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0):
                pass
        except OSError:
            raise ShareError(f"Nothing answers on port {port} of this computer. Start the web app first, or run  meshd share web  without a port to see what is running.") from None
        return self._open("web", port, ttl, idle, read_only)

    def open_screen(self, hub, ttl: int = DEFAULT_TTL, idle: int = DEFAULT_IDLE, lock_on_end: bool = False) -> dict:
        self.hub, self.lock_on_end = hub, lock_on_end
        return self._open("screen", 0, ttl, idle, False)

    def _open(self, kind: str, target: int, ttl: int, idle: int, read_only: bool) -> dict:
        existing = control_request(self.directory, {"cmd": "list"}, self.environ, timeout=1.0)
        if existing is not None:
            raise ShareError("A share is already open. See it with  meshd share list  or end it with  meshd share revoke.")
        self.cleanup_stale()
        share, secret = self.manager.create(kind, target, ttl, idle, read_only)
        try:
            self.gateway.server.hub = self.hub
            gw_port = self.gateway.start()
            origin = self.tunnel.start(gw_port)
            self.gateway.set_public_origin(origin)
            self.control_server = ControlServer(control_path(self.directory, self.environ), self)
            threading.Thread(target=self.control_server.serve_forever, kwargs={"poll_interval": 0.2}, name="mesh-share-control", daemon=True).start()
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "tunnel": self.tunnel.kind, "https_port": getattr(self.tunnel, "https_port", None),
                           "local_port": gw_port, "started": self.clock()}, f)
            os.chmod(self.state_path, 0o600)
        except Exception:
            self.close()
            raise
        self.link = f"{origin}{GATE}/g/{share.id}#{secret}"
        self.info = {"id": share.id, "link": self.link, "origin": origin, "expires": share.expires, "ttl": int(share.expires - share.created),
                     "read_only": share.read_only, "kind": kind}
        return self.info

    def cleanup_stale(self) -> None:
        """A previous run that was killed may have left the public address open: close it."""
        try:
            with open(self.state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return
        try:
            os.kill(int(state.get("pid", 0)), 0)
            alive = int(state.get("pid", 0)) != os.getpid()
        except (OSError, ValueError, TypeError):
            alive = False
        if alive:
            return
        if state.get("tunnel") == "funnel" and state.get("https_port"):
            stale = FunnelTunnel(int(state["https_port"]), runner=getattr(self.tunnel, "runner", subprocess.run), which=getattr(self.tunnel, "which", shutil.which))
            stale.local_port = state.get("local_port")
            stale.stop()
        try:
            os.unlink(self.state_path)
        except OSError:
            pass

    # ---- running
    def control(self, request: dict) -> dict:
        cmd = request.get("cmd")
        if cmd == "list":
            rows = self.manager.describe()
            if self.hub is not None:
                for row in rows:
                    if row["kind"] == "screen":
                        row.update({"paused": self.hub.paused, "view_only": self.hub.view_only, "quality": self.hub.quality, "streaming": self.hub.running})
            return {"ok": True, "shares": rows}
        if cmd in ("pause", "resume") and self.hub is not None:
            self.hub.set_paused(cmd == "pause")
            return {"ok": True, "message": "Paused: the guest sees a frozen picture and cannot type." if cmd == "pause" else "Resumed."}
        if cmd == "viewonly" and self.hub is not None:
            self.hub.set_view_only(bool(request.get("value")))
            return {"ok": True, "message": "View only: the guest can look but not type or click." if request.get("value") else "The guest can type and click again."}
        if cmd == "approve":
            ok, message = self.manager.approve(request.get("id"), request.get("code"))
            return {"ok": ok, "message": message}
        if cmd == "deny":
            ok, message = self.manager.deny(request.get("id"))
            return {"ok": ok, "message": message}
        if cmd == "revoke":
            target = request.get("id")
            count = self.manager.revoke_all("revoked") if target in (None, "all") else int(self.manager.revoke(str(target), "revoked"))
            if count:
                self.stop_event.set()
            return {"ok": bool(count), "message": "Ended." if count else "No such share."}
        return {"ok": False, "message": "unknown command"}

    def wait(self, poll: float = 0.5) -> None:
        while not self.stop_event.is_set():
            self.manager.reap()
            if not self.manager.open_shares():
                break
            self.stop_event.wait(poll)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.manager.revoke_all("revoked")
        try:
            self.tunnel.stop()          # the public address first
        except Exception:               # noqa: BLE001
            pass
        try:
            self.gateway.stop()
        except Exception:               # noqa: BLE001
            pass
        if self.hub is not None:
            try:
                self.hub.close()        # stops the screen capture and releases any key the guest was holding
            except Exception:           # noqa: BLE001
                pass
            if self.lock_on_end and shutil.which("loginctl"):
                try:
                    subprocess.run(["loginctl", "lock-sessions"], timeout=5, capture_output=True)
                except (OSError, subprocess.SubprocessError):
                    pass
        if self.control_server is not None:
            try:
                self.control_server.shutdown()
                self.control_server.server_close()
            except OSError:
                pass
            try:
                os.unlink(self.control_server.path)
            except OSError:
                pass
        try:
            os.unlink(self.state_path)
        except OSError:
            pass


def describe_event(event: dict) -> str:
    kind = event.get("event")
    who = f"{event.get('agent', 'someone')} ({event.get('ip', '?')})"
    if kind == "request":
        return (f"\n  Someone opened the link: {who}\n  The code on their screen is {event.get('code')}. "
                f"If it matches, allow them: type  a  then Enter here, or  meshd share approve {event.get('code')}  (deny: d)")
    return {
        "approved": f"  Allowed: {who}",
        "denied": f"  Refused: {who}",
        "left": f"  {who} left. The link is still open; a new visit needs your approval.",
        "idle": f"  {who} was idle and was let out.",
        "timeout": "  Nobody answered the request in time; it was dropped.",
        "expired": "  The link expired.",
        "lockout": "  Too many wrong attempts: the link was closed.",
        "revoked": "  The share was ended." if event.get("reason") == "revoked" else f"  The share was closed ({event.get('reason')}).",
    }.get(kind, "")


# ---------------------------------------------------------------- command line

def _stdin_commands(service: ShareService) -> None:
    for line in sys.stdin:
        word = line.strip().lower()
        if word in ("a", "y", "approve", "allow"):
            service.say("  " + service.manager.approve()[1])
        elif word in ("d", "n", "deny"):
            service.say("  " + service.manager.deny()[1])
        elif word in ("p", "pause", "resume") and service.hub is not None:
            service.hub.set_paused(not service.hub.paused)
            service.say("  Paused." if service.hub.paused else "  Resumed.")
        elif word in ("v", "viewonly") and service.hub is not None:
            service.hub.set_view_only(not service.hub.view_only)
            service.say("  View only." if service.hub.view_only else "  The guest can type and click.")
        elif word in ("l", "list"):
            service.say(format_list(service.manager.describe()))
        elif word in ("r", "q", "revoke", "quit", "end"):
            service.manager.revoke_all("revoked")
            service.stop_event.set()
            return


def format_list(rows: List[dict]) -> str:
    if not rows:
        return "No share is open."
    lines = []
    for row in rows:
        v = row.get("visitor")
        who = "nobody has opened it" if not v else (f"{v['agent']} ({v['ip']}): " + ("waiting for approval, code " + v["code"] if v["state"] == "pending" else f"in, idle {fmt_seconds(v['idle_for'])}"))
        if row["kind"] == "screen":
            what = "screen" + (" (paused)" if row.get("paused") else "") + (" (view only)" if row.get("view_only") else "") + (f", {row['quality']}" if row.get("quality") else "")
        else:
            what = f"web app on port {row['target']}" + (" (view only)" if row["read_only"] else "")
        lines.append(f"  {row['id']}  {what}  expires in {fmt_seconds(row['expires_in'])}  {who}")
    return "\n".join(lines)


def listening_ports(runner=subprocess.run) -> List[int]:
    """TCP ports something is listening on at this computer (from `ss`), without the ones that are never a web app."""
    try:
        done = runner(["ss", "-ltnH"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    ports = set()
    for line in (done.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        host, _, port = parts[3].rpartition(":")          # 127.0.0.1:80  0.0.0.0:80  *:80  [::]:80  [::1]:8080
        if port.isdigit() and (host.strip("[]") in ("*", "0.0.0.0", "::", "::1") or host.startswith("127.")):
            ports.add(int(port))
    return sorted(p for p in ports if p not in (22, 53, 631, 25, 111, 5353))


def answers_http(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"HEAD / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            return sock.recv(5) == b"HTTP/"
    except OSError:
        return False


def find_web_ports(runner=subprocess.run, probe: Callable[[int], bool] = answers_http) -> List[int]:
    return [port for port in listening_ports(runner) if probe(port)]


def run_web(args, environ=os.environ, tunnel=None, say: Callable[[str], None] = print, interactive: Optional[bool] = None,
            find: Callable[[], List[int]] = find_web_ports, ask: Callable[[str], str] = input) -> int:
    if args.port is None:
        found = find()
        if not found:
            say("No web server is answering on this computer.\n"
                "  Start one first, for example your PHP project folder:  php -S 127.0.0.1:8000 -t ~/your-project\n"
                "  or any folder:  python3 -m http.server 8000   then run  meshd share web 8000   (or give the port you use).")
            return 1
        if len(found) == 1:
            args.port = found[0]
            say(f"Found a web server on port {args.port}.")
        else:
            say("These ports answer as web servers: " + ", ".join(str(p) for p in found))
            try:
                args.port = int(ask("Which port should be shared? ").strip())
            except (ValueError, EOFError):
                say("Give the port: meshd share web PORT")
                return 2
    return _run(args, "web", environ, tunnel, say, interactive)


def run_screen(args, environ=os.environ, tunnel=None, say: Callable[[str], None] = print, interactive: Optional[bool] = None, source=None,
               injector_factory=None) -> int:
    return _run(args, "screen", environ, tunnel, say, interactive, source=source, injector_factory=injector_factory)


def _run(args, kind: str, environ, tunnel, say, interactive, source=None, injector_factory=None) -> int:
    import meshqr
    directory = args.dir
    try:
        ttl, idle = parse_duration(args.ttl), parse_duration(args.idle)
        tunnel = tunnel or make_tunnel(args.tunnel, args.https_port)
    except (ValueError, TunnelError) as exc:
        say(f"error: {exc}")
        return 2
    if ttl > MAX_TTL:
        say(f"The longest a share can stay open is {fmt_seconds(MAX_TTL)}.")
        ttl = MAX_TTL
    service = ShareService(directory, tunnel, say=say, environ=environ)
    try:
        if kind == "web":
            info = service.open_web(args.port, ttl, idle, args.read_only)
        else:
            import mesh_input as mi
            import meshstream
            try:
                source = source or meshstream.GstSource(directory, args.monitor or "", args.quality, args.sound, environ=environ)
            except meshstream.StreamError as exc:
                say(f"error: {exc}")
                return 2
            hub = meshstream.StreamHub(source, injector_factory or mi.UinputInjector, say=say, view_only=args.view_only, quality=args.quality)
            info = service.open_screen(hub, ttl, idle, args.lock_on_end)
    except (ShareError, TunnelError) as exc:
        say(f"error: {exc}")
        return 1

    def stop(_signum, _frame):
        service.manager.revoke_all("revoked")
        service.stop_event.set()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                previous[name] = signal.signal(getattr(signal, name), stop)
    if kind == "web":
        say(f"\nSharing the web app on port {args.port} for {fmt_seconds(info['ttl'])}" + (" (view only)" if args.read_only else "") + ".")
    else:
        say(f"\nSharing this computer's screen ({args.monitor or 'main monitor'}, quality {args.quality}, "
            f"{'with sound' if args.sound else 'no sound'}{', view only' if args.view_only else ''}) for {fmt_seconds(info['ttl'])}.")
    say(f"\n  {info['link']}\n")
    if args.qr and sys.stdout.isatty():
        try:
            matrix = meshqr.encode(info["link"].encode())
            say(meshqr.render_terminal(matrix, scale=1))
        except Exception:  # noqa: BLE001
            pass
    say("  Send the link to the person. Nothing opens until you allow it here (or from another terminal: meshd share approve).")
    say("  Ctrl+C, or  meshd share revoke  from anywhere, ends it and closes the public address." + (
        "\n  Note: the first visit after Funnel is switched on can fail for a few minutes while the new name spreads; try the link on your phone first." if tunnel.kind == "funnel" else ""))
    if (interactive if interactive is not None else sys.stdin.isatty()):
        threading.Thread(target=_stdin_commands, args=(service,), daemon=True).start()
    try:
        service.wait()
    finally:
        service.close()
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)
    say("\nThe share is closed. The link no longer works.")
    return 0


def run_control(args, environ=os.environ, say: Callable[[str], None] = print, ask: Callable[[str], str] = input) -> int:
    cmd = args.action
    if cmd in ("list", "status"):
        reply = control_request(args.dir, {"cmd": "list"}, environ)
        if reply is None:
            say("No share is open.")
            return 0
        say(format_list(reply.get("shares", [])))
        return 0
    if cmd == "approve":
        reply = control_request(args.dir, {"cmd": "list"}, environ)
        if reply is None:
            say("No share is open.")
            return 1
        waiting = [(r, r["visitor"]) for r in reply.get("shares", []) if r.get("visitor") and r["visitor"]["state"] == "pending"]
        if not waiting:
            say("Nobody is waiting for approval.")
            return 1
        if not args.code and not args.yes:
            for row, v in waiting:
                say(f"  {v['agent']} ({v['ip']}) is waiting. The code on their screen should be {v['code']}.")
            if ask("Allow? [y/N] ").strip().lower() not in ("y", "yes"):
                say("Not allowed.")
                return 1
        reply = control_request(args.dir, {"cmd": "approve", "code": args.code}, environ) or {"ok": False, "message": "The share stopped answering."}
        say(reply.get("message", ""))
        return 0 if reply.get("ok") else 1
    if cmd in ("pause", "resume"):
        reply = control_request(args.dir, {"cmd": cmd}, environ)
        say((reply or {}).get("message", "No share is open."))
        return 0 if reply and reply.get("ok") else 1
    if cmd == "viewonly":
        reply = control_request(args.dir, {"cmd": "viewonly", "value": args.value == "on"}, environ)
        say((reply or {}).get("message", "No share is open."))
        return 0 if reply and reply.get("ok") else 1
    if cmd == "deny":
        reply = control_request(args.dir, {"cmd": "deny"}, environ)
        say((reply or {}).get("message", "No share is open."))
        return 0 if reply and reply.get("ok") else 1
    if cmd == "revoke":
        reply = control_request(args.dir, {"cmd": "revoke", "id": args.target or "all"}, environ)
        if reply is None:
            closed = close_stale(args.dir, environ, say)
            say("No share is open." if not closed else "A leftover public address was closed.")
            return 0
        say(reply.get("message", ""))
        return 0 if reply.get("ok") else 1
    return 2


def close_stale(directory: str, environ=os.environ, say: Callable[[str], None] = print) -> bool:
    """revoke when no share answers: close a public address a killed share left behind."""
    service = ShareService(directory, NoTunnel(), say=say, environ=environ)
    before = os.path.exists(service.state_path)
    service.cleanup_stale()
    return before and not os.path.exists(service.state_path)


def build_parser(default_dir: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="meshd share", description="Share something on this computer as a link that opens in any browser.")
    ap.add_argument("--dir", default=default_dir, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("web", help="share a web app running on this computer (your PHP project, phpMyAdmin, any local site)")
    p.add_argument("port", type=int, nargs="?", default=None, help="the port the web app listens on; leave it out to see what is running")
    p.add_argument("--ttl", default="30m", help="how long the link works: 30m (default), 2h ... at most 8h")
    p.add_argument("--idle", default="15m", help="let the visitor out after this long without activity")
    p.add_argument("--read-only", action="store_true", help="the visitor can look but not submit forms or change anything")
    p.add_argument("--tunnel", choices=["funnel", "none"], default="funnel", help="funnel = public https address through Tailscale; none = this computer only (testing)")
    p.add_argument("--https-port", type=int, default=443, choices=[443, 8443, 10000], help="Funnel's public port")
    p.add_argument("--no-qr", dest="qr", action="store_false", help="do not draw the QR code")
    p = sub.add_parser("screen", help="share this computer's whole screen, with sound; the guest's keyboard and mouse control it")
    p.add_argument("--monitor", default="", help="which monitor (a connector name such as eDP-1); default: the main one")
    p.add_argument("--quality", choices=["text", "balanced", "smooth"], default="balanced", help="text = sharpest (most data), smooth = for a weak connection")
    p.add_argument("--no-sound", dest="sound", action="store_false", help="do not send this computer's sound")
    p.add_argument("--view-only", action="store_true", help="the guest can watch but not type or click (change later: meshd share viewonly on|off)")
    p.add_argument("--lock-on-end", action="store_true", help="lock this computer's screen when the share ends")
    p.add_argument("--ttl", default="30m", help="how long the link works: 30m (default) ... at most 8h")
    p.add_argument("--idle", default="20m", help="let the guest out after this long without typing or clicking")
    p.add_argument("--tunnel", choices=["funnel", "none"], default="funnel", help="funnel = public https address through Tailscale; none = this computer only (testing)")
    p.add_argument("--https-port", type=int, default=443, choices=[443, 8443, 10000], help="Funnel's public port")
    p.add_argument("--no-qr", dest="qr", action="store_false", help="do not draw the QR code")
    sub.add_parser("doctor", help="check what the screen share needs (GStreamer, encoder, sound, keyboard access) and what to install")
    sub.add_parser("pause", help="freeze the guest's picture and ignore their keys until resumed")
    sub.add_parser("resume", help="undo pause")
    p = sub.add_parser("viewonly", help="turn the guest's keyboard and mouse off or on")
    p.add_argument("value", choices=["on", "off"])
    sub.add_parser("list", help="what is open and who is connected")
    sub.add_parser("status", help="same as list")
    p = sub.add_parser("approve", help="let the waiting visitor in")
    p.add_argument("code", nargs="?", default=None, help="the 4 digit code on the visitor's screen")
    p.add_argument("--yes", action="store_true", help="do not ask")
    sub.add_parser("deny", help="refuse the waiting visitor")
    p = sub.add_parser("revoke", help="end the share now")
    p.add_argument("target", nargs="?", default=None, help="a share id, or all (default)")
    return ap


def main(argv: Optional[List[str]] = None, environ=os.environ, say: Callable[[str], None] = print) -> int:
    import mesh_input as mi
    args = build_parser(mi.default_dir()).parse_args(argv)
    if args.action == "web":
        return run_web(args, environ=environ, say=say)
    if args.action == "screen":
        return run_screen(args, environ=environ, say=say)
    if args.action == "doctor":
        import meshstream
        ok, lines = meshstream.doctor(environ=environ)
        say("Screen share check:\n" + "\n".join(lines))
        say("\nReady." if ok else "\nNot ready: fix the MISSING lines above, then run  meshd share doctor  again.")
        return 0 if ok else 1
    return run_control(args, environ=environ, say=say)


if __name__ == "__main__":
    sys.exit(main())
