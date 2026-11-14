#!/usr/bin/env python3
"""meshstream - the media side of Mesh Guest: take this computer's screen and sound, encode them, hand them to the browser.

    screen  -> GNOME's screen-cast (one monitor, no dialog) -> PipeWire -> GStreamer -> H.264 (hardware when VA-API works)
    sound   -> the default output's monitor -> Opus
    browser -> keys, mouse and wheel come back as small JSON messages and are injected like the phone's

Standard library only; it drives `gjs` (the screen-cast session) and `gst-launch-1.0` (encoding) as separate processes, so a
crash in either one ends the stream, never the share. Capture runs only while a guest is connected.
"""

import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Set, Tuple

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# quality presets: widest picture, tallest picture, frames per second, video kilobits per second
PRESETS: Dict[str, Tuple[int, int, int, int]] = {
    "text": (1920, 1080, 30, 6000),         # sharp small print (code, tables), the most data
    "balanced": (1600, 900, 30, 3500),
    "smooth": (1280, 720, 30, 2200),        # for a weak connection
}
DEFAULT_QUALITY = "balanced"
MAX_BACKLOG = 1_500_000        # bytes waiting for one guest before frames are dropped until the next key frame
MAX_GOP_FRAMES = 150
INPUT_RATE = 600               # input events per second one guest may send (a bucket, so bursts are fine)
INPUT_BURST = 240


class StreamError(Exception):
    pass


# ---------------------------------------------------------------- H.264 byte stream -> whole pictures

START = b"\x00\x00\x01"


def nal_type(nal: bytes) -> int:
    return nal[0] & 0x1F if nal else 0


def split_nals(data: bytes) -> List[bytes]:
    """The NAL units of an Annex-B chunk, without start codes or trailing zero bytes."""
    out, pos = [], data.find(START)
    while pos != -1:
        begin = pos + 3
        nxt = data.find(START, begin)
        nal = data[begin:nxt if nxt != -1 else len(data)].rstrip(b"\x00")
        if nal:
            out.append(nal)
        pos = nxt
    return out


class AccessUnitSplitter:
    """Cuts an encoder's Annex-B byte stream into pictures (access units), however the bytes were chunked on the way.

    A picture ends when the next one starts: an access-unit delimiter, or parameter sets/SEI after the picture's slices,
    or a slice that begins at the top of the frame. idle() hands out the last picture when nothing more arrives for a moment."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.cur: List[bytes] = []
        self.has_vcl = False
        self.key = False

    def _flush(self, out: List[Tuple[bytes, bool]]) -> None:
        if self.cur:
            if self.has_vcl:
                out.append((b"".join(b"\x00\x00\x00\x01" + n for n in self.cur), self.key))
            self.cur, self.has_vcl, self.key = [], False, False

    def _add(self, nal: bytes, out: List[Tuple[bytes, bool]]) -> None:
        kind = nal_type(nal)
        if self.has_vcl:
            first_slice = kind in (1, 5) and len(nal) > 1 and bool(nal[1] & 0x80)      # first_mb_in_slice == 0
            if kind in (6, 7, 8, 9) or first_slice:
                self._flush(out)
        self.cur.append(nal)
        if kind in (1, 5):
            self.has_vcl = True
            if kind == 5:
                self.key = True

    def feed(self, data: bytes) -> List[Tuple[bytes, bool]]:
        self.buf += data
        out: List[Tuple[bytes, bool]] = []
        starts, pos = [], self.buf.find(START)
        while pos != -1:
            starts.append(pos)
            pos = self.buf.find(START, pos + 3)
        if len(starts) < 2:
            if starts and starts[0] > 0:
                del self.buf[:starts[0]]
            return out
        for i in range(len(starts) - 1):
            nal = bytes(self.buf[starts[i] + 3:starts[i + 1]]).rstrip(b"\x00")
            if nal:
                self._add(nal, out)
        del self.buf[:starts[-1]]
        return out

    def idle(self) -> List[Tuple[bytes, bool]]:
        """Nothing arrived for a moment: whatever is waiting is complete."""
        out: List[Tuple[bytes, bool]] = []
        if self.buf.startswith(START):
            nal = bytes(self.buf[3:]).rstrip(b"\x00")
            self.buf.clear()
            if nal:
                self._add(nal, out)
        self._flush(out)
        return out


class RtpStreamParser:
    """RFC 4571 framing (what rtpstreampay writes): 2-byte length, then one RTP packet. Yields each packet's payload."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> List[bytes]:
        self.buf += data
        out = []
        while len(self.buf) >= 2:
            size = struct.unpack("!H", bytes(self.buf[:2]))[0]
            if len(self.buf) < 2 + size:
                break
            packet = bytes(self.buf[2:2 + size])
            del self.buf[:2 + size]
            payload = self._payload(packet)
            if payload:
                out.append(payload)
        return out

    @staticmethod
    def _payload(packet: bytes) -> bytes:
        if len(packet) < 12 or packet[0] >> 6 != 2:
            return b""
        head = 12 + 4 * (packet[0] & 0x0F)
        if packet[0] & 0x10:                                   # header extension
            if len(packet) < head + 4:
                return b""
            head += 4 + 4 * struct.unpack("!H", packet[head + 2:head + 4])[0]
        payload = packet[head:]
        if packet[0] & 0x20 and payload:                       # padding
            payload = payload[:-payload[-1]] if payload[-1] <= len(payload) else b""
        return payload


