"""fcmd → MCP 适配层。

把 :mod:`fcmd.core.toolkit` 的 ``@fcmd.tool`` 注册与
:mod:`fcmd.cli._builtins` 的内建命令暴露为 MCP stdio server 上的 tools，
严格遵守 :mod:`fcmd.cli.agent_whitelist` 的准入清单。

设计目标
--------
* **薄封装**：适配器只做协议转换（schema → JSON Schema、kwargs → argv、
  文本/JSON 信封 → MCP Content），不重新实现任何业务逻辑；所有执行仍走
  ``run_tool`` / ``run_builtin``。
* **安全闸门**：白名单外的子命令完全不注册；工具在本 server 下不可达。
* **机读优先**：适配器强制注入 ``--json``（或工具实际使用的机读 flag），
  保证输出可被模型直接消费。
* **不影响 CLI**：CLI 主入口不走本模块，缺 ``mcp`` 包时仍可正常执行
  ``fcmd ...``；MCP 相关测试用跳过标记在缺依赖时放行。
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Mapping, Sequence
from typing import Any

from fcmd.cli._builtins import run_builtin
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.cli.agent_security import (
    AgentSecurityPolicy,
    SecurityError,
    plan_hash_for,
    validate_no_denied_fragments,
    validate_path_within_root,
)
from fcmd.cli.agent_whitelist import AGENT_WHITELIST, AgentWhitelistEntry
from fcmd.core.exit_codes import ExitCode
from fcmd.core.result import reset_error_state
from fcmd.core.toolkit import ToolSpec, get_tool, param_schema, run_tool

# MCP SDK 是可选依赖：缺依赖时适配器仍可正常 import（CLI 不受影响），
# 仅在调用 build_mcp_server / run_mcp_stdio 时报明确错误。
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        CallToolResult,
        GetPromptResult,
        Prompt,
        ReadResourceResult,
        Resource,
        TextContent,
        TextResourceContents,
        Tool,
    )

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MCP_AVAILABLE = False
    Server = stdio_server = None  # type: ignore[assignment]
    CallToolResult = TextContent = Tool = None  # type: ignore[assignment]
    Resource = ReadResourceResult = TextResourceContents = None  # type: ignore[assignment]
    Prompt = GetPromptResult = None  # type: ignore[assignment]


__all__ = [
    "MCP_SERVER_NAME",
    "TOOL_NAME_SEP",
    "_MCP_AVAILABLE",
    "AgentSecurityPolicy",
    "build_mcp_server",
    "exec_fcmd",
    "run_mcp_stdio",
]

MCP_SERVER_NAME = "fcmd-agent-tools"
TOOL_NAME_SEP = "__"  # fcmd__filesearch__name

# 全局策略（进程级单例，_set_policy 可在启动前覆盖）
_policy_singleton: list[AgentSecurityPolicy | None] = [None]

# 跨调用 plan 缓存：plan_hash → {"entry_name": ..., "arguments": ..., "plan_text": ...}
# MCP stdio 是长驻进程，进程退出自动清除；内存态，不持久化。
_plan_cache: dict[str, dict[str, Any]] = {}


def _get_policy() -> AgentSecurityPolicy:
    """返回当前策略（默认策略在首次调用时生成）。"""
    if _policy_singleton[0] is None:
        _policy_singleton[0] = AgentSecurityPolicy()
    return _policy_singleton[0]


def _set_policy(policy: AgentSecurityPolicy) -> None:
    """替换进程级策略（启动前调用）。"""
    _policy_singleton[0] = policy


def _security_error_to_result(err: SecurityError) -> CallToolResult:
    """把 SecurityError 转成带 fcmd-error/1 envelope 的 CallToolResult。"""
    envelope = {
        "schema": "fcmd-error/1",
        "ok": False,
        "kind": err.kind,
        "code": err.code,
        "message": err.message,
        "exit_code": int(ExitCode.FAILURE),
    }
    if err.hint:
        envelope["hint"] = err.hint
    if err.expected:
        envelope["expected"] = err.expected
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))], isError=True
    )


def _check_security(entry: AgentWhitelistEntry, arguments: dict[str, Any]) -> None:
    """在执行前跑完整安全闸门。抛 SecurityError；通过则返回 None。"""
    policy = _get_policy()
    # 1) 网络门禁
    if entry.meta == "network" and not policy.allow_network:
        raise SecurityError(
            code="NETWORK_DENIED",
            message=f"策略禁止网络工具 {entry.tool_name}（allow_network=False）",
            hint="启动时设置 allow_network=True 或在策略里放开本工具",
        )
    # 2) write 工具的两段式确认（policy 要求时）
    if entry.meta == "write" and policy.require_write_confirmation:
        plan_hash = arguments.get("plan_hash")
        if not plan_hash:
            raise SecurityError(
                code="WRITE_CONFIRM_REQUIRED",
                message=f"写工具 {entry.tool_name} 必须先跑 plan（不传 plan_hash 只返回 dry-run 结果）"
                if entry.plan_flag is None
                else f"写工具 {entry.tool_name} 必须先跑 plan（adapter 会自动注入 {entry.plan_flag}）",
                hint="第一次调用不带 plan_hash 触发 plan 阶段，拿到 plan_hash 后第二次调用带上它触发 apply",
            )
        cached = _plan_cache.get(plan_hash)
        if cached is None:
            raise SecurityError(
                code="PLAN_CACHE_MISS",
                message=f"plan_hash {plan_hash!r} 不存在或已过期",
                hint="重新跑 plan 阶段获取新的 plan_hash",
            )
        if cached["entry_name"] != entry.tool_name:
            raise SecurityError(
                code="PLAN_ENTRY_MISMATCH",
                message=f"plan_hash 属于 {cached['entry_name']!r}，但当前调用的是 {entry.tool_name!r}",
                hint="同一个 plan_hash 只能用于它所属的工具",
            )
        # 比对 arguments（plan_hash 生成时排除了 plan_hash 字段本身）
        expected_args = {k: v for k, v in arguments.items() if k != "plan_hash"}
        expected_hash = plan_hash_for(expected_args, entry.tool_name)
        if expected_hash != plan_hash:
            raise SecurityError(
                code="PLAN_ARGUMENTS_CHANGED",
                message="apply 阶段的 arguments 与 plan 阶段不一致",
                hint="plan_hash 绑定了 arguments，篡改参数会被拒绝；请重新跑 plan",
            )
    # 3) 路径检查（针对 ToolSpec 里 type='path' 的参数）
    if policy.workspace_root is not None:
        registry = _get_registry()
        spec = registry.get_spec(entry.tool_name)
        if spec is not None:
            for p in param_schema(spec):
                if p["type"] != "path":
                    continue
                pname = p["name"]
                if pname not in arguments or arguments[pname] is None:
                    continue
                validate_path_within_root(str(arguments[pname]), policy.workspace_root)
                if policy.deny_patterns:
                    validate_no_denied_fragments(str(arguments[pname]), policy.deny_patterns)


# ---------------------------------------------------------------------------
# ToolSpec → JSON Schema
# ---------------------------------------------------------------------------


def _type_to_json_schema_type(tp: str) -> str:
    mapping = {
        "str": "string",
        "path": "string",
        "int": "integer",
        "float": "number",
        "number": "number",
        "bool": "boolean",
        "list": "array",
    }
    return mapping.get(tp, "string")


def _tool_spec_to_input_schema(spec: ToolSpec, *, skip_json_flag: bool = True) -> dict[str, Any]:
    """从 ToolSpec.params 构造 MCP tool 的 inputSchema（JSON Schema）。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in param_schema(spec):
        pname = p["name"]
        if skip_json_flag and pname == "json_output":
            continue
        js_type = _type_to_json_schema_type(p["type"])
        prop: dict[str, Any] = {"type": js_type}
        if p["help"]:
            prop["description"] = p["help"]
        if p["choices"]:
            prop["enum"] = list(p["choices"])
        default = p["default"]
        if default is not None and default is not False:
            prop["default"] = default
        if p["type"] == "path":
            prop.setdefault("format", "path")
        properties[pname] = prop
        if p["required"]:
            required.append(pname)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _builtin_info_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "description": "工具名（可选，缺省返回全部工具总览）"},
            "subcommand": {"type": "string", "description": "子命令名（可选）"},
        },
    }


