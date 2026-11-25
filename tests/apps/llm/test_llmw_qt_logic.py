"""llmw Qt 前端的纯逻辑测试：参数解析、资源检查、行渲染与采样封装。

页面类本身是 ``pragma: no cover`` 的 GUI 胶水，这里只测与 Qt 无关的模块级
函数。Qt 不可用时整体跳过（CI 只装 ``--extra test``，不含 PySide6）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.apps.llm import _llmw_convert, llmbase, llmconvert, llmserve
from fcmd.apps.llm._llmpaint_engine import SdEngine, engine_backend, engine_version
from fcmd.apps.llm._llmw_hardware import HardwareSnapshot
from fcmd.apps.llm._llmw_monitor import LoadSample
from fcmd.apps.llm._llmw_qt_center import _BrowsePanel, _ModelDetailView, _ModelItemRow, quantize_menu_items
from fcmd.apps.llm._llmw_qt_chat import endpoint_from_form, service_labels
from fcmd.apps.llm._llmw_qt_overview import (
    PaintSample,
    paint_running_subtitle,
    paint_running_title,
    paint_sample_text,
    performance_metrics_text,
    performance_rate_text,
    recommendation_params_html,
    recommendation_perf_html,
    running_model_subtitle,
    running_model_title,
    sample_overview,
    sample_paint,
)
from fcmd.apps.llm._llmw_qt_paint import (
    command_preview,
    components_from_settings,
    default_settings,
    format_extra_args,
    format_progress,
    model_roots,
    output_dir,
    parse_extra_args,
    parse_paint_settings,
    request_from_settings,
    settings_summary,
    size_preset,
)
from fcmd.apps.llm._llmw_qt_paint import startup_args_tail as paint_args_tail
from fcmd.apps.llm._llmw_qt_paint_engine import variant_note
from fcmd.apps.llm._llmw_qt_run import (
    build_plan_from,
    memory_guard,
    model_combo_text,
    model_item_text_color,
    parse_custom_args,
    parse_int,
    parse_optional_int,
    pick_model,
    plan_lines,
    sanitize_custom_args,
    startup_args_tail,
    strip_form_owned_args,
)
from fcmd.apps.llm._llmw_qt_settings import engine_rows_values
from fcmd.apps.llm._llmw_recommend import Recommendation
from fcmd.apps.llm.llmbase import EngineInfo, LaunchPlan, ModelEntry, Profile, ServeError, ServeState, Tuning
from fcmd.apps.llm.llmdown import ModelInfo, ModelSummary
from fcmd.gui import theme as _guitheme
from fcmd.gui.widgets import ItemRow


def _plan(*, model: Path = Path("model.gguf"), context: int = 65536, kv_type: str | None = None) -> LaunchPlan:
    return LaunchPlan(
        name="qwen",
        model=model,
        profile=Profile("balanced", "Q5_K_M", 65536, "f16", 2048, "平衡"),
        context=context,
        host="127.0.0.1",
        port=8080,
        alias="qwen",
        api_key="sk-test",
        threads=8,
        engine=EngineInfo(tag="b12211", exe=Path("llama-server")),
        log=Path("qwen.log"),
        kv_type=kv_type,
        tuning=Tuning(),
    )


def _state(**kwargs: Any) -> ServeState:
    base: dict[str, Any] = {
        "name": "qwen",
        "pid": 1,
        "create_time": 0.0,
        "host": "127.0.0.1",
        "port": 8080,
        "model": "/models/qwen.gguf",
        "profile": "balanced",
        "context": 65536,
        "engine": "b12211",
        "alias": "qwen",
        "api_key": "sk",
        "log": "/tmp/qwen.log",
        "started_at": "2026-01-01T00:00:00",
    }
    base.update(kwargs)
    return ServeState(**base)


def _model(root: Path, name: str, size: int = 100) -> ModelEntry:
    return ModelEntry(root=root, path=root / name, size=size, profiles=("balanced",), tags=("代码",))


# ---------------------------------------------------------------------- #
# 参数解析与资源检查
# ---------------------------------------------------------------------- #
def test_parse_int_and_optional_int() -> None:
    assert parse_int(" 8080 ", "端口") == 8080
    assert parse_int("-1", "端口") == -1
    assert parse_optional_int("", "上下文") is None
    assert parse_optional_int("4096", "上下文") == 4096
    with pytest.raises(ServeError, match="端口 必须是整数"):
        parse_int("abc", "端口")
    with pytest.raises(ServeError, match="上下文 必须是整数"):
        parse_optional_int("x", "上下文")


def test_model_combo_text_keeps_full_name_and_honors_explicit_limit() -> None:
    entry = ModelEntry(
        root=Path("/models"),
        path=Path("/models/Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf"),
        size=20 * 1024**3,
    )
    text = model_combo_text(entry)
    assert "Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf" in text
    assert "…" not in text
    # 仍可按调用方给的 limit 显式截断（不依赖窗口宽度）。
    assert len(model_combo_text(entry, limit=30).split("  (")[0]) == 30


def test_model_item_text_color_follows_theme_semantics() -> None:
    """弹层项文字色：未选中时运行中项置绿，选中项统一用主题选中前景色。"""
    theme = _guitheme.resolve_theme("dark-blue")
    palette = theme.palette
    assert model_item_text_color(theme, selected=False, running=True) == palette.success
    assert model_item_text_color(theme, selected=False, running=False) == palette.text
    for name in _guitheme.theme_names():
        themed = _guitheme.resolve_theme(name)
        selected = model_item_text_color(themed, selected=True, running=True)
        # 选中底色是 select_bg：深色 / 浅色 / 高对比 / native 四套主题都得用 select_fg。
        assert selected == themed.palette.select_fg
        assert selected == model_item_text_color(themed, selected=True, running=False)


def test_sanitize_custom_args_drops_model_flags() -> None:
    assert sanitize_custom_args(
        ("D:/models/demo.gguf", "-ngl", "99", "-m", "x.gguf", "--model", "y.gguf", "--mmproj", "z.gguf")
    ) == ("-ngl", "99")


def test_startup_args_tail_drops_executable_and_model() -> None:
    argv = [
        "llama-server",
        "-m",
        "D:/models/demo.gguf",
        "-ngl",
        "99",
        "--mmproj",
        "D:/models/vision.gguf",
        "-c",
        "65536",
    ]
    assert startup_args_tail(argv) == ("-ngl", "99", "-c", "65536")


def test_strip_form_owned_args_drops_stale_snapshot_flags() -> None:
    """「填入参数」留下的旧快照不能覆盖表单里的监听地址与端口。"""
    generated = ("-ngl", "99", "-c", "65536", "--host", "0.0.0.0", "--port", "8080", "--api-key", "sk-test")
    custom = ("-ngl", "99", "-c", "65536", "--host", "127.0.0.1", "--port", "8080", "--api-key", "sk-test")
    assert strip_form_owned_args(custom, generated) == ("-ngl", "99")


def test_strip_form_owned_args_keeps_extras_and_unsupported_flags() -> None:
    """表单没生成的开关与自定义扩展参数原样保留，``--flag=value`` 也能识别。"""
    generated = ("--host", "0.0.0.0", "--port", "8080")
    custom = ("--host=127.0.0.1", "--no-mmap", "--threads-http", "8", "--flash-attn", "off")
    assert strip_form_owned_args(custom, generated) == ("--no-mmap", "--threads-http", "8", "--flash-attn", "off")


def test_parse_custom_args_splits_shell_like_text() -> None:
    assert parse_custom_args("") == ()
    assert parse_custom_args("  --no-mmap   --flash-attn on  ") == ("--no-mmap", "--flash-attn", "on")
    with pytest.raises(ValueError):
        parse_custom_args("'unclosed")


def test_memory_guard_passes_with_enough_vram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmbase, "run_command", lambda *a, **k: llmbase.CommandResult(0))
    message = memory_guard(_plan(), (0, 48 * 1024))
    assert message.startswith("资源检查通过")


def test_memory_guard_rejects_hardware_limit() -> None:
    with pytest.raises(ServeError, match="整卡显存不足"):
        memory_guard(_plan(), (0, 1))


def test_memory_guard_rejects_busy_vram() -> None:
    with pytest.raises(ServeError, match="当前可用显存不足"):
        memory_guard(_plan(), (47 * 1024, 48 * 1024))


def test_memory_guard_falls_back_to_system_memory() -> None:
    message = memory_guard(_plan(), None, available_memory=64 * 1024**3)
    assert "可用内存" in message
    with pytest.raises(ServeError, match="可用内存不足"):
        memory_guard(_plan(), None, available_memory=1)


def test_plan_lines_include_command_and_warnings() -> None:
    plan = _plan()
    lines = plan_lines(
        plan,
        estimate=llmbase.GIB,
        tags=("代码", "对话"),
        precision_detail="Q5_K_M",
        advice="适合代码补全",
        total=None,
        free=None,
    )
    assert any("命令: " in line for line in lines)
    assert any("标签: 代码 · 对话" in line for line in lines)
    assert any("精度: Q5_K_M" in line for line in lines)
    assert any("显存估算" in line for line in lines)


def test_plan_lines_warn_when_fit_fails() -> None:
    from fcmd.apps.llm import llmmodel

    plan = _plan()
    unfit = llmmodel.VramFit(needed=10 * 1024**3, available=1024**3, max_context=2048)
    lines = plan_lines(plan, estimate=10 * 1024**3, total=unfit, free=None)
    assert any(line.strip().startswith("警告") for line in lines)


def test_build_plan_from_rejects_empty_name() -> None:
    with pytest.raises(ServeError, match="服务名不能为空"):
        build_plan_from({"name": "   "})


def test_build_plan_from_rejects_bad_port() -> None:
    with pytest.raises(ServeError, match="端口 必须是整数"):
        build_plan_from({"name": "qwen", "port": "x"})


def test_quantize_menu_items_marks_existing_precisions() -> None:
    """已量化的精度在菜单里打勾并禁用，其余精度保持可选。"""
    items = quantize_menu_items(("q4_k_m", "Q5_K_M"))
    by_quant = {quant: done for quant, _desc, done in items}
    assert by_quant["Q4_K_M"] is True
    assert by_quant["Q5_K_M"] is True
    assert by_quant["Q8_0"] is False
    assert all(desc for _quant, desc, _done in items)


def test_convert_source_status_reports_ready_and_missing(tmp_path: Path) -> None:
    """设置页按当前引擎 tag 判断 llama.cpp 是否已包含转换源码。"""
    tag = "b9"
    ready_path = llmconvert.convert_source_root(tag, tmp_path) / llmconvert.CONVERT_SCRIPT
    ready_path.parent.mkdir(parents=True)
    ready_path.write_text("# ready", encoding="utf-8")

    ready = _llmw_convert.convert_source_status(tag, tmp_path)
    assert ready.ready is True
    assert ready.tag == tag
    assert ready.title == "已包含源码"
    assert ready.tone == "success"
    assert ready.hint == str(ready_path)

    missing = _llmw_convert.convert_source_status("b10", tmp_path)
    assert missing.ready is False
    assert missing.title == "未下载源码"
    assert missing.tone == "warning"
    assert "首次转换时自动下载" in missing.hint


def test_model_item_row_expands_inline_detail() -> None:
    """浏览结果行展开后，详情区直接挂在行下方（不再需要右侧面板）。"""
    from PySide6.QtWidgets import QApplication, QLabel

    QApplication.instance() or QApplication([])
    row = _ModelItemRow(ModelSummary(repo="Qwen/demo"))
    # 行卡骨架来自共享 ItemRow（wslmanw 的发行版 / 镜像行同源）。
    assert isinstance(row, ItemRow)
    owner_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), owner="Qwen", installed=True)
    owner_labels = [label for label in owner_row.findChildren(QLabel) if label.objectName() == "OwnerTag"]
    assert [label.text() for label in owner_labels] == ["Qwen"]
    installed_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), installed=True)
    assert installed_row.download_button is None
    assert not hasattr(installed_row, "delete_button")
    local_only = _ModelItemRow(ModelSummary(repo="Qwen3.8-27B"), installed=True, downloadable=False)
    assert local_only.download_button is None
    assert local_only.downloadable is False
    assert installed_row.downloadable is True
    assert "仅本地" in {label.text() for label in local_only.findChildren(QLabel, "StatusTag")}
    assert "已安装" in {label.text() for label in installed_row.findChildren(QLabel, "StatusTag")}
    convertible_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), convertible=True)
    assert convertible_row.convert_button is not None
    assert convertible_row.convert_button.text() == "转换"
    assert convertible_row.convert_button.isEnabled()
    converted_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), convertible=True, converted=True)
    assert converted_row.convert_button is None
    assert "已转换" in {label.text() for label in converted_row.findChildren(QLabel, "StatusTag")}
    blocked_row = _ModelItemRow(ModelSummary(repo="Qwen/image"), convert_blocked="diffusers 多组件模型")
    assert blocked_row.convert_button is None
    assert "不可转换" in {label.text() for label in blocked_row.findChildren(QLabel, "StatusTag")}
    paint_row = _ModelItemRow(ModelSummary(repo="Qwen/Flux-2"), startable=True, paint_startable=True)
    assert paint_row.start_button is not None
    assert "画图服务" in paint_row.start_button.toolTip()
    quantized_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), quant_tags=("Q4_K_M", "Q5_K_M"))
    assert quantized_row.findChild(QLabel, "QuantTag") is not None
    start_row = _ModelItemRow(
        ModelSummary(repo="Qwen/demo"),
        quant_tags=("Q4_K_M", "Q5_K_M"),
        startable=True,
        default_quant="Q4_K_M",
        serve_path=Path("/models/Qwen/demo/x-Q4_K_M.gguf"),
    )
    assert start_row.start_button is not None
    assert start_row.start_button.text() == "启动服务"
    assert start_row.start_button.isEnabled()
    tag_texts = {label.text() for label in start_row.findChildren(QLabel, "QuantTag")}
    assert "Q4_K_M · 上次启动" in tag_texts
    assert "Q5_K_M" in tag_texts
    requested: list[str] = []
    start_row.startRequested.connect(requested.append)
    start_row.start_button.click()
    assert requested == ["Qwen/demo"]
    start_row.set_serve_state(busy=False, running=True)
    assert start_row.start_button.text() == "已启动"
    assert not start_row.start_button.isEnabled()
    # 禁用态的主按钮底色变浅：勾选图标必须跟着换成 muted，
    # 否则浅色主题下 on_accent（白）的勾在白底上看不见。
    assert start_row.start_button.property("fcmdIconTone") == "muted"
    start_row.set_serve_state(busy=True, running=False)
    assert start_row.start_button.property("fcmdIconTone") == "muted"
    start_row.set_serve_state(busy=False, running=False)
    assert start_row.start_button.text() == "启动服务"
    assert start_row.start_button.property("fcmdIconTone") == "on_accent"
    quantizable_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), quantizable=True)
    assert quantizable_row.quant_button is not None
    assert quantizable_row.quant_button.text() == "量化"
    assert quantizable_row.quant_button.isEnabled()
    blocked_quant_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), quant_blocked=True)
    assert blocked_quant_row.quant_button is None
    assert "不可量化" in {label.text() for label in blocked_quant_row.findChildren(QLabel, "StatusTag")}
    many_tags_row = _ModelItemRow(
        ModelSummary(repo="Qwen/demo"),
        quant_tags=("Q2_K", "Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"),
    )
    many_tag_texts = {label.text() for label in many_tags_row.findChildren(QLabel, "QuantTag")}
    assert "+2" in many_tag_texts
    assert len(many_tag_texts) == 5

    partial_row = _ModelItemRow(
        ModelSummary(repo="Qwen/demo"), installed=True, incomplete=True, progress_text="已下载 42%"
    )
    assert partial_row.download_button.text() == "继续下载"
    assert partial_row.download_button.isEnabled()
    progress = partial_row.findChild(QLabel, "DownloadProgress")
    assert progress is not None and progress.text() == "已下载 42%"
    partial_row.set_download_state(busy=True, active=False, queued=True)
    assert partial_row.download_button.text() == "排队中…"
    assert row.download_button.text() == "下载"
    assert row.repo == "Qwen/demo"
    assert not row.is_expanded()
    assert row.expand_button.icon().isNull() is False
    assert row.expand_button.toolTip() == "展开详细信息"
    assert not hasattr(row, "files_button")
    assert row.detail_host.isHidden()
    row.set_expanded(True)
    assert row.is_expanded()
    assert row.expand_button.toolTip() == "收起详细信息"
    assert not row.detail_host.isHidden()
    row.set_expanded(False)
    assert not row.is_expanded()
    assert row.expand_button.icon().isNull() is False
    assert row.expand_button.toolTip() == "展开详细信息"
    assert not hasattr(row, "files_button")
    assert row.detail_host.isHidden()


def test_model_detail_view_reset_and_metadata() -> None:
    from PySide6.QtWidgets import QApplication

    from fcmd.apps.llm.llmdown import ModelFile

    QApplication.instance() or QApplication([])
    view = _ModelDetailView(lambda _repo: [])
    view.reset("Qwen/demo")
    assert view.repo == "Qwen/demo"
    assert not hasattr(view, "download_button")
    assert not hasattr(view, "readme")
    # 详情分页：右侧「文件列表」与「详细信息」并列。
    assert [view.tabs.tabText(index) for index in range(view.tabs.count())] == ["详细信息", "文件列表"]
    # 页签用共享的药丸样式（同运行页）：无整条基线，容器底色跟行卡走。
    assert view.tabs.objectName() == "RowTabs"
    assert view.tabs.tabBar().objectName() == "PillTabBar"
    assert view.tabs.tabBar().drawBase() is False
    assert view.files_table.objectName() == "ModelFiles"
    assert view.files_table.columnCount() == 3
    assert [view.files_table.horizontalHeaderItem(i).text() for i in range(3)] == ["文件", "大小", "状态"]
    # 文件表与详情区同底色：不画交替行底色（否则会突出于行卡背景之外）。
    assert view.files_table.alternatingRowColors() is False
    view.set_error("网络错误")
    assert view.error.text() == "网络错误"
    assert not view.error.isHidden()
    view.render(ModelInfo(summary=ModelSummary(repo="Qwen/demo", chinese_name="示例")))
    assert view.meta_values["中文名"].text() == "示例"

    # 文件清单：路径 + 大小两列，合计写进状态行。
    view._files_ready(
        "Qwen/demo",
        [
            ModelFile("model-Q4_K_M.gguf", 2 * 1024 * 1024 * 1024),
            ModelFile("split_files/vae/ae.safetensors", 320 * 1024 * 1024),
        ],
    )
    assert view.files_table.rowCount() == 2
    assert view.files_table.item(0, 0).text() == "model-Q4_K_M.gguf"
    assert view.files_table.item(1, 1).text() == "320.00 MB"
    assert "共 2 个文件" in view.files_status.text()
    view._files_failed("Qwen/demo", "超时")
    assert view.files_table.rowCount() == 0
    assert "超时" in view.files_status.text()


def test_model_detail_files_show_state_and_download_all(tmp_path: Path) -> None:
    """文件列表显示每个文件的下载状态；「下载全部」只补未下载完的文件。"""
    from PySide6.QtWidgets import QApplication

    from fcmd.apps.llm.llmdown import ModelFile

    QApplication.instance() or QApplication([])
    root = tmp_path / "Qwen" / "demo"
    root.mkdir(parents=True)
    (root / "a.gguf").write_bytes(b"x" * 32)
    (root / "split_files").mkdir()
    (root / "split_files" / "b.safetensors.part").write_bytes(b"y" * 8)
    picked: list[tuple[str, tuple[ModelFile, ...]]] = []
    view = _ModelDetailView(
        lambda _repo: [],
        lambda repo, files: picked.append((repo, files)),
        local_dir=lambda _repo: root,
    )
    view.reset("Qwen/demo")
    view._files_ready(
        "Qwen/demo",
        [
            ModelFile("a.gguf", 32),
            ModelFile("split_files/b.safetensors", 64),
            ModelFile("mmproj.gguf", 16),
        ],
    )
    assert [view.files_table.item(row, 2).text() for row in range(3)] == ["已下载", "部分下载", "未下载"]
    assert "已下载 1 / 未下载 2" in view.files_status.text()
    # 「下载全部」只补未下载完的（含 .part 续传），已下载的跳过
    assert view.files_download_all_button.isHidden() is False
    assert view.files_download_all_button.isEnabled() is True
    assert "2 个" in view.files_download_all_button.toolTip()
    view.files_download_all_button.click()
    assert len(picked) == 1
    assert [file.path for file in picked[0][1]] == ["split_files/b.safetensors", "mmproj.gguf"]

    # 补完之后再刷新：全部已下载 → 「下载全部」禁用并给出提示
    (root / "split_files" / "b.safetensors").write_bytes(b"y" * 64)
    (root / "mmproj.gguf").write_bytes(b"z" * 16)
    view.refresh_file_states()
    assert [view.files_table.item(row, 2).text() for row in range(3)] == ["已下载", "已下载", "已下载"]
    assert view.files_download_all_button.isEnabled() is False
    assert view.files_download_all_button.toolTip() == "全部文件已下载"

    # 同仓库重新展开不重拉清单，只刷新状态
    view.reset("Qwen/demo")
    assert [view.files_table.item(row, 2).text() for row in range(3)] == ["已下载", "已下载", "已下载"]


def test_model_detail_view_downloads_selected_files() -> None:
    from PySide6.QtWidgets import QApplication

    from fcmd.apps.llm.llmdown import ModelFile

    QApplication.instance() or QApplication([])
    picked: list[tuple[str, tuple[ModelFile, ...]]] = []
    view = _ModelDetailView(lambda _repo: [], lambda repo, files: picked.append((repo, files)))
    view.reset("Qwen/demo")
    view._files_ready(
        "Qwen/demo",
        [
            ModelFile("split_files/diffusion_models/model-Q4_K_M.gguf", 1024),
            ModelFile("split_files/vae/ae.safetensors", 2048),
        ],
    )
    assert view.files_download_button.isEnabled() is False
    view.files_table.selectRow(1)
    assert view.files_download_button.isEnabled() is True
    view.files_download_button.click()
    assert len(picked) == 1
    assert picked[0][0] == "Qwen/demo"
    assert [file.path for file in picked[0][1]] == ["split_files/vae/ae.safetensors"]


def test_model_detail_view_skips_online_files_for_local_only() -> None:
    """仅本地模型不读在线文件清单，也不弹下载按钮。"""
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    loads: list[str] = []
    view = _ModelDetailView(lambda repo: loads.append(repo) or [])
    view.reset("Qwen3.8-27B", online=False)
    assert "仅本地" in view.error.text()
    view._ensure_files()
    assert loads == []
    assert view.files_table.rowCount() == 0
    assert "仅本地" in view.files_status.text()
    assert view.files_download_button.isHidden() is True


# ---------------------------------------------------------------------- #
# 概览 / 对话 / 设置行渲染
# ---------------------------------------------------------------------- #
def test_service_labels_marks_dead_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.llm._llmw_chat.process_alive", lambda pid, created: pid == 1)
    labels = service_labels([_state(pid=1), _state(name="dead", pid=2)])
    assert labels[0][0] == "qwen（运行中）"
    assert labels[1][0] == "dead（已退出）"


def test_endpoint_from_form_strips_v1() -> None:
    endpoint = endpoint_from_form("http://127.0.0.1:8080/v1/", " sk-test ", "qwen")
    assert endpoint.base_url == "http://127.0.0.1:8080"
    assert endpoint.api_key == "sk-test"
    assert endpoint.model == "qwen"


def test_endpoint_from_form_rejects_empty_fields() -> None:
    with pytest.raises(ServeError, match="服务地址"):
        endpoint_from_form("   ", "sk", "qwen")
    with pytest.raises(ServeError, match="模型名"):
        endpoint_from_form("http://127.0.0.1:8080", "sk", "  ")


def test_engine_rows_values() -> None:
    rows = engine_rows_values([EngineInfo(tag="b1", exe=Path("llama-server"), source="current")])
    assert rows == [("b1", "current", "llama-server")]


# ---------------------------------------------------------------------- #
# 采样封装
# ---------------------------------------------------------------------- #
def test_sample_overview_without_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview._sample_load", lambda pid, process: LoadSample())
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview.sample_paint", lambda: PaintSample(None, False))
    load, rows, paint = sample_overview([])
    assert isinstance(load, LoadSample)
    assert rows == ()
    assert isinstance(paint, PaintSample)
    assert paint.alive is False and paint.progress is None


def test_sample_overview_reports_each_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview.sample_paint", lambda: PaintSample(None, False))
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview._attach_process", lambda pid: None)
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview._sample_load", lambda pid, process: LoadSample())
    metrics = llmserve.ServeMetrics(prompt_tokens=5, predicted_tokens=4, processing=1, deferred=2, kv_usage=0.5)
    monkeypatch.setattr(llmserve, "fetch_metrics", lambda host, port, api_key: metrics)
    _load, rows, _paint = sample_overview([_state(), _state(name="coder", port=8081, model="/models/coder.gguf")])
    assert [row.name for row in rows] == ["qwen", "coder"]
    assert all(row.metrics is not None and row.error is None for row in rows)
    assert rows[0].metrics is not None
    assert rows[0].metrics.predicted_tokens == 4


def test_sample_overview_reports_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview.sample_paint", lambda: PaintSample(None, False))
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview._attach_process", lambda pid: None)
    monkeypatch.setattr("fcmd.apps.llm._llmw_qt_overview._sample_load", lambda pid, process: LoadSample())

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ServeError("connection refused")

    monkeypatch.setattr(llmserve, "fetch_metrics", boom)
    _load, rows, _paint = sample_overview([_state()])
    assert rows[0].metrics is None
    assert rows[0].error == "connection refused"


def test_sample_paint_reads_status_and_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """画图服务采样：只读登记状态 + 生成进度；服务未运行时不算运行中。"""
    from dataclasses import replace
    from unittest.mock import patch

    from fcmd.apps.llm._llmpaint_server import PaintState

    with patch("fcmd.apps.llm._llmpaint_server.status", return_value=(None, False)):
        assert sample_paint() == PaintSample(None, False, None)

    state = PaintState(
        pid=1,
        create_time=0.0,
        host="127.0.0.1",
        port=1234,
        engine_tag="master-1094",
        exe="sd-server",
        model="/models/z_image_turbo.gguf",
        components={},
        fingerprint=(),
        log="sd-server.log",
        started_at="2026-01-01T00:00:00",
    )
    with (
        patch("fcmd.apps.llm._llmpaint_server.status", return_value=(state, True)),
        patch("fcmd.apps.llm._llmpaint_server.fetch_progress", return_value={"progress": 0.42}),
    ):
        painted = sample_paint()
    assert painted.state is state and painted.alive is True
    assert painted.progress == pytest.approx(0.42)
    assert paint_running_title(state) == "z_image_turbo.gguf"
    assert paint_running_title(replace(state, model="")) == "画图服务"
    assert paint_running_subtitle(state) == "画图 · http://127.0.0.1:1234 · master-1094"
    assert paint_sample_text(None).startswith("sd.cpp 画图服务")
    assert "空闲" in paint_sample_text(0.0)
    assert "42%" in paint_sample_text(0.42)


def test_running_model_performance_helpers() -> None:
    """运行中模型行：标题 / 副标题 / tok/s 与性能摘要的纯文本口径。"""
    state = _state(model="D:/models/qwen.gguf")
    assert running_model_title(state) == "qwen.gguf"
    assert running_model_title(_state(model="")) == "qwen"
    subtitle = running_model_subtitle(state)
    assert "qwen" in subtitle and "127.0.0.1:8080" in subtitle and "balanced" in subtitle
    assert performance_rate_text(None) == "-"
    assert performance_rate_text(12.34) == "12.3"
    assert performance_metrics_text(None, None) == "等待采样…"
    metrics = llmserve.ServeMetrics(prompt_tokens=9302, predicted_tokens=12045, processing=1, deferred=0, kv_usage=0.18)
    text = performance_metrics_text(metrics, 5.5)
    assert "预填充 5.5 tok/s" in text
    assert "处理中 1" in text and "排队 0" in text
    assert "KV 18.0%" in text
    assert "tokens 12045 / 9302" in text


def test_hardware_snapshot_import_is_usable() -> None:
    """概览页只读快照；这里确认快照类型仍可独立构造（防止上游改名）。"""
    assert HardwareSnapshot().gpu_mib is None


def test_pick_model_prefers_explicit_selection(tmp_path: Path) -> None:
    entries = [_model(tmp_path, "a-Q4_K_XL.gguf", 10), _model(tmp_path, "b-Q5_K_M.gguf", 20)]
    selected = tmp_path / "other.gguf"
    assert pick_model(selected, entries, "Q5_K_M") == selected


def test_pick_model_matches_profile_quant_first(tmp_path: Path) -> None:
    entries = [
        _model(tmp_path, "a-Q4_K_XL.gguf", 100),
        _model(tmp_path, "b-Q5_K_M.gguf", 20),
    ]
    assert pick_model(None, entries, "Q5_K_M") == tmp_path / "b-Q5_K_M.gguf"


def test_pick_model_ud_xl_matches_base_profile(tmp_path: Path) -> None:
    entries = [
        _model(tmp_path, "a-UD-Q4_K_XL.gguf", 100),
        _model(tmp_path, "b-Q5_K_M.gguf", 20),
    ]
    assert pick_model(None, entries, "Q4_K_M") == tmp_path / "a-UD-Q4_K_XL.gguf"


def test_pick_model_falls_back_to_largest_when_quant_missing(tmp_path: Path) -> None:
    entries = [_model(tmp_path, "a-Q4_K_XL.gguf", 10), _model(tmp_path, "b-IQ4_XS.gguf", 30)]
    assert pick_model(None, entries, "Q5_K_M") == tmp_path / "b-IQ4_XS.gguf"


def test_pick_model_returns_none_for_empty_list() -> None:
    assert pick_model(None, [], "Q5_K_M") is None


def test_recommendation_html_highlights_name_params_and_perf() -> None:
    """推荐行参数 / 性能 HTML：关键数值加粗，性能标注来源。"""
    recommendation = Recommendation(
        model=ModelEntry(root=Path("/models"), path=Path("/models/qwen3-8b-Q5_K_M.gguf"), size=5 * 1024**3),
        profile="coder",
        context=32768,
        kv_type="q8_0",
        batch=512,
        ubatch=256,
        parallel=2,
        tuning="并发吞吐",
        decode_tps=42.4,
        prompt_tps=16200.0,
        measured=True,
        basis="代码 · 实测",
        score=90,
        score_parts=(40, 25, 15, 10),
    )

    params = recommendation_params_html(recommendation)
    assert "档位 <b>coder</b>" in params
    assert "上下文 <b>32K</b>" in params
    assert "KV <b>q8_0</b>" in params
    assert "并行 <b>2</b>" in params
    assert "调优 <b>并发吞吐</b>" in params

    perf = recommendation_perf_html(recommendation)
    assert "解码 <b>42</b> tok/s" in perf
    assert "预填充 <b>16.2k</b> tok/s" in perf
    assert "<b>实测</b>" not in perf


def test_recommendation_row_uses_perf_tag() -> None:
    """概览推荐行的「预测 / 实测」是胶囊标签，不混在性能小字里。"""
    from PySide6.QtWidgets import QLabel

    from fcmd.apps.llm._llmw_qt_overview import RecommendationRow
    from fcmd.apps.llm.llmbase import ModelEntry

    recommendation = Recommendation(
        model=ModelEntry(root=Path("/models"), path=Path("/models/demo.gguf"), size=1024),
        profile="balanced",
        context=8192,
        kv_type="f16",
        batch=512,
        ubatch=256,
        parallel=1,
        tuning="默认",
        decode_tps=10.0,
        prompt_tps=100.0,
        measured=False,
        basis="对话 · 预测",
        score=70,
        score_parts=(20, 20, 15, 15),
    )
    row = RecommendationRow(recommendation)
    tags = [label for label in row.findChildren(QLabel) if label.objectName() == "StatusTag"]
    assert [tag.text() for tag in tags] == ["预测"]
    assert tags[0].property("tone") == "accent"


def test_paint_settings_defaults_and_coercion() -> None:
    """画图页设置：非法类型回退默认，数值夹到合法区间，字符串去空白。"""
    assert parse_paint_settings(None) == default_settings()
    settings = parse_paint_settings(
        {
            "model": "  /models/sdxl.safetensors  ",
            "width": 100,
            "height": 9999,
            "steps": 0,
            "cfg_scale": 99,
            "seed": -5,
            "batch": 99,
            "port": 0,
            "auto_start": False,
            "vae_tiling": "yes",
            "extra_args": "--clip-on-cpu --foo bar",
            "mode": " diffusion ",
        }
    )
    assert settings["model"] == "/models/sdxl.safetensors"
    assert settings["width"] == 100
    assert settings["height"] == 4096
    assert settings["steps"] == 1
    assert settings["cfg_scale"] == 30.0
    assert settings["seed"] == -1
    assert settings["batch"] == 8
    assert settings["port"] == 1
    assert settings["auto_start"] is False
    assert settings["vae_tiling"] is False
    assert settings["extra_args"] == ("--clip-on-cpu", "--foo", "bar")
    assert settings["mode"] == "diffusion"
    assert parse_paint_settings({"extra_args": ["--a", "", 1]})["extra_args"] == ("--a",)


def test_paint_extra_args_roundtrip() -> None:
    """额外参数：双引号保留含空格的值，写回时再补引号。"""
    args = parse_extra_args('--foo "C:\\my models\\vae.safetensors" --bar')
    assert args == ("--foo", "C:\\my models\\vae.safetensors", "--bar")
    assert format_extra_args(args) == '--foo "C:\\my models\\vae.safetensors" --bar'
    assert parse_extra_args("  ") == ()
    assert format_extra_args(()) == ""


def test_paint_components_and_request_from_settings() -> None:
    """设置 → 组件 / 请求：路径扩展、seed=-1 与采样器原样传递。"""
    settings = parse_paint_settings(
        {
            "model": "/models/flux1-dev.safetensors",
            "vae": "/models/ae.safetensors",
            "t5xxl": "/models/t5xxl.safetensors",
            "mode": "auto",
            "offload_to_cpu": True,
            "extra_args": "--clip-on-cpu",
            "prompt": "a fox",
            "negative": "blurry",
            "width": 768,
            "height": 512,
            "steps": 20,
            "cfg_scale": 3.5,
            "seed": -1,
            "sampler": "euler",
            "scheduler": "karras",
            "batch": 2,
        }
    )
    components = components_from_settings(settings)
    assert components.model == Path("/models/flux1-dev.safetensors")
    assert components.vae == Path("/models/ae.safetensors")
    assert components.t5xxl == Path("/models/t5xxl.safetensors")
    assert components.offload_to_cpu is True
    assert components.extra_args == ("--clip-on-cpu",)

    request = request_from_settings(settings)
    assert (request.prompt, request.negative) == ("a fox", "blurry")
    assert (request.width, request.height) == (768, 512)
    assert (request.steps, request.cfg_scale) == (20, 3.5)
    assert (request.seed, request.sampler, request.scheduler, request.batch) == (-1, "euler", "karras", 2)


def test_paint_command_preview_uses_family_and_flags() -> None:
    """命令预览：FLUX 走 --diffusion-model，SDXL 走 -m，并带上优化开关与端口。"""
    flux = parse_paint_settings(
        {
            "model": "/models/flux1-dev.safetensors",
            "vae": "/models/ae.safetensors",
            "port": 4321,
            "vae_tiling": True,
            "extra_args": "--clip-on-cpu",
        }
    )
    preview = command_preview(flux)
    assert "--listen-port 4321" in preview
    assert "--diffusion-model" in preview
    assert "--vae" in preview
    assert "--vae-tiling" in preview
    assert "--clip-on-cpu" in preview

    sdxl = command_preview(parse_paint_settings({"model": "/models/sdxl-base.safetensors"}))
    # 路径在 Windows 上会规范化为反斜杠并由 shlex 加引号，这里只校验槽位与文件名
    assert "-m " in sdxl
    assert "sdxl-base.safetensors" in sdxl
    assert "--diffusion-model" not in sdxl


def test_paint_startup_args_tail_skips_executable_and_model() -> None:
    """尾部参数只留监听 / 组件 / 开关：不含可执行文件与主模型路径（可回贴自定义参数）。"""
    settings = parse_paint_settings(
        {
            "model": "/models/flux1-dev.safetensors",
            "vae": "/models/ae.safetensors",
            "port": 4321,
            "vae_tiling": True,
        }
    )
    tail = paint_args_tail(settings)
    assert tail[:2] == ("--listen-ip", "127.0.0.1")
    assert "--listen-port" in tail
    assert "4321" in tail
    assert "--vae" in tail
    assert "--vae-tiling" in tail
    assert "sd-server" not in tail
    assert "--diffusion-model" not in tail
    assert not any("flux1-dev" in item for item in tail)


def test_paint_filled_tail_is_not_appended_twice() -> None:
    """把「当前参数」原样贴回自定义参数时：组件不二次追加，命令预览保持不变。"""
    base = parse_paint_settings(
        {
            "model": "/models/flux1-dev.safetensors",
            "vae": "/models/ae.safetensors",
            "port": 4321,
            "vae_tiling": True,
        }
    )
    filled = parse_paint_settings({**base, "extra_args": format_extra_args(paint_args_tail(base))})
    assert components_from_settings(filled).extra_args == ()
    assert command_preview(filled) == command_preview(base)


def test_paint_format_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """进度文本、尺寸预设、扫描根与出图目录的默认 / 覆盖行为。"""
    assert format_progress({"progress": 0.42}) == "42%"
    assert format_progress({"progress": 42}) == "42%"
    assert format_progress({}) == ""
    assert format_progress(None) == ""

    assert size_preset(512, 512) == "512 × 512"
    assert size_preset(832, 1216) == "832 × 1216（竖）"
    assert size_preset(520, 512) == "自定义"

    # 配置摘要：模型名取文件名，尺寸 / 步数 / 采样器依次拼接，批量 >1 才出现。
    assert settings_summary(default_settings()) == "未选模型 · 512×512 · 25 步 · euler_a"
    summary = settings_summary(
        parse_paint_settings(
            {
                "model": "/models/qwen-image.gguf",
                "width": 1024,
                "height": 768,
                "steps": 30,
                "sampler": "dpm2",
                "batch": 2,
            }
        )
    )
    assert summary == "qwen-image.gguf · 1024×768 · 30 步 · dpm2 · 批量 2"
    assert (
        settings_summary(parse_paint_settings({"model": "/models/x.gguf", "sampler": ""})) == "x.gguf · 512×512 · 25 步"
    )

    monkeypatch.setenv("FCMD_MODELS", str(tmp_path / "models"))
    monkeypatch.setenv("FCMD_PAINT_DIR", str(tmp_path / "paint"))
    assert model_roots({}) == [tmp_path / "models", tmp_path / "models" / "converted"]
    assert model_roots({"model_dir": str(tmp_path / "extra")}) == [tmp_path / "extra"]
    assert output_dir({}) == tmp_path / "paint" / "out"
    assert output_dir({"output_dir": str(tmp_path / "mine")}) == tmp_path / "mine"


def test_paint_pending_downloads_lists_part_files(tmp_path: Path) -> None:
    """未下载完成的 ``*.part`` 文件单独列出（按大小降序），用于启动失败时的提示。"""
    from fcmd.apps.llm._llmpaint import pending_downloads

    small = tmp_path / "a" / "z_image_turbo-Q4_K.gguf.part"
    big = tmp_path / "b" / "MiniMax-H3-Turbo.tar.part"
    small.parent.mkdir(parents=True)
    big.parent.mkdir(parents=True)
    small.write_bytes(b"x" * 4)
    big.write_bytes(b"x" * 64)
    (tmp_path / "done.gguf").write_bytes(b"x" * 8)
    # 最终文件已存在的遗留分片不算未完成。
    (tmp_path / "done.gguf.part").write_bytes(b"x" * 8)
    assert pending_downloads([tmp_path]) == (big, small)
    assert pending_downloads([tmp_path / "missing"]) == ()


def test_paint_suggest_components_prefers_same_family() -> None:
    """自动配对优先同家族组件：避免把别的家族 VAE / 文本编码器配上导致启动即退出。"""
    from fcmd.apps.llm._llmpaint import PaintAsset, PaintAssets, suggest_components

    root = Path("/models")
    main = PaintAsset(root / "qwen-image/model.safetensors", "diffusion", "qwen-image", 3)
    sdxl_vae = PaintAsset(root / "sdxl/vae.safetensors", "vae", "sdxl", 9)
    qwen_vae = PaintAsset(root / "qwen-image/vae.safetensors", "vae", "qwen-image", 1)
    assets = PaintAssets((main, sdxl_vae, qwen_vae), (root,))
    suggestion = suggest_components(assets, main.path)
    assert suggestion.vae == qwen_vae.path
    # 只有别的家族时回退到该角色的第一个候选，而不是留空。
    fallback = suggest_components(PaintAssets((main, sdxl_vae), (root,)), main.path)
    assert fallback.vae == sdxl_vae.path


def test_paint_engine_version_and_backend_labels() -> None:
    """引擎展示名只取版本号，后端优先读 marker、缺失时从资产 tag 反推。"""
    exe = Path("sd-server.exe")
    release = SdEngine("sd-master-532d2c1-bin-win-cuda12-x64", exe)
    assert engine_version(release.tag) == "master-532d2c1"
    assert engine_backend(release) == "cuda12"
    older = SdEngine("sd-master-1106-abc123-bin-win-vulkan-x64", exe, backend="vulkan")
    assert engine_version(older.tag) == "master-1106-abc123"
    assert engine_backend(older) == "vulkan"
    assert engine_version("master-1106-abc123") == "master-1106-abc123"
    assert engine_backend(SdEngine("my-local", exe)) == ""
    assert engine_version("my-local") == "my-local"


def test_paint_engine_variant_note() -> None:
    """变体说明按前缀归并（cuda12 / rocm-6.0 与自定义构建）。"""
    assert "NVIDIA" in variant_note("cuda12")
    assert "兼容性最广" in variant_note("vulkan")
    assert "ROCm" in variant_note("rocm-6.0")
    assert "兜底" in variant_note("cpu")
    assert variant_note("metal") == "自定义构建"


def test_scan_installed_maps_diffusion_model_for_quick_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """已下载的扩散模型（如 Flux-2）要映射到 sd.cpp 启动入口，而不是被当成 LLM 量化模型。"""
    home = tmp_path / "home"
    models_root = tmp_path / "models"
    weight = models_root / "Qwen" / "Flux-2-dev" / "flux-2-dev.safetensors"
    weight.parent.mkdir(parents=True)
    weight.write_bytes(b"x" * (2 * 1024 * 1024))
    monkeypatch.setenv("FCMD_HOME", str(home))
    monkeypatch.setenv("FCMD_MODELS", str(models_root))
    monkeypatch.delenv("FCMD_CONFIG", raising=False)

    snapshot = _BrowsePanel._scan_installed()

    assert snapshot.paint_models["qwen/flux-2-dev"] == weight.resolve()
