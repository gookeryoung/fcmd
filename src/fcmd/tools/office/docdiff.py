"""docdiff - 对比两个 doc/docx 文件（Windows Word）。

交互参考 ``bitool_office.docdiff``：调用本机 Microsoft Word 的
``CompareDocuments`` 生成带修订标记的对比文档。仅在 Windows 可用，需要安装
Word 与 ``pywin32``（``pip install fcmd[win32]``）。

输出规则
--------
- ``output`` 省略：输出到新文档同目录，文件名为 ``<标题>@<时间戳>.docx``；
- ``output`` 是已存在的目录，或不带 ``.doc`` / ``.docx`` 后缀：作为输出目录；
- ``output`` 带 ``.doc`` / ``.docx`` 后缀或指向已存在的文件：作为完整输出路径。

清理
----
与原工具一致，对比结束后会关闭 Word 文档、退出 Word 并强制结束
``WINWORD.EXE``。运行前请保存其他正在编辑的 Word 文档。

示例
----
    fcmd docdiff old.docx new.docx
    fcmd docdiff old.docx new.docx -o 对比结果.docx
    fcmd docdiff old.docx new.docx --title 合同对比 --compare-mode revised --no-show-changes
"""

from __future__ import annotations

import contextlib
import importlib
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

import fcmd
from fcmd.console import print_error

__all__ = [
    "COMPARE_MODES",
    "DEFAULT_TITLE",
    "DOC_EXTENSIONS",
    "TRACK_REVISIONS",
    "WORD_PROCESS_NAME",
    "CompareMode",
    "DocDiffCommand",
    "DocDiffError",
    "compare_with_word",
    "ensure_windows",
    "kill_word_process",
    "normalize_compare_mode",
    "output_filename",
    "resolve_output_path",
    "validate_documents",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "对比两个 doc/docx 文件（Windows + Microsoft Word + pywin32）：调用 Word 的 CompareDocuments "
    "生成带修订标记的对比文档，可设置标题、对比模式与是否显示变更；依赖 fcmd[win32]"
)

CompareMode = Literal["original", "revised"]

COMPARE_MODES: tuple[CompareMode, ...] = ("original", "revised")
DOC_EXTENSIONS = frozenset({".doc", ".docx"})
DEFAULT_TITLE = "对比结果"
WORD_PROCESS_NAME = "WINWORD.EXE"
TRACK_REVISIONS = True


class DocDiffError(RuntimeError):
    """文档对比的业务错误（CLI 直接打印，不抛堆栈）。"""


# ---------------------------------------------------------------------- #
# 参数与路径
# ---------------------------------------------------------------------- #
def normalize_compare_mode(value: str) -> CompareMode:
    """校验并规范化对比模式（``original`` / ``revised``，忽略大小写与首尾空白）。

    Raises
    ------
    ValueError
        取值不在支持列表中。
    """
    mode = value.strip().lower()
    if mode not in COMPARE_MODES:
        raise ValueError(f"对比模式须为 {'/'.join(COMPARE_MODES)}: {value!r}")
    return cast("CompareMode", mode)


def ensure_windows(system: str | None = None) -> None:
    """确认当前平台为 Windows。

    Parameters
    ----------
    system:
        平台名称，默认取 :func:`platform.system`（测试可显式传入）。

    Raises
    ------
    DocDiffError
        非 Windows 平台。
    """
    current = system if system is not None else platform.system()
    if current != "Windows":
        raise DocDiffError("此工具仅在 Windows 平台可用。")


def validate_documents(old_doc: Path, new_doc: Path) -> None:
    """校验两个输入文件存在且扩展名为 ``.doc`` / ``.docx``。

    Raises
    ------
    DocDiffError
        文件不存在或扩展名不受支持。
    """
    for label, path in (("旧文件", old_doc), ("新文件", new_doc)):
        if not path.is_file():
            raise DocDiffError(f"{label}不存在: {path}")
        if path.suffix.lower() not in DOC_EXTENSIONS:
            raise DocDiffError(f"{label}扩展名不受支持（仅 .doc/.docx）: {path}")


def output_filename(title: str = DEFAULT_TITLE, timestamp: datetime | None = None) -> str:
    """生成对比结果文件名：``<标题>@<YYYYmmdd_HH_MM_SS>.docx``。"""
    stamp = timestamp or datetime.now()
    return f"{title}@{stamp.strftime('%Y%m%d_%H_%M_%S')}.docx"


def resolve_output_path(
    new_doc: Path,
    output: Path | None = None,
    *,
    title: str = DEFAULT_TITLE,
    timestamp: datetime | None = None,
) -> Path:
    """解析输出路径：目录 → 目录 + 自动文件名；文件路径 → 原样返回。"""
    filename = output_filename(title, timestamp)
    if output is None:
        return new_doc.parent / filename
    if output.suffix.lower() in DOC_EXTENSIONS or output.is_file():
        return output
    return output / filename


# ---------------------------------------------------------------------- #
# Word 自动化
# ---------------------------------------------------------------------- #
def _load_win32_client() -> Any:
    """惰性导入 ``win32com.client``；未安装时抛 :class:`DocDiffError`。"""
    try:
        return importlib.import_module("win32com.client")
    except ImportError as exc:
        raise DocDiffError("未安装 pywin32 库，请安装: pip install fcmd[win32]") from exc


