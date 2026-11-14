"""MCP 适配器与白名单的单元测试。

覆盖范围：
- 白名单条目完整性：140 个全部存在且无重复 tool_name
- kwargs → argv / 内建 kwargs → argv 参数映射正确性
- ToolSpec → JSON Schema 字段完整性（required / choices / default / path format）
- ExecResult.parsed_envelope 从 stdout / stderr 两边解析
- exec_fcmd 进程内执行（成功 / 失败 / 无信封降级）
- MCP Server list_tools / call_tool 异步链路（成功 / 失败 / 白名单外拒绝）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.cli.agent_whitelist import (
    AGENT_WHITELIST,
    AgentWhitelistEntry,
    build_whitelist_index,
    total_whitelisted_subcommands,
)
from fcmd.cli.mcp_adapter import (
    _BUILTIN_SCHEMAS,
    ExecResult,
    _builtin_kwargs_to_argv,
    _kwargs_to_argv,
    _tool_spec_to_input_schema,
    exec_fcmd,
)
from fcmd.core.toolkit import get_tool

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, TextContent, Tool
except ImportError:  # pragma: no cover - 测试在缺 mcp 包时跳过
    Server = stdio_server = CallToolResult = TextContent = Tool = None  # type: ignore[assignment]

ensure_tools_discovered()


# ---------------------------------------------------------------------------
# 白名单元数据
# ---------------------------------------------------------------------------


def test_whitelist_count() -> None:
    assert total_whitelisted_subcommands() == len(AGENT_WHITELIST)
    assert len(AGENT_WHITELIST) >= 100  # 首批应 ≥100 个只读条目


def test_whitelist_no_duplicate_tool_names() -> None:
    names = [e.tool_name for e in AGENT_WHITELIST]
    assert len(names) == len(set(names)), "白名单条目 tool_name 重复"


def test_whitelist_no_gui_tools() -> None:
    """白名单不得暴露 GUI-only 入口（manifest GUI_TOOLS）。"""
    from fcmd.cli._schema import GUI_TOOLS

    for entry in AGENT_WHITELIST:
        assert entry.tool not in GUI_TOOLS, f"GUI 工具不能进白名单: {entry.tool_name}"


def test_whitelist_entries_exist_in_registry() -> None:
    """每个白名单条目在运行时注册表中存在（内建命令除外）。"""
    from fcmd.core.toolkit import _TOOL_REGISTRY

    BUILTINS = {"info", "env", "doctor", "graph"}
    for entry in AGENT_WHITELIST:
        if entry.tool in BUILTINS:
            continue
        assert entry.tool in _TOOL_REGISTRY, f"{entry.tool} 不在 _TOOL_REGISTRY"
        subs = _TOOL_REGISTRY[entry.tool]
        assert entry.subcommand in subs, f"{entry.tool} 的子命令 {entry.subcommand!r} 不存在，有: {list(subs.keys())}"


def test_whitelist_index() -> None:
    index = build_whitelist_index()
    assert len(index) == len(AGENT_WHITELIST)
    # 抽查
    assert index[("filesearch", "name")].tool_name == "fcmd__filesearch__name"
    assert index[("info", None)].builtin is True


# ---------------------------------------------------------------------------
# kwargs → argv
# ---------------------------------------------------------------------------


def test_kwargs_to_argv_mixed_positional_and_flags() -> None:
    spec = get_tool("filesearch", "name")
    argv = _kwargs_to_argv(spec, {"directory": ".", "pattern": "*.py", "limit": 10})
    # 子命令名 "name" 由调用方（_call_tool_handler）在 argv 头部注入
    assert "." in argv
    assert "*.py" in argv
    assert "--limit" in argv
    assert "10" in argv
    assert argv[-1] == "--json"  # force_json 自动注入


def test_kwargs_to_argv_boolean_flag() -> None:
    spec = get_tool("filesearch", "name")
    argv = _kwargs_to_argv(spec, {"directory": ".", "pattern": "*.py", "include_dirs": True})
    assert "--include-dirs" in argv


def test_kwargs_to_argv_force_json_false() -> None:
    spec = get_tool("filesearch", "name")
    argv = _kwargs_to_argv(spec, {"directory": ".", "pattern": "*.py"}, force_json=False)
    assert argv[-1] != "--json"


def test_builtin_info_kwargs() -> None:
    assert _builtin_kwargs_to_argv("info", {}) == ["--json"]
    assert _builtin_kwargs_to_argv("info", {"tool": "filesearch"}) == ["filesearch", "--json"]
    assert _builtin_kwargs_to_argv("info", {"tool": "filesearch", "subcommand": "name"}) == [
        "filesearch",
        "name",
        "--json",
    ]


def test_builtin_env_doctor() -> None:
    assert _builtin_kwargs_to_argv("env", {}) == ["--json"]
    assert _builtin_kwargs_to_argv("doctor", {}) == ["--json"]


def test_builtin_graph() -> None:
    argv = _builtin_kwargs_to_argv("graph", {"tool": "pymake", "format": "mermaid"})
    assert argv[0] == "pymake"
    assert "--format" in argv
    assert "mermaid" in argv
    assert argv[-1] == "--json"


# ---------------------------------------------------------------------------
# ToolSpec → JSON Schema
# ---------------------------------------------------------------------------


def test_tool_spec_schema_basic() -> None:
    spec = get_tool("filesearch", "name")
    schema = _tool_spec_to_input_schema(spec)
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "directory" in props
    assert "pattern" in props
    assert "json_output" not in props  # 被跳过
    # required
    assert "directory" in schema["required"]
    assert "pattern" in schema["required"]


def test_tool_spec_schema_path_format() -> None:
    spec = get_tool("filesearch", "name")
    schema = _tool_spec_to_input_schema(spec)
    assert schema["properties"]["directory"]["format"] == "path"


def test_builtin_schemas_registered() -> None:
    assert set(_BUILTIN_SCHEMAS.keys()) == {"info", "env", "doctor", "graph"}
    assert _BUILTIN_SCHEMAS["info"]["properties"].keys() >= {"tool", "subcommand"}
    assert _BUILTIN_SCHEMAS["graph"]["required"] == ["tool"]


# ---------------------------------------------------------------------------
# ExecResult.envelope 解析
# ---------------------------------------------------------------------------


def test_exec_result_parses_success_envelope_from_stdout() -> None:
    stdout = json.dumps(
        {
            "schema": "fcmd-result/1",
            "ok": True,
            "tool": "filesearch",
            "data": {"matches": ["a.py"]},
        }
    )
    r = ExecResult(0, stdout, "")
    env = r.parsed_envelope()
    assert env is not None
    assert env["schema"] == "fcmd-result/1"
    assert env["ok"] is True


def test_exec_result_parses_error_envelope_from_stderr() -> None:
    stderr = json.dumps(
        {
            "schema": "fcmd-error/1",
            "ok": False,
            "tool": "filesearch",
            "kind": "not_found",
        }
    )
    r = ExecResult(4, "", stderr)
    env = r.parsed_envelope()
    assert env is not None
    assert env["schema"] == "fcmd-error/1"
    assert env["ok"] is False


def test_exec_result_stdout_priority_over_stderr() -> None:
    stdout = json.dumps({"schema": "fcmd-result/1", "ok": True})
    stderr = json.dumps({"schema": "fcmd-error/1", "ok": False})
    r = ExecResult(0, stdout, stderr)
    env = r.parsed_envelope()
    assert env["schema"] == "fcmd-result/1"  # stdout 优先


def test_exec_result_no_envelope() -> None:
    r = ExecResult(0, "plain text output\n", "")
    assert r.parsed_envelope() is None


def test_exec_result_empty() -> None:
    r = ExecResult(0, "", "")
    assert r.parsed_envelope() is None


# ---------------------------------------------------------------------------
# exec_fcmd 进程内执行
# ---------------------------------------------------------------------------


def test_exec_fcmd_reads_env() -> None:
    result = exec_fcmd("env", [], builtin=True)
    assert result.exit_code == 0
    env = result.parsed_envelope()
    assert env is None or env.get("ok") is True  # env 不走 fcmd-result/1 信封


def test_exec_fcmd_filesearch_ok() -> None:
    result = exec_fcmd("filesearch", ["name", ".", "*.py", "--limit", "2", "--json"])
    assert result.exit_code == 0
    env = result.parsed_envelope()
    assert env is not None
    assert env["schema"] == "fcmd-result/1"
    assert env["tool"] == "filesearch"
    assert "matches" in env["data"]


def test_exec_fcmd_filesearch_error() -> None:
    result = exec_fcmd("filesearch", ["content", "C:\\NOPE", "x", "--json"])
    assert result.exit_code != 0
    env = result.parsed_envelope()
    assert env is not None
    assert env["schema"] == "fcmd-error/1"


def test_exec_fcmd_exception_isolated() -> None:
    """一次调用的异常不会污染后续调用。"""
    result1 = exec_fcmd("filesearch", ["name", ".", "*.py", "--json"])
    assert result1.exit_code == 0
    result2 = exec_fcmd("filesearch", ["content", "C:\\NOPE", "x", "--json"])
    assert result2.exit_code != 0  # 正常返回错误，不是崩栈


# ---------------------------------------------------------------------------
# MCP Server list_tools / call_tool（需要 mcp 包）
# ---------------------------------------------------------------------------

requires_mcp = pytest.mark.skipif(Server is None, reason="mcp 包未安装")


def _run_async(coro):
    """在独立事件循环里跑 async handler（避免 pytest-asyncio 依赖）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@requires_mcp
