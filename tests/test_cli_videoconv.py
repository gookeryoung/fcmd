"""videoconv 工具测试。

覆盖 ``fcmd.tools.media.videoconv`` 的纯逻辑与执行层：
- 工具注册与格式 / 参数校验
- 输出路径、缩放滤镜、通配符展开
- ffmpeg 进度解析与命令构建
- 子进程执行（成功 / 失败 / 取消 / 启动失败）
- CLI 命令的汇总输出
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.media import videoconv
from fcmd.tools.media.videoconv import (
    OUTPUT_FORMAT_CHOICES,
    OUTPUT_FORMATS,
    PRESETS,
    SUPPORTED_INPUT_FORMATS,
    VideoOptions,
    build_ffmpeg_command,
    build_output_path,
    build_scale_filter,
    expand_input_patterns,
    ffmpeg_version,
    normalize_format,
    parse_duration,
    parse_progress_time,
    progress_percent,
    run_conversion,
    validate_options,
)


# ============================================================================ #
# 注册与常量
# ============================================================================ #
def test_tool_registered() -> None:
    """videoconv 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["videoconv"]) == {None}


def test_output_format_choices_match_formats() -> None:
    """Literal 选项与输出格式常量保持一致。"""
    assert tuple(item.lstrip(".") for item in OUTPUT_FORMATS) == OUTPUT_FORMAT_CHOICES


# ============================================================================ #
# 格式与参数校验
# ============================================================================ #
class TestNormalizeFormat:
    """输出格式规范化。"""

    @pytest.mark.parametrize(("raw", "expected"), [("mp4", ".mp4"), (" MKV ", ".mkv"), (".gif", ".gif")])
    def test_valid(self, raw: str, expected: str) -> None:
        assert normalize_format(raw) == expected

    @pytest.mark.parametrize("raw", ["", "avi2", ".m4s", "mp5"])
    def test_invalid_raises(self, raw: str) -> None:
        with pytest.raises(ValueError, match="输出格式"):
            normalize_format(raw)


class TestValidateOptions:
    """转换参数校验。"""

    def test_default_options_valid(self) -> None:
        validate_options(VideoOptions())

    @pytest.mark.parametrize(
        "options",
        [
            VideoOptions(video_codec=" "),
            VideoOptions(audio_codec=""),
            VideoOptions(crf=-1),
            VideoOptions(crf=52),
            VideoOptions(video_bitrate=0),
            VideoOptions(audio_bitrate=0),
            VideoOptions(frame_rate=0),
            VideoOptions(frame_rate=241),
            VideoOptions(scale=0),
            VideoOptions(scale=11),
            VideoOptions(preset="turbo"),
            VideoOptions(output_format="mp5"),
        ],
    )
    def test_invalid_raises(self, options: VideoOptions) -> None:
        with pytest.raises(ValueError):
            validate_options(options)


# ============================================================================ #
# 路径 / 滤镜 / 通配符
# ============================================================================ #
class TestOutputPath:
    """输出路径生成。"""

    def test_default_directory(self, tmp_path: Path) -> None:
        source = tmp_path / "clip.m4s"
        assert build_output_path(source, None, "mp4") == tmp_path / "clip_converted.mp4"

    def test_explicit_directory(self, tmp_path: Path) -> None:
        source = tmp_path / "clip.m4s"
        target = tmp_path / "out"
        assert build_output_path(source, target, "mkv") == target / "clip_converted.mkv"

    def test_avoids_double_suffix(self, tmp_path: Path) -> None:
        source = tmp_path / "clip_converted.m4s"
        assert build_output_path(source, None, ".mp4") == tmp_path / "clip_converted.mp4"


class TestScaleFilter:
    """缩放滤镜。"""

    @pytest.mark.parametrize("scale", [1.0, 1.0000000001])
    def test_identity(self, scale: float) -> None:
        assert build_scale_filter(scale) is None

    @pytest.mark.parametrize(("scale", "expected"), [(0.5, "scale=iw*0.5:ih*0.5"), (2, "scale=iw*2:ih*2")])
    def test_scaled(self, scale: float, expected: str) -> None:
        assert build_scale_filter(scale) == expected

    def test_invalid(self) -> None:
        with pytest.raises(ValueError, match="缩放"):
            build_scale_filter(0)


