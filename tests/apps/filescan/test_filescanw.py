"""filescanw 测试：设置解析 / 持久化、geometry、表达式应用与启动入口。"""

from __future__ import annotations

import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

from fcmd.apps.filescan import filescanw
from fcmd.apps.filescan._engine import ScanResult, ScanStats
from fcmd.apps.filescan._report import scan_summary
from fcmd.apps.filescan._rules import Expression, builtin_ruleset, disable_all
from fcmd.core.toolkit import _TOOL_REGISTRY


def test_tool_registration() -> None:
    """filescanw 仅注册一个无子命令的 GUI 入口。"""
    assert set(_TOOL_REGISTRY["filescanw"]) == {None}


def test_config_path_uses_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert filescanw.config_path() == tmp_path / "ui" / "filescanw.json"


def test_config_path_defaults_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FCMD_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows 的 Path.home() 走 USERPROFILE
    assert filescanw.config_path() == tmp_path / ".fcmd" / "ui" / "filescanw.json"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = filescanw.FilescanSettings(
        paths=("F:/data", "F:/logs"),
        recursive=False,
        max_depth=2,
        follow_symlinks=True,
        include_ext=".txt;.log",
        exclude_ext=".min.js",
        min_size=10,
        max_size=1000,
        skip_binary=False,
        max_file_bytes=4096,
        workers=8,
        context_chars=60,
        ruleset=disable_all(builtin_ruleset()),
        filter_rule="cn_phone",
        filter_path="customer",
        geometry="1000x700+10+20",
    )
    filescanw.save_settings(settings, path)
    assert filescanw.load_settings(path) == settings


def test_load_missing_or_invalid_returns_defaults(tmp_path: Path) -> None:
    assert filescanw.load_settings(tmp_path / "missing.json") == filescanw.FilescanSettings()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert filescanw.load_settings(broken) == filescanw.FilescanSettings()
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    assert filescanw.load_settings(array) == filescanw.FilescanSettings()


def test_settings_from_mapping_tolerates_bad_values() -> None:
    raw: dict[str, object] = {
        "paths": ["F:/data", 123, None],
        "recursive": 0,
        "max_depth": -5,
        "follow_symlinks": "yes",
        "include_ext": None,
        "exclude_ext": [".tmp"],
        "min_size": -1,
        "max_size": "big",
        "skip_binary": "no",
        "max_file_bytes": 0,
        "workers": "many",
        "context_chars": True,
        "geometry": "not-a-geometry",
    }
    settings = filescanw.settings_from_mapping(raw)
    assert settings.paths == ("F:/data",)
    assert settings.recursive is True  # 非布尔值回落默认
    assert settings.max_depth == 0
    assert settings.follow_symlinks is False
    assert settings.include_ext == ""
    assert settings.exclude_ext == ""
    assert settings.min_size == 0
    assert settings.max_size == 0
    assert settings.skip_binary is True
    assert settings.max_file_bytes == 1
    assert settings.workers == 4
    assert settings.context_chars == 40
    assert settings.geometry is None
    assert settings.ruleset == builtin_ruleset()


def test_settings_from_mapping_bad_paths_container() -> None:
    assert filescanw.settings_from_mapping({"paths": "F:/data"}).paths == ()


def test_settings_from_mapping_keeps_valid_ruleset() -> None:
    ruleset = builtin_ruleset()
    custom = replace(
        ruleset,
        expressions=(Expression(id="custom", name="自定义", expression="email and not cn_phone"),),
    )
    settings = filescanw.settings_from_mapping({"ruleset": filescanw.ruleset_to_mapping(custom)})
    assert settings.ruleset == custom


def test_settings_from_mapping_broken_ruleset_falls_back() -> None:
    settings = filescanw.settings_from_mapping({"ruleset": {"rules": [{"id": "bad id!", "name": ""}]}})
    assert settings.ruleset == builtin_ruleset()
    assert filescanw.settings_from_mapping({"ruleset": "oops"}).ruleset == builtin_ruleset()


