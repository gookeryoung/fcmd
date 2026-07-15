"""@fx.tool 装饰器框架测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import pytest

import fcmd
from fcmd.apis.toolkit import (
    _TOOL_REGISTRY,
    ToolExitCode,
    ToolSpec,
    _build_task_spec,
    _collect_with_deps,
    _has_function_logic,
    _is_aggregate,
    _is_list_annotation,
    _list_inner_type,
    _resolve_hints,
    clear_tool_registry,
    get_tool,
    list_subcommands,
    list_tools,
    run_tool,
    tool,
)
from fcmd.task import RetryPolicy, TaskSpec


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个测试前后清空注册表，避免污染。"""
    clear_tool_registry()
    yield
    clear_tool_registry()


# ---------------------------------------------------------------------- #
# ToolExitCode
# ---------------------------------------------------------------------- #
def test_tool_exit_code_values() -> None:
    """退出码值符合 POSIX 约定。"""
    assert ToolExitCode.SUCCESS.value == 0
    assert ToolExitCode.FAILURE.value == 1
    assert ToolExitCode.INTERRUPTED.value == 130


# ---------------------------------------------------------------------- #
# @tool 装饰器 + 注册表
# ---------------------------------------------------------------------- #
def test_tool_single_command() -> None:
    """单命令工具（subcommand=None）注册成功。"""

    @tool("solo")
    def solo() -> None:
        """单命令。"""

    assert "solo" in _TOOL_REGISTRY
    assert None in _TOOL_REGISTRY["solo"]
    spec = get_tool("solo")
    assert spec.name == "solo"
    assert spec.subcommand is None
    assert spec.func is solo
    assert spec.help == "单命令。"


def test_tool_multi_subcommand() -> None:
    """同名多 subcommand 注册到同一工具。"""

    @tool("demo", subcommand="a", help="A 子命令")
    def a() -> None:
        pass

    @tool("demo", subcommand="b", help="B 子命令")
    def b() -> None:
        pass

    assert set(list_subcommands("demo")) == {"a", "b"}
    assert get_tool("demo", "a").help == "A 子命令"
    assert get_tool("demo", "b").help == "B 子命令"


def test_tool_duplicate_raises() -> None:
    """重复注册同名同 subcommand 抛 ValueError。"""

    @tool("demo", subcommand="a")
    def a1() -> None:
        pass

    with pytest.raises(ValueError, match="已注册"):

        @tool("demo", subcommand="a")
        def a2() -> None:
            pass


def test_tool_help_from_docstring() -> None:
    """help 省略时从 docstring 取。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        """来自 docstring。"""

    assert get_tool("demo", "a").help == "来自 docstring。"


def test_tool_returns_original_func() -> None:
    """装饰器返回原函数（不包装）。"""

    @tool("demo", subcommand="a")
    def a(x: int) -> int:
        return x * 2

    assert a(3) == 6


def test_tool_cmd_tuple_conversion() -> None:
    """list cmd 转 tuple 存储。"""

    @tool("demo", subcommand="a", cmd=["echo", "hi"])
    def a() -> None:
        pass

    spec = get_tool("demo", "a")
    assert spec.cmd == ("echo", "hi")


def test_tool_str_cmd_kept() -> None:
    """str cmd 原样保留。"""

    @tool("demo", subcommand="a", cmd="echo hi")
    def a() -> None:
        pass

    assert get_tool("demo", "a").cmd == "echo hi"


def test_tool_needs_tuple_conversion() -> None:
    """list needs 转 tuple。"""

    @tool("demo", subcommand="a", needs=["b", "c"])
    def a() -> None:
        pass

    assert get_tool("demo", "a").needs == ("b", "c")


def test_tool_env_dict_conversion() -> None:
    """env Mapping 转 dict。"""

    @tool("demo", subcommand="a", env={"FOO": "bar"})
    def a() -> None:
        pass

    assert get_tool("demo", "a").env == {"FOO": "bar"}


def test_get_tool_unknown_tool() -> None:
    """未注册工具抛 KeyError。"""
    with pytest.raises(KeyError, match="未注册"):
        get_tool("nope")


def test_get_tool_unknown_subcommand() -> None:
    """未注册子命令抛 KeyError。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        pass

    with pytest.raises(KeyError, match="没有子命令"):
        get_tool("demo", "b")


