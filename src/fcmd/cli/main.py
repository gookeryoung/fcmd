"""fcmd CLI 主入口：FcmdApp 路由。

通过 ``fcmd <tool> [command] [options]`` 调用所有工具，
工具定义在 ``fcmd.cli`` 包中，每个模块用 ``@fx.tool`` 装饰器注册。

工具发现
--------
``fcmd.cli`` 包下每个非 ``main`` / 非 ``_`` 前缀的模块即一个工具，
模块名即工具名。模块内可选定义 ``__tool_aliases__: list[str]`` 声明别名。
首次调用 ``FcmdApp.run()`` 时用 ``pkgutil.iter_modules`` 扫描并导入所有
工具模块，``import fcmd`` 冷启动不受影响。

用法
----
    fcmd                  # 列出所有可用工具
    fcmd pymake           # 查看 pymake 工具的子命令
    fcmd pymake b         # 调用 pymake 的 b 子命令
    fcmd --version        # 输出版本号
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import importlib
import pkgutil
import sys
from collections.abc import Sequence
from typing import Any

from fcmd import __version__
from fcmd.apis.toolkit import _TOOL_REGISTRY, build_tool_graph, run_tool
from fcmd.console import get_console
from fcmd.errors import FcmdError

__all__ = ["FcmdApp", "main"]


# 工具别名 → 规范名（由 _ensure_tools_discovered 懒填充）
_TOOL_ALIASES: dict[str, str] = {}

# 规范工具名 → 模块路径（由 _ensure_tools_discovered 懒填充）
_TOOL_MODULES: dict[str, str] = {}

# 发现标志：True 表示已扫描过 fcmd.cli 包
_TOOLS_DISCOVERED = False

# 内建命令名（不通过 @fx.tool 注册，由 FcmdApp 直接处理）
_BUILTIN_COMMANDS: tuple[str, ...] = ("graph", "info", "completion", "yaml")


def _ensure_tools_discovered() -> None:
    """首次调用时扫描 ``fcmd.cli`` 包，发现工具模块并填充注册表。

    幂等：后续调用直接返回。用 ``setdefault`` 填充，不覆盖测试通过
    ``monkeypatch.setitem`` 注入的键。扫描时导入模块以读取
    ``__tool_aliases__`` 并触发 ``@fx.tool`` 注册。
    """
    global _TOOLS_DISCOVERED  # noqa: PLW0603
    if _TOOLS_DISCOVERED:
        return
    _TOOLS_DISCOVERED = True

    # 懒导入 fcmd.cli 以访问 __path__，避免 import fcmd 时触发
    import fcmd.cli as cli_pkg

    for _finder, name, _ispkg in pkgutil.iter_modules(cli_pkg.__path__):
        # 排除入口模块、私有模块、包自身
        if name.startswith("_") or name == "main":
            continue
        module_path = f"fcmd.cli.{name}"
        tool_name = name
        _TOOL_MODULES.setdefault(tool_name, module_path)
        _TOOL_ALIASES.setdefault(tool_name, tool_name)
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        # 读取模块声明的别名
        aliases = getattr(mod, "__tool_aliases__", ())
        for alias in aliases:
            _TOOL_ALIASES.setdefault(alias, tool_name)


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器（保留 P0 兼容）。"""
    parser = argparse.ArgumentParser(
        prog="fcmd",
        description="极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


class FcmdApp:
    """fcmd 统一入口应用。

    路由 ``fcmd <tool> [command]`` 到 ``@fx.tool`` 注册的工具。
    """

    def __init__(self, argv: Sequence[str] | None = None) -> None:
        self._argv: list[str] = list(argv) if argv is not None else sys.argv[1:]

    def run(self) -> int:
        """主入口，返回退出码。"""
        _ensure_tools_discovered()
        if not self._argv or self._argv[0] in ("--help", "-h"):
            self._list_tools()
            return 0

        first = self._argv[0]
        if first in ("--version", "-V"):
            get_console().print(f"fcmd [bold cyan]{__version__}[/bold cyan]")
            return 0

        # 内建命令（graph/info/...）优先于工具路由
        if first in _BUILTIN_COMMANDS:
            return self._run_builtin(first, self._argv[1:])

        rest = self._argv[1:]
        resolved = self._resolve_tool(first)
        if resolved is None:
            self._print_unknown_tool(first)
            return 1

        return self._run_tool(resolved, rest)

    # ------------------------------------------------------------------ #
    # 内建命令
    # ------------------------------------------------------------------ #
    def _run_builtin(self, name: str, argv: list[str]) -> int:
        """分发内建命令。"""
        if name == "graph":
            return self._builtin_graph(argv)
        if name == "info":
            return self._builtin_info(argv)
        if name == "completion":
            return self._builtin_completion(argv)
        if name == "yaml":
            return self._builtin_yaml(argv)
        get_console().print(f"[red]错误:[/red] 未知内建命令 {name!r}")
        return 1

    def _builtin_graph(self, argv: list[str]) -> int:
        """``fcmd graph <tool> <subcommand> [--format=mermaid|layers|describe]``。

        可视化工具子命令的 DAG 执行计划，不执行任务。

        格式：
        - ``mermaid``（默认）：Mermaid graph 定义，可粘贴到 mermaid.live
        - ``layers``：拓扑分层列表（每层可并行）
        - ``describe``：人类可读多行摘要（Graph.describe）
        """
        parser = argparse.ArgumentParser(
            prog="fcmd graph",
            description="可视化工具子命令的 DAG 执行计划",
        )
        parser.add_argument("tool", help="工具名（如 pymake）")
        parser.add_argument("subcommand", nargs="?", default=None, help="目标子命令（如 tc/all）")
        parser.add_argument(
            "--format",
            choices=("mermaid", "layers", "describe"),
            default="mermaid",
            help="输出格式（默认 mermaid）",
        )
        if not argv:
            parser.print_help()
            return 1
        parsed = parser.parse_args(argv)

        resolved = self._resolve_tool(parsed.tool)
        if resolved is None:
            self._print_unknown_tool(parsed.tool)
            return 1

        try:
            graph = build_tool_graph(resolved, parsed.subcommand)
        except FcmdError as e:
            get_console().print(f"[red]错误:[/red] {e}")
            return 1

        if parsed.format == "mermaid":
            get_console().print(graph.to_mermaid(), end="")
        elif parsed.format == "layers":
            layers = graph.layers()
            for idx, layer in enumerate(layers, 1):
                get_console().print(f"Layer {idx}: {layer}")
        else:  # describe
            get_console().print(graph.describe())
        return 0

    def _builtin_info(self, argv: list[str]) -> int:
        """``fcmd info <tool> [subcommand]``。

        展示工具或子命令的元信息（不执行）：

        - ``fcmd info``：列出全部内建命令与已注册工具
        - ``fcmd info <tool>``：列出工具的所有子命令（含 hidden）及
          help / needs / strategy / cmd 摘要
        - ``fcmd info <tool> <subcommand>``：展示该子命令的完整 ToolSpec
          字段（cmd / needs / strategy / cwd / hidden / env / retry / timeout）
        """
        parser = argparse.ArgumentParser(
            prog="fcmd info",
            description="展示工具或子命令的元信息",
        )
        parser.add_argument("tool", nargs="?", default=None, help="工具名（如 pymake）")
        parser.add_argument("subcommand", nargs="?", default=None, help="子命令名（如 tc）")
        if not argv:
            self._info_overview()
            return 0
        parsed = parser.parse_args(argv)

        resolved = self._resolve_tool(parsed.tool)
        if resolved is None:
            self._print_unknown_tool(parsed.tool)
            return 1

        subs = self._load_tool_subs(resolved)
        if subs is None:
            return 1

        if parsed.subcommand is None:
            self._info_tool(resolved, subs)
            return 0

        # 子命令详情：subcommand 可能是 None（单命令工具），用 .get 兼容
        spec = subs.get(parsed.subcommand)
        if spec is None:
            get_console().print(f"[red]错误:[/red] 工具 {resolved!r} 没有子命令 {parsed.subcommand!r}")
            return 1
        self._info_subcommand(resolved, spec)
        return 0

    def _load_tool_subs(self, tool_name: str) -> dict[str | None, Any] | None:
        """加载工具模块并返回子命令字典，失败时打印错误并返回 None。"""
        if tool_name in _TOOL_MODULES:
            try:
                importlib.import_module(_TOOL_MODULES[tool_name])
            except ImportError as e:
                get_console().print(f"[red]错误:[/red] 加载工具 {tool_name!r} 失败: {e}")
                return None
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        if tool_name not in _TOOL_REGISTRY:
            get_console().print(f"[red]错误:[/red] 工具 {tool_name!r} 未注册")
            return None
        return _TOOL_REGISTRY[tool_name]

    def _info_overview(self) -> None:
        """``fcmd info`` 无参数：列出内建命令与工具。"""
        from rich.table import Table

        console = get_console()
        console.print("[bold]内建命令:[/bold]")
        for cmd_name in _BUILTIN_COMMANDS:
            console.print(f"  [cyan]fcmd {cmd_name}[/cyan]")

        console.print("\n[bold]已注册工具:[/bold]")
        table = Table(show_header=True, header_style="bold", show_lines=False)
        table.add_column("工具", style="cyan", no_wrap=True)
        table.add_column("别名", style="dim", no_wrap=True)
        table.add_column("子命令数", justify="right", no_wrap=True)
        table.add_column("说明")
        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = self._aliases_for(tool_name)
            # 触发模块导入以统计子命令数
            if tool_name in _TOOL_MODULES:
                with contextlib.suppress(ImportError):
                    importlib.import_module(_TOOL_MODULES[tool_name])
            from fcmd.apis.toolkit import _TOOL_REGISTRY

            subs = _TOOL_REGISTRY.get(tool_name, {})
            visible = sum(1 for sc in subs if sc is not None and not subs[sc].hidden)
            hidden = sum(1 for sc in subs if sc is not None and subs[sc].hidden)
            count_str = f"{visible} (+{hidden} hidden)" if hidden else str(visible)
            table.add_row(tool_name, ", ".join(aliases), count_str, self._tool_description(tool_name))
        console.print(table)
        console.print("\n[dim]用法: fcmd info <tool> [subcommand][/dim]")

    def _info_tool(self, tool_name: str, subs: dict[str | None, Any]) -> None:
        """``fcmd info <tool>``：列出工具的全部子命令。"""
        from rich.table import Table

        console = get_console()
        console.print(f"[bold cyan]{tool_name}[/bold cyan] 子命令:")
        table = Table(show_header=True, header_style="bold", show_lines=False)
        table.add_column("子命令", style="cyan", no_wrap=True)
        table.add_column("类型", style="dim", no_wrap=True)
        table.add_column("needs", style="yellow", no_wrap=True)
        table.add_column("strategy", style="magenta", no_wrap=True)
        table.add_column("说明")
        # 排序：visible 在前（按名排序），hidden 在后（按名排序）
        visible = sorted((sc, spec) for sc, spec in subs.items() if sc is not None and not spec.hidden)
        hidden = sorted((sc, spec) for sc, spec in subs.items() if sc is not None and spec.hidden)
        for sc, spec in visible + hidden:
            kind = self._spec_kind(spec)
            needs_str = ", ".join(spec.needs) if spec.needs else "-"
            strategy_str = spec.strategy or "-"
            help_str = spec.help or ""
            if spec.hidden:
                sc_str = f"[dim]{sc} (hidden)[/dim]"
                help_str = f"[dim]{help_str}[/dim]"
            else:
                sc_str = str(sc)
            table.add_row(sc_str, kind, needs_str, strategy_str, help_str)
        console.print(table)

    def _info_subcommand(self, tool_name: str, spec: Any) -> None:
        """``fcmd info <tool> <subcommand>``：展示 ToolSpec 完整字段。"""
        console = get_console()
        sc_name = spec.subcommand if spec.subcommand is not None else "(single)"
        console.print(f"[bold cyan]{tool_name}[/bold cyan] / [cyan]{sc_name}[/cyan]")
        fields: list[tuple[str, str]] = [
            ("help", spec.help or "-"),
            ("description", spec.description or "-"),
            ("kind", self._spec_kind(spec)),
            ("cmd", " ".join(spec.cmd) if isinstance(spec.cmd, tuple) else (spec.cmd or "-")),
            ("needs", ", ".join(spec.needs) if spec.needs else "-"),
            ("strategy", spec.strategy or "-"),
            ("cwd", str(spec.cwd) if spec.cwd is not None else "-"),
            ("hidden", "yes" if spec.hidden else "no"),
            ("allow_upstream_skip", "yes" if spec.allow_upstream_skip else "no"),
            ("timeout", str(spec.timeout) if spec.timeout is not None else "-"),
            ("env", ", ".join(f"{k}=..." for k in spec.env) if spec.env else "-"),
            ("retry", str(spec.retry) if spec.retry is not None else "-"),
        ]
        from rich.table import Table

        table = Table(show_header=False, box=None, show_lines=False)
        table.add_column("字段", style="bold", no_wrap=True)
        table.add_column("值")
        for name, value in fields:
            table.add_row(name, value)
        console.print(table)

    @staticmethod
    def _spec_kind(spec: Any) -> str:
        """判断 ToolSpec 类型：cmd / aggregate / fn。"""
        if spec.cmd is not None:
            return "cmd"
        if spec.needs:
            return "aggregate"
        return "fn"

    # ------------------------------------------------------------------ #
    # completion 内建命令
    # ------------------------------------------------------------------ #
    def _builtin_completion(self, argv: list[str]) -> int:
        """``fcmd completion --shell bash|zsh|fish``。

        生成 shell 补全脚本到 stdout，可重定向安装::

            eval "$(fcmd completion --shell bash)"       # bash
            eval "$(fcmd completion --shell zsh)"         # zsh
            fcmd completion --shell fish | source         # fish

        脚本为静态生成（嵌入当前工具/子命令名），新增工具后需重新生成。
        """
        parser = argparse.ArgumentParser(
            prog="fcmd completion",
            description="生成 shell 补全脚本",
        )
        parser.add_argument(
            "--shell",
            choices=("bash", "zsh", "fish"),
            default="bash",
            help="目标 shell（默认 bash）",
        )
        if not argv:
            parser.print_help()
            return 1
        parsed = parser.parse_args(argv)

        # 收集所有工具数据：触发模块导入以填充注册表
        tools_data = self._collect_completion_data()

        if parsed.shell == "bash":
            script = self._gen_bash_script(tools_data)
        elif parsed.shell == "zsh":
            script = self._gen_zsh_script(tools_data)
        else:
            script = self._gen_fish_script(tools_data)
        sys.stdout.write(script)
        return 0

    def _collect_completion_data(self) -> list[dict[str, Any]]:
        """收集全部工具的补全数据：名称、别名、子命令列表。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        # 触发全部工具模块导入
        for _tool_name, module_path in list(_TOOL_MODULES.items()):
            with contextlib.suppress(ImportError):
                importlib.import_module(module_path)

        result: list[dict[str, Any]] = []
        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = self._aliases_for(tool_name)
            subs: list[tuple[str, str]] = []
            registry = _TOOL_REGISTRY.get(tool_name, {})
            for sc, spec in sorted(
                ((sc, spec) for sc, spec in registry.items() if sc is not None and not spec.hidden),
                key=lambda x: str(x[0]),
            ):
                subs.append((str(sc), spec.help or ""))
            result.append({"name": tool_name, "aliases": aliases, "subs": subs})
        return result

    @staticmethod
    def _gen_bash_script(tools_data: list[dict[str, Any]]) -> str:
        """生成 bash 补全脚本。"""
        # 第一层：内建命令 + 工具名 + 别名 + 全局选项
        first_words: list[str] = [*list(_BUILTIN_COMMANDS), "--version", "-V"]
        for tool in tools_data:
            first_words.append(tool["name"])
            first_words.extend(tool["aliases"])
        first_words_str = " ".join(first_words)

        # 每个工具的子命令 case 分支
        case_branches: list[str] = []
        for tool in tools_data:
            if not tool["subs"]:
                continue
            # 工具名 + 别名共用同一组子命令
            names = [tool["name"]] + tool["aliases"]
            pattern = "|".join(names)
            subs_str = " ".join(sc for sc, _ in tool["subs"])
            case_branches.append(
                f'            {pattern})\n                COMPREPLY=($(compgen -W "{subs_str}" -- "$cur")) ;;'
            )
        case_body = "\n".join(case_branches) if case_branches else "            *) ;;"

        return (
            "# fcmd bash 补全脚本\n"
            '# 安装: eval "$(fcmd completion --shell bash)"\n'
            "_fcmd_complete() {\n"
            '    local cur="${COMP_WORDS[COMP_CWORD]}"\n'
            "    if [ $COMP_CWORD -eq 1 ]; then\n"
            f'        COMPREPLY=($(compgen -W "{first_words_str}" -- "$cur"))\n'
            "    elif [ $COMP_CWORD -ge 2 ]; then\n"
            '        local tool="${COMP_WORDS[1]}"\n'
            '        case "$tool" in\n'
            f"{case_body}\n"
            "        esac\n"
            "    fi\n"
            "}\n"
            "complete -F _fcmd_complete fcmd\n"
        )

    @staticmethod
    def _gen_zsh_script(tools_data: list[dict[str, Any]]) -> str:
        """生成 zsh 补全脚本。"""
        # 第一层命令列表
        cmd_lines: list[str] = []
        for cmd in _BUILTIN_COMMANDS:
            cmd_lines.append(f"'{cmd}'")
        for tool in tools_data:
            desc = tool["name"]
            cmd_lines.append(f"'{tool['name']}:{desc}'")
            for alias in tool["aliases"]:
                cmd_lines.append(f"'{alias}:{desc}'")
        cmd_lines.append("'--version:版本号'")
        commands_str = "\n        ".join(cmd_lines)

        # 子命令分支
        sub_blocks: list[str] = []
        for tool in tools_data:
            if not tool["subs"]:
                continue
            names = [tool["name"]] + tool["aliases"]
            pattern = "|".join(names)
            sub_lines = []
            for sc, help_text in tool["subs"]:
                sub_lines.append(f"'{sc}:{help_text}'")
            subs_str = "\n                ".join(sub_lines)
            sub_blocks.append(
                f"            ({pattern})\n"
                f"                local -a subs=({subs_str})\n"
                f"                _describe 'subcommand' subs ;;"
            )
        sub_body = "\n".join(sub_blocks) if sub_blocks else "            (*) ;;"

        return (
            "#compdef fcmd\n"
            "# fcmd zsh 补全脚本\n"
            '# 安装: eval "$(fcmd completion --shell zsh)"\n'
            "_fcmd() {\n"
            "    local -a commands\n"
            "    commands=(\n"
            f"        {commands_str}\n"
            "    )\n"
            "    _arguments -C \\\n"
            "        '1: :->cmd' \\\n"
            "        '*::arg:->args'\n"
            "    case $state in\n"
            "        cmd)\n"
            "            _describe 'command' commands ;;\n"
            "        args)\n"
            "            case ${words[1]} in\n"
            f"{sub_body}\n"
            "            esac ;;\n"
            "    esac\n"
            "}\n"
            '_fcmd "$@"\n'
        )

    @staticmethod
    def _gen_fish_script(tools_data: list[dict[str, Any]]) -> str:
        """生成 fish 补全脚本。"""
        lines: list[str] = ["# fcmd fish 补全脚本", "# 安装: fcmd completion --shell fish | source"]
        # 第一层
        for cmd in _BUILTIN_COMMANDS:
            lines.append(f"complete -c fcmd -f -n '__fish_use_subcommand' -a '{cmd}'")
        for tool in tools_data:
            lines.append(f"complete -c fcmd -f -n '__fish_use_subcommand' -a '{tool['name']}'")
            for alias in tool["aliases"]:
                lines.append(f"complete -c fcmd -f -n '__fish_use_subcommand' -a '{alias}'")
        # 子命令层
        for tool in tools_data:
            if not tool["subs"]:
                continue
            names = [tool["name"]] + tool["aliases"]
            seen_cond = "__fish_seen_subcommand_from " + " ".join(names)
            for sc, help_text in tool["subs"]:
                lines.append(f"complete -c fcmd -f -n '{seen_cond}' -a '{sc}' -d '{help_text}'")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # yaml 内建命令
    # ------------------------------------------------------------------ #
    def _builtin_yaml(self, argv: list[str]) -> int:
        """``fcmd yaml <file> [job] [--dry-run] [--strategy S] [--verbose]``。

        从 YAML 文件加载 GitHub Actions 风格任务图并执行。

        - ``fcmd yaml deploy.yaml``：执行全部 jobs
        - ``fcmd yaml deploy.yaml build``：仅执行 build 及其依赖
        - ``fcmd yaml deploy.yaml --dry-run``：打印执行计划不执行
        - ``fcmd yaml deploy.yaml --strategy thread``：覆盖执行策略
        """
        parser = argparse.ArgumentParser(
            prog="fcmd yaml",
            description="从 YAML 文件加载并执行任务图",
        )
        parser.add_argument("file", help="YAML 文件路径")
        parser.add_argument("job", nargs="?", default=None, help="仅执行该 job 及其依赖（默认全部）")
        parser.add_argument("--dry-run", action="store_true", help="打印执行计划不执行")
        parser.add_argument(
            "--strategy",
            choices=("sequential", "thread", "async", "dependency"),
            default="dependency",
            help="执行策略（默认 dependency）",
        )
        parser.add_argument("--verbose", action="store_true", help="打印详细执行过程")
        if not argv:
            parser.print_help()
            return 1
        parsed = parser.parse_args(argv)

        from fcmd.errors import FcmdError
        from fcmd.executors import run
        from fcmd.yaml_loader import load_yaml

        try:
            graph = load_yaml(parsed.file)
        except (OSError, ValueError) as e:
            get_console().print(f"[red]错误:[/red] 加载 YAML 失败: {e}")
            return 1

        only = [parsed.job] if parsed.job else None
        try:
            report = run(
                graph,
                strategy=parsed.strategy,
                dry_run=parsed.dry_run,
                verbose=parsed.verbose,
                only=only,
            )
        except FcmdError as e:
            get_console().print(f"[red]错误:[/red] {e}")
            return 1

        if report.success:
            get_console().print("[green]YAML 任务图执行成功[/green]")
            return 0
        get_console().print("[red]YAML 任务图执行失败[/red]")
        return 1

    def _list_tools(self) -> None:
        """rich 表格列出所有可用工具。"""
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = get_console()
        console.print(
            Panel(
                Text(f"fcmd v{__version__}", style="bold cyan", justify="center"),
                subtitle="[dim]fcmd <tool> [command] [options][/dim]",
            )
        )

        table = Table(title="可用工具", show_header=True, header_style="bold", show_lines=False)
        table.add_column("命令", style="cyan", no_wrap=True)
        table.add_column("别名", style="dim", no_wrap=True)
        table.add_column("说明")

        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = self._aliases_for(tool_name)
            table.add_row(f"fcmd {tool_name}", ", ".join(aliases), self._tool_description(tool_name))

        console.print(table)

        console.print("\n[bold]示例:[/bold]")
        console.print("  [cyan]fcmd pymake[/cyan]              # 查看 pymake 子命令")
        console.print("  [cyan]fcmd pymake b[/cyan]            # 构建项目")
        console.print("  [cyan]fcmd pymake tc[/cyan]           # 类型检查（聚合）")
        console.print("  [cyan]fcmd info pymake[/cyan]         # 查看 pymake 元信息")
        console.print("  [cyan]fcmd graph pymake tc[/cyan]     # 可视化 DAG（Mermaid）")
        console.print("  [cyan]fcmd --version[/cyan]           # 查看版本")

    def _aliases_for(self, canonical: str) -> list[str]:
        """获取工具的别名（不含规范名本身）。"""
        return sorted(a for a, t in _TOOL_ALIASES.items() if t == canonical and a != canonical)

    def _tool_description(self, tool_name: str) -> str:
        """获取工具描述（从 _TOOL_REGISTRY 中已注册 ToolSpec 的 description/help）。"""
        # 触发模块导入以注册工具
        if tool_name in _TOOL_MODULES:
            try:
                importlib.import_module(_TOOL_MODULES[tool_name])
            except ImportError:
                return ""

        if tool_name not in _TOOL_REGISTRY:
            return ""

        subs = _TOOL_REGISTRY[tool_name]
        for spec in subs.values():
            if spec.description:
                return spec.description
        for spec in subs.values():
            if not spec.hidden and spec.help:
                return spec.help
        return ""

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def _resolve_tool(self, name: str) -> str | None:
        """解析工具名，返回规范名或 None。"""
        return _TOOL_ALIASES.get(name)

    def _print_unknown_tool(self, name: str) -> None:
        """打印未知工具错误 + 模糊匹配建议。"""
        console = get_console()
        console.print(f"[red]错误:[/red] 未知工具 [yellow]{name!r}[/yellow]")
        suggestions = difflib.get_close_matches(name, list(_TOOL_ALIASES), n=3, cutoff=0.5)
        if suggestions:
            console.print(f"[dim]是否想用: {', '.join(suggestions)}[/dim]")
        console.print("[dim]运行 'fcmd' 查看可用工具列表[/dim]")

    def _run_tool(self, tool_name: str, argv: list[str]) -> int:
        """运行工具：importlib 懒加载模块触发 @tool 注册，再调 run_tool。"""
        module_path = _TOOL_MODULES.get(tool_name)
        if module_path is None:
            get_console().print(f"[red]错误:[/red] 工具 {tool_name!r} 无模块映射")
            return 1

        try:
            importlib.import_module(module_path)
        except ImportError as e:
            get_console().print(f"[red]错误:[/red] 加载工具 {tool_name!r} 失败: {e}")
            return 1

        return run_tool(tool_name, argv)


def main() -> None:
    """主入口：解析参数并执行。"""
    sys.exit(FcmdApp().run())


if __name__ == "__main__":
    main()