def test_mcp_list_tools_returns_whitelist_count() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    tools = _run_async(server.list_tools())
    assert len(tools) == len(AGENT_WHITELIST)


@requires_mcp
def test_mcp_list_tools_schema_fields() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    tools = _run_async(server.list_tools())
    tool_map = {t.name: t for t in tools}
    fs_name = tool_map["fcmd__filesearch__name"]
    assert fs_name.inputSchema["type"] == "object"
    assert "directory" in fs_name.inputSchema["properties"]
    assert "pattern" in fs_name.inputSchema["required"]


@requires_mcp
def test_mcp_call_tool_success() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.call_tool("fcmd__filesearch__name", {"directory": ".", "pattern": "*.py", "limit": 1}))
    assert result.isError is False
    parsed = json.loads(result.content[0].text)
    assert parsed["schema"] == "fcmd-result/1"


@requires_mcp
def test_mcp_call_tool_error_no_such_dir() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.call_tool("fcmd__filesearch__content", {"directory": "C:\\NOPE", "pattern": "x"}))
    assert result.isError is True
    parsed = json.loads(result.content[0].text)
    assert parsed["schema"] == "fcmd-error/1"
    assert parsed["code"] == "DIR_NOT_FOUND"


@requires_mcp
def test_mcp_call_tool_builtin_env() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.call_tool("fcmd__env", {}))
    assert result.isError is False