def test_settings_mapping_roundtrip() -> None:
    settings = filescanw.FilescanSettings(paths=("F:/data",), skip_binary=False, workers=2)
    mapping = filescanw.settings_to_mapping(settings)
    assert mapping["paths"] == ["F:/data"]
    assert mapping["skip_binary"] is False
    assert mapping["ruleset"]["name"] == settings.ruleset.name
    assert filescanw.settings_from_mapping(mapping) == settings


def test_scan_task_roundtrip_and_name_disambiguation() -> None:
    """任务序列化保留 id / 历史字段；同名目录新增时自动加后缀。"""
    first = filescanw.ScanTask(
        id="abc123",
        path="/data/logs",
        name="日志扫描",
        last_scanned_at="2026-01-02 03:04",
        last_summary="命中 2 个文件 / 3 条",
        last_status="ok",
    )
    assert filescanw.task_from_mapping(filescanw.task_to_mapping(first)) == first
    existing = replace(first, name="logs")
    assert filescanw.new_scan_task("/data/logs", existing=(existing,)).display_name == "logs-2"
    assert filescanw.new_scan_task("/data/logs").display_name == "logs"


def test_legacy_paths_migrate_to_tasks_and_paths_stay_in_sync() -> None:
    """旧设置只有 ``paths``：读取时迁移成任务，写回时同时保留任务与 paths。"""
    settings = filescanw.settings_from_mapping({"paths": ["F:/data", "F:/logs"]})
    assert [item.path for item in settings.tasks] == ["F:/data", "F:/logs"]
    assert [item.display_name for item in settings.tasks] == ["data", "logs"]
    mapping = filescanw.settings_to_mapping(settings)
    assert mapping["paths"] == ["F:/data", "F:/logs"]
    assert [item["id"] for item in mapping["tasks"]] == ["legacy-0", "legacy-1"]
    assert filescanw.settings_from_mapping(mapping) == settings


def test_previous_instances_key_migrates_to_tasks() -> None:
    """上一版写出的 ``instances`` 键仍能读入，新写盘统一用 ``tasks``。"""
    settings = filescanw.settings_from_mapping(
        {
            "instances": [
                {"id": "old-1", "path": "F:/data", "name": "旧任务", "last_status": "ok"},
            ]
        }
    )
    assert [item.id for item in settings.tasks] == ["old-1"]
    assert [item.display_name for item in settings.tasks] == ["旧任务"]
    assert "instances" not in filescanw.settings_to_mapping(settings)


def test_settings_whitelist_groups_roundtrip() -> None:
    groups = (("文档", (".docx", ".pdf")), ("自定义", (".abc",)))
    settings = filescanw.FilescanSettings(whitelist_groups=groups)
    mapping = filescanw.settings_to_mapping(settings)
    assert mapping["whitelist_groups"] == [["文档", [".docx", ".pdf"]], ["自定义", [".abc"]]]
    assert filescanw.settings_from_mapping(mapping).whitelist_groups == groups


def test_settings_from_mapping_sanitizes_whitelist_groups() -> None:
    raw: dict[str, object] = {
        "whitelist_groups": [
            ["文档", [".docx", 123, ""]],
            ["文档", [".dup"]],
            ["", [".x"]],
            ["坏", ["日志", ".ok"]],
            "oops",
            ["空", []],
            ["结构不对", [".a"], "多余"],
        ]
    }
    assert filescanw.settings_from_mapping(raw).whitelist_groups == (
        ("文档", (".docx",)),
        ("坏", (".ok",)),
    )
    assert filescanw.settings_from_mapping({"whitelist_groups": "oops"}).whitelist_groups == ()


def test_resolve_geometry_restores_saved() -> None:
    assert filescanw.resolve_geometry("1000x700+10+20", 1920, 1080) == "1000x700+10+20"


def test_resolve_geometry_clamps_and_falls_back() -> None:
    assert filescanw.resolve_geometry("5000x5000+9999+9999", 1920, 1080) == "1920x1080+0+0"
    assert filescanw.resolve_geometry("garbage", 1920, 1080) == filescanw.DEFAULT_GEOMETRY
    assert filescanw.resolve_geometry(None, 1920, 1080) == filescanw.DEFAULT_GEOMETRY


