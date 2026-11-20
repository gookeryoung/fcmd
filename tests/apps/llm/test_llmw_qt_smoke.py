"""llmw Qt 窗口的离屏冒烟测试：七页可构建、主题可切换、设置可落盘。

用 ``QT_QPA_PLATFORM=offscreen`` 在无显示器环境（CI / 本地）跑通整窗构建，
模型扫描与硬件检测替换成空实现，避免测试触盘 / 调 ``nvidia-smi``。
"""

from __future__ import annotations

import itertools
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from fcmd.apps.llm import _llmw_qt_state as qt_state
from fcmd.apps.llm._llmw_qt_window import LlmWindow, create_application
from fcmd.apps.llm.llmbase import EngineInfo
from fcmd.gui import theme as _guitheme


@pytest.fixture()
def window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """构建离线窗口：扫描 / 检测 / 采样全部打桩，关闭时自动清理。

    引擎解析打桩成假引擎：CI / 无引擎机器上 ``preview`` 走正常路径而不是
    抛 ServeError 弹出模态错误框（模态框无人点击会让测试永久挂死）。
    模态弹窗整体打桩兜底：任何错误分支都不允许在测试里阻塞主线程。
    """
    from fcmd.apps.llm import _llmw_qt_center as center
    from fcmd.apps.llm.llmdown import ModelSearch

    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    monkeypatch.setattr(qt_state.LlmState, "refresh_models", lambda self, port=None: None)
    monkeypatch.setattr(qt_state.LlmState, "detect_hardware", lambda self, on_done=None: None)
    # 模型页构造时会拉 ModelScope 列表；测试不能留真实网络线程（PySide2 退出阶段会等它）。
    monkeypatch.setattr(center, "search_models", lambda *_args, **_kw: ModelSearch((), 0, 1, 30))
    monkeypatch.setattr(
        qt_state.llmserve.llmengine,
        "resolve_engine",
        lambda tag=None, home=None: EngineInfo("test-engine", Path("/nonexistent/llama-server"), "test", ()),
    )
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args, **_kw: shown.append("critical") or None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kw: QMessageBox.StandardButton.No)
    create_application([])
    win = LlmWindow(tmp_path / "ui" / "llmw.json")
    win.overview.stop()
    yield win
    win.close()


def test_window_allows_shrinking_height(window: Any) -> None:
    """窗口高度下限放宽到 600，用户可以继续缩小而不是卡在 780。"""
    assert window.minimumHeight() == 600
    window.resize(1040, 600)
    QApplication.processEvents()
    assert window.height() == 600


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
    assert keys == ("overview", "models", "run", "chat", "lab", "settings")
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
    from fcmd.apps.llm import _llmw_list_cache as cache_mod
    from fcmd.apps.llm import _llmw_qt_center as center
    from fcmd.apps.llm.llmdown import ModelSearch, ModelSummary

    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(cache_mod, "default_cache_path", lambda: cache_path)
    key = cache_mod.cache_key("", 1, 30, "downloads")
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


def test_models_category_chips_switch_filter(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """分类 chip 点击后切换到对应筛选（PySide2 下 ``clicked`` 重载要求槽函数可零参调用）。"""
    from fcmd.apps.llm import _llmw_models
    from fcmd.apps.llm import _llmw_qt_center as center

    window.show_page("models")
    browse = window.page("models").browse
    submitted: list[object] = []
    monkeypatch.setattr(center, "submit", lambda fn, *args, **kwargs: submitted.append(fn))
    for key, _label, query in _llmw_models.MODEL_FILTERS:
        browse.filter_buttons[key].click()
        QApplication.processEvents()
        assert browse._filter_key == key
        assert browse._filter_query == query
        assert browse.filter_buttons[key].isChecked()
    assert submitted and submitted[0] == browse._search_work


def test_chat_history_supports_single_and_all_clear(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """历史对话支持删除当前与清空全部；运行中禁止清理。"""
    from fcmd.apps.llm._llmw_qt_chat import _Conversation

    window.show_page("chat")
    chat = window.page("chat")
    chat._conversations = [_Conversation(title="一"), _Conversation(title="二"), _Conversation(title="三")]
    chat._active = 1
    chat._refresh_history()
    chat.delete_chat_button.click()
    assert [item.title for item in chat._conversations] == ["一", "三"]
    assert chat._active == 1
    assert chat.status.text() == "已删除当前对话"
    chat.delete_chat_button.click()
    assert [item.title for item in chat._conversations] == ["一"]
    chat.delete_chat_button.click()
    assert len(chat._conversations) == 1
    assert chat._conversations[0].title == "新对话"
    assert chat.transcript.toPlainText() == ""

    chat._conversations = [_Conversation(title="甲"), _Conversation(title="乙")]
    chat._active = 0
    chat._refresh_history()
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kw: QMessageBox.StandardButton.No)
    chat.clear_chats_button.click()
    assert len(chat._conversations) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kw: QMessageBox.StandardButton.Yes)
    chat.clear_chats_button.click()
    assert len(chat._conversations) == 1
    assert chat._conversations[0].title == "新对话"
    assert chat.status.text() == "已清空全部对话"

    chat._conversations = [_Conversation(title="一"), _Conversation(title="二")]
    chat._active = 0
    chat._refresh_history()
    chat._running = True
    try:
        chat.delete_chat_button.click()
        chat.clear_chats_button.click()
        assert len(chat._conversations) == 2
        assert "停止后才能清理" in chat.status.text()
    finally:
        chat._running = False