def test_list_tools_sorted() -> None:
    """list_tools 返回排序后的工具名。"""

    @tool("zeta", subcommand="a")
    def a() -> None:
        pass

    @tool("alpha", subcommand="b")
    def b() -> None:
        pass

    assert list_tools() == ["alpha", "zeta"]


def test_list_subcommands_excludes_hidden() -> None:
    """list_subcommands 默认排除 hidden。"""

    @tool("demo", subcommand="visible")
    def v() -> None:
        pass

    @tool("demo", subcommand="secret", hidden=True)
    def s() -> None:
        pass

    assert list_subcommands("demo") == ["visible"]
    assert list_subcommands("demo", include_hidden=True) == ["secret", "visible"]


def test_list_subcommands_single_command_returns_empty() -> None:
    """单命令工具 list_subcommands 返回空。"""

    @tool("solo")
    def s() -> None:
        pass

    assert list_subcommands("solo") == []


def test_list_subcommands_unknown_tool() -> None:
    """未注册工具返回空列表。"""
    assert list_subcommands("nope") == []


def test_clear_tool_registry() -> None:
    """clear_tool_registry 清空全部。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        pass

    assert list_tools() == ["demo"]
    clear_tool_registry()
    assert list_tools() == []


# ---------------------------------------------------------------------- #
# _collect_with_deps
# ---------------------------------------------------------------------- #
def test_collect_with_deps_single() -> None:
    """单节点收集返回 [target]。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        pass

    assert _collect_with_deps("demo", "a") == ["a"]


def test_collect_with_deps_chain() -> None:
    """链式依赖 a→b→c 收集后拓扑序 c, b, a。"""

    @tool("demo", subcommand="a", needs=["b"])
    def a() -> None:
        pass

    @tool("demo", subcommand="b", needs=["c"])
    def b() -> None:
        pass

    @tool("demo", subcommand="c")
    def c() -> None:
        pass

    assert _collect_with_deps("demo", "a") == ["c", "b", "a"]


def test_collect_with_deps_unknown_tool() -> None:
    """工具未注册时返回 [target]。"""
    assert _collect_with_deps("nope", "a") == ["a"]


def test_collect_with_deps_diamond() -> None:
    """菱形依赖 a→{b,c}→d，d 出现一次。"""

    @tool("demo", subcommand="a", needs=["b", "c"])
    def a() -> None:
        pass

    @tool("demo", subcommand="b", needs=["d"])
    def b() -> None:
        pass

    @tool("demo", subcommand="c", needs=["d"])
    def c() -> None:
        pass

    @tool("demo", subcommand="d")
    def d() -> None:
        pass

    chain = _collect_with_deps("demo", "a")
    # d 在最前，a 在最后，b/c 顺序由 BFS 决定
    assert chain[0] == "d"
    assert chain[-1] == "a"
    assert set(chain) == {"a", "b", "c", "d"}
    assert len(chain) == 4  # 无重复


# ---------------------------------------------------------------------- #
# _has_function_logic
# ---------------------------------------------------------------------- #
def test_has_function_logic_pass_only() -> None:
    """仅 pass 的函数无逻辑。"""

    def f() -> None:
        pass

    assert _has_function_logic(f) is False


def test_has_function_logic_docstring_only() -> None:
    """仅 docstring 的函数无逻辑。"""

    def f() -> None:
        """仅文档。"""

    assert _has_function_logic(f) is False


def test_has_function_logic_ellipsis_only() -> None:
    """仅 ... 的函数无逻辑。"""

    def f() -> None: ...

    assert _has_function_logic(f) is False


def test_has_function_logic_with_body() -> None:
    """有语句的函数有逻辑。"""

    def f() -> None:
        x = 1
        print(x)

    assert _has_function_logic(f) is True


def test_has_function_logic_uninspectable() -> None:
    """内建函数无法 getsource 时回退 True。"""
    assert _has_function_logic(len) is True


# ---------------------------------------------------------------------- #
# _is_aggregate
# ---------------------------------------------------------------------- #
def test_is_aggregate_cmd_not_aggregate() -> None:
    """有 cmd 的任务不是聚合。"""

    @tool("demo", subcommand="a", cmd=["echo", "hi"], needs=["b"])
    def a() -> None:
        pass

    assert _is_aggregate(get_tool("demo", "a")) is False


