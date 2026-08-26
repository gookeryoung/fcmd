"""``fcmd profiler`` 内建命令：分析脚本的工作流执行性能。"""

from __future__ import annotations

import argparse
from pathlib import Path

from fcmd.cli._profiler_helpers import inject_run_hook, output_profile, run_target_script
from fcmd.console import get_console

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd profiler <script.py> [args] [-E html|text] [-o FILE] [--no-browser]``。

    分析包含 ``fx.run()`` 调用的 Python 脚本，生成工作流执行性能剖面报告。

    工作原理：
    1. 注入 hook 捕获 ``fcmd.run()`` 调用的 ``Graph`` 与 ``RunReport``
    2. 用 ``runpy`` 以 ``__main__`` 身份执行目标脚本
    3. 从捕获的 report + graph 构建 :class:`ProfileReport`，输出 HTML 或文本

    示例::

        fcmd profiler workflow.py              # 生成 HTML 并打开浏览器
        fcmd profiler workflow.py -- t         # 传参 t 给脚本
        fcmd profiler workflow.py -E text      # 输出纯文本到 stdout
        fcmd profiler workflow.py -o rep.html  # 指定输出文件
        fcmd profiler workflow.py --no-browser # 不打开浏览器
    """
    parser = argparse.ArgumentParser(
        prog="fcmd profiler",
        description="分析包含 fcmd.run() 调用的脚本，生成性能剖面报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  fcmd profiler workflow.py              # 生成 HTML 并打开浏览器\n"
            "  fcmd profiler workflow.py -- t         # 传参 t 给脚本\n"
            "  fcmd profiler workflow.py -E text      # 输出纯文本到 stdout\n"
            "  fcmd profiler workflow.py -o rep.html  # 指定输出文件\n"
        ),
    )
    parser.add_argument("script", help="要分析的 Python 脚本路径")
    parser.add_argument(
        "-E",
        "--export",
        choices=("html", "text"),
        default="html",
        help="导出格式（默认 html）",
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器（仅 HTML 有效）")
    parser.add_argument("-o", "--output", help="输出文件路径（默认 <script>_profile.html）")

    if not argv:
        parser.print_help()
        return 1

    parsed, script_args = parser.parse_known_args(argv)
    # ``--`` 之后的参数全部传给脚本（argparse 已消费 ``--``，此处显式重取确保正确）
    if "--" in argv:
        sep_idx = argv.index("--")
        script_args = list(argv[sep_idx + 1 :])

    script_path = Path(parsed.script).resolve()
    if not script_path.is_file():
        get_console().print(f"[red]错误:[/red] 脚本不存在: {script_path}")
        return 2

    # 注入 hook 捕获 run() 调用
    captured = inject_run_hook()

    get_console().print(f"[bold]正在分析:[/bold] {script_path}")
    if script_args:
        get_console().print(f"[dim]脚本参数:[/dim] {script_args}")
    get_console().print("[dim]" + "-" * 60 + "[/dim]")

    # 执行目标脚本
    try:
        run_target_script(script_path, script_args)
    except SystemExit:
        # 脚本调用了 sys.exit，属正常情况
        pass
    except Exception as e:
        # 用户脚本可抛任意异常，宽捕获防止单个脚本失败影响主流程
        get_console().print(f"[yellow]警告:[/yellow] 脚本执行抛出异常: {e}")

    # 还原 hook
    captured["_restore"]()

    report = captured.get("report")
    graph = captured.get("graph")
    if report is None or graph is None:
        get_console().print("[red]错误:[/red] 未捕获到 fcmd.run() 调用，无法生成性能报告")
        get_console().print("[dim]请确保脚本通过 fcmd.run() 执行任务流图[/dim]")
        return 1

    # 生成报告
    from fcmd.apis.profiling import ProfileReport

    profile = ProfileReport.from_report(report, graph)
    output_profile(
        profile,
        export=parsed.export,
        output=parsed.output,
        script_stem=script_path.stem,
        no_browser=parsed.no_browser,
    )
    return 0