@requires_mcp
def test_mcp_call_tool_builtin_info() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.call_tool("fcmd__info", {"tool": "filesearch"}))
    assert result.isError is False
    parsed = json.loads(result.content[0].text)
    assert parsed["schema"] == "fcmd-schema/1"


@requires_mcp
def test_mcp_call_tool_whitelist_rejects() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.call_tool("fcmd__writefile", {"path": "x", "content": "y"}))
    assert result.isError is True
    assert "不在白名单内" in result.content[0].text


# ---------------------------------------------------------------------------
# AgentSecurityPolicy + 两段式写协议（不需要 mcp 包即可测安全闸门）
# ---------------------------------------------------------------------------


def test_security_policy_defaults() -> None:
    from fcmd.cli.agent_security import AgentSecurityPolicy

    p = AgentSecurityPolicy()
    assert p.workspace_root is None
    assert p.allow_absolute_paths is False
    assert p.allow_network is True
    assert p.require_write_confirmation is True
    assert p.deny_patterns == []


def test_security_policy_to_from_dict() -> None:
    from pathlib import Path

    from fcmd.cli.agent_security import AgentSecurityPolicy

    p = AgentSecurityPolicy(
        workspace_root=Path("F:/work"),
        allow_absolute_paths=True,
        allow_network=False,
        require_write_confirmation=False,
        deny_patterns=[".git", "__pycache__"],
    )
    d = p.to_dict()
    assert d["workspace_root"] == str(Path("F:/work"))
    assert d["allow_absolute_paths"] is True
    assert d["allow_network"] is False
    assert d["require_write_confirmation"] is False
    restored = AgentSecurityPolicy.from_dict(d)
    assert restored.workspace_root == Path("F:/work")
    assert restored.deny_patterns == [".git", "__pycache__"]


