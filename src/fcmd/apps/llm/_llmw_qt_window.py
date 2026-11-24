"""llmw 的 Qt 主窗口与入口。

窗口只做「壳」的工作：创建共享状态、组装五个页面、把状态信号接到状态栏与
跨页动作，其余逻辑都在各页模块。页面按需构建（模型 / 对话 / 设置首屏不可见），概览、运行两页建窗即构建：前者要展示硬件缓存并持续采样，
运行页要出模型列表。画图不再是独立页：在对话页勾选「画图」出图，
画图引擎的安装 / 状态在运行页。

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
from fcmd.apps.llm._llmw_qt_state import SERVICE_WATCH_MS, LlmState
from fcmd.gui import style as _qt_theme
from fcmd.gui.page import PageSpec
from fcmd.gui.window import QtMainWindow
from fcmd.gui.workers import periodic

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["LlmWindow", "create_application", "run_qt"]

TITLE = "LLM · 本地 LLM 服务"
NAV_TITLE = "LLM"
DEFAULT_SIZE = (1180, 900)
MIN_SIZE = (1040, 600)

PAGES: tuple[PageSpec, ...] = (
    PageSpec("overview", "概览", "系统信息、实时性能与智能推荐", "overview"),
    PageSpec("models", "模型", "GGUF 模型与显存适配", "models"),
    PageSpec("run", "运行", "档位、上下文与启动参数", "run"),
    PageSpec("chat", "对话", "多轮流式对话 / 画图（sd.cpp）", "chat"),
    PageSpec("flow", "工作流", "节点编排：提示词 → LLM / 出图 → 保存", "workflow"),
    PageSpec("settings", "设置", "存储、引擎、下载与转换", "settings"),
)
"""侧边栏页面顺序与提示；系统采样与实时性能已并入概览页。"""


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """创建（或复用）``QApplication``（委托共享工厂，保留本模块旧入口）。"""
    from fcmd.gui.app import create_application as shared_create_application

    return shared_create_application("llmw", argv)


class LlmWindow(QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """llmw 主窗口：共享状态 + 五个页面 + 状态栏 / 日志通道。"""

    log_action_text = "服务日志…"
    log_title = "服务日志"
    log_hint = "汇总服务启动 / 停止、模型扫描与下载等操作；复制、清空、导出见日志工具条。"

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
            theme_app="llmw",
        )
        self.overview = OverviewPage(self.state)
        self.run_page = RunPage(self.state)
        self._chat_page: Any = None
        self._flow_page: Any = None
        self._models_page: Any = None
        self._settings_page: Any = None
        self._bench_dialog: Any = None

        saved_page = self.state.settings.active_page
        self.add_pages(PAGES, {spec.key: self._factory(spec.key) for spec in PAGES})
        self.show_page(saved_page if any(spec.key == saved_page for spec in PAGES) else "overview")

        self.state.statusText.connect(self.set_status)
        self.state.pageStatus.connect(self.set_page_status)
        self.state.navigateRequested.connect(self.show_page)
        self.state.serveModelRequested.connect(self._serve_model)
        self.state.paintModelRequested.connect(self._serve_paint_model)
        self.state.loadingTextChanged.connect(self.show_loading)
        self.state.modelsUpdatedAtChanged.connect(self._show_models_updated_at)
        self._show_models_updated_at(self.state.models_updated_at or "")
        self.state.sampleCapacityChanged.connect(self.run_page.update_service_and_fit)
        # 运行中模型数量：侧边栏「运行」页按钮上的圆形徽标。
        # 画图探针在 RunPage 构造时已同步出结果，早于这里的 connect；先补一次，
        # 重启应用后只跑着 sd-server 时徽标也不会丢。
        self.run_page.runningChanged.connect(self._sync_run_badge)
        self._sync_run_badge(self.run_page.running_counts())
        self.state.recommendationApplied.connect(self.run_page.apply_recommendation)
        self.state.modelsChanged.connect(self._on_models_changed)
        self.nav.collapsedChanged.connect(self._persist_sidebar_collapsed)

        self.set_status("正在读取服务与模型目录…")
        self.state.refresh_services()
        # 服务看护：外部（CLI / 其他终端）启停或进程意外退出时，概览「运行中的模型」、
        # 运行页下拉的「✓ 已启动」、模型页「已启动」按钮都要自己跟上，不能等用户操作。
        self._services_watch = periodic(self.state, SERVICE_WATCH_MS, self.state.watch_services)
        self.state.refresh_models()
        snapshot = self.state.hardware
        # 旧快照没有驱动版本时重测一次，适配标签才能判断 CUDA 驱动是否过旧。
        if snapshot is None or (snapshot.gpu and not snapshot.gpu_driver):
            self.state.detect_hardware()
        else:
            self.overview.refresh_hardware()

    def _sync_run_badge(self, counts: object) -> None:
        """运行页的模型数量变化：把总数写到侧边栏「运行」按钮的徽标上。"""
        total = sum(counts.values()) if isinstance(counts, dict) else 0
        self.nav.set_badge("run", total)

    # ------------------------------------------------------------------ #
    # 菜单 / 日志
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        """共享菜单 + 「帮助 → 性能测试… / 服务日志…」（放在「关于」之前）。"""
        super()._build_menu()
        bench_action = QAction("性能测试…", self)
        bench_action.triggered.connect(self.show_bench)
        # 日志入口由公共组件在 super() 里插好，性能测试排在它前面。
        self.help_menu.insertAction(self.log_action or self.about_action, bench_action)
        self.bench_action = bench_action
        self.service_log_action = self.log_action

    def show_service_log(self) -> None:
        """打开（或聚焦）服务日志窗口；历史来自 ``LlmState`` 缓冲。"""
        self.show_log_dialog()

    def show_log_dialog(self, title: Any = None, hint: Any = None, *, parent: Any = None) -> None:
        """日志窗口的缓冲来自共享状态（页面都往 ``LlmState`` 写日志）。"""
        self.state.show_log_dialog(
            title or self.log_title,
            hint if hint is not None else self.log_hint,
            parent=parent or self,
        )

    def close_log_dialog(self) -> None:
        """窗口关闭时收起共享状态持有的日志窗口。"""
        self.state.close_log_dialog()

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
            "flow": self._build_flow_page,
            "settings": self._build_settings_page,
        }
        return builders[key]

    def _build_flow_page(self) -> QWidget:
        from fcmd.apps.llm._llmw_qt_flow import WorkflowPage

        page = WorkflowPage(self.state)
        self._flow_page = page
        return page

    def _build_models_page(self) -> QWidget:
        from fcmd.apps.llm._llmw_qt_center import ModelCenterPage

        page = ModelCenterPage(self.state)
        page.browse.downloadFinished.connect(self._on_download_finished)
        self._models_page = page
        return page

    def _on_download_finished(self) -> None:
        """模型下载完成：立刻重扫画图资产，并刷新 LLM 模型列表。

        扩散权重不进 LLM 模型列表，``refresh_models`` 发出的 ``modelsChanged`` 能
        带动画图资产扫描；这里额外直接刷一次，保证「刚下完就切到运行 → 画图」
        能看到新模型，而不必等到下一次全局刷新。
        """
        self.run_page.refresh_paint_assets()
        self.state.refresh_models()

    def _build_chat_page(self) -> QWidget:
        from fcmd.apps.llm._llmw_qt_chat import ChatPage

        page = ChatPage(self.state)
        self._chat_page = page
        return page

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
        self.run_page.refresh_paint_assets()

    def show_page(self, key: str) -> None:
        """切页时记住当前页；关窗时由 ``state.persist`` 写回设置。"""
        super().show_page(key)
        if key in {spec.key for spec in PAGES}:
            self.state.settings.active_page = key

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

    def _serve_paint_model(self, path: object) -> None:
        """模型页扩散模型快捷启动：切到运行页「画图」并启动 sd-server。"""
        if not isinstance(path, Path):
            return
        self.show_page("run")
        self.run_page.start_paint_model(path)

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