def _builtin_env_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


def _builtin_doctor_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


def _builtin_graph_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "description": "目标工具名"},
            "subcommand": {"type": "string", "description": "目标子命令（可选）"},
            "format": {
                "type": "string",
                "description": "输出格式（默认 tree）",
                "enum": ["tree", "mermaid", "layers", "describe"],
            },
        },
        "required": ["tool"],
    }


_BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "info": _builtin_info_schema(),
    "env": _builtin_env_schema(),
    "doctor": _builtin_doctor_schema(),
    "graph": _builtin_graph_schema(),
}

_BUILTIN_DESCRIPTIONS: dict[str, str] = {
    "info": "fcmd 工具注册表总览与参数 schema（机读入口）",
    "env": "运行环境：fcmd 版本、Python、平台、依赖列表",
    "doctor": "环境健康诊断：可执行文件、原生库、Python 包、架构一致性",
    "graph": "fcmd yaml / pymake 子命令的 DAG 执行计划可视化",
}


# ---------------------------------------------------------------------------
# kwargs → argv
# ---------------------------------------------------------------------------


def _add_flag_args(cli: str, value: Any, *, is_bool: bool, default_true: bool) -> list[str]:
    """把 ``--flag value`` / ``--flag`` / ``--no-flag`` 转成 argv 片段。"""
    if is_bool:
        if cli.startswith("--no-"):
            # Typer 的否定式选项（如 ``--no-symbols``）：False 才需要显式传，True 是默认行为
            return [] if value else [cli]
        if value is True:
            return [cli]
        if value is False and default_true:
            return [cli.replace("--", "--no-", 1)]
        return []
    if isinstance(value, (list, tuple)):
        return [cli, *[str(v) for v in value]]
    return [cli, str(value)]


