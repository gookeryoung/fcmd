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
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fcmd.apis._tool_args import ToolSpec, _build_parser_for_tool
from fcmd.apis._tool_exec import _apply_env_defaults, _build_task_spec
from fcmd.cli import _discovery as discovery_mod
from fcmd.cli._common import _BUILTIN_COMMANDS
from fcmd.dsl import (
    CommandDecl,
    CommandDeclError,
    ToolDecl,
    build_tool_spec,
    builtin_tool_decls,
    infer_tool_name,
    parse_command_table,
    parse_tool_table,
    run_named,
    select_platform_cmd,
    user_tool_decls,
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
# 内建动作原语（action）声明与校验
# ============================================================================ #
class TestActionDecl:
    """命令级 ``action`` 键的解析与校验（进程内动作原语）。"""

    def test_parse_action_command(self) -> None:
        """合法 action 声明解析成功，args 为空。"""
        decl = parse_command_table("setenv", {"help": "设置环境变量", "action": "setenv"})
        assert decl.action == "setenv"
        assert decl.args == ()
        assert decl.cmd is None and decl.win_cmd is None and decl.unix_cmd is None

    def test_action_no_cmd_required(self) -> None:
        """action 命令免除「须提供 cmd」校验。"""
        decl = parse_command_table("writefile", {"help": "写文件", "action": "writefile"})
        assert decl.action == "writefile"

    def test_unknown_action(self) -> None:
        """未注册动作报错并列出可用动作。"""
        with pytest.raises(CommandDeclError, match=r"未注册.*setenv"):
            parse_command_table("bad", {"help": "x", "action": "no_such_action"})

    @pytest.mark.parametrize("bad_value", ["", 42])
    def test_bad_action_value(self, bad_value: Any) -> None:
        """action 非非空字符串报错。"""
        with pytest.raises(CommandDeclError, match="action 须是非空字符串"):
            parse_command_table("bad", {"help": "x", "action": bad_value})

    def test_action_none_means_undeclared(self) -> None:
        """action = None（未声明）不报错，回退普通 cmd 命令判定。"""
        decl = parse_command_table("bad", {"help": "x", "action": None, "cmd": "echo x"})
        assert decl.action is None

    def test_action_mutex_with_cmd(self) -> None:
        """action 与 cmd 互斥。"""
        with pytest.raises(CommandDeclError, match=r"action 与 cmd/win/unix 互斥"):
            parse_command_table("bad", {"help": "x", "action": "setenv", "cmd": "echo x"})

    def test_action_mutex_with_args(self) -> None:
        """action 与 args 互斥（参数 schema 来自动作实现签名）。"""
        with pytest.raises(CommandDeclError, match=r"action 与 args 互斥"):
            parse_command_table("bad", {"help": "x", "action": "setenv", "args": {"name": {"type": "str"}}})

    @pytest.mark.parametrize("key, value", [("timeout", 5), ("env", {"K": "v"}), ("tty", True)])
    def test_action_mutex_with_subprocess_keys(self, key: str, value: Any) -> None:
        """action 与 timeout/env/tty 互斥（subprocess 概念，fn 任务不消费）。"""
        with pytest.raises(CommandDeclError, match=r"action.*互斥"):
            parse_command_table("bad", {"help": "x", "action": "setenv", key: value})

    def test_action_command_flat_detection(self) -> None:
        """action 计入单命令形态识别（不误判为子命令表）。"""
        tool = parse_tool_table("setenv", {"help": "设置环境变量", "action": "setenv"})
        assert tool.flat is True
        assert tool.commands[0].action == "setenv"


class TestActionSynthesis:
    """action 命令的 ToolSpec 合成与 fn 任务分发。"""

    def test_build_tool_spec_action_fn_task(self) -> None:
        """action 命令合成 fn 任务形态：cmd=None、有函数逻辑、param_help 来自注册表。"""
        decl = parse_command_table("setenv", {"help": "设置环境变量", "action": "setenv"})
        spec = build_tool_spec(decl)
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == "setenv"
        assert (spec.param_help or {})["value"] == "环境变量值"
        # 合成函数签名与实现一致（含默认值）
        sig_params = spec.func.__signature__.parameters  # type: ignore[attr-defined]
        assert list(sig_params) == ["name", "value", "default"]
        assert sig_params["default"].default is False

    def test_action_fn_task_dispatch_and_cwd(self) -> None:
        """action 命令走 fn 任务分支且 cwd 声明生效（fn 分支回退 spec.cwd）。"""
        decl = parse_command_table("writefile", {"help": "写文件", "action": "writefile", "cwd": "/tmp"})
        spec = build_tool_spec(decl)
        task = _build_task_spec(spec, {})
        assert task.fn is spec.func  # fn 任务（非 cmd/聚合）
        assert task.cwd is not None and task.cwd == Path("/tmp")

    def test_action_message_contract(self) -> None:
        """action 命令的 message 契约注入（post-run 完成消息）。"""
        decl = parse_command_table("setenv", {"help": "x", "action": "setenv", "message": "环境变量 {name} 已设置"})
        spec = build_tool_spec(decl)
        assert getattr(spec.func, "__dsl_message__", None) == "环境变量 {name} 已设置"

    def test_fn_task_cwd_prefers_cli_variable(self) -> None:
        """fn 分支 cwd 取值优先 CLI 变量（与 cmd 分支语义一致）。"""
        decl = parse_command_table("writefile", {"help": "写文件", "action": "writefile", "cwd": "/tmp"})
        spec = build_tool_spec(decl)
        task = _build_task_spec(spec, {"cwd": "/other"})
        assert task.cwd == Path("/other")


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

    def test_list_annotation_and_on_injection(self) -> None:
        """list 类型合成 list[str] 注解；on 注入 __dsl_param_on__ 属性。"""
        import typing

        decl = CommandDecl(
            name="t",
            help="x",
            cmd=("pip", "install", "{packages}"),
            args=(
                ParamDecl(name="packages", type="list"),
                ParamDecl(name="fix", type="bool", default=False, on=("--fix", "--unsafe-fixes")),
            ),
        )
        spec = build_tool_spec(decl)
        hints = typing.get_type_hints(spec.func)
        assert hints["packages"] == list[str]
        assert spec.func.__dsl_param_on__ == {"fix": ("--fix", "--unsafe-fixes")}  # type: ignore[attr-defined]

    def test_list_parser_nargs(self) -> None:
        """list 参数经 _build_parser_for_tool 映射为多值参数。"""
        decl = CommandDecl(name="t", help="x", cmd=("pip",), args=(ParamDecl(name="packages", type="list"),))
        spec = build_tool_spec(decl)
        parsed = _build_parser_for_tool(spec).parse_args(["a", "b"])
        assert parsed.packages == ["a", "b"]


# ============================================================================ #
# 文件加载（loader.py）
# ============================================================================ #
class TestLoader:
    """内置与用户级 commands.toml 的加载。"""

    def test_builtin_decls_valid(self) -> None:
        """内置命令目录 commands/*.toml 逐条合法且含 clr/pymake/gittool/autofmt/piptool（出厂即正确 CI 门禁）。"""
        tools = builtin_tool_decls()
        assert tools, "内置命令目录不应为空"
        names = [t.name for t in tools]
        assert "clr" in names
        assert "pymake" in names and "gittool" in names
        assert "autofmt" in names and "piptool" in names
        for tool in tools:
            for decl in tool.commands:
                spec = build_tool_spec(decl, tool_name=tool.name, subcommand=None if tool.flat else decl.name)
                # 聚合命令（needs 且无 cmd）与内建动作（fn 任务）合法；其余子命令必须可执行
                assert spec.cmd is not None or spec.needs or getattr(spec.func, "__dsl_action__", None)

    def test_user_decls_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """用户配置文件缺失时返回空（非警告事件）。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert user_tool_decls() == []

    def test_user_decls_normal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FCMD_HOME 指向的正常配置文件解析出命令。"""
        home = tmp_path / "fcmdhome"
        home.mkdir()
        (home / "commands.toml").write_text('[commands.hello]\nhelp = "问好"\ncmd = "echo hi"\n', encoding="utf-8")
        monkeypatch.setenv("FCMD_HOME", str(home))
        decls = user_tool_decls()
        assert [d.name for d in decls] == ["hello"]
        assert decls[0].commands[0].cmd == "echo hi"

    def test_user_decls_toml_syntax_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """TOML 语法错误时 warning 并跳过整个文件。"""
        home = tmp_path / "fcmdhome"
        home.mkdir()
        (home / "commands.toml").write_text("[commands.broken\nhelp = ", encoding="utf-8")
        monkeypatch.setenv("FCMD_HOME", str(home))
        with caplog.at_level("WARNING"):
            assert user_tool_decls() == []
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
            decls = user_tool_decls()
        assert [d.name for d in decls] == ["good"]
        assert any("已跳过" in r.message for r in caplog.records)

    def test_user_decls_commands_not_table(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[commands] 顶层不是表时跳过整个文件。"""
        home = tmp_path / "fcmdhome"
        home.mkdir()
        (home / "commands.toml").write_text('commands = "oops"\n', encoding="utf-8")
        monkeypatch.setenv("FCMD_HOME", str(home))
        assert user_tool_decls() == []

    def test_builtin_read_failure_degrades(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """内置命令目录读取失败时防御性降级（warning + 空，不阻断启动）。"""
        from fcmd.dsl import loader as loader_mod

        # 故障注入测试辅助（访问私有资源路径）：模拟内置资源不可读
        monkeypatch.setattr(loader_mod.resources, "files", lambda _pkg: _BrokenResource())
        with caplog.at_level("WARNING"):
            assert builtin_tool_decls() == []
        assert any("读取失败" in r.message for r in caplog.records)

    def test_builtin_multi_file_sorted_merge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """内置目录多文件按文件名排序合并；非 toml 条目与下划线前缀文件忽略。"""
        from fcmd.dsl import loader as loader_mod

        children = [
            _FakeResource("ztool.toml", '[commands.zb]\nhelp = "z"\ncmd = "echo z"\n'),
            _FakeResource("_draft.toml", '[commands.draft]\nhelp = "d"\ncmd = "echo d"\n'),
            _FakeResource("notes.md", "不是 toml"),
            _FakeResource("atool.toml", '[commands.aa]\nhelp = "a"\ncmd = "echo a"\n'),
        ]
        monkeypatch.setattr(loader_mod.resources, "files", lambda _pkg: _FakeResourceDir(children))
        assert [tool.name for tool in builtin_tool_decls()] == ["aa", "zb"]

    def test_builtin_single_file_broken_skipped(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """单文件损坏仅跳过该文件，其余文件命令继续加载。"""
        from fcmd.dsl import loader as loader_mod

        children = [
            _FakeResource("a.toml", '[commands.good]\nhelp = "好"\ncmd = "echo ok"\n'),
            _FakeResource("b.toml", "[commands.broken\nhelp = "),  # TOML 语法错误
            _FakeResource("c.toml", error=OSError("disk unavailable")),  # I/O 错误
        ]
        monkeypatch.setattr(loader_mod.resources, "files", lambda _pkg: _FakeResourceDir(children))
        with caplog.at_level("WARNING"):
            decls = builtin_tool_decls()
        assert [tool.name for tool in decls] == ["good"]
        assert any("b.toml" in r.message for r in caplog.records)
        assert any("c.toml" in r.message for r in caplog.records)


class _FakeResource:
    """模拟 importlib.resources 文件资源（目录加载行为测试辅助，访问私有资源路径）。"""

    def __init__(self, name: str, content: str | None = None, error: OSError | None = None) -> None:
        self._name = name
        self._content = content
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    def read_bytes(self) -> bytes:
        if self._error is not None:
            raise self._error
        return (self._content or "").encode("utf-8")


class _FakeResourceDir:
    """模拟 importlib.resources 目录资源（按给定子条目迭代，不排序）。"""

    def __init__(self, children: list[_FakeResource]) -> None:
        self._children = children

    def joinpath(self, _name: str) -> _FakeResourceDir:
        return self

    def iterdir(self) -> Iterator[_FakeResource]:
        return iter(self._children)


class _BrokenResource:
    """模拟读取即抛 OSError 的资源对象（故障注入）。"""

    def joinpath(self, _name: str) -> _BrokenResource:
        return self

    def iterdir(self) -> Iterator[_BrokenResource]:
        raise OSError("resource unavailable")

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

    def test_run_named_forwards_to_run_tool(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """run_named 按 argv[0] 推断工具名并转发 run_tool，退出码透传。"""
        seen: dict[str, Any] = {}

        def fake_run_tool(name: str, argv: list[str]) -> int:
            seen["name"] = name
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(discovery_mod, "ensure_tools_discovered", lambda: None)
        monkeypatch.setattr(discovery_mod, "resolve_tool", lambda name: name)
        monkeypatch.setattr("fcmd.apis.toolkit.run_tool", fake_run_tool)
        monkeypatch.setattr(sys, "argv", [r"C:\Scripts\clr.exe", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            run_named()
        assert exc_info.value.code == 0
        assert seen == {"name": "clr", "argv": ["--help"]}
        assert capsys.readouterr().out == ""

    def test_run_named_unknown_tool_exits_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """入口名未对应已注册工具 → 打印错误并以退出码 1 终止。"""
        monkeypatch.setattr(discovery_mod, "ensure_tools_discovered", lambda: None)
        monkeypatch.setattr(discovery_mod, "resolve_tool", lambda _name: None)
        monkeypatch.setattr(sys, "argv", ["nosuch.exe"])
        with pytest.raises(SystemExit) as exc_info:
            run_named()
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "未对应任何已注册工具" in out
        assert "查看可用工具列表" in out


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

    def test_user_cannot_override_builtin_subcommand(
        self, user_home: Path, caplog: pytest.LogCaptureFixture, reset_discovery: None
    ) -> None:
        """用户 DSL 不能覆盖内置 DSL 子命令（先注册者优先，已复核确认的策略）。

        单命令形态用户可覆盖内置（用户配置优先于出厂配置）；多子命令形态为
        保护内置聚合链（chk/tc 依赖 pyrefly_check/lint/fmt/tf）语义不被静默
        改写，同名子命令保留先注册者，用户仅可新增子命令。
        """
        from fcmd.apis.toolkit import get_tool

        (user_home / "commands.toml").write_text(
            '[commands.pymake.t]\nhelp = "用户版测试"\ncmd = "echo fake-t"\n'
            '[commands.pymake.myext]\nhelp = "用户新增"\ncmd = "echo ext"\n',
            encoding="utf-8",
        )
        with caplog.at_level("WARNING", logger="fcmd.cli._discovery"):
            discovery_mod.ensure_tools_discovered()
        # 同名 t 保留内置声明，用户版被跳过
        t = get_tool("pymake", "t")
        assert t.cmd == ("pytest", "-m", "not slow", "--color=yes", "--durations=10")
        assert t.help != "用户版测试"
        # 新增子命令 myext 正常合并注册
        assert get_tool("pymake", "myext").cmd == "echo ext"
        assert any("pymake" in r.message and "重名" in r.message for r in caplog.records)

    def test_user_overrides_builtin_mechanism(self, reset_discovery: None) -> None:
        """用户声明覆盖内置同名声明：移除旧注册后替换（机制单测）。"""
        builtin_decl = CommandDecl(name="dsldemo", help="内置版", cmd="builtin-cmd")
        user_decl = CommandDecl(name="dsldemo", help="用户版", cmd="user-cmd")
        discovery_mod._register_dsl_tool(ToolDecl(name="dsldemo", commands=(builtin_decl,)), "builtin")
        discovery_mod._register_dsl_tool(ToolDecl(name="dsldemo", commands=(user_decl,)), "user")
        from fcmd.apis.toolkit import get_tool

        spec = get_tool("dsldemo")
        assert spec.cmd == "user-cmd"
        assert spec.help == "用户版"
        assert discovery_mod._DSL_TOOL_SOURCES["dsldemo"] == "user"

    def test_reregister_same_source_idempotent(self, reset_discovery: None) -> None:
        """同源重入幂等：不抛子命令冲突异常，注册不重复。"""
        decl = CommandDecl(name="dsldemo", help="x", cmd="echo x")
        tool = ToolDecl(name="dsldemo", commands=(decl,))
        discovery_mod._register_dsl_tool(tool, "user")
        discovery_mod._register_dsl_tool(tool, "user")  # 不应抛 ValueError
        from fcmd.apis.toolkit import get_tool

        assert get_tool("dsldemo").cmd == "echo x"

    def test_alias_conflict_ignored(self, reset_discovery: None, caplog: pytest.LogCaptureFixture) -> None:
        """别名已被其他工具占用时 warning 忽略该别名。"""
        discovery_mod._TOOL_ALIASES["gitt"] = "gittool"  # 模拟已占用
        decl = CommandDecl(name="dsldemo", help="x", cmd="echo x", aliases=("gitt", "demo2"))
        tool = ToolDecl(name="dsldemo", commands=(decl,))
        with caplog.at_level("WARNING", logger="fcmd.cli._discovery"):
            discovery_mod._register_dsl_tool(tool, "user")
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

    def test_action_setenv_feeds_downstream_cmd_env(
        self, user_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """链式编排端到端：action setenv 设置进程环境，下游 cmd 子任务继承。

        目标命令 show 声明与 setenv 实现同名的 name/value 参数（共享 variables
        流入 fn 任务 kwargs），cmd 写文件断言子进程真实读到环境变量。
        """
        out = tmp_path / "env_out.txt"
        from fcmd.apis.toolkit import run_tool

        # win 分支用 TOML 字面串（反斜杠原样）；unix 分支用 posix 路径避免
        # 基本串中 \U 被当作转义
        (user_home / "commands.toml").write_text(
            '[commands.envchain.set]\nhelp = "设置变量"\naction = "setenv"\n'
            f'[commands.envchain.show]\nhelp = "读变量写文件"\nneeds = ["set"]\n'
            f"win.cmd = 'cmd /c echo %FCMD_CHAIN_VAR%> \"{out}\"'\n"
            f'unix.cmd = "echo $FCMD_CHAIN_VAR > {out.as_posix()}"\n'
            'args = { name = { type = "str" }, value = { type = "str" } }\n',
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        monkeypatch.delenv("FCMD_CHAIN_VAR", raising=False)
        code = run_tool("envchain", ["show", "FCMD_CHAIN_VAR", "chain_value"])
        assert code == 0
        assert out.read_text(encoding="utf-8").strip() == "chain_value"


class TestRunToolClr:
    """内置 DSL 命令 clr 的 CLI 语义（接管原 test_cli_clr.py）。

    退出码语义说明：旧 Python 实现在 CLI 层吞掉清屏命令返回码（恒 exit 0），
    DSL 走引擎 cmd 任务链后非零返回码如实透传为 exit 1（属修正，非回归）。
    """

    @staticmethod
    def _re_register_clr_for(platform: str) -> None:
        """把内置 clr 声明按指定平台重新注册（测试辅助，回滚由 fixture 负责）。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        clr_tool = next(t for t in builtin_tool_decls() if t.name == "clr")
        _TOOL_REGISTRY.pop("clr", None)
        _TOOL_REGISTRY["clr"] = {None: build_tool_spec(clr_tool.commands[0], platform=platform)}

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

    def test_parse_list_param(self) -> None:
        """type=list 无 default 解析为 positional list 参数。"""
        decl = parse_command_table("t", self._table({"packages": {"type": "list", "help": "包名列表"}}))
        assert decl.args[0].type == "list"
        assert decl.args[0].default is None

    def test_list_rejects_default(self) -> None:
        """type=list 不支持 default（positional 参数）。"""
        with pytest.raises(CommandDeclError, match=r"type=list"):
            parse_command_table("t", self._table({"x": {"type": "list", "default": ["a"]}}))

    def test_on_token_parses(self) -> None:
        """bool 参数 on 固定 token 解析保留。"""
        decl = parse_command_table(
            "t", self._table({"fix": {"type": "bool", "default": False, "on": ["--fix", "--unsafe-fixes"]}})
        )
        assert decl.args[0].on == ("--fix", "--unsafe-fixes")

    def test_on_with_non_bool_rejected(self) -> None:
        """非 bool 类型声明 on 报错。"""
        with pytest.raises(CommandDeclError, match=r"仅 type=bool"):
            parse_command_table("t", self._table({"x": {"type": "str", "on": ["--x"]}}))

    def test_on_must_be_non_empty_string_array(self) -> None:
        """on 含空串/非字符串报错。"""
        with pytest.raises(CommandDeclError, match="on 须是"):
            parse_command_table("t", self._table({"x": {"type": "bool", "default": False, "on": [""]}}))

    def test_list_partial_placeholder_rejected(self) -> None:
        """tuple cmd 中 list 参数非独占占位符（部分占位）报错。"""
        table = {"help": "x", "cmd": ["pip", "install", "pkg-{packages}"], "args": {"packages": {"type": "list"}}}
        with pytest.raises(CommandDeclError, match="独占占位符"):
            parse_command_table("t", table)

    def test_list_exclusive_placeholder_ok(self) -> None:
        """tuple cmd 中 list 参数独占占位符合法；str cmd 不校验。"""
        table = {"help": "x", "cmd": ["pip", "install", "{packages}"], "args": {"packages": {"type": "list"}}}
        decl = parse_command_table("t", table)
        assert decl.args[0].type == "list"
        str_table = {"help": "x", "cmd": "pip install pkg-{packages}", "args": {"packages": {"type": "list"}}}
        assert parse_command_table("t", str_table).args[0].type == "list"


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

    def test_list_exclusive_placeholder_spliced(self) -> None:
        """list 参数独占占位符按元素展开（tuple cmd，无 shell 语义）。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        decl = CommandDecl(
            name="t", help="x", cmd=("pip", "install", "{packages}"), args=(ParamDecl(name="packages", type="list"),)
        )
        spec = build_tool_spec(decl)
        result = _expand_cmd_placeholders(["pip", "install", "{packages}"], spec, {"packages": ["a", "b"]})
        assert result == ["pip", "install", "a", "b"]

    def test_on_tokens_appended_when_truthy(self) -> None:
        """bool on 固定 token：值为真追加，值为假不追加。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        decl = CommandDecl(
            name="t",
            help="x",
            cmd=("ruff", "check", "{target}"),
            args=(
                ParamDecl(name="target", type="str", default="."),
                ParamDecl(name="fix", type="bool", default=False, on=("--fix", "--unsafe-fixes")),
            ),
        )
        spec = build_tool_spec(decl)
        assert _expand_cmd_placeholders(["ruff", "check", "{target}"], spec, {"target": "src", "fix": True}) == [
            "ruff",
            "check",
            "src",
            "--fix",
            "--unsafe-fixes",
        ]
        assert _expand_cmd_placeholders(["ruff", "check", "{target}"], spec, {"target": "src", "fix": False}) == [
            "ruff",
            "check",
            "src",
        ]

    def test_on_tokens_str_cmd_appended(self) -> None:
        """str cmd（shell）的 on 固定 token 以空格追加到尾部。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        decl = CommandDecl(
            name="t",
            help="x",
            cmd="ruff check {target}",
            args=(
                ParamDecl(name="target", type="str", default="."),
                ParamDecl(name="fix", type="bool", default=False, on=("--fix",)),
            ),
        )
        spec = build_tool_spec(decl)
        assert (
            _expand_cmd_placeholders("ruff check {target}", spec, {"target": ".", "fix": True}) == "ruff check . --fix"
        )
        assert _expand_cmd_placeholders("ruff check {target}", spec, {"target": ".", "fix": False}) == "ruff check ."

    def test_non_dsl_func_no_on_tokens(self) -> None:
        """非 DSL 合成函数（无 __dsl_param_on__）不受 on 逻辑影响。"""
        from fcmd.apis._tool_exec import _expand_cmd_placeholders

        spec = self._spec("echo {name}", ("name",))
        assert _expand_cmd_placeholders("echo {name}", spec, {"name": "x"}) == "echo x"

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


# ============================================================================ #
# post-run 完成消息（decl.py message 字段 / synth __dsl_message__）
# ============================================================================ #
class TestMessageDeclaration:
    """命令级 message 声明的解析、注入与执行成功后打印。"""

    def test_message_parses(self) -> None:
        """message 字段解析保留。"""
        decl = parse_command_table("t", {"help": "x", "cmd": "echo", "message": "完成 {name}"})
        assert decl.message == "完成 {name}"

    def test_message_default_empty(self) -> None:
        """未声明 message 时为空串。"""
        assert parse_command_table("t", {"help": "x", "cmd": "echo"}).message == ""

    def test_message_non_string_rejected(self) -> None:
        """message 非字符串报错。"""
        with pytest.raises(CommandDeclError, match="message 须是字符串"):
            parse_command_table("t", {"help": "x", "cmd": "echo", "message": 42})

    def test_message_injected_on_synth(self) -> None:
        """synth 注入 __dsl_message__；未声明时不注入。"""
        with_message = build_tool_spec(CommandDecl(name="t", help="x", cmd="echo", message="done"))
        assert getattr(with_message.func, "__dsl_message__", None) == "done"
        without_message = build_tool_spec(CommandDecl(name="t", help="x", cmd="echo"))
        assert not hasattr(without_message.func, "__dsl_message__")

    def test_message_printed_after_success(
        self,
        user_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        reset_discovery: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """run_tool 全链路：执行成功后打印插值消息。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.hello]",
                    'help = "问好"',
                    'cmd = "echo hi {name}"',
                    'message = "已向 {name} 问好"',
                    "[commands.hello.args.name]",
                    'help = "名字"',
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        monkeypatch.setattr(
            "fcmd.engine.task_command.subprocess.run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert run_tool("hello", ["world"]) == 0
        assert "已向 world 问好" in capsys.readouterr().out


class TestFailMessageDeclaration:
    """命令级 fail_message 声明的解析、注入与失败后打印。"""

    def test_fail_message_parses(self) -> None:
        """fail_message 字段解析保留。"""
        decl = parse_command_table("t", {"help": "x", "cmd": "echo", "fail_message": "失败 {name}"})
        assert decl.fail_message == "失败 {name}"

    def test_fail_message_default_empty(self) -> None:
        """未声明 fail_message 时为空串。"""
        assert parse_command_table("t", {"help": "x", "cmd": "echo"}).fail_message == ""

    def test_fail_message_non_string_rejected(self) -> None:
        """fail_message 非字符串报错。"""
        with pytest.raises(CommandDeclError, match="fail_message 须是字符串"):
            parse_command_table("t", {"help": "x", "cmd": "echo", "fail_message": 42})

    def test_fail_message_injected_on_synth(self) -> None:
        """synth 注入 __dsl_fail_message__；未声明时不注入。"""
        with_fm = build_tool_spec(CommandDecl(name="t", help="x", cmd="echo", fail_message="oops"))
        assert getattr(with_fm.func, "__dsl_fail_message__", None) == "oops"
        without_fm = build_tool_spec(CommandDecl(name="t", help="x", cmd="echo"))
        assert not hasattr(without_fm.func, "__dsl_fail_message__")

    def test_fail_message_printed_on_failure(
        self,
        user_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        reset_discovery: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """run_tool 全链路：任务失败时打印插值提示，退出码非零。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.deploy]",
                    'help = "部署"',
                    'cmd = "deploy {target}"',
                    'message = "已部署 {target}"',
                    'fail_message = "部署失败，可手动执行: deploy {target}"',
                    "[commands.deploy.args.target]",
                    'help = "目标"',
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        monkeypatch.setattr(
            "fcmd.engine.task_command.subprocess.run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "", "boom"),
        )
        assert run_tool("deploy", ["prod"]) == 1
        out = capsys.readouterr().out
        assert "部署失败，可手动执行: deploy prod" in out
        assert "已部署" not in out

    def test_message_not_printed_when_target_skipped(
        self,
        tmp_path: Path,
        user_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        reset_discovery: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """when 守卫跳过目标任务时不打印完成消息（SKIPPED 不等于执行成功）。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.guarded]",
                    'help = "守卫命令"',
                    'when = { path = "{marker}", expect = "exists" }',
                    'cmd = "echo ran {marker}"',
                    'message = "已执行 {marker}"',
                    "[commands.guarded.args.marker]",
                    'help = "标记文件"',
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        captured: list[Any] = []
        monkeypatch.setattr(
            "fcmd.engine.task_command.subprocess.run",
            lambda cmd, **kwargs: (captured.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1],
        )
        # 探针目标不存在 → SKIPPED → exit 0 但不打印完成消息
        missing = tmp_path / "missing-marker.txt"
        assert run_tool("guarded", [str(missing)]) == 0
        assert captured == []
        out = capsys.readouterr().out
        assert "已执行" not in out
        assert "条件不满足" in out


class TestContentExpansion:
    """``{参数名:content}`` 文件内容插值的声明校验与运行时展开。"""

    def test_content_parses_with_str_param(self) -> None:
        """str 参数的内容插值声明合法。"""
        decl = parse_command_table("t", {"help": "x", "cmd": ["use", "{cfg:content}"], "args": {"cfg": {"help": "c"}}})
        assert decl.cmd == ("use", "{cfg:content}")

    def test_content_unknown_param_rejected(self) -> None:
        """content 插值引用未声明参数报错。"""
        with pytest.raises(CommandDeclError, match="引用未声明的参数"):
            parse_command_table("t", {"help": "x", "cmd": ["use", "{cfg:content}"]})

    def test_content_non_str_param_rejected(self) -> None:
        """content 插值目标非 str/path 类型报错。"""
        with pytest.raises(CommandDeclError, match="仅支持 type=str/path"):
            parse_command_table(
                "t", {"help": "x", "cmd": ["use", "{n:content}"], "args": {"n": {"help": "n", "type": "int"}}}
            )

    def test_content_validated_on_platform_cmds(self) -> None:
        """win.cmd / unix.cmd 平台分支同样校验 content 插值。"""
        ok = parse_command_table("t", {"help": "x", "win": {"cmd": "use {p:content}"}, "args": {"p": {"help": "p"}}})
        assert ok.win_cmd == "use {p:content}"
        with pytest.raises(CommandDeclError, match="引用未声明的参数"):
            parse_command_table("t", {"help": "x", "unix": {"cmd": "use {p:content}"}})

    def test_content_expanded_from_file(
        self,
        tmp_path: Path,
        user_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        reset_discovery: None,
    ) -> None:
        """run_tool 全链路：文件内容（去首尾空白）替换进 tuple cmd。"""
        from fcmd.apis.toolkit import run_tool

        cfg = tmp_path / "app.conf"
        cfg.write_text("key=value\n", encoding="utf-8")
        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.inject]",
                    'help = "注入"',
                    'cmd = ["run", "{cfg:content}"]',
                    "[commands.inject.args.cfg]",
                    'help = "配置路径"',
                    'default = "x"',
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        captured: list[Any] = []
        monkeypatch.setattr(
            "fcmd.engine.task_command.subprocess.run",
            lambda cmd, **kwargs: (captured.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1],
        )
        assert run_tool("inject", ["--cfg", str(cfg)]) == 0
        assert captured[0] == ["run", "key=value"]

    def test_content_read_failure_keeps_literal(
        self,
        tmp_path: Path,
        user_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        reset_discovery: None,
    ) -> None:
        """文件读取失败（不存在）时保持字面 token，不抛异常。"""
        from fcmd.apis.toolkit import run_tool

        missing = tmp_path / "missing.conf"

        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.inject]",
                    'help = "注入"',
                    'cmd = ["run", "{cfg:content}"]',
                    "[commands.inject.args.cfg]",
                    'help = "配置路径"',
                    'default = "x"',
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        captured: list[Any] = []
        monkeypatch.setattr(
            "fcmd.engine.task_command.subprocess.run",
            lambda cmd, **kwargs: (captured.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1],
        )
        assert run_tool("inject", ["--cfg", str(missing)]) == 0
        assert captured[0] == ["run", "{cfg:content}"]


