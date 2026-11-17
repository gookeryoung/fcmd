"""videoconvw Qt 窗口的离屏冒烟测试。

覆盖默认值、设置回填、参数收集、输入校验、转换事件流（进度 / 日志 / 成功 /
失败 / 异常）、中止、重置、ffmpeg 状态与关闭落盘。真实 ffmpeg 调用由
``test_cli_videoconv`` 覆盖，这里 monkeypatch 掉只验证界面编排。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application
from fcmd.tools.media import _videoconvw_qt
from fcmd.tools.media._videoconvw_qt import (
    VideoConvWindow,
    _ConvertBridge,
    _work_convert,
)
from fcmd.tools.media.videoconv import (
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_CRF,
    DEFAULT_PRESET,
    DEFAULT_VIDEO_BITRATE,
    DEFAULT_VIDEO_CODEC,
    VideoOptions,
)
from fcmd.tools.media.videoconvw import config_path


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录并应用默认主题。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    app = create_application("videoconvw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return base


# ============================================================================ #
# 构建与设置
# ============================================================================ #
def test_window_builds_with_defaults(home: Path) -> None:
    """默认格式 / 编码器 / 码率 / 帧率 / 缩放 / CRF / 预设与按钮状态。"""
    window = VideoConvWindow()
    try:
        assert window._input_edit.text() == ""
        assert window._output_edit.text() == ""
        assert window._format_combo.currentText() == "mp4"
        assert window._video_codec_combo.currentText() == DEFAULT_VIDEO_CODEC
        assert window._audio_codec_combo.currentText() == DEFAULT_AUDIO_CODEC
        assert window._video_bitrate_spin.value() == DEFAULT_VIDEO_BITRATE
        assert window._audio_bitrate_spin.value() == DEFAULT_AUDIO_BITRATE
        assert window._frame_rate_spin.value() == 0
        assert window._scale_spin.value() == 1.0
        assert window._crf_spin.value() == DEFAULT_CRF
        assert window._preset_combo.currentText() == DEFAULT_PRESET
        assert not window._remove_audio_check.isChecked()
        assert window._progress.value() == 0
        assert window._start_button.isEnabled()
        assert not window._stop_button.isEnabled()
        assert window._status_label.text() == "就绪"
        assert window.status_text() == "正在检测 ffmpeg…"
    finally:
        window.deleteLater()


def test_settings_restored(home: Path) -> None:
    """设置文件中的路径与编码参数逐项回填。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "input_path": "in.mkv",
                "output_dir": "out",
                "output_format": "MKV",
                "video_codec": "libx265",
                "audio_codec": "opus",
                "video_bitrate": 5000,
                "audio_bitrate": 192,
                "frame_rate": 60,
                "scale": 0.5,
                "crf": 20,
                "preset": "slow",
                "remove_audio": True,
                "audio_file": "bg.mp3",
            }
        ),
        encoding="utf-8",
    )
    window = VideoConvWindow()
    try:
        assert window._input_edit.text() == "in.mkv"
        assert window._output_edit.text() == "out"
        assert window._format_combo.currentText() == "mkv"
        assert window._video_codec_combo.currentText() == "libx265"
        assert window._audio_codec_combo.currentText() == "opus"
        assert window._video_bitrate_spin.value() == 5000
        assert window._audio_bitrate_spin.value() == 192
        assert window._frame_rate_spin.value() == 60
        assert window._scale_spin.value() == 0.5
        assert window._crf_spin.value() == 20
        assert window._preset_combo.currentText() == "slow"
        assert window._remove_audio_check.isChecked()
        assert window._audio_edit.text() == "bg.mp3"
    finally:
        window.deleteLater()


def test_custom_settings_path_isolated(home: Path, tmp_path: Path) -> None:
    """自定义设置路径优先于默认 ``ui/videoconvw.json``。"""
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({"output_format": "webm"}), encoding="utf-8")
    window = VideoConvWindow(custom)
    try:
        assert window._format_combo.currentText() == "webm"
        window.close()
    finally:
        window.deleteLater()
    assert custom.is_file()
    assert not config_path().exists()