# ---------------------------------------------------------------- GStreamer: which elements exist, and what they accept

def parse_gst_inspect(text: str) -> Set[str]:
    props: Set[str] = set()
    inside = False
    for line in text.splitlines():
        if line.startswith("Element Properties:"):
            inside = True
            continue
        if inside and re.match(r"^Element (Signals|Actions)", line):
            break
        match = re.match(r"^  ([a-z0-9][a-z0-9-]*)\s*:", line) if inside else None
        if match:
            props.add(match.group(1))
    return props


class GstProbe:
    """gst-inspect-1.0, asked once per element: None = not installed, otherwise the property names it has."""

    def __init__(self, runner=subprocess.run, which=shutil.which) -> None:
        self.runner, self.which = runner, which
        self.cache: Dict[str, Optional[Set[str]]] = {}

    def props(self, element: str) -> Optional[Set[str]]:
        if element not in self.cache:
            result: Optional[Set[str]] = None
            if self.which("gst-inspect-1.0"):
                try:
                    done = self.runner(["gst-inspect-1.0", element], capture_output=True, text=True, timeout=20)
                    if done.returncode == 0:
                        result = parse_gst_inspect(done.stdout or "")
                except (OSError, subprocess.SubprocessError):
                    result = None
            self.cache[element] = result
        return self.cache[element]

    def has(self, element: str) -> bool:
        return self.props(element) is not None


def fit_size(width: int, height: int, max_w: int, max_h: int) -> Tuple[int, int]:
    """Largest picture that fits in max_w x max_h with the same shape; never bigger than the original; both sides even."""
    if width <= 0 or height <= 0:
        return max_w, max_h
    scale = min(1.0, max_w / width, max_h / height)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def _opts(probe: GstProbe, element: str, wanted: List[Tuple[str, str]]) -> List[str]:
    """name=value options, only the ones this build of the element knows."""
    known = probe.props(element) or set()
    return [f"{name}={value}" for name, value in wanted if name in known]


def video_candidates(probe: GstProbe, node: int, width: int, height: int, fps: int, kbps: int) -> List[Tuple[str, List[str]]]:
    """Pipelines to try, best first: (name of the encoder, gst-launch arguments)."""
    source = ["pipewiresrc", *_opts(probe, "pipewiresrc", [("path", str(node)), ("do-timestamp", "true"), ("keepalive-time", "100")])]
    if "path=" not in " ".join(source):
        source += _opts(probe, "pipewiresrc", [("target-object", str(node))])
    head = ["gst-launch-1.0", "-q", *source, "!", "queue", "max-size-buffers=3", "leaky=downstream", "!", "videorate", "!", f"video/x-raw,framerate={fps}/1", "!"]
    tail = ["!", "h264parse", "config-interval=-1", "!", "video/x-h264,stream-format=byte-stream,alignment=au", "!", "fdsink", "fd=1", "sync=false"]
    out: List[Tuple[str, List[str]]] = []
    if probe.has("h264parse"):
        if probe.has("vah264enc") and probe.has("vapostproc"):
            enc = ["vah264enc", *_opts(probe, "vah264enc", [("bitrate", str(kbps)), ("key-int-max", str(fps)), ("b-frames", "0"), ("rate-control", "cbr"),
                                                              ("target-usage", "4"), ("aud", "true")])]
            out.append(("vah264enc (VA-API hardware)", head + ["vapostproc", "!", f"video/x-raw(memory:VAMemory),format=NV12,width={width},height={height}", "!"] + enc + tail))
        if probe.has("vaapih264enc") and probe.has("vaapipostproc"):
            enc = ["vaapih264enc", *_opts(probe, "vaapih264enc", [("bitrate", str(kbps)), ("keyframe-period", str(fps)), ("max-bframes", "0"), ("rate-control", "cbr")])]
            out.append(("vaapih264enc (VA-API hardware)", head + ["vaapipostproc", "!", f"video/x-raw,format=NV12,width={width},height={height}", "!"] + enc + tail))
        if probe.has("x264enc") and probe.has("videoconvert") and probe.has("videoscale"):
            enc = ["x264enc", *_opts(probe, "x264enc", [("bitrate", str(kbps)), ("key-int-max", str(fps)), ("bframes", "0"), ("tune", "zerolatency"),
                                                          ("speed-preset", "ultrafast"), ("aud", "true"), ("byte-stream", "true")])]
            out.append(("x264enc (software)", head + ["videoconvert", "!", "videoscale", "!", f"video/x-raw,format=I420,width={width},height={height}", "!"] + enc + tail))
        if probe.has("openh264enc") and probe.has("videoconvert") and probe.has("videoscale"):
            enc = ["openh264enc", *_opts(probe, "openh264enc", [("bitrate", str(kbps * 1000)), ("gop-size", str(fps)), ("rate-control", "bitrate"),
                                                                  ("usage-type", "screen"), ("complexity", "low")])]
            out.append(("openh264enc (software)", head + ["videoconvert", "!", "videoscale", "!", f"video/x-raw,format=I420,width={width},height={height}", "!"] + enc + tail))
    return out


