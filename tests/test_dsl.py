"""命令定义 DSL 测试。

覆盖 ``fcmd.dsl`` 包：
- 声明解析与校验（decl.py）
- 平台命令选定与 ToolSpec 合成（synth.py）
- 内置/用户级文件加载（loader.py）
- console script 通用入口（entry.py）
- 工具发现集成与 run_tool 执行（_discovery.py 集成后）
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fcmd.apis._tool_args import _build_parser_for_tool
from fcmd.cli import _discovery as discovery_mod
from fcmd.cli._common import _BUILTIN_COMMANDS
from fcmd.dsl import (
    CommandDecl,
    CommandDeclError,
    build_tool_spec,
    builtin_command_decls,
    infer_tool_name,
    parse_command_table,
    select_platform_cmd,
    user_command_decls,
)
from fcmd.dsl.decl import _RESERVED_NAMES, ParamDecl


# ============================================================================ #
# 声明解析与校验（decl.py）
# ============================================================================ #
class TestDeclParsing:
    """TOML 表 → CommandDecl 的解析与校验。"""

    @staticmethod
    def _clr_table() -> dict[str, Any]:
        """clr 的最小合法声明表。"""
        return {"help": "清屏（跨平台）", "win": {"cmd": "cls"}, "unix": {"cmd": ["clear"]}}

    def test_parse_minimal(self) -> None:
        """最小合法声明解析成功，平台命令归一（list → tuple）。"""
        decl = parse_command_table("clr", self._clr_table())
        assert decl.name == "clr"
        assert decl.help == "清屏（跨平台）"
        assert decl.win_cmd == "cls"
        assert decl.unix_cmd == ("clear",)
        assert decl.cmd is None
        assert decl.description == ""
        assert decl.aliases == ()
        assert decl.hidden is False

    def test_parse_full_options(self) -> None:
        """全部可选键（description/aliases/hidden/cmd）解析成功。"""
        table = self._clr_table()
        table.update({"description": "清屏", "aliases": ["clear-screen"], "hidden": True, "cmd": "tput reset"})
        decl = parse_command_table("clr", table)
        assert decl.description == "清屏"
        assert decl.aliases == ("clear-screen",)
        assert decl.hidden is True
        assert decl.cmd == "tput reset"

    def test_missing_help(self) -> None:
        """help 缺失报错。"""
        table = self._clr_table()
        del table["help"]
        with pytest.raises(CommandDeclError, match="help"):
            parse_command_table("clr", table)

    def test_empty_help(self) -> None:
        """help 为空串或空白串报错。"""
        with pytest.raises(CommandDeclError, match="help"):
            parse_command_table("clr", {"help": "   ", "cmd": "x"})

    def test_unknown_top_key(self) -> None:
        """未知顶层键报错（防拼写错误，如 hepl）。"""
        table = self._clr_table()
        table["hepl"] = "typo"
        with pytest.raises(CommandDeclError, match=r"未知键.*hepl"):
            parse_command_table("clr", table)

    @pytest.mark.parametrize("reserved", ["fcmd", "graph", "info", "yaml", "env"])
    def test_reserved_name(self, reserved: str) -> None:
        """保留名（fcmd 与内建命令）不可用作命令名。"""
        with pytest.raises(CommandDeclError, match="保留名"):
            parse_command_table(reserved, {"help": "x", "cmd": "x"})

    def test_reserved_names_match_builtins(self) -> None:
        """dsl 保留名集合与 cli._common._BUILTIN_COMMANDS 保持同步。"""
        expected = {"fcmd"} | set(_BUILTIN_COMMANDS)
        assert expected == _RESERVED_NAMES

    @pytest.mark.parametrize("bad_name", ["Clr", "1clr", "-clr", "", "clr中文"])
    def test_bad_tool_name(self, bad_name: str) -> None:
        """非法命令名（大写/数字开头/连字符开头/空/非 ASCII）报错。"""
        with pytest.raises(CommandDeclError, match="命令名"):
            parse_command_table(bad_name, {"help": "x", "cmd": "x"})

    def test_no_cmd_at_all(self) -> None:
        """win.cmd / unix.cmd / cmd 全缺报错。"""
        with pytest.raises(CommandDeclError, match="至少一个"):
            parse_command_table("clr", {"help": "x"})

    def test_platform_not_table(self) -> None:
        """win 不是表（直接写字符串）报错并提示正确写法。"""
        with pytest.raises(CommandDeclError, match=r"win.*须是表"):
            parse_command_table("clr", {"help": "x", "win": "cls"})

    def test_platform_unknown_key(self) -> None:
        """win 表内未知键报错。"""
        with pytest.raises(CommandDeclError, match=r"win.*未知键.*shell"):
            parse_command_table("clr", {"help": "x", "win": {"cmd": "cls", "shell": True}})

    @pytest.mark.parametrize("bad_cmd", [42, [], ["ok", ""], ["a", 1], ""])
    def test_bad_cmd_value(self, bad_cmd: Any) -> None:
        """cmd 值类型非法（非 str/空串/空数组/非全 str 数组）报错。"""
        with pytest.raises(CommandDeclError, match="cmd"):
            parse_command_table("clr", {"help": "x", "cmd": bad_cmd})

    def test_bad_aliases(self) -> None:
        """aliases 非字符串数组报错。"""
        with pytest.raises(CommandDeclError, match="aliases"):
            parse_command_table("clr", {"help": "x", "cmd": "x", "aliases": "clear"})

    def test_bad_hidden(self) -> None:
        """hidden 非布尔值报错。"""
        with pytest.raises(CommandDeclError, match="hidden"):
            parse_command_table("clr", {"help": "x", "cmd": "x", "hidden": "yes"})

    def test_bad_description(self) -> None:
        """description 非字符串报错。"""
        with pytest.raises(CommandDeclError, match="description"):
            parse_command_table("clr", {"help": "x", "cmd": "x", "description": 42})


# ============================================================================ #
# 平台命令选定与 ToolSpec 合成（synth.py）
# ============================================================================ #
class TestPlatformSelection:
    """select_platform_cmd 的平台分支逻辑（纯函数直测双平台）。"""

    @staticmethod
    def _decl(**kwargs: Any) -> CommandDecl:
        """构造测试用声明（默认三键齐备）。"""
        base: dict[str, Any] = {
            "name": "clr",
            "help": "x",
            "win_cmd": "cls",
            "unix_cmd": ("clear",),
            "cmd": "fallback",
        }
        base.update(kwargs)
        return CommandDecl(**base)

    def test_win32_selects_win(self) -> None:
        """win32 平台命中 win_cmd。"""
        assert select_platform_cmd(self._decl(), "win32") == "cls"

    @pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd"])
    def test_unix_platforms_select_unix(self, platform: str) -> None:
        """非 win32 平台（linux/darwin/freebsd）命中 unix_cmd。"""
        assert select_platform_cmd(self._decl(), platform) == ("clear",)

    def test_generic_cmd_only(self) -> None:
        """仅提供 cmd 时所有平台回退通用命令。"""
        decl = self._decl(win_cmd=None, unix_cmd=None)
        assert select_platform_cmd(decl, "win32") == "fallback"
        assert select_platform_cmd(decl, "linux") == "fallback"

    def test_win_missing_falls_back(self) -> None:
        """win 分支缺失时 win32 回退 cmd。"""
        decl = self._decl(win_cmd=None)
        assert select_platform_cmd(decl, "win32") == "fallback"
        assert select_platform_cmd(decl, "linux") == ("clear",)

    def test_unix_missing_falls_back(self) -> None:
        """unix 分支缺失时非 win32 平台回退 cmd。"""
        decl = self._decl(unix_cmd=None)
        assert select_platform_cmd(decl, "linux") == "fallback"

    def test_platform_unavailable(self) -> None:
        """平台分支与回退均未覆盖当前平台时报错。"""
        decl = self._decl(cmd=None, unix_cmd=None)
        with pytest.raises(CommandDeclError, match="无可用命令"):
            select_platform_cmd(decl, "linux")


class TestSpecSynthesis:
    """build_tool_spec：声明 → ToolSpec 与签名合成。"""

    def test_build_tool_spec_fields(self) -> None:
        """ToolSpec 字段完整映射（aliases 在发现层注册，不进 spec）。"""
        decl = CommandDecl(
            name="clr",
            help="清屏",
            win_cmd="cls",
            unix_cmd=("clear",),
            description="desc",
            aliases=("cs",),
            hidden=True,
        )
        spec = build_tool_spec(decl, platform="linux")
        assert spec.name == "clr"
        assert spec.subcommand is None
        assert spec.help == "清屏"
        assert spec.description == "desc"
        assert spec.hidden is True
        assert spec.cmd == ("clear",)

    def test_synthesized_func_signature(self) -> None:
        """合成函数无参签名可被 inspect/signature 与 get_type_hints 处理。"""
        import inspect
        import typing

        spec = build_tool_spec(CommandDecl(name="clr", help="h", cmd="x"), platform="win32")
        sig = inspect.signature(spec.func)
        assert list(sig.parameters) == []
        # 无参数注解（return: None 是合成函数自身的返回注解，不影响参数推导）
        assert {k: v for k, v in typing.get_type_hints(spec.func).items() if k != "return"} == {}

    def test_synthesized_spec_builds_parser(self) -> None:
        """合成 ToolSpec 直接复用 _build_parser_for_tool 构建无参 parser。"""
        spec = build_tool_spec(CommandDecl(name="clr", help="清屏（跨平台）", cmd="x"))
        parser = _build_parser_for_tool(spec)
        parsed = parser.parse_args([])
        assert parsed is not None


# ============================================================================ #
# 文件加载（loader.py）
# ============================================================================ #
class TestLoader:
    """内置与用户级 commands.toml 的加载。"""

    def test_builtin_decls_valid(self) -> None:
        """内置 commands.toml 逐条合法且含 clr（出厂即正确 CI 门禁）。"""
        decls = builtin_command_decls()
        assert decls, "内置 commands.toml 不应为空"
        names = [d.name for d in decls]
        assert "clr" in names
        for decl in decls:
            spec = build_tool_spec(decl)  # 每条都能构造 ToolSpec（双平台）
            assert spec.cmd is not None

    def test_user_decls_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """用户配置文件缺失时返回空（非警告事件）。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert user_command_decls() == []

    def test_user_decls_normal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FCMD_HOME 指向的正常配置文件解析出命令。"""
        home = tmp_path / "fcmdhome"
        home.mkdir()
        (home / "commands.toml").write_text('[commands.hello]\nhelp = "问好"\ncmd = "echo hi"\n', encoding="utf-8")
        monkeypatch.setenv("FCMD_HOME", str(home))
        decls = user_command_decls()
        assert [d.name for d in decls] == ["hello"]
        assert decls[0].cmd == "echo hi"

    def test_user_decls_toml_syntax_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """TOML 语法错误时 warning 并跳过整个文件。"""
        home = tmp_path / "fcmdhome"
        home.mkdir()
        (home / "commands.toml").write_text("[commands.broken\nhelp = ", encoding="utf-8")
        monkeypatch.setenv("FCMD_HOME", str(home))
        with caplog.at_level("WARNING"):
            assert user_command_decls() == []
        assert any("解析失败" in r.message for r in caplog.records)

    def test_user_decls_bad_entry_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """单条非法声明仅跳过该条，其余命令继续。"""
        home = tmp_path / "fcmdhome"
        home.mkdir()
        (home / "commands.toml").write_text(
            '[commands.good]\nhelp = "好"\ncmd = "echo ok"\n[commands.bad]\ndescription = "缺 help"\ncmd = "x"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("FCMD_HOME", str(home))
        with caplog.at_level("WARNING"):
            decls = user_command_decls()
        assert [d.name for d in decls] == ["good"]
        assert any("已跳过" in r.message for r in caplog.records)

    def test_user_decls_commands_not_table(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[commands] 顶层不是表时跳过整个文件。"""
        home = tmp_path / "fcmdhome"
        home.mkdir()
        (home / "commands.toml").write_text('commands = "oops"\n', encoding="utf-8")
        monkeypatch.setenv("FCMD_HOME", str(home))
        assert user_command_decls() == []

    def test_builtin_read_failure_degrades(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """内置文件读取失败时防御性降级（warning + 空，不阻断启动）。"""
        from fcmd.dsl import loader as loader_mod

        # 故障注入测试辅助（访问私有资源路径）：模拟内置资源不可读
        monkeypatch.setattr(loader_mod.resources, "files", lambda _pkg: _BrokenResource())
        with caplog.at_level("WARNING"):
            assert builtin_command_decls() == []
        assert any("读取失败" in r.message for r in caplog.records)


class _BrokenResource:
    """模拟读取即抛 OSError 的资源对象（故障注入）。"""

    def joinpath(self, _name: str) -> _BrokenResource:
        return self

    def read_bytes(self) -> bytes:
        raise OSError("resource unavailable")


# ============================================================================ #
# console script 通用入口（entry.py）
# ============================================================================ #
class TestBootstrap:
    """infer_tool_name 与 run_named。"""

    @pytest.mark.parametrize(
        ("argv0", "expected"),
        [
            (r"C:\Python\Scripts\clr.exe", "clr"),
            ("/usr/local/bin/clr", "clr"),
            ("clr", "clr"),
            ("./bin/my-tool", "my-tool"),
        ],
    )
    def test_infer_tool_name(self, argv0: str, expected: str) -> None:
        """basename 去扩展推断工具名。"""
        assert infer_tool_name(argv0) == expected


# ============================================================================ #
# 工具发现集成（_discovery.py 集成 DSL 注册）
# ============================================================================ #
@pytest.fixture
def reset_discovery(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """隔离 DSL 相关的发现状态：重置发现标志，结束时仅回滚 DSL 注册。

    故障注入测试辅助（访问私有状态），docstring 注明隔离策略：
    - ``_DSL_TOOL_SOURCES`` / ``_TOOL_ALIASES`` / ``_TOOL_MODULES`` 用
      monkeypatch 拷贝替换，undo 自动恢复原对象；
    - ``_TOOL_REGISTRY`` 不整体清空（模块 import 缓存导致 Python 工具
      无法经重新 ensure 恢复），仅回滚 DSL 命令的注册到快照值。
    """
    from fcmd.apis.toolkit import _TOOL_REGISTRY

    snap_registry = {name: dict(subs) for name, subs in _TOOL_REGISTRY.items()}
    monkeypatch.setattr(discovery_mod, "_TOOLS_DISCOVERED", False)
    monkeypatch.setattr(discovery_mod, "_DSL_TOOL_SOURCES", dict(discovery_mod._DSL_TOOL_SOURCES))
    monkeypatch.setattr(discovery_mod, "_TOOL_ALIASES", dict(discovery_mod._TOOL_ALIASES))
    monkeypatch.setattr(discovery_mod, "_TOOL_MODULES", dict(discovery_mod._TOOL_MODULES))
    yield
    # 仅回滚 DSL 命令注册（含用户覆盖内置的场景：恢复快照值）
    for name in list(discovery_mod._DSL_TOOL_SOURCES):
        if name in snap_registry:
            _TOOL_REGISTRY[name].clear()
            _TOOL_REGISTRY[name].update(snap_registry[name])
        else:
            _TOOL_REGISTRY.pop(name, None)


@pytest.fixture
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的 FCMD_HOME 目录（内含用户级 commands.toml）。"""
    home = tmp_path / "fcmdhome"
    home.mkdir()
    monkeypatch.setenv("FCMD_HOME", str(home))
    return home


class TestDiscoveryIntegration:
    """DSL 命令经 ensure_tools_discovered 注册进全局注册表。"""

    def test_user_command_registered(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """用户级命令经 ensure 注册进 _TOOL_REGISTRY 与 _TOOL_ALIASES。"""
        (user_home / "commands.toml").write_text(
            '[commands.hello]\nhelp = "问好"\ncmd = "echo hi"\naliases = ["hi"]\n', encoding="utf-8"
        )
        discovery_mod.ensure_tools_discovered()
        assert "hello" in discovery_mod._DSL_TOOL_SOURCES
        assert discovery_mod.resolve_tool("hello") == "hello"
        assert discovery_mod.resolve_tool("hi") == "hello"  # 别名
        assert discovery_mod._DSL_TOOL_SOURCES["hello"] == "user"

    def test_dsl_conflicts_with_python_tool(
        self, user_home: Path, caplog: pytest.LogCaptureFixture, reset_discovery: None
    ) -> None:
        """DSL 声明与 Python 模块工具重名时 warning 跳过（Python 优先）。"""
        (user_home / "commands.toml").write_text(
            '[commands.taskkill]\nhelp = "伪 taskkill"\ncmd = "echo fake"\n', encoding="utf-8"
        )
        with caplog.at_level("WARNING", logger="fcmd.cli._discovery"):
            discovery_mod.ensure_tools_discovered()
        assert "taskkill" not in discovery_mod._DSL_TOOL_SOURCES
        assert any("taskkill" in r.message and "重名" in r.message for r in caplog.records)

    def test_user_overrides_builtin_mechanism(self, reset_discovery: None) -> None:
        """用户声明覆盖内置同名声明：移除旧注册后替换（机制单测）。"""
        builtin_decl = CommandDecl(name="dsldemo", help="内置版", cmd="builtin-cmd")
        user_decl = CommandDecl(name="dsldemo", help="用户版", cmd="user-cmd")
        discovery_mod._register_dsl_decl(builtin_decl, "builtin")
        discovery_mod._register_dsl_decl(user_decl, "user")
        from fcmd.apis.toolkit import get_tool

        spec = get_tool("dsldemo")
        assert spec.cmd == "user-cmd"
        assert spec.help == "用户版"
        assert discovery_mod._DSL_TOOL_SOURCES["dsldemo"] == "user"

    def test_reregister_same_source_idempotent(self, reset_discovery: None) -> None:
        """同源重入幂等：不抛子命令冲突异常，注册不重复。"""
        decl = CommandDecl(name="dsldemo", help="x", cmd="echo x")
        discovery_mod._register_dsl_decl(decl, "user")
        discovery_mod._register_dsl_decl(decl, "user")  # 不应抛 ValueError
        from fcmd.apis.toolkit import get_tool

        assert get_tool("dsldemo").cmd == "echo x"

    def test_alias_conflict_ignored(self, reset_discovery: None, caplog: pytest.LogCaptureFixture) -> None:
        """别名已被其他工具占用时 warning 忽略该别名。"""
        discovery_mod._TOOL_ALIASES["gitt"] = "gittool"  # 模拟已占用
        decl = CommandDecl(name="dsldemo", help="x", cmd="echo x", aliases=("gitt", "demo2"))
        with caplog.at_level("WARNING", logger="fcmd.cli._discovery"):
            discovery_mod._register_dsl_decl(decl, "user")
        assert discovery_mod._TOOL_ALIASES["gitt"] == "gittool"  # 原占用者保留
        assert discovery_mod._TOOL_ALIASES["demo2"] == "dsldemo"
        assert any("gitt" in r.message and "占用" in r.message for r in caplog.records)


class TestRunToolDslCommand:
    """DSL 命令经 run_tool / FcmdApp 的执行链（mock subprocess）。"""

    @staticmethod
    def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]]) -> Any:
        """构造记录调用的 subprocess.run 替身。"""

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        return fake_run

    def test_run_tool_executes_cmd(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """run_tool 执行 DSL 命令的 cmd（str → shell=True）。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text('[commands.hello]\nhelp = "问好"\ncmd = "echo hi"\n', encoding="utf-8")
        discovery_mod.ensure_tools_discovered()
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run_factory(captured))
        code = run_tool("hello", [])
        assert code == 0
        assert captured[0][0] == "echo hi"
        assert captured[0][1].get("shell") is True

    def test_fcmd_app_routes_dsl_command(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """FcmdApp 路由 DSL 命令（main._run_tool 的 registry 回退路径）。"""
        from fcmd.cli.main import FcmdApp

        (user_home / "commands.toml").write_text('[commands.hello]\nhelp = "问好"\ncmd = "echo hi"\n', encoding="utf-8")
        discovery_mod.ensure_tools_discovered()
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", self._fake_run_factory(captured))
        app = FcmdApp(["hello"])
        assert app.run() == 0
        assert captured[0][0] == "echo hi"


class TestRunToolClr:
    """内置 DSL 命令 clr 的 CLI 语义（接管原 test_cli_clr.py）。

    退出码语义说明：旧 Python 实现在 CLI 层吞掉清屏命令返回码（恒 exit 0），
    DSL 走引擎 cmd 任务链后非零返回码如实透传为 exit 1（属修正，非回归）。
    """

    @staticmethod
    def _re_register_clr_for(platform: str) -> None:
        """把内置 clr 声明按指定平台重新注册（测试辅助，回滚由 fixture 负责）。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        decl = next(d for d in builtin_command_decls() if d.name == "clr")
        _TOOL_REGISTRY.pop("clr", None)
        _TOOL_REGISTRY["clr"] = {None: build_tool_spec(decl, platform=platform)}

    def test_clr_registered_as_dsl(self, reset_discovery: None) -> None:
        """clr 经 ensure 注册为内置 DSL 单命令工具。"""
        import fcmd as fx

        discovery_mod.ensure_tools_discovered()
        assert discovery_mod._DSL_TOOL_SOURCES.get("clr") == "builtin"
        assert fx.list_subcommands("clr") == []

    @pytest.mark.parametrize(
        ("platform", "expected_cmd", "expected_shell"),
        [("win32", "cls", True), ("linux", ["clear"], False)],
    )
    def test_clr_platform_commands(
        self,
        platform: str,
        expected_cmd: Any,
        expected_shell: bool,
        monkeypatch: pytest.MonkeyPatch,
        reset_discovery: None,
    ) -> None:
        """win32 执行 cls（shell），其余平台执行 clear（无 shell）。"""
        from fcmd.apis.toolkit import run_tool

        discovery_mod.ensure_tools_discovered()
        self._re_register_clr_for(platform)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr(
            "fcmd.engine.task_command.subprocess.run",
            lambda cmd, **kw: captured.append((cmd, kw)) or subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert run_tool("clr", []) == 0
        assert captured[0][0] == expected_cmd
        assert captured[0][1].get("shell") is expected_shell

    def test_clr_failure_returns_1(
        self, monkeypatch: pytest.MonkeyPatch, reset_discovery: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """清屏命令非零返回码时 run_tool 返回 1（新语义：如实透传）。"""
        from fcmd.apis.toolkit import run_tool

        discovery_mod.ensure_tools_discovered()
        self._re_register_clr_for("linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        assert run_tool("clr", []) == 1
        assert "执行失败" in capsys.readouterr().out

    def test_clr_not_found(
        self, monkeypatch: pytest.MonkeyPatch, reset_discovery: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """清屏命令不存在时友好报错（含命令名）并返回 1。"""
        from fcmd.apis.toolkit import run_tool

        discovery_mod.ensure_tools_discovered()
        self._re_register_clr_for("linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "系统找不到指定的文件。")

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        assert run_tool("clr", []) == 1
        assert "clear" in capsys.readouterr().out

    def test_clr_dry_run_no_exec(self, monkeypatch: pytest.MonkeyPatch, reset_discovery: None) -> None:
        """--dry-run 仅打印不执行命令。"""
        from fcmd.apis.toolkit import run_tool

        discovery_mod.ensure_tools_discovered()
        self._re_register_clr_for("linux")
        captured: list[Any] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        assert run_tool("clr", ["--dry-run"]) == 0
        assert captured == []  # 未执行

    def test_user_overrides_builtin_clr(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """用户级 clr 覆盖内置声明（用户配置优先于出厂配置）。"""
        from fcmd.apis.toolkit import get_tool

        (user_home / "commands.toml").write_text(
            '[commands.clr]\nhelp = "用户清屏"\ncmd = "echo user-clear"\n', encoding="utf-8"
        )
        discovery_mod.ensure_tools_discovered()
        assert discovery_mod._DSL_TOOL_SOURCES["clr"] == "user"
        assert get_tool("clr").cmd == "echo user-clear"


# ============================================================================ #
# 参数声明（decl.py：ParamDecl）
# ============================================================================ #
class TestParamDeclParsing:
    """[commands.<name>.args.<参数名>] 表的解析与校验。"""

    @staticmethod
    def _table(args: dict[str, Any]) -> dict[str, Any]:
        """构造带 args 的最小命令表。"""
        return {"help": "x", "cmd": "echo", "args": args}

    def test_parse_str_positional(self) -> None:
        """无 default 的 str 参数解析为 positional。"""
        decl = parse_command_table("t", self._table({"name": {"help": "名字"}}))
        assert len(decl.args) == 1
        assert decl.args[0].name == "name"
        assert decl.args[0].type == "str"
        assert decl.args[0].default is None
        assert decl.args[0].help == "名字"

    def test_parse_all_types(self) -> None:
        """六种类型参数解析成功，default 归一保留。"""
        decl = parse_command_table(
            "t",
            self._table(
                {
                    "name": {"type": "str"},
                    "count": {"type": "int", "default": 1},
                    "ratio": {"type": "float", "default": 0.5},
                    "flag": {"type": "bool", "default": False},
                    "src": {"type": "path"},
                    "mode": {"type": "choices", "choices": ["a", "b"], "default": "a"},
                }
            ),
        )
        assert len(decl.args) == 6
        by_name = {p.name: p for p in decl.args}
        assert by_name["name"].default is None
        assert by_name["count"].default == 1
        assert by_name["ratio"].default == 0.5
        assert by_name["flag"].default is False
        assert by_name["src"].type == "path"
        assert by_name["mode"].choices == ("a", "b")

    def test_bool_requires_default(self) -> None:
        """type=bool 必须提供 default。"""
        with pytest.raises(CommandDeclError, match=r"bool.*default"):
            parse_command_table("t", self._table({"flag": {"type": "bool"}}))

    @pytest.mark.parametrize("bad_name", ["BadName", "1name", "dry_run", "strategy"])
    def test_bad_param_name(self, bad_name: str) -> None:
        """非法/保留参数名报错。"""
        with pytest.raises(CommandDeclError, match="参数名"):
            parse_command_table("t", self._table({bad_name: {}}))

    def test_param_unknown_key(self) -> None:
        """参数表未知键报错。"""
        with pytest.raises(CommandDeclError, match="未知键"):
            parse_command_table("t", self._table({"name": {"tpye": "str"}}))

    def test_unsupported_type(self) -> None:
        """不支持的 type 报错。"""
        with pytest.raises(CommandDeclError, match="type"):
            parse_command_table("t", self._table({"x": {"type": "decimal"}}))

    def test_choices_missing(self) -> None:
        """type=choices 缺 choices 报错。"""
        with pytest.raises(CommandDeclError, match="choices"):
            parse_command_table("t", self._table({"mode": {"type": "choices"}}))

    def test_choices_with_non_choices_type(self) -> None:
        """非 choices 类型声明 choices 报错。"""
        with pytest.raises(CommandDeclError, match=r"仅 type=choices"):
            parse_command_table("t", self._table({"x": {"type": "str", "choices": ["a"]}}))

    def test_default_type_mismatch(self) -> None:
        """default 类型与 type 不匹配报错。"""
        with pytest.raises(CommandDeclError, match="不匹配"):
            parse_command_table("t", self._table({"x": {"type": "int", "default": "3"}}))

    def test_default_not_in_choices(self) -> None:
        """choices 参数的 default 不在取值内报错。"""
        with pytest.raises(CommandDeclError, match="choices 内"):
            parse_command_table("t", self._table({"mode": {"type": "choices", "choices": ["a", "b"], "default": "c"}}))

    def test_float_accepts_int_default(self) -> None:
        """float 参数宽容接受 int default（Python 数值惯例）。"""
        decl = parse_command_table("t", self._table({"ratio": {"type": "float", "default": 1}}))
        assert decl.args[0].default == 1

    def test_args_not_table(self) -> None:
        """args 不是表报错。"""
        with pytest.raises(CommandDeclError, match="args 须是表"):
            parse_command_table("t", {"help": "x", "cmd": "echo", "args": ["bad"]})


# ============================================================================ #
# 参数签名合成（synth.py）与 parser 复用
# ============================================================================ #
class TestParamSignature:
    """带参数声明的合成签名经 _build_parser_for_tool 生成 argparse 参数。"""

    @staticmethod
    def _parser_for(args: tuple[ParamDecl, ...] | None = None) -> Any:
        """按参数声明构造 parser（测试辅助）。"""
        decl = CommandDecl(name="t", help="测试", cmd="echo", args=args or ())
        return _build_parser_for_tool(build_tool_spec(decl))

    def test_positional_str(self) -> None:
        """无 default 的 str 参数 → positional。"""
        parser = self._parser_for((ParamDecl(name="name", help="名字"),))
        parsed = parser.parse_args(["world"])
        assert parsed.name == "world"

    def test_int_option(self) -> None:
        """有 default 的 int 参数 → --count 选项。"""
        parser = self._parser_for((ParamDecl(name="count", type="int", default=1, help="次数"),))
        assert parser.parse_args([]).count == 1
        assert parser.parse_args(["--count", "3"]).count == 3

    def test_bool_false_store_true(self) -> None:
        """bool default=false → --flag store_true 启用开关。"""
        parser = self._parser_for((ParamDecl(name="flag", type="bool", default=False),))
        assert parser.parse_args([]).flag is False
        assert parser.parse_args(["--flag"]).flag is True

    def test_bool_true_no_prefix(self) -> None:
        """bool default=true → --no-flag store_false 关闭开关。"""
        parser = self._parser_for((ParamDecl(name="flag", type="bool", default=True),))
        assert parser.parse_args([]).flag is True
        assert parser.parse_args(["--no-flag"]).flag is False

    def test_choices_validation(self) -> None:
        """choices 参数 → argparse choices 校验。"""
        parser = self._parser_for((ParamDecl(name="mode", type="choices", choices=("a", "b"), default="a"),))
        assert parser.parse_args(["--mode", "b"]).mode == "b"
        with pytest.raises(SystemExit):
            parser.parse_args(["--mode", "c"])

    def test_path_positional(self) -> None:
        """path 参数 → argparse type=Path。"""
        parser = self._parser_for((ParamDecl(name="src", type="path"),))
        parsed = parser.parse_args(["/tmp/x.txt"])
        assert parsed.src == Path("/tmp/x.txt")

    def test_param_help_shown(self) -> None:
        """参数 help 文本呈现于 --help 输出。"""
        parser = self._parser_for((ParamDecl(name="count", type="int", default=1, help="重复次数"),))
        assert "重复次数" in parser.format_help()


# ============================================================================ #
# cmd 模板插值（_tool_exec.py）
# ============================================================================ #
class TestInterpolation:
    """cmd 中 {参数名} 占位符替换。"""

    @staticmethod
    def _spec(cmd: str, arg_names: tuple[str, ...]) -> Any:
        """构造带参数签名的 ToolSpec（测试辅助）。"""
        from inspect import Parameter, Signature

        decl = CommandDecl(name="t", help="x", cmd=cmd)
        spec = build_tool_spec(decl)
        params = [Parameter(n, Parameter.POSITIONAL_OR_KEYWORD) for n in arg_names]
        spec.func.__signature__ = Signature(params)  # type: ignore[attr-defined]
        return spec

    def test_str_cmd_expanded(self) -> None:
        """str cmd 的占位符替换为 CLI 值。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        spec = self._spec("echo {name} x{count}", ("name", "count"))
        result = _expand_cmd_placeholders("echo {name} x{count}", spec, {"name": "world", "count": 3})
        assert result == "echo world x3"

    def test_list_cmd_expanded(self) -> None:
        """list cmd 逐元素替换。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        spec = self._spec("x", ("host",))
        result = _expand_cmd_placeholders(["ping", "{host}", "-c", "1"], spec, {"host": "localhost"})
        assert result == ["ping", "localhost", "-c", "1"]

    def test_no_placeholder_fast_path(self) -> None:
        """无 { 的 cmd 原样直返（快路径）。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        spec = self._spec("echo hi", ("name",))
        result = _expand_cmd_placeholders("echo hi", spec, {"name": "x"})
        assert result == "echo hi"

    def test_literal_braces_untouched(self) -> None:
        """未声明参数的花括号（如 python -c "print({})"）不受影响。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        spec = self._spec("python -c print({})", ("name",))
        result = _expand_cmd_placeholders('python -c "print({})"', spec, {"name": "x"})
        assert result == 'python -c "print({})"'

    def test_global_vars_not_expanded(self) -> None:
        """签名外变量（dry_run 等）不展开。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        spec = self._spec("echo {dry_run}", ())
        result = _expand_cmd_placeholders("echo {dry_run}", spec, {"dry_run": True})
        assert result == "echo {dry_run}"

    def test_value_conversions(self) -> None:
        """bool → true/false；list → 空格连接；Path → str。"""
        from pathlib import Path

        from fcmd.apis._tool_exec import _expand_cmd_placeholders, _value_to_cmd_str

        assert _value_to_cmd_str(True) == "true"
        assert _value_to_cmd_str(False) == "false"
        assert _value_to_cmd_str(["a", "b"]) == "a b"
        assert _value_to_cmd_str(Path("/tmp/x")) == str(Path("/tmp/x"))
        spec = self._spec("x", ("flag",))
        assert _expand_cmd_placeholders("--v={flag}", spec, {"flag": True}) == "--v=true"

    def test_callable_cmd_task_spec_unaffected(self) -> None:
        """callable 型 cmd（如 pymake push 的函数任务）不被插值破坏。"""
        from fcmd.apis._tool_exec import _build_task_spec
        from fcmd.apis.toolkit import ToolSpec

        def _push_all_remotes() -> None:
            """pymake push 的函数任务形态。"""

        def push(dry_run: bool = False) -> None:
            """签名含 dry_run 的 cmd 任务函数。"""

        spec = ToolSpec(
            name="pymake",
            subcommand="push",
            func=push,
            help="推送代码到所有远程仓库",
            cmd=_push_all_remotes,  # type: ignore[arg-type]  # callable 型 cmd（既有用法）
        )
        # dry_run 在签名内且在 variables 中——callable cmd 必须直通不迭代
        task = _build_task_spec(spec, {"dry_run": True, "quiet": False})
        assert task.cmd is _push_all_remotes

    def test_run_tool_with_interpolation(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """run_tool 全链路：用户命令带参数声明与 cmd 插值。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text(
            '[commands.greet]\nhelp = "问好"\ncmd = "echo 你好 {name} x{count}"\n'
            "[commands.greet.args.name]\n"
            'help = "目标名字"\n'
            "[commands.greet.args.count]\n"
            'type = "int"\ndefault = 1\nhelp = "次数"\n',
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        captured: list[Any] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        assert run_tool("greet", ["world", "--count", "3"]) == 0
        assert captured[0] == "echo 你好 world x3"


# ============================================================================ #
# cwd / timeout / env 透传（P2）
# ============================================================================ #
class TestTransparency:
    """cwd / timeout / env 声明的解析、映射与插值。"""

    def test_decl_transparency_fields(self) -> None:
        """cwd / timeout / env 解析成功（timeout 归一 float）。"""
        decl = parse_command_table(
            "t",
            {
                "help": "x",
                "cmd": "echo",
                "cwd": "/tmp/{dir_name}",
                "timeout": 30,
                "env": {"MODE": "{mode}", "EXTRA": "static"},
            },
        )
        assert decl.cwd == "/tmp/{dir_name}"
        assert decl.timeout == 30.0
        assert decl.env == {"MODE": "{mode}", "EXTRA": "static"}

    @pytest.mark.parametrize(
        ("key", "value"),
        [("cwd", 42), ("timeout", -1), ("timeout", "30"), ("timeout", True), ("env", ["bad"]), ("env", {"K": 1})],
    )
    def test_decl_bad_transparency(self, key: str, value: Any) -> None:
        """cwd / timeout / env 值类型非法报错。"""
        with pytest.raises(CommandDeclError, match=key):
            parse_command_table("t", {"help": "x", "cmd": "echo", key: value})

    def test_spec_transparency_mapping(self) -> None:
        """build_tool_spec 完整映射 cwd / timeout / env（env 拷贝）。"""
        decl = CommandDecl(
            name="t",
            help="x",
            cmd="echo",
            cwd="/tmp",
            timeout=30.0,
            env={"K": "v"},
        )
        spec = build_tool_spec(decl)
        assert spec.cwd == "/tmp"
        assert spec.timeout == 30.0
        assert spec.env == {"K": "v"}

    def test_cwd_env_interpolation_in_task_spec(self) -> None:
        """cwd 与 env 值的 {参数名} 占位符在 TaskSpec 构建时展开。"""
        from fcmd.apis._tool_exec import _build_task_spec

        decl = CommandDecl(
            name="t",
            help="x",
            cmd="echo {name}",
            cwd="/data/{name}",
            env={"WHO": "{name}"},
            args=(ParamDecl(name="name"),),
        )
        spec = build_tool_spec(decl)
        task = _build_task_spec(spec, {"name": "world", "dry_run": False})
        assert task.cwd == Path("/data/world")
        assert task.env == {"WHO": "world"}
        assert task.cmd == "echo world"

    def test_timeout_passed_through(self) -> None:
        """timeout 声明透传 TaskSpec。"""
        from fcmd.apis._tool_exec import _build_task_spec

        decl = CommandDecl(name="t", help="x", cmd="sleep 1", timeout=0.5)
        task = _build_task_spec(build_tool_spec(decl), {})
        assert task.timeout == 0.5

    def test_run_tool_transparency_e2e(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None, tmp_path: Path
    ) -> None:
        """run_tool 全链路：cwd/env/timeout 到达 subprocess（mock 断言）。"""
        from fcmd.apis.toolkit import run_tool

        # cwd 必须真实存在：引擎对带 cwd 的任务先 os.chdir（进程级），
        # subprocess 再按 cwd 参数执行（引擎既有行为）
        work_dir = tmp_path / "workdir"
        work_dir.mkdir()
        toml_text = "\n".join(
            [
                "[commands.where]",
                'help = "定位"',
                'cmd = "echo at {place}"',
                f'cwd = "{work_dir.as_posix()}"',
                "timeout = 15",
                'env = { PLACE = "{place}" }',
                "[commands.where.args.place]",
                'help = "地点"',
            ]
        )
        (user_home / "commands.toml").write_text(toml_text, encoding="utf-8")
        discovery_mod.ensure_tools_discovered()
        captured: dict[str, Any] = {}

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.update(kwargs)
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        assert run_tool("where", ["home"]) == 0
        assert captured["cmd"] == "echo at home"
        assert captured["cwd"] == work_dir
        assert captured["timeout"] == 15
        assert captured["env"]["PLACE"] == "home"