def _collect_positional(value: Any, pname: str) -> list[tuple[str, Any]]:
    """把 MCP kwargs 的位置参数值展开成 ``[(pname, value), ...]`` 列表。"""
    if isinstance(value, (list, tuple)):
        return [(pname, v) for v in value]
    return [(pname, value)]


def _kwargs_to_argv(spec: ToolSpec, kwargs: Mapping[str, Any], *, force_json: bool = True) -> list[str]:
    """把 MCP tool call 的 kwargs 转成 fcmd CLI 的 argv 列表。"""
    argv: list[str] = []
    positional: list[tuple[str, Any]] = []

    for p in param_schema(spec):
        pname = p["name"]
        if pname == "json_output":
            continue
        if pname not in kwargs:
            continue
        value = kwargs[pname]
        if value is None:
            continue
        cli = p["cli"]
        is_flag = cli.startswith("--")
        if is_flag:
            argv.extend(_add_flag_args(cli, value, is_bool=(p["type"] == "bool"), default_true=bool(p.get("default"))))
        else:
            positional.extend(_collect_positional(value, pname))

    for _pname, val in positional:
        argv.append(str(val))

    if force_json:
        argv.append("--json")
    return argv


def _builtin_kwargs_to_argv(tool: str, kwargs: Mapping[str, Any], *, force_json: bool = True) -> list[str]:
    """内建命令的 kwargs → argv 映射。"""
    argv: list[str] = []
    if tool == "info":
        if kwargs.get("tool"):
            argv.append(str(kwargs["tool"]))
            if kwargs.get("subcommand"):
                argv.append(str(kwargs["subcommand"]))
    elif tool == "graph":
        if kwargs.get("tool"):
            argv.append(str(kwargs["tool"]))
        if kwargs.get("subcommand"):
            argv.append(str(kwargs["subcommand"]))
        if kwargs.get("format"):
            argv.extend(["--format", str(kwargs["format"])])
    # env / doctor：无位置参数
    if force_json:
        argv.append("--json")
    return argv


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------


