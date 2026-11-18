"""工具 console script 通用入口。

pyproject ``[project.scripts]`` 中多子命令工具的入口统一指向本模块
:func:`run_named`（如 ``clr = "fcmd.cli._entry:run_named"``）：pip 生成的
console script 可执行文件名即 pyproject 键名，故从 ``sys.argv[0]`` 的
basename 可可靠推断工具名。

已知边界：直接执行 pip 生成的 ``*-script.py`` 包装脚本会得到带后缀的
``*-script`` 名（标准安装走 exe/软链入口，不受影响）。
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["infer_tool_name", "run_named"]


def infer_tool_name(argv0: str) -> str:
    """从 argv[0] 推断工具名：取 basename 去可执行扩展。

    先统一反斜杠为正斜杠再解析，确保 POSIX 系统（CI）也能正确处理
    Windows 风格路径；正斜杠路径不受影响。

    Examples
    --------
    ``C:\\Scripts\\clr.exe`` / ``/usr/local/bin/clr`` / ``clr`` → ``"clr"``
    """
    return Path(argv0.replace("\\", "/")).stem


def run_named() -> None:
    """console script 通用入口：按 argv[0] 推断工具名并转发执行。

    工具名优先按生成清单只加载目标工具（:func:`fcmd.cli._discovery.resolve_tool_fast`），
    与 ``fcmd <tool>`` 走同一快路径；清单未命中 / 过期时才回退全量发现。
    工具级 ``--help`` 与 ``fcmd <tool> --help`` 一样优先输出固化文本
    （见 :func:`fcmd.cli._manifest.help_text`），不导入工具模块。
    """
    args = sys.argv[1:]
    name = infer_tool_name(sys.argv[0])
    if args[:1] in (["-h"], ["--help"]):
        # 先试 argv[0]，再退回加载器 exe 名（runpy 入口会改写 argv[0]）
        from fcmd.cli._manifest import help_text

        text = help_text(name, args) or help_text(infer_tool_name(sys.executable), args)
        if text is not None:
            sys.stdout.write(text)
            sys.stdout.flush()
            sys.exit(0)

    # 懒导入避免 cli 包级循环（_discovery 函数内懒导入本包）
    from fcmd.cli._discovery import ensure_tools_discovered, resolve_tool, resolve_tool_fast
    from fcmd.console import print_error
    from fcmd.core.exit_codes import ExitCode
    from fcmd.core.toolkit import run_tool

    resolved = resolve_tool_fast(name)
    if resolved is None:
        # runpy 入口（fspack 打包包装器以 run_module(alter_sys=True) 执行本模块）
        # 会把 argv[0] 改写为入口模块文件路径而非加载器 exe 名；此时退回从
        # sys.executable（即 per-tool loader exe，如 img2ico.exe）推断工具名。
        resolved = resolve_tool_fast(infer_tool_name(sys.executable))
    if resolved is None:
        # 清单不可用 / 过期：回退全量发现，与 fcmd <tool> 的回退路径一致
        ensure_tools_discovered()
        resolved = resolve_tool(name)
        if resolved is None:
            resolved = resolve_tool(infer_tool_name(sys.executable))
    if resolved is None:
        print_error(
            f"入口 {name!r} 未对应任何已注册工具",
            code="UNKNOWN_TOOL",
            hint="运行 'fcmd' 查看可用工具列表",
        )
        sys.exit(int(ExitCode.USAGE))
    sys.exit(run_tool(resolved, sys.argv[1:]))


if __name__ == "__main__":
    # fspack 打包包装器经 runpy.run_module 执行本模块（无函数调用上下文），
    # 与 fcmd.cli.main 同样需要 __main__ 守卫触发入口函数。
    run_named()