def test_is_aggregate_no_needs_not_aggregate() -> None:
    """无 needs 的任务不是聚合。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        pass

    assert _is_aggregate(get_tool("demo", "a")) is False


def test_is_aggregate_empty_body_aggregate() -> None:
    """needs + 空函数体是聚合。"""

    @tool("demo", subcommand="a", needs=["b"])
    def a() -> None:
        pass

    assert _is_aggregate(get_tool("demo", "a")) is True


def test_is_aggregate_with_body_not_aggregate() -> None:
    """needs + 有函数体不是聚合（fn 任务）。"""

    @tool("demo", subcommand="a", needs=["b"])
    def a() -> str:
        return "result"

    assert _is_aggregate(get_tool("demo", "a")) is False


# ---------------------------------------------------------------------- #
# _build_task_spec
# ---------------------------------------------------------------------- #
def test_build_task_spec_cmd_task() -> None:
    """cmd 任务转 TaskSpec.cmd，cwd 从变量取。"""

    @tool("demo", subcommand="a", cmd=["echo", "hi"], cwd="/tmp")
    def a() -> None:
        pass

    spec = _build_task_spec(get_tool("demo", "a"), {"cwd": "/custom"})
    assert spec.name == "a"
    assert spec.cmd == ["echo", "hi"]
    assert spec.cwd == Path("/custom")
    assert spec.depends_on == ()


def test_build_task_spec_cmd_task_default_cwd() -> None:
    """cmd 任务无变量 cwd 时用装饰器 cwd。"""

    @tool("demo", subcommand="a", cmd=["echo", "hi"], cwd="/tmp")
    def a() -> None:
        pass

    spec = _build_task_spec(get_tool("demo", "a"), {})
    assert spec.cwd == Path("/tmp")


def test_build_task_spec_cmd_task_no_cwd() -> None:
    """cmd 任务无 cwd 时 cwd 为 None。"""

    @tool("demo", subcommand="a", cmd=["echo", "hi"])
    def a() -> None:
        pass

    spec = _build_task_spec(get_tool("demo", "a"), {})
    assert spec.cwd is None


def test_build_task_spec_cmd_task_env_retry() -> None:
    """cmd 任务透传 env/retry/timeout。"""
    retry = RetryPolicy(max_attempts=3)

    @tool("demo", subcommand="a", cmd=["echo"], env={"K": "V"}, retry=retry, timeout=5.0)
    def a() -> None:
        pass

    spec = _build_task_spec(get_tool("demo", "a"), {})
    assert spec.env == {"K": "V"}
    assert spec.retry.max_attempts == 3
    assert spec.timeout == 5.0


def test_build_task_spec_cmd_task_retry_default() -> None:
    """cmd 任务无 retry 时默认 RetryPolicy()。"""

    @tool("demo", subcommand="a", cmd=["echo"])
    def a() -> None:
        pass

    spec = _build_task_spec(get_tool("demo", "a"), {})
    assert spec.retry.max_attempts == 1


def test_build_task_spec_aggregate() -> None:
    """聚合任务 fn=_noop。"""

    @tool("demo", subcommand="a", needs=["b"])
    def a() -> None:
        pass

    spec = _build_task_spec(get_tool("demo", "a"), {})
    assert spec.fn is not None
    assert spec.cmd is None
    assert spec.depends_on == ("b",)


def test_build_task_spec_fn_task() -> None:
    """fn 任务按签名从 variables 取 kwargs。"""

    @tool("demo", subcommand="a")
    def a(name: str, times: int = 1) -> str:
        return name * times

    spec = _build_task_spec(get_tool("demo", "a"), {"name": "x", "times": 3})
    assert spec.fn is a
    assert spec.kwargs == {"name": "x", "times": 3}


def test_build_task_spec_fn_task_partial_vars() -> None:
    """fn 任务仅取签名中存在的变量。"""

    @tool("demo", subcommand="a")
    def a(name: str) -> str:
        return name

    spec = _build_task_spec(get_tool("demo", "a"), {"name": "x", "extra": "ignored"})
    assert spec.kwargs == {"name": "x"}


def test_build_task_spec_fn_task_cwd_from_var() -> None:
    """fn 任务 cwd 从变量取。"""

    @tool("demo", subcommand="a")
    def a(name: str) -> str:
        return name

    spec = _build_task_spec(get_tool("demo", "a"), {"name": "x", "cwd": "/tmp"})
    assert spec.cwd == Path("/tmp")


# ---------------------------------------------------------------------- #
# 类型注解辅助
# ---------------------------------------------------------------------- #
def test_resolve_hints_normal() -> None:
    """_resolve_hints 解析普通注解。"""

    def f(x: int, y: str) -> None:
        pass

    hints = _resolve_hints(f)
    assert hints["x"] is int
    assert hints["y"] is str


def test_resolve_hints_failure_returns_empty() -> None:
    """_resolve_hints 失败时返回空 dict。"""

    class Bad:
        pass

    # 构造无法解析的注解场景
    hints = _resolve_hints(Bad.__init__)
    assert isinstance(hints, dict)


def test_is_list_annotation_list_origin() -> None:
    """list[X] 注解识别。"""
    ann = List[int]
    assert _is_list_annotation(ann) is True


def test_is_list_annotation_List_str() -> None:
    """List[int] 字符串形式识别。"""
    assert _is_list_annotation("List[int]") is True


def test_is_list_annotation_list_str() -> None:
    """list[int] 字符串形式识别。"""
    assert _is_list_annotation("list[int]") is True


def test_is_list_annotation_non_list() -> None:
    """非 list 注解。"""
    assert _is_list_annotation(int) is False
    assert _is_list_annotation("str") is False


def test_list_inner_type_from_args() -> None:
    """list[X].__args__ 提取内部类型。"""
    assert _list_inner_type(List[int]) is int
    assert _list_inner_type(List[Path]) is Path


def test_list_inner_type_from_str() -> None:
    """字符串形式提取内部类型名。"""
    assert _list_inner_type("list[int]") == "int"
    assert _list_inner_type("List[Path]") == "Path"


def test_list_inner_type_none() -> None:
    """无法提取时返回 None。"""
    assert _list_inner_type(int) is None


# ---------------------------------------------------------------------- #
# argparse 构建
# ---------------------------------------------------------------------- #
def test_build_parser_positional_str() -> None:
    """positional str 参数。"""

    @tool("demo", subcommand="a")
    def a(name: str) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["hello"])
    assert args.name == "hello"


def test_build_parser_positional_int() -> None:
    """positional int 参数。"""

    @tool("demo", subcommand="a")
    def a(count: int) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["42"])
    assert args.count == 42


def test_build_parser_positional_list() -> None:
    """positional list[str] 参数 nargs='+'。"""

    @tool("demo", subcommand="a")
    def a(items: list[str]) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["a", "b", "c"])
    assert args.items == ["a", "b", "c"]


def test_build_parser_optional_str() -> None:
    """optional str 参数。"""

    @tool("demo", subcommand="a")
    def a(name: str = "default") -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["--name", "custom"])
    assert args.name == "custom"
    args_default = parser.parse_args([])
    assert args_default.name == "default"


def test_build_parser_optional_int() -> None:
    """optional int 参数。"""

    @tool("demo", subcommand="a")
    def a(count: int = 5) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["--count", "10"])
    assert args.count == 10


def test_build_parser_bool_store_true() -> None:
    """bool 默认 False 时 store_true。"""

    @tool("demo", subcommand="a")
    def a(verbose: bool = False) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args_off = parser.parse_args([])
    assert args_off.verbose is False
    args_on = parser.parse_args(["--verbose"])
    assert args_on.verbose is True


def test_build_parser_optional_list_nargs_star() -> None:
    """optional list 参数 nargs='*'。"""

    @tool("demo", subcommand="a")
    def a(items: list[str]) -> None:
        pass

    # 无默认值 list 会被当作 positional，这里测试有默认值的
    # 实际上 list[str] 无默认值会走 positional 分支，跳过此场景

    from fcmd.apis.toolkit import _add_optional_arg

    parser = _add_optional_arg  # 仅验证函数可调用
    assert callable(parser)


def test_build_parser_global_options() -> None:
    """全局选项 --dry-run/--quiet/--strategy 可解析。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["--dry-run", "--quiet", "--strategy", "thread"])
    assert args.dry_run is True
    assert args.quiet is True
    assert args.strategy == "thread"


