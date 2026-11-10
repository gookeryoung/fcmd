"""envw 工具测试。

验证 ``fcmd.tools.system.envw`` 模块：
- 工具注册
- 作用域可用性 / 变量名与值校验
- PATH 条目解析与拼接
- 变量引用展开（循环引用收敛）
- POSIX 启动文件解析与重写
- 作用域读取 / 保存后端
- JSON 备份与恢复
- 设置文件读写与 geometry 恢复
- GUI 启动入口的降级行为
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.system import envw
from fcmd.tools.system.envw import (
    REG_EXPAND_SZ,
    REG_SZ,
    SCOPE_PROCESS,
    SCOPE_SYSTEM,
    SCOPE_USER,
    EnvSettings,
    available_scopes,
    build_backup,
    config_path,
    default_path_separator,
    expand_value,
    format_export_line,
    join_path_entries,
    load_backup,
    load_settings,
    parse_backup,
    parse_export_line,
    parse_path_entries,
    parse_profile_text,
    read_profile_variables,
    read_scope,
    registry_value_type,
    render_profile_text,
    resolve_geometry,
    save_backup,
    save_scope,
    save_settings,
    scope_writable,
    valid_geometry,
    validate_name,
    validate_value,
    write_profile_variables,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """envw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["envw"]) == {None}


# ============================================================================ #
# 作用域与校验
# ============================================================================ #
class TestScopes:
    """平台作用域判定。"""

    def test_windows_scopes(self) -> None:
        assert available_scopes("win32") == (SCOPE_USER, SCOPE_SYSTEM, SCOPE_PROCESS)

    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    def test_posix_scopes(self, platform: str) -> None:
        assert available_scopes(platform) == (SCOPE_USER, SCOPE_PROCESS)

    def test_scope_writable(self) -> None:
        assert scope_writable(SCOPE_USER, "linux")
        assert not scope_writable(SCOPE_PROCESS, "linux")
        assert scope_writable(SCOPE_SYSTEM, "win32")
        assert not scope_writable(SCOPE_SYSTEM, "linux")


class TestValidation:
    """变量名与值校验。"""

    @pytest.mark.parametrize("name", ["PATH", "_PRIVATE", "A1", "ProgramFiles(x86)", "A-B"])
    def test_valid_windows_names(self, name: str) -> None:
        assert validate_name(name, platform="win32") is None

    @pytest.mark.parametrize(("name", "message"), [("", "不能为空"), ("   ", "不能为空"), (" A", "空白")])
    def test_invalid_blank(self, name: str, message: str) -> None:
        assert message in (validate_name(name, platform="linux") or "")

    def test_name_with_equals(self) -> None:
        assert "=" in (validate_name("A=B", platform="win32") or "")

    def test_name_with_nul(self) -> None:
        assert "NUL" in (validate_name("A\x00B", platform="win32") or "")

    @pytest.mark.parametrize("name", ["1ABC", "A-B", "中文"])
    def test_posix_name_rules(self, name: str) -> None:
        assert validate_name(name, platform="linux") is not None

    def test_posix_valid_name(self) -> None:
        assert validate_name("JAVA_HOME", platform="linux") is None

    def test_value_rules(self) -> None:
        assert validate_value("") is None
        assert validate_value("C:/x;D:/y") is None
        assert validate_value("a\x00b") is not None
        assert validate_value("a\nb") is not None
        assert validate_value("a\rb") is not None


# ============================================================================ #
# PATH 条目
# ============================================================================ #
class TestPathEntries:
    """PATH 条目拆分 / 拼接。"""

    def test_default_separator(self) -> None:
        assert default_path_separator("win32") == ";"
        assert default_path_separator("linux") == ":"

    def test_parse_posix(self) -> None:
        assert parse_path_entries("/usr/bin:/bin::/opt/x", ":") == ["/usr/bin", "/bin", "/opt/x"]

    def test_parse_windows(self) -> None:
        assert parse_path_entries(r"C:\Windows;C:\Tools;", ";") == [r"C:\Windows", r"C:\Tools"]

    def test_parse_strips_quotes_and_spaces(self) -> None:
        assert parse_path_entries(' "/opt/a" ;  /opt/b ', ";") == ["/opt/a", "/opt/b"]

    def test_join(self) -> None:
        assert join_path_entries([" /a ", "", "/b"], ":") == "/a:/b"
        assert join_path_entries(["C:\\A", "C:\\B"], ";") == "C:\\A;C:\\B"

    def test_registry_value_type(self) -> None:
        assert registry_value_type("%JAVA_HOME%\\bin") == REG_EXPAND_SZ
        assert registry_value_type("/usr/bin") == REG_SZ