def test_models_folder_import_uses_detected_type(window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """选择文件夹后先识别类型，再按类型提供的选项决定是否导入并转换。"""
    from PySide6.QtWidgets import QFileDialog

    from fcmd.apps.llm._llmw_import import FolderKind

    window.show_page("models")
    browse = window.page("models").browse
    folder = tmp_path / "hf-model"
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(b"x")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kw: str(folder))
    seen: list[FolderKind] = []

    def _ask(info: object) -> tuple[bool, bool]:
        seen.append(info.kind)  # type: ignore[attr-defined]
        return True, True

    monkeypatch.setattr(browse, "_ask_folder_import", _ask)
    calls: list[tuple[list[Path], bool]] = []
    monkeypatch.setattr(browse, "start_import", lambda paths, *, convert=False: calls.append((paths, convert)))
    browse._pick_model_folder()
    assert seen == [FolderKind.TENSORS]
    assert calls == [([folder], True)]


def test_models_import_button_submits_copy(window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """更新模型右侧提供导入模型按钮，GGUF 导入走后台复制并刷新本地标识。"""
    from fcmd.apps.llm import _llmw_qt_center as center

    window.show_page("models")
    browse = window.page("models").browse
    assert browse.update_button.text() == "更新模型"
    assert browse.import_button.text() == "导入模型"
    source = tmp_path / "import-src" / "demo-Q4_K_M.gguf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"GGUF")
    calls: list[object] = []
    monkeypatch.setattr(center, "submit", lambda fn, *args, **kwargs: calls.append(fn))
    browse.start_import([source])
    assert calls == [browse._import_work]
    assert "import" in window.state._loading_tasks


def test_models_manual_update_forces_refresh_and_shows_time(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """顶部更新按钮强制刷新并重扫本地；成功后状态栏常驻更新时间。"""
    window.show_page("models")
    browse = window.page("models").browse
    assert browse.update_button.text() == "更新模型"
    calls: list[object] = []
    monkeypatch.setattr(browse, "refresh_installed", lambda: calls.append("scan"))
    monkeypatch.setattr(browse, "start_search", lambda page, *, force=False: calls.append((page, force)))
    browse._searching = False
    browse._page = 3
    browse.update_button.click()
    assert calls == ["scan", (3, True)]
    window.state.mark_models_updated()
    assert not window.status_note.isHidden()
    assert window.status_note.text().startswith("更新于 ")
    assert window.state.settings.models_updated_at


def test_models_page_view_options_restored_and_persisted(window: Any) -> None:
    """来源 / 排序 / 同名折叠等列表选项从设置恢复，修改后写回设置。"""
    window.state.settings.model_view = {
        "filter": "all",
        "sort": "name",
        "source": "installed",
        "owner": "",
        "group_same": True,
    }
    window.show_page("models")
    browse = window.page("models").browse
    assert browse.filter_buttons["all"].isChecked()
    assert browse.sort_combo.currentData() == "name"
    assert browse.source_combo.currentData() == "installed"
    assert browse.group_check.isChecked()

    browse.group_check.setChecked(False)
    assert window.state.settings.model_view["group_same"] is False
    browse.source_combo.setCurrentIndex(browse.source_combo.findData("online"))
    assert window.state.settings.model_view["source"] == "online"


def test_models_page_delete_and_cleanup(window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """模型页支持删除本地模型与清理 .part 残留。"""
    from fcmd.apps.llm import _llmconfig

    models = tmp_path / "models"
    target = models / "Qwen" / "demo"
    target.mkdir(parents=True)
    (target / "model.gguf").write_bytes(b"x")
    part = models / "Qwen" / "other" / "model.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"partial")
    monkeypatch.setattr(_llmconfig, "models_dir", lambda: models)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)

    window.show_page("models")
    page = window.page("models").browse
    page.delete_model("Qwen/demo")
    assert not target.exists()

    from fcmd.apps.llm._llmw_qt_model_manager import ModelManagerDialog
    from fcmd.apps.llm.llmdown import scan_local_models

    dialog = ModelManagerDialog(models)
    dialog._render(scan_local_models(models))
    assert dialog._table.rowCount() == 1
    assert "未完成" in dialog._table.item(0, 3).text()
    dialog._cleanup_partial()
    assert not part.exists()

    first = models / "Qwen" / "first"
    second = models / "Qwen" / "second"
    for directory in (first, second):
        directory.mkdir(parents=True)
        (directory / "model.gguf").write_bytes(b"x")
    dialog._render(scan_local_models(models))
    dialog._table.selectAll()
    dialog._remove_selected()
    assert not first.exists()
    assert not second.exists()


def test_settings_engine_directory_and_archive_actions(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """设置页支持导入 llama.cpp 目录（复制）与导入本地 zip。"""
    from PySide6.QtWidgets import QFileDialog, QInputDialog

    from fcmd.apps.llm import _llmw_settings as settings_logic
    from fcmd.apps.llm.llmbase import EngineInfo

    window.show_page("settings")
    page = window.page("settings")
    fake = EngineInfo("custom", tmp_path / "llama-server.exe", str(tmp_path))
    calls: list[tuple[str, str]] = []
    copies: list[bool] = []
    monkeypatch.setattr(page, "_after_engine_install", lambda info, action: calls.append((info.tag, action)))
    monkeypatch.setattr(
        settings_logic,
        "install_offline",
        lambda tag, source, **kwargs: copies.append(bool(kwargs.get("copy_directory"))) or (fake, tag),
    )
    monkeypatch.setattr(settings_logic, "install_archives", lambda tag, archives: fake)
    monkeypatch.setattr(settings_logic, "inspect_offline", lambda source: [])
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("custom", True))

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(tmp_path))
    page._import_engine_dir()
    assert copies == [True]
    assert calls[-1] == ("custom", "已导入")

    archive = tmp_path / "llama-b1-bin-win-cpu-x64.zip"
    archive.write_bytes(b"zip")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_args, **_kwargs: ([str(archive)], ""))
    page._import_engine_archive()
    assert calls[-1] == ("custom", "已导入")