def audio_candidates(probe: GstProbe, sink: Optional[str]) -> List[Tuple[str, List[str]]]:
    if not all(probe.has(e) for e in ("opusenc", "rtpopuspay", "rtpstreampay", "audioconvert", "audioresample")):
        return []
    enc = ["opusenc", *_opts(probe, "opusenc", [("bitrate", "96000"), ("frame-size", "10"), ("audio-type", "restricted-lowdelay")])]
    chain = ["!", "audioconvert", "!", "audioresample", "!", "audio/x-raw,rate=48000,channels=2", "!", *enc, "!", "rtpopuspay", "!", "rtpstreampay", "!", "fdsink", "fd=1", "sync=false"]
    out: List[Tuple[str, List[str]]] = []
    if sink and probe.has("pulsesrc"):
        out.append(("pulsesrc", ["gst-launch-1.0", "-q", "pulsesrc", f"device={sink}.monitor", *_opts(probe, "pulsesrc", [("buffer-time", "40000"), ("latency-time", "10000")])] + chain))
    if probe.has("pipewiresrc"):
        out.append(("pipewiresrc (default output)", ["gst-launch-1.0", "-q", "pipewiresrc", "stream-properties=props,stream.capture.sink=true", "do-timestamp=true"] + chain))
    return out


def default_sink(runner=subprocess.run, which=shutil.which) -> Optional[str]:
    if not which("pactl"):
        return None
    for args in (["pactl", "get-default-sink"], ["pactl", "info"]):
        try:
            done = runner(args, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        text = (done.stdout or "").strip()
        if done.returncode == 0 and text:
            if args[1] == "info":
                match = re.search(r"^Default Sink:\s*(\S+)", text, re.M)
                text = match.group(1) if match else ""
            if text:
                return text.splitlines()[0].strip()
    return None


# ---------------------------------------------------------------- the screen-cast session (gjs), held while a guest watches

HOLDER_JS = r'''// Mesh Guest: hold a GNOME screen-cast session of one monitor and say where its video can be read. Runs until stopped.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

const args = (typeof ARGV !== 'undefined') ? ARGV : System.programArgs;
const WANT = args[0] || '';
const SC = 'org.gnome.Mutter.ScreenCast';
const DC = 'org.gnome.Mutter.DisplayConfig';
const bus = Gio.DBus.session;
const out = o => print(JSON.stringify(o));

function call(name, path, iface, method, params, replyType) {
  return bus.call_sync(name, path, iface, method, params, replyType ? new GLib.VariantType(replyType) : null,
    Gio.DBusCallFlags.NONE, 15000, null);
}

function monitors() {
  const reply = call(DC, '/org/gnome/Mutter/DisplayConfig', DC, 'GetCurrentState', null, null);
  const [, mons, logical] = reply.recursiveUnpack();
  const layout = {};
  for (const l of logical) {
    const [x, y, scale, , primary, specs] = l;
    for (const s of specs) layout[s[0]] = { x, y, scale: scale || 1, primary };
  }
  return mons.map(m => {
    const [spec, modes] = m;
    const cur = modes.find(md => md[6] && md[6]['is-current']);
    return { connector: spec[0], width: cur ? cur[1] : 0, height: cur ? cur[2] : 0, ...(layout[spec[0]] ?? {}) };
  }).filter(m => m.x !== undefined && m.width > 0);
}

const loop = new GLib.MainLoop(null, false);
let sessionPath = null;
try {
  const mons = monitors();
  if (!mons.length) throw new Error('GNOME reported no monitors');
  const chosen = WANT ? mons.find(m => m.connector === WANT) : (mons.find(m => m.primary) || mons[0]);
  if (!chosen) throw new Error(`no monitor called ${WANT} (there is: ${mons.map(m => m.connector).join(', ')})`);
  const logicalSize = m => [Math.round(m.width / m.scale), Math.round(m.height / m.scale)];
  const [lw, lh] = logicalSize(chosen);
  const desktop = [Math.max(...mons.map(m => m.x + logicalSize(m)[0])), Math.max(...mons.map(m => m.y + logicalSize(m)[1]))];

  const created = call(SC, '/org/gnome/Mutter/ScreenCast', SC, 'CreateSession', new GLib.Variant('(a{sv})', [{}]), '(o)');
  sessionPath = created.deepUnpack()[0];
  const recorded = call(SC, sessionPath, `${SC}.Session`, 'RecordMonitor',
    new GLib.Variant('(sa{sv})', [chosen.connector, { 'cursor-mode': new GLib.Variant('u', 1) }]), '(o)');
  const streamPath = recorded.deepUnpack()[0];
  let announced = false;
  bus.signal_subscribe(SC, `${SC}.Stream`, 'PipeWireStreamAdded', streamPath, null, Gio.DBusSignalFlags.NONE, (_c, _s, _p, _i, _n, params) => {
    if (announced) return;
    announced = true;
    out({ node: params.deepUnpack()[0], connector: chosen.connector, size: [chosen.width, chosen.height], monitor: [chosen.x, chosen.y, lw, lh], desktop });
  });
  bus.signal_subscribe(SC, `${SC}.Session`, 'Closed', sessionPath, null, Gio.DBusSignalFlags.NONE, () => { out({ closed: true }); loop.quit(); });
  call(SC, sessionPath, `${SC}.Session`, 'Start', null, null);
  GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 12, () => {
    if (!announced) { out({ error: 'GNOME started the session but never announced a video stream' }); loop.quit(); }
    return GLib.SOURCE_REMOVE;
  });
  for (const sig of [1, 2, 15]) GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, () => { loop.quit(); return GLib.SOURCE_REMOVE; });
  loop.run();
} catch (e) {
  out({ error: String(e.message || e) });
} finally {
  try { if (sessionPath) call(SC, sessionPath, `${SC}.Session`, 'Stop', null, null); } catch (e) { /* already gone */ }
}
'''

try:
    import ctypes
    _prctl = ctypes.CDLL(None, use_errno=True).prctl
except (OSError, AttributeError):  # not Linux
    _prctl = None


def _die_with_parent() -> None:
    """Child processes end when the share process does, even if it is killed: nothing keeps recording."""
    if _prctl is not None:
        _prctl(1, signal.SIGTERM)       # PR_SET_PDEATHSIG


def _terminate(proc, wait: float = 3.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=wait)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


class ScreenCapture:
    """gjs holding a screen-cast of one monitor. start() -> {node, connector, size, monitor, desktop}."""

    def __init__(self, directory: str, environ=os.environ, popen=subprocess.Popen, which=shutil.which) -> None:
        self.directory, self.environ, self.popen, self.which = directory, environ, popen, which
        self.proc = None
        self.lines: "deque[str]" = deque()
        self.cond = threading.Condition()

    def start(self, connector: str = "", timeout: float = 15.0) -> dict:
        import mesh_input as mi
        if not self.which("gjs"):
            raise StreamError("gjs is not installed (Arch: sudo pacman -S gjs). It is how this computer's screen is captured without a pop-up.")
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        script = os.path.join(self.directory, "guest-holder.mjs")
        with open(script, "w", encoding="utf-8") as f:
            f.write(HOLDER_JS)
        env = {**self.environ, **mi.graphical_environment(self.environ)}
        try:
            self.proc = self.popen(["gjs", "-m", script, connector], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   env=env, text=True, bufsize=1, preexec_fn=_die_with_parent)
        except OSError as exc:
            raise StreamError(f"could not start gjs: {exc}") from None
        threading.Thread(target=self._read, daemon=True, name="mesh-holder-out").start()
        deadline = time.monotonic() + timeout
        with self.cond:
            while time.monotonic() < deadline:
                while self.lines:
                    line = self.lines.popleft().strip()
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(data, dict) and data.get("error"):
                        self.stop()
                        raise StreamError(f"GNOME would not share the screen: {data['error']}")
                    if isinstance(data, dict) and "node" in data:
                        return data
                if self.proc.poll() is not None:
                    raise StreamError("the screen-capture helper stopped at once (is a desktop session running on this computer?)")
                self.cond.wait(0.2)
        self.stop()
        raise StreamError("GNOME did not answer in time: is the screen unlocked and a desktop session running?")

    def _read(self) -> None:
        proc = self.proc
        try:
            for line in proc.stdout:
                with self.cond:
                    self.lines.append(line)
                    self.cond.notify_all()
        except (OSError, ValueError):
            pass
        with self.cond:
            self.cond.notify_all()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        _terminate(self.proc)
        self.proc = None


# ---------------------------------------------------------------- one running source: capture + video encoder + audio encoder

class GstSource:
    """What the hub needs: .info, .start(on_video, on_audio, on_end), .stop()."""

    def __init__(self, directory: str, connector: str = "", quality: str = DEFAULT_QUALITY, sound: bool = True, environ=os.environ,
                 runner=subprocess.run, popen=subprocess.Popen, which=shutil.which, capture: Optional[ScreenCapture] = None,
                 probe: Optional[GstProbe] = None) -> None:
        if quality not in PRESETS:
            raise StreamError(f"quality is one of {', '.join(PRESETS)}")
        self.directory, self.connector, self.quality, self.sound = directory, connector, quality, sound
        self.environ, self.runner, self.popen, self.which = environ, runner, popen, which
        self.capture = capture or ScreenCapture(directory, environ, popen, which)
        self.probe = probe or GstProbe(runner, which)
        self.procs: List = []
        self.stopping = False
        self.info: dict = {}
        self.encoder = ""
        self.log_path = os.path.join(directory, "guest-gst.log")

    def start(self, on_video: Callable[[bytes, bool], None], on_audio: Callable[[bytes], None], on_end: Callable[[str], None]) -> None:
        if not self.which("gst-launch-1.0"):
            raise StreamError("GStreamer is not installed (Arch: sudo pacman -S gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugin-pipewire gst-plugin-va).")
        self.stopping = False
        max_w, max_h, fps, kbps = PRESETS[self.quality]
        cap = self.capture.start(self.connector)
        try:
            width, height = fit_size(*(cap.get("size") or [0, 0]), max_w, max_h)
            candidates = video_candidates(self.probe, int(cap["node"]), width, height, fps, kbps)
            if not candidates:
                raise StreamError("no H.264 encoder is available to GStreamer. Run  meshd share doctor  to see what to install.")
            video = self._first_working(candidates, "video")
            if video is None:
                raise StreamError("GStreamer could not encode the screen. Run  meshd share doctor ; the details are in " + self.log_path)
            proc, self.encoder, first = video
            audio_name = ""
            if self.sound:
                sink = default_sink(self.runner, self.which)
                audio = self._first_working(audio_candidates(self.probe, sink), "audio", first_bytes=False)
                if audio is not None:
                    audio_proc, audio_name, _ = audio
                    threading.Thread(target=self._pump_audio, args=(audio_proc, on_audio), daemon=True, name="mesh-audio").start()
            self.info = {"width": width, "height": height, "fps": fps, "kbps": kbps, "codec": "h264", "audio": bool(audio_name), "encoder": self.encoder,
                         "monitor": cap.get("monitor"), "desktop": cap.get("desktop"), "connector": cap.get("connector")}
            threading.Thread(target=self._pump_video, args=(proc, on_video, on_end, first), daemon=True, name="mesh-video").start()
        except BaseException:
            self.stop()
            raise

    def _first_working(self, candidates: List[Tuple[str, List[str]]], what: str, first_bytes: bool = True):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        tried = []
        for name, argv in candidates:
            with open(self.log_path, "ab") as log:
                log.write(f"\n--- {what}: {name}\n{' '.join(argv)}\n".encode())
                try:
                    proc = self.popen(argv, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL, env=self._env(), preexec_fn=_die_with_parent)
                except OSError as exc:
                    tried.append(f"{name}: {exc}")
                    continue
            self.procs.append(proc)
            deadline = time.monotonic() + (8.0 if first_bytes else 1.5)
            fd = proc.stdout.fileno()
            while time.monotonic() < deadline:
                if first_bytes:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if ready:
                        data = os.read(fd, 65536)
                        if data:
                            return proc, name, data          # bytes are flowing: this one works
                        break                                # end of stream with nothing: it died
                elif proc.poll() is not None:
                    break
                else:
                    time.sleep(0.1)
            if not first_bytes and proc.poll() is None:
                return proc, name, b""                       # sound may be silent for a while; a process still running is good enough
            _terminate(proc, 1.0)
            tried.append(name)
        return None

    def _env(self) -> dict:
        import mesh_input as mi
        return {**self.environ, **mi.graphical_environment(self.environ)}

    def _pump_video(self, proc, on_video, on_end, first: bytes = b"") -> None:
        splitter = AccessUnitSplitter()
        fd = proc.stdout.fileno()
        reason = "the screen stream stopped"
        try:
            for au, key in splitter.feed(first):
                on_video(au, key)
            while not self.stopping:
                ready, _, _ = select.select([fd], [], [], 0.015)
                if ready:
                    data = os.read(fd, 65536)
                    if not data:
                        break
                    for au, key in splitter.feed(data):
                        on_video(au, key)
                else:
                    for au, key in splitter.idle():
                        on_video(au, key)
                    if not self.capture.alive():
                        reason = "GNOME ended the screen capture (screen locked or monitor removed?)"
                        break
        except (OSError, ValueError):
            pass
        if not self.stopping:
            on_end(reason)

    def _pump_audio(self, proc, on_audio) -> None:
        parser = RtpStreamParser()
        fd = proc.stdout.fileno()
        try:
            while not self.stopping:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                data = os.read(fd, 16384)
                if not data:
                    break
                for packet in parser.feed(data):
                    on_audio(packet)
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        self.stopping = True
        for proc in self.procs:
            _terminate(proc, 2.0)
        self.procs = []
        self.capture.stop()


# ---------------------------------------------------------------- what the guest may do with the keyboard and mouse

BLOCKED_COMBO_KEYS = {f"F{n}" for n in range(1, 13)}


class GuestInput:
    """Turns the browser's JSON messages into injector calls. Everything is checked: types, ranges, rate, key names."""

    def __init__(self, injector, abs_map=None, view_only: bool = False, clock: Callable[[], float] = time.monotonic) -> None:
        import mesh_input as mi
        self.mi = mi
        self.injector = injector
        if abs_map is not None and hasattr(injector, "abs_map"):
            injector.abs_map = abs_map
        self.view_only = view_only
        self.paused = False
        self.clock = clock
        self.tokens, self.stamp = float(INPUT_BURST), clock()
        self.down: Set[str] = set()

    def _allow(self) -> bool:
        now = self.clock()
        self.tokens = min(float(INPUT_BURST), self.tokens + (now - self.stamp) * INPUT_RATE)
        self.stamp = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    @staticmethod
    def _int(value, low: int, high: int) -> Optional[int]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(min(max(value, low), high))

    def handle(self, msg: dict) -> bool:
        """-> True when the message was real input (it counts as the guest being active)."""
        kind = msg.get("t")
        if kind == "r":
            self.release_all()
            return False
        if self.view_only or self.paused or kind not in ("m", "b", "w", "k") or not self._allow():
            return False
        inj = self.injector
        if kind == "m":
            x, y = self._int(msg.get("x"), 0, 65535), self._int(msg.get("y"), 0, 65535)
            if x is None or y is None:
                return False
            inj.move_abs(x, y)
            return True
        if kind == "b":
            button, down = msg.get("b"), msg.get("d")
            if isinstance(button, bool) or not isinstance(button, int) or not 1 <= button <= 5 or down not in (0, 1, True, False):
                return False
            inj.button(button, bool(down))
            return True
        if kind == "w":
            v, h = msg.get("v", 0), msg.get("h", 0)
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in (v, h)):
                return False
            inj.scroll(max(-20.0, min(20.0, float(v))), max(-20.0, min(20.0, float(h))))
            return True
        code, down = msg.get("c"), msg.get("d")
        if not isinstance(code, str) or code not in self.mi.KEY_CODES or code in ("ctrl", "shift", "alt", "meta") or down not in (0, 1, True, False):
            return False
        down = bool(down)
        alt_down = bool({"AltLeft", "AltRight"} & self.down)
        if down and ((code in BLOCKED_COMBO_KEYS and alt_down and {"ControlLeft", "ControlRight"} & self.down)   # Ctrl+Alt+Fn: switch to a text console
                     or (code == "PrintScreen" and alt_down)):                                                    # Alt+SysRq: kernel magic keys
            return False
        (self.down.add if down else self.down.discard)(code)
        inj.key(code, down)
        return True

    def release_all(self) -> None:
        self.down.clear()
        try:
            self.injector.release_all()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- the hub: one source, the guests watching it

