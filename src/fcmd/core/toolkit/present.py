"""展示适配：把任务事件 / 错误翻译成人读行（rich 只在这里出现）。"""

from __future__ import annotations

from collections.abc import Mapping
from subprocess import CalledProcessError
from typing import Any

from ..result import emit_error, has_pending_error
from ..task import TaskStatus
from .spec import ToolSpec


def _describe_exception(exc: BaseException) -> str:
    """把任务异常转成一句人能看懂的话。

    - 工具自己抛的 :class:`RuntimeError` 已是中文句子（如「命令执行失败: …（返回码 1）」），
      直接透传；
    - ``subprocess`` 的 :class:`~subprocess.CalledProcessError` 转成命令行 + 返回码，
      不再把 Python 的 ``repr(list)`` 丢给用户；
    - 其他异常保留类型名，方便反馈时定位。
    """
    if isinstance(exc, SystemExit):
        return f"工具进程以退出码 {exc.code} 结束"
    text = str(exc).strip()
    if isinstance(exc, CalledProcessError):
        cmd = exc.cmd if isinstance(exc.cmd, str) else " ".join(str(part) for part in exc.cmd or ())
        return f"命令执行失败: {cmd}（返回码 {exc.returncode}）"
    if isinstance(exc, RuntimeError) and text:
        return text
    return f"{type(exc).__name__}: {exc}"


def _report_tool_failure(  # noqa: PLR0913 - 与错误信封字段一一对应
    name: str,
    target: str | None,
    *,
    code: str,
    message: str,
    json_output: bool,
    hint: str | None = None,
) -> None:
    """呈现工具级失败：已呈现过则跳过，避免同一次调用输出两条错误。

    ``--json`` 走 stderr 错误信封，默认路径走 ``错误: …`` 行；两条路径都由
    这里决定，保证不会出现「退出 1 却没有任何错误文本」。
    """
    if has_pending_error():
        return
    if json_output:
        emit_error(name, target, code=code, message=message, hint=hint)
        return
    from fcmd.console import print_error

    print_error(message, code=code, hint=hint)


def _display_label(spec: ToolSpec) -> str:
    """子命令的用户可见动作名。

    优先用子命令的 ``help``（已是中文动词短语），去掉靠后的「（内部）」标记；
    两者都缺失时回退到子命令名 / 工具名。展示层不再出现 ``'_commit'`` 这类
    内部标识，也不使用 ``repr`` 的引号。
    """
    text = (spec.help or "").strip().rstrip("。")
    for suffix in ("（内部）", "(内部)"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text or str(spec.subcommand or spec.name)


def _make_progress_printer(labels: Mapping[str, str], noop_tasks: frozenset[str]) -> Any:
    """构造进度回调：把任务事件翻译成用户可读的步骤行。

    - ``noop_tasks``（纯聚合壳任务，如 ``gitt a``）不单独占一行：用户只关心
      真正干活的那几步；
    - 不打印开始行，成功路径只留一行结果，减少噪音；
    - 任务已在函数体内通过 ``print_error`` 报错时不再补「完成」，避免矛盾。
    """
    from fcmd.console import print_step_done, print_step_failed, print_step_skipped
    from fcmd.core.result import has_pending_error

    def _printer(event: Any) -> None:
        if event.status == TaskStatus.SUCCESS:
            if event.task in noop_tasks or has_pending_error():
                return
            print_step_done(labels.get(event.task, event.task), event.duration)
        elif event.status == TaskStatus.FAILED:
            print_step_failed(labels.get(event.task, event.task), event.duration, attempts=event.attempts)
        elif event.status == TaskStatus.SKIPPED:
            print_step_skipped(labels.get(event.task, event.task), event.reason or "")

    return _printer


def _print_task_summary(
    report: Any,
    labels: Mapping[str, str] | None = None,
    noop_tasks: frozenset[str] = frozenset(),
) -> None:
    """多步执行成功后的收尾行：``共 3 步，用时 1.61s``。

    单步不打印（上面那行「完成 …」已说明结果）；纯聚合壳任务（如 ``gitt a``）
    不计入步数——它们是编排占位，不是用户眼里的“一步”。

    Parameters
    ----------
    report:
        本次运行报告。
    labels:
        任务名 → 用户可读动作名；缺失时回退到任务名（保持向后兼容）。
    noop_tasks:
        不计入步数的纯聚合任务名。
    """
    from fcmd.console import print_run_summary

    del labels  # 步骤行已带动作名，收尾行只给步数与耗时
    steps = [r for name, r in report.results.items() if name not in noop_tasks]
    if len(steps) <= 1:
        return
    total = sum(r.duration for r in steps if r.duration is not None)
    print_run_summary(len(steps), total)


def _print_subcommands(name: str, subs: dict[str | None, ToolSpec]) -> None:
    """打印工具的所有非 hidden 子命令。

    Parameters
    ----------
    subs:
        工具的子命令字典，由调用方从 ``_TOOL_REGISTRY`` 取出后传入
    """
    from fcmd.console import Table, get_console

    console = get_console()
    visible = [(sc, spec) for sc, spec in subs.items() if sc is not None and not spec.hidden]
    if not visible:
        console.print(f"[dim]工具 {name!r} 无可见子命令[/dim]")
        return
    table = Table(title=f"{name} 子命令", show_header=True, header_style="bold")
    table.add_column("子命令", style="cyan", no_wrap=True)
    table.add_column("说明")
    for sc, spec in sorted(visible, key=lambda x: str(x[0])):
        table.add_row(str(sc), spec.help or "")
    console.print(table)