class ExecResult:
    """一次 fcmd 工具调用的原始结果。"""

    __slots__ = ("exit_code", "stderr", "stdout")

    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def parsed_envelope(self) -> dict[str, Any] | None:
        """尝试从 stdout 或 stderr 解析 ``fcmd-result/1`` / ``fcmd-error/1`` 信封。

        成功信封优先在 stdout；错误信封有的工具写 stdout、有的写 stderr，
        因此两条路径都尝试。返回第一个有效信封；都没有时返回 ``None``。
        """
        for candidate in (self.stdout, self.stderr):
            text = candidate.strip()
            if not text:
                continue
            try:
                envelope = json.loads(text)
            except json.JSONDecodeError:
                continue
            schema = envelope.get("schema", "")
            if schema in ("fcmd-result/1", "fcmd-error/1"):
                return envelope
        return None


def exec_fcmd(tool: str, argv: Sequence[str], *, builtin: bool = False) -> ExecResult:
    """进程内执行一次 fcmd 工具调用，捕获 stdout/stderr。"""
    import io

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    reset_error_state()
    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        if builtin:
            ensure_tools_discovered()
            rc = run_builtin(tool, list(argv))
        else:
            ensure_tools_discovered()
            rc = run_tool(tool, list(argv))
    except Exception as exc:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        stderr_full = stderr_buf.getvalue() + "".join(tb)
        return ExecResult(int(ExitCode.FAILURE), stdout_buf.getvalue(), stderr_full)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return ExecResult(int(rc), stdout_buf.getvalue(), stderr_buf.getvalue())


# ---------------------------------------------------------------------------
# 白名单索引 + tool name 路由表
# ---------------------------------------------------------------------------


class _ToolRegistry:
    """运行时按 tool_name 快速查 AgentWhitelistEntry（一次性构建）。"""

    def __init__(self) -> None:
        self._by_name: dict[str, AgentWhitelistEntry] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._tools: list[Tool] | None = None
        self._build()

    def _build(self) -> None:
        ensure_tools_discovered()
        for entry in AGENT_WHITELIST:
            tool_name = entry.tool_name
            self._by_name[tool_name] = entry
            if not entry.builtin:
                spec = get_tool(entry.tool, entry.subcommand)
                self._specs[tool_name] = spec

    def _build_tool(self, entry: AgentWhitelistEntry) -> Tool:
        return Tool(
            name=entry.tool_name,
            description=self._build_description(entry),
            inputSchema=self._build_schema(entry),
            annotations=self._build_annotations(entry),
        )

    def _build_schema(self, entry: AgentWhitelistEntry) -> dict[str, Any]:
        if entry.builtin:
            return _BUILTIN_SCHEMAS[entry.tool]
        spec = self._specs[entry.tool_name]
        return _tool_spec_to_input_schema(spec, skip_json_flag=True)

    def _build_description(self, entry: AgentWhitelistEntry) -> str:
        if entry.builtin:
            base = _BUILTIN_DESCRIPTIONS[entry.tool]
        else:
            spec = self._specs[entry.tool_name]
            base = spec.help or entry.tool
        parts = [base]
        if entry.meta == "network":
            parts.append("[network: 需要联网]")
        if entry.notes:
            parts.append(f"备注: {entry.notes}")
        return " — ".join(parts)

    @staticmethod
    def _build_annotations(entry: AgentWhitelistEntry) -> dict[str, Any] | None:
        ann: dict[str, Any] = {}
        if entry.meta == "read":
            ann["read_only_hint"] = True
        elif entry.meta == "network":
            ann["open_world_hint"] = True
        return ann or None

    @property
    def tools(self) -> list[Tool]:
        """按需构建 MCP Tool 列表；安全闸门等不依赖 mcp 包的路径不会触发。"""
        if self._tools is None:
            self._tools = [self._build_tool(entry) for entry in self._by_name.values()]
        return list(self._tools)

    def get_entry(self, tool_name: str) -> AgentWhitelistEntry | None:
        return self._by_name.get(tool_name)

    def get_spec(self, tool_name: str) -> ToolSpec | None:
        return self._specs.get(tool_name)


# ---------------------------------------------------------------------------
# 原生 Server：list_tools + call_tool
# ---------------------------------------------------------------------------


async def _list_tools_handler() -> list[Tool]:
    """MCP listTools handler：返回白名单内全部工具。"""
    registry = _get_registry()
    return registry.tools


