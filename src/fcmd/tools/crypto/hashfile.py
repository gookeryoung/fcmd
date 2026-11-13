"""hashfile 工具：单文件与目录下所有文件的哈希。

哈希计算与目录过滤在 :mod:`fcmd.infras.hashing`，本模块只做参数适配与输出。
默认输出保持 ``算法  摘要  路径`` 的人类可读形态；``--json`` 为增量开关。
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.core.result import emit_error, emit_result
from fcmd.infras import dupes, hashing
from fcmd.infras.system import format_bytes


@fx.tool(
    "hashfile",
    subcommand="f",
    help="计算单个文件哈希",
    param_help={
        "path": "目标文件路径",
        "algorithm": "哈希算法（md5/sha256/sha1/sha512，默认 sha256）",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd hashfile f README.md",
        "fcmd hashfile f README.md --algorithm sha512",
        "fcmd hashfile f README.md --json",
    ],
)
def hashfile_f(path: Path, algorithm: str = "sha256", json_output: bool = False) -> None:
    """计算并打印单个文件的哈希值。"""
    try:
        hashing.ensure_algorithm(algorithm)
    except ValueError as exc:
        if json_output:
            emit_error(
                "hashfile",
                "f",
                code="INVALID_ALGORITHM",
                message=str(exc),
                field="algorithm",
                expected="、".join(sorted(hashing.FILE_ALGORITHMS)),
                hint="从 expected 中选一个算法后重试",
            )
            return
        raise
    if not path.is_file():
        if json_output:
            emit_error(
                "hashfile",
                "f",
                code="FILE_NOT_FOUND",
                message=f"文件不存在: {path}",
                field="path",
                expected="已存在的文件",
                hint="确认路径存在且是文件",
            )
            return
        print(f"文件不存在: {path}")
        return
    digest = hashing.compute_file_hash(path, algorithm)
    if json_output:
        emit_result("hashfile", "f", {"algorithm": algorithm, "digest": digest, "path": str(path)})
        return
    print(f"{algorithm}  {digest}  {path}")


@fx.tool(
    "hashfile",
    subcommand="d",
    help="计算目录下所有文件哈希",
    param_help={
        "directory": "目标目录路径",
        "algorithm": "哈希算法（md5/sha256/sha1/sha512，默认 sha256）",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd hashfile d src",
        "fcmd hashfile d src --algorithm md5",
        "fcmd hashfile d src --json",
    ],
)
def hashfile_d(directory: Path, algorithm: str = "sha256", json_output: bool = False) -> None:
    """计算目录下所有文件的哈希值并打印。

    跳过常见忽略目录（.git/__pycache__ 等）与 .pyc/.pyo 文件。
    """
    try:
        hashing.ensure_algorithm(algorithm)
    except ValueError as exc:
        if json_output:
            emit_error(
                "hashfile",
                "d",
                code="INVALID_ALGORITHM",
                message=str(exc),
                field="algorithm",
                expected="、".join(sorted(hashing.FILE_ALGORITHMS)),
                hint="从 expected 中选一个算法后重试",
            )
            return
        raise
    if not directory.is_dir():
        if json_output:
            emit_error(
                "hashfile",
                "d",
                code="DIR_NOT_FOUND",
                message=f"目录不存在: {directory}",
                field="directory",
                expected="已存在的目录",
                hint="确认路径存在且是目录",
            )
            return
        print(f"目录不存在: {directory}")
        return
    entries = [
        {"path": str(file_path), "digest": hashing.compute_file_hash(file_path, algorithm)}
        for file_path in hashing.iter_directory_files(directory)
    ]
    if json_output:
        emit_result("hashfile", "d", {"algorithm": algorithm, "files": entries, "total": len(entries)})
        return
    for entry in entries:
        print(f"{algorithm}  {entry['digest']}  {entry['path']}")


@fx.tool(
    "hashfile",
    subcommand="dupes",
    help="查找目录下内容重复的文件（只读，不删除/移动）",
    param_help={
        "directory": "要扫描的目录",
        "algorithm": "用于确认内容相同的哈希算法（md5/sha256/sha1/sha512，默认 sha256）",
        "min_size": "只统计不小于该字节数的文件（默认 1，跳过空文件）",
        "limit": "最多显示多少组（0=全部）",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd hashfile dupes .",
        "fcmd hashfile dupes D:\\photos --min-size 4096",
        "fcmd hashfile dupes . --limit 5",
        "fcmd hashfile dupes . --json",
    ],
)
def hashfile_dupes(
    directory: Path,
    algorithm: str = "sha256",
    min_size: int = 1,
    limit: int = 0,
    json_output: bool = False,
) -> None:
    """查找目录下内容重复的文件（大小预筛 + 哈希确认）。

    每组第一项为默认保留项，其余为可回收副本；只读，不删除也不移动文件。
    """
    try:
        hashing.ensure_algorithm(algorithm)
    except ValueError as exc:
        if json_output:
            emit_error(
                "hashfile",
                "dupes",
                code="INVALID_ALGORITHM",
                message=str(exc),
                field="algorithm",
                expected="、".join(sorted(hashing.FILE_ALGORITHMS)),
                hint="从 expected 中选一个算法后重试",
            )
            return
        raise
    if min_size < 0:
        if json_output:
            emit_error(
                "hashfile",
                "dupes",
                code="INVALID_MIN_SIZE",
                message="最小大小不能为负数",
                field="min_size",
                expected=">= 0 的整数字节数",
                hint="改用 0 或正整数字节数；0 表示包含空文件",
            )
            return
        raise ValueError("最小大小不能为负数")
    if not directory.is_dir():
        if json_output:
            emit_error(
                "hashfile",
                "dupes",
                code="DIR_NOT_FOUND",
                message=f"目录不存在: {directory}",
                field="directory",
                expected="已存在的目录",
                hint="确认路径存在且是目录",
            )
            return
        print(f"目录不存在: {directory}")
        return
    report = dupes.find_duplicates([directory], algorithm=algorithm, min_size=min_size)
    if json_output:
        data = dupes.report_to_mapping(report, limit=limit)
        emit_result("hashfile", "dupes", {"algorithm": algorithm, **data})
        return
    if not report.groups:
        print("（无重复文件）")
        return
    shown = report.groups if limit <= 0 else report.groups[:limit]
    print(
        f"重复文件: {len(report.groups)} 组，可回收 {format_bytes(report.wasted)}"
        f"（扫描 {report.scanned} 个文件，算法 {algorithm}）"
    )
    for index, group in enumerate(shown, start=1):
        print(f"\n[{index}] {format_bytes(group.size)}  {algorithm}:{group.digest[:12]}  保留: {group.paths[0]}")
        for path in group.paths[1:]:
            print(f"    重复: {path}")
    if len(shown) < len(report.groups):
        print(f"\n（共 {len(report.groups)} 组，已显示前 {len(shown)} 组；--limit 0 查看全部）")