# ============================================================================ #
# 变量引用展开
# ============================================================================ #
class TestExpandValue:
    """变量引用展开。"""

    def test_posix_braced_and_plain(self) -> None:
        variables = {"HOME": "/home/u", "BIN": "${HOME}/bin"}
        assert expand_value("${HOME}/bin", variables, platform="linux") == "/home/u/bin"
        assert expand_value("$BIN", variables, platform="linux") == "/home/u/bin"

    def test_posix_unknown_kept(self) -> None:
        assert expand_value("$NOPE/x", {}, platform="linux") == "$NOPE/x"

    def test_windows_percent(self) -> None:
        assert expand_value("%JAVA_HOME%\\bin", {"JAVA_HOME": "C:\\Java"}, platform="win32") == "C:\\Java\\bin"

    def test_windows_case_insensitive(self) -> None:
        assert expand_value("%java_home%\\bin", {"JAVA_HOME": "C:\\Java"}, platform="win32") == "C:\\Java\\bin"

    def test_cycle_stops(self) -> None:
        result = expand_value("%A%", {"A": "%B%", "B": "%A%"}, platform="win32")
        assert result in {"%A%", "%B%"}

    def test_plain_text(self) -> None:
        assert expand_value("abc", {}, platform="linux") == "abc"


# ============================================================================ #
# POSIX 启动文件
# ============================================================================ #
class TestProfileText:
    """``export`` 行解析与重写。"""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ('export A="x"', ("A", "x")),
            ("export B='x y'", ("B", "x y")),
            ("export C=plain", ("C", "plain")),
            ('  export D="a\\"b\\$c"', ("D", 'a"b$c')),
            ('export E=""', ("E", "")),
        ],
    )
    def test_parse_export_line(self, line: str, expected: tuple[str, str]) -> None:
        assert parse_export_line(line) == expected

    @pytest.mark.parametrize("line", ["# comment", "A=x", "export 1A=x", ""])
    def test_parse_non_export(self, line: str) -> None:
        assert parse_export_line(line) is None

    def test_parse_profile_text(self) -> None:
        text = '# c\nexport A="1"\nexport B=2\nOTHER=3\n'
        assert parse_profile_text(text) == {"A": "1", "B": "2"}

    def test_format_roundtrip(self) -> None:
        value = 'a"b$c`d\\e f'
        parsed = parse_export_line(format_export_line("A", value))
        assert parsed == ("A", value)

    def test_render_replaces_removes_and_appends(self) -> None:
        text = '# comment\nexport OLD="x"\nexport KEEP="1"\n'
        variables = {"OLD": "new", "KEEP": "1", "NEW": "2"}
        result = render_profile_text(text, variables)
        assert result == '# comment\nexport OLD="new"\nexport KEEP="1"\nexport NEW="2"\n'

    def test_render_removes(self) -> None:
        text = 'export A="1"\nexport B="2"\n'
        assert render_profile_text(text, {"A": "1"}, removed=["B"]) == 'export A="1"\n'

    def test_render_empty_text(self) -> None:
        assert render_profile_text("", {"A": "1"}) == 'export A="1"\n'
        assert render_profile_text("", {}) == ""

    def test_read_profile_variables(self, tmp_path: Path) -> None:
        path = tmp_path / "profile"
        path.write_text('export A="1"\nexport B=2\n', encoding="utf-8")
        assert read_profile_variables(path) == {"A": "1", "B": "2"}

    def test_read_profile_missing(self, tmp_path: Path) -> None:
        assert read_profile_variables(tmp_path / "missing") == {}

    def test_read_profile_undecodable(self, tmp_path: Path) -> None:
        path = tmp_path / "profile"
        path.write_bytes(b"\xff\xfe\x00")
        assert read_profile_variables(path) == {}

    def test_write_creates_parent_and_preserves_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "profile"
        original = {"OLD": "x", "KEEP": "1"}
        path.parent.mkdir()
        path.write_text('# keep\nexport OLD="x"\nexport KEEP="1"\n', encoding="utf-8")
        write_profile_variables(path, {"KEEP": "2", "NEW": "3"}, original)
        assert path.read_text(encoding="utf-8") == '# keep\nexport KEEP="2"\nexport NEW="3"\n'

    def test_write_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "profile"
        write_profile_variables(path, {"A": "1"}, {})
        assert parse_profile_text(path.read_text(encoding="utf-8")) == {"A": "1"}