# ============================================================================ #
# 多子命令形态（decl.py：parse_tool_table / ToolDecl）
# ============================================================================ #
class TestToolTableParsing:
    """[commands.<tool>.<sub>] 多子命令形态的解析与校验。"""

    @staticmethod
    def _subs_table(**overrides: Any) -> dict[str, Any]:
        """构造双子命令的最小工具表（cmd + 聚合形态）。"""
        table: dict[str, Any] = {
            "lint": {"help": "lint", "cmd": "ruff check"},
            "chk": {"help": "聚合", "needs": ["lint"], "strategy": "thread"},
        }
        table.update(overrides)
        return table

    def test_parse_multi_sub_tool(self) -> None:
        """多子命令形态解析为 flat=False 的 ToolDecl。"""
        tool = parse_tool_table("pymakedemo", self._subs_table())
        assert tool.name == "pymakedemo"
        assert tool.flat is False
        assert [c.name for c in tool.commands] == ["lint", "chk"]
        assert tool.commands[1].needs == ("lint",)
        assert tool.commands[1].strategy == "thread"

    def test_flat_form_still_parsed(self) -> None:
        """单命令表自动识别为 flat=True。"""
        tool = parse_tool_table("solotool", {"help": "x", "cmd": "echo"})
        assert tool.flat is True
        assert tool.commands[0].name == "solotool"

    def test_mixed_form_rejected(self) -> None:
        """单命令与子命令形态混用报错。"""
        table = self._subs_table(cmd="echo oops")
        with pytest.raises(CommandDeclError, match="混用"):
            parse_tool_table("mixedtool", table)

    def test_sub_aliases_rejected(self) -> None:
        """子命令声明 aliases 报错（多子命令形态的别名在工具级声明）。"""
        table = self._subs_table(lint={"help": "lint", "cmd": "ruff", "aliases": ["l"]})
        with pytest.raises(CommandDeclError, match="aliases"):
            parse_tool_table("aliastool", table)

    def test_tool_level_metadata(self) -> None:
        """工具级 description/aliases 解析成功。"""
        table = self._subs_table()
        table["description"] = "演示工具"
        table["aliases"] = ["demo"]
        tool = parse_tool_table("metatable", table)
        assert tool.description == "演示工具"
        assert tool.aliases == ("demo",)

    def test_needs_self_reference_rejected(self) -> None:
        """needs 引用自身报错。"""
        table = {"solo": {"help": "x", "needs": ["solo"], "strategy": "sequential"}}
        with pytest.raises(CommandDeclError, match="自身"):
            parse_tool_table("selfref", table)

    def test_needs_missing_ref_rejected(self) -> None:
        """needs 引用不存在的子命令报错。"""
        table = {"a": {"help": "x", "cmd": "echo"}, "b": {"help": "y", "needs": ["ghost"]}}
        with pytest.raises(CommandDeclError, match="ghost"):
            parse_tool_table("missingref", table)

    def test_empty_tool_rejected(self) -> None:
        """工具表无任何子命令报错。"""
        with pytest.raises(CommandDeclError, match="至少声明一个子命令"):
            parse_tool_table("emptytool", {"description": "空"})

    def test_tool_level_unknown_key_rejected(self) -> None:
        """工具级未知键（非 description/aliases/子命令表）报错。"""
        with pytest.raises(CommandDeclError, match="hepl"):
            parse_tool_table("badtool", {"hepl": "typo", "a": {"help": "x", "cmd": "echo"}})


