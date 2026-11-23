"""filetime - 读取/设置文件时间戳（mtime / atime / ctime）。

与 ``filedate``（文件名日期前缀）不同：本工具改的是文件系统元数据。
``set`` 属于写操作，必须 ``--confirm``；``--dry-run`` 只打印计划。
"""

from __future__ import annotations

import os
from pathlib import Path

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras.fs.timestamps import TimestampError, file_stamps, parse_timestamp

__all__ = ["get", "set"]

_STAMP_KEYS = ("mtime", "atime", "ctime")


def _print_stamps(path: Path) -> None:
    """按 mtime / atime / ctime 三行打印时间戳。"""
    stamps = file_stamps(path)
    print(f"{path}")
    for key in _STAMP_KEYS:
        item = stamps[key]
        print(f"  {key:<5} {item['local']}  (unix={item['unix']}, utc={item['utc']})")


@fx.tool(
    "filetime",
    subcommand="get",
    help="读取文件 mtime/atime/ctime",
    param_help={"paths": "文件路径列表"},
    examples=["fcmd filetime get README.md", "fcmd filetime get README.md --json"],
)
def get(paths: list[Path], json_output: bool = False) -> None:
    """读取文件时间戳（本地时间、UTC 与 Unix 秒）。"""
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        report_error(
            "filetime",
            "get",
            code="FILE_NOT_FOUND",
            message=f"文件不存在: {', '.join(missing)}",
            json_output=json_output,
            field="paths",
            expected="已存在的文件",
            hint="确认路径拼写；目录请改用其他工具",
        )
        return
    if json_output:
        data = [{"path": str(path), **file_stamps(path)} for path in paths]
        emit_result("filetime", "get", {"files": data})
        return
    for index, path in enumerate(paths):
        if index:
            print()
        _print_stamps(path)


@fx.tool(
    "filetime",
    subcommand="set",
    help="设置文件 mtime/atime（需要 --confirm）",
    param_help={
        "path": "目标文件",
        "mtime": "修改时间（必填；ISO 8601 或 @Unix 秒）",
        "atime": "访问时间；省略时与 mtime 相同",
    },
    examples=[
        "fcmd filetime set README.md --mtime 2025-01-02T03:04:05 --dry-run",
        "fcmd filetime set README.md --mtime @1735787045 --confirm",
    ],
    requires_confirmation=True,
)
def set(path: Path, mtime: str = "", atime: str | None = None, json_output: bool = False) -> None:
    """设置文件 mtime（可选 atime），需要 ``--confirm``。"""
    if not path.is_file():
        report_error(
            "filetime",
            "set",
            code="FILE_NOT_FOUND",
            message=f"文件不存在: {path}",
            json_output=json_output,
            field="path",
            expected="已存在的文件",
            hint="确认路径拼写；目录请改用其他工具",
        )
        return
    try:
        mtime_epoch = parse_timestamp(mtime)
    except TimestampError as exc:
        report_error(
            "filetime",
            "set",
            code="INVALID_ARGUMENT",
            message=str(exc) if mtime.strip() else "必须提供 --mtime（ISO 8601 或 @Unix 秒）",
            json_output=json_output,
            field="mtime",
            expected="ISO 8601（如 2025-01-02T03:04:05）或 @Unix 秒",
            hint="示例: --mtime 2025-01-02T03:04:05 或 --mtime @1735787045",
        )
        return
    atime_epoch = mtime_epoch
    if atime is not None:
        try:
            atime_epoch = parse_timestamp(atime)
        except TimestampError as exc:
            report_error(
                "filetime",
                "set",
                code="INVALID_ARGUMENT",
                message=str(exc),
                json_output=json_output,
                field="atime",
                expected="ISO 8601（如 2025-01-02T03:04:05）或 @Unix 秒",
                hint="省略 --atime 时与 mtime 相同",
            )
            return
    try:
        os.utime(path, (atime_epoch, mtime_epoch))
    except OSError as exc:
        report_error(
            "filetime",
            "set",
            code="RUNTIME_ERROR",
            message=f"设置时间戳失败: {exc}",
            json_output=json_output,
            field="path",
            hint="检查文件是否被占用以及当前用户是否有写权限",
        )
        return
    if json_output:
        emit_result("filetime", "set", {"path": str(path), **file_stamps(path)})
        return
    _print_stamps(path)