def test_validate_path_within_root_ok() -> None:
    from pathlib import Path

    from fcmd.cli.agent_security import validate_path_within_root

    root = Path("F:/work").resolve()
    p = validate_path_within_root("F:/work/sub/file.txt", root)
    assert root in p.parents or p == root


def test_validate_path_within_root_escape() -> None:
    from pathlib import Path

    from fcmd.cli.agent_security import SecurityError, validate_path_within_root

    root = Path("F:/work").resolve()
    with pytest.raises(SecurityError) as exc:
        validate_path_within_root("C:/Windows/System32", root)
    assert exc.value.code == "PATH_ESCAPE"


def test_validate_no_denied_fragments_ok() -> None:
    from fcmd.cli.agent_security import validate_no_denied_fragments

    validate_no_denied_fragments("safe/path/file.txt", [".git", "__pycache__"])  # 不抛


def test_validate_no_denied_fragments_hit() -> None:
    from fcmd.cli.agent_security import SecurityError, validate_no_denied_fragments

    with pytest.raises(SecurityError) as exc:
        validate_no_denied_fragments("project/.git/config", [".git"])
    assert exc.value.code == "PATH_DENIED"


def test_sha256_plan_payload_stable() -> None:
    from fcmd.cli.agent_security import sha256_plan_payload

    a = sha256_plan_payload({"tool": "x", "args": {"b": 2, "a": 1}})
    b = sha256_plan_payload({"args": {"a": 1, "b": 2}, "tool": "x"})
    assert a == b  # sort_keys=True 规范化


def test_plan_hash_for_bind() -> None:
    from fcmd.cli.agent_security import plan_hash_for

    h1 = plan_hash_for({"x": 1, "y": 2}, "fcmd__peview__edit")
    h2 = plan_hash_for({"x": 1, "y": 2}, "fcmd__peview__edit")
    h3 = plan_hash_for({"x": 99, "y": 2}, "fcmd__peview__edit")
    assert h1 == h2
    assert h1 != h3


# ---------------------------------------------------------------------------
# 两段式协议：安全闸门拦截（不依赖 wsl.exe / PE 文件）
# ---------------------------------------------------------------------------


def _fresh_policy_and_cache() -> None:
    """重置全局策略和 plan_cache（每个测试独立）。"""
    from fcmd.cli.mcp_adapter import _plan_cache, _policy_singleton

    _policy_singleton[0] = None
    _plan_cache.clear()


def _entry(tool: str, subcommand: str | None) -> AgentWhitelistEntry:
    return build_whitelist_index()[(tool, subcommand)]


def test_write_tool_without_plan_hash_is_blocked() -> None:
    """write 工具第一次调用不带 plan_hash：安全闸门拦截。"""
    from fcmd.cli.agent_security import AgentSecurityPolicy, SecurityError
    from fcmd.cli.mcp_adapter import _check_security, _plan_cache, _set_policy

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(require_write_confirmation=True))

    # peview edit 是 write 工具；闸门在构造 argv / 执行之前
    with pytest.raises(SecurityError) as info:
        _check_security(_entry("peview", "edit"), {"file": "C:/nope.dll", "assign": ["entry_point=0x1000"]})
    assert info.value.code == "WRITE_CONFIRM_REQUIRED"
    # cache 应该还是空的（plan 阶段被闸门拦了，没走到执行与缓存）
    assert len(_plan_cache) == 0


def test_network_denied_by_policy() -> None:
    """allow_network=False 时 meta='network' 工具被拦截。"""
    from fcmd.cli.agent_security import AgentSecurityPolicy, SecurityError
    from fcmd.cli.mcp_adapter import _check_security, _set_policy

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(allow_network=False))

    with pytest.raises(SecurityError) as info:
        _check_security(_entry("nettool", "get"), {"url": "https://example.com"})
    assert info.value.code == "NETWORK_DENIED"