class TestExpandInputs:
    """通配符展开。"""

    def test_single_file(self, tmp_path: Path) -> None:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        assert expand_input_patterns([str(video)]) == [video]

    def test_glob_filters_and_dedupes(self, tmp_path: Path) -> None:
        (tmp_path / "a.mp4").write_bytes(b"x")
        (tmp_path / "b.mkv").write_bytes(b"x")
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.avi").write_bytes(b"x")

        found = expand_input_patterns(
            [str(tmp_path / "*.mp4"), str(tmp_path / "*.mp4"), str(tmp_path / "**" / "*.avi")]
        )
        assert [item.name for item in found] == ["a.mp4", "c.avi"]

    def test_directory_is_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "clip.mp4").mkdir()
        assert expand_input_patterns([str(tmp_path / "clip.mp4")]) == []

    def test_no_match(self, tmp_path: Path) -> None:
        assert expand_input_patterns([str(tmp_path / "*.mp4")]) == []


# ============================================================================ #
# 进度解析
# ============================================================================ #
class TestProgressParsing:
    """ffmpeg 输出解析。"""

    def test_parse_duration(self) -> None:
        assert parse_duration("  Duration: 00:01:23.45, start: 0.000") == pytest.approx(83.45)

    def test_parse_duration_without_fraction(self) -> None:
        assert parse_duration("Duration: 01:00:00") == pytest.approx(3600)

    def test_parse_duration_invalid(self) -> None:
        assert parse_duration("no duration here") is None

    def test_parse_progress_time(self) -> None:
        assert parse_progress_time("frame=10 time=00:00:05.25 bitrate=1") == pytest.approx(5.25)
        assert parse_progress_time("out_time=00:00:02.500000") == pytest.approx(2.5)

    def test_parse_progress_time_invalid(self) -> None:
        assert parse_progress_time("frame=10") is None

    @pytest.mark.parametrize(("current", "total", "expected"), [(0, 0, 0), (5, 10, 50), (20, 10, 100), (-1, 10, 0)])
    def test_progress_percent(self, current: float, total: float, expected: int) -> None:
        assert progress_percent(current, total) == expected


# ============================================================================ #
# 命令构建
# ============================================================================ #
class TestBuildCommand:
    """ffmpeg 参数构建。"""

    def test_default(self, tmp_path: Path) -> None:
        cmd = build_ffmpeg_command(tmp_path / "in.mp4", tmp_path / "out.mp4", VideoOptions())
        assert cmd[:4] == ["ffmpeg", "-y", "-hide_banner", "-i"]
        assert cmd[-1] == str(tmp_path / "out.mp4")
        assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
        assert "-crf" in cmd
        assert "-preset" in cmd
        assert "-r" not in cmd
        assert "-vf" not in cmd

    def test_frame_rate_and_scale(self, tmp_path: Path) -> None:
        cmd = build_ffmpeg_command(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            VideoOptions(frame_rate=30, scale=0.5),
        )
        assert cmd[cmd.index("-r") + 1] == "30"
        assert cmd[cmd.index("-vf") + 1] == "scale=iw*0.5:ih*0.5"

    def test_remove_audio(self, tmp_path: Path) -> None:
        cmd = build_ffmpeg_command(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            VideoOptions(remove_audio=True, audio_file=tmp_path / "bg.mp3"),
        )
        assert "-an" in cmd
        assert "-c:a" not in cmd
        assert str(tmp_path / "bg.mp3") not in cmd

    def test_external_audio(self, tmp_path: Path) -> None:
        cmd = build_ffmpeg_command(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            VideoOptions(audio_file=tmp_path / "bg.mp3"),
        )
        assert cmd.count("-i") == 2
        assert "1:a:0" in cmd
        assert cmd[cmd.index("-shortest")] == "-shortest"

    def test_non_crf_codec_uses_bitrate(self, tmp_path: Path) -> None:
        cmd = build_ffmpeg_command(
            tmp_path / "in.mp4",
            tmp_path / "out.mp4",
            VideoOptions(video_codec="mpeg4", video_bitrate=5000),
        )
        assert "-crf" not in cmd
        assert cmd[cmd.index("-b:v") + 1] == "5000k"

    def test_gif_output(self, tmp_path: Path) -> None:
        cmd = build_ffmpeg_command(
            tmp_path / "in.mp4",
            tmp_path / "out.gif",
            VideoOptions(output_format="gif"),
        )
        assert cmd[cmd.index("-c:v") + 1] == "gif"
        assert "-an" in cmd
        assert "-crf" not in cmd

    def test_invalid_options_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="CRF"):
            build_ffmpeg_command(tmp_path / "in.mp4", tmp_path / "out.mp4", VideoOptions(crf=99))