# ============================================================================ #
# needs / strategy 字段（decl.py）
# ============================================================================ #
class TestNeedsStrategyParsing:
    """needs 与 strategy 的解析与约束校验。"""

    def test_needs_and_strategy_parsed(self) -> None:
        """needs 与 strategy 解析成功（子命令形态）。"""
        decl = parse_command_table("chk", {"help": "x", "needs": ["a", "b"], "strategy": "thread"}, subcommand=True)
        assert decl.needs == ("a", "b")
        assert decl.strategy == "thread"

    def test_aggregate_without_cmd_allowed(self) -> None:
        """无 cmd 但有 needs 的聚合命令合法。"""
        decl = parse_command_table("agg", {"help": "x", "needs": ["a"]}, subcommand=True)
        assert decl.cmd is None
        assert decl.needs == ("a",)

    def test_no_cmd_and_no_needs_rejected(self) -> None:
        """无 cmd 且无 needs 报错（须至少提供一个）。"""
        with pytest.raises(CommandDeclError, match="至少一个"):
            parse_command_table("orphan", {"help": "x"}, subcommand=True)

    def test_needs_forbidden_in_flat_form(self) -> None:
        """单命令形态声明 needs 报错（needs 引用同工具其他子命令）。"""
        with pytest.raises(CommandDeclError, match="单命令形态"):
            parse_command_table("flat", {"help": "x", "cmd": "echo", "needs": ["other"]})

    def test_aggregate_args_allowed(self) -> None:
        """聚合命令（needs 且无 cmd）可声明 args（参数经共享 variables 流入子任务插值）。"""
        table: dict[str, Any] = {
            "help": "x",
            "needs": ["a"],
            "args": {"message": {"type": "str", "default": "chore: update", "help": "提交信息"}},
        }
        decl = parse_command_table("agg", table, subcommand=True)
        assert decl.needs == ("a",)
        assert len(decl.args) == 1
        assert decl.args[0].name == "message"
        assert decl.args[0].default == "chore: update"

    def test_cmd_with_needs_allowed(self) -> None:
        """cmd + needs 混合（如 gittool c）合法。"""
        decl = parse_command_table("c", {"help": "x", "cmd": "git status", "needs": ["clean"]}, subcommand=True)
        assert decl.cmd == "git status"
        assert decl.needs == ("clean",)

    @pytest.mark.parametrize("bad", [42, "thread", ["a", 1], [""], {"a": 1}])
    def test_bad_needs(self, bad: Any) -> None:
        """needs 须是非空字符串数组。"""
        with pytest.raises(CommandDeclError, match="needs"):
            parse_command_table("t", {"help": "x", "cmd": "echo", "needs": bad}, subcommand=True)

    @pytest.mark.parametrize("bad", ["parallel", "THREAD", 1, True])
    def test_bad_strategy(self, bad: Any) -> None:
        """strategy 取值超出四策略集合报错。"""
        with pytest.raises(CommandDeclError, match="strategy"):
            parse_command_table("t", {"help": "x", "needs": ["a"], "strategy": bad}, subcommand=True)


