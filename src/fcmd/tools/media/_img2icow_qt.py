"""img2icow 的 Qt 主窗口与入口。

窗口只做「选输入 / 选输出 / 定尺寸与质量 / 起转换 / 展示日志」，转换本身仍在
:mod:`fcmd.infras.media`。批量转换放进 ``QThreadPool``，进度经信号桥排队回
GUI 线程。

布局口径：输入 / 输出 / 选项三张卡片纵向排列，输入框整行拉伸并在下方给出
长路径省略提示；窗口变矮时内容进 :class:`PageScroll`，日志占满剩余高度。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
)

from fcmd import __version__
from fcmd.gui.app import run_window
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.uistate import dialog_remember, dialog_start
from fcmd.gui.widgets import (
    Card,
    ElidedLabel,
    FormGrid,
    ProgressRow,
    button,
    hint_label,
    messages,
    pick_dir,
    pick_file,
    save_file,
)
from fcmd.gui.workers import EventBridge, submit
from fcmd.tools.media.img2icow import (
    QUALITY_CHOICES,
    Img2IcoSettings,
    load_settings,
    normalize_quality,
    parse_sizes_text,
    save_settings,
)

__all__ = ["Img2IcoWindow", "run_qt"]

DEFAULT_SIZE = (880, 640)
MIN_SIZE = (760, 560)

_IMAGE_FILTER = "图像文件 (*.svg *.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff);;所有文件 (*.*)"


def _run_conversion(
    input_path: Path,
    output_path: Path | None,
    sizes: list[int],
    quality: str,
    bridge: EventBridge,
) -> None:
    """在线程池里执行转换，把进度与结果投回界面。"""
    from fcmd.infras.media import convert_images

    def progress(done: int, total: int, message: str) -> None:
        bridge.event.emit(("progress", done, total, message))

    try:
        result = convert_images(
            input_path,
            output_path,
            fmt="win",
            sizes=sizes,
            quality=quality,
            progress=progress,
        )
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("done", result.outputs, result.used_sizes))


class Img2IcoWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """图像转 ICO 转换器主窗口。"""

    log_hint = "汇总输入、尺寸、转换进度与输出图标；复制、清空、导出见日志工具条。"

    def format_log_line(self, message: str) -> str:
        """任务日志前缀：``[HH:MM:SS]``。"""
        return f"[{time.strftime('%H:%M:%S')}] {message}"

    def __init__(self) -> None:
        self._settings = load_settings()
        self._last_open_dir = self._settings.last_open_dir
        self._last_save_dir = self._settings.last_save_dir
        self._busy = False
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"图像转 ICO 转换器 v{__version__}",
            config_name="img2icow",
            default_size=DEFAULT_SIZE,
            min_size=MIN_SIZE,
            persist=self._persist,
            scroll=True,
        )
        self._build()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """输入 / 输出 / 选项 / 操作行 / 进度 / 日志。"""
        self.add(self._build_input_card())
        self.add(self._build_output_card())
        self.add(self._build_options_card())

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        self._convert_button = button("开始转换", kind="primary")
        self._convert_button.clicked.connect(self._start_conversion)
        actions.addWidget(self._convert_button)
        self.add_layout(actions)

        self._progress_row = ProgressRow(parent=self)
        self._progress = self._progress_row.bar
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self.add(self._progress_row)

        self.log("选择输入图像或目录，填写尺寸后点击「开始转换」。")

    def _build_input_card(self) -> Card:
        """输入行：可手填路径，也可分别按「文件…」「目录…」挑选。"""
        card = Card("输入", self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._input = QLineEdit(self._settings.input_path, card)
        self._input.setPlaceholderText("输入图像文件或目录")
        self._input.textChanged.connect(self._refresh_input_hint)
        row.addWidget(self._input, 1)
        self._browse_file_button = button("文件…")
        self._browse_file_button.clicked.connect(self._browse_input_file)
        row.addWidget(self._browse_file_button)
        self._browse_dir_button = button("目录…")
        self._browse_dir_button.clicked.connect(self._browse_input_dir)
        row.addWidget(self._browse_dir_button)
        card.body.addLayout(row)
        self._input_hint = ElidedLabel("", parent=card)
        self._input_hint.setObjectName("Mono")
        card.body.addWidget(self._input_hint)
        self._refresh_input_hint(self._input.text())
        return card

    def _build_output_card(self) -> Card:
        """输出行：留空时按输入类型落到同目录或 ``ico_output/``。"""
        card = Card("输出", self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._output = QLineEdit(self._settings.output_path, card)
        self._output.setPlaceholderText("留空时自动决定输出位置")
        self._output.textChanged.connect(self._refresh_output_hint)
        row.addWidget(self._output, 1)
        self._browse_output_button = button("浏览…")
        self._browse_output_button.clicked.connect(self._browse_output)
        row.addWidget(self._browse_output_button)
        card.body.addLayout(row)
        self._output_hint = ElidedLabel("", parent=card)
        self._output_hint.setObjectName("Hint")
        card.body.addWidget(self._output_hint)
        self._refresh_output_hint(self._output.text())
        return card

    def _build_options_card(self) -> Card:
        """尺寸文本 + 质量档位。"""
        card = Card("选项", self)
        grid = FormGrid(card)
        self._sizes = QLineEdit(self._settings.sizes, card)
        self._sizes.setPlaceholderText("16,24,32,48,64,128,256")
        grid.add_row("图标尺寸", self._sizes)
        self._quality = QComboBox(card)
        self._quality.addItems(list(QUALITY_CHOICES))
        self._quality.setCurrentText(self._settings.quality)
        grid.add_pair("质量", self._quality)
        card.body.addWidget(grid)
        card.body.addWidget(hint_label("尺寸用逗号或空格分隔；留空输出位置时：单文件写同目录，目录输入写 ico_output/"))
        return card

    def _refresh_input_hint(self, text: str) -> None:
        """显示当前输入路径（长路径中间省略，悬停看全文）。"""
        self._input_hint.setText(str(Path(text).expanduser()) if text.strip() else "未选择输入")

    def _refresh_output_hint(self, text: str) -> None:
        """显示输出路径；留空时说明默认落点。"""
        if text.strip():
            self._output_hint.setText(f"输出到：{Path(text).expanduser()}")
        else:
            self._output_hint.setText("输出位置：留空时单文件写同目录，目录输入写 ico_output/")

    # ------------------------------------------------------------------ #
    # 选择路径
    # ------------------------------------------------------------------ #
    def _browse_input_file(self) -> None:
        start = dialog_start(self._last_open_dir) or self._input.text()
        filename = pick_file(self, "选择输入图像", start, _IMAGE_FILTER)
        if filename:
            self._last_open_dir = dialog_remember(filename)
            self._input.setText(filename)

    def _browse_input_dir(self) -> None:
        directory = pick_dir(
            self,
            "选择输入目录",
            dialog_start(self._last_open_dir) or self._input.text(),
        )
        if directory:
            self._last_open_dir = dialog_remember(directory)
            self._input.setText(directory)

    def _browse_output(self) -> None:
        """按输入类型决定选目录还是选文件。"""
        raw_input = self._input.text().strip()
        input_path = Path(raw_input).expanduser() if raw_input else None
        if input_path is not None and input_path.is_dir():
            directory = pick_dir(
                self,
                "选择输出目录",
                dialog_start(self._last_save_dir) or self._output.text(),
            )
            if directory:
                self._last_save_dir = dialog_remember(directory)
                self._output.setText(directory)
            return

        initial_dir = dialog_start(self._last_save_dir) or (
            str(input_path.parent) if input_path is not None and input_path.parent.exists() else ""
        )
        initial_file = f"{input_path.stem}.ico" if input_path is not None else "icon.ico"
        filename = save_file(
            self,
            "选择输出图标",
            str(Path(initial_dir) / initial_file) if initial_dir else initial_file,
            "ICO 图标 (*.ico);;所有文件 (*.*)",
        )
        if filename:
            self._last_save_dir = dialog_remember(filename)
            self._output.setText(filename)

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _set_busy(self, busy: bool) -> None:
        """转换期间禁用控件，避免重复提交。"""
        self._busy = busy
        for widget in (
            self._convert_button,
            self._browse_file_button,
            self._browse_dir_button,
            self._browse_output_button,
            self._input,
            self._output,
            self._sizes,
            self._quality,
        ):
            widget.setEnabled(not busy)

    def _start_conversion(self) -> None:
        """校验输入后在后台启动转换。"""
        if self._busy:
            return

        raw_input = self._input.text().strip()
        if not raw_input:
            self.log("错误：请选择输入图像或目录")
            return
        input_path = Path(raw_input).expanduser()
        if not input_path.exists():
            self.log(f"错误：输入路径不存在 {input_path}")
            return

        try:
            sizes = parse_sizes_text(self._sizes.text())
            quality = normalize_quality(self._quality.currentText())
        except ValueError as exc:
            self.log(f"错误：{exc}")
            return

        raw_output = self._output.text().strip()
        output_path = Path(raw_output).expanduser() if raw_output else None

        self.clear_log()
        self._set_busy(True)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self.set_status("正在转换…")
        self.log(f"输入：{input_path}")
        self.log(f"尺寸：{', '.join(str(size) for size in sizes)}；质量：{quality}")

        bridge = EventBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(_run_conversion, input_path, output_path, sizes, quality, bridge)

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：进度、完成、失败。"""
        kind = event[0]
        if kind == "progress":
            _, done, total, message = event
            self._progress.setRange(0, max(total, 1))
            self._progress.setValue(done)
            self.log(message)
        elif kind == "done":
            _, outputs, used_sizes = event
            self._progress.setRange(0, max(len(outputs), 1))
            self._progress.setValue(len(outputs))
            for output in outputs:
                self.log(f"已生成 {output}")
            self.log(f"完成：{len(outputs)} 个图标，{len(used_sizes)} 种尺寸")
            self.set_status(f"完成：{len(outputs)} 个图标")
            self._finish()
            messages.info(self, "转换完成", f"已生成 {len(outputs)} 个图标文件。")
        elif kind == "error":
            self.log(f"错误：{event[1]}")
            self.set_status("转换失败")
            self._finish()
            messages.error(self, "转换失败", event[1])

    def _finish(self) -> None:
        """任务结束：恢复控件并释放信号桥。"""
        self._set_busy(False)
        self._bridge = None

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回输入 / 输出 / 尺寸 / 质量与窗口 geometry。"""
        save_settings(
            Img2IcoSettings(
                input_path=self._input.text(),
                output_path=self._output.text(),
                sizes=self._sizes.text(),
                quality=self._quality.currentText(),
                last_open_dir=self._last_open_dir,
                last_save_dir=self._last_save_dir,
                geometry=geometry,
            )
        )


def run_qt() -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(Img2IcoWindow, app_name="img2icow")
