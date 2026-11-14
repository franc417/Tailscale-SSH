"""The screen share end to end: gateway + WebSocket + hub + input, with ffmpeg standing in for GStreamer (video and Opus are real).

The browser test at the bottom needs Playwright/Chromium and is skipped where they are missing (CI)."""

import base64
import http.client
import json
import os
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402
import meshshare as ms  # noqa: E402
import meshstream as st  # noqa: E402
import meshws  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


class OggParser:
    """Opus packets out of an Ogg stream (the first two packets are the headers)."""

    def __init__(self):
        self.buf, self.partial, self.count = b"", b"", 0

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 27:
            if self.buf[:4] != b"OggS":
                self.buf = self.buf[1:]
                continue
            nseg = self.buf[26]
            if len(self.buf) < 27 + nseg:
                break
            table = self.buf[27:27 + nseg]
            size = sum(table)
            if len(self.buf) < 27 + nseg + size:
                break
            body, pos = self.buf[27 + nseg:27 + nseg + size], 0
            self.buf = self.buf[27 + nseg + size:]
            for lace in table:
                self.partial += body[pos:pos + lace]
                pos += lace
                if lace < 255:
                    self.count += 1
                    if self.count > 2 and self.partial:
                        out.append(self.partial)
                    self.partial = b""
        return out