async def _call_tool_handler(name: str, arguments: dict[str, Any]) -> CallToolResult:  # noqa: PLR0912 — 两段式分流使分支多
    """MCP callTool handler：按 tool_name 路由执行，内置两段式安全协议。"""
    registry = _get_registry()
    entry = registry.get_entry(name)
    if entry is None:
        return CallToolResult(
            content=[TextContent(type="text", text=f"工具 {name!r} 不在白名单内或未知")],
            isError=True,
        )

    # -- 分流：write 工具两段式 vs 普通只读/网络工具 ----------------------------
    is_write = entry.meta == "write"
    plan_hash_in_args = arguments.get("plan_hash") if is_write else None

    try:
        # 跑安全闸门（apply 阶段才会强制要求 plan_hash 存在且校验通过）
        _check_security(entry, arguments)
    except SecurityError as exc:
        return _security_error_to_result(exc)

    # -- 构造 argv ------------------------------------------------------------
    if entry.builtin:
        argv = _builtin_kwargs_to_argv(entry.tool, arguments, force_json=entry.force_json)
    else:
        spec = registry.get_spec(name)
        assert spec is not None
        # 过滤掉 plan_hash（这是 adapter 内部机制，不进 fcmd argv）
        filtered_args = {k: v for k, v in arguments.items() if k != "plan_hash"}
        argv = _kwargs_to_argv(spec, filtered_args, force_json=entry.force_json)
        if entry.subcommand is not None:
            argv = [entry.subcommand, *argv]

    # -- 两段式注入 -----------------------------------------------------------
    if is_write:
        if plan_hash_in_args:
            # apply 阶段：移除 plan_flag，加 apply_flag
            if entry.plan_flag:
                argv = [a for a in argv if a != entry.plan_flag and a != entry.plan_flag.replace("--", "--no-")]
            if entry.apply_flag:
                for f in entry.apply_flag.split():
                    argv.append(f)
        # plan 阶段：加 plan_flag（如果有）
        elif entry.plan_flag:
            argv.append(entry.plan_flag)

    # -- 执行 -----------------------------------------------------------------
    result = exec_fcmd(entry.tool, argv, builtin=entry.builtin)

    # -- post: plan 阶段缓存 hash / apply 阶段清缓存 ---------------------------
    if is_write:
        if not plan_hash_in_args:
            # plan 阶段：计算 hash 并缓存
            effective_args = {k: v for k, v in arguments.items() if k != "plan_hash"}
            ph = plan_hash_for(effective_args, entry.tool_name)
            _plan_cache[ph] = {
                "entry_name": entry.tool_name,
                "arguments": effective_args,
                "plan_text": result.stdout[:2000],  # 保留前段 plan 文本方便 debug
            }
            return _format_mcp_result(result, plan_hash=ph)
        else:
            # apply 阶段：清掉 cache 条目（一次性）
            _plan_cache.pop(plan_hash_in_args, None)
    return _format_mcp_result(result)


_registry_singleton: list[_ToolRegistry | None] = [None]


def _get_registry() -> _ToolRegistry:
    """返回模块级单例 ``_ToolRegistry``（首次调用时构建）。"""
    if _registry_singleton[0] is None:
        _registry_singleton[0] = _ToolRegistry()
    return _registry_singleton[0]


def _format_mcp_result(result: ExecResult, *, plan_hash: str | None = None) -> CallToolResult:
    """把 ExecResult 转成 MCP CallToolResult。

    - 成功信封 / 失败信封：直接返回完整 envelope JSON（字符串），模型可解析。
    - 无信封（工具未实现 --json）：返回原始 stdout + 退出码提示。
    - plan 阶段：envelope 里注入 plan_hash，让模型拿到 apply 凭据。
    """
    envelope = result.parsed_envelope()
    if envelope is not None:
        is_error = envelope.get("schema") == "fcmd-error/1" or not envelope.get("ok", True)
        if plan_hash is not None and not is_error:
            envelope["plan_hash"] = plan_hash
        text = json.dumps(envelope, ensure_ascii=False)
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=is_error)
    # 无 envelope：返回原始文本
    text = result.stdout.strip() or "(无输出)"
    if result.exit_code != 0:
        err = result.stderr.strip()
        if err:
            text += f"\n--- stderr ---\n{err}"
        text += f"\n[exit_code={result.exit_code}]"
    if plan_hash is not None:
        text += f"\n[plan_hash={plan_hash}]"
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        isError=(result.exit_code != 0),
    )


