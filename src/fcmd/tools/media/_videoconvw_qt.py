"""videoconvw 的 Qt 主窗口与入口。

界面分四段：输入与输出（输入视频 / 输出目录 / 命名提示）、转换设置（格式、
视频 / 音频编码器、码率、帧率、缩放、CRF、预设、音轨）、动作行（开始 / 停止 /
状态 / 重置）与进度条、日志。转换在后台线程执行，ffmpeg 的进度与日志经信号桥
回投 GUI 线程，可随时中止。

布局口径：路径输入用单行编辑框 + 浏览按钮，长路径由输入框自身横向滚动；日志
吸收剩余高度，窗口变矮时优先压缩日志区；状态标签用主题 Hint / Success / Danger
语义色，切换主题后同色系。
"""

from __future__ import annotations

import contextlib
import io
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QWidget,
)

from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, LogView, button, hint_label
from fcmd.gui.qt.workers import submit
from fcmd.tools.media.videoconv import (
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_AUDIO_CODEC,
    DEFAULT_CRF,
    DEFAULT_PRESET,
    DEFAULT_VIDEO_BITRATE,
    DEFAULT_VIDEO_CODEC,
    OUTPUT_FORMAT_CHOICES,
    PRESETS,
    SUPPORTED_INPUT_FORMATS,
    VideoOptions,
    build_output_path,
    ffmpeg_version,
    normalize_format,
    run_conversion,
    validate_options,
)
from fcmd.tools.media.videoconvw import (
    MIN_HEIGHT,
    MIN_WIDTH,
    VideoConvSettings,
    __version__,
    load_settings,
    parse_frame_rate,
    parse_scale,
    save_settings,
)

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme

__all__ = ["VideoConvWindow", "run_qt"]

DEFAULT_SIZE = (980, 760)

VIDEO_CODEC_CHOICES: tuple[str, ...] = ("libx264", "libx265", "libvpx-vp9", "mpeg4", "libxvid")
AUDIO_CODEC_CHOICES: tuple[str, ...] = ("aac", "mp3", "opus", "libvorbis", "ac3", "flac")

_AUDIO_FILTER = "音频文件 (*.mp3 *.wav *.aac *.flac *.ogg *.m4a *.wma);;所有文件 (*.*)"