class Subscriber:
    """One guest's connection and the queue of what still has to be sent to it."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.items: Deque[Tuple[int, bytes]] = deque()      # (kind, bytes); kind 1 video, 2 audio, 0 text
        self.bytes = 0
        self.cond = threading.Condition()
        self.waiting_key = True
        self.dead = False
        self.dropped = 0
        self.sent = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="mesh-guest-send")

    def start(self) -> None:
        self.thread.start()

    def _put(self, kind: int, data: bytes) -> None:
        with self.cond:
            self.items.append((kind, data))
            self.bytes += len(data)
            self.cond.notify()

    def put_text(self, text: str) -> None:
        self._put(0, text.encode("utf-8"))

    def offer_video(self, frame: bytes, key: bool) -> None:
        if self.dead:
            return
        with self.cond:
            if self.waiting_key and not key:
                return
            if self.bytes > MAX_BACKLOG and not key:           # the guest cannot keep up: drop until the next key frame
                self.items = deque(item for item in self.items if item[0] != 1)
                self.bytes = sum(len(item[1]) for item in self.items)
                self.waiting_key = True
                self.dropped += 1
                return
            if key:
                self.waiting_key = False
            self.items.append((1, frame))
            self.bytes += len(frame)
            self.cond.notify()

    def offer_audio(self, frame: bytes) -> None:
        if self.dead:
            return
        with self.cond:
            if self.bytes > MAX_BACKLOG:
                return
            self.items.append((2, frame))
            self.bytes += len(frame)
            self.cond.notify()

    def _run(self) -> None:
        try:
            while not self.dead:
                with self.cond:
                    while not self.items and not self.dead:
                        self.cond.wait(0.5)
                    if self.dead:
                        return
                    kind, data = self.items.popleft()
                    self.bytes -= len(data)
                if kind == 0:
                    self.ws.send_text(data.decode("utf-8"))
                else:
                    self.ws.send_binary(data)
                    self.sent += 1
        except Exception:  # noqa: BLE001 - the connection broke: the serving loop sees .dead and ends
            self.dead = True

    def stop(self) -> None:
        self.dead = True
        with self.cond:
            self.cond.notify_all()


class StreamHub:
    def __init__(self, source, injector_factory: Callable[[], object], say: Callable[[str], None] = print, view_only: bool = False,
                 quality: str = DEFAULT_QUALITY, clock: Callable[[], float] = time.monotonic) -> None:
        self.source, self.injector_factory, self.say = source, injector_factory, say
        self.quality = quality
        self.clock = clock
        self.lock = threading.RLock()
        self.subs: Set[Subscriber] = set()
        self.gop: List[Tuple[bytes, bool]] = []
        self.headers: Dict[int, bytes] = {}
        self.t0 = clock()
        self.view_only = view_only
        self.paused = False
        self.input: Optional[GuestInput] = None
        self.running = False
        self.end_reason = ""

    # ---- what the owner can change while it runs
    def set_view_only(self, value: bool) -> None:
        self.view_only = value
        if self.input:
            self.input.view_only = value
            self.input.release_all()
        self.broadcast_info()

    def set_paused(self, value: bool) -> None:
        self.paused = value
        if self.input:
            self.input.paused = value
            self.input.release_all()
        self.broadcast_info()

    def info(self, extra: Optional[dict] = None) -> dict:
        src = getattr(self.source, "info", {}) or {}
        data = {"t": "info", "width": src.get("width"), "height": src.get("height"), "fps": src.get("fps"), "codec": "h264", "audio": bool(src.get("audio")),
                "view_only": self.view_only, "paused": self.paused, "quality": self.quality}
        data.update(extra or {})
        return data

    def broadcast_info(self) -> None:
        with self.lock:
            for sub in list(self.subs):
                sub.put_text(json.dumps(self.info()))

    # ---- the source's callbacks
    def _pts(self) -> int:
        return int((self.clock() - self.t0) * 1000) & 0xFFFFFFFF

    def _with_headers(self, frame: bytes, key: bool) -> bytes:
        """Every key frame carries its parameter sets, so a guest can start from any key frame."""
        if not key:
            return frame
        types = {nal_type(n) for n in split_nals(frame)}
        for nal in split_nals(frame):
            if nal_type(nal) in (7, 8):
                self.headers[nal_type(nal)] = nal
        missing = [self.headers[t] for t in (7, 8) if t not in types and t in self.headers]
        if missing:
            frame = b"".join(b"\x00\x00\x00\x01" + n for n in missing) + frame
        return frame

    def on_video(self, frame: bytes, key: bool) -> None:
        frame = self._with_headers(frame, key)
        with self.lock:
            if key:
                self.gop = [(frame, True)]
            elif self.gop and len(self.gop) < MAX_GOP_FRAMES:
                self.gop.append((frame, False))
            if self.paused:
                return
            packet = b"\x01" + (b"\x01" if key else b"\x00") + struct.pack("!I", self._pts()) + frame
            for sub in list(self.subs):
                sub.offer_video(packet, key)

    def on_audio(self, packet: bytes) -> None:
        if self.paused:
            return
        data = b"\x02\x00" + struct.pack("!I", self._pts()) + packet
        with self.lock:
            for sub in list(self.subs):
                sub.offer_audio(data)

    def on_end(self, reason: str) -> None:
        self.end_reason = reason
        self.say(f"  The screen stream stopped: {reason}")
        with self.lock:
            self.running = False
            for sub in list(self.subs):
                sub.put_text(json.dumps({"t": "end", "why": "The owner's screen stopped being shared."}))

    # ---- one guest
    def _ensure_started(self) -> None:
        with self.lock:
            if self.running:
                return
            self.gop, self.headers = [], {}
            self.source.start(self.on_video, self.on_audio, self.on_end)
            self.running = True
            if self.input is None:
                try:
                    injector = self.injector_factory()
                except Exception as exc:  # noqa: BLE001 - no /dev/uinput permission: the guest can look, not touch
                    self.say(f"  The guest can watch but not type or click: {exc}")
                    self.view_only = True
                    injector = None
                if injector is not None:
                    import mesh_input as mi
                    info = getattr(self.source, "info", {}) or {}
                    mon, desk = info.get("monitor"), info.get("desktop")
                    abs_map = mi.AbsoluteMap(tuple(mon) if mon else None, tuple(desk) if desk else None)
                    self.input = GuestInput(injector, abs_map, self.view_only)
                    self.input.paused = self.paused
            self.say(f"  Streaming the screen: {getattr(self.source, 'encoder', '') or 'encoder unknown'}, "
                     f"{(getattr(self.source, 'info', {}) or {}).get('width')}x{(getattr(self.source, 'info', {}) or {}).get('height')}"
                     f"{', with sound' if (getattr(self.source, 'info', {}) or {}).get('audio') else ', no sound'}")

    def serve(self, ws, still_valid: Callable[[], bool], on_activity: Callable[[], None]) -> str:
        """Runs for the length of one guest's connection. Returns why it ended."""
        sub = Subscriber(ws)
        try:
            self._ensure_started()
        except StreamError as exc:
            self.say(f"  Could not start the screen stream: {exc}")
            try:
                ws.send_text(json.dumps({"t": "end", "why": "The owner's computer could not start the screen. Ask them to run: meshd share doctor"}))
                ws.close(1011)
            except Exception:  # noqa: BLE001
                pass
            return "source"
        with self.lock:
            self.subs.add(sub)
            catch_up = list(self.gop)
        try:
            sub.put_text(json.dumps(self.info()))
            for frame, key in catch_up:                    # the picture appears at once, not at the next key frame
                sub.offer_video(b"\x01" + (b"\x01" if key else b"\x00") + struct.pack("!I", self._pts()) + frame, key)
            sub.start()
            while not sub.dead and ws.closed is False:
                if not still_valid():
                    return "ended"
                try:
                    message = ws.recv(timeout=1.0)
                except Exception:  # noqa: BLE001 - closed or broken
                    return "gone"
                if message is None:
                    continue
                kind, payload = message
                if kind != "text":
                    continue
                try:
                    data = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("t") == "p":
                    ts = data.get("ts")
                    sub.put_text(json.dumps({"t": "pong", "ts": ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else 0}))
                elif self.input is not None and self.input.handle(data):
                    on_activity()
            return "gone"
        finally:
            sub.stop()
            with self.lock:
                self.subs.discard(sub)
                idle = not self.subs
            if self.input:
                self.input.release_all()
            if idle:
                self.stop_source()
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def stop_source(self) -> None:
        with self.lock:
            if not self.running:
                return
            self.running = False
        self.source.stop()
        self.say("  The screen stream stopped (nobody is watching).")

    def close(self) -> None:
        with self.lock:
            for sub in list(self.subs):
                sub.stop()
        self.stop_source()
        if self.input:
            self.input.release_all()
            try:
                self.input.injector.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- `meshd share doctor`