# ============================================================================ #
# 参数收集
# ============================================================================ #
def test_collect_options_reads_widgets(home: Path) -> None:
    """界面控件收集为 ``VideoOptions``，帧率 0 表示保持源帧率。"""
    window = VideoConvWindow()
    try:
        window._format_combo.setCurrentText("mkv")
        window._video_codec_combo.setCurrentText("libx265")
        window._audio_codec_combo.setCurrentText("opus")
        window._video_bitrate_spin.setValue(4000)
        window._audio_bitrate_spin.setValue(96)
        window._frame_rate_spin.setValue(0)
        window._scale_spin.setValue(0.5)
        window._crf_spin.setValue(18)
        window._preset_combo.setCurrentText("slow")
        window._remove_audio_check.setChecked(True)
        options = window._collect_options()
        assert options.output_format == ".mkv"
        assert options.video_codec == "libx265"
        assert options.audio_codec == "opus"
        assert options.video_bitrate == 4000
        assert options.audio_bitrate == 96
        assert options.frame_rate is None
        assert options.scale == 0.5
        assert options.crf == 18
        assert options.preset == "slow"
        assert options.remove_audio is True
    finally:
        window.deleteLater()


# ============================================================================ #
# 输入校验
# ============================================================================ #
def test_start_without_input_warns(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未选择输入文件时只提示，不启动转换。"""
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a: warnings.append(text) or None)
    window = VideoConvWindow()
    try:
        window._start_conversion()
        assert warnings == ["请选择要转换的视频文件"]
        assert not window._running
    finally:
        window.deleteLater()


def test_start_with_missing_file_errors(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """输入文件不存在时弹错误框。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, text, *a: errors.append(text) or None)
    window = VideoConvWindow()
    try:
        window._input_edit.setText(str(tmp_path / "missing.mp4"))
        window._start_conversion()
        assert "输入文件不存在" in errors[0]
    finally:
        window.deleteLater()


def test_start_with_unsupported_suffix_errors(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扩展名不在支持列表时弹错误框。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, text, *a: errors.append(text) or None)
    source = tmp_path / "note.txt"
    source.write_text("x", encoding="utf-8")
    window = VideoConvWindow()
    try:
        window._input_edit.setText(str(source))
        window._start_conversion()
        assert "不支持的输入格式" in errors[0]
    finally:
        window.deleteLater()


def test_start_with_missing_audio_errors(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """外部音轨不存在且未勾选移除音轨时弹错误框。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, text, *a: errors.append(text) or None)
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    window = VideoConvWindow()
    try:
        window._input_edit.setText(str(source))
        window._audio_edit.setText(str(tmp_path / "missing.mp3"))
        window._start_conversion()
        assert "外部音频文件不存在" in errors[0]
    finally:
        window.deleteLater()


def test_start_submits_worker(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """校验通过后计算输出路径、切换忙碌态并提交后台任务。"""
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    captured: dict[str, Any] = {}

    def fake_submit(fn: Any, *args: Any, **kwargs: Any) -> None:
        captured["fn"] = fn
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(_videoconvw_qt, "submit", fake_submit)
    window = VideoConvWindow()
    try:
        window._input_edit.setText(str(source))
        window._output_edit.setText(str(tmp_path / "out"))
        window._start_conversion()
        assert captured["fn"] is _work_convert
        input_path, output_path, options, cancel, bridge = captured["args"]
        assert input_path == source
        assert output_path == tmp_path / "out" / "in_converted.mp4"
        assert isinstance(options, VideoOptions)
        assert cancel is window._cancel
        assert isinstance(bridge, _ConvertBridge)
        assert window._running
        assert not window._start_button.isEnabled()
        assert window._stop_button.isEnabled()
        assert window._status_label.text() == "正在转换…"
        assert str(output_path) in window._log.text()
    finally:
        window._set_busy(False)
        window.deleteLater()


# ============================================================================ #
# 转换事件流
# ============================================================================ #
def test_worker_success_event(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功：进度置 100、状态转成功色、日志回传命令行输出并提示。"""
    infos: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _p, _t, text, *a: infos.append(text) or QMessageBox.StandardButton.Ok,
    )

    def fake_run(
        _input: Path,
        _output: Path,
        _options: VideoOptions,
        progress: Any = None,
        cancel: Any = None,
    ) -> bool:
        assert progress is not None
        assert cancel is not None
        progress(40, "转换中 4.0s / 10.0s")
        print("ffmpeg 输出行")
        return True

    monkeypatch.setattr(_videoconvw_qt, "run_conversion", fake_run)
    window = VideoConvWindow()
    try:
        window._set_busy(True)
        bridge = _ConvertBridge()
        bridge.event.connect(window._on_event)
        _work_convert(Path("in.mp4"), Path("out.mp4"), VideoOptions(), threading.Event(), bridge)

        assert window._progress.value() == 100
        assert window._status_label.text() == "✓ 转换成功"
        assert window._status_label.objectName() == "Success"
        assert "转换中 4.0s / 10.0s" in window._log.text()
        assert "ffmpeg 输出行" in window._log.text()
        assert window._start_button.isEnabled()
        assert window._stop_button.isEnabled() is False
        assert infos and "out.mp4" in infos[0]
    finally:
        window.deleteLater()