# ============================================================================ #
# 聚合 ToolSpec 合成（synth.py）与聚合判定（_tool_exec.py）
# ============================================================================ #
class TestAggregateSpecSynthesis:
    """needs/strategy 经 build_tool_spec 映射，合成函数标记聚合语义。"""

    def test_aggregate_spec_cmd_none(self) -> None:
        """聚合声明（needs 无 cmd）合成 spec：cmd=None、needs/strategy/命名透传。"""
        decl = CommandDecl(name="chk", help="聚合", needs=("lint", "fmt"), strategy="thread")
        spec = build_tool_spec(decl, tool_name="pymakedemo", subcommand="chk")
        assert spec.cmd is None
        assert spec.needs == ("lint", "fmt")
        assert spec.strategy == "thread"
        assert spec.name == "pymakedemo"
        assert spec.subcommand == "chk"

    def test_hybrid_spec_keeps_cmd(self) -> None:
        """cmd + needs 混合声明合成 spec：cmd 与 needs 并存（gittool c 形态）。"""
        decl = CommandDecl(name="c", help="清理并查看", cmd=("git", "status"), needs=("clean",))
        spec = build_tool_spec(decl, tool_name="gittooldemo", subcommand="c")
        assert spec.cmd == ("git", "status")
        assert spec.needs == ("clean",)

    def test_synthesized_func_marked_empty_body(self) -> None:
        """合成函数标记 __dsl_empty_body__，被 _is_aggregate 识别为无函数逻辑。"""
        from fcmd.apis._tool_exec import _is_aggregate

        decl = CommandDecl(name="chk", help="聚合", needs=("lint",), strategy="thread")
        spec = build_tool_spec(decl, tool_name="t", subcommand="chk")
        assert getattr(spec.func, "__dsl_empty_body__", False) is True
        assert _is_aggregate(spec) is True

    def test_plain_cmd_spec_not_aggregate(self) -> None:
        """普通 cmd 命令的合成函数同样有标记，但 _is_aggregate 因有 cmd 返回 False。"""
        from fcmd.apis._tool_exec import _is_aggregate

        decl = CommandDecl(name="b", help="构建", cmd="uv build")
        spec = build_tool_spec(decl, tool_name="t", subcommand="b")
        assert _is_aggregate(spec) is False