# ============================================================================ #
# ffmpeg 版本检测
# ============================================================================ #
class TestFfmpegVersion:
    """ffmpeg 可用性检测。"""

    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Result:
            returncode = 0
            stdout = "ffmpeg version 7.0\nbuilt with gcc\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
        assert ffmpeg_version() == "ffmpeg version 7.0"

    def test_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
        assert ffmpeg_version() is None

    def test_missing_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise FileNotFoundError("ffmpeg")

        monkeypatch.setattr(subprocess, "run", boom)
        assert ffmpeg_version() is None

    def test_empty_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Result:
            returncode = 0
            stdout = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())
        assert ffmpeg_version() == "ffmpeg"


# ============================================================================ #
# 执行层（fake ffmpeg 子进程）
# ============================================================================ #
class _FakeProcess:
    """模拟 ``subprocess.Popen``：按块输出 stderr，进程状态可控。"""

    def __init__(self, chunks: list[str], returncode: int = 0, delay: float = 0.0) -> None:
        self._chunks = list(chunks)
        self._delay = delay
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.exited = False
        self.stderr = self

    def read(self, _size: int = -1) -> str:
        if self._delay:
            time.sleep(self._delay)
        if not self._chunks:
            self.exited = True
            return ""
        return self._chunks.pop(0)

    def poll(self) -> int | None:
        return self.returncode if self.exited else None

    def wait(self, timeout: float | None = None) -> int:
        self.exited = True
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.exited = True

    def kill(self) -> None:
        self.killed = True
        self.exited = True


_STREAM = "Duration: 00:00:10.00, start: 0.0\rframe=1 time=00:00:05.00\r"


