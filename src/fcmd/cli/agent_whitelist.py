"""小模型调用的工具白名单。

本模块是 MCP 适配器的唯一准入闸口：只有出现在 :data:`AGENT_WHITELIST`
中的 ``(tool, subcommand)`` 组合才会被暴露为 MCP tool。白名单外的调用被
适配器主动拒绝，而非依赖上层路由决定。

与 :mod:`fcmd.cli._schema` 的 ``GUI_TOOLS`` 互补：GUI_TOOLS 过滤 Tk 入口，
本清单过滤"机器不应触达"的能力（破坏性写操作、交互式、需要 TTY、无机读
输出等）。两个集合**不交**——GUI_TOOLS 不在本清单里。

首批范围：只读工具，优先已实现 ``--json`` 的子命令（机器有稳定输出）。
写操作第二批再评估接入，不在本清单中。
"""

from __future__ import annotations

from typing import Literal

ToolMeta = Literal["read", "write", "network"]
"""子命令的能力侧写：决定 MCP annotations 与未来闸门策略。"""


class AgentWhitelistEntry:
    """单个允许暴露的 ``(tool, subcommand)``。

    Attributes
    ----------
    tool:
        工具名（与 ``_TOOL_REGISTRY`` key 同值，内建命令也用此名）。
    subcommand:
        子命令名；单命令工具为 ``None``。
    meta:
        能力侧写，用于 MCP annotations 与未来确认闸门。
    builtin:
        True 表示内建命令（``info`` / ``env`` / ``doctor`` / ``graph``），
        不走 ``@fcmd.tool`` 注册，需要在适配器里单独路由到 cli._builtins。
    force_json:
        是否由适配器强制注入 ``--json``。默认 True；个别子命令机读开关
        叫 ``--format json`` 而非 ``--json`` 时仍为 True（适配器内部
        决定实际注入的 flag）。
    notes:
        自由文本备注（写在 MCP tool description 末尾）。
    """

    __slots__ = (
        "apply_flag",
        "builtin",
        "force_json",
        "meta",
        "notes",
        "plan_flag",
        "subcommand",
        "tool",
    )

    def __init__(  # noqa: PLR0913 — 数据类参数多是设计使然
        self,
        tool: str,
        subcommand: str | None,
        meta: ToolMeta = "read",
        *,
        builtin: bool = False,
        force_json: bool = True,
        notes: str = "",
        plan_flag: str | None = None,
        apply_flag: str | None = None,
    ) -> None:
        self.tool = tool
        self.subcommand = subcommand
        self.meta = meta
        self.builtin = builtin
        self.force_json = force_json
        self.notes = notes
        # write 工具专用：adapter 两段式确认协议用
        self.plan_flag = plan_flag  # plan 阶段注入的 preview/dry-run flag；None=不注入
        self.apply_flag = apply_flag  # apply 阶段注入的 --yes/--apply flag；None=不注入

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.tool, self.subcommand)

    @property
    def tool_name(self) -> str:
        """MCP tool 命名：扁平 ``fcmd__<tool>__<subcommand>``（无 subcommand 时为 ``fcmd__<tool>``）。"""
        if self.subcommand is None:
            return f"fcmd__{self.tool}"
        return f"fcmd__{self.tool}__{self.subcommand}"


# ---------------------------------------------------------------------------
# 首批白名单：只读、有稳定机读输出、无交互、无破坏性写操作
# ---------------------------------------------------------------------------