def _com_initialize() -> Any:
    """在调用线程初始化 COM；未安装 ``pythoncom`` 时返回 ``None``。"""
    try:
        pythoncom = importlib.import_module("pythoncom")
    except ImportError:
        return None
    pythoncom.CoInitialize()
    return pythoncom


def _com_uninitialize(pythoncom: Any | None) -> None:
    """释放 COM（未初始化时为空操作）。"""
    if pythoncom is None:
        return
    with contextlib.suppress(Exception):
        pythoncom.CoUninitialize()


def _start_word(win32: Any) -> Any:
    """启动不可见的 Word 应用并关闭修订跟踪提示。

    Raises
    ------
    DocDiffError
        Word 无法启动（未安装、COM 注册损坏等）。
    """
    try:
        app = win32.gencache.EnsureDispatch("Word.Application")
    except Exception as exc:
        raise DocDiffError(f"启动 Word 失败: {exc}") from exc
    app.Visible = False
    app.DisplayAlerts = False
    with contextlib.suppress(AttributeError):
        app.Options.TrackRevisions = TRACK_REVISIONS
    return app


def _release_word(app: Any | None) -> None:
    """关闭文档、退出 Word 并清理残留进程。"""
    if app is None:
        return
    with contextlib.suppress(Exception):
        app.Documents.Close(SaveChanges=False)
    with contextlib.suppress(Exception):
        app.Quit()
    kill_word_process()


def kill_word_process() -> None:
    """强制结束 ``WINWORD.EXE``（与原工具一致）。"""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["taskkill", "/f", "/t", "/im", WORD_PROCESS_NAME],
            capture_output=True,
            check=False,
        )


def compare_with_word(
    old_doc: Path,
    new_doc: Path,
    output: Path,
    *,
    show_changes: bool = True,
    compare_mode: CompareMode = "original",
) -> Path:
    """调用 Word 对比两个文档并保存结果。

    Parameters
    ----------
    old_doc:
        旧文档路径。
    new_doc:
        新文档路径。
    output:
        对比结果保存路径（父目录自动创建）。
    show_changes:
        是否在结果中显示修订标记。
    compare_mode:
        ``original`` 以原文档为基础，``revised`` 以修订文档为基础。

    Returns
    -------
    Path
        ``output`` 本身。

    Raises
    ------
    DocDiffError
        缺少 pywin32、Word 不可用、对比失败或保存失败。
    """
    win32 = _load_win32_client()
    pythoncom = _com_initialize()
    app: Any | None = None
    try:
        app = _start_word(win32)
        old_data = app.Documents.Open(str(old_doc.resolve()))
        new_data = app.Documents.Open(str(new_doc.resolve()))
        try:
            compared = app.CompareDocuments(
                old_data,
                new_data,
                0,
                2 if compare_mode == "revised" else 0,
                True,
            )
        except Exception as exc:
            raise DocDiffError(f"对比失败: {exc}") from exc
        if not compared:
            raise DocDiffError("对比失败：Word 未返回对比文档")
        compared.ShowRevisions = show_changes
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            compared.SaveAs2(str(output))
        except Exception as exc:
            raise DocDiffError(f"保存对比结果失败: {output}: {exc}") from exc
        return output
    finally:
        _release_word(app)
        _com_uninitialize(pythoncom)


@dataclass(frozen=True)
class DocDiffCommand:
    """一次文档对比：校验输入 → 解析输出 → 调用 Word。"""

    old_doc: Path
    new_doc: Path
    output: Path | None = None
    title: str = DEFAULT_TITLE
    show_changes: bool = True
    compare_mode: CompareMode = "original"

    def resolve_output(self, timestamp: datetime | None = None) -> Path:
        """按当前设置解析对比结果路径。"""
        return resolve_output_path(self.new_doc, self.output, title=self.title, timestamp=timestamp)

    def run(self) -> Path:
        """执行对比并返回结果路径。"""
        ensure_windows()
        validate_documents(self.old_doc, self.new_doc)
        target = self.resolve_output()
        return compare_with_word(
            self.old_doc,
            self.new_doc,
            target,
            show_changes=self.show_changes,
            compare_mode=self.compare_mode,
        )


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
@fcmd.tool("docdiff", help="对比两个 doc/docx 文件（Windows Word）", description=_DESCRIPTION)
def compare_docs(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    old_doc: Path,
    new_doc: Path,
    output: Path | None = None,
    title: str = DEFAULT_TITLE,
    show_changes: bool = True,
    compare_mode: CompareMode = "original",
) -> None:
    """对比两个 Word 文档并保存对比结果。

    Parameters
    ----------
    old_doc:
        旧文档路径（.doc / .docx）。
    new_doc:
        新文档路径（.doc / .docx）。
    output:
        对比结果输出路径；省略时输出到新文档同目录，自动命名 ``<标题>@<时间戳>.docx``。
    title:
        对比结果标题，用于自动生成文件名。
    show_changes:
        默认在结果中显示修订标记（使用 ``--no-show-changes`` 关闭）。
    compare_mode:
        对比模式：original 以原文档为基础，revised 以修订文档为基础。
    """
    command = DocDiffCommand(
        old_doc,
        new_doc,
        output,
        title=title,
        show_changes=show_changes,
        compare_mode=compare_mode,
    )
    try:
        target = command.run()
    except DocDiffError as exc:
        print_error(str(exc))
        return
    print(f"对比完成。已保存至: {target}")


@fcmd.main("docdiff")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