class TestExecute:
    """ffmpeg 子进程执行。"""

    def test_success_reports_progress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        process = _FakeProcess([_STREAM])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)
        events: list[tuple[int, str]] = []
        assert videoconv._execute_ffmpeg(["ffmpeg"], lambda pct, msg: events.append((pct, msg)), None) is True
        assert events[-1][0] == 100
        assert 50 in [item[0] for item in events]

    def test_failure_prints_error(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        process = _FakeProcess(["boom: invalid data\n"], returncode=1)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)
        assert videoconv._execute_ffmpeg(["ffmpeg"], None, None) is False
        assert "返回码 1" in capsys.readouterr().out

    def test_launch_failure(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise FileNotFoundError("ffmpeg")

        monkeypatch.setattr(subprocess, "Popen", boom)
        assert videoconv._execute_ffmpeg(["ffmpeg"], None, None) is False
        assert "启动 ffmpeg 失败" in capsys.readouterr().out

    def test_cancel_terminates_process(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        process = _FakeProcess(["frame=1\r"] * 100, delay=0.05)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)
        cancel = threading.Event()
        timer = threading.Timer(0.02, cancel.set)
        timer.start()
        try:
            assert videoconv._execute_ffmpeg(["ffmpeg"], None, cancel) is False
        finally:
            timer.cancel()
        assert process.terminated is True
        assert "转换已取消" in capsys.readouterr().out


class TestRunConversion:
    """入口校验与执行编排。"""

    def test_pre_cancelled(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cancel = threading.Event()
        cancel.set()
        assert run_conversion(tmp_path / "in.mp4", tmp_path / "out.mp4", VideoOptions(), cancel=cancel) is False
        assert "已取消" in capsys.readouterr().out

    def test_missing_input(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert run_conversion(tmp_path / "in.mp4", tmp_path / "out.mp4", VideoOptions()) is False
        assert "输入文件不存在" in capsys.readouterr().out

    def test_directory_input(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        source = tmp_path / "in.mp4"
        source.mkdir()
        assert run_conversion(source, tmp_path / "out.mp4", VideoOptions()) is False
        assert "输入文件不存在" in capsys.readouterr().out

    def test_unsupported_input(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        source = tmp_path / "in.txt"
        source.write_text("x", encoding="utf-8")
        assert run_conversion(source, tmp_path / "out.mp4", VideoOptions()) is False
        assert "不支持的输入格式" in capsys.readouterr().out

    def test_invalid_options(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"x")
        assert run_conversion(source, tmp_path / "out.mp4", VideoOptions(crf=99)) is False
        assert "CRF" in capsys.readouterr().out

    def test_missing_audio_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"x")
        options = VideoOptions(audio_file=tmp_path / "bg.mp3")
        assert run_conversion(source, tmp_path / "out.mp4", options) is False
        assert "外部音频文件不存在" in capsys.readouterr().out

    def test_output_dir_creation_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"x")

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("denied")

        monkeypatch.setattr(Path, "mkdir", boom)
        assert run_conversion(source, tmp_path / "out" / "out.mp4", VideoOptions()) is False
        assert "创建输出目录失败" in capsys.readouterr().out

    def test_success_delegates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"x")
        calls: list[list[str]] = []

        def fake_execute(cmd: list[str], progress: Any, cancel: Any) -> bool:
            calls.append(cmd)
            return True

        monkeypatch.setattr(videoconv, "_execute_ffmpeg", fake_execute)
        target = tmp_path / "out" / "out.mp4"
        assert run_conversion(source, target, VideoOptions()) is True
        assert calls and target.parent.is_dir()
        assert calls[0][-1] == str(target)


# ============================================================================ #
# CLI 命令
# ============================================================================ #
class TestCliCommand:
    """CLI 汇总输出。"""

    def test_invalid_options(self, capsys: pytest.CaptureFixture[str]) -> None:
        videoconv.videoconv_cmd(["a.mp4"], crf=99)
        assert "CRF" in capsys.readouterr().out

    def test_ffmpeg_missing(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(videoconv, "ffmpeg_version", lambda: None)
        videoconv.videoconv_cmd(["a.mp4"])
        assert "未找到 ffmpeg" in capsys.readouterr().out

    def test_no_input_files(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(videoconv, "ffmpeg_version", lambda: "ffmpeg")
        monkeypatch.setattr(videoconv, "expand_input_patterns", lambda inputs: [])
        videoconv.videoconv_cmd(["*.mp4"])
        assert "未找到受支持的视频文件" in capsys.readouterr().out

    def test_single_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "a.mp4"
        source.write_bytes(b"x")
        monkeypatch.setattr(videoconv, "ffmpeg_version", lambda: "ffmpeg")
        monkeypatch.setattr(videoconv, "expand_input_patterns", lambda inputs: [source])

        def fake_run(
            source: Path, target: Path, options: VideoOptions, progress: Any = None, cancel: Any = None
        ) -> bool:
            assert progress is not None
            progress(10, "转换中")
            progress(100, "转换完成")
            return True

        monkeypatch.setattr(videoconv, "run_conversion", fake_run)
        videoconv.videoconv_cmd([str(source)])
        out = capsys.readouterr().out
        assert "a_converted.mp4" in out
        assert "转换成功" in out

    def test_multi_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        files = []
        for name in ("a.mp4", "b.mkv"):
            path = tmp_path / name
            path.write_bytes(b"x")
            files.append(path)
        monkeypatch.setattr(videoconv, "ffmpeg_version", lambda: "ffmpeg")
        monkeypatch.setattr(videoconv, "expand_input_patterns", lambda inputs: files)
        monkeypatch.setattr(videoconv, "run_conversion", lambda *a, **k: False)
        videoconv.videoconv_cmd([str(tmp_path / "*")])
        out = capsys.readouterr().out
        assert "成功 0" in out
        assert "失败 2" in out


def test_constants_cover_common_formats() -> None:
    """常用输入输出格式均在支持列表中。"""
    for ext in (".mp4", ".m4s", ".mkv", ".avi", ".mov", ".ts", ".rmvb"):
        assert ext in SUPPORTED_INPUT_FORMATS
    assert ".gif" in OUTPUT_FORMATS
    assert "medium" in PRESETS