def test_settings_engine_panel_is_compact(window: Any) -> None:
    """引擎独立成卡片且位于存储下方：导入按钮在目录行、刷新在右上角。"""
    from fcmd.gui.qt.widgets import Card

    def card_of(widget: Any) -> Card:
        card = widget.parent()
        while card is not None and not isinstance(card, Card):
            card = card.parent()
        assert card is not None
        return card

    window.show_page("settings")
    page = window.page("settings")
    storage = card_of(page.models_path)
    engine = card_of(page.engines_path)
    assert storage.title_label.text() == "存储"
    assert engine.title_label.text() == "引擎"
    assert engine is not storage
    assert not storage.isAncestorOf(page.engines_path)
    assert [card.title_label.text() for card in page.findChildren(Card)] == [
        "存储",
        "引擎",
        "下载",
        "模型转换",
    ]
    assert page.refresh_engine_button.text() == "刷新"
    assert page.refresh_engine_button.parent() is engine
    assert page.models_path.browse.text() == "更改"
    assert page.engines_path.browse.text() == "更改"
    assert page.download_engine_button.text() == "下载引擎"
    assert page.import_engine_dir_button.text() == "导入目录"
    assert page.import_engine_archive_button.text() == "导入压缩包"
    assert not hasattr(page, "add_engine_button")
    assert not hasattr(page, "engines_open")
    assert not hasattr(page, "engine_status_tag")
    assert not hasattr(page, "check_engine_button")
    assert page.engines_list.objectName() == "EngineList"
    assert not hasattr(page, "engines_table")
    assert not hasattr(page, "use_engine_button")
    assert not hasattr(page, "switch_engine_dir_button")
    assert not hasattr(page, "import_engine_zip_button")
    assert not hasattr(page, "engine_status_label")