def test_path_escapes_workspace_root() -> None:
    from pathlib import Path

    from fcmd.cli.agent_security import AgentSecurityPolicy, SecurityError
    from fcmd.cli.mcp_adapter import _check_security, _set_policy

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(workspace_root=Path("F:/Dev/fcmd").resolve()))

    with pytest.raises(SecurityError) as info:
        _check_security(_entry("filesearch", "content"), {"directory": "C:/Windows", "pattern": "x"})
    assert info.value.code == "PATH_ESCAPE"


def test_deny_patterns_block() -> None:
    from pathlib import Path

    from fcmd.cli.agent_security import AgentSecurityPolicy, SecurityError
    from fcmd.cli.mcp_adapter import _check_security, _set_policy

    _fresh_policy_and_cache()
    _set_policy(
        AgentSecurityPolicy(workspace_root=Path("F:/Dev/fcmd").resolve(), deny_patterns=[".git", "node_modules"])
    )

    with pytest.raises(SecurityError) as info:
        _check_security(_entry("filesearch", "name"), {"directory": "F:/Dev/fcmd/.git", "pattern": "config"})
    assert info.value.code == "PATH_DENIED"


def test_plan_cache_miss_on_apply() -> None:
    from fcmd.cli.agent_security import AgentSecurityPolicy, SecurityError
    from fcmd.cli.mcp_adapter import _check_security, _set_policy

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(require_write_confirmation=True))

    # 不存在的 plan_hash
    with pytest.raises(SecurityError) as info:
        _check_security(_entry("peview", "edit"), {"file": "C:/nope.dll", "plan_hash": "deadbeef12345678"})
    assert info.value.code == "PLAN_CACHE_MISS"


def test_plan_entry_mismatch() -> None:
    from fcmd.cli.agent_security import AgentSecurityPolicy, SecurityError
    from fcmd.cli.mcp_adapter import _check_security, _plan_cache, _set_policy

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(require_write_confirmation=True))
    # 伪造一条属于 A 工具的 cache，用它去调 B 工具
    _plan_cache["abcdef1234567890"] = {
        "entry_name": "fcmd__wslman__export",
        "arguments": {"distro": "Ubuntu", "file": "x.tar"},
        "plan_text": "",
    }
    with pytest.raises(SecurityError) as info:
        _check_security(_entry("peview", "edit"), {"file": "C:/nope.dll", "plan_hash": "abcdef1234567890"})
    assert info.value.code == "PLAN_ENTRY_MISMATCH"


def test_plan_arguments_changed_rejected() -> None:
    from fcmd.cli.agent_security import AgentSecurityPolicy, SecurityError, plan_hash_for
    from fcmd.cli.mcp_adapter import _check_security, _plan_cache, _set_policy

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(require_write_confirmation=True))
    args = {"file": "C:/a.dll", "assign": ["entry_point=0x1000"]}
    ph = plan_hash_for(args, "fcmd__peview__edit")
    _plan_cache[ph] = {"entry_name": "fcmd__peview__edit", "arguments": args, "plan_text": ""}
    # apply 阶段改了参数
    changed = dict(args)
    changed["assign"] = ["entry_point=0x2000"]
    changed["plan_hash"] = ph
    with pytest.raises(SecurityError) as info:
        _check_security(_entry("peview", "edit"), changed)
    assert info.value.code == "PLAN_ARGUMENTS_CHANGED"


def test_plan_arguments_matching_passes_security_gate() -> None:
    """参数完全一致 → 通过安全闸门（执行期失败不属于闸门拦截）。"""
    from fcmd.cli.agent_security import AgentSecurityPolicy, plan_hash_for
    from fcmd.cli.mcp_adapter import _check_security, _plan_cache, _set_policy

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(require_write_confirmation=True))
    args = {"file": "C:/a.dll", "assign": ["entry_point=0x1000"]}
    ph = plan_hash_for(args, "fcmd__peview__edit")
    _plan_cache[ph] = {"entry_name": "fcmd__peview__edit", "arguments": args, "plan_text": ""}

    assert _check_security(_entry("peview", "edit"), {**args, "plan_hash": ph}) is None


