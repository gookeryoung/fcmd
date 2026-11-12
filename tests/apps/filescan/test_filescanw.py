"""filescanw 测试：设置解析 / 持久化、geometry、表达式应用与启动入口。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from fcmd.apps.filescan import filescanw
from fcmd.apps.filescan._rules import builtin_ruleset, disable_all
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.gui import window as _guiwindow


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
    custom = replace(ruleset, expression="email and not cn_phone")
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


def test_apply_expression_accepts_valid_expression() -> None:
    ruleset = builtin_ruleset()
    updated = filescanw.apply_expression(ruleset, "  email OR cn_phone ")
    assert updated.expression == "email OR cn_phone"
    assert updated.rules == ruleset.rules


def test_apply_expression_clears_to_default() -> None:
    updated = filescanw.apply_expression(builtin_ruleset(), "   ")
    assert updated.expression == ""


def test_apply_expression_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        filescanw.apply_expression(builtin_ruleset(), "(email")
    with pytest.raises(ValueError):
        filescanw.apply_expression(builtin_ruleset(), "unknown_rule")


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


def test_run_gui_delegates_to_shared_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def fake_run_window(factory: object, message: str) -> None:
        recorded["factory"] = factory
        recorded["message"] = message

    monkeypatch.setattr(_guiwindow, "run_window", fake_run_window)
    filescanw.run_gui(Path("custom.json"))
    assert callable(recorded["factory"])
    assert recorded["message"] == "敏感内容扫描"


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(filescanw, "run_gui", lambda: called.append(True))
    filescanw.launch_cmd()
    assert called == [True]