# ============================================================================ #
# 合并注册规则（_discovery.py：_register_dsl_tool 多子命令形态）
# ============================================================================ #
class TestRegisterDslToolMerge:
    """DSL 多子命令工具与既有注册表的合并规则。"""

    @staticmethod
    def _multi_tool(name: str, **meta: Any) -> ToolDecl:
        """构造双子命令工具声明（cmd + 聚合，测试辅助）。"""
        commands = (
            CommandDecl(name="go", help="go", cmd="echo go"),
            CommandDecl(name="agg", help="聚合", needs=("go",), strategy="thread"),
        )
        return ToolDecl(name=name, commands=commands, flat=False, **meta)

    def test_new_multi_sub_tool_registered(self, reset_discovery: None) -> None:
        """全新多子命令工具逐子命令注册，工具级 description 传播到各子命令。"""
        from fcmd.apis.toolkit import get_tool

        tool = self._multi_tool("mergetool", description="合并演示")
        discovery_mod._register_dsl_tool(tool, "builtin")
        go_spec = get_tool("mergetool", "go")
        assert go_spec.cmd == "echo go"
        assert go_spec.description == "合并演示"
        agg = get_tool("mergetool", "agg")
        assert agg.needs == ("go",) and agg.strategy == "thread"
        assert discovery_mod._DSL_TOOL_SOURCES["mergetool"] == "builtin"

    def test_merge_into_existing_python_tool(self, reset_discovery: None, caplog: pytest.LogCaptureFixture) -> None:
        """与既有 Python 工具重名：逐子命令合并，同名子命令跳过并 warning。"""
        from fcmd.apis.toolkit import ToolSpec, _register_tool, get_tool

        def existing_fn() -> None:
            """既有 Python 子命令占位。"""

        _register_tool(ToolSpec(name="mergetool", subcommand="go", func=existing_fn, help="Python 版 go", cmd="py-go"))
        with caplog.at_level("WARNING", logger="fcmd.cli._discovery"):
            discovery_mod._register_dsl_tool(self._multi_tool("mergetool"), "builtin")
        # 同名 go 保留 Python 版；agg 为 DSL 新增
        assert get_tool("mergetool", "go").cmd == "py-go"
        assert get_tool("mergetool", "agg").needs == ("go",)
        assert any("go" in r.message and "重名" in r.message for r in caplog.records)
        assert discovery_mod._DSL_TOOL_SOURCES["mergetool"] == "builtin"

    def test_multi_sub_idempotent_reentry(self, reset_discovery: None) -> None:
        """多子命令工具同源重入幂等：不抛子命令冲突异常。"""
        tool = self._multi_tool("mergetool")
        discovery_mod._register_dsl_tool(tool, "builtin")
        discovery_mod._register_dsl_tool(tool, "builtin")  # 不应抛 ValueError
        from fcmd.apis.toolkit import get_tool

        assert get_tool("mergetool", "go").cmd == "echo go"

    def test_tool_level_alias_registered(self, reset_discovery: None) -> None:
        """多子命令工具级别名注册进 _TOOL_ALIASES。"""
        tool = self._multi_tool("mergetool", aliases=("mt",))
        discovery_mod._register_dsl_tool(tool, "builtin")
        assert discovery_mod.resolve_tool("mt") == "mergetool"