def test_worker_failure_event(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """失败：状态转危险色并弹警告框。"""
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a: warnings.append(text) or None)
    monkeypatch.setattr(_videoconvw_qt, "run_conversion", lambda *a, **k: False)
    window = VideoConvWindow()
    try:
        window._set_busy(True)
        bridge = _ConvertBridge()
        bridge.event.connect(window._on_event)
        _work_convert(Path("in.mp4"), Path("out.mp4"), VideoOptions(), threading.Event(), bridge)

        assert window._status_label.text() == "✗ 转换失败或已取消"
        assert window._status_label.objectName() == "Danger"
        assert "转换失败或已取消，详见日志" in window._log.text()
        assert warnings == ["转换失败或已取消，详见日志与命令行输出"]
        assert window._start_button.isEnabled()
    finally:
        window.deleteLater()


def test_worker_exception_event(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """后台异常转成失败事件，日志记录异常文本。"""
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    def _fail(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(_videoconvw_qt, "run_conversion", _fail)
    window = VideoConvWindow()
    try:
        bridge = _ConvertBridge()
        bridge.event.connect(window._on_event)
        _work_convert(Path("in.mp4"), Path("out.mp4"), VideoOptions(), threading.Event(), bridge)

        assert "后台异常：boom" in window._log.text()
        assert window._status_label.objectName() == "Danger"
    finally:
        window.deleteLater()


# ============================================================================ #
# 中止 / 重置 / ffmpeg 状态
# ============================================================================ #
def test_stop_sets_cancel_event(home: Path) -> None:
    """「停止」置位取消事件并切换到停止中状态。"""
    window = VideoConvWindow()
    try:
        window._set_busy(True)
        window._stop_conversion()
        assert window._cancel.is_set()
        assert window._status_label.text() == "正在停止…"
        assert "已请求中止转换" in window._log.text()
    finally:
        window._set_busy(False)
        window.deleteLater()


def test_stop_when_idle_is_noop(home: Path) -> None:
    """未在转换时「停止」不置位取消事件。"""
    window = VideoConvWindow()
    try:
        window._stop_conversion()
        assert not window._cancel.is_set()
    finally:
        window.deleteLater()


def test_reset_settings_restores_defaults(home: Path) -> None:
    """「重置设置」把所有参数恢复默认，保留输入 / 输出路径。"""
    window = VideoConvWindow()
    try:
        window._input_edit.setText("in.mp4")
        window._output_edit.setText("out")
        window._format_combo.setCurrentText("webm")
        window._video_bitrate_spin.setValue(9999)
        window._frame_rate_spin.setValue(60)
        window._scale_spin.setValue(0.5)
        window._remove_audio_check.setChecked(True)
        window._reset_settings()
        assert window._format_combo.currentText() == "mp4"
        assert window._video_codec_combo.currentText() == DEFAULT_VIDEO_CODEC
        assert window._video_bitrate_spin.value() == DEFAULT_VIDEO_BITRATE
        assert window._frame_rate_spin.value() == 0
        assert window._scale_spin.value() == 1.0
        assert window._crf_spin.value() == DEFAULT_CRF
        assert window._preset_combo.currentText() == DEFAULT_PRESET
        assert not window._remove_audio_check.isChecked()
        assert window._audio_edit.text() == ""
        assert window._input_edit.text() == "in.mp4"
        assert window._output_edit.text() == "out"
    finally:
        window.deleteLater()


def test_ffmpeg_version_status(home: Path) -> None:
    """检测到 ffmpeg 时状态栏显示版本，缺失时显示安装提示。"""
    window = VideoConvWindow()
    try:
        window._apply_ffmpeg_version("ffmpeg version 6.0")
        assert window.status_text() == "✓ ffmpeg version 6.0"
        assert "ffmpeg 可用" in window._log.text()

        window._apply_ffmpeg_version(None)
        assert window.status_text().startswith("✗ 未找到 ffmpeg")
        assert "转换功能不可用" in window._log.text()
    finally:
        window.deleteLater()


# ============================================================================ #
# 忙碌态 / 关闭落盘
# ============================================================================ #
def test_busy_disables_buttons(home: Path) -> None:
    """转换期间开始 / 重置禁用、停止可用，结束后恢复。"""
    window = VideoConvWindow()
    try:
        window._set_busy(True)
        assert not window._start_button.isEnabled()
        assert window._stop_button.isEnabled()
        assert not window._reset_button.isEnabled()

        window._set_busy(False)
        assert window._start_button.isEnabled()
        assert not window._stop_button.isEnabled()
        assert window._reset_button.isEnabled()
    finally:
        window.deleteLater()


def test_confirm_close_running(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """运行中关闭：拒绝时保持打开，确认后置位取消并允许关闭。"""
    window = VideoConvWindow()
    try:
        window._set_busy(True)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        assert window._confirm_close() is False
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        assert window._confirm_close() is True
        assert window._cancel.is_set()
    finally:
        window._set_busy(False)
        window.deleteLater()


def test_confirm_close_when_idle(home: Path) -> None:
    """未在转换时直接允许关闭。"""
    window = VideoConvWindow()
    try:
        assert window._confirm_close() is True
    finally:
        window.deleteLater()


def test_close_persists_ui_state(home: Path) -> None:
    """关闭时写回输入 / 输出、编码参数与 geometry。"""
    window = VideoConvWindow()
    try:
        window.show()
        window._input_edit.setText("in.mkv")
        window._output_edit.setText("out")
        window._format_combo.setCurrentText("webm")
        window._video_codec_combo.setCurrentText("libvpx-vp9")
        window._audio_bitrate_spin.setValue(96)
        window._frame_rate_spin.setValue(30)
        window._scale_spin.setValue(0.5)
        window._crf_spin.setValue(30)
        window._preset_combo.setCurrentText("veryslow")
        window._remove_audio_check.setChecked(True)
        window._audio_edit.setText("bg.mp3")
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert raw["input_path"] == "in.mkv"
    assert raw["output_dir"] == "out"
    assert raw["output_format"] == "webm"
    assert raw["video_codec"] == "libvpx-vp9"
    assert raw["audio_bitrate"] == 96
    assert raw["frame_rate"] == 30
    assert raw["scale"] == 0.5
    assert raw["crf"] == 30
    assert raw["preset"] == "veryslow"
    assert raw["remove_audio"] is True
    assert raw["audio_file"] == "bg.mp3"
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]