def test_settings_use_cache_roundtrip() -> None:
    settings = filescanw.FilescanSettings(use_cache=False)
    mapping = filescanw.settings_to_mapping(settings)
    assert mapping["use_cache"] is False
    assert filescanw.settings_from_mapping(mapping).use_cache is False
    assert filescanw.settings_from_mapping({"use_cache": "no"}).use_cache is True


def test_settings_dialog_dirs_roundtrip() -> None:
    """对话框上次目录随设置持久化；缺失 / 非法类型回落空串。"""
    settings = filescanw.FilescanSettings(
        last_task_dir="D:/scan",
        last_import_dir="D:/in",
        last_export_dir="D:/out",
        active_page="results",
        result_columns=(260, 60, 52),
    )
    mapping = filescanw.settings_to_mapping(settings)
    assert mapping["last_task_dir"] == "D:/scan"
    assert mapping["last_import_dir"] == "D:/in"
    assert mapping["last_export_dir"] == "D:/out"
    loaded = filescanw.settings_from_mapping(mapping)
    assert (loaded.last_task_dir, loaded.last_import_dir, loaded.last_export_dir) == ("D:/scan", "D:/in", "D:/out")
    assert loaded.active_page == "results"
    assert loaded.result_columns == (260, 60, 52)
    fallback = filescanw.settings_from_mapping({"last_export_dir": 3})
    assert fallback.last_export_dir == ""


def test_new_expression_id_avoids_rule_and_expression_ids() -> None:
    ruleset = builtin_ruleset()
    assert filescanw.new_expression_id(ruleset) == "expr"
    taken = replace(ruleset, expressions=(Expression(id="expr", name="E", expression="email"),))
    assert filescanw.new_expression_id(taken) == "expr-2"
    clash = replace(taken, expressions=(*taken.expressions, Expression(id="expr-2", name="E2", expression="email")))
    assert filescanw.new_expression_id(clash) == "expr-3"


def test_parse_whitelist_text_handles_comments_and_separators() -> None:
    text = "# 文档\n.docx .xlsx\n\n.pdf;.md, txt\n  # 注释行\n*.log\n"
    assert filescanw.parse_whitelist_text(text) == (".docx", ".xlsx", ".pdf", ".md", ".txt", ".log")


def test_parse_whitelist_text_empty() -> None:
    assert filescanw.parse_whitelist_text("") == ()
    assert filescanw.parse_whitelist_text("# 只有注释\n") == ()


def test_group_whitelist_covers_defaults_and_marks_unknown() -> None:
    from fcmd.apps.filescan._engine import DEFAULT_SCAN_EXTENSIONS

    groups = filescanw.group_whitelist()
    labels = [label for label, _ in groups]
    assert labels[0] == "文档"
    flat = [ext for _, items in groups for ext in items]
    assert set(flat) == set(DEFAULT_SCAN_EXTENSIONS)
    assert len(flat) == len(set(flat))

    extended = filescanw.group_whitelist((*DEFAULT_SCAN_EXTENSIONS, "custom"))
    assert extended[-1] == ("其他", (".custom",))


def test_group_whitelist_normalizes_and_dedupes() -> None:
    assert filescanw.group_whitelist(("log", ".LOG", "txt")) == (("文本/日志", (".txt", ".log")),)
    assert filescanw.group_whitelist(()) == ()


def test_parse_and_validate_ext_line_splits_on_comma() -> None:
    assert filescanw.parse_and_validate_ext_line(".log, txt，PY") == ((".log", ".txt", ".py"), ())
    assert filescanw.parse_and_validate_ext_line("") == ((), ())
    assert filescanw.parse_and_validate_ext_line(".log, , .log") == ((".log",), ())


def test_parse_and_validate_ext_line_reports_invalid_entries() -> None:
    valid, invalid = filescanw.parse_and_validate_ext_line(".log, 日志, p y, *, ..py")
    assert valid == (".log", "*")
    assert invalid == ("日志", "p y", "..py")