# ============================================================================ #
# when 探针守卫与 allow_upstream_skip（decl.py / synth.py / _tool_exec.py）
# ============================================================================ #
class TestWhenDeclParsing:
    """when 探针声明与 allow_upstream_skip 键的解析校验。"""

    @staticmethod
    def _table(**when: Any) -> dict[str, Any]:
        """构造带 when 的最小命令表。"""
        return {"help": "x", "cmd": "echo", "when": when}

    def test_cmd_probe_parses(self) -> None:
        """cmd 探针（nonempty/empty）解析成功。"""
        decl = parse_command_table("t", self._table(cmd="git status --porcelain", expect="nonempty"))
        assert decl.when is not None
        assert decl.when.cmd == "git status --porcelain"
        assert decl.when.path is None
        assert decl.when.expect == "nonempty"
        empty = parse_command_table("t", self._table(cmd="exit 0", expect="empty"))
        assert empty.when is not None
        assert empty.when.expect == "empty"

    def test_path_probe_parses(self) -> None:
        """path 探针（exists/missing）解析成功。"""
        decl = parse_command_table("t", self._table(path=".git", expect="missing"))
        assert decl.when is not None
        assert decl.when.path == ".git"
        assert decl.when.cmd is None
        assert decl.when.expect == "missing"
        exists = parse_command_table("t", self._table(path="~/.fcmd", expect="exists"))
        assert exists.when is not None
        assert exists.when.expect == "exists"

    def test_no_when_defaults(self) -> None:
        """未声明 when / allow_upstream_skip 时为 None / False。"""
        decl = parse_command_table("t", {"help": "x", "cmd": "echo"})
        assert decl.when is None
        assert decl.allow_upstream_skip is False

    def test_when_not_table_rejected(self) -> None:
        """when 不是表报错。"""
        with pytest.raises(CommandDeclError, match="when 须是表"):
            parse_command_table("t", {"help": "x", "cmd": "echo", "when": "echo"})

    def test_when_unknown_key_rejected(self) -> None:
        """when 表内未知键报错。"""
        with pytest.raises(CommandDeclError, match="未知键"):
            parse_command_table("t", self._table(cmd="x", expect="nonempty", timeout=5))

    @pytest.mark.parametrize(
        "when",
        [
            {"expect": "nonempty"},  # cmd/path 均未提供
            {"cmd": "x", "path": "y", "expect": "nonempty"},  # 同时提供
        ],
    )
    def test_when_cmd_path_exclusive(self, when: dict[str, Any]) -> None:
        """cmd 与 path 须恰有其一。"""
        with pytest.raises(CommandDeclError, match="仅须提供 cmd 或 path 之一"):
            parse_command_table("t", self._table(**when))

    def test_when_empty_probe_rejected(self) -> None:
        """探针目标为空字符串报错。"""
        with pytest.raises(CommandDeclError, match="非空字符串"):
            parse_command_table("t", self._table(cmd="", expect="nonempty"))

    @pytest.mark.parametrize(
        "when",
        [
            {"cmd": "x"},  # 缺 expect
            {"cmd": "x", "expect": "exists"},  # cmd 探针用 path 枚举
            {"path": "x", "expect": "nonempty"},  # path 探针用 cmd 枚举
            {"path": "x", "expect": "success"},  # path 探针用返回码枚举
            {"path": "x"},  # 缺 expect
        ],
    )
    def test_when_bad_expect_rejected(self, when: dict[str, Any]) -> None:
        """expect 缺失或与探针类型不匹配报错。"""
        with pytest.raises(CommandDeclError, match=r"when\.expect"):
            parse_command_table("t", self._table(**when))

    def test_allow_upstream_skip_parses(self) -> None:
        """allow_upstream_skip 布尔值解析成功。"""
        decl = parse_command_table("t", {"help": "x", "cmd": "echo", "allow_upstream_skip": True})
        assert decl.allow_upstream_skip is True

    @pytest.mark.parametrize("bad", [1, "true", []])
    def test_allow_upstream_skip_bad_type(self, bad: Any) -> None:
        """allow_upstream_skip 非布尔值报错。"""
        with pytest.raises(CommandDeclError, match="allow_upstream_skip"):
            parse_command_table("t", {"help": "x", "cmd": "echo", "allow_upstream_skip": bad})

    def test_subcommand_underscore_prefix_allowed(self) -> None:
        """子命令允许下划线开头（`_init` 式内部隐藏子命令约定）。"""
        decl = parse_command_table("_init", {"help": "x", "cmd": "git init"}, subcommand=True)
        assert decl.name == "_init"

    def test_tool_name_underscore_prefix_rejected(self) -> None:
        """工具名仍禁止下划线开头（内部命名仅限子命令）。"""
        with pytest.raises(CommandDeclError, match="命令名"):
            parse_command_table("_bad", {"help": "x", "cmd": "echo"})


