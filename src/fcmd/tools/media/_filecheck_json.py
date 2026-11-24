"""filecheck 的机读结果构造（从 filecheck.py 拆出以控制模块行数）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fcmd.tools.media.filecheck import ScanResult


def json_payload(result: ScanResult) -> dict[str, Any]:
    """把扫描结果转为机读字典（不写入报告文件）。"""
    errors = [item for item in result.issues if item.level == "错误"]
    warns = [item for item in result.issues if item.level == "警告"]
    notes = [item for item in result.issues if item.level == "提示"]
    return {
        "root": str(result.root),
        "all_files": result.all_files,
        "chunk_kb": result.chunk_kb,
        "stats": result.stats,
        "summary": {
            "checked": int(result.stats.get("checked", 0)),
            "errors": len(errors),
            "warnings": len(warns),
            "notes": len(notes),
            "dir_errors": len(result.dir_errors),
            "missing_groups": len(result.missing),
        },
        "issues": [{"level": item.level, "path": item.path, "message": item.message} for item in result.issues],
        "missing": [
            {
                "dirpath": group.dirpath,
                "prefix": group.prefix,
                "season": group.season,
                "found": group.found,
                "lo": group.lo,
                "hi": group.hi,
                "missing": list(group.missing),
            }
            for group in result.missing
        ],
        "dir_errors": result.dir_errors,
    }
