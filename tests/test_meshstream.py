"""meshstream: cutting the encoder's stream into pictures, RTP framing, GStreamer pipelines, the guest's input rules, capture plumbing."""

import io
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402
import meshstream as st  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


def h264(frames=60, extra=(), aud=False, size="320x240"):
    cmd = [FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30", "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast",
           "-tune", "zerolatency", "-g", "30", "-bf", "0", *extra]
    if aud:
        cmd += ["-bsf:v", "h264_metadata=aud=insert"]
    cmd += ["-f", "h264", "-"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def chunks(data, rng, low=1, high=9000):
    pos = 0
    while pos < len(data):
        n = rng.randint(low, high)
        yield data[pos:pos + n]
        pos += n


# ---------------------------------------------------------------- frames

@needs_ffmpeg
@pytest.mark.parametrize("aud,extra", [(False, ()), (True, ()), (False, ("-x264-params", "slices=4")), (True, ("-x264-params", "slices=4"))])
def test_splitter_finds_every_picture_however_the_bytes_arrive(aud, extra):
    data = h264(60, extra, aud)
    for seed in (1, 2, 3):
        sp = st.AccessUnitSplitter()
        out = []
        for part in chunks(data, random.Random(seed)):
            out += sp.feed(part)
        out += sp.idle()
        assert len(out) == 60, (aud, extra, seed, len(out))
        assert [key for _, key in out].count(True) == 2 and out[0][1] and out[30][1]
        assert b"".join(au for au, _ in out).count(b"\x00\x00\x00\x01") >= 60
        types = [st.nal_type(n) for n in st.split_nals(out[0][0])]
        assert 7 in types and 8 in types and 5 in types                       # the first picture carries its parameter sets


@needs_ffmpeg
def test_splitter_idle_hands_out_the_last_picture_without_waiting_for_the_next():
    data = h264(3)
    sp = st.AccessUnitSplitter()
    out = sp.feed(data)
    assert len(out) in (1, 2)                 # a picture is complete only when the next one starts (an AUD, when the encoder writes one, starts it early)...
    out += sp.idle()
    assert len(out) == 3                      # ...or when nothing more arrives for a moment


def test_splitter_ignores_junk_before_the_first_start_code_and_empty_input():
    sp = st.AccessUnitSplitter()
    assert sp.feed(b"") == [] and sp.idle() == []
    nal = lambda t, body: b"\x00\x00\x00\x01" + bytes([0x60 | t]) + body      # noqa: E731
    stream = b"garbage" + nal(7, b"\x42\x00\x1e") + nal(8, b"\xce") + nal(5, b"\x88\x00") + nal(1, b"\x9a\x00")
    out = sp.feed(stream) + sp.idle()
    assert [key for _, key in out] == [True, False]
    assert [st.nal_type(n) for n in st.split_nals(out[0][0])] == [7, 8, 5]


# ---------------------------------------------------------------- RTP framing (what rtpstreampay writes)

def rtp(payload, cc=0, ext=False, pad=0):
    head = bytes([0x80 | (0x20 if pad else 0) | (0x10 if ext else 0) | cc, 111]) + struct.pack("!HII", 1, 2, 3) + b"\x00\x00\x00\x07" * cc
    if ext:
        head += struct.pack("!HH", 0xBEDE, 1) + b"\x00" * 4
    body = payload + (b"\x00" * (pad - 1) + bytes([pad]) if pad else b"")
    packet = head + body
    return struct.pack("!H", len(packet)) + packet


def test_rtp_stream_parser():
    parts = [b"opus-one", b"opus-two-longer", b"x"]
    stream = rtp(parts[0]) + rtp(parts[1], cc=2, ext=True) + rtp(parts[2], pad=4)
    for step in (1, 3, 1000):
        p = st.RtpStreamParser()
        out = []
        for i in range(0, len(stream), step):
            out += p.feed(stream[i:i + step])
        assert out == parts
    assert st.RtpStreamParser().feed(struct.pack("!H", 12) + b"\x00" * 12) == []        # not RTP version 2


# ---------------------------------------------------------------- GStreamer: properties and pipelines

PIPEWIRESRC = """Factory Details:
  Rank                     primary + 1 (257)
Pad Templates:
  SRC template: 'src'
Element Properties:

  always-copy         : Always copy the buffer and data
                        flags: readable, writable, deprecated
  do-timestamp        : Apply current stream time to buffers
  keepalive-time      : Periodically send last buffer (in milliseconds, 0 = disabled)
  name                : The name of the object
  path                : The source path to connect to (NULL = default)
  target-object       : The source name/serial to connect to (NULL = default)

Element Signals:
  none
"""


def test_parse_gst_inspect_reads_the_property_names():
    props = st.parse_gst_inspect(PIPEWIRESRC)
    assert {"keepalive-time", "do-timestamp", "path", "target-object", "name"} <= props and "Factory" not in props and "none" not in props


class FakeGst:
    """gst-inspect-1.0 with a chosen set of elements: name -> property names."""

    def __init__(self, elements):
        self.elements = elements
        self.calls = []

    def which(self, name):
        return "/usr/bin/" + name

    def run(self, args, capture_output=True, text=True, timeout=None):
        self.calls.append(args)
        if args[0] == "gst-inspect-1.0" and args[1] in self.elements:
            body = "Element Properties:\n\n" + "".join(f"  {p:<20}: x\n" for p in self.elements[args[1]]) + "\nElement Signals:\n"
            return subprocess.CompletedProcess(args, 0, body, "")
        if args[0] == "pactl":
            return subprocess.CompletedProcess(args, 0, "alsa_output.test\n", "")
        return subprocess.CompletedProcess(args, 1, "", "")

    def probe(self):
        return st.GstProbe(self.run, self.which)


COMMON = {"pipewiresrc": ["path", "do-timestamp", "keepalive-time", "target-object"], "h264parse": ["config-interval"], "videoconvert": [], "videoscale": []}


def test_pipeline_prefers_va_hardware_and_only_uses_properties_that_exist():
    gst = FakeGst({**COMMON, "vah264enc": ["bitrate", "key-int-max", "b-frames", "rate-control", "aud"], "vapostproc": [], "x264enc": ["bitrate"]})
    names = [name for name, _ in st.video_candidates(gst.probe(), 57, 1600, 900, 30, 3500)]
    assert names[0].startswith("vah264enc") and names[1].startswith("x264enc")
    argv = st.video_candidates(gst.probe(), 57, 1600, 900, 30, 3500)[0][1]
    text = " ".join(argv)
    assert "bitrate=3500" in text and "key-int-max=30" in text and "b-frames=0" in text and "aud=true" in text
    assert "target-usage" not in text                                    # this build has no such property: it is left out, not guessed
    assert "path=57" in text and "keepalive-time=100" in text and "width=1600,height=900" in text
    assert argv[:2] == ["gst-launch-1.0", "-q"] and argv[-3:] == ["fdsink", "fd=1", "sync=false"]


def test_pipeline_software_fallback_and_nothing_available():
    gst = FakeGst({**COMMON, "x264enc": ["bitrate", "key-int-max", "bframes", "tune", "speed-preset", "aud", "byte-stream"]})
    candidates = st.video_candidates(gst.probe(), 9, 1280, 720, 30, 2200)
    assert [n for n, _ in candidates] == ["x264enc (software)"]
    assert "tune=zerolatency" in " ".join(candidates[0][1]) and "format=I420" in " ".join(candidates[0][1])
    assert st.video_candidates(FakeGst({}).probe(), 9, 1280, 720, 30, 2200) == []
    assert st.video_candidates(FakeGst({**COMMON, "h264parse": None} if False else {k: v for k, v in COMMON.items() if k != "h264parse"}).probe(), 9, 1, 1, 30, 1) == []


def test_pipeline_uses_target_object_when_this_pipewiresrc_has_no_path():
    gst = FakeGst({**COMMON, "pipewiresrc": ["target-object", "do-timestamp"], "x264enc": ["bitrate"]})
    assert "target-object=12" in " ".join(st.video_candidates(gst.probe(), 12, 640, 360, 30, 1000)[0][1])


def test_audio_pipeline():
    elements = {"opusenc": ["bitrate", "frame-size", "audio-type"], "rtpopuspay": [], "rtpstreampay": [], "audioconvert": [], "audioresample": [],
                "pulsesrc": ["device", "buffer-time", "latency-time"], "pipewiresrc": ["do-timestamp"]}
    gst = FakeGst(elements)
    c = st.audio_candidates(gst.probe(), "alsa_output.test")
    assert [n for n, _ in c] == ["pulsesrc", "pipewiresrc (default output)"]
    text = " ".join(c[0][1])
    assert "device=alsa_output.test.monitor" in text and "frame-size=10" in text and "rtpstreampay" in text and "audio/x-raw,rate=48000,channels=2" in text
    assert [n for n, _ in st.audio_candidates(gst.probe(), None)] == ["pipewiresrc (default output)"]
    assert st.audio_candidates(FakeGst({"pulsesrc": []}).probe(), "x") == []          # no Opus: no sound, not a crash


def test_default_sink_from_pactl():
    gst = FakeGst({})
    assert st.default_sink(gst.run, gst.which) == "alsa_output.test"
    assert st.default_sink(gst.run, lambda n: None) is None


def test_fit_size():
    assert st.fit_size(2560, 1440, 1600, 900) == (1600, 900)
    assert st.fit_size(1366, 768, 1920, 1080) == (1366, 768)               # never enlarged
    assert st.fit_size(1920, 1200, 1600, 900) == (1440, 900)
    w, h = st.fit_size(1001, 563, 800, 800)
    assert w % 2 == 0 and h % 2 == 0
    assert st.fit_size(0, 0, 1600, 900) == (1600, 900)


def test_doctor_lists_what_is_missing_and_how_to_fix_it():
    gst = FakeGst({**COMMON, "x264enc": ["bitrate"]})
    ok, lines = st.doctor(gst.run, gst.which, {}, access=lambda p, m: False)
    text = "\n".join(lines)
    assert ok is False and "uinput" in text and "software encoder" in text and "gst-plugin-va" in text and "sound (Opus)" in text and "MISSING" in text
    full = FakeGst({**COMMON, "vah264enc": [], "vapostproc": [], "opusenc": [], "rtpopuspay": [], "rtpstreampay": [], "audioconvert": [], "audioresample": [],
                    "pulsesrc": [], "videorate": []})
    ok, lines = st.doctor(full.run, full.which, {}, access=lambda p, m: True)
    assert ok is True and "MISSING" not in "\n".join(lines)


# ---------------------------------------------------------------- the guest's input

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def guest(view_only=False):
    inj = mi.RecordingInjector()
    clock = Clock()
    return st.GuestInput(inj, mi.AbsoluteMap((1920, 0, 1920, 1080), (3840, 1080)), view_only, clock=clock), inj, clock


def test_guest_mouse_keys_and_wheel_reach_the_injector():
    g, inj, _ = guest()
    assert g.handle({"t": "m", "x": 0, "y": 65535})
    assert g.handle({"t": "b", "b": 1, "d": 1}) and g.handle({"t": "b", "b": 1, "d": 0})
    assert g.handle({"t": "w", "v": -1.5, "h": 0})
    assert g.handle({"t": "k", "c": "KeyA", "d": 1}) and g.handle({"t": "k", "c": "KeyA", "d": 0})
    assert inj.events[0] == ("moveabs", 32768, 65535)                         # placed inside the second monitor of two
    assert ("button", 1, True) in inj.events and ("button", 1, False) in inj.events
    assert ("scroll", -1.5, 0.0) in inj.events and ("key", "KeyA", True) in inj.events


def test_guest_input_is_validated():
    g, inj, _ = guest()
    bad = [{"t": "m", "x": "a", "y": 1}, {"t": "m", "x": True, "y": 1}, {"t": "b", "b": 9, "d": 1}, {"t": "b", "b": 1, "d": 5}, {"t": "w", "v": "x"},
           {"t": "k", "c": "NoSuchKey", "d": 1}, {"t": "k", "c": "ctrl", "d": 1}, {"t": "k", "c": 5, "d": 1}, {"t": "zzz"}, {}, {"t": "k", "c": "KeyA", "d": 2}]
    assert not any(g.handle(m) for m in bad)
    assert inj.events == []
    assert g.handle({"t": "m", "x": 10 ** 9, "y": -5}) and inj.events[-1][1:] != (None, None)
    assert g.handle({"t": "w", "v": 999, "h": -999}) and inj.events[-1] == ("scroll", 20.0, -20.0)      # clamped


def test_guest_cannot_switch_console_or_use_sysrq():
    g, inj, _ = guest()
    g.handle({"t": "k", "c": "ControlLeft", "d": 1})
    g.handle({"t": "k", "c": "AltLeft", "d": 1})
    assert not g.handle({"t": "k", "c": "F2", "d": 1})                          # Ctrl+Alt+F2 = another console
    assert not g.handle({"t": "k", "c": "PrintScreen", "d": 1})                 # Alt+SysRq
    assert g.handle({"t": "k", "c": "Tab", "d": 1})                             # Ctrl+Alt+Tab is fine
    assert not any(e[:2] == ("key", "F2") for e in inj.events)
    g.handle({"t": "k", "c": "ControlLeft", "d": 0})
    assert g.handle({"t": "k", "c": "F2", "d": 1})                              # Alt+F2 alone is allowed


def test_guest_view_only_pause_and_release():
    g, inj, _ = guest(view_only=True)
    assert not g.handle({"t": "k", "c": "KeyA", "d": 1}) and inj.events == []
    g.view_only = False
    g.handle({"t": "k", "c": "KeyA", "d": 1})
    g.paused = True
    assert not g.handle({"t": "m", "x": 1, "y": 1})
    g.handle({"t": "r"})
    assert ("key", "KeyA", False) in inj.events                                   # a held key is let go
    assert g.down == set()


def test_guest_input_rate_limit_refills_over_time():
    g, inj, clock = guest()
    sent = sum(g.handle({"t": "m", "x": 1, "y": 1}) for _ in range(st.INPUT_BURST + 100))
    assert sent == st.INPUT_BURST
    clock.t += 1.0
    assert g.handle({"t": "m", "x": 1, "y": 1})


# ---------------------------------------------------------------- one guest's queue

class NullWs:
    closed = False

    def __init__(self):
        self.sent = []

    def send_binary(self, data):
        self.sent.append(data)

    def send_text(self, text):
        self.sent.append(text)


def test_subscriber_waits_for_a_key_frame_and_drops_when_behind():
    sub = st.Subscriber(NullWs())
    sub.offer_video(b"delta", False)
    assert not sub.items                                                       # nothing is shown before a key frame
    sub.offer_video(b"key", True)
    sub.offer_video(b"d" * 10, False)
    assert [k for k, _ in sub.items] == [1, 1]
    sub.offer_video(b"x" * (st.MAX_BACKLOG + 1), True)                         # a huge key frame still goes in
    sub.offer_video(b"late", False)                                            # now the backlog is too big: frames are dropped...
    assert sub.waiting_key and sub.dropped == 1 and not any(k == 1 for k, _ in sub.items)
    sub.offer_video(b"key2", True)                                             # ...until the next key frame
    assert sub.items[-1] == (1, b"key2") and not sub.waiting_key
    sub.offer_audio(b"a")
    assert sub.items[-1] == (2, b"a")


def test_subscriber_sender_thread_sends_in_order_and_stops():
    ws = NullWs()
    sub = st.Subscriber(ws)
    sub.start()
    sub.put_text("hello")
    sub.offer_video(b"k", True)
    sub.offer_audio(b"a")
    deadline = time.time() + 3
    while len(ws.sent) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert ws.sent == ["hello", b"k", b"a"]
    sub.stop()


# ---------------------------------------------------------------- capture and source plumbing with stand-in processes

class FakeProc:
    """A real subprocess standing in for gjs / gst-launch."""

    def __init__(self, argv, **kw):
        self.proc = subprocess.Popen(argv, **kw)
        self.stdout = self.proc.stdout

    def __getattr__(self, name):
        return getattr(self.proc, name)


def python_popen(code):
    def popen(argv, **kw):
        kw.pop("preexec_fn", None)
        return FakeProc([sys.executable, "-u", "-c", code], **kw)
    return popen


HOLDER_OK = ("import json,time\nprint(json.dumps({'node': 57, 'connector': 'eDP-1', 'size': [1920, 1080], 'monitor': [0, 0, 1920, 1080], 'desktop': [1920, 1080]}), flush=True)\n"
             "time.sleep(60)")


def test_screen_capture_returns_the_node_and_stops_the_helper(tmp_path):
    cap = st.ScreenCapture(str(tmp_path), {"XDG_RUNTIME_DIR": str(tmp_path)}, popen=python_popen(HOLDER_OK), which=lambda n: "/usr/bin/gjs")
    info = cap.start("eDP-1", timeout=10)
    assert info["node"] == 57 and info["monitor"] == [0, 0, 1920, 1080] and cap.alive()
    assert "RecordMonitor" in open(tmp_path / "guest-holder.mjs").read()
    proc = cap.proc
    cap.stop()
    assert proc.poll() is not None and not cap.alive()


def test_screen_capture_reports_what_gnome_said(tmp_path):
    code = "import json\nprint(json.dumps({'error': 'no monitor called HDMI-9 (there is: eDP-1)'}), flush=True)"
    cap = st.ScreenCapture(str(tmp_path), {}, popen=python_popen(code), which=lambda n: "/usr/bin/gjs")
    with pytest.raises(st.StreamError, match="HDMI-9"):
        cap.start("HDMI-9", timeout=10)
    with pytest.raises(st.StreamError, match="gjs is not installed"):
        st.ScreenCapture(str(tmp_path), {}, which=lambda n: None).start()
    cap = st.ScreenCapture(str(tmp_path), {}, popen=python_popen("pass"), which=lambda n: "/usr/bin/gjs")
    with pytest.raises(st.StreamError, match="stopped at once"):
        cap.start(timeout=10)


@needs_ffmpeg
def test_gst_source_runs_the_pipeline_and_delivers_pictures(tmp_path):
    video = tmp_path / "v.h264"
    video.write_bytes(h264(45))
    gst = FakeGst({**COMMON, "x264enc": ["bitrate"]})
    attempts = []

    def popen(argv, **kw):
        kw.pop("preexec_fn", None)
        attempts.append(argv)
        return FakeProc([sys.executable, "-u", "-c", f"import sys,time\nd=open({str(video)!r},'rb').read()\nsys.stdout.buffer.write(d);sys.stdout.flush();time.sleep(30)"], **kw)

    cap = st.ScreenCapture(str(tmp_path), {}, popen=python_popen(HOLDER_OK), which=lambda n: "/usr/bin/gjs")
    src = st.GstSource(str(tmp_path), "", "smooth", sound=False, environ={"XDG_RUNTIME_DIR": str(tmp_path)}, runner=gst.run, popen=popen, which=gst.which, capture=cap)
    got, ended = [], []
    src.start(lambda au, key: got.append((au, key)), lambda p: None, ended.append)
    try:
        deadline = time.time() + 10
        while len(got) < 45 and time.time() < deadline:
            time.sleep(0.05)
        assert len(got) == 45 and got[0][1] and "x264enc" in src.encoder
        assert src.info["width"] == 1280 and src.info["height"] == 720 and src.info["fps"] == 30 and src.info["audio"] is False
        assert src.info["monitor"] == [0, 0, 1920, 1080]
        assert attempts[0][:2] == ["gst-launch-1.0", "-q"]
    finally:
        src.stop()
    assert not cap.alive()


def test_gst_source_falls_through_to_the_next_encoder_and_explains_total_failure(tmp_path):
    gst = FakeGst({**COMMON, "vah264enc": ["bitrate"], "vapostproc": [], "x264enc": ["bitrate"]})
    order = []

    def popen(argv, **kw):
        kw.pop("preexec_fn", None)
        order.append(" ".join(argv))
        if "vah264enc" in order[-1]:      # the hardware encoder dies at once (no VA-API driver)
            return FakeProc([sys.executable, "-c", "import sys;sys.stderr.write('no VA display');sys.exit(1)"], **kw)
        return FakeProc([sys.executable, "-u", "-c", "import sys,time\nsys.stdout.buffer.write(b'\\x00\\x00\\x00\\x01\\x67ab\\x00\\x00\\x00\\x01\\x68c\\x00\\x00\\x00\\x01\\x65\\x88d');sys.stdout.flush();time.sleep(30)"], **kw)

    cap = st.ScreenCapture(str(tmp_path), {}, popen=python_popen(HOLDER_OK), which=lambda n: "/usr/bin/gjs")
    src = st.GstSource(str(tmp_path), "", "balanced", False, {}, gst.run, popen, gst.which, cap)
    src.start(lambda *a: None, lambda p: None, lambda r: None)
    try:
        assert "x264enc" in src.encoder and "vah264enc" in order[0] and "x264enc" in order[1]
    finally:
        src.stop()
    broken = st.GstSource(str(tmp_path), "", "balanced", False, {}, FakeGst({**COMMON, "x264enc": ["bitrate"]}).run,
                          lambda argv, **kw: FakeProc([sys.executable, "-c", "import sys;sys.exit(1)"], **{k: v for k, v in kw.items() if k != "preexec_fn"}),
                          gst.which, st.ScreenCapture(str(tmp_path), {}, popen=python_popen(HOLDER_OK), which=lambda n: "/usr/bin/gjs"))
    with pytest.raises(st.StreamError, match="could not encode"):
        broken.start(lambda *a: None, lambda p: None, lambda r: None)
    assert not broken.capture.alive()                                         # the capture is released when starting fails
    with pytest.raises(st.StreamError, match="GStreamer is not installed"):
        st.GstSource(str(tmp_path), which=lambda n: None).start(lambda *a: None, lambda p: None, lambda r: None)
    with pytest.raises(st.StreamError):
        st.GstSource(str(tmp_path), quality="ultra")