def test_validate_exts_accepts_star_and_wildcard_forms() -> None:
    assert filescanw.validate_exts(["LOG", "*.log", "log"]) == ((".log",), ())
    assert filescanw.validate_exts(["*"]) == (("*",), ())


def test_run_gui_delegates_to_qt_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []
    fake = types.ModuleType("fcmd.apps.filescan._filescanw_qt")
    fake.run_qt = lambda settings_path=None: calls.append(settings_path)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fcmd.apps.filescan._filescanw_qt", fake)
    path = tmp_path / "settings.json"
    filescanw.run_gui(path)
    assert calls == [path]


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(filescanw, "run_gui", lambda: called.append(True))
    filescanw.launch_cmd()
    assert called == [True]


# ---------------------------------------------------------------------- #
# 状态栏口径：与 CLI 共用 scan_summary / no_hit_hint
# ---------------------------------------------------------------------- #
def _status_result(stats: ScanStats) -> ScanResult:
    return ScanResult(roots=(Path("D:/data"),), hits=[], skipped=[], errors=[], stats=stats)


def test_scan_task_summary_is_concise() -> None:
    """任务行摘要只给命中数字；无命中 / 取消各给一句短文案。"""
    hits = ScanStats(hit_files=2, hit_count=3)
    assert filescanw.scan_task_summary(_status_result(hits)) == "命中 2 文件 / 3 条"
    assert filescanw.scan_task_summary(_status_result(ScanStats())) == "无命中"
    stopped = ScanStats(hit_files=1, hit_count=1, cancelled=True)
    assert filescanw.scan_task_summary(_status_result(stopped)) == "已停止 · 命中 1 文件 / 1 条"


def test_scan_status_text_separates_scope_from_read_skips() -> None:
    """范围外文件不再被 GUI 笼统称作「跳过」，0 命中时给出诊断。"""
    stats = ScanStats(
        files_seen=4909,
        files_scanned=24,
        files_skipped=4885,
        files_skipped_extension=4881,
        files_skipped_size_time=0,
        files_skipped_no_text=4,
        hit_files=0,
        hit_count=0,
        elapsed=1.5,
    )
    text = filescanw.scan_status_text(_status_result(stats))
    assert "\n" not in text
    assert text.startswith(scan_summary(stats, compact=True))
    assert "范围外 4881（后缀白名单 4881 / 大小或时间 0）" in text
    assert "读取后跳过 4（二进制 0 / 其他 0 / 无文本 4）" in text
    assert "未发现命中" in text
    assert "检查后缀白名单与规则启用状态" in text


def test_scan_status_text_omits_hint_when_hits_found() -> None:
    stats = ScanStats(files_seen=3, files_scanned=3, hit_files=1, hit_count=2, elapsed=0.1)
    text = filescanw.scan_status_text(_status_result(stats))
    assert text == scan_summary(stats, compact=True)
    assert "未发现命中" not in text


def test_scan_status_text_hint_survives_full_cache_reuse() -> None:
    """全量缓存复用、0 命中时仍要给出诊断，不能让人误以为「全是跳过」。"""
    stats = ScanStats(files_seen=3, files_scanned=0, files_reused=3, hit_files=0, hit_count=0, elapsed=0.01)
    text = filescanw.scan_status_text(_status_result(stats))
    assert "缓存复用 3" in text
    assert "未发现命中" in text


def test_scan_progress_text_matches_status_bar_style() -> None:
    """状态栏进度按文件数给百分比 / 已扫 / 速度 / 剩余，与 wslmanw 下载口径对齐。"""
    text = filescanw.scan_progress_text(420, 1000, 12.0)
    assert text.startswith("扫描 42% · 420 / 1000 个文件")
    assert "12 个/s" in text
    assert "剩余" in text


def test_scan_progress_text_without_total_or_rate() -> None:
    """总量未知时只给已扫数；速度未采样到时不追加速度与剩余。"""
    assert filescanw.scan_progress_text(7, 0) == "扫描 7 个文件"
    assert filescanw.scan_progress_text(0, 0) == "扫描 0 个文件"
    assert "个/s" not in filescanw.scan_progress_text(3, 10)
