"""locllmw 的 Qt 主窗口与入口。

窗口只做「壳」的工作：创建共享状态、组装七个页面、把状态信号接到状态栏与
跨页动作，其余逻辑都在各页模块。页面按需构建（模型 / 对话 / 实验室 / 设置
首屏不可见），概览、运行、监测三页建窗即构建：前者要展示硬件缓存，运行页
要出模型列表，监测页要在整个会话期间接收日志。

``run_qt`` 负责创建 ``QApplication``、应用主题并进入事件循环；Tk 回退入口
仍在 :mod:`fcmd.apps.locllm.locllmw`（``--ui tk``）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from fcmd.apps.locllm._locllmw_qt_monitor import MonitorPage
from fcmd.apps.locllm._locllmw_qt_overview import OverviewPage
from fcmd.apps.locllm._locllmw_qt_run import RunPage
from fcmd.apps.locllm._locllmw_qt_state import LocllmState
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.window import QtMainWindow

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["LocllmWindow", "create_application", "run_qt"]

TITLE = "本地 LLM 服务"
NAV_TITLE = "locllm"
DEFAULT_SIZE = (1180, 900)
MIN_SIZE = (1040, 780)

PAGES = (
    ("overview", "概览", "服务状态与快捷入口"),
    ("models", "模型", "GGUF 模型与显存适配"),
    ("run", "运行", "档位、上下文与启动参数"),
    ("chat", "对话", "多轮流式对话"),
    ("lab", "实验室", "代理 / 画图 / 视频 / 音频规划"),
    ("monitor", "监测", "指标、负载、日志与性能测试"),
    ("settings", "设置", "存储、引擎与关于"),
)
"""侧边栏页面顺序与提示；与 Tk 版的七页口径一致。"""


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """创建（或复用）``QApplication``：应用名与组织名写进 Qt 设置根。"""
    existing = QApplication.instance()
    if existing is not None:
        return existing  # type: ignore[return-value]
    app = QApplication(list(argv) if argv is not None else list(sys.argv))
    app.setApplicationName("locllmw")
    app.setOrganizationName("fcmd")
    return app


class LocllmWindow(QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """locllmw 主窗口：共享状态 + 七个页面 + 状态栏 / 日志通道。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self.state = LocllmState(settings_path)
        super().__init__(
            title=TITLE,
            config_name="locllmw",
            nav_title=NAV_TITLE,
            default_size=DEFAULT_SIZE,
            min_size=MIN_SIZE,
            on_close=self._on_close,
            persist=self.state.persist,
            sidebar_collapsed=self.state.settings.sidebar_collapsed,
        )
        self.overview = OverviewPage(self.state)
        self.run_page = RunPage(self.state)
        self.monitor_page = MonitorPage(self.state)
        self._chat_page: Any = None
        self._models_page: Any = None
        self._settings_page: Any = None

        for key, label, hint in PAGES:
            self.add_page(key, label, hint=hint, factory=self._factory(key))

        self.state.statusText.connect(self.set_status)
        self.state.navigateRequested.connect(self.show_page)
        self.state.logLine.connect(self.monitor_page.log_view.append)
        self.state.sampleCapacityChanged.connect(self.run_page.update_service_and_fit)
        self.state.recommendationApplied.connect(self.run_page.apply_recommendation)
        self.state.modelsChanged.connect(self._on_models_changed)
        self.nav.collapsedChanged.connect(self._persist_sidebar_collapsed)
        self.run_page.name_combo.currentTextChanged.connect(self.monitor_page.set_target_name)

        self.set_status("正在读取服务与模型目录…")
        self.state.refresh_services()
        self.state.refresh_models()
        if self.state.hardware is None:
            self.state.detect_hardware()
        else:
            self.overview.refresh_hardware()
        self.monitor_page.set_target_name(self.run_page.name_combo.currentText())

    # ------------------------------------------------------------------ #
    # 页面工厂
    # ------------------------------------------------------------------ #
    def _factory(self, key: str) -> Any:
        """返回页面构建函数；未迁移的重页面延迟到首次切页再实例化。"""
        builders = {
            "overview": lambda: self.overview,
            "models": self._build_models_page,
            "run": lambda: self.run_page,
            "chat": self._build_chat_page,
            "lab": self._build_lab_page,
            "monitor": lambda: self.monitor_page,
            "settings": self._build_settings_page,
        }
        return builders[key]

    def _build_models_page(self) -> QWidget:
        from fcmd.apps.locllm._locllmw_qt_models import ModelsPage

        page = ModelsPage(self.state, fit_resolver=self.run_page.fit_text_for)
        page.tree.modelSelected.connect(lambda path: self.state.select_model(path, update_name=True))
        page.tree.refresh()
        self.run_page.fitChanged.connect(page.tree.refresh)
        self._models_page = page
        return page

    def _build_chat_page(self) -> QWidget:
        from fcmd.apps.locllm._locllmw_qt_chat import ChatPage

        page = ChatPage(self.state)
        self._chat_page = page
        return page

    def _build_lab_page(self) -> QWidget:
        from fcmd.apps.locllm._locllmw_qt_lab import LabPage

        return LabPage(self.state)

    def _build_settings_page(self) -> QWidget:
        from fcmd.apps.locllm._locllmw_qt_settings import SettingsPage

        page = SettingsPage(self.state)
        self._settings_page = page
        return page

    # ------------------------------------------------------------------ #
    # 联动
    # ------------------------------------------------------------------ #
    def _on_models_changed(self, outcome: Any) -> None:
        """扫描完成：健康横幅、设置页来源与聊天服务列表同步刷新。"""
        issue = getattr(outcome, "issue", None)
        engine_missing = getattr(outcome, "engine_missing", False)
        port_busy = getattr(outcome, "port_busy", False)
        self.run_page.update_health(issue, engine_missing, port_busy)
        count = len(self.state.models)
        if issue or engine_missing or port_busy:
            problems = [
                text
                for text in (issue, "引擎未安装" if engine_missing else "", "默认端口被占用" if port_busy else "")
                if text
            ]
            self.set_status("注意：" + "；".join(problems))
        else:
            gpu = f"GPU {self.state.gpu_mib // 1024} GB" if self.state.gpu_mib else "CPU 推理"
            self.set_status(f"环境就绪 · {count} 个模型 · {gpu}")
        self.overview.refresh_hardware()
        if self._settings_page is not None:
            self._settings_page.refresh()
        if self._chat_page is not None:
            self._chat_page.refresh_services()

    def _persist_sidebar_collapsed(self, collapsed: bool) -> None:
        """折叠状态属于窗口设置，随 ``locllmw.json`` 一起持久化。"""
        self.state.settings.sidebar_collapsed = collapsed
        self.state.persist()

    def _on_close(self) -> bool:
        """关窗前确认进行中的任务，落盘设置并停掉采样 / 生成。"""
        if self.state.busy:
            answer = QMessageBox.question(self, "任务进行中", "启动 / 停止任务仍在执行，确实退出？")
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if self._chat_page is not None:
            self._chat_page.close()
        self.monitor_page.stop()
        self.state.closing = True
        return True


def run_qt(settings_path: Path | None = None) -> None:
    """创建 Qt 应用与主窗口并进入事件循环（阻塞到窗口关闭）。"""
    app = create_application()
    _qt_theme.apply_theme(app, _qt_theme.load_theme())
    window = LocllmWindow(settings_path)
    window.show()
    app.exec()