class _ConvertBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台转换线程 → GUI 的事件桥。"""

    event = Signal(object)


def _work_convert(
    input_path: Path,
    output_path: Path,
    options: VideoOptions,
    cancel: threading.Event,
    bridge: _ConvertBridge,
) -> None:
    """线程池里执行一次转换，把进度、日志与结果逐条投回界面。"""

    def progress(percent: int, message: str) -> None:
        bridge.event.emit(("progress", percent, message))

    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            success = run_conversion(
                input_path,
                output_path,
                options,
                progress=progress,
                cancel=cancel,
            )
    except Exception as exc:  # 后台异常须转事件回主线程，避免界面卡在「转换中」
        captured.write(f"后台异常：{exc}\n")
        success = False
    for line in captured.getvalue().splitlines():
        if line.strip():
            bridge.event.emit(("log", line))
    bridge.event.emit(("done", success, output_path))


class VideoConvWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """视频转换器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._settings_override = settings_path
        self._running = False
        self._cancel = threading.Event()
        self._bridge: _ConvertBridge | None = None
        super().__init__(
            title=f"视频转换器 v{__version__}",
            config_name="videoconvw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._confirm_close,
            persist=self._persist,
            status="正在检测 ffmpeg…",
        )
        if settings_path is not None:
            self._settings_path = settings_path
            self._raw_settings = _guiconfig.read_settings(settings_path) or {}
        self._build()
        self._append_log("选择输入视频并设置转换参数后点击「开始转换」。")
        QTimer.singleShot(50, self._start_ffmpeg_check)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """输入输出 / 转换设置 / 动作行 / 进度条 / 日志 / 页脚。"""
        self.add(self._build_io_card())
        self.add(self._build_options_card())
        self.add(self._build_action_row())
        self.add(self._build_progress())
        self.add(self._build_log_card(), 1)
        self.add(self._build_footer())

    def _build_io_card(self) -> Card:
        """输入视频、输出目录与输出命名提示。"""
        card = Card("输入与输出", self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._input_edit = QLineEdit(self._settings.input_path, card)
        self._input_edit.setPlaceholderText("选择或输入要转换的视频文件")
        browse_input = button("浏览…")
        browse_input.clicked.connect(self._browse_input)

        self._output_edit = QLineEdit(self._settings.output_dir, card)
        self._output_edit.setPlaceholderText("留空时写入输入文件所在目录")
        browse_output = button("浏览…")
        browse_output.clicked.connect(self._browse_output_dir)

        grid.addWidget(QLabel("输入视频：", card), 0, 0)
        grid.addWidget(self._input_edit, 0, 1)
        grid.addWidget(browse_input, 0, 2)
        grid.addWidget(QLabel("输出目录：", card), 1, 0)
        grid.addWidget(self._output_edit, 1, 1)
        grid.addWidget(browse_output, 1, 2)
        grid.addWidget(
            hint_label("输出目录留空时写入输入文件所在目录，文件名为 <原名>_converted.<格式>"),
            2,
            0,
            1,
            3,
        )
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        return card

    def _build_options_card(self) -> Card:
        """输出格式、编码器、码率、帧率、缩放、CRF、预设与音轨。"""
        card = Card("转换设置", self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._format_combo = QComboBox(card)
        self._format_combo.addItems(list(OUTPUT_FORMAT_CHOICES))
        self._format_combo.setCurrentText(self._settings.output_format)

        self._video_codec_combo = QComboBox(card)
        self._video_codec_combo.setEditable(True)
        self._video_codec_combo.addItems(list(VIDEO_CODEC_CHOICES))
        self._video_codec_combo.setCurrentText(self._settings.video_codec)

        self._audio_codec_combo = QComboBox(card)
        self._audio_codec_combo.setEditable(True)
        self._audio_codec_combo.addItems(list(AUDIO_CODEC_CHOICES))
        self._audio_codec_combo.setCurrentText(self._settings.audio_codec)

        self._video_bitrate_spin = _spin(1, 50000, 100, self._settings.video_bitrate, card)
        self._audio_bitrate_spin = _spin(1, 1024, 32, self._settings.audio_bitrate, card)
        self._frame_rate_spin = _spin(0, 240, 1, self._settings.frame_rate, card)
        self._crf_spin = _spin(0, 51, 1, self._settings.crf, card)

        self._scale_spin = QDoubleSpinBox(card)
        self._scale_spin.setRange(0.1, 10.0)
        self._scale_spin.setSingleStep(0.1)
        self._scale_spin.setDecimals(1)
        self._scale_spin.setValue(self._settings.scale)

        self._preset_combo = QComboBox(card)
        self._preset_combo.addItems(list(PRESETS))
        self._preset_combo.setCurrentText(self._settings.preset)

        self._remove_audio_check = QCheckBox("移除音轨", card)
        self._remove_audio_check.setChecked(self._settings.remove_audio)

        self._audio_edit = QLineEdit(self._settings.audio_file, card)
        self._audio_edit.setPlaceholderText("可选：用于替换/合并的外部音频文件")
        browse_audio = button("浏览…")
        browse_audio.clicked.connect(self._browse_audio_file)

        _pair(grid, 0, ("输出格式：", self._format_combo), ("视频编码器：", self._video_codec_combo))
        _pair(grid, 1, ("音频编码器：", self._audio_codec_combo), ("视频比特率 (kbps)：", self._video_bitrate_spin))
        _pair(grid, 2, ("音频比特率 (kbps)：", self._audio_bitrate_spin), ("帧率 (0=源帧率)：", self._frame_rate_spin))
        _pair(grid, 3, ("分辨率缩放：", self._scale_spin), ("CRF 质量 (0-51)：", self._crf_spin))
        _pair(grid, 4, ("编码预设：", self._preset_combo), ("", self._remove_audio_check))
        grid.addWidget(QLabel("外部音轨：", card), 5, 0)
        grid.addWidget(self._audio_edit, 5, 1, 1, 2)
        grid.addWidget(browse_audio, 5, 3)
        for column in (1, 3):
            grid.setColumnStretch(column, 1)
        card.body.addLayout(grid)
        return card

    def _build_action_row(self) -> QWidget:
        """开始 / 停止 / 状态标签 / 重置。"""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._start_button = button("开始转换", kind="primary")
        self._start_button.clicked.connect(self._start_conversion)
        self._stop_button = button("停止", kind="danger")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._stop_conversion)
        self._status_label = QLabel("就绪", row)
        self._status_label.setObjectName("Hint")
        self._reset_button = button("重置设置")
        self._reset_button.clicked.connect(self._reset_settings)
        layout.addWidget(self._start_button)
        layout.addWidget(self._stop_button)
        layout.addWidget(self._status_label, 1)
        layout.addWidget(self._reset_button)
        return row

    def _build_progress(self) -> QProgressBar:
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        return self._progress

    def _build_log_card(self) -> Card:
        card = Card("日志", self)
        self._log = LogView(height=150, parent=card)
        self._log.setMinimumHeight(110)
        card.body.addWidget(self._log)
        return card

    def _build_footer(self) -> QWidget:
        footer = QWidget(self)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(hint_label("依赖系统 ffmpeg（含 ffprobe）", wrap=False))
        return footer

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _append_log(self, message: str) -> None:
        """追加一行带时间戳的日志。"""
        from datetime import datetime

        self._log.append(f"{datetime.now():%H:%M:%S} {message}")

    def _set_status(self, text: str, kind: str = "Hint") -> None:
        """更新动作行状态标签，并按 Hint / Success / Danger 切换语义色。"""
        self._status_label.setText(text)
        if self._status_label.objectName() != kind:
            self._status_label.setObjectName(kind)
            self._status_label.style().unpolish(self._status_label)
            self._status_label.style().polish(self._status_label)

    def _browse_input(self) -> None:
        """选择输入视频文件，并把输出目录同步到它所在目录。"""
        patterns = " ".join(f"*{item}" for item in SUPPORTED_INPUT_FORMATS)
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择输入视频",
            _parent_dir(self._input_edit.text()),
            f"视频文件 ({patterns});;所有文件 (*.*)",
        )
        if not filename:
            return
        self._input_edit.setText(filename)
        self._output_edit.setText(str(Path(filename).parent))

    def _browse_output_dir(self) -> None:
        """选择输出目录。"""
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output_edit.text())
        if directory:
            self._output_edit.setText(directory)

    def _browse_audio_file(self) -> None:
        """选择用于替换/合并的外部音频文件。"""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            _parent_dir(self._audio_edit.text()),
            _AUDIO_FILTER,
        )
        if filename:
            self._audio_edit.setText(filename)

    def _set_busy(self, busy: bool) -> None:
        """转换期间禁用按钮，避免重复提交。"""
        self._running = busy
        self._start_button.setEnabled(not busy)
        self._stop_button.setEnabled(busy)
        self._reset_button.setEnabled(not busy)

    def _collect_options(self) -> VideoOptions:
        """从界面控件收集并校验转换参数。"""
        audio_text = self._audio_edit.text().strip()
        options = VideoOptions(
            output_format=normalize_format(self._format_combo.currentText()),
            video_codec=self._video_codec_combo.currentText().strip(),
            audio_codec=self._audio_codec_combo.currentText().strip(),
            video_bitrate=self._video_bitrate_spin.value(),
            audio_bitrate=self._audio_bitrate_spin.value(),
            frame_rate=parse_frame_rate(self._frame_rate_spin.value()),
            scale=parse_scale(self._scale_spin.value()),
            crf=self._crf_spin.value(),
            preset=self._preset_combo.currentText(),
            remove_audio=self._remove_audio_check.isChecked(),
            audio_file=Path(audio_text).expanduser() if audio_text else None,
        )
        validate_options(options)
        return options

    def _start_ffmpeg_check(self) -> None:
        """后台检测 ffmpeg，避免启动时阻塞界面。"""
        submit(ffmpeg_version, on_done=self._apply_ffmpeg_version)

    def _apply_ffmpeg_version(self, version: str | None) -> None:
        """在状态栏显示 ffmpeg 版本或缺失提示。"""
        if version:
            self.set_status(f"✓ {version}")
            self._append_log(f"ffmpeg 可用：{version}")
        else:
            self.set_status("✗ 未找到 ffmpeg，请安装并加入 PATH")
            self._append_log("警告：未找到 ffmpeg，转换功能不可用")

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _start_conversion(self) -> None:
        """校验输入与参数，启动后台转换。"""
        if self._running:
            return

        raw_input = self._input_edit.text().strip()
        if not raw_input:
            QMessageBox.warning(self, "提示", "请选择要转换的视频文件")
            return
        input_path = Path(raw_input).expanduser()
        if not input_path.is_file():
            QMessageBox.critical(self, "错误", f"输入文件不存在：\n{input_path}")
            return
        if input_path.suffix.lower() not in SUPPORTED_INPUT_FORMATS:
            QMessageBox.critical(self, "错误", f"不支持的输入格式：{input_path.suffix or '(无扩展名)'}")
            return

        try:
            options = self._collect_options()
        except (ValueError, TypeError) as exc:
            QMessageBox.critical(self, "参数错误", str(exc))
            return

        if options.audio_file is not None and not options.remove_audio and not options.audio_file.is_file():
            QMessageBox.critical(self, "错误", f"外部音频文件不存在：\n{options.audio_file}")
            return

        raw_output_dir = self._output_edit.text().strip()
        output_dir = Path(raw_output_dir).expanduser() if raw_output_dir else None
        output_path = build_output_path(input_path, output_dir, options.output_format)

        self._append_log(f"输入：{input_path}")
        self._append_log(f"输出：{output_path}")
        self._cancel = threading.Event()
        self._progress.setValue(0)
        self._set_status("正在转换…")
        self._set_busy(True)

        bridge = _ConvertBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(
            _work_convert,
            input_path,
            output_path,
            options,
            self._cancel,
            bridge,
            on_error=self._on_worker_error,
        )

    def _on_worker_error(self, message: str) -> None:
        """线程池兜底异常：记日志、恢复界面。"""
        self._append_log(f"错误：{message}")
        self._set_status("✗ 转换失败", "Danger")
        self._set_busy(False)
        self._bridge = None

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：进度、日志与结果。"""
        kind = event[0]
        if kind == "progress":
            _, percent, message = event
            self._progress.setValue(percent)
            self._set_status(f"进度：{percent}% - {message}")
            self._append_log(message)
        elif kind == "log":
            self._append_log(str(event[1]))
        elif kind == "done":
            _, success, output_path = event
            self._finish(bool(success), output_path)

    def _finish(self, success: bool, output_path: Path) -> None:
        """转换结束：恢复按钮、显示结果并提示。"""
        self._set_busy(False)
        self._bridge = None
        if success:
            self._progress.setValue(100)
            self._set_status("✓ 转换成功", "Success")
            self._append_log(f"转换成功：{output_path}")
            QMessageBox.information(self, "转换完成", f"视频已生成：\n{output_path}")
        else:
            self._set_status("✗ 转换失败或已取消", "Danger")
            self._append_log("转换失败或已取消，详见日志")
            QMessageBox.warning(self, "转换失败", "转换失败或已取消，详见日志与命令行输出")

    def _stop_conversion(self) -> None:
        """请求中止后台转换。"""
        if not self._running:
            return
        self._cancel.set()
        self._set_status("正在停止…")
        self._append_log("已请求中止转换")

    def _reset_settings(self) -> None:
        """把界面参数恢复为默认值（不改变输入/输出路径）。"""
        self._format_combo.setCurrentText("mp4")
        self._video_codec_combo.setCurrentText(DEFAULT_VIDEO_CODEC)
        self._audio_codec_combo.setCurrentText(DEFAULT_AUDIO_CODEC)
        self._video_bitrate_spin.setValue(DEFAULT_VIDEO_BITRATE)
        self._audio_bitrate_spin.setValue(DEFAULT_AUDIO_BITRATE)
        self._frame_rate_spin.setValue(0)
        self._scale_spin.setValue(1.0)
        self._crf_spin.setValue(DEFAULT_CRF)
        self._preset_combo.setCurrentText(DEFAULT_PRESET)
        self._remove_audio_check.setChecked(False)
        self._audio_edit.clear()
        self._append_log("转换参数已重置为默认值")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _confirm_close(self) -> bool:
        """运行中关闭时先确认；确认后通知后台尽快停止。"""
        if not self._running:
            return True
        answer = QMessageBox.question(
            self,
            "确认",
            "转换正在进行中，确定要退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._cancel.set()
        return True

    def _persist(self, geometry: str) -> None:
        """关闭时把当前界面状态与 geometry 写入设置文件。"""
        try:
            output_format = normalize_format(self._format_combo.currentText()).lstrip(".")
        except ValueError:
            output_format = "mp4"
        save_settings(
            VideoConvSettings(
                input_path=self._input_edit.text(),
                output_dir=self._output_edit.text(),
                output_format=output_format,
                video_codec=self._video_codec_combo.currentText().strip() or DEFAULT_VIDEO_CODEC,
                audio_codec=self._audio_codec_combo.currentText().strip() or DEFAULT_AUDIO_CODEC,
                video_bitrate=self._video_bitrate_spin.value(),
                audio_bitrate=self._audio_bitrate_spin.value(),
                frame_rate=self._frame_rate_spin.value(),
                scale=self._scale_spin.value(),
                crf=self._crf_spin.value(),
                preset=self._preset_combo.currentText(),
                remove_audio=self._remove_audio_check.isChecked(),
                audio_file=self._audio_edit.text(),
                geometry=geometry,
            ),
            self._settings_path,
        )

    def set_theme(self, theme: Theme) -> None:
        """切换主题后按当前状态刷新语义色标签。"""
        super().set_theme(theme)
        if hasattr(self, "_status_label") and self._status_label.objectName() != "Hint":
            self._status_label.style().unpolish(self._status_label)
            self._status_label.style().polish(self._status_label)


def _spin(low: int, high: int, step: int, value: int, parent: QWidget) -> QSpinBox:
    """创建整数数值框（范围 / 步长 / 初值）。"""
    widget = QSpinBox(parent)
    widget.setRange(low, high)
    widget.setSingleStep(step)
    widget.setValue(value)
    return widget


def _pair(
    grid: QGridLayout,
    row: int,
    left: tuple[str, QWidget],
    right: tuple[str, QWidget],
) -> None:
    """在一行里放两组「标签 + 控件」；右标签为空时右侧控件单独占位。"""
    grid.addWidget(QLabel(left[0]), row, 0)
    grid.addWidget(left[1], row, 1)
    if right[0]:
        grid.addWidget(QLabel(right[0]), row, 2)
    grid.addWidget(right[1], row, 3)


def _parent_dir(text: str) -> str:
    """把输入框里的文件路径转成其所在目录；为空时返回空串。"""
    raw = text.strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().parent)


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: VideoConvWindow(settings_path), app_name="videoconvw")
