"""`encoder-bench`: time every encoder ffmpeg offers here, recommend the fastest hardware one, optionally write it into Sunshine's config."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mesh_input as mi  # noqa: E402

ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 (codec h264)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
"""


class Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class Machine:
    """A fake ffmpeg: each encoder costs a fixed ms per frame plus a start-up cost; a clock the fake runner advances."""

    def __init__(self, per_frame_ms, failing=None, source_ms=1.0, startup=0.4):
        self.per_frame_ms, self.failing, self.source_ms, self.startup = per_frame_ms, failing or {}, source_ms, startup
        self.now = 0.0
        self.commands = []

    def clock(self):
        return self.now

    def runner(self, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd[:3] == ["ffmpeg", "-hide_banner", "-encoders"]:
            return Done(stdout=ENCODERS)
        frames = int(cmd[cmd.index("-frames:v") + 1])
        encoder = cmd[cmd.index("-c:v") + 1] if "-c:v" in cmd else None
        if encoder in self.failing:
            return Done(stderr=self.failing[encoder] + "\nNothing was written into output file\n", returncode=1)
        scale = 2.25 if "1920x1080" in " ".join(cmd) else 1.0  # 1080p costs more than 720p
        self.now += self.startup + frames * (self.source_ms + (self.per_frame_ms[encoder] if encoder else 0.0)) * scale / 1000.0
        return Done()


def run_bench(machine, config=None, nodes=("/dev/dri/renderD128",), environ=None):
    return mi.encoder_bench(
        runner=machine.runner, which=lambda name: "/usr/bin/" + name, clock=machine.clock,
        environ=environ if environ is not None else {"XDG_CONFIG_HOME": "/cfg"},
        read_file=lambda path: config if config is not None else (_ for _ in ()).throw(FileNotFoundError(path)),
        glob=lambda pattern: list(nodes))


# ------------------------------------------------------------------ reading ffmpeg

def test_the_encoders_are_read_from_ffmpeg_s_list_and_audio_ones_do_not_count_as_missing():
    names = mi.parse_ffmpeg_encoders(ENCODERS)
    assert {"libx264", "h264_nvenc", "h264_vaapi", "aac"} <= names
    assert "Encoders:" not in names and "------" not in names


def test_a_run_with_no_encoder_times_only_the_picture_source():
    cmd = mi.bench_command(None, [], "", [], (1280, 720), 60)
    assert "-c:v" not in cmd and "testsrc2=size=1280x720:rate=60" in cmd and cmd[-3:] == ["-f", "null", "-"]


def test_hardware_options_come_before_the_input_where_ffmpeg_needs_them():
    cmd = mi.bench_command("h264_vaapi", ["-vaapi_device", "/dev/dri/renderD129"], "format=nv12,hwupload", ["-bf", "0"], (1920, 1080), 300)
    assert cmd.index("-vaapi_device") < cmd.index("-i") < cmd.index("-c:v")
    assert cmd[cmd.index("-vf") + 1] == "format=nv12,hwupload"


# ------------------------------------------------------------------ measuring

def test_start_up_cost_and_the_picture_source_are_taken_out_of_the_number():
    machine = Machine({None: 0.0, "libx264": 12.0, "h264_nvenc": 3.0, "h264_vaapi": 5.0})
    report, best = run_bench(machine)
    nvenc_1080 = next(l for l in report.splitlines() if "h264_nvenc" in l and "ms" in l)
    # 3.0 ms per 720p frame x 2.25 for 1080p = 6.75 ms, however long ffmpeg took to start and however slow the source is.
    assert " 6.8 ms" in nvenc_1080 or " 6.7 ms" in nvenc_1080
    assert best["encoder"] == "h264_nvenc"


def test_the_fastest_hardware_encoder_is_recommended_even_when_the_processor_is_faster_on_paper():
    machine = Machine({None: 0.0, "libx264": 1.0, "h264_nvenc": 4.0, "h264_vaapi": 3.0})
    report, best = run_bench(machine)
    assert best["encoder"] == "h264_vaapi" and best["sunshine"] == "vaapi"
    assert "Recommendation: encoder = vaapi" in report


def test_with_no_working_hardware_the_processor_is_recommended_and_the_driver_is_blamed():
    machine = Machine({None: 0.0, "libx264": 6.0},
                      failing={"h264_nvenc": "Cannot load libcuda.so.1", "h264_vaapi": "Failed to open /dev/dri/renderD128"})
    report, best = run_bench(machine)
    assert best["encoder"] == "libx264" and "Recommendation: encoder = software" in report
    assert "Cannot load libcuda.so.1" in report and "Failed to open /dev/dri/renderD128" in report  # the cause, not ffmpeg's fallout
    assert "Nothing was written" not in report
    assert "graphics driver" in report


def test_a_slow_encoder_is_called_too_slow_for_60_fps():
    machine = Machine({None: 0.0, "libx264": 20.0}, failing={"h264_nvenc": "x", "h264_vaapi": "y"})
    report, _ = run_bench(machine)
    assert "TOO SLOW for 60 fps" in report


def test_an_encoder_this_ffmpeg_does_not_have_is_said_so_and_not_run():
    machine = Machine({None: 0.0, "libx264": 5.0, "h264_nvenc": 3.0, "h264_vaapi": 4.0})
    report, _ = run_bench(machine)
    assert "h264_vulkan" in report and "this ffmpeg was built without it" in report
    assert not any("h264_vulkan" in " ".join(c) and "-frames:v" in c for c in machine.commands)


def test_only_encoders_sunshine_can_use_on_linux_are_timed():
    machine = Machine({None: 0.0, "libx264": 5.0, "h264_nvenc": 3.0, "h264_vaapi": 4.0})
    report, _ = run_bench(machine)
    assert "h264_qsv" not in report and "quicksync" not in report  # Windows-only in Sunshine


def test_a_missing_va_driver_is_named_with_the_package_to_install():
    machine = Machine({None: 0.0, "libx264": 3.0}, failing={
        "h264_vaapi": "[VAAPI @ 0x1] Failed to initialise VAAPI connection: -1 (unknown libva error).", "h264_nvenc": "Cannot load libcuda.so.1"})
    report, best = run_bench(machine)
    assert best["encoder"] == "libx264"
    assert "sudo pacman -S intel-media-driver libva-utils" in report and "Sunshine is already doing the same" in report


def test_a_different_vaapi_failure_does_not_blame_the_driver_package():
    machine = Machine({None: 0.0, "libx264": 3.0}, failing={"h264_vaapi": "Impossible to convert between the formats", "h264_nvenc": "x"})
    report, _ = run_bench(machine)
    assert "intel-media-driver" not in report and "check `vainfo`" in report


def test_nothing_working_at_all_gives_no_recommendation():
    machine = Machine({None: 0.0}, failing={"h264_nvenc": "a", "h264_vaapi": "b", "libx264": "c"})
    report, best = run_bench(machine)
    assert best is None and "nothing to recommend" in report


def test_without_ffmpeg_the_report_says_how_to_install_it():
    report, best = mi.encoder_bench(which=lambda name: None)
    assert best is None and "pacman -S ffmpeg" in report


def test_the_render_node_comes_from_sunshine_s_adapter_setting_when_there_is_one():
    machine = Machine({None: 0.0, "libx264": 5.0, "h264_nvenc": 3.0, "h264_vaapi": 4.0})
    report, _ = run_bench(machine, config="adapter_name = /dev/dri/renderD129\n", nodes=("/dev/dri/renderD128",))
    assert "render node used for VAAPI: /dev/dri/renderD129 (from sunshine.conf adapter_name)" in report
    assert any("/dev/dri/renderD129" in c for cmd in machine.commands for c in cmd)


def test_the_current_encoder_setting_is_shown_so_a_mismatch_is_visible():
    machine = Machine({None: 0.0, "libx264": 5.0, "h264_nvenc": 3.0, "h264_vaapi": 4.0})
    report, _ = run_bench(machine, config="encoder = software\nbitrate = 20000\n")
    assert "Sunshine's current setting: encoder = software" in report


# ------------------------------------------------------------------ writing the setting

def test_an_existing_encoder_line_is_replaced_and_everything_else_is_kept():
    text = "# my settings\nbitrate = 20000\nencoder = software\nfps = 60\n"
    assert mi.set_config_option(text, "encoder", "vaapi") == "# my settings\nbitrate = 20000\nencoder = vaapi\nfps = 60\n"


def test_a_missing_encoder_line_is_added_and_duplicates_collapse():
    assert mi.set_config_option("fps = 60\n", "encoder", "nvenc") == "fps = 60\nencoder = nvenc\n"
    assert mi.set_config_option("encoder=a\nfps = 60\nencoder = b\n", "encoder", "vaapi") == "encoder = vaapi\nfps = 60\n"
    assert mi.set_config_option("", "encoder", "vaapi") == "encoder = vaapi\n"


def test_a_key_that_only_starts_the_same_is_left_alone():
    text = "encoder_extra = 1\n"
    assert mi.set_config_option(text, "encoder", "vaapi") == "encoder_extra = 1\nencoder = vaapi\n"


def test_applying_keeps_a_backup_of_the_old_file(tmp_path):
    (tmp_path / "sunshine.conf").write_text("fps = 60\nencoder = software\n")
    message = mi.apply_encoder_setting({"sunshine": "vaapi"}, str(tmp_path))
    assert (tmp_path / "sunshine.conf").read_text() == "fps = 60\nencoder = vaapi\n"
    assert (tmp_path / "sunshine.conf.bak").read_text() == "fps = 60\nencoder = software\n"
    assert "systemctl --user restart sunshine" in message and ".bak" in message


def test_applying_with_no_config_yet_creates_it_and_makes_no_backup(tmp_path):
    message = mi.apply_encoder_setting({"sunshine": "nvenc"}, str(tmp_path / "sunshine"))
    assert (tmp_path / "sunshine" / "sunshine.conf").read_text() == "encoder = nvenc\n"
    assert not (tmp_path / "sunshine" / "sunshine.conf.bak").exists() and ".bak" not in message
