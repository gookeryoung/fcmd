"""llmw 的 Qt 主窗口与入口。

窗口只做「壳」的工作：创建共享状态、组装七个页面、把状态信号接到状态栏与
跨页动作，其余逻辑都在各页模块。页面按需构建（模型 / 对话 / 实验室 / 设置
首屏不可见），概览、运行两页建窗即构建：前者要展示硬件缓存并持续采样，
运行页要出模型列表。

服务日志与性能测试不占分页，从顶部「帮助」菜单以非模态窗口打开；两者都复用
共享状态（日志缓冲 / 运行中服务），关窗不丢历史与结果。

``run_qt`` 负责创建 ``QApplication``、应用主题并进入事件循环；
:mod:`fcmd.apps.llm.llmw` 只保留工具注册与启动委托。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from fcmd.apps.llm._llmw_icon import window_icon
from fcmd.apps.llm._llmw_qt_overview import OverviewPage
from fcmd.apps.llm._llmw_qt_run import RunPage
from fcmd.apps.llm._llmw_qt_state import LlmState
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.window import QtMainWindow

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["LlmWindow", "create_application", "run_qt"]

TITLE = "LLM · 本地 LLM 服务"
NAV_TITLE = "LLM"
DEFAULT_SIZE = (1180, 900)
MIN_SIZE = (1040, 600)

PAGES = (
    ("overview", "概览", "系统信息、实时性能与智能推荐"),
    ("models", "模型", "GGUF 模型与显存适配"),
    ("run", "运行", "档位、上下文与启动参数"),
    ("chat", "对话", "多轮流式对话"),
    ("lab", "实验室", "代理 / 画图 / 视频 / 音频规划"),
    ("settings", "设置", "存储、下载、转换与引擎"),
)
"""侧边栏页面顺序与提示；系统采样与实时性能已并入概览页。"""


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """创建（或复用）``QApplication``（委托共享工厂，保留本模块旧入口）。"""
    from fcmd.gui.qt.app import create_application as shared_create_application

    return shared_create_application("llmw", argv)


class LlmWindow(QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """llmw 主窗口：共享状态 + 七个页面 + 状态栏 / 日志通道。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self.state = LlmState(settings_path)
        super().__init__(
            title=TITLE,
            config_name="llmw",
            nav_title=NAV_TITLE,
            default_size=DEFAULT_SIZE,
            min_size=MIN_SIZE,
            on_close=self._on_close,
            persist=self.state.persist,
            sidebar_collapsed=self.state.settings.sidebar_collapsed,
            icon=window_icon(),
            settings_path=settings_path,
        )
        self.overview = OverviewPage(self.state)
        self.run_page = RunPage(self.state)
        self._chat_page: Any = None
        self._models_page: Any = None
        self._settings_page: Any = None
        self._log_dialog: Any = None
        self._bench_dialog: Any = None

        for key, label, hint in PAGES:
            self.add_page(key, label, hint=hint, factory=self._factory(key))

        self.state.statusText.connect(self.set_status)
        self.state.navigateRequested.connect(self.show_page)
        self.state.serveModelRequested.connect(self._serve_model)
        self.state.loadingTextChanged.connect(self.show_loading)
        self.state.modelsUpdatedAtChanged.connect(self._show_models_updated_at)
        self._show_models_updated_at(self.state.models_updated_at or "")
        self.state.sampleCapacityChanged.connect(self.run_page.update_service_and_fit)
        self.state.recommendationApplied.connect(self.run_page.apply_recommendation)
        self.state.modelsChanged.connect(self._on_models_changed)
        self.nav.collapsedChanged.connect(self._persist_sidebar_collapsed)
        self.state.selectionChanged.connect(lambda *_args: self.overview.set_target_name(self.run_page.service_name()))

        self.set_status("正在读取服务与模型目录…")
        self.state.refresh_services()
        self.state.refresh_models()
        if self.state.hardware is None:
            self.state.detect_hardware()
        else:
            self.overview.refresh_hardware()
        self.overview.set_target_name(self.run_page.service_name())

    # ------------------------------------------------------------------ #
    # 菜单 / 日志
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        """共享菜单 + 「帮助 → 性能测试… / 服务日志…」（放在「关于」之前）。"""
        super()._build_menu()
        bench_action = QAction("性能测试…", self)
        bench_action.triggered.connect(self.show_bench)
        self.help_menu.insertAction(self.about_action, bench_action)
        log_action = QAction("服务日志…", self)
        log_action.setShortcut("Ctrl+Shift+L")
        log_action.triggered.connect(self.show_service_log)
        self.help_menu.insertAction(self.about_action, log_action)
        self.help_menu.insertSeparator(self.about_action)
        self.bench_action = bench_action
        self.service_log_action = log_action

    def show_service_log(self) -> None:
        """打开（或聚焦）服务日志窗口；历史来自 ``LlmState`` 缓冲。"""
        if self._log_dialog is None:
            from fcmd.apps.llm._llmw_qt_log import ServiceLogDialog

            self._log_dialog = ServiceLogDialog(self.state, self)
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()

    def show_bench(self) -> None:
        """打开（或聚焦）性能测试窗口；被测服务来自共享状态。"""
        if self._bench_dialog is None:
            from fcmd.apps.llm._llmw_qt_bench import BenchDialog

            self._bench_dialog = BenchDialog(self.state, self)
        self._bench_dialog.show()
        self._bench_dialog.raise_()
        self._bench_dialog.activateWindow()

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
            "settings": self._build_settings_page,
        }
        return builders[key]

    def _build_models_page(self) -> QWidget:
        from fcmd.apps.llm._llmw_qt_center import ModelCenterPage

        page = ModelCenterPage(self.state)
        self._models_page = page
        return page

    def _build_chat_page(self) -> QWidget:
        from fcmd.apps.llm._llmw_qt_chat import ChatPage

        page = ChatPage(self.state)
        self._chat_page = page
        return page

    def _build_lab_page(self) -> QWidget:
        from fcmd.apps.llm._llmw_qt_lab import LabPage

        return LabPage(self.state)

    def _build_settings_page(self) -> QWidget:
        from fcmd.apps.llm._llmw_qt_settings import SettingsPage

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
        if self._models_page is not None:
            self._models_page.refresh_config()
        if self._settings_page is not None:
            self._settings_page.refresh()
        if self._chat_page is not None:
            self._chat_page.refresh_services()

    def _persist_sidebar_collapsed(self, collapsed: bool) -> None:
        """折叠状态属于窗口设置，随 ``llmw.json`` 一起持久化。"""
        self.state.settings.sidebar_collapsed = collapsed
        self.state.persist()

    def _show_models_updated_at(self, stamp: str) -> None:
        """状态栏常驻显示模型列表最近更新时间。"""
        from fcmd.apps.llm._llmw_models import format_models_updated

        self.set_status_note(format_models_updated(stamp))

    def _serve_model(self, path: object) -> None:
        """模型页快捷启动：切到运行页并选中该模型后立即启动。"""
        if not isinstance(path, Path):
            return
        self.show_page("run")
        self.run_page.start_model(path)

    def _on_close(self) -> bool:
        """关窗前确认进行中的任务，落盘设置并停掉采样 / 生成。"""
        if self.state.busy:
            answer = QMessageBox.question(self, "任务进行中", "启动 / 停止任务仍在执行，确实退出？")
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if self._models_page is not None and not self._models_page.close():
            return False
        if self._chat_page is not None:
            self._chat_page.close()
        if self._log_dialog is not None:
            self._log_dialog.close()
        if self._bench_dialog is not None:
            self._bench_dialog.stop()
            self._bench_dialog.close()
        self.run_page.persist_params()
        self.overview.stop()
        self.state.closing = True
        return True


def run_qt(settings_path: Path | None = None) -> None:
    """创建 Qt 应用与主窗口并进入事件循环（阻塞到窗口关闭）。"""
    app = create_application()
    app.setWindowIcon(window_icon())
    _qt_theme.apply_theme(app, _qt_theme.load_theme())
    window = LlmWindow(settings_path)
    window.show()
    app.exec()