def test_settings_engine_empty_and_error_states(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """无引擎时列表收起、提示行承接；读取失败时提示行直接显示错误。"""
    from fcmd.apps.llm import _llmw_settings as settings_logic

    monkeypatch.setattr(settings_logic, "installed_engines", lambda: [])
    window.show_page("settings")
    page = window.page("settings")
    page.refresh_engines()
    assert not page.engines_list.isVisibleTo(page)
    assert page.engines_empty.isVisibleTo(page)

    def broken_engines() -> object:
        raise OSError("boom")

    monkeypatch.setattr(settings_logic, "installed_engines", broken_engines)
    page.refresh_engines()
    assert "boom" in page.engines_empty.text()
    assert not page.engines_list.isVisibleTo(page)


def test_settings_engine_rows_are_compact_single_line(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """已安装引擎为紧凑单行：标签 + 状态 + 路径 + 行内「设为默认」。"""
    from fcmd.apps.llm import _llmw_settings as settings_logic
    from fcmd.apps.llm.llmbase import EngineInfo

    engines = [
        EngineInfo("b2", tmp_path / "b2" / "llama-server.exe", str(tmp_path / "source")),
        EngineInfo("b1", tmp_path / "b1" / "llama-server.exe", "current"),
    ]
    monkeypatch.setattr(settings_logic, "installed_engines", lambda: engines)
    window.show_page("settings")
    page = window.page("settings")
    page.refresh_engines()
    assert page.engines_list.count() == 2
    other_row = page.engines_list.itemWidget(page.engines_list.item(0))
    current_row = page.engines_list.itemWidget(page.engines_list.item(1))
    assert other_row.objectName() == "EngineRow"
    assert other_row.use_button is not None
    assert other_row.use_button.text() == "设为默认"
    assert other_row.use_button.isEnabled()
    other_widgets = [other_row.layout().itemAt(pos).widget() for pos in range(other_row.layout().count())]
    assert other_widgets[-2] is other_row.use_button
    assert other_widgets[-1].text() == "CPU"
    assert current_row.use_button is None
    current_labels = [label.objectName() for label in current_row.findChildren(QLabel)]
    assert "EngineItemName" in current_labels
    assert "StatusTag" in current_labels
    assert "OwnerTag" not in current_labels
    current_widgets = [current_row.layout().itemAt(pos).widget() for pos in range(current_row.layout().count())]
    assert current_widgets[-2].text() == "默认"
    assert current_widgets[-1].text() == "CPU"
    assert "构建类别：CPU" in current_widgets[-1].toolTip()
    assert "兼容性最好" in current_widgets[-1].toolTip()

    QApplication.processEvents()
    for index in range(page.engines_list.count()):
        row = page.engines_list.itemWidget(page.engines_list.item(index))
        widgets = [row.layout().itemAt(pos).widget() for pos in range(row.layout().count())]
        for left, right in itertools.pairwise(widgets):
            assert left.geometry().right() < right.geometry().left(), "引擎行内文字控件重叠"

    selected: list[str] = []
    monkeypatch.setattr(settings_logic, "select_engine", lambda tag: selected.append(tag) or engines[0])
    other_row.use_button.click()
    assert selected == ["b2"]


def test_settings_has_no_source_hint(window: Any) -> None:
    """设置页不再显示「来源：配置 / 环境变量」说明。"""
    window.show_page("settings")
    page = window.page("settings")
    assert not hasattr(page, "models_source")
    assert not hasattr(page, "engines_source")


def test_models_page_source_owner_and_group_controls(window: Any) -> None:
    """模型页提供来源 / 发布方过滤与同名折叠，默认只显示在线结果。"""
    window.show_page("models")
    browse = window.page("models").browse
    assert browse.source_combo.currentData() == "online"
    assert browse.owner_combo.itemText(0) == "全部发布方"
    assert browse.group_check.text() == "同名折叠"
    assert browse.cleanup_button.text() == "模型管理"


def test_models_page_name_filter_is_debounced(window: Any) -> None:
    """上一页左侧的名称过滤框只在停止输入后重建列表。"""
    window.show_page("models")
    browse = window.page("models").browse
    assert browse.name_filter.placeholderText().startswith("按名称筛选")
    assert browse.name_filter.isClearButtonEnabled()
    assert browse.name_filter.minimumWidth() >= 220
    assert browse.sort_combo.currentData() == "downloads"
    assert [browse.sort_combo.itemData(i) for i in range(browse.sort_combo.count())][:2] == [
        "downloads",
        "created",
    ]
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
    assert chat.new_chat_button.objectName() == "RunPrimary"
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
    from fcmd.apps.llm.llmbase import ModelEntry

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
    from fcmd.apps.llm.llmbase import ModelEntry

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
    from fcmd.apps.llm.llmbase import ModelEntry

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
    settings_file = tmp_path / "ui" / "llmw.json"
    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["geometry"]
    assert "hardware" in payload


def test_sidebar_collapse_updates_menu_and_settings(window: Any, tmp_path: Path) -> None:
    assert not window.nav.is_collapsed()
    window.nav.set_collapsed(True)
    assert window.sidebar_action.isChecked()
    payload = json.loads((tmp_path / "ui" / "llmw.json").read_text(encoding="utf-8"))
    assert payload["sidebar_collapsed"] is True
    window.sidebar_action.trigger()  # 可勾选动作：再触发一次即展开
    assert not window.nav.is_collapsed()


def test_sidebar_collapsed_state_restored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    monkeypatch.setattr(qt_state.LlmState, "refresh_models", lambda self, port=None: None)
    monkeypatch.setattr(qt_state.LlmState, "detect_hardware", lambda self, on_done=None: None)
    settings_path = tmp_path / "ui" / "llmw.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"sidebar_collapsed": True}), encoding="utf-8")
    create_application([])
    win = LlmWindow(settings_path)
    win.overview.stop()
    try:
        assert win.nav.is_collapsed()
        assert win.sidebar_action.isChecked()
    finally:
        win.close()


def test_service_log_moves_to_help_menu(window: Any) -> None:
    """服务日志入口在「帮助 → 服务日志…」，日志窗口回放缓冲并实时追加。"""
    texts = [action.text() for action in window.help_menu.actions() if not action.isSeparator()]
    assert texts[-3:] == ["性能测试…", "服务日志…", "关于"]

    window.state.log("启动前日志")
    window.service_log_action.trigger()
    QApplication.processEvents()
    dialog = window._log_dialog
    assert dialog is not None
    assert dialog.isVisible()
    assert "启动前日志" in dialog.log_view.text()

    window.state.log("运行中日志")
    QApplication.processEvents()
    assert "运行中日志" in dialog.log_view.text()

    dialog.log_view.clear_button.click()
    assert dialog.log_view.text() == ""
    assert window.state.log_text() == ""
    dialog.close()


def test_overview_load_blocks_and_rate_display(window: Any) -> None:
    """概览硬件卡：GPU / CPU / 内存格图 + 关键信息 + 实时 tok/s。"""
    from fcmd.apps.llm import llmserve
    from fcmd.apps.llm._llmw_monitor import GpuSample, LoadSample

    overview = window.overview
    assert not hasattr(overview, "_gpu_gauge")
    assert overview._recommend_list.parent().title() == "智能推荐"
    # 负载块不再支持展开：标题后直接用小字提示关键信息。
    for name in ("_gpu_toggle", "_cpu_toggle", "_mem_toggle", "_gpu_metrics", "_cpu_metrics", "_mem_metrics"):
        assert not hasattr(overview, name)
    assert overview._gpu_hint.fullText() == "等待采样…"
    assert overview._cpu_hint.fullText() == "等待采样…"
    assert overview._mem_hint.fullText() == "等待采样…"
    sample = LoadSample(
        cpu=41.0,
        cpu_freq=3600.0,
        memory_used=13 * 1024**3,
        memory_total=32 * 1024**3,
        memory_percent=41.0,
        swap_used=2 * 1024**3,
        swap_total=8 * 1024**3,
        process_cpu=12.5,
        process_rss=5 * 1024**3,
        process_threads=12,
        process_uptime=65.0,
        gpu=GpuSample(
            name="NVIDIA GeForce RTX 4090",
            index="0",
            utilization=73.0,
            memory_used=18000.0,
            memory_total=24576.0,
            temperature=62.0,
            power_draw=210.0,
            power_limit=450.0,
            fan=45.0,
            clock_mhz=2520.0,
            process_memory=15000.0,
        ),
    )
    overview._apply_sample((sample, None, None))

    assert overview._gpu_percent.text() == "73 %"
    assert overview._cpu_percent.text() == "41 %"
    assert overview._mem_percent.text() == "41 %"
    assert overview._gpu_bar.percent() == 73.0
    assert overview._cpu_bar.percent() == 41.0
    assert overview._mem_bar.percent() == 41.0
    assert overview._gpu_percent.property("band") == "warning"
    assert overview._cpu_percent.property("band") == "success"
    assert "显存 73%" in overview._gpu_hint.fullText()
    assert "210 / 450 W" in overview._gpu_hint.fullText()
    assert "频率 3600 MHz" in overview._cpu_hint.fullText()
    assert "服务 12.5 %" in overview._cpu_hint.fullText()
    assert overview._mem_hint.fullText().startswith("13.00 GB / 32.00 GB")
    assert "交换 25%" in overview._mem_hint.fullText()

    # 服务指标：生成 tok/s 单独大字展示，其余进次要网格。
    metrics = llmserve.ServeMetrics(prompt_tokens=9302, predicted_tokens=12045, processing=1, deferred=0, kv_usage=0.18)
    overview._apply_sample((sample, metrics, None))
    assert overview._gen_rate_label.objectName() == "RateValue"
    assert overview._gen_rate_label.text() == "0.0"
    assert overview._performance_metrics.value("prompt_rate") == "0.0 tok/s"
    assert overview._performance_metrics.value("processing") == "1"
    assert overview._performance_metrics.value("kv") == "18.0 %"
    assert overview._performance_badge.text() == "监控中"
    assert overview._performance_badge.tone() == "success"


def test_overview_load_blocks_fit_min_width(window: Any) -> None:
    """窗口宽度最小时三块负载仍在视口内，不出现横向溢出。"""
    from PySide6.QtWidgets import QFrame

    window.show()
    window.resize(1040, 700)
    window.show_page("overview")
    QApplication.processEvents()
    page = window.overview
    blocks = [widget for widget in page.findChildren(QFrame) if widget.objectName() == "LoadBlock"]
    assert len(blocks) == 3
    for block in blocks:
        assert block.isVisible()
        right = block.mapTo(page, block.rect().topRight()).x()
        assert right <= page.width()


def test_bench_moves_to_help_menu(window: Any) -> None:
    """性能测试从帮助菜单打开独立窗口：目标 / 场景 / 参数 / 结果自适应布局。"""
    from fcmd.apps.llm.llmbase import ServeState

    window.show()
    window.resize(1180, 820)
    QApplication.processEvents()

    window.bench_action.trigger()
    QApplication.processEvents()
    dialog = window._bench_dialog
    assert dialog is not None
    assert dialog.isVisible()
    assert dialog.windowTitle() == "性能测试"
    assert dialog._bench_table.minimumHeight() >= 220
    assert "未检测到运行中的服务" in dialog._target_hint.text()

    def top(widget: Any) -> int:
        return widget.mapTo(dialog, widget.rect().topLeft()).y()

    # 场景表与参数栏左右分栏：参数控件落在场景表的纵向范围内。
    table_top = top(dialog._scenario_table)
    assert table_top <= top(dialog._repeat_spin) < table_top + dialog._scenario_table.height()

    window.state.alive_states = [
        ServeState(
            name="demo",
            pid=1234,
            create_time=0.0,
            host="127.0.0.1",
            port=8080,
            model="demo.gguf",
            profile="balanced",
            context=4096,
            engine="",
            alias="demo",
            api_key="",
            log="",
            started_at="",
        )
    ]
    window.state.servicesChanged.emit(window.state.alive_states)
    assert dialog._target_combo.count() == 2
    assert dialog._target_combo.itemText(1).startswith("demo")
    dialog.close()


def test_model_quant_options_falls_back_source(monkeypatch: pytest.MonkeyPatch, window: Any) -> None:
    """显式源读取清单失败时切到 auto 重试，避免直接报「读取清单失败」。"""
    from fcmd.apps.llm import _llmw_qt_center as center
    from fcmd.apps.llm.llmdown import ModelFile, ModelToolError, SourceSelection

    selection = SourceSelection("modelscope", (ModelFile("model-UD-Q4_K_XL.gguf", 1),))
    calls: list[str] = []

    def fake(repo: str, source: str, *_args: Any, **_kwargs: Any) -> SourceSelection:
        calls.append(source)
        if source == "hf-mirror":
            raise ModelToolError("清单读取失败: HTTP 429")
        return selection

    monkeypatch.setattr(center, "select_source", fake)
    options = center._BrowsePanel._quant_options_work("org/model", "hf-mirror")
    assert calls == ["hf-mirror", "auto"]
    assert options[0].label == "UD-Q4_K_XL"


def test_model_download_quant_chooser(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """下载前按量化选择：唯一选项直接下载，多选项弹列表。"""
    from fcmd.apps.llm.llmdown import ModelFile, QuantOption

    window.show_page("models")
    page = window.page("models").browse
    calls: list[tuple[str, tuple[ModelFile, ...] | None]] = []
    monkeypatch.setattr(page, "start_download", lambda repo, files=None: calls.append((repo, files)))
    ud = QuantOption(
        "UD-Q4_K_XL",
        "Q4_K",
        True,
        (ModelFile("model-UD-Q4_K_XL.gguf", 10),),
        10,
    )
    q5 = QuantOption("Q5_K_M", "Q5_K", False, (ModelFile("model-Q5_K_M.gguf", 20),), 20)
    page._choose_quant("org/demo", (ud,))
    assert calls[-1] == ("org/demo", ud.files)

    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args, **_kwargs: (q5.display, True))
    page._choose_quant("org/demo", (ud, q5))
    assert calls[-1] == ("org/demo", q5.files)

    page.download_status.setVisible(True)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args, **_kwargs: ("", False))
    page._choose_quant("org/demo", (ud, q5))
    assert page.download_status.isHidden()
    assert page.download_text.text() == ""


def test_overview_scrolls_only_recommendation_list(window: Any) -> None:
    """概览不再整页滚动：页面高度不足时只由推荐列表内部滚动。"""
    from fcmd.gui.qt.widgets import PageScroll

    window.show_page("overview")
    overview = window.overview
    assert overview.findChild(PageScroll) is None
    assert not overview._recommend_list.parentWidget().isAncestorOf(overview._apply_button)
    assert overview._apply_button.parentWidget() is overview


def test_pages_use_scroll_container(window: Any) -> None:
    """长页面必须放进滚动容器：窗口变矮时滚动而不是压扁控件（模型列表自身滚动）。"""
    from fcmd.gui.qt.widgets import PageScroll

    for key in ("run", "settings", "lab"):
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


def test_run_page_restart_button_only_enabled_while_running(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """重启按钮在「启动服务」右侧，只在服务运行且空闲时可用。"""
    from fcmd.apps.llm.llmbase import ModelEntry, ServeState

    model_path = tmp_path / "demo.gguf"
    model_path.write_bytes(b"\0")
    window.state.models = [ModelEntry(root=tmp_path, path=model_path, size=1)]
    window.state.selected_model = model_path
    window.state.alive_states = [
        ServeState(
            name="demo",
            pid=1234,
            create_time=0.0,
            host="127.0.0.1",
            port=8080,
            model=str(model_path),
            profile="balanced",
            context=4096,
            engine="",
            alias="demo",
            api_key="",
            log="",
            started_at="",
        )
    ]
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_run.llmserve.process_alive", lambda *_args, **_kw: True)
    page = window.run_page
    page._refresh_action_states()
    assert page.restart_button.text() == "重启服务"
    assert page.model_action_button.text() == "停止服务"
    assert page.restart_button.isEnabled()
    assert page.restart_button.objectName() == "RunAction"

    window.state.set_busy(True)
    assert not page.restart_button.isEnabled()
    window.state.set_busy(False)

    no_states: list[ServeState] = []
    window.state.alive_states = no_states
    page._refresh_action_states()
    assert not page.restart_button.isEnabled()


def test_run_page_restart_stops_then_starts(window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """重启先停旧服务，停止成功后再按当前参数启动。"""
    from fcmd.apps.llm import _llmw_qt_run as run_mod
    from fcmd.apps.llm.llmbase import ModelEntry, ServeState

    model_path = tmp_path / "demo.gguf"
    model_path.write_bytes(b"\0")
    window.state.models = [ModelEntry(root=tmp_path, path=model_path, size=1)]
    window.state.selected_model = model_path
    window.state.alive_states = [
        ServeState(
            name="demo",
            pid=1234,
            create_time=0.0,
            host="127.0.0.1",
            port=8080,
            model=str(model_path),
            profile="balanced",
            context=4096,
            engine="",
            alias="demo",
            api_key="",
            log="",
            started_at="",
        )
    ]
    monkeypatch.setattr(run_mod.llmserve, "process_alive", lambda *_args, **_kw: True)
    calls: list[tuple[Any, tuple[Any, ...]]] = []
    monkeypatch.setattr(run_mod, "submit", lambda fn, *args, **_kw: calls.append((fn, args)))
    page = window.run_page
    page.restart_selected()
    assert calls and calls[0][1] == (("demo",),)
    assert window.state.busy

    started: list[bool] = []
    monkeypatch.setattr(page, "start", lambda: started.append(True) or None)
    page._on_restart_stopped(([], []))
    assert started == [True]
    assert not window.state.busy


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
    from fcmd.apps.llm.llmbase import ModelEntry

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
    assert window.nav.title_label.text() == "LLM"
    assert window.windowTitle().startswith("LLM")
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
    """「重新检测」是文字链接（无按钮边框），位于系统信息标题行右侧。"""
    overview = window.overview
    assert overview._hardware_title.text() == "系统信息"
    assert overview._detect_button.text() == "重新检测"
    assert overview._detect_button.objectName() == "Link"
    assert "model_disk" not in overview._hardware_values
    assert "model_free" not in overview._hardware_values


def test_recommendation_list_fills_width_without_clipping(window: Any) -> None:
    """推荐列表宽度跟随视口（长模型名不撑宽），高度随卡片扩展且不出现内层滚动。"""
    from PySide6.QtWidgets import QApplication

    from fcmd.apps.llm._llmw_hardware import HardwareSnapshot
    from fcmd.apps.llm.llmbase import ModelEntry

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

    from fcmd.apps.llm._llmw_hardware import HardwareSnapshot
    from fcmd.apps.llm.llmbase import ModelEntry

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


def _engine_release_payload() -> list[dict[str, Any]]:
    """引擎下载对话框用的 GitHub releases 替身（x64 引擎包 + cudart）。"""

    def asset(name: str, size: int = 100) -> dict[str, Any]:
        return {"name": name, "browser_download_url": f"https://example.com/{name}", "size": size}

    return [
        {
            "tag_name": "b7000",
            "published_at": "2026-01-02T03:04:05Z",
            "assets": [
                asset("llama-b7000-bin-win-cuda-13.5-x64.zip", 200),
                asset("cudart-llama-bin-win-cuda-13.5-x64.zip", 50),
                asset("llama-b7000-bin-win-cpu-x64.zip"),
            ],
        }
    ]


def test_engine_download_dialog_lists_versions_and_installs(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """版本列表可读：选版本 / 变体后按 release 实际资产名下载安装。"""
    from fcmd.apps.llm import _llmw_qt_engine_install as install_ui
    from fcmd.apps.llm import _llmw_settings as settings_logic

    releases = install_ui.llmengine.parse_engine_releases(_engine_release_payload())
    monkeypatch.setattr(settings_logic, "engine_releases", lambda limit=10: releases)
    calls: list[tuple[str, str, tuple[str, ...] | None]] = []

    def fake_install(
        tag: str,
        variant: str = "cuda",
        home: Path | None = None,
        on_progress: Any = None,
        asset_names: tuple[str, ...] | None = None,
    ) -> EngineInfo:
        if on_progress is not None:
            on_progress("llama-b7000-bin-win-cuda-13.5-x64.zip", 100, 200)
        calls.append((tag, variant, asset_names))
        return EngineInfo(tag, Path("llama-server.exe"), "release")

    monkeypatch.setattr(settings_logic, "install_online", fake_install)
    dialog = install_ui.EngineDownloadDialog(window)
    try:
        deadline = time.monotonic() + 5
        while dialog.version_combo.count() == 0 and time.monotonic() < deadline:
            QApplication.processEvents()
        assert dialog.version_combo.currentData() == "b7000"
        variants = [dialog.variant_combo.itemData(index) for index in range(dialog.variant_combo.count())]
        assert variants == ["cuda", "cpu"]
        assert "llama-b7000-bin-win-cuda-13.5-x64.zip" in dialog.info.text()
        assert "NVIDIA" in dialog.variant_hint.text()
        assert "NVIDIA" in str(dialog.variant_combo.itemData(0, Qt.ItemDataRole.ToolTipRole))

        dialog._download()
        deadline = time.monotonic() + 5
        while "已安装" not in dialog.status.text() and time.monotonic() < deadline:
            QApplication.processEvents()
        assert calls == [
            (
                "b7000",
                "cuda",
                ("llama-b7000-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip"),
            )
        ]
        assert dialog.progress.value() == 100
        assert "已安装" in dialog.status.text()
    finally:
        dialog.close()


def test_engine_download_dialog_reports_list_failure(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """拉取版本失败时给出提示，下载按钮保持不可用。"""
    from fcmd.apps.llm import _llmw_qt_engine_install as install_ui
    from fcmd.apps.llm import _llmw_settings as settings_logic
    from fcmd.apps.llm.llmbase import ServeError

    def boom(limit: int = 10) -> Any:
        raise ServeError("offline")

    monkeypatch.setattr(settings_logic, "engine_releases", boom)
    dialog = install_ui.EngineDownloadDialog(window)
    try:
        deadline = time.monotonic() + 5
        while "offline" not in dialog.status.text() and time.monotonic() < deadline:
            QApplication.processEvents()
        assert "获取版本列表失败" in dialog.status.text()
        assert not dialog.download_button.isEnabled()
    finally:
        dialog.close()


def test_engine_download_dialog_can_close_while_fetching(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """拉取版本列表卡住时也能立即关窗（不等待后台线程）。"""
    import threading

    from fcmd.apps.llm import _llmw_qt_engine_install as install_ui
    from fcmd.apps.llm import _llmw_settings as settings_logic

    gate = threading.Event()
    monkeypatch.setattr(settings_logic, "engine_releases", lambda limit=10: gate.wait(5) and ())
    dialog = install_ui.EngineDownloadDialog(window)
    try:
        dialog.show()
        QApplication.processEvents()
        dialog.close()
        assert dialog._closed
    finally:
        gate.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
            if dialog._fetching is False:
                break