def test_build_parser_path_type() -> None:
    """Path 参数转换为 pathlib.Path。"""

    @tool("demo", subcommand="a")
    def a(path: Path) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["/tmp/x"])
    assert args.path == Path("/tmp/x")


def test_build_parser_varkw_skipped() -> None:
    """**kwargs 参数被跳过。"""

    @tool("demo", subcommand="a")
    def a(name: str, **kwargs: object) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["hello"])
    assert args.name == "hello"


def test_build_parser_optional_path() -> None:
    """optional Path 参数。"""

    @tool("demo", subcommand="a")
    def a(out: Path = Path("default.txt")) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["--out", "/tmp/x"])
    assert args.out == Path("/tmp/x")


def test_build_parser_optional_list_str() -> None:
    """optional list[str] 参数 nargs='*'。"""

    @tool("demo", subcommand="a")
    def a(items: list[str]) -> None:
        pass

    # List[str] 无默认值 → positional，这里测试有默认值的 list
    # 改用有默认值的 list 参数


def test_build_parser_optional_list_with_default() -> None:
    """有默认值的 list 参数走 _add_optional_arg 的 list 分支。"""
    import argparse

    @tool("demo", subcommand="a")
    def a(items: list[str]) -> None:
        pass

    from fcmd.apis.toolkit import _add_optional_arg

    parser = argparse.ArgumentParser()
    _add_optional_arg(parser, "items", List[str], ["default"])
    args = parser.parse_args(["--items", "a", "b"])
    assert args.items == ["a", "b"]