class TestWhenGuardExecution:
    """when 探针闭包构造、SKIPPED 语义与 allow_upstream_skip 豁免。"""

    def test_conditions_empty_without_when(self) -> None:
        """无 when 声明的 DSL 命令 conditions 为空元组。"""
        from fcmd.apis._tool_exec import _build_conditions

        spec = build_tool_spec(CommandDecl(name="t", help="x", cmd="echo"))
        assert _build_conditions(spec, {}) == ()

    def test_when_injected_on_synth(self) -> None:
        """synth 注入 __dsl_when__；未声明 when 时不注入。"""
        with_when = build_tool_spec(
            CommandDecl(name="t", help="x", cmd="echo", when=parse_command_table("t", self._when_table()).when)
        )
        assert getattr(with_when.func, "__dsl_when__", None) is not None
        without = build_tool_spec(CommandDecl(name="t", help="x", cmd="echo"))
        assert not hasattr(without.func, "__dsl_when__")

    @staticmethod
    def _when_table() -> dict[str, Any]:
        """构造带 when cmd 探针的最小命令表。"""
        return {"help": "x", "cmd": "git commit", "when": {"cmd": "git status --porcelain", "expect": "nonempty"}}

    def test_cmd_probe_closure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cmd 探针按 stdout 是否非空与 expect 判定，_reason 描述探针。"""
        from fcmd.apis._tool_exec import _build_conditions

        spec = build_tool_spec(parse_command_table("t", self._when_table()))
        conditions = _build_conditions(spec, {})
        assert len(conditions) == 1
        probe = conditions[0]
        # stdout 非空 → 满足 nonempty 期望
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, " M file\n", ""),
        )
        assert probe({}) is True
        # stdout 为空 → 不满足
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert probe({}) is False
        reason = getattr(probe, "_reason", None)
        assert reason is not None
        assert "git status --porcelain" in reason
        assert "非空" in reason

    def test_cmd_probe_empty_expect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cmd 探针 expect="empty"：stdout 为空时满足。"""
        from fcmd.apis._tool_exec import _build_conditions

        decl = parse_command_table(
            "t", {"help": "x", "cmd": "echo", "when": {"cmd": "cmd /c exit 0", "expect": "empty"}}
        )
        spec = build_tool_spec(decl)
        probe = _build_conditions(spec, {})[0]
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert probe({}) is True
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "out", ""),
        )
        assert probe({}) is False

    def test_cmd_probe_returncode_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cmd 探针 expect="success"：返回码 0 时满足，与 stdout 无关。"""
        from fcmd.apis._tool_exec import _build_conditions

        decl = parse_command_table(
            "t", {"help": "x", "cmd": "echo", "when": {"cmd": "git diff --quiet", "expect": "success"}}
        )
        probe = _build_conditions(build_tool_spec(decl), {})[0]
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert probe({}) is True  # rc 0 → success 满足（stdout 为空不影响）
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "changes"),
        )
        assert probe({}) is False
        reason = getattr(probe, "_reason", None)
        assert reason is not None
        assert "为 0" in reason

    def test_cmd_probe_returncode_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cmd 探针 expect="failure"：返回码非 0 时满足。"""
        from fcmd.apis._tool_exec import _build_conditions

        decl = parse_command_table(
            "t", {"help": "x", "cmd": "echo", "when": {"cmd": "git diff --quiet", "expect": "failure"}}
        )
        probe = _build_conditions(build_tool_spec(decl), {})[0]
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""),
        )
        assert probe({}) is True
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert probe({}) is False
        assert "非 0" in str(probe._reason)  # type: ignore[missing-attribute]

    def test_cmd_probe_interpolation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cmd 探针支持 ``{参数名}`` 插值（构造时求值，reason 同步插值后命令）。"""
        from fcmd.apis._tool_exec import _build_conditions

        decl = parse_command_table(
            "t",
            {
                "help": "x",
                "cmd": "echo done",
                "args": {"branch": {"help": "分支名"}},
                "when": {"cmd": "test -n {branch}", "expect": "nonempty"},
            },
        )
        spec = build_tool_spec(decl)
        probe = _build_conditions(spec, {"branch": "main"})[0]
        captured: list[Any] = []

        def fake_run(cmd: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "out", "")

        monkeypatch.setattr("fcmd.apis._tool_exec.subprocess.run", fake_run)
        assert probe({}) is True
        assert captured == ["test -n main"]  # 探针已插值
        assert "test -n main" in str(probe._reason)  # type: ignore[missing-attribute]

    def test_path_probe_interpolation(self, tmp_path: Path) -> None:
        """path 探针支持 ``{参数名}`` 插值（构造时求值，``~`` 展开语义保留）。"""
        from fcmd.apis._tool_exec import _build_conditions

        marker = tmp_path / "dest"
        decl = parse_command_table(
            "t",
            {
                "help": "x",
                "cmd": "echo",
                "args": {"dest": {"help": "目标路径"}},
                "when": {"path": "{dest}", "expect": "exists"},
            },
        )
        probe = _build_conditions(build_tool_spec(decl), {"dest": str(marker)})[0]
        assert probe({}) is False  # 文件未创建 → 不满足
        marker.write_text("x", encoding="utf-8")
        assert probe({}) is True
        assert str(marker) in str(probe._reason)  # type: ignore[missing-attribute]

    def test_path_probe_closure(self, tmp_path: Path) -> None:
        """path 探针按存在性与 expect 判定。"""
        from fcmd.apis._tool_exec import _build_conditions

        marker = tmp_path / "marker"
        decl = parse_command_table("t", {"help": "x", "cmd": "echo", "when": {"path": str(marker), "expect": "exists"}})
        probe = _build_conditions(build_tool_spec(decl), {})[0]
        assert probe({}) is False  # 不存在 → 不满足 exists
        marker.write_text("x", encoding="utf-8")
        assert probe({}) is True
        assert "marker" in str(probe._reason)  # type: ignore[missing-attribute]

    def test_path_probe_missing_expect(self, tmp_path: Path) -> None:
        """path 探针 expect="missing"：不存在时满足。"""
        from fcmd.apis._tool_exec import _build_conditions

        marker = tmp_path / "gone"
        decl = parse_command_table(
            "t", {"help": "x", "cmd": "echo", "when": {"path": str(marker), "expect": "missing"}}
        )
        probe = _build_conditions(build_tool_spec(decl), {})[0]
        assert probe({}) is True
        marker.write_text("x", encoding="utf-8")
        assert probe({}) is False

    def test_probe_exception_treated_unsatisfied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """探针命令执行异常 → should_execute 视为条件不满足。"""
        from fcmd.apis._tool_exec import _build_task_spec

        def boom(cmd: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "命令不存在")

        monkeypatch.setattr("fcmd.apis._tool_exec.subprocess.run", boom)
        spec = build_tool_spec(parse_command_table("t", self._when_table()))
        task = _build_task_spec(spec, {})
        assert task.conditions
        should_run, reason = task.should_execute({})
        assert should_run is False
        assert reason is not None

    def test_guard_skip_exit_zero(
        self,
        user_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        reset_discovery: None,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """when 不满足：任务 SKIPPED、命令不执行、exit 0、提示条件不满足。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.pick]",
                    'help = "按存在性执行"',
                    'cmd = "echo picked"',
                    f'when = {{path = "{(user_home / "no-such-marker").as_posix()}", expect = "exists"}}',
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        captured: list[Any] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        assert run_tool("pick", []) == 0
        assert captured == []  # 命令未执行
        assert "条件不满足" in capsys.readouterr().out

    def test_downstream_skip_and_allow_upstream_skip(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """上游守卫 SKIPPED：默认连坐下游；allow_upstream_skip 豁免照常执行。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.guardtool._gate]",
                    'help = "闸门"',
                    'cmd = "echo gate"',
                    f'when = {{path = "{(user_home / "no-such-marker").as_posix()}", expect = "exists"}}',
                    "hidden = true",
                    "[commands.guardtool.follow]",
                    'help = "连坐"',
                    'cmd = "echo follow"',
                    'needs = ["_gate"]',
                    "[commands.guardtool.exempt]",
                    'help = "豁免"',
                    'cmd = "echo exempt"',
                    'needs = ["_gate"]',
                    "allow_upstream_skip = true",
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        captured: list[Any] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", fake_run)
        # follow 连坐 SKIPPED（exit 0），echo follow 不执行
        captured.clear()
        assert run_tool("guardtool", ["follow"]) == 0
        assert captured == []
        # exempt 经 allow_upstream_skip 豁免，echo exempt 照常执行
        captured.clear()
        assert run_tool("guardtool", ["exempt"]) == 0
        assert captured == ["echo exempt"]

    def test_dry_run_skips_probes(
        self, user_home: Path, monkeypatch: pytest.MonkeyPatch, reset_discovery: None
    ) -> None:
        """--dry-run 仅打印计划：探针不求值（引擎在任务执行前短路）。"""
        from fcmd.apis.toolkit import run_tool

        (user_home / "commands.toml").write_text(
            "\n".join(
                [
                    "[commands.prober]",
                    'help = "探针命令"',
                    'cmd = "echo go"',
                    'when = {cmd = "echo probe", expect = "nonempty"}',
                ]
            ),
            encoding="utf-8",
        )
        discovery_mod.ensure_tools_discovered()
        probe_calls: list[Any] = []
        monkeypatch.setattr(
            "fcmd.apis._tool_exec.subprocess.run",
            lambda cmd, **kw: probe_calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        task_calls: list[Any] = []
        monkeypatch.setattr(
            "fcmd.engine.task_command.subprocess.run",
            lambda cmd, **kw: task_calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        assert run_tool("prober", ["--dry-run"]) == 0
        assert probe_calls == []  # 探针未求值
        assert task_calls == []  # 任务未执行

    def test_aggregate_with_params_uses_full_parser(self) -> None:
        """带参聚合：parser 复用 _build_parser_for_tool，--message 可解析。"""
        from fcmd.apis._tool_exec import _parse_tool_args

        decl = parse_command_table(
            "agg",
            {
                "help": "聚合",
                "needs": ["sub"],
                "args": {"message": {"type": "str", "default": "chore: update", "help": "提交信息"}},
            },
            subcommand=True,
        )
        spec = build_tool_spec(decl, tool_name="t", subcommand="agg")
        parsed = _parse_tool_args("t", "agg", ["--message", "hello"], {"agg": spec})
        assert not isinstance(parsed, int)
        variables, resolved = parsed
        assert variables["message"] == "hello"
        assert resolved is spec

    def test_aggregate_without_params_keeps_bare_parser(self) -> None:
        """无参聚合：保持裸 parser（仅全局选项），未知参数解析失败返回 FAILURE。"""
        from fcmd.apis._tool_exec import _parse_tool_args

        decl = parse_command_table("agg", {"help": "聚合", "needs": ["sub"]}, subcommand=True)
        spec = build_tool_spec(decl, tool_name="t", subcommand="agg")
        result = _parse_tool_args("t", "agg", ["--message", "x"], {"agg": spec})
        assert isinstance(result, int)
        assert result != 0


# ============================================================================ #
# tty 透传与 default_env 环境变量回退链
# ============================================================================ #
class TestTtyAndEnvDefault:
    """命令级 tty 声明（→ TaskSpec.passthrough）与参数级 default_env。"""

    # ---------------- decl 解析 ---------------- #
    def test_parse_tty(self) -> None:
        """tty = true 解析进 CommandDecl。"""
        decl = parse_command_table("ttool", {"help": "h", "cmd": "echo hi", "tty": True})
        assert decl.tty is True

    def test_tty_defaults_false(self) -> None:
        """未声明 tty 默认 False。"""
        decl = parse_command_table("ttool", {"help": "h", "cmd": "echo hi"})
        assert decl.tty is False

    def test_tty_must_be_bool(self) -> None:
        """tty 非布尔值报错。"""
        with pytest.raises(CommandDeclError, match="tty 须是布尔值"):
            parse_command_table("ttool", {"help": "h", "cmd": "echo hi", "tty": "yes"})

    # ---------------- default_env 校验 ---------------- #
    @staticmethod
    def _env_table(arg: dict[str, Any]) -> dict[str, Any]:
        """单参数 login 声明表（default_env 校验用）。"""
        return {"help": "h", "cmd": "echo {username}", "args": {"username": arg}}

    def test_default_env_str_and_list_forms(self) -> None:
        """default_env 接受单字符串与非空字符串数组。"""
        decl = parse_command_table("ttool", self._env_table({"default": "", "default_env": "USERNAME"}))
        assert decl.args[0].default_env == ("USERNAME",)
        decl = parse_command_table("ttool", self._env_table({"default": "", "default_env": ["USERNAME", "USER"]}))
        assert decl.args[0].default_env == ("USERNAME", "USER")

    def test_default_env_requires_str_type(self) -> None:
        """非 str 类型声明 default_env 报错。"""
        arg = {"type": "int", "default": 0, "default_env": "N"}
        with pytest.raises(CommandDeclError, match="仅 type=str 可声明 default_env"):
            parse_command_table("ttool", self._env_table(arg))

    def test_default_env_requires_default(self) -> None:
        """声明 default_env 须同时提供 default（positional 参数无默认可比对）。"""
        arg = {"default_env": "USERNAME"}
        with pytest.raises(CommandDeclError, match="声明 default_env 须同时提供 default"):
            parse_command_table("ttool", self._env_table(arg))

    @pytest.mark.parametrize("raw", ["", []])
    def test_default_env_empty_rejected(self, raw: Any) -> None:
        """default_env 空字符串/空数组报错（配置错误不应被静默忽略）。"""
        with pytest.raises(CommandDeclError, match="default_env 须是非空字符串或非空字符串数组"):
            parse_command_table("ttool", self._env_table({"default": "", "default_env": raw}))

    # ---------------- synth 合成 ---------------- #
    def test_synthesizes_passthrough_and_param_env(self) -> None:
        """tty → ToolSpec.passthrough；default_env → __dsl_param_env__ 契约。"""
        table = {
            "help": "登录",
            "cmd": ["docker", "login", "{username}"],
            "tty": True,
            "args": {"username": {"default": "", "default_env": ["USERNAME", "USER"]}},
        }
        spec = build_tool_spec(parse_command_table("dtool", table))
        assert spec.passthrough is True
        assert getattr(spec.func, "__dsl_param_env__", None) == {"username": (("USERNAME", "USER"), "")}

    def test_no_default_env_no_attribute(self) -> None:
        """未声明 default_env 不注入 __dsl_param_env__。"""
        spec = build_tool_spec(parse_command_table("ttool", {"help": "h", "cmd": "echo hi"}))
        assert getattr(spec.func, "__dsl_param_env__", None) is None

    def test_build_task_spec_maps_passthrough(self) -> None:
        """_build_task_spec 将 ToolSpec.passthrough 映射到 TaskSpec.passthrough。"""
        spec = build_tool_spec(parse_command_table("ttool", {"help": "h", "cmd": "echo hi", "tty": True}))
        assert _build_task_spec(spec, {}).passthrough is True

    # ---------------- 引擎环境回退链解析 ---------------- #
    @staticmethod
    def _env_spec() -> ToolSpec:
        """username 参数带 default_env 链的 ToolSpec。"""
        table = {
            "help": "登录",
            "cmd": "docker login {username}",
            "args": {"username": {"default": "", "default_env": ["USERNAME", "LOGNAME", "USER"]}},
        }
        return build_tool_spec(parse_command_table("etool", table))

    def test_apply_env_defaults_resolves_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI 值等于声明默认值 → 取链中第一个非空环境变量。"""
        # 清空链中位于 USER 之前的全部变量（CI 的 Linux runner 预置 LOGNAME=runner）
        for name in ("USERNAME", "LOGNAME"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("USER", "posixuser")
        variables: dict[str, Any] = {"username": ""}
        _apply_env_defaults(variables, self._env_spec())
        assert variables["username"] == "posixuser"

    def test_apply_env_defaults_skips_explicit_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI 显式值不等于默认值 → 不查环境变量。"""
        monkeypatch.setenv("USER", "posixuser")
        variables: dict[str, Any] = {"username": "admin"}
        _apply_env_defaults(variables, self._env_spec())
        assert variables["username"] == "admin"

    def test_apply_env_defaults_chain_all_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """链上环境变量全部缺失 → 保持声明默认值。"""
        for name in ("USERNAME", "LOGNAME", "USER"):
            monkeypatch.delenv(name, raising=False)
        variables: dict[str, Any] = {"username": ""}
        _apply_env_defaults(variables, self._env_spec())
        assert variables["username"] == ""

    def test_apply_env_defaults_skips_empty_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """设置为空串的环境变量视为未设，继续向后回退。"""
        monkeypatch.setenv("USERNAME", "")
        monkeypatch.setenv("LOGNAME", "loguser")
        variables: dict[str, Any] = {"username": ""}
        _apply_env_defaults(variables, self._env_spec())
        assert variables["username"] == "loguser"

    def test_apply_env_defaults_noop_without_contract(self) -> None:
        """无 __dsl_param_env__ 契约的函数（普通 Python 工具）为空操作。"""

        def plain(username: str = "") -> None:
            """普通工具函数。"""

        variables: dict[str, Any] = {"username": ""}
        _apply_env_defaults(variables, ToolSpec(name="plain", subcommand=None, func=plain))
        assert variables["username"] == ""
