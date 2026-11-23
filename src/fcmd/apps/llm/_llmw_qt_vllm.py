"""vLLM 引擎设置卡与安装对话框。

设置页复用 :class:`EngineCard` 的既有布局：版本列表 / 设为默认 / 安装按钮 /
引擎目录行；只有「安装」换成 vLLM 专属对话框（选版本 + 在 WSL 内建独立 venv）。
平台不支持时整个分区收进 :class:`Collapsible` 并禁用内容，避免误以为可用。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import _llmvllm
from fcmd.apps.llm import _llmw_settings as settings_logic
from fcmd.apps.llm._llmw_models import open_path
from fcmd.apps.llm._llmw_qt_engine_card import EngineCard, EngineCardConfig, EngineOption
from fcmd.gui.widgets import (
    Collapsible,
    PathRow,
    StatusTag,
    button,
    fit_dialog_to_content,
    hint_label,
)
from fcmd.gui.workers import ProgressBridge, submit_daemon

__all__ = ["VllmInstallDialog", "build_vllm_engine_card"]


def build_vllm_engine_card(
    parent: QWidget | None = None,
    platform: _llmvllm.VllmPlatform | None = None,
) -> QWidget:
    """构造 vLLM 引擎卡；不支持时返回折叠且禁用的说明分区。"""
    current = platform or _llmvllm.detect_platform()
    if not current.supported:
        return _unsupported_section(parent, current)

    status = StatusTag(f"可用 · {current.reason}", tone="accent", parent=parent)
    status.setToolTip(current.reason)
    note = hint_label(
        "vLLM 只接受 HF safetensors 模型目录（config.json + *.safetensors）；"
        "GGUF 继续用 llama.cpp。安装会在 WSL / 本机创建独立 venv，不影响其它引擎。",
        wrap=True,
    )
    root_row = PathRow(
        value=str(_llmvllm.engines_root()),
        placeholder="vLLM 引擎目录",
        directory=True,
        show_reset=True,
        browse_text="选择目录",
        parent=parent,
    )

    def load() -> list[EngineOption]:
        options: list[EngineOption] = []
        for engine in _llmvllm.list_engines():
            backend = "WSL" if engine.in_wsl else "Linux"
            options.append(
                EngineOption(
                    tag=engine.tag,
                    exe=Path(engine.python),
                    current=engine.source == "current",
                    display_name=f"vLLM {engine.tag}",
                    badge=backend,
                    badge_tone="accent",
                    badge_tooltip=engine.launcher,
                    tooltip=f"vLLM {engine.tag}\n{engine.python}\n{engine.launcher}",
                    compat_label=engine.distro or "Linux",
                    compat_tooltip=f"venv: {engine.venv}",
                )
            )
        return options

    def use(tag: str) -> None:
        try:
            _llmvllm.use_engine(tag)
        except Exception as exc:
            QMessageBox.warning(parent, "切换失败", str(exc))
            return
        card.refresh()

    def install(card_widget: EngineCard) -> None:
        latest = _llmvllm.detect_platform(use_cache=False)
        if not latest.ready and latest.stopped:
            # 探测已停止的实例等于启动它（wsl -d），必须先让用户确认。
            names = "、".join(latest.stopped)
            answer = QMessageBox.question(
                card_widget,
                "启动 WSL 实例",
                f"检测 vLLM 需要启动以下已停止的实例：\n{names}\n\n"
                "启动后会在实例内探测 Python（不会改动实例内容）。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            latest = _llmvllm.detect_platform(use_cache=False, allow_start=True)
        if not latest.ready:
            QMessageBox.warning(card_widget, "无法安装", latest.reason)
            return
        dialog = VllmInstallDialog(card_widget, platform=latest)
        dialog.installed.connect(lambda _tag: card_widget.refresh())
        dialog.exec()
        card_widget.refresh()

    def save_root(value: str) -> None:
        try:
            settings_logic.apply_path("vllm_engines_root", value)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(parent, "保存失败", str(exc))
            return
        root_row.set_value(str(_llmvllm.engines_root()))
        card.refresh()

    root_row.committed.connect(save_root)

    card: EngineCard = EngineCard(
        EngineCardConfig(
            title="推理引擎（vLLM）",
            list_object_name="VllmEngineList",
            empty_text="还没有安装 vLLM：点击「安装 vLLM」从 PyPI 选择版本安装（WSL 内建 venv）。",
            load=load,
            use=use,
            install=install,
            install_text="安装 vLLM",
            install_tooltip="选择 vLLM 版本，在 WSL / 本机创建独立 venv 并安装；不影响 llama.cpp",
            root_text=lambda: str(_llmvllm.engines_root()),
            open_root=lambda: open_path(_llmvllm.engines_root()),
            open_text="打开引擎目录",
            root_widget=lambda _parent: root_row,
            refresh_root=lambda widget: (
                widget.set_value(str(_llmvllm.engines_root())) if isinstance(widget, PathRow) else None
            ),
            header_widgets=(status, note),
        ),
        parent,
    )
    if not current.ready:
        card.install_button.setEnabled(False)
        card.install_button.setToolTip(f"暂不可安装：{current.reason}")
    return card


def _unsupported_section(
    parent: QWidget | None,
    platform: _llmvllm.VllmPlatform,
) -> Collapsible:
    """不支持平台：折叠分区 + 禁用内容，并说明启用条件。"""
    section = Collapsible("推理引擎（vLLM）", expanded=False, parent=parent)
    panel = QWidget(section)
    panel.setEnabled(False)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(StatusTag("当前系统不可用", tone="muted", parent=panel))
    layout.addWidget(
        hint_label(
            f"{platform.reason}\n"
            "启用条件：Linux 原生，或 Windows + WSL2（GPU 直通）。"
            "其余系统上该区域保持折叠且不会写入任何 vLLM 配置。",
            wrap=True,
        )
    )
    section.body.addWidget(panel)
    section.setToolTip(platform.reason)
    return section


class VllmInstallDialog(QDialog):  # pragma: no cover - Qt 控件
    """选择版本并在 WSL / 本机安装 vLLM（独立 venv）。"""

    installed = Signal(str)

    def __init__(self, parent: QWidget, *, platform: _llmvllm.VllmPlatform) -> None:
        super().__init__(parent)
        self.setWindowTitle("安装 vLLM")
        self.setModal(True)
        self._platform = platform
        self._busy = False
        self._bridge = ProgressBridge(self)
        self._bridge.progress.connect(self._append_line)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(hint_label(platform.reason, wrap=True))

        target = QHBoxLayout()
        target.addWidget(QLabel("安装目标", self))
        self.interpreter_combo = QComboBox(self)
        for item in platform.interpreters:
            label = item.label + (f" · {item.gpu}" if item.gpu else "")
            self.interpreter_combo.addItem(label, item)
        target.addWidget(self.interpreter_combo, 1)
        layout.addLayout(target)

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("vLLM 版本", self))
        self.version_combo = QComboBox(self)
        self.version_combo.setEditable(True)
        self.version_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.version_combo.lineEdit().setPlaceholderText("正在读取 PyPI 版本列表…")
        version_row.addWidget(self.version_combo, 1)
        layout.addLayout(version_row)

        self.status_label = QLabel("准备就绪", self)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("Log")
        self.log_view.setMinimumHeight(160)
        self.log_view.setPlaceholderText("安装日志会显示在这里（pip 下载与构建可能需要较长时间）")
        layout.addWidget(self.log_view, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.install_button = button("开始安装", kind="primary", parent=self)
        self.install_button.clicked.connect(self._start_install)
        self.close_button = button("取消", parent=self)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.install_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        fit_dialog_to_content(self, min_width=560)
        self._load_versions()

    # ------------------------------------------------------------------ #
    # 版本列表
    # ------------------------------------------------------------------ #
    def _load_versions(self) -> None:
        def done(versions: object) -> None:
            if not isinstance(versions, tuple) or not versions:
                return
            self.version_combo.clear()
            self.version_combo.addItems([str(item) for item in versions])
            self.version_combo.setCurrentIndex(0)
            self.version_combo.lineEdit().setPlaceholderText("选择或输入 vLLM 版本号")

        def failed(message: str) -> None:
            self.version_combo.lineEdit().setPlaceholderText("无法读取版本列表，请手动输入版本号")
            self.status_label.setText(f"版本列表获取失败：{message}")

        submit_daemon(_llmvllm.fetch_versions, on_done=done, on_error=failed)

    # ------------------------------------------------------------------ #
    # 安装
    # ------------------------------------------------------------------ #
    def _append_line(self, line: object) -> None:
        text = str(line).rstrip()
        if not text:
            return
        self.log_view.appendPlainText(text)
        self.status_label.setText(text[-160:])

    def _start_install(self) -> None:
        if self._busy:
            return
        tag = self.version_combo.currentText().strip()
        if not tag:
            QMessageBox.warning(self, "缺少版本", "请选择或输入 vLLM 版本号")
            return
        interpreter = self.interpreter_combo.currentData()
        if not isinstance(interpreter, _llmvllm.VllmInterpreter):
            QMessageBox.warning(self, "缺少目标", "没有可用的 Python 解释器")
            return

        def work() -> _llmvllm.VllmEngine:
            return _llmvllm.install_engine(tag, interpreter=interpreter, on_output=self._bridge.progress.emit)

        self._set_busy(True, f"正在安装 vLLM {tag}…（首次安装需下载数 GB 依赖）")
        submit_daemon(
            work,
            on_done=self._on_installed,
            on_error=self._on_install_failed,
            on_finished=lambda: self._set_busy(False),
        )

    def _on_installed(self, engine: object) -> None:
        if isinstance(engine, _llmvllm.VllmEngine):
            self.status_label.setText(f"已安装 vLLM {engine.tag}")
            self.installed.emit(engine.tag)
        else:
            self.status_label.setText("安装完成")
        self.close_button.setText("完成")

    def _on_install_failed(self, message: str) -> None:
        self.status_label.setText("安装失败，详见日志")
        self.log_view.appendPlainText(f"\n[失败] {message}")
        QMessageBox.critical(self, "安装失败", message)

    def _set_busy(self, busy: bool, note: str | None = None) -> None:
        self._busy = busy
        self.install_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.version_combo.setEnabled(not busy)
        self.interpreter_combo.setEnabled(not busy)
        if note:
            self.status_label.setText(note)