# ---------------------------------------------------------------------------
# MCP Resource / Prompt
# ---------------------------------------------------------------------------


_FCMD_RESOURCES: list[tuple[str, str, str]] = [
    ("fcmd://manifest", "fcmd 工具注册表（MCP 适配器暴露的白名单快照）", "application/json"),
    ("fcmd://info", "fcmd 运行环境：版本、Python、平台、依赖列表（等价于 fcmd info --json）", "application/json"),
    ("fcmd://doctor", "环境健康诊断：可执行文件、原生库、Python 包、架构一致性", "application/json"),
    (
        "fcmd://policy",
        "当前 MCP 安全策略（workspace_root / allow_network / require_write_confirmation ...）",
        "application/json",
    ),
]


_FCMD_PROMPTS: list[tuple[str, str]] = [
    ("fcmd-quickstart", "fcmd MCP server 快速上手：可用工具类别、调用约定、两段式确认协议"),
    ("fcmd-filesystem-scan", "扫描 workspace：用 filesearch/filescan 先扫出需要处理的文件列表，再定向处理"),
    ("fcmd-diff-confirm", "改文件前先 diff 确认：textdiff + renamer preview，再 apply"),
]


async def _list_resources_handler() -> list[Resource]:
    return [Resource(uri=uri, name=name, mimeType=mime) for uri, name, mime in _FCMD_RESOURCES]


async def _read_resource_handler(uri: str) -> ReadResourceResult:
    """按 uri 路由读 resource。"""
    from mcp.types import TextResourceContents  # type: ignore[assignment]

    if uri == "fcmd://manifest":
        registry = _get_registry()
        snapshot = [
            {
                "tool_name": entry.tool_name,
                "tool": entry.tool,
                "subcommand": entry.subcommand,
                "meta": entry.meta,
                "builtin": entry.builtin,
                "notes": entry.notes,
                "plan_flag": entry.plan_flag,
                "apply_flag": entry.apply_flag,
            }
            for entry in registry._by_name.values()
        ]
        text = json.dumps({"count": len(snapshot), "tools": snapshot}, ensure_ascii=False, indent=2, sort_keys=True)
    elif uri == "fcmd://info":
        r = exec_fcmd("info", ["--json"], builtin=True)
        text = r.stdout.strip() or json.dumps({"ok": False, "message": r.stderr.strip()})
    elif uri == "fcmd://doctor":
        r = exec_fcmd("doctor", ["--json"], builtin=True)
        text = r.stdout.strip() or json.dumps({"ok": False, "message": r.stderr.strip()})
    elif uri == "fcmd://policy":
        text = json.dumps(_get_policy().to_dict(), ensure_ascii=False, indent=2)
    else:
        text = json.dumps({"error": f"unknown uri: {uri}"}, ensure_ascii=False)
    return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="application/json", text=text)])