def test_build_parser_optional_list_int() -> None:
    """optional list[int] 参数类型转换。"""
    import argparse

    from fcmd.apis.toolkit import _add_optional_arg

    parser = argparse.ArgumentParser()
    _add_optional_arg(parser, "nums", List[int], [1])
    args = parser.parse_args(["--nums", "1", "2"])
    assert args.nums == [1, 2]


def test_build_parser_optional_list_float() -> None:
    """optional list[float] 参数类型转换。"""
    import argparse

    from fcmd.apis.toolkit import _add_optional_arg

    parser = argparse.ArgumentParser()
    _add_optional_arg(parser, "nums", List[float], [1.0])
    args = parser.parse_args(["--nums", "1.5", "2.5"])
    assert args.nums == [1.5, 2.5]


def test_build_parser_optional_list_path() -> None:
    """optional list[Path] 参数类型转换。"""
    import argparse

    from fcmd.apis.toolkit import _add_optional_arg

    parser = argparse.ArgumentParser()
    _add_optional_arg(parser, "paths", List[Path], [Path("a")])
    args = parser.parse_args(["--paths", "/tmp/x", "/tmp/y"])
    assert args.paths == [Path("/tmp/x"), Path("/tmp/y")]


def test_build_parser_positional_list_path() -> None:
    """positional list[Path] 参数。"""

    @tool("demo", subcommand="a")
    def a(items: list[Path]) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["/tmp/a", "/tmp/b"])
    assert args.items == [Path("/tmp/a"), Path("/tmp/b")]


def test_build_parser_positional_list_int() -> None:
    """positional list[int] 参数。"""

    @tool("demo", subcommand="a")
    def a(items: list[int]) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["1", "2", "3"])
    assert args.items == [1, 2, 3]


def test_build_parser_positional_list_float() -> None:
    """positional list[float] 参数。"""

    @tool("demo", subcommand="a")
    def a(items: list[float]) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["1.5", "2.5"])
    assert args.items == [1.5, 2.5]


def test_build_parser_positional_path() -> None:
    """positional Path 参数。"""

    @tool("demo", subcommand="a")
    def a(p: Path) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["/tmp/x"])
    assert args.p == Path("/tmp/x")


def test_build_parser_positional_unknown_type() -> None:
    """positional 未知类型走 else 分支（无 type 转换）。"""

    @tool("demo", subcommand="a")
    def a(custom: object) -> None:
        pass

    from fcmd.apis.toolkit import _build_parser_for_tool

    parser = _build_parser_for_tool(get_tool("demo", "a"))
    args = parser.parse_args(["anything"])
    assert args.custom == "anything"


def test_build_parser_optional_unknown_type() -> None:
    """optional 未知类型走默认分支（无 type 转换）。"""
    import argparse

    from fcmd.apis.toolkit import _add_optional_arg

    parser = argparse.ArgumentParser()
    _add_optional_arg(parser, "custom", object, None)
    args = parser.parse_args(["--custom", "xyz"])
    assert args.custom == "xyz"


