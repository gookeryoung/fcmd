"""locllmw Qt 窗口的离屏冒烟测试：七页可构建、主题可切换、设置可落盘。

用 ``QT_QPA_PLATFORM=offscreen`` 在无显示器环境（CI / 本地）跑通整窗构建，
模型扫描与硬件检测替换成空实现，避免测试触盘 / 调 ``nvidia-smi``。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from fcmd.apps.locllm import _locllmw_qt_state as qt_state
from fcmd.apps.locllm._locllmw_qt_window import LocllmWindow, create_application
from fcmd.apps.locllm.llmbase import EngineInfo
from fcmd.gui import theme as _guitheme


@pytest.fixture()
def window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """构建离线窗口：扫描 / 检测 / 采样全部打桩，关闭时自动清理。

    引擎解析打桩成假引擎：CI / 无引擎机器上 ``preview`` 走正常路径而不是
    抛 ServeError 弹出模态错误框（模态框无人点击会让测试永久挂死）。
    模态弹窗整体打桩兜底：任何错误分支都不允许在测试里阻塞主线程。
    """
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    monkeypatch.setattr(qt_state.LocllmState, "refresh_models", lambda self, port=None: None)
    monkeypatch.setattr(qt_state.LocllmState, "detect_hardware", lambda self, on_done=None: None)
    monkeypatch.setattr(
        qt_state.llmserve.llmengine,
        "resolve_engine",
        lambda tag=None, home=None: EngineInfo("test-engine", Path("/nonexistent/llama-server"), "test", ()),
    )
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args, **_kw: shown.append("critical") or None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kw: QMessageBox.StandardButton.No)
    create_application([])
    win = LocllmWindow(tmp_path / "ui" / "locllmw.json")
    win.monitor_page.stop()
    yield win
    win.close()


def test_first_page_is_built_immediately(window: Any) -> None:
    """首屏页面必须已经实例化，不能停在空占位控件上（点击已选中项不会触发切换）。"""
    assert window.stack.currentWidget() is window.page("overview")
    assert window.current_page() == "overview"


def test_status_reflects_scan_result(window: Any) -> None:
    """扫描结束后状态栏不能停在「正在读取服务与模型目录…」。"""
    from types import SimpleNamespace

    window._on_models_changed(SimpleNamespace(issue=None, engine_missing=False, port_busy=False))
    status = window.status_text()
    assert "环境就绪" in status
    assert "个模型" in status
    window._on_models_changed(SimpleNamespace(issue="模型目录不存在", engine_missing=True, port_busy=False))
    status = window.status_text()
    assert status.startswith("注意：")
    assert "引擎未安装" in status


def test_window_builds_all_pages(window: Any) -> None:
    keys = window.nav.keys()
    assert keys == ("overview", "models", "run", "chat", "lab", "monitor", "settings")
    for key in keys:
        window.show_page(key)
        assert window.current_page() == key
        assert window.page(key) is not None


def test_generated_pages_expose_core_widgets(window: Any) -> None:
    window.show_page("models")
    models = window.page("models")
    assert models.browse is not None
    window.show_page("chat")
    chat = window.page("chat")
    assert chat.service_combo.count() >= 1
    chat.reset()
    assert "新对话" in chat.status.text()
    window.show_page("settings")
    settings_page = window.page("settings")
    settings_page.refresh()
    assert settings_page.models_path.value()


def test_loading_bar_sits_right_in_status_bar(window: Any) -> None:
    """加载提示放在状态栏右侧，不覆盖左侧状态文字。"""
    assert window.statusBar().isAncestorOf(window.loading_bar)
    window.set_status("环境就绪")
    window.show_loading("正在加载第 1 页…")
    assert window.loading_bar.is_running()
    assert window.loading_bar.text_label.text() == "正在加载第 1 页…"
    assert window.status_text() == "环境就绪"
    window.show_loading("正在扫描本地模型…")
    assert window.loading_bar.text_label.text() == "正在扫描本地模型…"
    window.show_loading("")
    assert not window.loading_bar.is_running()
    assert window.status_text() == "环境就绪"
    # 多任务共用状态栏右端：后开始的显示，结束后回退到仍在进行的任务。
    window.state.begin_loading("hardware", "正在检测硬件…")
    window.state.begin_loading("search", "正在加载第 1 页…")
    assert window.loading_bar.text_label.text() == "正在加载第 1 页…"
    window.state.end_loading("search")
    assert window.loading_bar.text_label.text() == "正在检测硬件…"
    window.state.end_loading("hardware")
    assert not window.loading_bar.is_running()
    assert not hasattr(window.overview, "loading")
    window.show_page("models")
    assert not hasattr(window.page("models").browse, "loading")


def test_models_page_fresh_cache_skips_network(window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """新鲜缓存命中时直接展示缓存页，不再提交在线搜索任务。"""
    from fcmd.apps.locllm import _locllmw_list_cache as cache_mod
    from fcmd.apps.locllm import _locllmw_qt_center as center
    from fcmd.apps.locllm.llmdown import ModelSearch, ModelSummary

    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(cache_mod, "default_cache_path", lambda: cache_path)
    key = cache_mod.cache_key("", 1, 30, "default")
    cache_mod.ModelListCache(cache_path).put(
        key,
        ModelSearch((ModelSummary(repo="org/cached"),), total=1, page=1, page_size=30),
    )
    submitted: list[object] = []
    real_submit = center.submit

    def _record(fn: Any, *args: Any, **kwargs: Any) -> Any:
        submitted.append(fn)
        return real_submit(fn, *args, **kwargs)

    monkeypatch.setattr(center, "submit", _record)
    window.show_page("models")
    browse = window.page("models").browse
    assert browse._last_result is not None
    assert browse._last_result.items[0].repo == "org/cached"
    assert browse._search_work not in submitted


def test_models_page_name_filter_is_debounced(window: Any) -> None:
    """上一页左侧的名称过滤框只在停止输入后重建列表。"""
    window.show_page("models")
    browse = window.page("models").browse
    assert browse.name_filter.placeholderText().startswith("按名称筛选")
    assert browse.name_filter.isClearButtonEnabled()
    assert browse._filter_timer.isSingleShot()
    assert browse._filter_timer.interval() == 250
    browse.name_filter.setText("qwen")
    assert browse._filter_timer.isActive()
    assert browse._name_filter == ""
    browse._apply_name_filter()
    assert browse._name_filter == "qwen"


def test_chat_parameter_icon_buttons_toggle_sections(window: Any) -> None:
    """连接 / 生成参数入口是刷新右侧的图标按钮，点击展开对应分区。"""
    window.show_page("chat")
    chat = window.page("chat")
    assert chat.connection_button.text() == "🔗"
    assert chat.generation_button.text() == "⚙"
    assert not chat.connection_section.is_expanded()
    chat.connection_button.click()
    assert chat.connection_section.is_expanded()
    chat.connection_button.click()
    assert not chat.connection_section.is_expanded()
    chat.generation_button.click()
    assert chat.settings_section.is_expanded()
    chat.generation_button.click()
    assert not chat.settings_section.is_expanded()


def test_run_page_model_combo_selects_model(window: Any) -> None:
    """运行页顶部下拉框列出模型，选中后同步共享状态与硬件提示。"""
    from fcmd.apps.locllm.llmbase import ModelEntry

    root = Path("/models")
    model = ModelEntry(
        root=root, path=root / "org" / "small.gguf", size=1024**3, profiles=("balanced",), tags=("对话",)
    )
    window.state.models = [model]
    window.state.models_root = root
    window.state.running_paths = set()
    window.state.gpu_mib = 32768
    window.state.gpu_free_mib = 32000
    page = window.run_page
    page.on_models_changed()
    assert page.model_combo.count() == 1
    assert "small.gguf" in page.model_combo.itemText(0)
    assert "未启动" not in page.model_combo.itemText(0)
    assert page.model_combo.minimumHeight() >= 40
    assert not hasattr(page, "manage_models_button")
    assert not hasattr(page, "browse_button")
    assert not hasattr(page, "clear_button")
    page.model_combo.setCurrentIndex(0)
    assert window.state.selected_model == model.path
    assert page.mode_choice.key() in {"speed", "balanced", "long", "max-context", "low-vram"}
    assert page.model_action_button.text() == "启动服务"
    assert page.model_action_button.isEnabled()
    assert not hasattr(page, "stop_selected_button")
    assert not hasattr(page, "stop_all_button")
    window.state.set_busy(True)
    assert not page.model_action_button.isEnabled()
    window.state.set_busy(False)


def test_run_page_form_defaults(window: Any, tmp_path: Path) -> None:
    run = window.run_page
    assert run.mode_choice.key() == "balanced"
    assert run.kv_choice.key() == ""
    assert not hasattr(run, "profile_choice")
    assert not hasattr(run, "tune_choice")
    assert not hasattr(run, "health_label")
    assert not hasattr(run, "health_button")
    assert run.mode_choice._buttons["speed"].property("tone") == "danger"
    assert run.host_edit.text() == "127.0.0.1"
    assert run.port_edit.text() == "8080"
    # CI / 无引擎机器上没有真实模型目录，垫一个可启动的模型再预览
    from fcmd.apps.locllm.llmbase import ModelEntry

    model_file = tmp_path / "demo-Q4_K_XL.gguf"
    model_file.write_bytes(b"\0" * 64)
    window.state.models = [ModelEntry(root=tmp_path, path=model_file, size=64, profiles=("balanced",), tags=("对话",))]
    window.state.models_root = tmp_path
    run.on_models_changed()
    run._refresh_command_preview()
    command = run.command_preview.toPlainText()
    assert "demo-Q4_K_XL.gguf" in command
    assert "--model" in command or "-m" in command
    run._fill_params()
    custom = run.extra_args_edit.toPlainText()
    assert custom.startswith("-ngl 99")
    assert "demo-Q4_K_XL.gguf" not in custom
    assert "--alias" in custom and "--host 127.0.0.1" in custom
    # 与预览一致：填入完整参数后不会在命令里重复追加一遍
    assert run.command_preview.toPlainText().count("-ngl") == 1


def test_run_page_restores_last_model_and_per_model_params(window: Any, tmp_path: Path) -> None:
    from fcmd.apps.locllm.llmbase import ModelEntry

    first_path = tmp_path / "a.gguf"
    second_path = tmp_path / "b.gguf"
    first_path.write_bytes(b"a")
    second_path.write_bytes(b"bb")
    first = ModelEntry(root=tmp_path, path=first_path, size=10)
    second = ModelEntry(root=tmp_path, path=second_path, size=20)
    window.state.models = [first, second]
    window.state.models_root = tmp_path
    window.state.selected_model = None
    window.state.settings.last_model = str(first.path)
    page = window.run_page
    page.on_models_changed()
    assert window.state.selected_model == first.path
    page.port_edit.setText("9123")
    page.model_combo.setCurrentIndex(1)
    page.model_combo.setCurrentIndex(0)
    assert page.port_edit.text() == "9123"


def test_theme_switch_persists(window: Any, tmp_path: Path) -> None:
    window.set_theme(_guitheme.resolve_theme("light"))
    assert window.theme().name == "light"
    payload = json.loads((tmp_path / "ui" / "theme.json").read_text(encoding="utf-8"))
    assert payload["theme"] == "light"


def test_close_persists_geometry(window: Any, tmp_path: Path) -> None:
    window.set_status("进行中")
    window.close()
    settings_file = tmp_path / "ui" / "locllmw.json"
    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["geometry"]
    assert "hardware" in payload


def test_sidebar_collapse_updates_menu_and_settings(window: Any, tmp_path: Path) -> None:
    assert not window.nav.is_collapsed()
    window.nav.set_collapsed(True)
    assert window.sidebar_action.isChecked()
    payload = json.loads((tmp_path / "ui" / "locllmw.json").read_text(encoding="utf-8"))
    assert payload["sidebar_collapsed"] is True
    window.sidebar_action.trigger()  # 可勾选动作：再触发一次即展开
    assert not window.nav.is_collapsed()


def test_sidebar_collapsed_state_restored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    monkeypatch.setattr(qt_state.LocllmState, "refresh_models", lambda self, port=None: None)
    monkeypatch.setattr(qt_state.LocllmState, "detect_hardware", lambda self, on_done=None: None)
    settings_path = tmp_path / "ui" / "locllmw.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"sidebar_collapsed": True}), encoding="utf-8")
    create_application([])
    win = LocllmWindow(settings_path)
    win.monitor_page.stop()
    try:
        assert win.nav.is_collapsed()
        assert win.sidebar_action.isChecked()
    finally:
        win.close()


def test_pages_use_scroll_container(window: Any) -> None:
    """长页面必须放进滚动容器：窗口变矮时滚动而不是压扁控件（模型列表自身滚动）。"""
    from fcmd.gui.qt.widgets import PageScroll

    for key in ("overview", "run", "monitor", "settings", "lab"):
        window.show_page(key)
        page = window.page(key)
        assert page.findChild(PageScroll) is not None, key


def test_run_page_has_no_bottom_action_bar(window: Any) -> None:
    """底部不再保留启停按钮，动作集中到顶部模型卡片。"""
    from PySide6.QtWidgets import QApplication

    window.show()
    window.show_page("run")
    window.resize(1040, 780)
    window.run_page.advanced.set_expanded(True)
    QApplication.processEvents()
    run = window.run_page
    assert not hasattr(run, "stop_selected_button")
    assert not hasattr(run, "stop_all_button")
    assert not hasattr(run, "start_button")
    assert not hasattr(run, "status_button")
    assert not hasattr(run, "browser_button")
    assert not hasattr(run, "logs_button")


def test_chat_input_buttons_share_one_row(window: Any) -> None:
    """对话页输入区按钮横排，新对话入口移入左侧历史栏。"""
    window.show_page("chat")
    chat = window.page("chat")
    buttons = [chat.send_button, chat.stop_button, chat.export_button]
    tops = {widget.mapTo(chat, widget.rect().topLeft()).y() for widget in buttons}
    assert len(tops) == 1
    assert chat.new_chat_button.text() == "＋ 新对话"


def test_chat_history_lists_conversations_and_switches(window: Any) -> None:
    window.show_page("chat")
    chat = window.page("chat")
    chat._append_text("你：第一段", "user")
    chat.new_conversation()
    assert chat.history_list.count() == 2
    chat.history_list.setCurrentRow(0)
    assert "第一段" in chat.transcript.toPlainText()


def test_scan_result_auto_selects_a_startable_model(window: Any, tmp_path: Path) -> None:
    """列表里有模型但未选择时，自动带入可启动的模型（避免启动报「未找到量化」）。"""
    from fcmd.apps.locllm.llmbase import ModelEntry

    entries = [
        ModelEntry(root=tmp_path, path=tmp_path / "a-Q4_K_XL.gguf", size=10),
        ModelEntry(root=tmp_path, path=tmp_path / "b-Q5_K_M.gguf", size=20),
    ]
    window.state.models = entries
    window.state.models_root = tmp_path
    window.state.selected_model = None
    window.run_page.on_models_changed()
    assert window.state.selected_model == tmp_path / "b-Q5_K_M.gguf"
    assert window.run_page.service_name() == "b"


def test_window_and_sidebar_use_packaged_icon(window: Any) -> None:
    """窗口图标来自 assets/icons：展开时在标题左侧，折叠后居中显示并可点击展开。"""
    assert window.nav.title_label.text() == "LocLLM"
    assert window.windowTitle().startswith("LocLLM")
    assert not window.windowIcon().isNull()
    assert window.windowIcon().availableSizes()
    icon_label = window.nav.icon_label
    assert icon_label is not None
    window.nav.set_collapsed(True)
    assert not icon_label.isHidden()
    assert window.nav.toggle_button.isHidden()
    window.nav.set_collapsed(False)
    assert not icon_label.isHidden()
    assert not window.nav.toggle_button.isHidden()


def test_overview_actions_are_text_links(window: Any) -> None:
    """「重新检测」是文字链接（无按钮边框），位于硬件信息标题旁。"""
    overview = window.overview
    assert overview._detect_button.text() == "重新检测"
    assert overview._detect_button.objectName() == "Link"
    assert "model_disk" not in overview._hardware_values
    assert "model_free" not in overview._hardware_values


def test_overview_gauges_paint_event_runs(window: Any) -> None:
    """环形仪表在不同余量档位下重绘不抛异常（回归：QPalette 必须从 QtGui 导入）。"""
    from PySide6.QtGui import QPaintEvent

    gauge = window.overview._gpu_gauge
    for percent in (42.0, 72.0, 92.0):
        gauge.set_usage(percent, "1.0 / 24.0 GB")
        gauge.paintEvent(QPaintEvent(gauge.rect()))
        assert gauge._percent == percent


def test_recommendation_list_fills_width_without_clipping(window: Any) -> None:
    """推荐列表宽度跟随视口（长模型名不撑宽），高度随卡片扩展且不出现内层滚动。"""
    from PySide6.QtWidgets import QApplication

    from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot
    from fcmd.apps.locllm.llmbase import ModelEntry

    window.resize(1000, 800)
    window.state.hardware = HardwareSnapshot(
        gpu="RTX 4090",
        gpu_mib=24576,
        cpu="Intel i9",
        cpu_cores=24,
        cpu_threads=32,
        memory_total=64 * 1024**3,
    )
    root = Path(window.state.settings_path or "")
    models = []
    for index in range(3):
        path = root / f"Qwen3-Coder-30B-A3B-Instruct-very-long-model-name-{index}-Q5_K_M.gguf"
        models.append(ModelEntry(root=root, path=path, size=5 * 1024**3, tags=("代码",)))
    window.state.models = models
    window.overview.refresh_recommendations()
    window.show()

    QApplication.processEvents()
    recommend_list = window.overview._recommend_list
    assert recommend_list.count() == 3
    assert recommend_list.horizontalScrollBar().maximum() == 0
    assert recommend_list.verticalScrollBar().maximum() == 0
    for index in range(recommend_list.count()):
        item = recommend_list.item(index)
        widget = recommend_list.itemWidget(item)
        assert widget is not None
        assert widget.width() <= recommend_list.viewport().width()


def test_recommendation_rows_follow_width_when_resized_repeatedly(window: Any) -> None:
    """反复拖宽 / 拖窄窗口后，每行仍贴合视口（回归：右侧内容被旧宽度挤出视口）。"""
    from PySide6.QtWidgets import QApplication

    from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot
    from fcmd.apps.locllm.llmbase import ModelEntry

    window.state.hardware = HardwareSnapshot(
        gpu="RTX 4090",
        gpu_mib=24576,
        cpu="Intel i9",
        cpu_cores=24,
        cpu_threads=32,
        memory_total=64 * 1024**3,
    )
    root = Path(window.state.settings_path or "")
    window.state.models = [
        ModelEntry(
            root=root,
            path=root / f"Qwen3-Coder-30B-A3B-Instruct-very-long-model-name-{index}-Q5_K_M.gguf",
            size=5 * 1024**3,
            tags=("代码",),
        )
        for index in range(3)
    ]
    window.overview.refresh_recommendations()
    window.show()
    QApplication.processEvents()

    recommend_list = window.overview._recommend_list
    model = recommend_list.model()
    for width in (1500, 1000, 1600, 1200, 1400, 1000):
        window.resize(width, 800)
        QApplication.processEvents()
        for index in range(recommend_list.count()):
            widget = recommend_list.itemWidget(recommend_list.item(index))
            assert widget is not None
            rect = recommend_list.visualRect(model.index(index, 0))
            assert widget.width() == rect.width()
            assert widget.width() <= recommend_list.viewport().width()