# ---------------------------------------------------------------------------
# Resource / Prompt handler（需要 mcp 包）
# ---------------------------------------------------------------------------


@requires_mcp
def test_mcp_list_resources_count() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    resources = _run_async(server.list_resources())
    assert len(resources) == 4
    uris = {str(r.uri) for r in resources}  # AnyUrl → str
    assert {"fcmd://manifest", "fcmd://info", "fcmd://doctor", "fcmd://policy"} <= uris


@requires_mcp
def test_mcp_read_resource_manifest() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.read_resource("fcmd://manifest"))
    assert len(result.contents) == 1
    text = result.contents[0].text
    data = json.loads(text)
    assert data["count"] == len(AGENT_WHITELIST)
    tools = data["tools"]
    # 抽查 write 条目
    write_tools = [t for t in tools if t["meta"] == "write"]
    assert len(write_tools) >= 6  # wslman 5 个 + peview edit
    assert any(t["tool"] == "peview" and t["subcommand"] == "edit" for t in write_tools)
    assert any(t["tool"] == "wslman" and t["subcommand"] == "export" for t in write_tools)


@requires_mcp
def test_mcp_read_resource_info() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.read_resource("fcmd://info"))
    text = result.contents[0].text
    data = json.loads(text)
    assert data.get("schema") in ("fcmd-schema/1", "fcmd-result/1")


@requires_mcp
def test_mcp_read_resource_doctor() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.read_resource("fcmd://doctor"))
    text = result.contents[0].text
    data = json.loads(text)
    # doctor 可能返回 fcmd-result/1 或文本降级，只要是有效 JSON 即可
    assert isinstance(data, dict)


@requires_mcp
def test_mcp_read_resource_policy() -> None:
    from fcmd.cli.agent_security import AgentSecurityPolicy
    from fcmd.cli.mcp_adapter import _set_policy, build_mcp_server

    _fresh_policy_and_cache()
    _set_policy(AgentSecurityPolicy(require_write_confirmation=False, allow_network=False))

    server = build_mcp_server()
    result = _run_async(server.read_resource("fcmd://policy"))
    data = json.loads(result.contents[0].text)
    assert data["require_write_confirmation"] is False
    assert data["allow_network"] is False


@requires_mcp
def test_mcp_read_resource_unknown_uri() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.read_resource("fcmd://nope"))
    data = json.loads(result.contents[0].text)
    assert "error" in data


@requires_mcp
def test_mcp_list_prompts() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    prompts = _run_async(server.list_prompts())
    names = {p.name for p in prompts}
    assert names == {"fcmd-quickstart", "fcmd-filesystem-scan", "fcmd-diff-confirm"}


@requires_mcp
def test_mcp_get_prompt_quickstart() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.get_prompt("fcmd-quickstart"))
    assert len(result.messages) == 1
    msg = result.messages[0]
    assert msg.role == "user"
    assert "两段式确认协议" in msg.content.text
    assert "plan 阶段" in msg.content.text


@requires_mcp
def test_mcp_get_prompt_unknown() -> None:
    from fcmd.cli.mcp_adapter import build_mcp_server

    server = build_mcp_server()
    result = _run_async(server.get_prompt("fcmd-nonexistent"))
    assert "(unknown prompt: fcmd-nonexistent)" in result.messages[0].content.text


# ---------------------------------------------------------------------------
# 纯逻辑补充分支：不依赖 mcp 包
# ---------------------------------------------------------------------------


def test_kwargs_to_argv_boolean_false_with_default_true() -> None:
    """否定式布尔选项：False 传 ``--no-*``，True 保持默认不传。"""
    spec = get_tool("randtool", "password")
    assert _kwargs_to_argv(spec, {"symbols": False}) == ["--no-symbols", "--json"]
    assert _kwargs_to_argv(spec, {"symbols": True}) == ["--json"]


def test_kwargs_to_argv_false_bool_default_false_is_omitted() -> None:
    """默认 False 的布尔参数显式传 False 不产生参数。"""
    argv = _kwargs_to_argv(get_tool("filesearch", "name"), {"directory": ".", "include_dirs": False})
    assert "--include-dirs" not in argv and "--no-include-dirs" not in argv


