"""meshd share bench: where the time goes when this computer streams its screen, and what to change.

It runs the same capture and encoder pipelines the share runs, one stage at a time (capture only, then + scale and convert, then + encode),
and prints how many pictures a second each manages and how much of a processor core it uses, so the slow part is measured, not guessed. It
also looks at the graphics card (is a hardware encoder possible, and what to install), tries scaling on the card (OpenGL), and with --save
remembers the best starting quality for `meshd share screen` (auto quality starts there instead of climbing up or down from a guess).

    meshd share bench              measure with the real screen capture (a desktop session is needed)
    meshd share bench --save       and remember the result
    meshd share bench --synthetic  measure with a made-up moving picture instead of the screen (for a check without a desktop)
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable, Dict, List, Optional, Tuple

import meshstream as st

VENDORS = {"0x8086": "intel", "0x1002": "amd", "0x10de": "nvidia"}
FPS_LINE = re.compile(r"(?:current|fps): ([0-9.]+)")
SAVE_NAME = "quality.json"
BENCH_RUNGS = ("fast", "balanced", "smooth")        # a 60, a 30 and a lighter 30 picture a second: where a computer without a hardware encoder ends up


def save_path(environ=os.environ) -> str:
    base = environ.get("XDG_CACHE_HOME") or os.path.join(environ.get("HOME", "~"), ".cache")
    return os.path.join(os.path.expanduser(base), "mesh", SAVE_NAME)


def load_saved(path: Optional[str] = None, hardware: Optional[bool] = None) -> Optional[dict]:
    """What the last `bench --save` found: {"rung", "chain", "hardware", ...}; None when nothing valid is saved or it does not fit this encoder."""
    try:
        with open(path or save_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("rung") not in st.LADDER or data.get("chain") not in ("cpu", "gl", "hw"):
        return None
    if hardware is not None and bool(data.get("hardware")) != hardware:
        return None
    return data


def apply_saved(source, quality: str, fps: Optional[int], path: Optional[str] = None) -> Optional[dict]:
    """Start auto quality where the last bench said this computer does best (only for --quality auto without --fps, and only when it was found
    with the same kind of encoder). -> what was applied, or None."""
    if quality != "auto" or fps is not None or not hasattr(source, "_hardware"):
        return None
    saved = load_saved(path, hardware=source._hardware())
    if saved:
        source.rung = saved["rung"]
        source.prefer = "gl" if saved["chain"] == "gl" else ""
    return saved


# ---- the graphics card and what to install

def gpus(sys_root: str = "/sys/class/drm", listdir=os.listdir, read=lambda p: open(p, encoding="utf-8").read().strip()) -> List[Dict[str, str]]:
    found = []
    try:
        names = sorted(n for n in listdir(sys_root) if re.fullmatch(r"card\d+", n))
    except OSError:
        return found
    for name in names:
        try:
            vendor = read(f"{sys_root}/{name}/device/vendor")
        except OSError:
            continue
        driver = ""
        try:
            driver = os.path.basename(os.path.realpath(f"{sys_root}/{name}/device/driver"))
        except OSError:
            pass
        found.append({"card": name, "vendor": VENDORS.get(vendor, "other"), "driver": driver})
    return found


def hardware_lines(probe: st.GstProbe, cards: List[Dict[str, str]], vainfo: str = "") -> List[str]:
    """Plain words: is a hardware encoder possible here, and what to do about it (Arch commands)."""
    lines = []
    vendors = sorted({c["vendor"] for c in cards}) or ["unknown"]
    lines.append("  graphics: " + ", ".join(f"{c['vendor']} ({c['driver'] or '?'})" for c in cards) if cards else "  graphics: not found under /sys/class/drm")
    if probe.has("vah264enc") or probe.has("vaapih264enc") or probe.has("nvh264enc"):
        lines.append("  ok      a hardware H.264 encoder is available to GStreamer: " + ", ".join(e for e in ("vah264enc", "vaapih264enc", "nvh264enc") if probe.has(e)))
        return lines
    enc_slice = bool(re.search(r"H264.*VAEntrypointEncSlice", vainfo))
    if vainfo and not enc_slice:
        lines.append("  note    vainfo runs but lists no H.264 encoding: this graphics card/driver cannot hardware-encode H.264 for us")
    lines.append("  MISSING no hardware encoder for GStreamer.  Arch:  sudo pacman -Syu   (never -Sy alone), then:")
    if "intel" in vendors:
        lines.append("          Intel:   sudo pacman -S gst-plugin-va libva-utils intel-media-driver    (older than Broadwell: libva-intel-driver)")
    if "amd" in vendors:
        lines.append("          AMD:     sudo pacman -S gst-plugin-va libva-utils mesa")
    if "nvidia" in vendors:
        lines.append("          NVIDIA:  nvidia-utils with a gst-plugins-bad that has nvcodec gives nvh264enc (used automatically when present); check: gst-inspect-1.0 nvh264enc")
    if not set(vendors) & {"intel", "amd", "nvidia"}:
        lines.append("          sudo pacman -S gst-plugin-va libva-utils    (and the VA-API driver for your graphics card)")
    lines.append("          then check:  vainfo | grep -i 'h264.*enc'   and   gst-inspect-1.0 vah264enc")
    return lines


# ---- measuring one pipeline

def ticks_of(pid: int) -> Optional[int]:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            fields = handle.read().rsplit(") ", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return None


def run_stage(chain: List[str], seconds: float = 5.0, warmup: float = 2.0, popen=subprocess.Popen, sleep=time.sleep, clock=time.time,
              ticks: Callable[[int], Optional[int]] = ticks_of, hz: Optional[int] = None) -> Dict:
    """Run `gst-launch-1.0 -v <chain> ! fpsdisplaysink` and read how many pictures a second come out and how much of a core it takes.
    -> {"fps": float or None, "cpu": percent of one core or None, "error": text}"""
    hz = hz or os.sysconf("SC_CLK_TCK")
    argv = ["gst-launch-1.0", "-v", *chain, "!", "fpsdisplaysink", "sync=false", "text-overlay=false", "video-sink=fakesink"]
    try:
        proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=st._die_with_parent)
    except OSError as exc:
        return {"fps": None, "cpu": None, "error": str(exc)}
    sleep(warmup)
    t0, c0 = clock(), ticks(proc.pid)
    sleep(seconds)
    t1, c1 = clock(), ticks(proc.pid)
    try:
        proc.send_signal(signal.SIGINT)
        out = proc.communicate(timeout=8)[0] or ""
    except (subprocess.SubprocessError, OSError):
        proc.kill()
        out = proc.communicate()[0] or ""
    values = [float(v) for v in FPS_LINE.findall(out)][-4:]
    cpu = (c1 - c0) / hz / max(0.001, t1 - t0) * 100 if c0 is not None and c1 is not None else None
    error = "" if values else ((out.strip().splitlines() or ["no output"])[-1][:160])
    return {"fps": sum(values) / len(values) if values else None, "cpu": cpu, "error": error}


# ---- the stages

def synthetic_head(native: Tuple[int, int], fps: int) -> List[str]:
    return ["videotestsrc", "is-live=true", "pattern=ball", "!", f"video/x-raw,format=BGRx,width={native[0]},height={native[1]},framerate={fps}/1"]


def stages(probe: st.GstProbe, head_for: Callable[[int], List[str]], native: Tuple[int, int]) -> List[Dict]:
    """Every pipeline worth timing: {"label", "stage", "rung", "chain", "target", "argv"}; argv is the gst-launch chain without its end."""
    out: List[Dict] = []
    top = st.settings_for("fast", False, native)
    out.append({"label": f"capture only ({native[0]}x{native[1]}, {top['fps']} a second)", "stage": "capture", "rung": "", "chain": "", "target": top["fps"],
                "argv": head_for(top["fps"]) + ["!", "identity"]})
    for rung in BENCH_RUNGS:
        cfg = st.settings_for(rung, False, native)
        size = f"{cfg['width']}x{cfg['height']} at {cfg['fps']}"
        if probe.has("videoscale") and probe.has("videoconvert"):
            cpu = ["videoscale", "!", f"video/x-raw,width={cfg['width']},height={cfg['height']}", "!", "videoconvert", "!", "video/x-raw,format=I420"]
            out.append({"label": f"{rung}: + scale and convert on the processor ({size})", "stage": "convert", "rung": rung, "chain": "cpu", "target": cfg["fps"],
                        "argv": head_for(cfg["fps"]) + ["!"] + cpu})
        if all(probe.has(e) for e in ("glupload", "glcolorscale", "glcolorconvert", "gldownload")):
            gl = ["glupload", "!", "glcolorscale", "!", f"video/x-raw(memory:GLMemory),width={cfg['width']},height={cfg['height']}", "!", "glcolorconvert", "!",
                  "video/x-raw(memory:GLMemory),format=I420", "!", "gldownload", "!", "video/x-raw,format=I420"]
            out.append({"label": f"{rung}: + scale and convert on the graphics card ({size})", "stage": "convert", "rung": rung, "chain": "gl", "target": cfg["fps"],
                        "argv": head_for(cfg["fps"]) + ["!"] + gl})
        for prefer in ("", "gl"):
            for name, argv, used in st.video_candidates(probe, 0, native, rung, None, None, prefer):
                if prefer == "gl" and "GPU" not in name:
                    continue
                if prefer == "" and "GPU" in name:
                    continue
                cut = argv.index("h264parse") - 1                  # the encoder's own pipeline, minus the stream's end
                body = argv[argv.index("!", argv.index("videorate")) + 3:cut] if "videorate" in argv else argv[:cut]
                chain = "gl" if "GPU" in name else ("hw" if "hardware" in name else "cpu")
                out.append({"label": f"{rung}: + encode {name} ({size})", "stage": "encode", "rung": rung, "chain": chain, "encoder": name, "hardware": "hardware" in name,
                            "target": used["fps"], "argv": head_for(used["fps"]) + ["!"] + body})
    return out


def recommend(rows: List[Dict], cores: int) -> Optional[Dict]:
    """The best working encode row: the highest rung (ladder order) that makes >= 93% of its pictures with at least a third of the machine to spare."""
    ok = [r for r in rows if r["stage"] == "encode" and r.get("fps") and r["fps"] >= 0.93 * r["target"] and (r.get("cpu") is None or r["cpu"] <= 0.67 * 100 * max(1, cores))]
    if not ok:
        return None
    return sorted(ok, key=lambda r: (st.LADDER.index(r["rung"]), 0 if r["chain"] == "hw" else 1 if r["chain"] == "gl" else 2, r.get("cpu") or 0))[0]


def format_table(rows: List[Dict], pick: Optional[Dict], cores: int) -> List[str]:
    lines = [f"  {'stage':74s} {'pictures/s':>10s} {'cpu (1 core = 100%)':>20s}"]
    for r in rows:
        if r.get("fps") is None:
            lines.append(f"  {r['label'][:74]:74s} {'-':>10s} {'-':>20s}   did not run: {r.get('error', '')}")
            continue
        flag = "   <- slow" if r["fps"] < 0.93 * r["target"] else ""
        cpu = "-" if r.get("cpu") is None else f"{r['cpu']:.0f}%"
        lines.append(f"  {r['label'][:74]:74s} {r['fps']:10.1f} {cpu:>20s}{flag}")
    lines.append("")
    if pick:
        lines.append(f"  Best here: {pick['rung']} with {pick['encoder']} ({pick['fps']:.0f} pictures a second using {pick['cpu'] or 0:.0f}% of a core, {cores} cores).")
    else:
        lines.append("  No quality reached its pictures-a-second with room to spare: use  --quality smooth  or  low, and see the hardware advice above.")
    return lines


def run_bench(probe: st.GstProbe, head_for: Callable[[int], List[str]], native: Tuple[int, int], seconds: float, say: Callable[[str], None] = print,
              runner=run_stage, cores: Optional[int] = None) -> Tuple[List[Dict], Optional[Dict]]:
    cores = cores or os.cpu_count() or 1
    rows = []
    plan = stages(probe, head_for, native)
    for i, stage in enumerate(plan, 1):
        say(f"  [{i}/{len(plan)}] {stage['label']} ...")
        result = runner(stage["argv"], seconds)
        rows.append({**stage, **result})
    return rows, recommend(rows, cores)


def save_result(pick: Dict, path: Optional[str] = None) -> str:
    path = path or save_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"rung": pick["rung"], "chain": pick["chain"], "encoder": pick["encoder"], "hardware": bool(pick.get("hardware")), "fps": round(pick["fps"], 1),
                   "cpu": round(pick.get("cpu") or 0), "at": int(time.time())}, handle)
    return path


def main(argv: List[str], environ=os.environ, say: Callable[[str], None] = print) -> int:
    seconds = 5.0
    for i, a in enumerate(argv):
        if a == "--seconds" and i + 1 < len(argv):
            seconds = max(2.0, min(30.0, float(argv[i + 1])))
    probe = st.GstProbe()
    if not probe.has("fpsdisplaysink"):
        say("error: GStreamer's fpsdisplaysink is missing (Arch: sudo pacman -S gst-plugins-good).")
        return 2
    say("meshd share bench: where the time goes (about a minute)\n")
    vainfo = ""
    if shutil.which("vainfo"):
        try:
            vainfo = subprocess.run(["vainfo"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            vainfo = ""
    for line in hardware_lines(probe, gpus(), vainfo):
        say(line)
    say("")
    capture = None
    if "--synthetic" in argv:
        native = (1920, 1080)
        head_for = lambda fps: synthetic_head(native, fps)          # noqa: E731
        say("  (a made-up moving 1920x1080 picture, not your screen)")
    else:
        capture = st.ScreenCapture(tempfile.mkdtemp(prefix="mesh-bench-"), environ)
        try:
            info = capture.start("")
        except st.StreamError as exc:
            say(f"error: {exc}")
            return 2
        node = int(info["node"])
        native = tuple(info.get("size") or (1920, 1080))
        head_for = lambda fps: st.capture_head(probe, node, fps)    # noqa: E731
        say(f"  screen capture started: {native[0]}x{native[1]}")
        say("  TIP: keep something moving on the screen while this runs (scroll a long page, or play a video); a still screen makes every stage look cheaper than it is.")
    try:
        rows, pick = run_bench(probe, head_for, native, seconds, say)
    finally:
        if capture is not None:
            capture.stop()
    say("")
    for line in format_table(rows, pick, os.cpu_count() or 1):
        say(line)
    if "--save" in argv and pick:
        say(f"\n  saved: {save_result(pick, save_path(environ))}  (auto quality now starts at {pick['rung']})")
    elif pick:
        say("\n  run again with --save to start the share at this quality next time")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
