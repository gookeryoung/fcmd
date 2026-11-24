"""``tomltool convert`` / ``yamtool convert`` 共用的结果落地逻辑。"""

from __future__ import annotations

from pathlib import Path

from fcmd.core.result import report_error

__all__ = ["write_or_print"]


def write_or_print(tool: str, subcommand: str, content: str, output: Path | None) -> None:
    """把转换结果写到 ``output``（父目录自动创建）或标准输出。"""
    if output is None:
        print(content, end="" if content.endswith("\n") else "\n")
        return
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    except OSError as exc:
        report_error(
            tool,
            subcommand,
            code="RUNTIME_ERROR",
            message=f"写入目标文件失败: {exc}",
            field="output",
            expected="可写的文件路径",
            json_output=False,
        )
        return
    print(f"已写入: {output}")