ARCH_PACKAGE = {"gjs": "gjs", "gst-launch-1.0": "gstreamer", "pipewiresrc": "gst-plugin-pipewire", "vah264enc": "gst-plugin-va",
                "vapostproc": "gst-plugin-va", "h264parse": "gst-plugins-bad", "x264enc": "gst-plugins-ugly", "opusenc": "gst-plugins-base",
                "rtpopuspay": "gst-plugins-good", "rtpstreampay": "gst-plugins-good", "pulsesrc": "gst-plugins-good", "videoconvert": "gst-plugins-base",
                "videoscale": "gst-plugins-base", "videorate": "gst-plugins-base", "audioconvert": "gst-plugins-base", "audioresample": "gst-plugins-base"}


def doctor(runner=subprocess.run, which=shutil.which, environ=os.environ, access=os.access) -> Tuple[bool, List[str]]:
    """What the screen stream needs, one line each. -> (can stream, lines)"""
    probe = GstProbe(runner, which)
    lines: List[str] = []
    ok = True
    need: Set[str] = set()

    def line(good: bool, text: str, fix: str = "") -> None:
        lines.append(f"  {'ok     ' if good else 'MISSING'} {text}" + (f"   ->  {fix}" if fix and not good else ""))

    for tool in ("gjs", "gst-launch-1.0", "gst-inspect-1.0"):
        have = bool(which(tool))
        line(have, tool, "sudo pacman -S " + ARCH_PACKAGE.get(tool, "gstreamer"))
        ok = ok and have
        if not have:
            need.add(ARCH_PACKAGE.get(tool, "gstreamer"))
    if which("gst-inspect-1.0"):
        for element, label in (("pipewiresrc", "screen source (PipeWire)"), ("h264parse", "H.264 parser"), ("videoconvert", "video convert"), ("videoscale", "video scale"),
                               ("videorate", "frame rate")):
            have = probe.has(element)
            line(have, f"{element}: {label}", "sudo pacman -S " + ARCH_PACKAGE[element])
            ok = ok and have
            if not have:
                need.add(ARCH_PACKAGE[element])
        encoders = [(name, probe.has(name)) for name in ("vah264enc", "vaapih264enc", "x264enc", "openh264enc")]
        usable = [name for name, have in encoders if have]
        lines.append("  " + ("ok      " if usable else "MISSING ") + "H.264 encoder: " + (", ".join(usable) if usable else "none (need one of vah264enc, x264enc)")
                     + ("" if usable else "   ->  sudo pacman -S gst-plugin-va   (hardware)   or   sudo pacman -S gst-plugins-ugly   (software)"))
        if usable and usable[0] not in ("vah264enc", "vaapih264enc"):
            lines.append("          note: only a software encoder was found; it works but uses the processor. For hardware: sudo pacman -S gst-plugin-va")
        if not usable:
            ok = False
        sound = all(probe.has(e) for e in ("opusenc", "rtpopuspay", "rtpstreampay", "audioconvert", "audioresample")) and (probe.has("pulsesrc") or probe.has("pipewiresrc"))
        lines.append("  " + ("ok      " if sound else "MISSING ") + "sound (Opus): " + ("yes" if sound else "the stream would have no sound")
                     + ("" if sound else "   ->  sudo pacman -S gst-plugins-good gst-plugins-base"))
        sink = default_sink(runner, which)
        lines.append("  " + ("ok      " if sink else "note    ") + ("sound comes from: " + sink if sink else "no default output found with pactl: sound capture will use PipeWire's default"))
    uinput = access("/dev/uinput", os.W_OK)
    lines.append("  " + ("ok      " if uinput else "MISSING ") + "/dev/uinput writable (the guest's keyboard and mouse)"
                 + ("" if uinput else "   ->  the Mesh app needs this too: run  meshd install  (it adds the udev rule), then log out and in once; until then the guest can only watch"))
    lines.append("  " + ("ok      " if which("loginctl") else "note    ") + "loginctl (for --lock-on-end)")
    if need:
        lines.append("\n  Install:  sudo pacman -S " + " ".join(sorted(need)))
    return ok, lines
