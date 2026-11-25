"""llmw Qt 窗口的离屏冒烟测试：七页可构建、主题可切换、设置可落盘。

用 ``QT_QPA_PLATFORM=offscreen`` 在无显示器环境（CI / 本地）跑通整窗构建，
模型扫描与硬件检测替换成空实现，避免测试触盘 / 调 ``nvidia-smi``。
"""

from __future__ import annotations

import itertools
import json
import os
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from fcmd.apps.llm import _llmw_qt_state as qt_state
from fcmd.apps.llm._llmw_qt_window import LlmWindow, create_application
from fcmd.apps.llm.llmbase import EngineInfo
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from tests.helpers import destroy_qt_top_levels, qt_top_levels


@pytest.fixture()
def window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """构建离线窗口：扫描 / 检测 / 采样全部打桩，关闭时自动清理。

    引擎解析打桩成假引擎：CI / 无引擎机器上 ``preview`` 走正常路径而不是
    抛 ServeError 弹出模态错误框（模态框无人点击会让测试永久挂死）。
    模态弹窗整体打桩兜底：任何错误分支都不允许在测试里阻塞主线程。
    """
    from fcmd.apps.llm import _llmpaint_server, _llmvllm
    from fcmd.apps.llm import _llmw_qt_center as center
    from fcmd.apps.llm.llmdown import ModelSearch

    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    # vLLM 平台探测会启动 WSL 发行版；测试里固定为不支持，保证离屏冒烟不触外部环境。
    monkeypatch.setattr(
        _llmvllm,
        "detect_platform",
        lambda *_args, **_kwargs: _llmvllm.VllmPlatform("none", False, "测试环境不支持 vLLM"),
    )
    # Windows 默认模型目录是 D:/models（开发者本机路径），测试必须隔离，
    # 否则画图页会扫到真实模型，断言随本机环境漂移。
    monkeypatch.setenv("FCMD_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(qt_state.LlmState, "refresh_models", lambda self, port=None: None)
    monkeypatch.setattr(qt_state.LlmState, "detect_hardware", lambda self, on_done=None: None)
    # 画图状态探针默认打桩：冒烟测试不连真实端口（本机可能有服务在跑）。
    monkeypatch.setattr(
        _llmpaint_server,
        "probe_paint",
        lambda *_args, **_kwargs: _llmpaint_server.PaintProbe(None, False, False, False),
    )
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
    # 与其它 Qt 窗口测试一致：显式应用主题，控件尺寸断言不依赖测试执行顺序。
    _qt_theme.apply_theme(QApplication.instance(), _guitheme.resolve_theme("dark-blue"))
    stale = qt_top_levels()
    win = LlmWindow(tmp_path / "ui" / "llmw.json")
    win.overview.stop()
    yield win
    win.close()
    # 关窗不等于销毁：按需构建的页面 / 菜单 / 对话框没有父控件，只 close() 会把
    # 它们留在 QApplication 里；累积后下一次 apply_theme 的 setStyleSheet 要为整棵
    # 旧树重新 polish，单次数秒（xdist 尾部 99%→100% 卡住的根因）。
    destroy_qt_top_levels(stale)


def drain_background_tasks(limit: int = 200) -> None:
    """等已提交的后台任务与回调全部落地。

    ``xdist`` 并行下固定次数的 ``processEvents`` 可能在线程完成前就退出，残留的
    扩散模型扫描回调会把测试注入的资产覆盖成空结果。
    """
    from fcmd.gui import workers

    for _ in range(limit):
        QApplication.processEvents()
        if not workers._PENDING:
            return


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


def test_vllm_section_collapsed_and_disabled_on_unsupported_platform(window: Any) -> None:
    """不支持的系统上 vLLM 设置区折叠、引擎选项禁用，不能让人以为可用。"""
    from fcmd.gui.widgets import Collapsible

    window.show_page("settings")
    settings = window.page("settings")
    assert isinstance(settings.vllm_section, Collapsible)
    assert settings.vllm_section.toggle.isChecked() is False
    window.show_page("run")
    run = window.page("run")
    assert run.engine_choice.key() == "llama.cpp"
    assert run.engine_choice._buttons["vllm"].isEnabled() is False


def test_vllm_section_expanded_on_supported_platform(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """支持平台上 vLLM 引擎卡正常展开，安装按钮可用。"""
    from fcmd.apps.llm import _llmvllm
    from fcmd.apps.llm._llmw_qt_engine_card import EngineCard

    platform = _llmvllm.VllmPlatform(
        "wsl",
        True,
        "WSL2 · Ubuntu · Python 3.12",
        (_llmvllm.VllmInterpreter("/usr/bin/python3.12", "3.12.3", "Ubuntu", "/root", "RTX 5090"),),
    )
    monkeypatch.setattr(_llmvllm, "detect_platform", lambda *_args, **_kwargs: platform)
    window.show_page("settings")
    card = window.page("settings").vllm_section
    assert isinstance(card, EngineCard)
    assert card.install_button.isEnabled()
    # 运行页可能已随窗口预构建并缓存了平台快照；引擎选项文案由纯函数单测覆盖。


def test_vllm_mode_lists_hf_models_and_builds_wsl_plan(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """vLLM 模式：运行页只列 HF 目录，启动命令经 wsl.exe 并带上 vLLM 参数。"""
    from types import SimpleNamespace

    from fcmd.apps.llm import _llmconfig, _llmvllm

    interpreter = _llmvllm.VllmInterpreter("/usr/bin/python3.12", "3.12.3", "Ubuntu", "/root", "RTX 5090")
    platform = _llmvllm.VllmPlatform("wsl", True, "WSL2 · Ubuntu · Python 3.12", (interpreter,))
    monkeypatch.setattr(_llmvllm, "detect_platform", lambda *_args, **_kwargs: platform)
    _llmvllm.install_engine(
        "0.34.0",
        interpreter=interpreter,
        execute=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=""),
    )
    model_dir = tmp_path / "models" / "Qwen2.5-0.5B-Instruct"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text('{"max_position_embeddings": 4096}', encoding="utf-8")
    (model_dir / "model.safetensors").write_bytes(b"\0" * 8)
    _llmconfig.set_option("infer_engine", "vllm")

    from fcmd.apps.llm._llmw_qt_run import RunPage

    # 窗口可能已缓存不支持平台时构建的运行页；这里按当前桩重新构建一页。
    run = RunPage(window.state)
    # 平台探测已是后台任务：等线程池落地后再看引擎与模型候选。
    drain_background_tasks()
    assert run.engine_choice.key() == "vllm"
    assert [entry.path for entry in run._usage_models()] == [model_dir]
    plan = run.build_plan()
    assert plan.engine_kind == "vllm"
    argv = plan.argv()
    assert argv[0] == "wsl.exe"
    assert argv[1:4] == ["-d", "Ubuntu", "--"]
    assert argv[4] == "/root/.fcmd/engines/vllm/0.34.0/venv/bin/python"
    model_arg = argv[argv.index("--model") + 1]
    # wsl_path 只转换 Windows 盘符路径；Linux runner 没有 C:，路径原样透传
    # （wsl.exe 分支只会在 Windows 运行时出现）。
    if os.name == "nt":
        assert model_arg.startswith("/mnt/c/")
    else:
        assert model_arg == str(model_dir)
    assert argv.count("--max-model-len") == 1
    assert plan.context == 4096


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


def test_status_bar_follows_current_page(window: Any) -> None:
    """状态栏按页面隔离：切页显示该页自己的状态，切回时恢复。"""
    window.show_page("chat")
    # 服务清单在后台读回后才写状态栏，等它落地再断言。
    drain_background_tasks()
    chat_status = window.status_text()
    assert "没有已登记的服务" in chat_status
    window.set_status("已复制当前对话内容")
    window.show_page("overview")
    assert window.status_text() == window.page_status("overview")
    assert window.status_text() != "已复制当前对话内容"
    window.show_page("chat")
    assert window.status_text() == "已复制当前对话内容"


def test_window_builds_all_pages(window: Any) -> None:
    keys = window.nav.keys()
    assert keys == ("overview", "models", "run", "chat", "flow", "settings")
    for key in keys:
        window.show_page(key)
        assert window.current_page() == key
        assert window.page(key) is not None


def test_chat_paint_toggle_and_missing_model(window: Any) -> None:
    """对话页「画图」模式胶囊：选中即按画图配置出图，缺模型时只报错不派后台任务。"""
    window.show_page("chat")
    # 先把后台读的服务清单落地：它的状态文字晚到会盖掉下面这步的提示。
    drain_background_tasks()
    page = window.page("chat")
    assert page.chat_toggle.isChecked()
    assert not page.paint_toggle.isChecked()
    page.paint_toggle.setChecked(True)
    QApplication.processEvents()
    assert not page.chat_toggle.isChecked()
    assert page.input_edit.placeholderText().startswith("输入画图提示词")
    assert window.state.settings.paint["enabled"] is True
    assert window.status_text().startswith("已启用画图")

    page.input_edit.setPlainText("一只在雪地里的红狐狸")
    page.send()
    assert not page._running
    assert "画图失败" in page.transcript.toPlainText()
    assert "主模型" in page.transcript.toPlainText()
    assert window.status_text().startswith("画图参数错误")
    # 切回对话后回到对话提示词，互斥组保持始终有一个选中。
    page.paint_toggle.setChecked(False)
    assert page.chat_toggle.isChecked()
    assert page.input_edit.placeholderText().startswith("输入问题")
    assert window.state.settings.paint["enabled"] is False


def test_chat_mode_chips_are_rounded_exclusive_toggles(window: Any) -> None:
    """对话 / 画图为元宝式圆角胶囊：图标 + 文字，互斥切换并同步图标色调。"""
    window.show_page("chat")
    chat = window.page("chat")
    assert chat.chat_toggle.objectName() == "ToggleChip"
    assert chat.paint_toggle.objectName() == "ToggleChip"
    for chip, icon, tone in (
        (chat.chat_toggle, "chat", "accent"),
        (chat.paint_toggle, "paint", "muted"),
    ):
        assert chip.isCheckable()
        assert chip.height() == 34  # 与右侧 34px 发送图标同高
        assert chip.text()
        assert chip.icon().isNull() is False
        assert chip.property("fcmdIconName") == icon
        assert chip.property("fcmdIconTone") == tone

    chat.paint_toggle.click()
    assert chat.paint_toggle.isChecked()
    assert not chat.chat_toggle.isChecked()
    assert chat.paint_toggle.property("fcmdIconTone") == "accent"
    assert chat.chat_toggle.property("fcmdIconTone") == "muted"
    assert window.state.settings.paint["enabled"] is True

    chat.chat_toggle.click()
    assert chat.chat_toggle.isChecked()
    assert not chat.paint_toggle.isChecked()
    assert window.state.settings.paint["enabled"] is False
    assert window.status_text().startswith("已关闭画图")


def test_chat_config_contains_paint_sample_settings(window: Any) -> None:
    """对话配置只留画图采样 / 输出；模型与引擎配置在运行页「画图」页签。"""
    window.show_page("chat")
    page = window.page("chat")
    assert not page.config_dialog.isVisible()
    page.open_config()
    QApplication.processEvents()
    assert page.config_dialog.isVisible()
    section = page.paint_section
    for widget in (section.width, section.steps, section.sampler, section.output_label):
        assert widget.window() is page.config_dialog
    # 引擎 / 模型组件不在对话配置里。
    for name in ("port", "model_combo", "auto_start", "preview_label"):
        assert not hasattr(section, name), name
    section.width.setValue(1024)
    assert section.settings()["width"] == 1024
    page.config_dialog.close()


def test_chat_config_dialog_starts_compact(window: Any) -> None:
    """配置窗口默认贴合内容：画图采样分区收起，展开时才长高。"""
    from PySide6.QtWidgets import QApplication

    window.show_page("chat")
    page = window.page("chat")
    page.open_config()
    QApplication.processEvents()
    dialog = page.config_dialog
    from fcmd.gui.widgets import PageScroll

    # 不再用滚动区：窗口高度直接跟随内容。
    assert dialog.findChild(PageScroll) is None
    assert page.paint_collapsible.is_expanded() is False
    collapsed = dialog.height()
    assert collapsed < 420
    assert page.paint_section.isVisibleTo(dialog) is False

    page.paint_collapsible.set_expanded(True)
    QApplication.processEvents()
    assert page.paint_collapsible.is_expanded() is True
    assert dialog.height() > collapsed
    assert page.paint_section.isVisibleTo(dialog) is True
    dialog.close()


def test_running_models_show_count_badges(window: Any) -> None:
    """运行中的模型数量：运行页对应类别页签 + 侧边栏「运行」都显示圆形徽标。"""
    from fcmd.apps.llm import _llmpaint_server
    from fcmd.apps.llm.llmbase import ServeState

    def serve_state(model: str, port: int) -> ServeState:
        return ServeState(
            name=f"svc-{port}",
            pid=1000 + port,
            create_time=0.0,
            host="127.0.0.1",
            port=port,
            model=model,
            profile="balanced",
            context=8192,
            engine="",
            alias="",
            api_key="",
            log="",
            started_at="",
        )

    # 构造期的服务刷新已后台化：先等它落地，否则晚到的真实服务列表会覆盖下面手工造的状态。
    drain_background_tasks()
    window.state.alive_states = [
        serve_state("/models/Qwen3-Coder-30B.gguf", 8081),
        serve_state("/models/Qwen3-8B-Instruct.gguf", 8082),
    ]
    window.state.servicesChanged.emit(window.state.alive_states)
    bar = window.run_page.tabs.tabBar()
    assert bar.badge(0) == 2  # 文本（对话 / 编程 / 推理合并）
    assert bar.badge(1) == 0  # 图像
    assert window.nav.badge("run") == 2

    # 图像（sd.cpp）是独立后端：单独计数并落到「图像」页签
    window.run_page._paint_probe = _llmpaint_server.PaintProbe(None, True, True, True)
    window.run_page.sync_running_badges()
    assert bar.badge(1) == 1
    assert window.nav.badge("run") == 3

    # 全部停止：页签与侧边栏徽标一起收起
    stopped: list[ServeState] = []
    window.state.alive_states = stopped
    window.state.servicesChanged.emit([])
    window.run_page._paint_probe = None
    window.run_page.sync_running_badges()
    assert [bar.badge(index) for index in range(bar.count())] == [0, 0]
    assert window.nav.badge("run") == 0


def test_sidebar_badge_restores_paint_only_after_restart(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """重启应用时只跑着画图服务：侧边栏「运行」徽标也要恢复（探针早于信号连接）。"""
    from fcmd.apps.llm import _llmpaint_server
    from fcmd.apps.llm._llmw_qt_window import LlmWindow

    state = _llmpaint_server.PaintState(
        pid=1234,
        create_time=1.0,
        host="127.0.0.1",
        port=1234,
        engine_tag="sd-test",
        exe="sd-server.exe",
        model="qwen-image.safetensors",
        components={},
        fingerprint=("sd-test",),
        log="sd-server.log",
        started_at="",
    )
    monkeypatch.setattr(_llmpaint_server, "status", lambda home=None: (state, True))
    monkeypatch.setattr(
        _llmpaint_server,
        "probe_paint",
        lambda *_args, **_kwargs: _llmpaint_server.PaintProbe(state, True, True, True),
    )
    restarted = LlmWindow(tmp_path / "ui" / "llmw-restart.json")
    try:
        restarted.overview.stop()
        QApplication.processEvents()
        assert restarted.run_page.running_counts()["paint"] == 1
        assert restarted.run_page.tabs.tabBar().badge(1) == 1
        assert restarted.nav.badge("run") == 1
    finally:
        restarted.overview.stop()
        restarted.deleteLater()


def test_storage_card_removes_directory_and_persists_config(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """公共存储卡支持移除目录：组件信号 → llmw 落盘 models_dirs。"""
    from fcmd.apps.llm import _llmconfig
    from fcmd.gui.widgets import storage as storage_module

    first = tmp_path / "models-a"
    second = tmp_path / "models-b"
    _llmconfig.set_models_dirs([first, second])
    monkeypatch.delenv("FCMD_MODELS", raising=False)
    window.show_page("settings")
    page = window.page("settings")
    page.refresh()
    monkeypatch.setattr(storage_module, "confirm", lambda *_args, **_kwargs: True)
    page.storage_card._remove(str(second))
    assert _llmconfig.configured_models_dirs() == [first]
    assert page.storage_card.roots() == [first]


def test_storage_card_can_remove_last_dir_and_falls_back_to_default(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """移除最后一个模型目录后回落到默认目录，而不是卡在空网格。"""
    from fcmd.apps.llm import _llmconfig
    from fcmd.gui.widgets import storage as storage_module

    only = tmp_path / "only-models"
    _llmconfig.set_models_dirs([only])
    monkeypatch.delenv("FCMD_MODELS", raising=False)
    window.show_page("settings")
    page = window.page("settings")
    page.refresh()
    monkeypatch.setattr(storage_module, "confirm", lambda *_args, **_kwargs: True)
    page.storage_card._remove(str(only))
    app = QApplication.instance()
    assert app is not None
    app.processEvents()
    assert _llmconfig.configured_models_dirs() == []
    assert page.storage_card.roots() == [_llmconfig.default_models_dir()]


def test_paint_watchdog_restarts_once_and_stops_after_repeated_failures(
    window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """画图服务意外退出：看护自动重启一次；连续失败后停止看护。"""
    from fcmd.apps.llm import _llmpaint_server
    from fcmd.apps.llm import _llmw_qt_run as run_module

    state = _llmpaint_server.PaintState(
        pid=999,
        create_time=1.0,
        host="127.0.0.1",
        port=1234,
        engine_tag="sd-test",
        exe="sd-server.exe",
        model="model.safetensors",
        components={},
        fingerprint=("sd-test",),
        log="sd-server.log",
        started_at="",
    )
    run = window.run_page
    monkeypatch.setattr(_llmpaint_server, "status", lambda home=None: (state, False))
    submitted: list[object] = []
    monkeypatch.setattr(run_module, "submit_daemon", lambda fn, *args, **kwargs: submitted.append((fn, args, kwargs)))
    run._paint_expected = True
    run._paint_watch_failures = 0
    run._paint_watch_retry_at = 0.0
    run._watch_paint()
    assert submitted
    assert run._paint_expected is True

    # 模拟连续多次自动重启失败：达到上限后不再自动拉起。
    run._paint_watch_restarting = False
    run._paint_watch_failures = 3
    run._watch_paint()
    assert run._paint_expected is False


def test_flow_page_builds_with_builtin_templates(window: Any) -> None:
    """工作流页首次打开：内置模板落盘、节点卡片可编辑、运行按钮可用。"""
    window.show_page("flow")
    QApplication.processEvents()
    page = window._flow_page
    assert page is not None
    assert len(page._flows) == 3
    assert [card.definition.label for card in page._cards]
    assert page._name_edit.text() in {flow.name for flow in page._flows}
    assert page._run_button.isEnabled() is True
    # 在「文本 → 文生图 → 保存」之间插入 LLM 节点：按端口兼容性自动选择位置。
    page._add_node("llm.chat")
    assert [card.definition.type for card in page._cards][:2] == ["text.input", "llm.chat"]


def test_flow_page_applies_recommendation_to_nodes(window: Any) -> None:
    """智能推荐写回节点：出图尺寸 / 步数 / CFG 随方案更新，推荐卡片可读。"""
    from fcmd.apps.llm._llmw_flow_nodes import IMAGE_TXT2IMG
    from fcmd.apps.llm._llmw_flow_recommend import FlowRecommendation

    window.show_page("flow")
    QApplication.processEvents()
    page = window._flow_page
    image_flow = next(flow for flow in page._flows if any(node.type == IMAGE_TXT2IMG for node in flow.nodes))
    page._select_flow(image_flow.id)
    page._recommendations = (
        FlowRecommendation(
            scene="image",
            scene_label="文生图",
            attribute="quality",
            attribute_label="画质优先",
            score=90,
            score_parts=(100, 88),
            llm_name="",
            llm_path=None,
            llm_profile="",
            llm_context=0,
            llm_temperature=0.7,
            llm_max_tokens=1024,
            llm_system="",
            image_name="",
            image_path=None,
            width=1024,
            height=1024,
            steps=30,
            cfg_scale=7.0,
            rationale="测试方案",
            basis="测试",
        ),
    )
    page._render_recommendation()
    assert page._apply_button.isEnabled() is True
    page._apply_recommendation()
    image_card = next(card for card in page._cards if card.node.type == IMAGE_TXT2IMG)
    assert image_card.node.params["width"] == 1024
    assert image_card.node.params["height"] == 1024
    assert image_card.node.params["steps"] == 30
    assert image_card.node.params["cfg_scale"] == 7.0


def test_run_page_usage_tabs_and_paint_service(window: Any) -> None:
    """运行页分「文本 / 图像」：文本配 llama.cpp 参数，图像只配 sd.cpp 参数与启停。"""
    window.show_page("run")
    run = window.run_page
    assert [run.tabs.tabText(index) for index in range(run.tabs.count())] == ["文本", "图像"]
    # 页签用胶囊样式：去掉 QTabBar 基线（多余横条），页签与内容同底色衔接。
    bar = run.tabs.tabBar()
    assert run.tabs.objectName() == "PillTabs"
    assert bar.objectName() == "PillTabBar"
    assert bar.drawBase() is False
    assert bar.expanding() is False
    # 文本页独占一套启动参数表单。
    assert run.service_page.parent() is run.text_tab
    run.tabs.setCurrentIndex(0)
    QApplication.processEvents()
    assert run.service_page.parent() is run.text_tab

    # 画图页签：选模型 + 启停按钮，配置在下面的「启动参数」卡片。
    assert run.paint_model_card.title() == "选择模型"
    # 运行状态只看按钮文案 + 下拉「✓ 已启动」标记：不再放状态胶囊 / 小字说明。
    assert not hasattr(run, "paint_service_tag")
    assert not hasattr(run, "paint_service_detail")
    assert run.paint_config_card.title() == "启动参数"
    assert run.paint_action_button.text() in {"启动服务", "停止服务"}
    assert run.paint_restart_button.text() == "重启服务"
    assert not hasattr(run, "paint_install_button")
    assert not hasattr(run, "paint_card")
    section = run.paint_engine_section
    for widget in (section.port, section.model_combo, section.auto_start, section.command_preview):
        assert widget.window() is window
    # 参数卡与对话 / 编程同构：加载方式方块 + 运行选项胶囊 + 只读命令预览。
    from fcmd.gui.widgets import ChoiceGroup, ToggleChip

    assert isinstance(section.mode_choice, ChoiceGroup)
    for key in ("auto", "checkpoint", "diffusion"):
        section.mode_choice.set_key(key)
        assert section.settings()["mode"] == key
    for chip in (section.auto_start, section.offload_to_cpu, section.vae_tiling, section.diffusion_fa):
        assert isinstance(chip, ToggleChip)
        assert chip.isCheckable()
    assert section.command_preview.objectName() == "Log"
    assert section.command_preview.isReadOnly()
    # 主模型下拉搬进「选择模型」卡片（不留在参数表单里）。
    assert section._show_model is False
    assert section.model_combo.window() is window
    assert section.settings()["port"] == 1234
    run.refresh_paint()
    assert "paint" not in tuple(window.nav.keys())


def test_text_usage_models_list_all_llm(window: Any, tmp_path: Path) -> None:
    """文本页列出全部 LLM（含对话 / 编程 / 推理），不再按视频 / 音频拆分类别。"""
    from fcmd.apps.llm.llmbase import ModelEntry

    run = window.run_page
    paths = (
        tmp_path / "Qwen2.5-Coder-7B.gguf",
        tmp_path / "DeepSeek-R1-8B.gguf",
        tmp_path / "Llama-3-8B.gguf",
    )
    window.state.models = [ModelEntry(tmp_path, path, 1) for path in paths]

    run._usage = "text"
    assert [entry.path.name for entry in run._usage_models()] == [path.name for path in paths]


def test_paint_model_combo_has_no_placeholder(window: Any, tmp_path: Path) -> None:
    """主模型下拉不再插「（未找到扩散模型）」占位项：有模型时第一项就是模型。"""
    from fcmd.apps.llm import _llmpaint

    main = tmp_path / "sdxl.safetensors"
    main.write_bytes(b"x" * 16)
    asset = _llmpaint.PaintAsset(main, "checkpoint", "sdxl", 16)
    window.show_page("run")
    section = window.run_page.paint_engine_section
    section._apply_assets(_llmpaint.PaintAssets((asset,), (tmp_path,)))
    labels = [section.model_combo.itemText(index) for index in range(section.model_combo.count())]
    assert labels == ["sdxl.safetensors"]
    assert all("未找到" not in label for label in labels)
    assert section.model_combo.currentData() == str(main)

    # 没有模型时下拉是空的（提示写在底部状态栏，不占第一项）。
    # 清掉已保存的选择，避免「保留已保存路径」逻辑补回一条。
    section._settings["model"] = ""
    section._apply_assets(_llmpaint.PaintAssets((), (tmp_path,)))
    assert section.model_combo.count() == 0


def test_paint_model_combo_marks_running_model(window: Any, tmp_path: Path) -> None:
    """画图下拉与对话 / 编程一致：运行中的扩散模型显示「✓ 已启动」并置绿。"""
    from fcmd.apps.llm import _llmpaint, _llmpaint_server

    main = tmp_path / "sdxl.safetensors"
    main.write_bytes(b"x" * 16)
    asset = _llmpaint.PaintAsset(main, "checkpoint", "sdxl", 16)
    window.show_page("run")
    run = window.run_page
    section = run.paint_engine_section
    section._apply_assets(_llmpaint.PaintAssets((asset,), (tmp_path,)))
    base = section.model_combo.itemText(0)
    assert "已启动" not in base

    state = _llmpaint_server.PaintState(
        pid=4321,
        create_time=0.0,
        host="127.0.0.1",
        port=1234,
        engine_tag="master-1094",
        exe="sd-server",
        model=str(main),
        components={},
        fingerprint=(),
        log="sd-server.log",
        started_at="",
    )
    run._paint_probe = _llmpaint_server.PaintProbe(state, True, True, True)
    run._refresh_paint_marker()
    assert section.model_combo.itemText(0) == f"{base} · ✓ 已启动"
    assert section.model_combo.property("running") == "true"
    assert bool(section.model_combo.itemData(0, int(Qt.ItemDataRole.UserRole) + 1)) is True

    # 服务退出后标记撤回，文案恢复（状态文件残留 / 端口探针都不会影响）。
    run._paint_probe = _llmpaint_server.PaintProbe(None, False, False, False)
    run._refresh_paint_marker()
    assert section.model_combo.itemText(0) == base
    assert section.model_combo.property("running") == "false"


def test_paint_status_controls_buttons_only(window: Any) -> None:
    """状态文件丢失 / 服务由外部启动：只反映在启停按钮上（不再渲染状态胶囊 / 细节行）。"""
    from fcmd.apps.llm import _llmpaint_server

    window.show_page("run")
    run = window.run_page
    run.tabs.setCurrentIndex(1)
    probe = _llmpaint_server.PaintProbe(
        state=None,
        alive=False,
        reachable=True,
        port_open=True,
        occupants=(_llmpaint_server.PortOccupant(4321, "sd-server.exe"),),
    )
    # 手动推进探针序号：丢弃页签切换时还在排队的后台探针回调。
    run._paint_probe_seq += 1
    seq = run._paint_probe_seq
    run._paint_probed(seq, probe)
    assert run.paint_action_button.text() == "停止服务"
    # 重启只对已登记的服务开放，避免无状态可停时盲重启。
    assert not run.paint_restart_button.isEnabled()

    # 端口被其它程序占用（健康探针不通）：仍按「未运行」显示启动按钮。
    run._paint_probed(
        seq,
        _llmpaint_server.PaintProbe(None, False, False, True, (_llmpaint_server.PortOccupant(9, "python.exe"),)),
    )
    assert run.paint_action_button.text() == "启动服务"

    # 陈旧探针结果不允许覆盖新状态。
    run._paint_probed(0, probe)
    assert run.paint_action_button.text() == "启动服务"


def test_paint_save_params_persists_immediately(window: Any, tmp_path: Path) -> None:
    """画图「保存参数」立即写入 llmw.json：不等延迟定时器 / 页签切换。"""
    window.show_page("run")
    section = window.run_page.paint_engine_section
    section.port.setValue(4321)
    section.offload_to_cpu.setChecked(True)
    section.save_params_button.click()
    assert window.state.settings.paint["port"] == 4321
    assert window.state.settings.paint["offload_to_cpu"] is True
    saved = json.loads((tmp_path / "ui" / "llmw.json").read_text(encoding="utf-8"))
    assert saved["paint"]["port"] == 4321
    assert saved["paint"]["offload_to_cpu"] is True
    assert window.status_text().startswith("画图启动参数已保存")


def test_paint_fill_params_matches_command_preview(window: Any, tmp_path: Path) -> None:
    """「填入当前参数」写入监听 / 组件 / 开关；预览与组件去重后不重复追加。"""
    from fcmd.apps.llm import _llmpaint
    from fcmd.apps.llm._llmw_qt_paint import components_from_settings

    main = tmp_path / "z_image_turbo-Q8_0.gguf"
    main.write_bytes(b"x" * 16)
    asset = _llmpaint.PaintAsset(main, "diffusion", "z-image", 16)
    window.show_page("run")
    run = window.run_page
    run.tabs.setCurrentIndex(1)
    section = run.paint_engine_section
    drain_background_tasks()  # 切页签会重扫扩散模型：等它落地再注入测试资产
    section._settings["model_dir"] = str(tmp_path)
    section._apply_assets(_llmpaint.PaintAssets((asset,), (tmp_path,)))
    section.model_combo.setCurrentIndex(section.model_combo.findData(str(main)))
    section.vae_tiling.setChecked(True)
    before = section.command_preview.toPlainText()
    assert "--vae-tiling" in before

    section.fill_params_button.click()
    text = section.extra_args_edit.toPlainText()
    assert "--listen-ip" in text
    assert "--listen-port" in text
    assert "--vae-tiling" in text
    assert str(main) not in text  # 主模型路径不重复填进自定义参数
    assert text.count("--listen-port") == 1
    assert section.command_preview.toPlainText() == before
    assert components_from_settings(run.paint_settings()).extra_args == ()


def test_model_download_finished_rescans_paint_assets(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """模型下载完成立即重扫画图资产：扩散权重不进 LLM 列表，不能只等 modelsChanged。"""
    window.show_page("models")
    run = window.run_page
    calls: list[str] = []
    monkeypatch.setattr(run, "refresh_paint_assets", lambda: calls.append("paint"))
    window.page("models").browse.downloadFinished.emit()
    assert calls == ["paint"]


def test_paint_tab_entry_rescans_diffusion_models(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """切到画图页签会重扫扩散模型，刚下载完的模型不必等其它页面的信号。"""
    window.show_page("run")
    run = window.run_page
    run.tabs.setCurrentIndex(0)
    QApplication.processEvents()
    calls: list[str] = []
    monkeypatch.setattr(run, "refresh_paint_assets", lambda: calls.append("paint"))
    run.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert calls == ["paint"]


def test_paint_missing_component_offers_download(window: Any, tmp_path: Path) -> None:
    """缺 VAE 且家族有候选仓库时，画图配置里露出「下载缺失的 VAE…」。"""
    from fcmd.apps.llm import _llmpaint

    main = tmp_path / "z_image_turbo-Q8_0.gguf"
    main.write_bytes(b"x" * 16)
    asset = _llmpaint.PaintAsset(main, "diffusion", "z-image", 16)
    window.show_page("run")
    run = window.run_page
    for _ in range(5):
        QApplication.processEvents()
    run.tabs.setCurrentIndex(1)
    section = run.paint_engine_section
    section._settings["model_dir"] = str(tmp_path)
    section._apply_assets(_llmpaint.PaintAssets((asset,), (tmp_path,)))
    section.model_combo.setCurrentIndex(section.model_combo.findData(str(main)))
    QApplication.processEvents()
    assert not section.download_button.isHidden()
    assert "VAE" in section.download_button.text()

    # 额外参数里给了 --vae / --llm 后不再算缺失，按钮收起。
    section.extra_args_edit.setPlainText(f"--vae {tmp_path / 'my-vae.safetensors'} --llm {tmp_path / 'enc.gguf'}")
    QApplication.processEvents()
    assert section.download_button.isHidden()


def test_paint_missing_model_hint_points_to_incomplete_download(window: Any, tmp_path: Path) -> None:
    """没有可用扩散模型时，画图卡片提示去继续下载（并列出 ``*.part`` 文件）。"""
    from fcmd.apps.llm import _llmpaint

    window.show_page("run")
    run = window.run_page
    for _ in range(5):
        QApplication.processEvents()
    run.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    section = run.paint_engine_section
    section._settings["model_dir"] = str(tmp_path)
    section._apply_assets(_llmpaint.PaintAssets((), (tmp_path,)))
    assert "未找到扩散主模型" in window.status_text()

    partial = tmp_path / "z_image_turbo-Q4_K.gguf.part"
    partial.write_bytes(b"x" * 16)
    section._apply_assets(_llmpaint.PaintAssets((), (tmp_path,)))
    assert "z_image_turbo-Q4_K.gguf.part" in window.status_text()
    assert "继续下载" in window.status_text()

    # 有可用主模型时回到常态说明，不再报缺模型。
    main = tmp_path / "z_image_turbo-Q4_K.gguf"
    main.write_bytes(b"x" * 16)
    asset = _llmpaint.PaintAsset(main, "diffusion", "z-image", 16 * 1024 * 1024)
    section._apply_assets(_llmpaint.PaintAssets((asset,), (tmp_path,)))
    assert "未找到" not in window.status_text()
    assert not hasattr(run, "paint_hint")


def test_paint_auto_pair_fills_model_and_components(window: Any, tmp_path: Path) -> None:
    """自动配对：主模型为空时先填模型再补组件，配对后组件能通过启动校验。"""

    from fcmd.apps.llm import _llmpaint
    from fcmd.apps.llm._llmw_qt_paint import components_from_settings

    def weight(rel: str) -> Path:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.truncate(2 * 1024 * 1024)
        return path

    main = weight("Qwen-Image-2.1/model.safetensors")
    vae = weight("vae/qwen_image_vae.safetensors")
    encoder = weight("text_encoders/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf")
    assets = _llmpaint.scan_assets([tmp_path])

    window.show_page("run")
    run = window.run_page
    section = run.paint_engine_section
    # 先等构造时的后台扫描回调落地，再注入测试资产，避免被空扫描结果覆盖。
    drain_background_tasks()
    section._apply_assets(assets)
    assert section.model_combo.count() > 0

    # 选择主模型后自动配对：不再有独立「自动配对」按钮。
    assert not hasattr(section, "suggest_button")
    for combo in section.role_combos.values():
        combo.setCurrentIndex(0)
    section.model_combo.setCurrentIndex(-1)
    if section.model_combo.currentData() != str(main):
        section.model_combo.setCurrentIndex(section.model_combo.findData(str(main)))
    QApplication.processEvents()
    assert section.model_combo.currentData() == str(main)
    assert section.role_combos["vae"].currentData() == str(vae)
    assert section.role_combos["llm"].currentData() == str(encoder)
    components = components_from_settings(run.paint_settings())
    _llmpaint.validate_components(components)  # 不应再抛「请选择主模型 / 缺少组件」


def test_generated_pages_expose_core_widgets(window: Any) -> None:
    window.show_page("models")
    models = window.page("models")
    assert models.browse is not None
    window.show_page("chat")
    chat = window.page("chat")
    assert chat.service_combo.count() >= 1
    chat.reset()
    assert "新对话" in window.status_text()
    window.show_page("settings")
    settings_page = window.page("settings")
    settings_page.refresh()
    assert settings_page.storage_grid is not None
    assert settings_page.storage_grid.findChildren(object)


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
    assert browse.valid_check.isChecked() is True
    browse.valid_check.click()
    assert browse._valid_only is False
    browse.valid_check.click()
    assert browse._valid_only is True
    assert submitted and submitted[0] == browse._search_work


def test_category_switch_while_searching_queues_latest_query(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """上一页请求未返回时切换分类：排队最新查询，不能静默丢弃导致列表为空 / 串类。"""
    from fcmd.apps.llm import _llmw_qt_center as center

    window.show_page("models")
    browse = window.page("models").browse
    submitted: list[object] = []
    monkeypatch.setattr(center, "submit", lambda fn, *args, **kwargs: submitted.append(fn))
    browse._searching = True
    browse._pending_search = None
    browse._filter_key = "paint"
    browse._filter_query = "图像生成"
    browse.start_search(1, force=True)
    assert submitted == []
    assert browse._pending_search == (1, True)
    browse._search_finished()
    QApplication.processEvents()
    assert submitted and submitted[0] == browse._search_work
    assert browse._pending_search is None


def test_paint_search_uses_multi_keyword_merger(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """画图分类走多关键字合并，保证 Qwen-Image / MiniMax 等官方仓库都能命中。"""
    from fcmd.apps.llm import _llmw_models
    from fcmd.apps.llm import _llmw_qt_center as center
    from fcmd.apps.llm.llmdown import ModelSearch

    window.show_page("models")
    browse = window.page("models").browse
    calls: list[tuple[str, ...]] = []

    def fake_multi(
        keywords: Sequence[str],
        page: int = 1,
        page_size: int = 20,
        sort: str = "default",
        timeout: float = 30,
    ) -> ModelSearch:
        calls.append(tuple(keywords))
        return ModelSearch((), 0, page, page_size)

    monkeypatch.setattr(center, "search_models_multi", fake_multi)
    browse._search_work(browse._list_cache, "k", _llmw_models.PAINT_FILTER_QUERY, 1, 30, "default")
    browse._search_work(browse._list_cache, "k2", _llmw_models.TEXT_FILTER_QUERY, 1, 30, "default")
    assert calls == [_llmw_models.IMAGE_SEARCH_TERMS, _llmw_models.TEXT_SEARCH_TERMS]


def test_all_and_local_keep_seen_category_items(window: Any) -> None:
    """「画图」里看到的模型切回「全部 / 适合本机」后不能消失。"""
    from fcmd.apps.llm.llmdown import ModelSearch, ModelSummary

    window.show_page("models")
    browse = window.page("models").browse
    paint = ModelSummary(repo="Qwen/Qwen-Image-2.1", storage_size=20 * 1024**3)
    chat = ModelSummary(repo="Qwen/Qwen3-7B", storage_size=8 * 1024**3)
    browse._remember_items((paint,))
    browse._last_result = ModelSearch((chat,), 1, 1, 30)
    browse._source_filter = "online"
    for key in ("all", "local"):
        browse._filter_key = key
        browse._render_results()
        repos = [item.repo for item in browse._result_items]
        assert paint.repo in repos and chat.repo in repos, key
    # 具体分类仍然只显示命中的模型。
    browse._filter_key = "paint"
    browse._render_results()
    assert [item.repo for item in browse._result_items] == [paint.repo]


def test_model_row_copy_button_copies_full_repo(window: Any) -> None:
    """模型行标题右侧的小复制按钮复制完整仓库名（标题本身只显示模型名）。"""
    from PySide6.QtGui import QGuiApplication

    window.show_page("models")
    browse = window.page("models").browse
    from fcmd.apps.llm.llmdown import ModelSearch, ModelSummary

    repo = "Qwen/Qwen3.8-27B"
    browse._last_result = ModelSearch((ModelSummary(repo=repo, name="Qwen3.8-27B"),), 1, 1, 30)
    browse._source_filter = "all"
    browse._filter_key = "all"
    browse._render_results()
    item = browse.models.item(0)
    row = browse.models.itemWidget(item)
    assert row.copy_text() == repo
    button = row.copy_button()
    assert button is not None and button.isHidden() is False
    assert repo in button.toolTip()

    button.click()
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None and clipboard.text() == repo


def test_local_model_keeps_official_entry_in_local_fit_view(window: Any) -> None:
    """「适合本机」下：已安装模型的同名官方条目不能被容量估算藏掉。"""
    from fcmd.apps.llm.llmdown import ModelSearch, ModelSummary

    window.show_page("models")
    browse = window.page("models").browse
    local = ModelSummary(repo="Qwen3.8-27B", name="Qwen3.8-27B", storage_size=16 * 1024**3)
    official = ModelSummary(repo="Qwen/Qwen3.8-27B", name="Qwen3.8-27B", storage_size=52 * 1024**3)
    other = ModelSummary(repo="Qwen/Qwen3.8-72B", name="Qwen3.8-72B", storage_size=120 * 1024**3)
    browse._installed_models = {"qwen3.8-27b": local}
    browse._installed_repos = {"qwen3.8-27b"}
    browse._usable_repos = {"qwen3.8-27b"}
    browse._last_result = ModelSearch((official, other), 2, 1, 30)
    browse._source_filter = "all"
    browse._filter_key = "local"
    browse._group_same = True
    browse._name_filter = ""
    # 显存 / 内存口径的容量小于官方整仓体积（52 GB）
    browse._local_capacity = lambda: 24 * 1024**3  # type: ignore[method-assign]
    browse._render_results()

    repos = {item.repo for item in browse._result_items}
    assert "Qwen/Qwen3.8-27B" in repos  # 同名官方条目保留
    assert "Qwen/Qwen3.8-72B" not in repos  # 无关的超容模型仍按「适合本机」过滤
    assert "Qwen3.8-27B" in repos  # 本地模型本身也在


def test_local_directory_does_not_hide_official_model(window: Any) -> None:
    """本地已有同名目录（Qwen3.8-27B）时，官方仓库仍要显示出来。"""
    from fcmd.apps.llm.llmdown import ModelSearch, ModelSummary

    window.show_page("models")
    browse = window.page("models").browse
    local = ModelSummary(repo="Qwen3.8-27B", storage_size=40 * 1024**3)
    official = ModelSummary(repo="Qwen/Qwen3.8-27B", name="Qwen3.8-27B", storage_size=40 * 1024**3)
    browse._installed_models = {"qwen3.8-27b": local}
    browse._installed_repos = {"qwen3.8-27b"}
    browse._usable_repos = {"qwen3.8-27b"}
    browse._last_result = ModelSearch((official,), 1, 1, 30)
    browse._source_filter = "all"
    browse._filter_key = "all"
    browse._group_same = True
    browse._render_results()

    repos = {item.repo for item in browse._result_items}
    assert repos == {"Qwen/Qwen3.8-27B", "Qwen3.8-27B"}
    assert browse.models.count() == 2
    # 官方行不能被折进本地行（否则「同名 2」只留本地那一条）。
    rows = {}
    for index in range(browse.models.count()):
        row = browse.models.itemWidget(browse.models.item(index))
        rows[row.repo] = row
    assert "仅本地" in {tag.text() for tag in rows["Qwen3.8-27B"].findChildren(QLabel, "StatusTag")}
    assert "仅本地" not in {tag.text() for tag in rows["Qwen/Qwen3.8-27B"].findChildren(QLabel, "StatusTag")}


def test_convert_can_be_stopped_from_status_row(window: Any) -> None:
    """转换（含首次下载 llama.cpp 源码）可从底部状态行停止。"""
    import threading

    window.show_page("models")
    browse = window.page("models").browse
    browse._converting = True
    browse._convert_cancel = threading.Event()
    browse.stop_download_button.setText("停止转换")
    browse.stop_download_button.setEnabled(True)
    try:
        browse.stop_download()
        assert browse._convert_cancel.is_set()
        assert not browse.stop_download_button.isEnabled()
        assert browse.download_text.text() == "正在停止转换…"
    finally:
        browse._converting = False
        browse._convert_cancel = None


def test_settings_shows_engine_source_status(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """设置页「推理引擎（llama.cpp）」卡片显示当前引擎是否带有转换源码。"""
    from fcmd.apps.llm import _llmw_convert

    window.show_page("settings")
    page = window.page("settings")
    ready = _llmw_convert.ConvertSourceStatus(
        ready=True, tag="b9", path=Path("/engines/b9/llama.cpp/convert_hf_to_gguf.py")
    )
    monkeypatch.setattr(_llmw_convert, "convert_source_status", lambda _tag=None: ready)
    page._refresh_engine_source()
    assert page.engine_source_tag.text() == "已包含源码"
    assert page.engine_source_tag.property("tone") == "success"
    assert "convert_hf_to_gguf.py" in page.engine_source_tag.toolTip()
    # 模型转换卡片不再重复显示源码状态。
    assert not hasattr(page, "convert_source_tag")


def test_convert_preparation_shows_busy_status(window: Any) -> None:
    """转换准备阶段用忙碌条 + 状态行反馈（探测解释器 / 下载源码 / clone），不再是静止的 0%。"""
    window.show_page("models")
    browse = window.page("models").browse
    browse._convert_active_repo = "Qwen/WeMM"
    browse.download_bar.setRange(0, 0)
    browse._on_convert_progress("正在下载 llama.cpp 源码包（1/5）…")
    assert "llama.cpp 源码包" in browse.download_text.text()
    browse._on_convert_progress(42)
    assert browse.download_bar.minimum() == 0
    assert browse.download_bar.maximum() == 100
    assert browse.download_bar.value() == 42


def test_name_search_bypasses_local_fit(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """名称搜索是明确意图：停在「适合本机」也要显示官方大仓库的命中结果。"""
    from fcmd.apps.llm.llmdown import ModelSearch, ModelSummary

    window.show_page("models")
    browse = window.page("models").browse
    official = ModelSummary(
        repo="Qwen/Qwen2.5-72B-Instruct",
        name="Qwen2.5-72B-Instruct",
        tags=("text-generation",),
        storage_size=135 * 1024**3,
    )
    browse._last_result = ModelSearch((official,), 1, 1, 30)
    browse._source_filter = "online"
    browse._filter_key = "local"
    monkeypatch.setattr(browse, "_local_capacity", lambda: 1024**3)

    browse._name_filter = ""
    browse._render_results()
    assert official.repo not in [item.repo for item in browse._result_items]

    browse._name_filter = "Qwen"
    browse._render_results()
    assert [item.repo for item in browse._result_items] == [official.repo]


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
    assert window.status_text() == "已删除当前对话"
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
    assert window.status_text() == "已清空全部对话"

    chat._conversations = [_Conversation(title="一"), _Conversation(title="二")]
    chat._active = 0
    chat._refresh_history()
    chat._running = True
    try:
        chat.delete_chat_button.click()
        chat.clear_chats_button.click()
        assert len(chat._conversations) == 2
        assert "停止后才能清理" in window.status_text()
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


def _write_gguf_header(path: Path, file_type: int) -> None:
    """写一个最小 GGUF 头（只带 general.architecture / general.file_type），供精度快路径识别。"""
    import struct

    def key_value(key: str, type_id: int, payload: bytes) -> bytes:
        encoded = key.encode()
        return struct.pack("<Q", len(encoded)) + encoded + struct.pack("<I", type_id) + payload

    def text_value(value: str) -> bytes:
        encoded = value.encode()
        return struct.pack("<Q", len(encoded)) + encoded

    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = bytearray(b"GGUF") + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 2)
    buffer += key_value("general.architecture", 8, text_value("qwen2"))
    buffer += key_value("general.file_type", 4, struct.pack("<I", file_type))
    path.write_bytes(bytes(buffer))


def test_scan_installed_reads_converted_products(window: Any, tmp_path: Path) -> None:
    """转换产物落在 converted/<名称>/ 下：f16 可量化、量化变体可在下拉里启动。"""
    window.show_page("models")
    browse = window.page("models").browse
    models_root = tmp_path / "models"
    source = models_root / "Qwen" / "Qwen2.5-0.5B-Instruct"
    source.mkdir(parents=True)
    (source / "config.json").write_text("{}", encoding="utf-8")
    (source / "model.safetensors").write_bytes(b"x" * 16)
    products = models_root / "converted" / source.name
    f16 = products / f"{source.name}.gguf"
    _write_gguf_header(f16, 1)
    quantized = products / f"{source.name}-Q4_K_M.gguf"
    _write_gguf_header(quantized, 15)

    snapshot = browse._scan_installed()

    key = "qwen/qwen2.5-0.5b-instruct"
    assert snapshot.convertible[key] == (source, f16, True)
    assert snapshot.quant_candidates[key] == (f16,)
    assert snapshot.quantized[key] == (("Q4_K_M", quantized),)
    assert key in snapshot.usable


def test_converted_model_row_offers_serve_button(window: Any, tmp_path: Path) -> None:
    """已转换但未量化的模型也要有「启动服务」，点它启动转换产物（f16 GGUF）。"""
    from fcmd.apps.llm.llmdown import ModelSearch, ModelSummary

    window.show_page("models")
    browse = window.page("models").browse
    key = "qwen/qwen2.5-0.5b-instruct"
    source = tmp_path / "Qwen2.5-0.5B-Instruct"
    source.mkdir()
    output = tmp_path / "converted" / source.name / f"{source.name}.gguf"
    output.parent.mkdir(parents=True)
    _write_gguf_header(output, 1)
    summary = ModelSummary(repo="Qwen/Qwen2.5-0.5B-Instruct", storage_size=1024**3)
    browse._installed_models = {key: summary}
    browse._installed_repos = {key}
    browse._usable_repos = {key}
    browse._convertible = {key: (source, output, True)}
    browse._last_result = ModelSearch(items=(summary,), total=1)
    browse._source_filter = "installed"
    browse._filter_key = "all"
    browse._name_filter = ""
    browse._render_results()

    row = browse.models.itemWidget(browse.models.item(0))
    assert row.start_button is not None
    assert row.serve_path == output
    assert "已转换" in {tag.text() for tag in row.findChildren(QLabel, "StatusTag")}

    served: list[tuple[Path, str]] = []
    browse._request_serve = lambda path, repo_key="": served.append((path, repo_key))  # type: ignore[method-assign]
    row.start_button.click()
    assert served == [(output, key)]


def test_service_watch_only_notifies_when_services_change(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """服务看护：外部启停 / 进程退出后自动刷新，但快照没变时不反复通知各页。"""
    from fcmd.apps.llm import _llmw_qt_state as qt_state
    from fcmd.apps.llm.llmbase import ServeState

    state = window.state
    state.refresh_services()
    seen: list[object] = []
    state.servicesChanged.connect(seen.append)

    def serve_state(name: str, pid: int) -> ServeState:
        return ServeState(
            name=name,
            pid=pid,
            create_time=1.0,
            host="127.0.0.1",
            port=8080,
            model="D:/models/demo.gguf",
            profile="balanced",
            context=4096,
            engine="",
            alias=name,
            api_key="",
            log="",
            started_at="",
        )

    running = serve_state("demo", 4242)
    monkeypatch.setattr(qt_state, "service_snapshot", lambda: ([running], [running]))
    state.watch_services()
    assert len(seen) == 1
    assert state.is_running(Path("D:/models/demo.gguf"))

    # 快照没变：不重复发信号（否则每秒采样都会重建列表 / 重填下拉）。
    state.watch_services()
    assert len(seen) == 1

    # 进程退出（登记项还在，存活列表为空）：立刻反映成「未运行」。
    monkeypatch.setattr(qt_state, "service_snapshot", lambda: ([running], []))
    state.watch_services()
    assert len(seen) == 2
    assert not state.is_running(Path("D:/models/demo.gguf"))


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


def test_import_source_archive_sets_convert_script(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导入压缩包识别到 llama.cpp 源码时，复制到引擎根并设为转换源码，不安装成引擎。"""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    window.show_page("settings")
    page = window.page("settings")
    archive = tmp_path / "llama.cpp-source.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("llama.cpp-master/convert_hf_to_gguf.py", "# source")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_a, **_k: ([str(archive)], ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)

    page._import_engine_archive()
    script = Path(page.convert_script.text())
    assert script.is_file()
    assert script.parent.name == "llama.cpp"
    assert script.parent.parent.name == "engines"
    assert script.read_bytes() == archive.read_bytes()
    assert page.engine_source_tag.text() == "已指定源码包"
    assert page.engine_source_tag.property("tone") == "success"


def test_convert_engine_tag_is_editable_combo(window: Any) -> None:
    """「引擎 tag」是可编辑下拉框：既能选已装引擎，也能填自定义 tag。"""
    from PySide6.QtWidgets import QComboBox

    window.show_page("settings")
    page = window.page("settings")
    assert isinstance(page.convert_tag, QComboBox)
    assert page.convert_tag.isEditable()
    page.convert_tag.setCurrentText("b9-custom")
    page._save_convert()
    assert window.state.settings.convert["tag"] == "b9-custom"


def test_convert_architectures_button_lists_supported(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """「查看支持的架构…」列出当前脚本注册的架构，便于判断模型能否转换。"""
    from fcmd.apps.llm import llmconvert

    window.show_page("settings")
    page = window.page("settings")
    messages: list[str] = []
    monkeypatch.setattr(
        llmconvert, "supported_architectures", lambda *_a, **_k: ("LlamaForCausalLM", "GPT2LMHeadModel")
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, text, *_a, **_k: messages.append(str(text)),
    )
    page.convert_arch_button.click()
    assert messages and "LlamaForCausalLM" in messages[0]
    assert "2 个架构" in messages[0]


def test_settings_engine_panel_is_compact(window: Any) -> None:
    """引擎独立成卡片且位于存储下方：导入按钮在目录行、刷新在右上角。"""
    from fcmd.gui.widgets import Card

    def card_of(widget: Any) -> Card:
        card = widget.parent()
        while card is not None and not isinstance(card, Card):
            card = card.parent()
        assert card is not None
        return card

    window.show_page("settings")
    page = window.page("settings")
    storage = card_of(page.storage_grid)
    engine = card_of(page.engines_path)
    assert storage.title_label.text() == "存储"
    assert engine.title_label.text() == "推理引擎（llama.cpp）"
    assert engine is not storage
    assert not storage.isAncestorOf(page.engines_path)
    assert [card.title_label.text() for card in page.findChildren(Card)] == [
        "存储",
        "推理引擎（llama.cpp）",
        "画图/视频/音频引擎(sd.cpp)",
        "下载",
        "模型转换",
    ]
    assert page.refresh_engine_button.text() == "刷新"
    assert page.refresh_engine_button.parent() is engine
    assert page.manage_engines_button.text() == "管理…"
    assert page.manage_engines_button.parent() is engine
    assert page.engine_source_tag.parent() is engine
    title_row = engine.title_row
    assert title_row is not None
    assert page.engine_source_tag in [title_row.itemAt(index).widget() for index in range(title_row.count())]
    assert page.engines_path.browse.text() == "更改"
    assert page.download_engine_button.text() == "下载引擎"
    assert page.import_engine_archive_button.text() == "导入压缩包"
    assert not hasattr(page, "import_engine_dir_button")
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


def test_settings_paint_engine_card_matches_llama_layout(window: Any) -> None:
    """sd.cpp 引擎卡对齐推理引擎：刷新在标题行、目录行动作、单行列表 + 空态提示。"""
    window.show_page("settings")
    from fcmd.apps.llm._llmw_qt_engine_card import EngineCard

    page = window.page("settings")
    card = page.paint_engine_card
    # 与推理引擎共用同一个 EngineCard 类，只是配置不同。
    assert isinstance(card, EngineCard)
    assert isinstance(page.engine_card, EngineCard)
    assert card.config.list_object_name == "PaintEngineList"
    assert page.engine_card.config.list_object_name == "EngineList"
    assert card.title() == "画图/视频/音频引擎(sd.cpp)"
    assert card.refresh_button.text() == "刷新"
    assert card.refresh_button.parent() is card
    assert card.root_widget.browse.text() == "更改"
    assert not hasattr(card, "open_button") or card.open_button is None
    assert card.install_button.text() == "下载引擎"
    assert card.extra_buttons[0].text() == "导入压缩包"
    assert card.manage_button is not None and card.manage_button.text() == "管理…"
    assert card.engines_list.objectName() == "PaintEngineList"
    assert card.engines_empty.text().startswith("还没有已安装 sd.cpp 引擎")
    # 旧的状态胶囊 / 自绘行布局已移除，改为与推理引擎一致的列表行。
    assert not hasattr(card, "engine_tag")
    assert not hasattr(card, "rows")


def test_paint_engine_manage_dialog_uses_sd_admin(window: Any) -> None:
    """sd.cpp 卡片提供与管理入口相同的对话框，只隐藏不适用的「清理源码」。"""
    window.show_page("settings")
    card = window.page("settings").paint_engine_card
    card.manage_button.click()
    dialog = card._manage_dialog
    try:
        assert dialog.windowTitle() == "管理画图引擎"
        assert dialog.cleanup_source_button.isHidden()
        assert dialog.cleanup_all_button.isHidden()
        assert not dialog.cleanup_cache_button.isHidden()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_settings_single_sd_engine_card_is_shared(window: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """画图 / 视频 / 音频共用一张 sd.cpp 引擎卡与一个默认 tag。"""
    from fcmd.apps.llm import _llmconfig, _llmpaint_engine

    root = tmp_path / "engines" / "sd.cpp"
    for tag in ("sd-a", "sd-b"):
        directory = root / tag
        directory.mkdir(parents=True)
        (directory / "sd-server.exe").write_bytes(b"exe")
    _llmpaint_engine.use_sd_engine("sd-a")

    window.show_page("settings")
    page = window.page("settings")
    page.refresh_engine_cards()
    card = page.paint_engine_card
    assert card.engines_list.count() == 2
    assert _llmconfig.paint_engines_root() == root
    # 按用途拆分的卡片与覆盖 key 都已移除
    for name in ("video_engine_card", "audio_engine_card"):
        assert not hasattr(page, name), name
    assert not hasattr(_llmpaint_engine, "usage_engine_tag")
    assert not hasattr(_llmpaint_engine, "use_sd_engine_for")

    def row_for(tag: str) -> Any:
        for index in range(card.engines_list.count()):
            row = card.engines_list.itemWidget(card.engines_list.item(index))
            if row.engine.tag == tag:
                return row
        raise AssertionError(tag)

    # 「设为默认」写全局 current.json，三种用途一起切过去
    row_for("sd-b").use_button.click()
    assert _llmpaint_engine.current_sd_tag() == "sd-b"
    assert row_for("sd-b").use_button is None
    assert row_for("sd-a").use_button is not None
    assert _llmconfig.get_option("video_engine") is None
    assert _llmconfig.get_option("audio_engine") is None


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
    assert not hasattr(other_row, "backup_button")
    assert not hasattr(other_row, "remove_button")
    other_widgets = [other_row.layout().itemAt(pos).widget() for pos in range(other_row.layout().count())]
    assert other_widgets[-3] is other_row.use_button
    assert other_widgets[-2].objectName() == "StatusTag"
    assert "适配" in other_widgets[-2].text()
    assert other_widgets[-1].text() == "CPU"
    assert current_row.use_button is None
    current_labels = [label.objectName() for label in current_row.findChildren(QLabel)]
    assert "EngineItemName" in current_labels
    assert "StatusTag" in current_labels
    assert "OwnerTag" not in current_labels
    current_widgets = [current_row.layout().itemAt(pos).widget() for pos in range(current_row.layout().count())]
    assert current_widgets[-3].text() == "默认"
    assert "适配" in current_widgets[-2].text()
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


def test_backup_default_path_uses_engine_root(tmp_path: Path) -> None:
    """备份默认路径取引擎根下的 backups/（回归：engines_root 属于 llmenv）。"""
    from fcmd.apps.llm._llmw_qt_engine_manage import backup_default_path

    path = backup_default_path("b9", tmp_path, stamp="20260101")
    assert path.parent == tmp_path / "engines" / "llama.cpp" / "backups"
    assert path.name == "llama-b9-20260101.zip"


def test_engine_manage_dialog_lists_and_scopes_actions(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """管理入口集中备份 / 删除 / 清理；默认引擎不可删除。"""
    from fcmd.apps.llm import llmengine
    from fcmd.apps.llm._llmw_qt_engine_manage import EngineManageDialog
    from fcmd.apps.llm.llmbase import EngineInfo

    engines = [
        EngineInfo("b2", Path("b2") / "llama-server.exe", str(Path("source"))),
        EngineInfo("b1", Path("b1") / "llama-server.exe", "current"),
    ]
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: engines)
    dialog = EngineManageDialog()
    try:
        assert dialog.list.count() == 2
        dialog.list.setCurrentRow(0)  # b2 非默认
        assert dialog.delete_button.isEnabled()
        assert dialog.backup_button.isEnabled()
        dialog.list.setCurrentRow(1)  # b1 默认
        assert not dialog.delete_button.isEnabled()
        assert "切换到其它引擎" in dialog.delete_button.toolTip()
        dialog._set_busy(True)
        assert not dialog.cleanup_all_button.isEnabled()
        dialog._set_busy(False)
        assert dialog.cleanup_all_button.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_settings_opens_engine_manager(window: Any) -> None:
    """卡片标题行「管理…」打开引擎管理对话框。"""
    window.show_page("settings")
    page = window.page("settings")
    page.manage_engines_button.click()
    dialog = page._engine_manager_dialog
    assert dialog is not None
    assert dialog.isVisible() or dialog.isHidden() is False
    dialog.close()


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


def test_chat_config_button_opens_separate_dialog(window: Any) -> None:
    """模型 / 生成参数收进独立配置窗口，不占用对话页正文与对话框区域。"""
    window.show_page("chat")
    chat = window.page("chat")
    assert chat.new_chat_button.objectName() == "SidebarAction"
    assert chat.config_button.icon().isNull() is False
    # 对话页内不再出现配置表单，配置控件都在独立窗口里。
    assert chat.config_dialog.isAncestorOf(chat.url_edit)
    assert chat.config_dialog.isAncestorOf(chat.system_edit)
    assert not chat.isAncestorOf(chat.url_edit)
    chat.config_button.click()
    assert chat.config_dialog.isVisible()
    chat.config_dialog.close()
    assert not chat.config_dialog.isVisible()


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
    # 下拉与主按钮同高，且只比常规按钮略高（不再写死 40px）。
    assert page.model_combo.minimumHeight() == page.model_action_button.minimumHeight()
    normal = QPushButton("普通按钮")
    normal.ensurePolished()
    assert 0 < normal.minimumHeight() < page.model_combo.minimumHeight()
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


def test_run_page_model_combo_keeps_full_long_name(window: Any) -> None:
    """模型下拉项保存完整文件名：宽度由控件按实际空间省略，不再预截断到 30 字。"""
    from fcmd.apps.llm.llmbase import ModelEntry

    root = Path("/models")
    name = "Qwen3-Coder-30B-A3B-Instruct-Q5_K_M-very-long-model-name.gguf"
    model = ModelEntry(root=root, path=root / name, size=1024**3)
    window.state.models = [model]
    window.state.models_root = root
    window.run_page.on_models_changed()
    text = window.run_page.model_combo.itemText(0)
    assert name in text
    assert "…" not in text


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
    # 预览构建已合并到下一轮事件循环（避免连续编辑重复扫模型），这里手动排空。
    QApplication.processEvents()
    command = run.command_preview.toPlainText()
    assert "demo-Q4_K_XL.gguf" in command
    assert "--model" in command or "-m" in command
    run._fill_params()
    custom = run.extra_args_edit.toPlainText()
    assert custom.startswith("-ngl 99")
    assert "demo-Q4_K_XL.gguf" not in custom
    assert "--alias" in custom and "--host 127.0.0.1" in custom
    # 与预览一致：填入完整参数后不会在命令里重复追加一遍
    QApplication.processEvents()
    assert run.command_preview.toPlainText().count("-ngl") == 1


def test_run_page_form_overrides_filled_custom_args(window: Any, tmp_path: Path) -> None:
    """「填入当前启动参数」后再改监听地址，旧快照不能覆盖表单值。"""
    from fcmd.apps.llm.llmbase import ModelEntry

    model_file = tmp_path / "demo-Q4_K_XL.gguf"
    model_file.write_bytes(b"\0" * 64)
    window.state.models = [ModelEntry(root=tmp_path, path=model_file, size=64, profiles=("balanced",), tags=("对话",))]
    window.state.models_root = tmp_path
    run = window.run_page
    run.on_models_changed()
    run._fill_params()
    assert "--host 127.0.0.1" in run.extra_args_edit.toPlainText()
    run.host_edit.setText("0.0.0.0")
    QApplication.processEvents()
    command = run.command_preview.toPlainText()
    assert "--host 0.0.0.0" in command
    assert "--host 127.0.0.1" not in command
    assert command.count("--port") == 1
    assert run.build_plan().host == "0.0.0.0"


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
    dialog = window.state._log_dialog
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
    from fcmd.apps.llm._llmw_qt_overview import PaintSample

    overview = window.overview
    assert not hasattr(overview, "_gpu_gauge")
    assert overview._recommend_list.parent().title() == "智能推荐"
    # 负载块不再支持展开：标题后直接用小字提示关键信息。
    for name in ("_gpu_toggle", "_cpu_toggle", "_mem_toggle", "_gpu_metrics", "_cpu_metrics", "_mem_metrics"):
        assert not hasattr(overview, name)
    assert overview._gpu_hint.fullText() == "等待采样…"
    assert overview._vram_hint.fullText() == "等待采样…"
    assert overview._cpu_hint.fullText() == "等待采样…"
    assert overview._cpu_proc_hint.fullText() == "等待采样…"
    assert overview._mem_hint.fullText() == "等待采样…"
    assert overview._swap_hint.fullText() == "等待采样…"
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
    overview._apply_sample((sample, (), PaintSample(None, False)))

    assert overview._gpu_percent.text() == "73 %"
    assert overview._cpu_percent.text() == "41 %"
    assert overview._mem_percent.text() == "41 %"
    assert overview._gpu_bar.percent() == 73.0
    # 显存独立一条格图与利用率并列，不再挤在标题后的小字里。
    assert overview._vram_bar.percent() == pytest.approx(18000 / 24576 * 100)
    assert overview._vram_hint.fullText() == "显存 17.58 GB / 24.00 GB（73%）"
    assert overview._cpu_bar.percent() == 41.0
    assert overview._mem_bar.percent() == 41.0
    # 三块统一为「主格图 + 副条」：CPU 副条是服务进程 CPU，内存副条是交换分区。
    assert overview._cpu_proc_bar.percent() == 12.5
    assert overview._swap_bar.percent() == pytest.approx(25.0)
    assert overview._gpu_percent.property("band") == "warning"
    assert overview._cpu_percent.property("band") == "success"
    assert "显存" not in overview._gpu_hint.fullText()
    assert "210 / 450 W" in overview._gpu_hint.fullText()
    assert overview._cpu_hint.fullText() == "频率 3600 MHz"
    assert overview._cpu_proc_hint.fullText() == "服务 12.5 %"
    assert overview._mem_hint.fullText().startswith("13.00 GB / 32.00 GB")
    assert overview._swap_hint.fullText() == "交换 2.00 GB / 8.00 GB（25%）"

    # 运行中的模型：每个服务一行，实时性能绑定到该行（不再有全局实时性能面板）。
    from fcmd.apps.llm._llmw_qt_overview import ServiceSample
    from fcmd.apps.llm.llmbase import ServeState

    assert not hasattr(overview, "_performance_badge")
    assert overview._running_badge.text() == "未运行"
    assert not overview._running_empty.isHidden()
    state = ServeState(
        name="qwen",
        pid=1234,
        create_time=0.0,
        host="127.0.0.1",
        port=8080,
        model="/models/qwen.gguf",
        profile="balanced",
        context=65536,
        engine="b1",
        alias="qwen",
        api_key="",
        log="",
        started_at="",
    )
    overview._on_services_changed([state])
    assert overview._running_badge.text() == "1 个运行中"
    assert overview._running_empty.isHidden()
    row = overview._running_rows["qwen"]
    assert row.rate_label.objectName() == "RateValue"
    metrics = llmserve.ServeMetrics(prompt_tokens=9302, predicted_tokens=12045, processing=1, deferred=0, kv_usage=0.18)
    overview._apply_sample((sample, (ServiceSample("qwen", metrics, None),), PaintSample(None, False)))
    assert row.rate_label.text() == "0.0"
    assert "预填充 0.0 tok/s" in row.metrics_label.fullText()
    assert "处理中 1" in row.metrics_label.fullText()
    assert "KV 18.0%" in row.metrics_label.fullText()
    assert row.status_tag.text() == "监控中"
    assert row.status_tag.tone() == "success"

    # 采样失败：同一行显示失败原因，不把旧速率留着。
    overview._apply_sample(
        (LoadSample(cpu=5.0), (ServiceSample("qwen", None, "connection refused"),), PaintSample(None, False))
    )
    assert row.status_tag.text() == "采样失败"
    assert row.status_tag.tone() == "danger"
    assert "connection refused" in row.metrics_label.fullText()

    # 画图（sd.cpp）模型也显示在「运行中的模型」：没有 token 指标，用生成进度表示。
    from fcmd.apps.llm._llmpaint_server import PaintState

    paint_state = PaintState(
        pid=4321,
        create_time=0.0,
        host="127.0.0.1",
        port=1234,
        engine_tag="master-1094",
        exe="sd-server",
        model="/models/z_image_turbo.gguf",
        components={},
        fingerprint=(),
        log="sd-server.log",
        started_at="",
    )
    # 服务停止后行销毁，回到空态提示。
    overview._on_services_changed([])
    assert overview._running_badge.text() == "未运行"
    assert not overview._running_empty.isHidden()
    assert overview._running_rows == {}

    overview._apply_sample((sample, (), PaintSample(paint_state, True, 0.42)))
    assert overview._paint_row is not None
    assert overview._paint_row.name_label.fullText() == "z_image_turbo.gguf"
    assert overview._paint_row.rate_label.isHidden()
    assert "生成进度 42%" in overview._paint_row.metrics_label.fullText()
    assert overview._running_badge.text() == "1 个运行中"
    # 画图服务退出：行移除，回到空态。
    overview._apply_sample((sample, (), PaintSample(None, False)))
    assert overview._paint_row is None
    assert overview._running_badge.text() == "未运行"

    # 无 GPU / 无服务 / 无交换：副条清空并给出对应提示，不留上半轮的旧值。
    overview._apply_sample((LoadSample(cpu=5.0), (), PaintSample(None, False)))
    assert overview._vram_bar.percent() is None
    assert overview._vram_hint.fullText() == "未检测到 GPU（CPU 推理）"
    assert overview._gpu_hint.fullText() == ""
    assert overview._cpu_proc_bar.percent() is None
    assert overview._cpu_proc_hint.fullText() == "服务 -（未运行）"
    assert overview._swap_bar.percent() is None
    assert overview._swap_hint.fullText() == "未配置交换分区"


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
    """下载默认整仓全部文件；弹窗里仍可只选一个量化。"""
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
    # 没有量化选项时直接整仓下载。
    page._choose_quant("org/demo", ())
    assert calls[-1] == ("org/demo", ())

    from PySide6.QtWidgets import QInputDialog

    labels_seen: list[list[str]] = []

    def _default_item(
        _parent: Any, _title: str, _label: str, labels: list[str], index: int = 0, _editable: bool = False
    ) -> tuple[str, bool]:
        labels_seen.append(list(labels))
        return labels[index], True

    monkeypatch.setattr(QInputDialog, "getItem", _default_item)
    # 只有一个量化时也弹窗，默认选中「全部文件」。
    page._choose_quant("org/demo", (ud,))
    assert labels_seen[-1][0].startswith("全部文件")
    assert calls[-1] == ("org/demo", ())

    # 显式选择具体量化时只下该量化（含组件）。
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args, **_kwargs: (q5.display, True))
    page._choose_quant("org/demo", (ud, q5))
    assert calls[-1] == ("org/demo", q5.files)

    page.download_status.setVisible(True)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args, **_kwargs: ("", False))
    page._choose_quant("org/demo", (ud, q5))
    assert page.download_status.isHidden()
    assert page.download_text.text() == ""


def test_model_download_parallel_and_queue(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """多模型并行：达到并发上限后排队，完成一个自动启动下一个，同仓库不重复入队。"""
    from fcmd.apps.llm import _llmw_qt_center as center

    window.show_page("models")
    page = window.page("models").browse
    settings = dict(page.state.settings.download or {})
    settings.update({"source": "auto", "workers": 8, "parallel": 2, "verify": True, "include": "", "exclude": ""})
    page.state.settings.download = settings
    page._jobs.clear()
    page._queued.clear()
    started: list[str] = []

    def _fake_start(repo: str, _files: tuple[Any, ...], _settings: Any) -> None:
        started.append(repo)
        page._jobs[repo.casefold()] = center._DownloadJob(
            repo,
            Path("D:/tmp") / repo,
            center.ProgressBridge(page),
            center.SpeedMeter(),
        )

    monkeypatch.setattr(page, "_start_job", _fake_start)
    page.start_download("org/a", files=())
    page.start_download("org/b", files=())
    page.start_download("org/c", files=())
    assert started == ["org/a", "org/b"]
    assert [repo for repo, _files in page._queued] == ["org/c"]
    page.start_download("org/a", files=())
    assert started == ["org/a", "org/b"]
    page._jobs.pop("org/a")
    page._drain_queue()
    assert started == ["org/a", "org/b", "org/c"]
    assert page._queued == []
    page._jobs.clear()


def test_start_job_splits_parallel_workers(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """并行下载时按并发上限分摊每个模型的连接数，避免总连接数翻倍。"""
    from fcmd.apps.llm import _llmw_qt_center as center
    from fcmd.apps.llm._llmw_models import DownloadGuiSettings

    window.show_page("models")
    page = window.page("models").browse
    captured: dict[str, Any] = {}
    monkeypatch.setattr(center, "submit", lambda *args, **_kwargs: captured.setdefault("args", args))
    page._jobs.clear()
    page._start_job("org/a", (), DownloadGuiSettings(source="auto", workers=8, parallel=2))
    assert captured["args"][6] == 4  # fn/repo/dest/source/include/exclude/workers
    assert "org/a" in page._jobs
    page._jobs.clear()


def test_local_only_model_skips_online_detail(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """仅本地模型展开详情时不发详情 / 清单请求，只提示仅本地。"""
    from fcmd.apps.llm import _llmw_qt_center as center

    window.show_page("models")
    page = window.page("models").browse
    submitted: list[tuple[Any, ...]] = []
    monkeypatch.setattr(center, "submit", lambda *args, **_kwargs: submitted.append(args))
    page.load_repo("Qwen3.8-27B", online=False)
    assert submitted == []
    assert page.current_info is None
    assert "仅本地" in page.detail_view.error.text()

    page.load_repo("Qwen/demo")
    assert submitted


def test_refresh_installed_requeues_after_inflight_scan(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描途中下载完成：结束后续扫一次，确保行切到已下载状态。"""
    from fcmd.apps.llm import _llmw_qt_center as center

    window.show_page("models")
    page = window.page("models").browse
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(center, "submit", lambda *args, **_kwargs: calls.append(args))
    monkeypatch.setattr(page, "_begin_loading", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(page, "_end_loading", lambda *_args, **_kwargs: None)
    page._scanning_installed = True
    page._rescan_installed = False
    page.refresh_installed()
    assert page._rescan_installed is True
    assert calls == []
    page._scanning_installed = False
    page._installed_finished()
    assert page._scanning_installed is True
    assert page._rescan_installed is False
    assert len(calls) == 1


def test_overview_scrolls_only_recommendation_list(window: Any) -> None:
    """概览不再整页滚动：页面高度不足时只由推荐列表内部滚动。"""
    from fcmd.gui.widgets import PageScroll

    window.show_page("overview")
    overview = window.overview
    assert overview.findChild(PageScroll) is None
    assert not overview._recommend_list.parentWidget().isAncestorOf(overview._apply_button)
    assert overview._apply_button.parentWidget() is overview


def test_pages_use_scroll_container(window: Any) -> None:
    """长页面必须放进滚动容器：窗口变矮时滚动而不是压扁控件（模型列表自身滚动）。"""
    from fcmd.gui.widgets import PageScroll

    # 对话页是 splitter + 正文阅读区，不走整页滚动容器。
    for key in ("run", "settings"):
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
    """对话页输入区与历史栏功能键尺寸统一，发送仅图标。"""
    window.show_page("chat")
    chat = window.page("chat")
    assert chat.send_button.objectName() == "ChatSend"
    assert chat.send_button.text() == ""
    assert chat.send_button.icon().isNull() is False
    # 无边框风格：图标用 accent 色表达主操作，不靠实心背景块。
    assert chat.send_button.property("fcmdIconTone") == "accent"
    assert chat.send_button.size().width() == chat.send_button.size().height() == 34

    actions = [chat.copy_chat_button, chat.delete_chat_button, chat.clear_chats_button]
    tops = {widget.mapTo(chat, widget.rect().topLeft()).y() for widget in actions}
    assert len(tops) == 1
    assert all(widget.size().width() == widget.size().height() == 30 for widget in actions)
    assert all(widget.icon().isNull() is False for widget in actions)
    # 无边框操作条：三个动作等宽铺满，hover 才显底色，不与正文抢视线。
    assert chat.history_actions.objectName() == "ChatSidebarActions"
    assert all(widget.parent() is chat.history_actions for widget in actions)
    widths = {widget.width() for widget in actions}
    assert len(widths) == 1
    assert chat.new_chat_button.text() == "新对话"
    assert chat.new_chat_button.icon().isNull() is False
    # 「新对话」与历史条目同宽、同圆角风格：只是底色更实、边框更深。
    item = chat.history_list.item(0)
    assert item is not None
    item_width = chat.history_list.visualItemRect(item).width()
    assert chat.new_chat_button.width() == item_width


def test_chat_send_stop_toggle_uses_one_button(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """发送 / 停止合并：空闲点击发送，生成中点击停止，图标同步切换。"""
    window.show_page("chat")
    chat = window.page("chat")
    calls: list[str] = []
    monkeypatch.setattr(chat, "send", lambda: calls.append("send"))
    monkeypatch.setattr(chat, "stop", lambda: calls.append("stop"))
    chat.send_button.click()
    assert calls == ["send"]
    assert chat.send_button.property("fcmdIconName") == "send"
    chat._running = True
    chat._sync_send_button()
    assert chat.send_button.objectName() == "ChatStop"
    assert chat.send_button.property("fcmdIconName") == "stop"
    chat.send_button.click()
    assert calls[-1] == "stop"
    chat._running = False
    chat._sync_send_button()
    assert chat.send_button.objectName() == "ChatSend"
    assert chat.send_button.property("fcmdIconName") == "send"


def test_chat_copy_action_copies_transcript(window: Any) -> None:
    """历史栏底部的复制键把当前对话正文写入剪贴板。"""
    from PySide6.QtWidgets import QApplication

    window.show_page("chat")
    chat = window.page("chat")
    chat._append_text("你：第一段\n", "user")
    chat.copy_chat_button.click()
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    assert "第一段" in clipboard.text()
    assert window.status_text() == "已复制当前对话内容"


def test_chat_sidebar_width_is_resizable_and_persisted(window: Any) -> None:
    """对话侧边栏不再固定宽度：可拖动展开 / 收窄，关闭时记住宽度。"""
    from PySide6.QtWidgets import QApplication

    window.show_page("chat")
    chat = window.page("chat")
    splitter = chat._splitter
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert splitter.objectName() == "QuietSplitter"
    assert splitter.handleWidth() == 4
    assert chat.sidebar.minimumWidth() == 120
    assert chat.sidebar.maximumWidth() == 480
    assert chat._sidebar_width == 120
    assert chat.sidebar.width() == 120
    assert chat.history_list.textElideMode() == Qt.TextElideMode.ElideRight
    assert chat.history_list.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    window.show()
    QApplication.processEvents()

    splitter.setSizes([320, 600])
    QApplication.processEvents()
    expected = min(max(splitter.sizes()[0], 180), 480)
    assert expected > 300
    chat.close()
    assert chat.state.settings.chat_sidebar_width == expected


def test_chat_sidebar_can_collapse_and_restore(window: Any) -> None:
    """对话列表可收起：正文占满宽度，只留小展开键，状态写入设置。"""
    from PySide6.QtWidgets import QApplication

    window.show_page("chat")
    window.show()
    QApplication.processEvents()
    chat = window.page("chat")
    assert not chat.sidebar.isHidden()
    assert chat.sidebar_toggle.width() == 28
    chat.sidebar_toggle.click()
    QApplication.processEvents()
    assert chat.sidebar.isHidden()
    assert chat.state.settings.chat_sidebar_collapsed is True
    assert chat.sidebar_toggle.property("fcmdIconName") == "chevron-right"
    chat.sidebar_toggle.click()
    QApplication.processEvents()
    assert not chat.sidebar.isHidden()
    assert chat.state.settings.chat_sidebar_collapsed is False
    assert chat.sidebar_toggle.property("fcmdIconName") == "chevron-left"


def test_chat_sidebar_toggle_shares_page_title_row(window: Any) -> None:
    """对话列表收起键挂在页标题同一行：正文不再为它单独让出一行高度。"""
    from PySide6.QtWidgets import QApplication

    window.show()
    window.show_page("chat")
    QApplication.processEvents()
    chat = window.page("chat")
    toggle = chat.sidebar_toggle
    # 控件归主窗口标题行所有，不再属于对话页正文布局。
    assert toggle.window() is window
    assert toggle.parent() is not chat
    assert window.header_actions.indexOf(toggle) >= 0
    toggle_top = toggle.mapTo(window, toggle.rect().topLeft()).y()
    title_top = window.page_title.mapTo(window, window.page_title.rect().topLeft()).y()
    assert abs(toggle_top - title_top) <= 2
    # 页标题下的描述已移除：正文紧跟标题行，中间没有额外的说明 / 动作行。
    assert window.page_hint.isVisible() is False
    title_bottom = window.page_title.mapTo(window, window.page_title.rect().bottomLeft()).y()
    transcript_top = chat.transcript.mapTo(window, chat.transcript.rect().topLeft()).y()
    assert 0 <= transcript_top - title_bottom <= 24

    # 切到其它页时收起键隐藏且不占位；切回恢复。
    window.show_page("overview")
    QApplication.processEvents()
    assert toggle.isHidden()
    window.show_page("chat")
    QApplication.processEvents()
    assert not toggle.isHidden()


def test_chat_composer_puts_config_left_of_send(window: Any) -> None:
    """对话框底部工具行：接入配置在发送按钮左侧（不再有模型胶囊）。"""
    from PySide6.QtWidgets import QApplication

    window.show_page("chat")
    window.show()
    QApplication.processEvents()
    chat = window.page("chat")
    assert not hasattr(chat, "model_combo")
    config_pos = chat.config_button.mapTo(chat, chat.config_button.rect().topLeft())
    chat_pos = chat.chat_toggle.mapTo(chat, chat.chat_toggle.rect().topLeft())
    paint_pos = chat.paint_toggle.mapTo(chat, chat.paint_toggle.rect().topLeft())
    send_pos = chat.send_button.mapTo(chat, chat.send_button.rect().topLeft())
    assert abs(config_pos.y() - send_pos.y()) <= 2
    assert config_pos.x() < chat_pos.x() < paint_pos.x() < send_pos.x()
    assert chat.transcript.objectName() == "ChatTranscript"
    assert chat.input_edit.objectName() == "ChatInput"


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
    """窗口图标来自 assets/icons：展开时在标题左侧；折叠后图标居中、箭头按钮在下方且可见。"""
    assert window.nav.title_label.text() == "LLM"
    assert window.windowTitle().startswith("LLM")
    assert not window.windowIcon().isNull()
    assert window.windowIcon().availableSizes()
    icon_label = window.nav.icon_label
    assert icon_label is not None
    window.nav.set_collapsed(True)
    assert not icon_label.isHidden()
    assert not window.nav.toggle_button.isHidden()
    assert window.nav.toggle_button.property("collapsed") == "true"
    window.nav.set_collapsed(False)
    assert not icon_label.isHidden()
    assert not window.nav.toggle_button.isHidden()
    assert window.nav.toggle_button.property("collapsed") == "false"


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
        # 版本列表回来之前不显示空白下拉 / 空行：加载条在跑、下拉显示占位、信息行收起。
        assert dialog.loading.is_running()
        assert dialog.version_combo.currentIndex() == -1
        assert "正在获取版本列表" in dialog.version_combo.placeholderText()
        assert dialog.variant_hint.isHidden()
        assert dialog.info.isHidden()
        assert dialog.status.isHidden()
        # 进度条 / 下载并安装 / 关闭那一行在获取版本期间整体隐藏。
        assert dialog.progress_row.isHidden()
        deadline = time.monotonic() + 5
        while dialog.version_combo.count() == 0 and time.monotonic() < deadline:
            QApplication.processEvents()
        assert not dialog.loading.is_running()
        assert not dialog.progress_row.isHidden()
        assert dialog.version_combo.currentData() == "b7000"
        variants = [dialog.variant_combo.itemData(index) for index in range(dialog.variant_combo.count())]
        assert variants == ["cuda", "cpu"]
        assert "llama-b7000-bin-win-cuda-13.5-x64.zip" in dialog.info.text()
        assert "NVIDIA" in dialog.variant_hint.text()
        assert "NVIDIA" in str(dialog.variant_combo.itemData(0, Qt.ItemDataRole.ToolTipRole))
        # 高度贴合内容：不再写死 660×280，也没有滚动区。
        from fcmd.gui.widgets import PageScroll, fit_dialog_to_content  # noqa: F401

        assert dialog.findChild(PageScroll) is None
        assert abs(dialog.height() - dialog.sizeHint().height()) <= 4

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


def test_quick_serve_routes_diffusion_models_to_paint(window: Any, tmp_path: Path) -> None:
    """扩散主模型的「启动服务」走 sd.cpp 请求，不再按 LLM 量化模型处理。"""
    window.show_page("models")
    browse = window.page("models").browse
    weight = tmp_path / "flux-2-dev.safetensors"
    emitted: list[Path] = []
    browse.state.paintModelRequested.connect(emitted.append)
    browse._paint_models["qwen/flux-2-dev"] = weight

    browse._on_quick_serve("Qwen/Flux-2-dev")

    assert emitted == [weight]