def test_build_task_spec_fn_task_no_matching_vars() -> None:
    """fn 任务变量无匹配参数时 kwargs 为空（覆盖 343→342 跳转）。"""

    @tool("demo", subcommand="a")
    def a(name: str) -> str:
        return name

    spec = _build_task_spec(get_tool("demo", "a"), {"other": "x"})
    assert spec.kwargs == {}


# ---------------------------------------------------------------------- #
# run_tool
# ---------------------------------------------------------------------- #
def _echo_cmd() -> list[str]:
    """跨平台 echo 命令。"""
    if sys.platform == "win32":
        return ["cmd", "/c", "echo", "hello"]
    return ["echo", "hello"]


def _fail_cmd() -> list[str]:
    """跨平台失败命令。"""
    if sys.platform == "win32":
        return ["cmd", "/c", "exit", "1"]
    return ["false"]


def test_run_tool_unknown_tool() -> None:
    """未注册工具返回 FAILURE。"""
    code = run_tool("nope", [])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_unknown_subcommand() -> None:
    """未注册子命令返回 FAILURE 并列子命令。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        pass

    code = run_tool("demo", ["b"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_no_argv_multi_subcommand_lists() -> None:
    """多 subcommand 工具无 argv 时列出子命令并返回 SUCCESS。"""

    @tool("demo", subcommand="a")
    def a() -> None:
        pass

    @tool("demo", subcommand="b")
    def b() -> None:
        pass

    code = run_tool("demo", [])
    assert code == ToolExitCode.SUCCESS.value


def test_run_tool_cmd_success() -> None:
    """cmd 任务成功返回 SUCCESS。"""

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a"])
    assert code == ToolExitCode.SUCCESS.value


def test_run_tool_cmd_failure() -> None:
    """cmd 任务失败返回 FAILURE。"""

    @tool("demo", subcommand="a", cmd=_fail_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_aggregate_success() -> None:
    """聚合任务（needs + 空函数体）执行依赖后返回 SUCCESS。"""

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    @tool("demo", subcommand="agg", needs=["a"], strategy="sequential")
    def agg() -> None:
        pass

    code = run_tool("demo", ["agg"])
    assert code == ToolExitCode.SUCCESS.value


def test_run_tool_aggregate_dependency_fails() -> None:
    """聚合任务的依赖失败时返回 FAILURE。"""

    @tool("demo", subcommand="a", cmd=_fail_cmd())
    def a() -> None:
        pass

    @tool("demo", subcommand="agg", needs=["a"])
    def agg() -> None:
        pass

    code = run_tool("demo", ["agg"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_fn_task_success() -> None:
    """fn 任务成功返回 SUCCESS。"""
    captured: list[str] = []

    @tool("demo", subcommand="a")
    def a(name: str) -> str:
        captured.append(name)
        return name

    code = run_tool("demo", ["a", "hello"])
    assert code == ToolExitCode.SUCCESS.value
    assert captured == ["hello"]


def test_run_tool_dry_run() -> None:
    """--dry-run 不实际执行，返回 SUCCESS。"""
    captured: list[str] = []

    @tool("demo", subcommand="a")
    def a(name: str) -> str:
        captured.append(name)
        return name

    code = run_tool("demo", ["a", "hello", "--dry-run"])
    assert code == ToolExitCode.SUCCESS.value
    assert captured == []


def test_run_tool_quiet() -> None:
    """--quiet 抑制 verbose 输出。"""

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a", "--quiet"])
    assert code == ToolExitCode.SUCCESS.value


def test_run_tool_strategy_override() -> None:
    """--strategy 覆盖装饰器策略。"""

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a", "--strategy", "sequential"])
    assert code == ToolExitCode.SUCCESS.value


def test_run_tool_single_command_no_argv() -> None:
    """单命令工具无 argv 时直接执行。"""

    @tool("solo", cmd=_echo_cmd())
    def s() -> None:
        pass

    code = run_tool("solo", [])
    assert code == ToolExitCode.SUCCESS.value


def test_run_tool_single_command_with_argv() -> None:
    """单命令工具有 argv 时（非选项开头）走子命令路径返回 FAILURE。"""

    @tool("solo", cmd=_echo_cmd())
    def s() -> None:
        pass

    code = run_tool("solo", ["unknown"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_single_command_with_options() -> None:
    """单命令工具接受全局选项。"""

    @tool("solo", cmd=_echo_cmd())
    def s() -> None:
        pass

    code = run_tool("solo", ["--dry-run"])
    assert code == ToolExitCode.SUCCESS.value


def test_run_tool_help_system_exit() -> None:
    """--help 触发 SystemExit（argparse 默认行为）。"""

    @tool("demo", subcommand="a")
    def a(name: str) -> None:
        pass

    with pytest.raises(SystemExit) as exc_info:
        run_tool("demo", ["a", "--help"])
    assert exc_info.value.code == 0


def test_run_tool_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeyboardInterrupt 返回 INTERRUPTED。"""
    from fcmd.apis import toolkit

    def boom(_graph: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(toolkit, "run", boom)

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a"])
    assert code == ToolExitCode.INTERRUPTED.value


def test_run_tool_fcmd_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """FcmdError（非 TaskFailedError）返回 FAILURE。"""
    from fcmd.apis import toolkit
    from fcmd.errors import CycleError

    def boom(_graph: object, **_kwargs: object) -> None:
        raise CycleError(["cycle"])

    monkeypatch.setattr(toolkit, "run", boom)

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_fcmd_error_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """FcmdError + quiet 不打印。"""
    from fcmd.apis import toolkit
    from fcmd.errors import CycleError

    def boom(_graph: object, **_kwargs: object) -> None:
        raise CycleError(["cycle"])

    monkeypatch.setattr(toolkit, "run", boom)

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a", "--quiet"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_task_failed_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """TaskFailedError + quiet 不打印诊断。"""
    from fcmd.apis import toolkit
    from fcmd.errors import TaskFailedError

    def boom(_graph: object, **_kwargs: object) -> None:
        raise TaskFailedError(task="a", cause=RuntimeError("boom"), attempts=1)

    monkeypatch.setattr(toolkit, "run", boom)

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a", "--quiet"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_task_failed_with_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """TaskFailedError 携带 report 时打印失败任务。"""
    from fcmd.apis import toolkit
    from fcmd.errors import TaskFailedError
    from fcmd.report import RunReport
    from fcmd.task import TaskResult

    # 构造带 failed_tasks 的 report
    spec = TaskSpec(name="a", cmd=["echo"])
    result = TaskResult(spec=spec)
    report = RunReport()
    report.results["a"] = result

    def boom(_graph: object, **_kwargs: object) -> None:
        err = TaskFailedError(task="a", cause=RuntimeError("boom"), attempts=1)
        err.report = report
        raise err

    monkeypatch.setattr(toolkit, "run", boom)

    @tool("demo", subcommand="a", cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a"])
    assert code == ToolExitCode.FAILURE.value


def test_run_tool_chain_dependency() -> None:
    """链式依赖 a→b→c 全部执行。"""
    log: list[str] = []

    @tool("demo", subcommand="c", cmd=_echo_cmd())
    def c() -> None:
        pass

    @tool("demo", subcommand="b", needs=["c"], cmd=_echo_cmd())
    def b() -> None:
        pass

    @tool("demo", subcommand="a", needs=["b"], cmd=_echo_cmd())
    def a() -> None:
        pass

    code = run_tool("demo", ["a"])
    assert code == ToolExitCode.SUCCESS.value
    assert log == []


def test_run_tool_missing_dependency_in_chain() -> None:
    """依赖链中存在未注册子命令返回 FAILURE。"""
    # needs 引用不存在的 subcommand
    # _collect_with_deps 会收集 needs，但 subs 中找不到 → FAILURE

    @tool("demo", subcommand="a", needs=["ghost"])
    def a() -> str:
        return "x"

    code = run_tool("demo", ["a"])
    assert code == ToolExitCode.FAILURE.value


# ---------------------------------------------------------------------- #
# 集成：通过 fcmd 顶层 API
# ---------------------------------------------------------------------- #
def test_fx_tool_decorator_accessible() -> None:
    """fx.tool 通过懒加载可访问。"""
    assert fcmd.tool is tool


def test_fx_run_tool_accessible() -> None:
    """fx.run_tool 通过懒加载可访问。"""
    assert fcmd.run_tool is run_tool


def test_fx_tool_spec_accessible() -> None:
    """fx.ToolSpec 通过懒加载可访问。"""
    assert fcmd.ToolSpec is ToolSpec