def test_kwargs_to_argv_skips_none() -> None:
    """值为 None 的可选参数不生成命令行片段。"""
    argv = _kwargs_to_argv(get_tool("filesearch", "name"), {"directory": ".", "pattern": None})
    assert "None" not in argv


def test_collect_positional_expands_sequence() -> None:
    """位置参数为列表时展开为多个 token。"""
    from fcmd.cli.mcp_adapter import _collect_positional

    assert _collect_positional(["a", "b"], "item") == [("item", "a"), ("item", "b")]
    assert _collect_positional("a", "item") == [("item", "a")]


def test_builtin_kwargs_info_without_tool_and_graph() -> None:
    """内建 info 无 tool；graph 拼接 tool/subcommand/format 与 --json。"""
    assert _builtin_kwargs_to_argv("info", {}, force_json=False) == []
    assert _builtin_kwargs_to_argv("graph", {"subcommand": "name"}, force_json=False) == ["name"]
    assert _builtin_kwargs_to_argv("graph", {"tool": "filesearch", "subcommand": "name"}, force_json=False) == [
        "filesearch",
        "name",
    ]
    argv = _builtin_kwargs_to_argv(
        "graph", {"tool": "filesearch", "subcommand": "name", "format": "json"}, force_json=True
    )
    assert argv == ["filesearch", "name", "--format", "json", "--json"]


def test_builtin_kwargs_env_without_force_json() -> None:
    """force_json=False 时不追加 --json。"""
    assert _builtin_kwargs_to_argv("env", {}, force_json=False) == []


@requires_mcp
def test_security_error_includes_hint_and_expected() -> None:
    """安全错误信封透传 hint / expected；未提供时不出现空字段。"""
    from fcmd.cli.agent_security import SecurityError
    from fcmd.cli.mcp_adapter import _security_error_to_result

    error = SecurityError("TEST_CODE", "被拦截", hint="改参数", expected="合法值")
    data = json.loads(_security_error_to_result(error).content[0].text)
    assert data["code"] == "TEST_CODE"
    assert data["hint"] == "改参数"
    assert data["expected"] == "合法值"

    bare = json.loads(_security_error_to_result(SecurityError("BARE", "无提示")).content[0].text)
    assert "hint" not in bare and "expected" not in bare


@requires_mcp
def test_workspace_scan_skips_non_path_and_missing_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """工作区模式下跳过非 path 参数与未提供参数，合法路径放行。"""
    import fcmd.cli.mcp_adapter as adapter
    from fcmd.cli.agent_security import AgentSecurityPolicy

    _fresh_policy_and_cache()
    adapter._set_policy(AgentSecurityPolicy(workspace_root=Path.cwd(), require_write_confirmation=False))
    monkeypatch.setattr(adapter, "exec_fcmd", lambda *a, **k: ExecResult(0, _ok_envelope(), ""))
    try:
        result = _run_async(adapter._call_tool_handler("fcmd__filesearch__name", {"directory": ".", "pattern": "*.py"}))
        assert result.isError is False
        # 只给 directory、不给 pattern → 未提供的 path/非 path 参数都被跳过
        result = _run_async(adapter._call_tool_handler("fcmd__filesearch__name", {"directory": "."}))
        assert result.isError is False
        # 路径参数完全未提供 → 直接跳过校验
        result = _run_async(adapter._call_tool_handler("fcmd__wslman__export", {"distro": "Ubuntu"}))
        assert result.isError is False
    finally:
        _fresh_policy_and_cache()


@requires_mcp
def test_workspace_policy_skips_builtin_without_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """工作区模式对无 ToolSpec 的内建条目不做路径校验。"""
    import fcmd.cli.mcp_adapter as adapter
    from fcmd.cli.agent_security import AgentSecurityPolicy

    _fresh_policy_and_cache()
    adapter._set_policy(AgentSecurityPolicy(workspace_root=Path.cwd()))
    monkeypatch.setattr(adapter, "exec_fcmd", lambda *a, **k: ExecResult(0, _ok_envelope(), ""))
    try:
        result = _run_async(adapter._call_tool_handler("fcmd__info", {"tool": "filesearch"}))
        assert result.isError is False
    finally:
        _fresh_policy_and_cache()


