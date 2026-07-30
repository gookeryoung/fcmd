"""运行报告：单次 :func:`fcmd.run` 的类型化、可查询结果。

报告是执行后的唯一事实来源。它通过 ``report["name"]`` 暴露单任务结果
（类型为 ``Any``，因为映射异构）、汇总统计，以及整次运行是否成功的标志。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .task import TaskResult, TaskStatus

__all__ = ["RunReport"]


@dataclass
class RunReport:
    """工作流运行的聚合结果。

    属性
    ----
    results:
        任务名 -> :class:`TaskResult` 的映射。插入顺序与任务完成顺序一致。
    success:
        当且仅当所有非跳过任务都以 ``SUCCESS`` 结束时为 ``True``。
    run_id:
        8 字符十六进制运行 ID，自动生成，用于日志追踪与跨系统关联。
    """

    results: dict[str, TaskResult[Any]] = field(default_factory=dict)
    success: bool = True
    run_id: str = field(default_factory=lambda: uuid4().hex[:8])

    # ---- 类型化访问 --------------------------------------------------- #
    def __getitem__(self, name: str) -> Any:
        """返回任务 ``name`` 的*值*（而非 TaskResult）。

        任务不在本次运行中则抛出 ``KeyError``。未达到 SUCCESS 的任务
        返回 ``None``。
        """
        return self.results[name].value

    def result_of(self, name: str) -> TaskResult[Any]:
        """返回 ``name`` 的完整 :class:`TaskResult`。"""
        return self.results[name]

    def __contains__(self, name: object) -> bool:
        return name in self.results

    def __iter__(self) -> Iterator[str]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    # ---- 汇总 --------------------------------------------------------- #
    def summary(self) -> dict[str, Any]:
        """用于日志/仪表盘的紧凑统计字典。"""
        counts: dict[str, int] = {}
        total_duration = 0.0
        for r in self.results.values():
            counts[r.status.value] = counts.get(r.status.value, 0) + 1
            if r.duration is not None:
                total_duration += r.duration
        return {
            "run_id": self.run_id,
            "success": self.success,
            "total_tasks": len(self.results),
            "by_status": counts,
            "total_duration_seconds": round(total_duration, 6),
        }

    def failed_tasks(self) -> list[str]:
        """以 FAILED 状态结束的任务名列表。"""
        return [name for name, r in self.results.items() if r.status == TaskStatus.FAILED]

    def succeeded_tasks(self) -> list[str]:
        """以 SUCCESS 状态结束的任务名列表。"""
        return [name for name, r in self.results.items() if r.status == TaskStatus.SUCCESS]

    def skipped_tasks(self) -> list[str]:
        """以 SKIPPED 状态结束的任务名列表。"""
        return [name for name, r in self.results.items() if r.status == TaskStatus.SKIPPED]

    def tasks_by_status(self, status: TaskStatus) -> list[str]:
        """返回指定状态的任务名列表。"""
        return [name for name, r in self.results.items() if r.status == status]

    def describe(self) -> str:
        """用于调试的人类可读多行报告。"""
        lines: list[str] = [f"RunReport(run_id={self.run_id}, success={self.success})"]
        for name, r in self.results.items():
            dur = f"{r.duration:.3f}s" if r.duration is not None else "-"
            err = f" error={r.error!r}" if r.error else ""
            lines.append(f"  {name}: {r.status.value} ({dur} attempts={r.attempts}){err}")
        return "\n".join(lines)