class FfmpegSource:
    """The Source the hub drives, with a moving test picture and a sine tone instead of the screen."""

    encoder = "ffmpeg libx264 (test)"

    def __init__(self, sound=True, width=320, height=240):
        self.width, self.height, self.sound = width, height, sound
        self.info = {"width": width, "height": height, "fps": 30, "audio": sound, "monitor": [0, 0, width, height], "desktop": [width, height]}
        self.started = self.stopped = 0
        self.procs = []
        self.stopping = False
        self.fail_start = None

    def start(self, on_video, on_audio, on_end):
        if self.fail_start:
            raise st.StreamError(self.fail_start)
        self.started += 1
        self.stopping = False
        video = subprocess.Popen([FFMPEG, "-v", "error", "-re", "-f", "lavfi", "-i", f"testsrc2=size={self.width}x{self.height}:rate=30", "-c:v", "libx264", "-preset", "ultrafast",
                                  "-tune", "zerolatency", "-g", "30", "-bf", "0", "-pix_fmt", "yuv420p", "-f", "h264", "-"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        self.procs = [video]
        threading.Thread(target=self._video, args=(video, on_video), daemon=True).start()
        if self.sound:
            audio = subprocess.Popen([FFMPEG, "-v", "error", "-re", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-ac", "2", "-c:a", "libopus", "-b:a", "64k",
                                      "-frame_duration", "10", "-f", "ogg", "-"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
            self.procs.append(audio)
            threading.Thread(target=self._audio, args=(audio, on_audio), daemon=True).start()

    def _video(self, proc, on_video):
        sp, fd = st.AccessUnitSplitter(), proc.stdout.fileno()
        while not self.stopping:
            ready, _, _ = select.select([fd], [], [], 0.015)
            if ready:
                data = os.read(fd, 65536)
                if not data:
                    return
                for au, key in sp.feed(data):
                    on_video(au, key)
            else:
                for au, key in sp.idle():
                    on_video(au, key)

    def _audio(self, proc, on_audio):
        ogg, fd = OggParser(), proc.stdout.fileno()
        while not self.stopping:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                data = os.read(fd, 4096)
                if not data:
                    return
                for packet in ogg.feed(data):
                    on_audio(packet)

    def stop(self):
        self.stopping = True
        self.stopped += 1
        for proc in self.procs:
            proc.terminate()
            try:
                proc.wait(2)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs = []


class WsClient:
    def __init__(self, port, cookie, origin=None, host=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        host = host or f"127.0.0.1:{port}"
        lines = ["GET /_mesh/ws HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}",
                 "Sec-WebSocket-Version: 13"]
        if origin:
            lines.append(f"Origin: {origin}")
        if cookie:
            lines.append(f"Cookie: {ms.COOKIE}={cookie}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        head, _, self.buf = raw.partition(b"\r\n\r\n")
        self.status = int(head.split(b" ")[1]) if head else 0

    def frame(self, timeout=5.0):
        """-> (opcode, payload) or None on timeout; raises EOFError when the server closed."""
        deadline = time.time() + timeout
        while True:
            if len(self.buf) >= 2:
                length, pos = self.buf[1] & 0x7F, 2
                if length == 126 and len(self.buf) >= 4:
                    length, pos = struct.unpack("!H", self.buf[2:4])[0], 4
                elif length == 127 and len(self.buf) >= 10:
                    length, pos = struct.unpack("!Q", self.buf[2:10])[0], 10
                if not (self.buf[1] & 0x7F in (126, 127) and pos == 2) and len(self.buf) >= pos + length:
                    op, payload = self.buf[0] & 0x0F, self.buf[pos:pos + length]
                    self.buf = self.buf[pos + length:]
                    return op, payload
            left = deadline - time.time()
            if left <= 0:
                return None
            ready, _, _ = select.select([self.sock], [], [], left)
            if not ready:
                return None
            data = self.sock.recv(65536)
            if not data:
                raise EOFError("closed")
            self.buf += data

    def send_json(self, obj):
        self.sock.sendall(meshws.encode_frame(meshws.OP_TEXT, json.dumps(obj).encode(), os.urandom(4)))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def collect(ws, want_video=0, want_audio=0, timeout=10.0):
    video, audio, texts = [], [], []
    deadline = time.time() + timeout
    while time.time() < deadline and (len(video) < want_video or len(audio) < want_audio):
        try:
            got = ws.frame(max(0.1, deadline - time.time()))
        except EOFError:                 # the server closed (after its last message)
            break
        if got is None:
            continue
        op, payload = got
        if op == meshws.OP_TEXT:
            texts.append(json.loads(payload))
        elif op == meshws.OP_BINARY:
            (video if payload[0] == 1 else audio).append(payload)
    return video, audio, texts


class Rig:
    def __init__(self, tmp_path, sound=True, view_only=False):
        runtime = tmp_path / "run"
        runtime.mkdir(mode=0o700)
        self.environ = {"XDG_RUNTIME_DIR": str(runtime)}
        self.dir = str(tmp_path / "cfg")
        self.lines = []
        self.source = FfmpegSource(sound=sound)
        self.injector = mi.RecordingInjector()
        self.service = ms.ShareService(self.dir, ms.NoTunnel(), say=self.lines.append, environ=self.environ)
        self.hub = st.StreamHub(self.source, lambda: self.injector, say=self.lines.append, view_only=view_only)
        info = self.service.open_screen(self.hub, ttl=600)
        self.link = info["link"]
        path, self.secret = self.link.split("#")
        self.port = int(path.split(":")[2].split("/")[0])
        self.share_id = path.rsplit("/", 1)[1]

    def enter(self, approve=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", f"/_mesh/g/{self.share_id}/claim", body=json.dumps({"secret": self.secret}), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        token = (resp.getheader("Set-Cookie") or "").split("=", 1)[-1].split(";")[0]
        if approve:
            assert self.service.manager.approve(self.share_id, body["code"])[0]
        return token

    def close(self):
        self.service.close()


@pytest.fixture()
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


def test_screen_stream_over_websocket(rig):
    token = rig.enter()
    ws = WsClient(rig.port, token)
    try:
        assert ws.status == 101
        video, audio, texts = collect(ws, want_video=20, want_audio=5)
        assert texts[0]["t"] == "info" and texts[0]["width"] == 320 and texts[0]["height"] == 240 and texts[0]["audio"] is True and texts[0]["codec"] == "h264"
        assert len(video) >= 20 and len(audio) >= 5
        assert video[0][1] == 1                                                    # the first picture is a key frame
        first = video[0][6:]
        types = {st.nal_type(n) for n in st.split_nals(first)}
        assert {7, 8, 5} <= types                                                  # with its parameter sets, so the browser can start at once
        assert all(v[1] in (0, 1) for v in video) and all(len(a) > 8 for a in audio)
        # the guest's mouse, keys and wheel
        for msg in ({"t": "m", "x": 32768, "y": 16384}, {"t": "b", "b": 1, "d": 1}, {"t": "b", "b": 1, "d": 0}, {"t": "k", "c": "KeyH", "d": 1}, {"t": "k", "c": "KeyH", "d": 0},
                    {"t": "w", "v": -2, "h": 0}, {"t": "k", "c": "ControlLeft", "d": 1}, {"t": "k", "c": "AltLeft", "d": 1}, {"t": "k", "c": "F3", "d": 1}):
            ws.send_json(msg)
        deadline = time.time() + 5
        while len(rig.injector.events) < 8 and time.time() < deadline:
            time.sleep(0.02)
        events = rig.injector.events
        assert events[0] == ("moveabs", 32768, 16384) and ("button", 1, True) in events and ("key", "KeyH", True) in events and ("scroll", -2.0, 0.0) in events
        assert not any(e[:2] == ("key", "F3") for e in events)                      # Ctrl+Alt+F3 is refused
        ws.send_json({"t": "p", "ts": 123})
        _, _, texts = collect(ws, want_video=10**6, timeout=1.0)
        assert {"t": "pong", "ts": 123} in texts
    finally:
        ws.close()


def test_nobody_gets_the_stream_without_a_session_or_from_another_site(rig):
    assert WsClient(rig.port, "").status == 403
    assert WsClient(rig.port, "nottoken").status == 403
    token = rig.enter(approve=False)                                              # waiting for approval: still no stream
    assert WsClient(rig.port, token).status == 403
    rig.service.manager.approve(rig.share_id)
    assert WsClient(rig.port, token, origin="https://evil.example").status == 403   # a page on another site cannot drive it (cross-site hijack)
    ok = WsClient(rig.port, token, origin=f"http://127.0.0.1:{rig.port}")
    assert ok.status == 101
    ok.close()
    assert rig.source.started <= 1


def test_capture_runs_only_while_someone_watches(rig):
    assert rig.source.started == 0
    token = rig.enter()
    ws = WsClient(rig.port, token)
    collect(ws, want_video=3)
    assert rig.source.started == 1 and rig.hub.running
    ws.close()
    deadline = time.time() + 6
    while rig.source.stopped < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert rig.source.stopped == 1 and not rig.hub.running                         # the recording stops when the guest goes away
    ws2 = WsClient(rig.port, token)                                                  # the same guest can reconnect (Wi-Fi blip)
    video, _, _ = collect(ws2, want_video=3)
    assert len(video) >= 3 and rig.source.started == 2
    ws2.close()


def test_revoke_cuts_the_stream_and_stops_capture(rig):
    token = rig.enter()
    ws = WsClient(rig.port, token)
    collect(ws, want_video=3)
    rig.service.manager.revoke(rig.share_id)
    with pytest.raises(EOFError):
        for _ in range(400):
            ws.frame(0.5)
    assert WsClient(rig.port, token).status == 403
    deadline = time.time() + 6
    while rig.source.stopped < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert rig.source.stopped >= 1 and rig.injector.keys_down == set()
    ws.close()


def test_owner_pause_and_view_only(rig):
    token = rig.enter()
    ws = WsClient(rig.port, token)
    try:
        collect(ws, want_video=5)
        reply = rig.service.control({"cmd": "viewonly", "value": True})
        assert reply["ok"]
        _, _, texts = collect(ws, want_video=10 ** 6, timeout=1.0)
        assert any(t.get("t") == "info" and t["view_only"] is True for t in texts)
        before = len(rig.injector.events)
        ws.send_json({"t": "k", "c": "KeyZ", "d": 1})
        time.sleep(0.3)
        assert len(rig.injector.events) == before                                    # view only: keys are ignored
        rig.service.control({"cmd": "viewonly", "value": False})
        rig.service.control({"cmd": "pause"})
        time.sleep(0.4)
        collect(ws, want_video=10 ** 6, timeout=0.6)                                 # drain
        video, audio, texts = collect(ws, want_video=10 ** 6, timeout=1.0)
        assert video == [] and audio == []                                           # paused: a frozen picture, no new frames
        assert rig.service.control({"cmd": "list"})["shares"][0]["paused"] is True
        rig.service.control({"cmd": "resume"})
        video, _, _ = collect(ws, want_video=5)
        assert len(video) >= 5
    finally:
        ws.close()


def test_start_failure_tells_the_guest_and_the_owner(tmp_path):
    r = Rig(tmp_path)
    try:
        r.source.fail_start = "gjs is not installed"
        token = r.enter()
        ws = WsClient(r.port, token)
        _, _, texts = collect(ws, want_video=1, timeout=3.0)
        assert texts and texts[0]["t"] == "end" and "meshd share doctor" in texts[0]["why"]
        assert any("Could not start the screen stream: gjs is not installed" in line for line in r.lines)
        ws.close()
    finally:
        r.close()


def test_without_uinput_permission_the_guest_can_only_watch(tmp_path):
    r = Rig(tmp_path)
    try:
        def denied():
            raise PermissionError("no permission to open /dev/uinput")
        r.hub.injector_factory = denied
        token = r.enter()
        ws = WsClient(r.port, token)
        video, _, texts = collect(ws, want_video=3)
        assert len(video) >= 3 and texts[0]["view_only"] is True
        ws.send_json({"t": "k", "c": "KeyA", "d": 1})
        time.sleep(0.2)
        assert r.injector.events == []
        ws.close()
        assert any("watch but not type" in line for line in r.lines)
    finally:
        r.close()


def test_guest_page_and_assets(rig):
    token = rig.enter()
    conn = http.client.HTTPConnection("127.0.0.1", rig.port, timeout=5)
    conn.request("GET", "/", headers={"Cookie": f"{ms.COOKIE}={token}"})
    resp = conn.getresponse()
    body = resp.read().decode()
    csp = resp.getheader("Content-Security-Policy")
    assert resp.status == 200 and 'id="screen"' in body and "script-src 'self'" in csp and f"ws://127.0.0.1:{rig.port}" in csp
    conn.request("GET", "/some/other/path.php", headers={"Cookie": f"{ms.COOKIE}={token}"})
    assert 'id="screen"' in conn.getresponse().read().decode()                      # any path shows the screen, there is no app behind it
    conn.request("POST", "/", body="x", headers={"Cookie": f"{ms.COOKIE}={token}"})
    assert conn.getresponse().status == 405
    for path, marker in (("/_mesh/screen.js", "VideoDecoder"), ("/_mesh/screen.css", "canvas")):
        c = http.client.HTTPConnection("127.0.0.1", rig.port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        assert r.status == 200 and marker in r.read().decode()
    c = http.client.HTTPConnection("127.0.0.1", rig.port, timeout=5)
    c.request("GET", "/")
    assert c.getresponse().status == 401


def test_cli_screen_prints_the_link_and_ends_on_revoke(tmp_path):
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    environ = {"XDG_RUNTIME_DIR": str(runtime)}
    directory = str(tmp_path / "cfg")
    args = ms.build_parser(directory).parse_args(["screen", "--tunnel", "none", "--quality", "text", "--no-sound", "--no-qr"])
    out, result = [], {}
    src = FfmpegSource(sound=False)
    t = threading.Thread(target=lambda: result.update(code=ms.run_screen(args, environ=environ, say=out.append, interactive=False, source=src, injector_factory=lambda: mi.RecordingInjector())))
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and ms.control_request(directory, {"cmd": "list"}, environ) is None:
        time.sleep(0.05)
    listed = ms.control_request(directory, {"cmd": "list"}, environ)["shares"][0]
    assert listed["kind"] == "screen" and listed["quality"] == "text"
    assert "screen" in ms.format_list([listed])
    assert ms.control_request(directory, {"cmd": "revoke"}, environ)["ok"]
    t.join(8)
    assert result["code"] == 0 and any("Sharing this computer's screen" in line for line in out) and any("/_mesh/g/" in line for line in out)


def test_lock_on_end_locks_the_screen(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ms.subprocess, "run", lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(ms.shutil, "which", lambda n: "/usr/bin/" + n)
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    service = ms.ShareService(str(tmp_path / "cfg"), ms.NoTunnel(), say=lambda s: None, environ={"XDG_RUNTIME_DIR": str(runtime)})
    hub = st.StreamHub(FfmpegSource(), lambda: mi.RecordingInjector(), say=lambda s: None)
    service.open_screen(hub, lock_on_end=True)
    service.close()
    assert ["loginctl", "lock-sessions"] in calls


# ---------------------------------------------------------------- the guest page in a real browser

try:
    from playwright import sync_api
except ImportError:  # CI has no browser: the two browser tests are skipped, everything above still runs
    sync_api = None


def until(page, script, ok, timeout=15.0):
    """Poll page.evaluate (works under the page's strict CSP, unlike wait_for_function which evals a string)."""
    deadline = time.time() + timeout
    value = None
    while time.time() < deadline:
        value = page.evaluate(script)
        if ok(value):
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting on {script}: last value {value!r}")


@pytest.fixture()
def browser():
    if sync_api is None:
        pytest.skip("Playwright is not installed")
    try:
        pw = sync_api.sync_playwright().start()
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Chromium here: {exc}")
    yield b
    b.close()
    pw.stop()


def test_guest_watches_and_controls_in_a_real_browser(rig, browser):
    page = browser.new_page(viewport={"width": 1000, "height": 700})
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(rig.link)
    page.wait_for_selector("#code:not(:empty)", timeout=5000)
    code = page.inner_text("#code")
    ok, message = rig.service.manager.approve(rig.share_id, code)
    assert ok, message
    page.wait_for_selector("#go", timeout=8000)                                      # the shared screen's page, with its Start button
    assert page.is_visible("#go")
    page.click("#go")                                                                # a click: sound and input start
    until(page, "() => window.__mesh ? window.__mesh.frames : 0", lambda v: v > 10)
    until(page, "() => window.__mesh.audioChunks", lambda v: v > 3, 10)
    assert page.evaluate("window.__mesh.errors") == [] and not problems
    pixels = page.evaluate("""() => { const c = document.getElementById('screen'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                                       let lit = 0; for (let i = 0; i < d.length; i += 4) { if (d[i] + d[i+1] + d[i+2] > 60) lit++; } return [c.width, c.height, lit]; }""")
    assert pixels[0] == 320 and pixels[1] == 240 and pixels[2] > 5000                # a real picture was decoded and drawn, not a black canvas
    # the page fits the canvas to the window and keeps the picture's shape
    box = page.eval_on_selector("#screen", "e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }")
    assert abs(box[0] / box[1] - 320 / 240) < 0.02 and box[0] <= 1000 and box[1] <= 700
    # mouse: the middle of the picture, then a click, the wheel, and keys
    rect = page.eval_on_selector("#screen", "e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }")
    page.mouse.move(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
    page.mouse.down()
    page.mouse.up()
    page.mouse.wheel(0, 100)
    page.keyboard.press("KeyA")
    page.keyboard.press("Enter")
    page.keyboard.down("Control")
    page.keyboard.press("c")
    page.keyboard.up("Control")
    deadline = time.time() + 5
    while len(rig.injector.events) < 12 and time.time() < deadline:
        time.sleep(0.05)
    events = rig.injector.events
    moves = [e for e in events if e[0] == "moveabs"]
    assert moves and abs(moves[0][1] - 32768) < 700 and abs(moves[0][2] - 32768) < 700   # the middle of the screen
    assert ("button", 1, True) in events and ("button", 1, False) in events
    assert any(e[0] == "scroll" and e[1] < 0 for e in events)                           # scrolling down sends a negative amount
    assert ("key", "Enter", True) in events and ("key", "ControlLeft", True) in events and ("key", "KeyC", True) in events
    # sound can be switched off and on again
    page.click("#snd")
    assert page.inner_text("#snd") == "Sound off"
    page.click("#snd")
    # Leave: the guest is out, the capture stops, the link stays open for a new approval
    page.click("#leave")
    page.wait_for_selector("#over:not([hidden])", timeout=5000)
    assert "You left" in page.inner_text("#why")
    deadline = time.time() + 6
    while rig.source.stopped < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert rig.source.stopped >= 1
    assert rig.service.manager.open_shares()
    page.close()


def test_guest_close_and_owner_revoke_in_a_browser(rig, browser, tmp_path_factory):
    page = browser.new_page()
    page.goto(rig.link)
    page.wait_for_selector("#code:not(:empty)", timeout=5000)
    rig.service.manager.approve(rig.share_id, page.inner_text("#code"))
    page.wait_for_selector("#go", timeout=8000)
    page.click("#go")
    until(page, "() => window.__mesh ? window.__mesh.frames : 0", lambda v: v > 3)
    rig.service.manager.revoke(rig.share_id)                                          # the owner ends it while the guest is watching
    page.wait_for_selector("#over:not([hidden])", timeout=8000)
    assert "owner" in page.inner_text("#why").lower() or "ended" in page.inner_text("#why").lower()
    page.close()
    r2 = Rig(tmp_path_factory.mktemp("second"))                                       # a fresh share to test the guest's own Close
    try:
        page = browser.new_page()
        page.goto(r2.link)
        page.wait_for_selector("#code:not(:empty)", timeout=5000)
        r2.service.manager.approve(r2.share_id, page.inner_text("#code"))
        page.wait_for_selector("#go", timeout=8000)
        page.click("#go")
        until(page, "() => window.__mesh ? window.__mesh.frames : 0", lambda v: v > 3)
        page.click("#close")
        page.wait_for_selector("#over:not([hidden])", timeout=5000)
        assert "no longer works" in page.inner_text("#why")
        assert r2.service.manager.open_shares() == []
        page.close()
    finally:
        r2.close()
