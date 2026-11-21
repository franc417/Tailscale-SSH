"""meshd share bench: stage timing, the recommendation, the hardware advice and the saved starting quality. No GStreamer, no screen, no GPU."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import meshbench as mb  # noqa: E402
import meshstream as st  # noqa: E402
from test_meshstream import COMMON, FakeGst  # noqa: E402

SOFT = {**COMMON, "x264enc": ["bitrate", "key-int-max", "bframes", "tune", "speed-preset", "aud", "byte-stream", "vbv-buf-capacity"], "fpsdisplaysink": []}
GL = {"glupload": [], "glcolorscale": [], "glcolorconvert": [], "gldownload": []}


class FakeProc:
    def __init__(self, text):
        self.pid, self.text, self.signals = 4242, text, []

    def send_signal(self, sig):
        self.signals.append(sig)

    def communicate(self, timeout=None):
        return (self.text, None)

    def kill(self):
        pass


def timed_stage(text, c0, c1, hz=100):
    clock = iter([100.0, 105.0])
    ticks = iter([c0, c1])
    seen = {}

    def popen(argv, **kw):
        seen["argv"] = argv
        return FakeProc(text)
    result = mb.run_stage(["videotestsrc", "!", "identity"], 5.0, 2.0, popen=popen, sleep=lambda s: None, clock=lambda: next(clock), ticks=lambda pid: next(ticks), hz=hz)
    return result, seen["argv"]


def test_stage_reads_pictures_a_second_and_the_cores_it_uses():
    text = "...last-message = rendered: 90, dropped: 0, current: 58.0, average: 59.0\nlast-message = rendered: 120, dropped: 0, current: 60.0, average: 59.5\n"
    result, argv = timed_stage(text, 1000, 1450)                     # 450 ticks at 100 Hz in 5 s = 90% of one core
    assert result["fps"] == pytest.approx(59.0) and result["cpu"] == pytest.approx(90.0) and result["error"] == ""
    assert argv[:2] == ["gst-launch-1.0", "-v"] and argv[-3:] == ["sync=false", "text-overlay=false", "video-sink=fakesink"] and "fpsdisplaysink" in argv
    older, _ = timed_stage("last-message = rendered: 30, dropped: 0, fps: 29.97, drop rate: 0.00\n", 0, 250)        # the older wording
    assert older["fps"] == pytest.approx(29.97)


def test_stage_that_does_not_run_says_why():
    result, _ = timed_stage("WARNING: erroneous pipeline: no element \"glupload\"\n", 0, 0)
    assert result["fps"] is None and "glupload" in result["error"]
    assert mb.run_stage(["x"], popen=lambda *a, **k: (_ for _ in ()).throw(OSError("no gst")), sleep=lambda s: None)["error"] == "no gst"


def head_for(fps):
    return st.capture_head(FakeGst(SOFT).probe(), 57, fps)


def test_stages_cover_capture_convert_and_each_encoder_with_the_streams_own_pipeline():
    plan = mb.stages(FakeGst(SOFT).probe(), head_for, (1920, 1080))
    assert [s["stage"] for s in plan][:3] == ["capture", "convert", "encode"]
    capture = plan[0]
    assert "pipewiresrc" in capture["argv"] and "path=57" in capture["argv"] and capture["argv"][-1] == "identity" and capture["target"] == 60
    encode = [s for s in plan if s["stage"] == "encode" and s["rung"] == "fast"][0]
    text = " ".join(encode["argv"])
    assert "x264enc" in text and "bitrate=4500" in text and "width=1280,height=720" in text and "h264parse" not in text and "fdsink" not in text
    assert encode["encoder"] == "x264enc (software)" and encode["chain"] == "cpu" and encode["target"] == 60 and not encode["hardware"]
    assert not any(s["chain"] == "gl" for s in plan)                     # no OpenGL elements here: no graphics-card stages


def test_stages_add_the_graphics_card_variant_only_when_opengl_elements_exist():
    plan = mb.stages(FakeGst({**SOFT, **GL}).probe(), head_for, (1920, 1080))
    gl = [s for s in plan if s["chain"] == "gl"]
    assert {s["stage"] for s in gl} == {"convert", "encode"} and all("glupload" in " ".join(s["argv"]) for s in gl)
    assert any(s["encoder"] == "x264enc (software, GPU scale/convert)" for s in gl if s["stage"] == "encode")


def row(rung, chain, fps, cpu, target, encoder="x264enc (software)", hardware=False):
    return {"stage": "encode", "rung": rung, "chain": chain, "fps": fps, "cpu": cpu, "target": target, "encoder": encoder, "hardware": hardware, "label": f"{rung} {chain}"}


def test_recommendation_is_the_best_rung_that_keeps_up_with_room_to_spare():
    rows = [row("fast", "cpu", 40.0, 190.0, 60), row("balanced", "cpu", 30.0, 160.0, 30), row("smooth", "cpu", 30.0, 90.0, 30)]
    assert mb.recommend(rows, cores=4)["rung"] == "balanced"             # fast makes 40 of 60; balanced uses 160% of 400%: fine
    assert mb.recommend(rows, cores=2)["rung"] == "smooth"               # on two cores balanced would take 160% of 200%
    assert mb.recommend([row("fast", "cpu", 20.0, 100.0, 60)], cores=8) is None
    same = [row("fast", "cpu", 60.0, 150.0, 60), row("fast", "gl", 60.0, 60.0, 60, "x264enc (software, GPU scale/convert)"),
            row("fast", "hw", 60.0, 30.0, 60, "vah264enc (VA-API hardware)", True)]
    assert mb.recommend(same, cores=4)["chain"] == "hw" and mb.recommend(same[:2], cores=4)["chain"] == "gl"      # hardware, then the card, then the processor


def test_table_marks_slow_stages_and_stages_that_did_not_run():
    rows = [{**row("fast", "cpu", 40.0, 190.0, 60), "label": "fast: encode"}, {"stage": "convert", "label": "fast: gl", "fps": None, "error": "no element glupload", "target": 60}]
    text = "\n".join(mb.format_table(rows, None, 4))
    assert "<- slow" in text and "did not run: no element glupload" in text and "No quality reached" in text


def test_hardware_advice_names_the_vendor_and_the_commands():
    soft = FakeGst(SOFT).probe()
    intel = "\n".join(mb.hardware_lines(soft, [{"card": "card0", "vendor": "intel", "driver": "i915"}]))
    assert "sudo pacman -Syu" in intel and "intel-media-driver" in intel and "gst-plugin-va" in intel and "vainfo" in intel
    amd = "\n".join(mb.hardware_lines(soft, [{"card": "card0", "vendor": "amd", "driver": "amdgpu"}]))
    assert "mesa" in amd and "intel-media-driver" not in amd
    nvidia = "\n".join(mb.hardware_lines(soft, [{"card": "card1", "vendor": "nvidia", "driver": "nvidia"}]))
    assert "nvh264enc" in nvidia
    no_enc = "\n".join(mb.hardware_lines(soft, [{"card": "card0", "vendor": "intel", "driver": "i915"}], vainfo="VAProfileH264Main : VAEntrypointVLD\n"))
    assert "lists no H.264 encoding" in no_enc
    have = "\n".join(mb.hardware_lines(FakeGst({**SOFT, "vah264enc": []}).probe(), [{"card": "card0", "vendor": "intel", "driver": "i915"}]))
    assert "ok" in have and "vah264enc" in have and "MISSING" not in have


def test_gpus_are_read_from_sysfs():
    files = {"/s/card0/device/vendor": "0x8086", "/s/card1/device/vendor": "0x10de", "/s/card1-eDP-1/device/vendor": "0x1002"}
    found = mb.gpus("/s", listdir=lambda root: ["card0", "card1", "card1-eDP-1", "renderD128"], read=lambda p: files[p])
    assert [(g["card"], g["vendor"]) for g in found] == [("card0", "intel"), ("card1", "nvidia")]
    assert mb.gpus("/nonexistent") == []


def test_saved_result_round_trips_and_only_applies_to_the_same_kind_of_encoder(tmp_path):
    path = str(tmp_path / "mesh" / "quality.json")
    pick = row("balanced", "gl", 30.0, 80.0, 30, "x264enc (software, GPU scale/convert)")
    mb.save_result(pick, path)
    saved = mb.load_saved(path)
    assert saved["rung"] == "balanced" and saved["chain"] == "gl" and saved["hardware"] is False
    assert mb.load_saved(path, hardware=False) and mb.load_saved(path, hardware=True) is None
    assert mb.load_saved(str(tmp_path / "missing.json")) is None
    (tmp_path / "bad.json").write_text("{not json")
    assert mb.load_saved(str(tmp_path / "bad.json")) is None
    (tmp_path / "odd.json").write_text(json.dumps({"rung": "insane", "chain": "cpu"}))
    assert mb.load_saved(str(tmp_path / "odd.json")) is None


class StubSource:
    def __init__(self, hardware):
        self.hw, self.rung, self.prefer = hardware, None, ""

    def _hardware(self):
        return self.hw


def test_auto_quality_starts_where_the_bench_said(tmp_path):
    path = str(tmp_path / "q.json")
    mb.save_result(row("balanced", "gl", 30.0, 80.0, 30, "x264enc (software, GPU scale/convert)"), path)
    source = StubSource(False)
    assert mb.apply_saved(source, "auto", None, path)["rung"] == "balanced" and source.rung == "balanced" and source.prefer == "gl"
    for quality, fps, hardware in (("high", None, False), ("auto", 30, False), ("auto", None, True)):     # a named quality, a named speed, another encoder: untouched
        other = StubSource(hardware)
        assert mb.apply_saved(other, quality, fps, path) is None and other.rung is None and other.prefer == ""


def test_stream_pipelines_use_the_graphics_card_only_when_asked_and_know_nvenc():
    probe = FakeGst({**SOFT, **GL}).probe()
    names = [n for n, _, _ in st.video_candidates(probe, 57, (1920, 1080), "fast")]
    assert names == ["x264enc (software)"]
    names = [n for n, _, _ in st.video_candidates(probe, 57, (1920, 1080), "fast", prefer="gl")]
    assert names == ["x264enc (software, GPU scale/convert)", "x264enc (software)"]
    gl = " ".join(st.video_candidates(probe, 57, (1920, 1080), "fast", prefer="gl")[0][1])
    assert gl.index("glupload") < gl.index("glcolorscale") < gl.index("glcolorconvert") < gl.index("gldownload") < gl.index("x264enc") and "videoscale" not in gl
    nv = st.video_candidates(FakeGst({**SOFT, "nvh264enc": ["bitrate", "gop-size", "zerolatency", "rc-mode"]}).probe(), 57, (1920, 1080), "auto")
    assert [n.split()[0] for n, _, _ in nv] == ["nvh264enc", "x264enc"] and nv[0][2]["quality"] == "high" and "bitrate=9000" in " ".join(nv[0][1])      # hardware numbers
    source = st.GstSource("/tmp", "", "auto", sound=False, probe=FakeGst({**SOFT, "nvh264enc": []}).probe())
    assert source._hardware()
    source.encoder = "nvh264enc (NVIDIA hardware)"
    assert source._hardware()


def test_bench_command_is_on_the_command_line():
    import meshshare as ms
    args = ms.build_parser("/tmp").parse_args(["bench", "--save", "--synthetic", "--seconds", "3"])
    assert args.action == "bench" and args.save and args.synthetic and args.seconds == 3.0
    assert not ms.build_parser("/tmp").parse_args(["bench"]).save


@pytest.mark.skipif(not __import__("shutil").which("gst-launch-1.0") or subprocess.run(["gst-inspect-1.0", "fpsdisplaysink"], capture_output=True).returncode != 0,
                    reason="GStreamer with fpsdisplaysink is not installed")
def test_real_gstreamer_times_a_synthetic_picture():
    result = mb.run_stage(mb.synthetic_head((640, 360), 30) + ["!", "identity"], seconds=2.0, warmup=1.0)
    assert result["fps"] and 20 < result["fps"] < 40 and result["cpu"] is not None