AGENT_WHITELIST: list[AgentWhitelistEntry] = [
    # ---- 内建自描述（不走 @fcmd.tool） ----
    AgentWhitelistEntry(
        "info", None, meta="read", builtin=True, notes="fcmd 工具注册表总览与参数字段；MCP resource 也会暴露 info.json"
    ),
    AgentWhitelistEntry("env", None, meta="read", builtin=True, notes="运行环境、版本、依赖检查"),
    AgentWhitelistEntry("doctor", None, meta="read", builtin=True, notes="可执行文件、原生库、Python 包、架构一致性"),
    AgentWhitelistEntry("graph", "tree", meta="read", builtin=True, notes="fcmd yaml DAG 可视化；机读输出暂不稳定"),
    # ---- 文件系统搜索 ----
    AgentWhitelistEntry("filesearch", "content", meta="read", notes="内容正则搜索；建议加 --limit 控制体积"),
    AgentWhitelistEntry("filesearch", "name", meta="read", notes="文件名 glob 搜索；fnmatch 语法"),
    AgentWhitelistEntry("filescan", "scan", meta="read", notes="规则/表达式扫描，支持文件/PDF/文档，结果可缓存"),
    AgentWhitelistEntry("which", None, meta="read"),
    AgentWhitelistEntry("sysinfo", None, meta="read"),
    AgentWhitelistEntry("filecheck", None, meta="read"),
    AgentWhitelistEntry("pathtool", "show", meta="read"),
    AgentWhitelistEntry("pathtool", "rel", meta="read"),
    AgentWhitelistEntry("pathtool", "norm", meta="read"),
    AgentWhitelistEntry("pathtool", "diff", meta="read"),
    # ---- 数据文件 ----
    AgentWhitelistEntry("csvtool", "show", meta="read"),
    AgentWhitelistEntry("csvtool", "to-json", meta="read"),
    AgentWhitelistEntry("csvtool", "from-json", meta="read"),
    AgentWhitelistEntry("csvtool", "select", meta="read"),
    AgentWhitelistEntry("csvtool", "merge", meta="read"),
    AgentWhitelistEntry("jsontool", "pretty", meta="read", force_json=False),
    AgentWhitelistEntry("jsontool", "minify", meta="read", force_json=False),
    AgentWhitelistEntry("jsontool", "query", meta="read", force_json=False),
    AgentWhitelistEntry("jsontool", "sort", meta="read", force_json=False),
    AgentWhitelistEntry("tomltool", "get", meta="read"),
    AgentWhitelistEntry("tomltool", "keys", meta="read"),
    AgentWhitelistEntry("tomltool", "format", meta="read"),
    AgentWhitelistEntry("tomltool", "validate", meta="read"),
    AgentWhitelistEntry("xmltool", "pretty", meta="read", force_json=False),
    AgentWhitelistEntry("xmltool", "minify", meta="read", force_json=False),
    AgentWhitelistEntry("xmltool", "extract", meta="read", force_json=False),
    AgentWhitelistEntry("xmltool", "validate", meta="read", force_json=False),
    AgentWhitelistEntry("yamtool", "pretty", meta="read", force_json=False),
    AgentWhitelistEntry("yamtool", "get", meta="read", force_json=False),
    AgentWhitelistEntry("yamtool", "keys", meta="read", force_json=False),
    AgentWhitelistEntry("yamtool", "validate", meta="read", force_json=False),
    AgentWhitelistEntry("inifile", "get", meta="read"),
    AgentWhitelistEntry("inifile", "list", meta="read"),
    AgentWhitelistEntry("inifile", "keys", meta="read"),
    # ---- 计算与编码 ----
    AgentWhitelistEntry("mathtool", "eval", meta="read"),
    AgentWhitelistEntry("mathtool", "sqrt", meta="read"),
    AgentWhitelistEntry("mathtool", "pow", meta="read"),
    AgentWhitelistEntry("mathtool", "factorial", meta="read"),
    AgentWhitelistEntry("convtool", "length", meta="read"),
    AgentWhitelistEntry("convtool", "weight", meta="read"),
    AgentWhitelistEntry("convtool", "temp", meta="read"),
    AgentWhitelistEntry("convtool", "datasize", meta="read"),
    AgentWhitelistEntry("hashfile", "f", meta="read"),
    AgentWhitelistEntry("hashfile", "d", meta="read"),
    AgentWhitelistEntry("hashtool", "md5", meta="read"),
    AgentWhitelistEntry("hashtool", "sha1", meta="read"),
    AgentWhitelistEntry("hashtool", "sha256", meta="read"),
    AgentWhitelistEntry("hashtool", "sha512", meta="read"),
    AgentWhitelistEntry("codetool", "base64", meta="read"),
    AgentWhitelistEntry("codetool", "url", meta="read"),
    AgentWhitelistEntry("codetool", "hex", meta="read"),
    AgentWhitelistEntry("codetool", "rot13", meta="read"),
    AgentWhitelistEntry("codetool", "html", meta="read"),
    AgentWhitelistEntry("urltool", "parse", meta="read"),
    AgentWhitelistEntry("urltool", "query", meta="read"),
    AgentWhitelistEntry("urltool", "baseurl", meta="read"),
    AgentWhitelistEntry("calcdate", "diff", meta="read"),
    AgentWhitelistEntry("calcdate", "compare", meta="read"),
    AgentWhitelistEntry("stattool", "mean", meta="read"),
    AgentWhitelistEntry("stattool", "median", meta="read"),
    AgentWhitelistEntry("stattool", "stddev", meta="read"),
    AgentWhitelistEntry("stattool", "variance", meta="read"),
    AgentWhitelistEntry("stattool", "summarize", meta="read"),
    AgentWhitelistEntry("timetool", "now", meta="read"),
    AgentWhitelistEntry("timetool", "parse", meta="read"),
    AgentWhitelistEntry("timetool", "unix", meta="read"),
    AgentWhitelistEntry("timetool", "fromunix", meta="read"),
    AgentWhitelistEntry("timetool", "convert", meta="read"),
    # ---- 文本处理 ----
    AgentWhitelistEntry("textdiff", "file", meta="read"),
    AgentWhitelistEntry("textdiff", "dir", meta="read"),
    AgentWhitelistEntry("regextool", "match", meta="read"),
    AgentWhitelistEntry("regextool", "find", meta="read"),
    AgentWhitelistEntry("regextool", "split", meta="read"),
    AgentWhitelistEntry("regexvalidate", None, meta="read"),
    AgentWhitelistEntry("txttool", "count", meta="read"),
    AgentWhitelistEntry("txttool", "sort", meta="read"),
    AgentWhitelistEntry("txttool", "unique", meta="read"),
    AgentWhitelistEntry("colortool", "hex2rgb", meta="read"),
    AgentWhitelistEntry("colortool", "rgb2hex", meta="read"),
    AgentWhitelistEntry("colortool", "rgb2hsl", meta="read"),
    AgentWhitelistEntry("colortool", "hsl2rgb", meta="read"),
    AgentWhitelistEntry("casetool", "snake", meta="read"),
    AgentWhitelistEntry("casetool", "camel", meta="read"),
    AgentWhitelistEntry("casetool", "pascal", meta="read"),
    AgentWhitelistEntry("casetool", "kebab", meta="read"),
    AgentWhitelistEntry("asciitool", "char", meta="read"),
    AgentWhitelistEntry("asciitool", "code", meta="read"),
    AgentWhitelistEntry("asciitool", "table", meta="read"),
    # ---- 差异与比较 ----
    AgentWhitelistEntry("filesync", "compare", meta="read"),
    AgentWhitelistEntry("renamer", "preview", meta="read"),
    # ---- 归档只读 ----
    AgentWhitelistEntry("archivex", "list", meta="read"),
    AgentWhitelistEntry(
        "folderzip", None, meta="read", notes="folderzip 单命令压缩；暂未升级为 write（无 --yes），后续审计"
    ),
    # ---- PE 只读 ----
    AgentWhitelistEntry("peview", "info", meta="read"),
    AgentWhitelistEntry("peview", "headers", meta="read"),
    AgentWhitelistEntry("peview", "sections", meta="read"),
    AgentWhitelistEntry("peview", "directories", meta="read"),
    AgentWhitelistEntry("peview", "imports", meta="read"),
    AgentWhitelistEntry("peview", "exports", meta="read"),
    AgentWhitelistEntry("peview", "resources", meta="read"),
    AgentWhitelistEntry("peview", "relocations", meta="read"),
    AgentWhitelistEntry("peview", "debug", meta="read"),
    AgentWhitelistEntry("peview", "hex", meta="read"),
    AgentWhitelistEntry("peview", "check", meta="read"),
    AgentWhitelistEntry("peview", "fields", meta="read"),
    # ---- PE 写操作（第二批：两段式确认） ----
    AgentWhitelistEntry(
        "peview",
        "edit",
        meta="write",
        force_json=False,  # peview edit 不支持 --json；只有 plan 文本输出
        plan_flag=None,  # peview edit 默认只预演，不需要额外 plan flag
        apply_flag="--apply",
        notes="编辑 PE 头字段；默认 dry-run 预演，apply 加 --apply 真正写入",
    ),
    # ---- 网络探测 ----
    AgentWhitelistEntry("nettool", "get", meta="network", force_json=False),
    AgentWhitelistEntry("nettool", "head", meta="network", force_json=False),
    AgentWhitelistEntry("urlcheck", "u", meta="network"),
    AgentWhitelistEntry("urlcheck", "r", meta="network"),
    AgentWhitelistEntry("portcheck", "c", meta="network"),
    AgentWhitelistEntry("portcheck", "s", meta="network"),
    AgentWhitelistEntry("iptool", "parse", meta="network"),
    AgentWhitelistEntry("iptool", "subnet", meta="network"),
    AgentWhitelistEntry("iptool", "validate", meta="network"),
    # ---- WSL 只读 ----
    AgentWhitelistEntry("wslman", "list", meta="read"),
    AgentWhitelistEntry("wslman", "status", meta="read"),
    AgentWhitelistEntry("wslman", "info", meta="read"),
    AgentWhitelistEntry("wslman", "doctor", meta="read"),
    AgentWhitelistEntry("wslman", "backups", meta="read"),
    AgentWhitelistEntry("wslman", "available", meta="read"),
    AgentWhitelistEntry("wslman", "config", meta="read"),
    # ---- WSL 写操作（第二批：两段式确认） ----
    AgentWhitelistEntry(
        "wslman",
        "export",
        meta="write",
        plan_flag="--preview",
        apply_flag="--overwrite",
        notes="导出发行版为 tar；plan=--preview, apply=--overwrite",
    ),
    AgentWhitelistEntry(
        "wslman",
        "import",
        meta="write",
        plan_flag="--preview",
        apply_flag="--replace --yes",
        notes="从 tar 导入发行版；plan=--preview, apply 需调用方传 --replace 本 adapter 补 --yes",
    ),
    AgentWhitelistEntry(
        "wslman",
        "unregister",
        meta="write",
        plan_flag="--preview",
        apply_flag="--yes",
        notes="注销发行版；plan=--preview, apply=--yes",
    ),
    AgentWhitelistEntry(
        "wslman",
        "shutdown",
        meta="write",
        plan_flag="--preview",
        apply_flag="--yes",
        notes="停止全部发行版（含 Docker Desktop）；plan=--preview, apply=--yes",
    ),
    AgentWhitelistEntry(
        "wslman",
        "backup-remove",
        meta="write",
        plan_flag="--preview",
        apply_flag="--yes",
        notes="删除备份文件；plan=--preview, apply=--yes",
    ),
    # ---- LLM 只读 ----
    AgentWhitelistEntry("llmserve", "status", meta="read"),
    AgentWhitelistEntry("llmbench", None, meta="read"),
    AgentWhitelistEntry("llmdoctor", None, meta="read"),
    AgentWhitelistEntry("llmenv", "show", meta="read"),
    AgentWhitelistEntry("llmenv", "list", meta="read"),
    AgentWhitelistEntry("llmmodel", "list", meta="read"),
    AgentWhitelistEntry("llmmodel", "find", meta="read"),
    AgentWhitelistEntry("llmmodel", "vram", meta="read"),
    # ---- 领域应用只读 ----
    AgentWhitelistEntry("dynacalc", "report", meta="read"),
    AgentWhitelistEntry("dynacalc", "compare", meta="read"),
    AgentWhitelistEntry("dynacalc", "gates", meta="read"),
    AgentWhitelistEntry("paramcad", "digest", meta="read"),
    AgentWhitelistEntry("paramcad", "inspect", meta="read"),
    AgentWhitelistEntry("paramesh", "check", meta="read"),
    AgentWhitelistEntry("paramesh", "report", meta="read"),
    AgentWhitelistEntry("lscalc", "status", meta="read"),
]


def build_whitelist_index() -> dict[tuple[str, str | None], AgentWhitelistEntry]:
    """构建 ``(tool, subcommand) → entry`` 快速查询字典。"""
    return {entry.key: entry for entry in AGENT_WHITELIST}


def total_whitelisted_subcommands() -> int:
    """白名单条目数（便于启动时打印统计）。"""
    return len(AGENT_WHITELIST)