_PROMPT_RECIPES: dict[str, str] = {
    "fcmd-quickstart": (
        "你可以通过 fcmd MCP server 调用以下类别工具：\n"
        "- **filesearch/filescan**：文件内容/名称/正则搜索，用 `fcmd__filesearch__name` / `fcmd__filesearch__content` / `fcmd__filescan__scan`\n"
        "- **数据类**：csvtool/jsontool/tomltool/xmltool/yamtool 的只读子命令\n"
        "- **网络探测**：nettool/urlcheck/portcheck/iptool（如果策略 allow_network=True）\n"
        "- **WSL / PE 只读**：wslman list/status/info、peview info/headers/sections 等\n"
        "- **WSL / PE 写操作**：wslman export/import/unregister/shutdown/backup-remove、peview edit —— 必须两段式\n"
        "\n"
        "## 两段式确认协议（write 工具强制）\n"
        "1. **plan 阶段**：调用写工具但不传 `plan_hash` 参数，adapter 自动注入 dry-run/preview flag。\n"
        "2. 从返回 envelope 里拿到 `plan_hash` 字段。\n"
        "3. **apply 阶段**：同一工具 + 同一参数 + 加上 `plan_hash=<上一步的 hash>`，adapter 注入 --yes/--apply 真正写入。\n"
        "4. plan_hash 绑定了 arguments，apply 时篡改参数会被拒绝；同一个 hash 一次性消费。\n"
        "\n"
        "## 机读输出\n"
        "adapter 强制注入 `--json`，所以所有结果都是 JSON envelope：\n"
        '- 成功：`{"schema": "fcmd-result/1", "ok": true, ...}`\n'
        '- 失败：`{"schema": "fcmd-error/1", "ok": false, "code": "ERROR_CODE", ...}`\n'
        '- 安全拦截：同样是 fcmd-error/1，但 `kind="security"`，code 如 WRITE_CONFIRM_REQUIRED / PATH_ESCAPE'
    ),
    "fcmd-filesystem-scan": (
        "在对目录做任何写操作前，先扫一遍确认范围：\n"
        '1. `fcmd__filesearch__name(pattern="*.log", path=".")` —— 找出所有 log 文件\n'
        '2. `fcmd__filesearch__content(pattern="TODO|FIXME", path=".")` —— 找出含 TODO 的文件\n'
        '3. `fcmd__filescan__scan(path=".", rule="size>10MB")` —— 找大文件（filescan 规则语法）\n'
        "4. 根据扫描结果决定下一步，不要盲目遍历\n"
        "5. 读操作用 filescan scan；写操作（如果用到）必须先 plan 再 apply"
    ),
    "fcmd-diff-confirm": (
        "改文件 / 重命名 / 压缩前先预演：\n"
        '1. **重命名**：`fcmd__renamer__preview(path=".", pattern=..., replace=...)` —— 看 old→new 映射对不对\n'
        "2. **文本 diff**：`fcmd__textdiff__file(path_a=..., path_b=...)` —— 看具体改了哪些行\n"
        "3. **归档内容**：`fcmd__archivex__list(file=...)` —— 看归档里有啥，别解错\n"
        "4. 确认 diff 无误后，如需写入操作（如 wslman import / peview edit），走 plan→apply 两段式"
    ),
}


async def _list_prompts_handler() -> list[Prompt]:
    return [Prompt(name=name, description=desc) for name, desc in _FCMD_PROMPTS]


async def _get_prompt_handler(name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult:  # noqa: ARG001
    from mcp.types import PromptMessage  # type: ignore[assignment]

    recipe = _PROMPT_RECIPES.get(name)
    if recipe is None:
        recipe = f"(unknown prompt: {name})"
    return GetPromptResult(messages=[PromptMessage(role="user", content=TextContent(type="text", text=recipe))])


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_mcp_server() -> Server:
    """构造 MCP Server 并注册全部 handler（tools + resources + prompts）。"""
    if not _MCP_AVAILABLE or Server is None:
        raise RuntimeError(
            "mcp 包未安装。请运行 'uv pip install \"mcp>=1.0,<2\"' 或安装 fcmd[agent] extra （uv sync --extra agent）。"
        )
    server = Server(MCP_SERVER_NAME)
    server.list_tools = _list_tools_handler  # type: ignore[assignment]
    server.call_tool = _call_tool_handler  # type: ignore[assignment]
    server.list_resources = _list_resources_handler  # type: ignore[assignment]
    server.read_resource = _read_resource_handler  # type: ignore[assignment]
    server.list_prompts = _list_prompts_handler  # type: ignore[assignment]
    server.get_prompt = _get_prompt_handler  # type: ignore[assignment]
    return server


def run_mcp_stdio() -> None:
    """启动 MCP stdio server（阻塞直到客户端断开）。"""
    import asyncio

    if not _MCP_AVAILABLE or Server is None or stdio_server is None:
        print(
            "fcmd MCP server 需要 mcp Python 包。请运行: uv pip install mcp>=1.0,<2",
            file=sys.stderr,
        )
        sys.exit(1)
    server = build_mcp_server()

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):  # pyrefly: ignore
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    run_mcp_stdio()
