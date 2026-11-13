"""peview - PE 文件查看（CLI）。

用标准库解析 PE，不依赖 ``pefile``：``info`` 看概览，``headers`` / ``sections`` /
``directories`` 看结构，``imports`` / ``exports`` / ``resources`` / ``relocations`` /
``debug`` 看各目录，``hex`` 看十六进制，``check`` 做一致性检查，``fields`` 列出
可编辑字段。编辑入口在 ``edit`` 子命令（默认只预演，需 ``--apply`` 才写入）。

示例
----
    fcmd peview info app.exe
    fcmd peview sections app.exe
    fcmd peview hex app.exe --offset 0x1000 --length 128
    fcmd peview fields app.exe
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fcmd
from fcmd.apps.peview import _edit, _report
from fcmd.apps.peview._pe import PEImage, load_pe
from fcmd.apps.peview._reader import DEFAULT_HEX_LIMIT, PEError, format_hex_dump
from fcmd.core.result import emit_result, report_error

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "check",
    "debug",
    "directories",
    "edit",
    "exports",
    "fields",
    "headers",
    "hexdump",
    "imports",
    "info",
    "relocations",
    "resources",
    "sections",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PE 文件查看（DOS/NT/可选头、节表、数据目录、导入导出、资源、重定位、签名与 overlay，"
    "含十六进制视图和一致性检查）；编辑需显式 --apply 且写入前自动备份"
)


def _run(file: Path | None, action: Callable[[], None]) -> None:
    """执行一个子命令动作，把 :class:`PEError` 转成中文错误输出（非零退出）。"""
    try:
        action()
    except PEError as exc:
        _report_pe_error(exc, file, subcommand=None, json_output=False)


def _report_pe_error(exc: PEError, file: Path | None, *, subcommand: str | None, json_output: bool) -> None:
    """按「文件不存在 / 不是 PE」两类呈现 PEError，两条路径同一退出码。"""
    not_found = file is not None and not file.is_file()
    report_error(
        "peview",
        subcommand,
        code="FILE_NOT_FOUND" if not_found else "NOT_PE_FILE",
        message=str(exc),
        field="file",
        expected="已存在的 PE 文件" if not_found else "PE 文件（带 MZ / PE 签名）",
        hint="确认路径存在且是文件" if not_found else "确认文件是有效的 exe/dll/sys 等 PE 映像",
        json_output=json_output,
    )


def _load_for_output(file: Path, subcommand: str, json_output: bool) -> PEImage | None:
    """加载 PE；失败时按当前输出形态呈现错误并返回 ``None``（退出码非零）。"""
    try:
        return load_pe(file)
    except PEError as exc:
        _report_pe_error(exc, file, subcommand=subcommand, json_output=json_output)
        return None


@fcmd.tool(
    "peview",
    subcommand="info",
    help="查看 PE 文件概览",
    param_help={
        "file": "PE 文件路径（exe/dll/sys 等）",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd peview info app.exe",
        "fcmd peview info app.exe --json",
    ],
)
def info(file: Path, json_output: bool = False) -> None:
    """输出文件类型、架构、入口点、目录数量、签名与 overlay 等概览。

    Parameters
    ----------
    file:
        PE 文件路径（exe/dll/sys 等）
    json_output:
        以 JSON 输出（``--json`` 同义）
    """
    pe = _load_for_output(file, "info", json_output)
    if pe is None:
        return
    if json_output:
        emit_result("peview", "info", _report.summary_data(pe))
        return
    print(_report.format_summary(pe))


@fcmd.tool("peview", subcommand="headers", help="查看 DOS / NT / 可选头")
def headers(file: Path) -> None:
    """输出 DOS 头、COFF 文件头与可选头字段。

    Parameters
    ----------
    file:
        PE 文件路径
    """
    _run(file, lambda: print(_report.format_headers(load_pe(file))))


@fcmd.tool(
    "peview",
    subcommand="sections",
    help="查看节表",
    param_help={
        "file": "PE 文件路径",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd peview sections app.exe",
        "fcmd peview sections app.exe --json",
    ],
)
def sections(file: Path, json_output: bool = False) -> None:
    """输出节名称、虚拟 / 原始大小、地址与属性解读。

    Parameters
    ----------
    file:
        PE 文件路径
    json_output:
        以 JSON 输出（``--json`` 同义）
    """
    pe = _load_for_output(file, "sections", json_output)
    if pe is None:
        return
    if json_output:
        emit_result("peview", "sections", _report.sections_data(pe))
        return
    print(_report.format_sections(pe))


@fcmd.tool("peview", subcommand="directories", help="查看数据目录")
def directories(file: Path) -> None:
    """输出 16 项数据目录的 RVA / 大小 / 表项偏移。

    Parameters
    ----------
    file:
        PE 文件路径
    """
    _run(file, lambda: print(_report.format_directories(load_pe(file))))


@fcmd.tool(
    "peview",
    subcommand="imports",
    help="查看导入与延迟导入",
    param_help={
        "file": "PE 文件路径",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd peview imports app.exe",
        "fcmd peview imports app.exe --json",
    ],
)
def imports(file: Path, json_output: bool = False) -> None:
    """输出导入库与符号（按序号或名称）。

    Parameters
    ----------
    file:
        PE 文件路径
    json_output:
        以 JSON 输出（``--json`` 同义）
    """
    pe = _load_for_output(file, "imports", json_output)
    if pe is None:
        return
    if json_output:
        emit_result("peview", "imports", _report.imports_data(pe))
        return
    print(_report.format_imports(pe))


@fcmd.tool("peview", subcommand="exports", help="查看导出表")
def exports(file: Path) -> None:
    """输出导出 DLL 名、基序号、函数与转发器。

    Parameters
    ----------
    file:
        PE 文件路径
    """
    _run(file, lambda: print(_report.format_exports(load_pe(file))))


@fcmd.tool("peview", subcommand="resources", help="查看资源树")
def resources(file: Path) -> None:
    """输出资源类型 / 名称 / 语言 / 数据位置。

    Parameters
    ----------
    file:
        PE 文件路径
    """
    _run(file, lambda: print(_report.format_resources(load_pe(file))))


@fcmd.tool("peview", subcommand="relocations", help="查看基址重定位")
def relocations(file: Path) -> None:
    """输出重定位块与条目。

    Parameters
    ----------
    file:
        PE 文件路径
    """
    _run(file, lambda: print(_report.format_relocations(load_pe(file))))


@fcmd.tool("peview", subcommand="debug", help="查看调试目录")
def debug(file: Path) -> None:
    """输出调试目录项的类型、大小与数据位置。

    Parameters
    ----------
    file:
        PE 文件路径
    """
    _run(file, lambda: print(_report.format_debug(load_pe(file))))


@fcmd.tool("peview", subcommand="hex", help="查看文件十六进制")
def hexdump(file: Path, offset: int = 0, length: int = DEFAULT_HEX_LIMIT) -> None:
    """输出从 ``offset`` 开始的十六进制窗口。

    Parameters
    ----------
    file:
        PE 文件路径
    offset:
        起始文件偏移，默认 0
    length:
        展示字节数，默认 256
    """
    _run(file, lambda: _print_hex(file, offset, length))


def _print_hex(file: Path, offset: int, length: int) -> None:
    pe = load_pe(file)
    if offset < 0 or offset > pe.size:
        raise PEError(f"偏移 0x{offset:X} 超出文件大小 0x{pe.size:X}")
    if length < 0:
        raise PEError(f"长度不能为负：{length}")
    print(format_hex_dump(pe.data[offset : offset + length], offset=offset))


@fcmd.tool(
    "peview",
    subcommand="check",
    help="检查结构与校验和",
    param_help={
        "file": "PE 文件路径",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd peview check app.exe",
        "fcmd peview check app.exe --json",
    ],
)
def check(file: Path, json_output: bool = False) -> None:
    """输出校验和、对齐、入口点、签名与 overlay 的检查结论。

    Parameters
    ----------
    file:
        PE 文件路径
    json_output:
        以 JSON 输出（``--json`` 同义）
    """
    pe = _load_for_output(file, "check", json_output)
    if pe is None:
        return
    if json_output:
        emit_result("peview", "check", _report.check_data(pe))
        return
    print(_report.format_check(pe))


@fcmd.tool("peview", subcommand="fields", help="列出可编辑字段")
def fields(file: Path) -> None:
    """列出允许编辑的字段、文件偏移、宽度与当前值。

    Parameters
    ----------
    file:
        PE 文件路径
    """
    _run(file, lambda: print(_report.format_fields(load_pe(file))))


@fcmd.tool("peview", subcommand="edit", help="编辑允许的头字段（默认只预演）")
def edit(
    file: Path,
    assign: list[str] | None = None,
    apply: bool = False,
    allow_signature_break: bool = False,
    recompute_checksum: bool = True,
) -> None:
    """按 ``NAME=VALUE`` 修改白名单字段；默认只预演，``--apply`` 才写入。

    写入前自动创建 ``<文件名>.backup[N]`` 备份，写入后重新解析校验结构；
    结构性字段（``e_lfanew`` / 节地址与大小 / 数据目录等）一律拒绝。

    Parameters
    ----------
    file:
        PE 文件路径
    assign:
        字段赋值列表，如 ``entry_point=0x2000 subsystem=3``；字段名可用
        ``fcmd peview fields`` 查看
    apply:
        真正写入（默认只打印「旧值 → 新值」预演）
    allow_signature_break:
        文件带 Authenticode 签名时仍允许写入（写入会使签名失效）
    recompute_checksum:
        按修改后的内容重算校验和（默认开启；加 ``--no-recompute-checksum`` 关闭）
    """
    _run(file, lambda: _run_edit(file, assign or [], apply, allow_signature_break, recompute_checksum))


def _run_edit(
    file: Path,
    assign: list[str],
    apply: bool,
    allow_signature_break: bool,
    recompute_checksum: bool,
) -> None:
    edits = _edit.parse_assignments(assign)
    result = _edit.apply_edits(
        file,
        edits,
        apply=apply,
        allow_signature_break=allow_signature_break,
        recompute_checksum=recompute_checksum,
    )
    rows = [(change.name, change.label, f"0x{change.old:X}", f"0x{change.new:X}") for change in result.changes]
    print(_report.format_table(("字段", "说明", "原值", "新值"), rows))
    print(f"校验和: 0x{result.checksum:08X}")
    if result.signature_break:
        print("注意: 文件带 Authenticode 签名，本次写入已使签名失效")
    if result.problems:
        print("结构告警:")
        for problem in result.problems:
            print(f"  - {problem}")
    if result.dry_run:
        print("预演完成：未修改文件；确认后加 --apply 写入")
    else:
        print(f"已写入: {result.path}")
        print(f"备份: {result.backup}")


@fcmd.main("peview")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