def test_exec_fcmd_unexpected_exception_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """执行器抛异常时降级为 exit_code=1，并保留 stderr。"""
    import fcmd.cli.mcp_adapter as adapter

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(adapter, "run_tool", _boom)
    result = exec_fcmd("filesearch", ["name", ".", "*.py", "--json"])
    assert result.exit_code == 1
    assert "RuntimeError" in result.stderr and "boom" in result.stderr


@requires_mcp
def test_two_phase_plan_and_apply_caches_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """写操作 plan 阶段注入并缓存 plan_hash，apply 阶段命中后消费缓存。"""
    import fcmd.cli.mcp_adapter as adapter
    from fcmd.cli.agent_security import AgentSecurityPolicy

    _fresh_policy_and_cache()
    adapter._set_policy(AgentSecurityPolicy(require_write_confirmation=False))
    monkeypatch.setattr(adapter, "exec_fcmd", lambda *a, **k: ExecResult(0, _ok_envelope(), ""))
    try:
        args = {"distro": "Ubuntu", "file": "x.tar"}
        plan = _run_async(adapter._call_tool_handler("fcmd__wslman__export", args))
        plan_data = json.loads(plan.content[0].text)
        plan_hash = plan_data["plan_hash"]
        assert plan_hash in adapter._plan_cache

        applied = _run_async(adapter._call_tool_handler("fcmd__wslman__export", {**args, "plan_hash": plan_hash}))
        assert json.loads(applied.content[0].text)["ok"] is True
        assert plan_hash not in adapter._plan_cache
    finally:
        _fresh_policy_and_cache()


@requires_mcp
def test_format_result_without_envelope_keeps_stderr_and_plan_hash() -> None:
    """无 JSON 信封时保留 stderr、退出码与 plan_hash 提示。"""
    from fcmd.cli.mcp_adapter import _format_mcp_result

    result = _format_mcp_result(ExecResult(2, "", "boom"), plan_hash="abcdef")
    text = result.content[0].text
    assert "boom" in text
    assert "[exit_code=2]" in text
    assert "[plan_hash=abcdef]" in text

    no_stderr = _format_mcp_result(ExecResult(3, "", "")).content[0].text
    assert "stderr" not in no_stderr and "[exit_code=3]" in no_stderr


def test_build_mcp_server_requires_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 mcp 包时构建服务器给出可操作报错。"""
    import fcmd.cli.mcp_adapter as adapter

    monkeypatch.setattr(adapter, "_MCP_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="mcp 包未安装"):
        adapter.build_mcp_server()


def test_run_mcp_stdio_requires_mcp(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """缺 mcp 包时 stdio 入口打印安装指引并以 1 退出。"""
    import fcmd.cli.mcp_adapter as adapter

    monkeypatch.setattr(adapter, "_MCP_AVAILABLE", False)
    with pytest.raises(SystemExit) as exc:
        adapter.run_mcp_stdio()
    assert exc.value.code == 1
    assert "uv pip install mcp" in capsys.readouterr().err


def test_run_mcp_stdio_starts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """stdio 入口接线：Server.run 收到 stdio 读写流并启动。"""
    import fcmd.cli.mcp_adapter as adapter

    class _FakeServer:
        def __init__(self, name: str) -> None:
            self.name = name

        def create_initialization_options(self) -> dict[str, object]:
            return {}

        async def run(self, read: object, write: object, options: object) -> None:
            self.ran = (read, write, options)

    class _FakeStdio:
        async def __aenter__(self) -> tuple[str, str]:
            return ("read", "write")

        async def __aexit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr(adapter, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(adapter, "Server", _FakeServer)
    monkeypatch.setattr(adapter, "stdio_server", _FakeStdio)
    adapter.run_mcp_stdio()


def _ok_envelope() -> str:
    """成功结果信封（供 exec_fcmd 替身使用）。"""
    return json.dumps({"schema": "fcmd-result/1", "ok": True, "data": {}}, ensure_ascii=False)