# ============================================================================ #
# 作用域读取 / 保存
# ============================================================================ #
class TestScopeBackend:
    """跨平台作用域后端。"""

    def test_read_process_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVW_TEST", "1")
        assert read_scope(SCOPE_PROCESS, platform="linux")["ENVW_TEST"] == "1"

    def test_read_user_scope(self, tmp_path: Path) -> None:
        path = tmp_path / "profile"
        path.write_text('export A="1"\n', encoding="utf-8")
        assert read_scope(SCOPE_USER, platform="linux", profile_path=path) == {"A": "1"}

    def test_read_unavailable_scope(self) -> None:
        with pytest.raises(ValueError, match="不支持"):
            read_scope(SCOPE_SYSTEM, platform="linux")

    def test_read_windows_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(envw, "_read_windows_scope", lambda scope: {scope: "x"})
        assert read_scope(SCOPE_USER, platform="win32") == {SCOPE_USER: "x"}

    def test_save_user_scope_writes_and_syncs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "profile"
        path.write_text('export A="1"\nexport OLD="x"\n', encoding="utf-8")
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("OLD", "x")
        original = {"A": "1", "OLD": "x"}
        variables = {"A": "2", "NEW": "y"}
        save_scope(SCOPE_USER, variables, original, platform="linux", profile_path=path)
        assert parse_profile_text(path.read_text(encoding="utf-8")) == variables
        assert os.environ["A"] == "2"
        assert os.environ["NEW"] == "y"
        assert "OLD" not in os.environ

    def test_save_process_scope_rejected(self) -> None:
        with pytest.raises(ValueError, match="只读"):
            save_scope(SCOPE_PROCESS, {}, {}, platform="linux")

    def test_save_unavailable_scope(self) -> None:
        with pytest.raises(ValueError, match="不支持"):
            save_scope(SCOPE_SYSTEM, {}, {}, platform="linux")

    def test_save_windows_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_write(scope: str, variables: Any, original: Any) -> None:
            calls.append((scope, dict(variables)))

        monkeypatch.setattr(envw, "_write_windows_scope", fake_write)
        save_scope(SCOPE_USER, {"A": "1"}, {}, platform="win32")
        assert calls == [(SCOPE_USER, {"A": "1"})]


# ============================================================================ #
# 备份 / 恢复
# ============================================================================ #
class TestBackup:
    """JSON 备份。"""

    def test_build_backup(self) -> None:
        payload = build_backup({SCOPE_USER: {"A": "1"}}, created="2026-01-01T00:00:00")
        assert payload["tool"] == "envw"
        assert payload["version"] == 1
        assert payload["created"] == "2026-01-01T00:00:00"
        assert payload["scopes"] == {SCOPE_USER: {"A": "1"}}

    def test_parse_backup(self) -> None:
        assert parse_backup({"scopes": {"user": {"A": "1", "B": 2}}}) == {"user": {"A": "1"}}
        assert parse_backup({"scopes": {"user": {"A": "1"}}}) == {"user": {"A": "1"}}

    @pytest.mark.parametrize("raw", [[], {}, {"scopes": []}, {"scopes": {1: {}}}, {"scopes": {"user": []}}])
    def test_parse_backup_invalid(self, raw: Any) -> None:
        with pytest.raises(ValueError):
            parse_backup(raw)

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "backup.json"
        scopes = {SCOPE_USER: {"A": "1"}, SCOPE_SYSTEM: {"B": "2"}}
        save_backup(path, scopes, created="2026-01-01T00:00:00")
        assert load_backup(path) == scopes
        assert json.loads(path.read_text(encoding="utf-8"))["created"] == "2026-01-01T00:00:00"

    def test_load_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="无法读取"):
            load_backup(tmp_path / "missing.json")

    def test_load_corrupt(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{oops", encoding="utf-8")
        with pytest.raises(ValueError, match="无法读取"):
            load_backup(path)


# ============================================================================ #
# 设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "envw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "envw.json"

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "envw.json"
        expected = EnvSettings(scope=SCOPE_SYSTEM, geometry="900x600+10+20")
        save_settings(expected, path)
        assert load_settings(path) == expected

    def test_load_missing(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == EnvSettings()

    def test_load_corrupt_and_wrong_types(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{oops", encoding="utf-8")
        assert load_settings(path) == EnvSettings()
        path.write_text('{"scope": "nope", "geometry": "bad"}', encoding="utf-8")
        assert load_settings(path) == EnvSettings()
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == EnvSettings()

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(EnvSettings(), tmp_path / "x.json")  # 不应抛出


class TestGeometry:
    """窗口 geometry 校验与恢复。"""

    @pytest.mark.parametrize("text", ["900x600", "900x600+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "900", "900x600abc", "900 x 600"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None

    def test_keeps_visible_position(self) -> None:
        assert resolve_geometry("1000x700+100+80", 1920, 1080) == "1000x700+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert resolve_geometry("1000x700+5000+5000", 1920, 1080) == "1000x700+920+380"

    def test_enforces_minimum_size(self) -> None:
        assert resolve_geometry("200x100+0+0", 1920, 1080) == "820x560+0+0"

    def test_falls_back_to_default(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == "1000x700+220+110"
        assert resolve_geometry("bad", 1920, 1080) == "1000x700+220+110"

    def test_clamps_default_into_small_screen(self) -> None:
        assert resolve_geometry(None, 800, 500) == "800x500+0+0"


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(envw, "EnvWindow", fake_window)
        envw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(envw, "EnvWindow", lambda _path: _Window())
        envw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(envw, "run_gui", lambda: calls.append(True))
        envw.launch_cmd()
        assert calls == [True]
