"""filescan 文档文本抽取：docx / xlsx / pptx / odt / ods / pdf。

设计要点：

- 前五种格式本质都是 zip + XML，用 ``zipfile`` 打开、``xml.etree.iterparse``
  流式抽取，不引入第三方依赖；
- PDF 走可选依赖 ``pypdf``（``pip install "fcmd[pdf]"``），按 ``find_spec``
  探测 + 函数内延迟导入，缺失时抛 :data:`KIND_UNSUPPORTED` 并带安装提示；
- 抽取结果是一组 :class:`TextUnit`（文本 + 可读位置），位置用于把命中回溯到
  段落、工作表单元格、幻灯片、页码等，纯文本路径不经过本模块；
- 三重上限防解压炸弹：压缩包成员数、单部件解压大小、全部部件解压总量；PDF
  另设页数与累计文本长度上限；
- 失败按 :data:`KIND_ENCRYPTED` / :data:`KIND_CORRUPT` / :data:`KIND_NO_TEXT` /
  :data:`KIND_UNSUPPORTED` / :data:`KIND_TOO_LARGE` 分类，由引擎落到不同统计口径；
- 本模块不持有打开的句柄与全局可变状态，可在进程池子进程内运行。
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

__all__ = [
    "DECOMPRESSED_MAX_BYTES",
    "DOCUMENT_EXTENSIONS",
    "DOCUMENT_MAX_BYTES",
    "KIND_CORRUPT",
    "KIND_ENCRYPTED",
    "KIND_NO_TEXT",
    "KIND_TOO_LARGE",
    "KIND_UNSUPPORTED",
    "PART_MAX_BYTES",
    "PDF_EXTENSIONS",
    "PDF_MAX_PAGES",
    "PDF_MAX_TEXT_CHARS",
    "ZIP_MAX_MEMBERS",
    "ExtractError",
    "TextUnit",
    "extract_document",
]

PDF_EXTENSIONS: tuple[str, ...] = (".pdf",)
"""按 PDF 解析的后缀（可选依赖 pypdf）。"""

_ZIP_EXTENSIONS: tuple[str, ...] = (".docx", ".xlsx", ".pptx", ".odt", ".ods")
"""按 zip + XML 解析的后缀（标准库实现）。"""

DOCUMENT_EXTENSIONS: tuple[str, ...] = _ZIP_EXTENSIONS + PDF_EXTENSIONS
"""默认按文档解析的后缀（zip 容器 + PDF）。"""

DOCUMENT_MAX_BYTES = 32 * 1024 * 1024
"""单个文档文件的读取上限（32 MiB）。"""

DECOMPRESSED_MAX_BYTES = 256 * 1024 * 1024
"""单个文档全部部件解压后的总量上限（256 MiB）。"""

PART_MAX_BYTES = 8 * 1024 * 1024
"""单个 zip 部件解压后的上限（8 MiB）。"""

ZIP_MAX_MEMBERS = 5000
"""单个文档压缩包允许的成员数上限。"""

PDF_MAX_PAGES = 500
"""单个 PDF 允许解析的页数上限（实测约 2.8 ms/页，500 页约 1.7 s）。"""

PDF_MAX_TEXT_CHARS = 8 * 1024 * 1024
"""单个 PDF 累计抽取文本的字符数上限（约 8 MiB 字符，防解压炸弹撑爆内存）。"""

PDF_MISSING_HINT = '缺少 pypdf 库，PDF 解析不可用，请安装: pip install "fcmd[pdf]"'
"""pypdf 缺失时的提示（随跳过记录展示，不静默跳过）。"""

HAS_PYPDF = importlib.util.find_spec("pypdf") is not None
"""可选依赖探测：只查定位不触发 import，进程池子进程导入本模块时会重新计算。"""

KIND_ENCRYPTED = "encrypted"
KIND_CORRUPT = "corrupt"
KIND_NO_TEXT = "no_text"
KIND_UNSUPPORTED = "unsupported"
KIND_TOO_LARGE = "too_large"

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
"""OLE 复合文档魔数：加密后的 OOXML 会整体变成这种容器。"""

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


@dataclass(frozen=True)
class TextUnit:
    """一段可扫描文本及其可读位置（如 ``段落 3``、``Sheet1!A12``）。"""

    text: str
    location: str = ""


class ExtractError(Exception):
    """文档抽取失败；``kind`` 决定统计归类，不要合并成笼统的 ``skipped``。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _iter_end(data: bytes, tags: str | Iterable[str]) -> Iterator[ET.Element]:
    """``iterparse`` 到指定结束标签就产出并清理，限制内存占用。"""
    wanted = (tags,) if isinstance(tags, str) else tuple(tags)
    parser = ET.iterparse(io.BytesIO(data), events=("end",))
    for _event, element in parser:
        if element.tag in wanted:
            yield element
            element.clear()


class _PartReader:
    """受限的 zip 读取器：加密标记 / 单部件 / 解压总量三重检查。"""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self._archive = archive
        self._infos = {info.filename: info for info in archive.infolist()}
        self._total = 0

    def has(self, name: str) -> bool:
        """压缩包内是否存在该部件。"""
        return name in self._infos

    def matching(self, prefix: str, suffix: str = ".xml") -> list[str]:
        """按前缀与后缀筛选部件名（字典序）。"""
        return sorted(name for name in self._infos if name.startswith(prefix) and name.endswith(suffix))

    def read(self, name: str) -> bytes:
        """读取部件内容，超过任一上限或已加密时抛 :class:`ExtractError`。"""
        info = self._infos[name]
        if info.flag_bits & 0x1:
            raise ExtractError(KIND_ENCRYPTED, f"文档部件已加密: {name}")
        if info.file_size > PART_MAX_BYTES:
            raise ExtractError(KIND_TOO_LARGE, f"部件 {name} 解压后超过 {PART_MAX_BYTES} 字节上限")
        if self._total + info.file_size > DECOMPRESSED_MAX_BYTES:
            raise ExtractError(KIND_TOO_LARGE, "文档解压总量超过上限")
        try:
            data = self._archive.read(name)
        except RuntimeError as exc:  # zipfile 对加密成员抛 RuntimeError
            raise ExtractError(KIND_ENCRYPTED, f"文档部件已加密: {name}") from exc
        if len(data) > PART_MAX_BYTES:
            raise ExtractError(KIND_TOO_LARGE, f"部件 {name} 解压后超过 {PART_MAX_BYTES} 字节上限")
        self._total += len(data)
        if self._total > DECOMPRESSED_MAX_BYTES:
            raise ExtractError(KIND_TOO_LARGE, "文档解压总量超过上限")
        return data


# ---------------------------------------------------------------------- #
# docx
# ---------------------------------------------------------------------- #
def _docx_paragraphs(data: bytes, locate: Callable[[int], str]) -> list[TextUnit]:
    """按 ``w:p`` 抽取段落文本，位置由 ``locate(段落序号)`` 生成。"""
    units: list[TextUnit] = []
    for index, paragraph in enumerate(_iter_end(data, f"{_W}p"), start=1):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag == f"{_W}br":
                parts.append("\n")
        text = "".join(parts)
        if text.strip():
            units.append(TextUnit(text, locate(index)))
    return units


def _docx_comments(data: bytes) -> list[TextUnit]:
    """批注单独标注，位置记为 ``批注 N``。"""
    units: list[TextUnit] = []
    for comment in _iter_end(data, f"{_W}comment"):
        comment_id = comment.get(f"{_W}id") or "?"
        parts: list[str] = []
        for node in comment.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag == f"{_W}br":
                parts.append("\n")
        text = "".join(parts)
        if text.strip():
            units.append(TextUnit(text, f"批注 {comment_id}"))
    return units


def _extract_docx(reader: _PartReader) -> list[TextUnit]:
    units: list[TextUnit] = []
    if reader.has("word/document.xml"):
        units.extend(_docx_paragraphs(reader.read("word/document.xml"), lambda index: f"段落 {index}"))
    for header, name in enumerate(reader.matching("word/header", ".xml"), start=1):
        units.extend(
            _docx_paragraphs(
                reader.read(name),
                lambda index, header=header: f"页眉 {header} 段落 {index}",
            )
        )
    for footer, name in enumerate(reader.matching("word/footer", ".xml"), start=1):
        units.extend(
            _docx_paragraphs(
                reader.read(name),
                lambda index, footer=footer: f"页脚 {footer} 段落 {index}",
            )
        )
    if reader.has("word/comments.xml"):
        units.extend(_docx_comments(reader.read("word/comments.xml")))
    return units


# ---------------------------------------------------------------------- #
# xlsx
# ---------------------------------------------------------------------- #
def _zip_child(base: str, target: str) -> str:
    """把 rels 里的 Target 解析成压缩包内路径。"""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base, target))


def _xlsx_shared_strings(reader: _PartReader) -> tuple[str, ...]:
    """读取共享字符串表（跳过 ``rPh`` 注音）。"""
    if not reader.has("xl/sharedStrings.xml"):
        return ()
    values: list[str] = []
    for item in _iter_end(reader.read("xl/sharedStrings.xml"), f"{_S}si"):
        parts: list[str] = []
        for child in item:
            if child.tag == f"{_S}t":
                parts.append(child.text or "")
            elif child.tag == f"{_S}r":
                parts.extend(node.text or "" for node in child.iter(f"{_S}t"))
        values.append("".join(parts))
    return tuple(values)


def _xlsx_sheet_parts(reader: _PartReader) -> list[tuple[str, str]]:
    """返回 ``(工作表名, 部件路径)``；rels 缺失时按文件名回退。"""
    rels: dict[str, str] = {}
    if reader.has("xl/_rels/workbook.xml.rels"):
        for rel in _iter_end(reader.read("xl/_rels/workbook.xml.rels"), f"{_REL}Relationship"):
            rels[rel.get("Id") or ""] = rel.get("Target") or ""
    sheets: list[tuple[str, str]] = []
    if reader.has("xl/workbook.xml"):
        for sheet in _iter_end(reader.read("xl/workbook.xml"), f"{_S}sheet"):
            name = sheet.get("name") or f"Sheet{len(sheets) + 1}"
            target = rels.get(sheet.get(f"{_R}id") or "")
            if target:
                sheets.append((name, _zip_child("xl", target)))
    if sheets:
        return sheets
    return [(Path(name).stem, name) for name in reader.matching("xl/worksheets/sheet", ".xml")]


def _cell_value(cell: ET.Element) -> str:
    node = cell.find(f"{_S}v")
    return (node.text or "") if node is not None else ""


def _xlsx_sheet_units(data: bytes, sheet_name: str, shared: tuple[str, ...]) -> list[TextUnit]:
    """按单元格抽取文本，位置记为 ``工作表!A12``。"""
    units: list[TextUnit] = []
    for cell in _iter_end(data, f"{_S}c"):
        reference = cell.get("r") or ""
        kind = cell.get("t") or "n"
        text = ""
        if kind == "s":
            try:
                index = int(_cell_value(cell))
            except ValueError:
                index = -1
            if 0 <= index < len(shared):
                text = shared[index]
        elif kind == "inlineStr":
            text = "".join(node.text or "" for node in cell.iter(f"{_S}t"))
        elif kind == "str":
            text = _cell_value(cell)
        if text.strip():
            units.append(TextUnit(text, f"{sheet_name}!{reference}" if reference else sheet_name))
    return units


def _xlsx_comment_units(data: bytes) -> list[TextUnit]:
    """批注位置记为 ``批注 A12``。"""
    units: list[TextUnit] = []
    for comment in _iter_end(data, f"{_S}comment"):
        reference = comment.get("ref") or ""
        text = "".join(node.text or "" for node in comment.iter(f"{_S}t"))
        if text.strip():
            units.append(TextUnit(text, f"批注 {reference}" if reference else "批注"))
    return units


def _extract_xlsx(reader: _PartReader) -> list[TextUnit]:
    shared = _xlsx_shared_strings(reader)
    units: list[TextUnit] = []
    for name, part in _xlsx_sheet_parts(reader):
        if reader.has(part):
            units.extend(_xlsx_sheet_units(reader.read(part), name, shared))
    for name in reader.matching("xl/comments", ".xml"):
        units.extend(_xlsx_comment_units(reader.read(name)))
    return units


# ---------------------------------------------------------------------- #
# pptx
# ---------------------------------------------------------------------- #
def _trailing_number(name: str) -> int:
    """从 ``slide12.xml`` 取序号，取不到时返回 0。"""
    stem = Path(name).stem
    digits = ""
    for char in reversed(stem):
        if not char.isdigit():
            break
        digits = char + digits
    return int(digits) if digits else 0


def _pptx_paragraphs(data: bytes, label: str) -> list[TextUnit]:
    """按 ``a:p`` 抽取段落文本，位置记为 ``幻灯片 N`` / ``备注 N``。"""
    units: list[TextUnit] = []
    for paragraph in _iter_end(data, f"{_A}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_A}t"))
        if text.strip():
            units.append(TextUnit(text, label))
    return units


def _extract_pptx(reader: _PartReader) -> list[TextUnit]:
    units: list[TextUnit] = []
    slides = sorted(reader.matching("ppt/slides/slide", ".xml"), key=_trailing_number)
    for name in slides:
        units.extend(_pptx_paragraphs(reader.read(name), f"幻灯片 {_trailing_number(name)}"))
    notes = sorted(reader.matching("ppt/notesSlides/notesSlide", ".xml"), key=_trailing_number)
    for name in notes:
        units.extend(_pptx_paragraphs(reader.read(name), f"备注 {_trailing_number(name)}"))
    return units


# ---------------------------------------------------------------------- #
# odt / ods
# ---------------------------------------------------------------------- #
def _odf_text(element: ET.Element) -> str:
    """递归拼接 ODF 文本，保留制表符、空格与换行语义。"""
    parts: list[str] = [element.text or ""]
    for child in element:
        tag = child.tag
        if tag in (f"{_TEXT}p", f"{_TEXT}h"):
            parts.append("\n")
            parts.append(_odf_text(child))
        elif tag == f"{_TEXT}s":
            parts.append(" ")
        elif tag == f"{_TEXT}tab":
            parts.append("\t")
        elif tag == f"{_TEXT}line-break":
            parts.append("\n")
        elif tag in (f"{_TEXT}span", f"{_TEXT}a", f"{_TEXT}note"):
            parts.append(_odf_text(child))
        elif child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _odt_units(data: bytes) -> list[TextUnit]:
    """按 ``text:p`` / ``text:h`` 抽取，位置记为 ``段落 N``。"""
    units: list[TextUnit] = []
    for index, element in enumerate(_iter_end(data, (f"{_TEXT}p", f"{_TEXT}h")), start=1):
        text = _odf_text(element)
        if text.strip():
            units.append(TextUnit(text, f"段落 {index}"))
    return units


def _ods_units(data: bytes) -> list[TextUnit]:
    """按工作表行抽取，位置记为 ``工作表 X 第 N 行``。"""
    units: list[TextUnit] = []
    for table_index, table in enumerate(_iter_end(data, f"{_TABLE}table"), start=1):
        name = table.get(f"{_TABLE}name") or f"表 {table_index}"
        for row_index, row in enumerate(table.iter(f"{_TABLE}table-row"), start=1):
            cells: list[str] = []
            for cell in row.iter(f"{_TABLE}table-cell"):
                text = _odf_text(cell).strip()
                if text:
                    cells.append(text)
            if cells:
                units.append(TextUnit("\t".join(cells), f"工作表 {name} 第 {row_index} 行"))
    return units


def _odf_content(reader: _PartReader) -> bytes:
    if not reader.has("content.xml"):
        raise ExtractError(KIND_CORRUPT, "文档缺少 content.xml")
    return reader.read("content.xml")


def _extract_odt(reader: _PartReader) -> list[TextUnit]:
    return _odt_units(_odf_content(reader))


def _extract_ods(reader: _PartReader) -> list[TextUnit]:
    return _ods_units(_odf_content(reader))


_EXTRACTORS: dict[str, Callable[[_PartReader], list[TextUnit]]] = {
    ".docx": _extract_docx,
    ".xlsx": _extract_xlsx,
    ".pptx": _extract_pptx,
    ".odt": _extract_odt,
    ".ods": _extract_ods,
}


# ---------------------------------------------------------------------- #
# pdf（可选依赖 pypdf）
# ---------------------------------------------------------------------- #
def _require_pypdf() -> Any:
    """按 ``HAS_PYPDF`` 延迟导入 pypdf；缺失时抛 unsupported（带安装提示）。"""
    if not HAS_PYPDF:
        raise ExtractError(KIND_UNSUPPORTED, PDF_MISSING_HINT)
    return importlib.import_module("pypdf")


def _extract_pdf(path: Path) -> list[TextUnit]:
    """按页抽取 PDF 文本，位置记为 ``p.N``；先判加密标志位再取页。"""
    pypdf = _require_pypdf()
    try:
        reader = pypdf.PdfReader(str(path), strict=False)
    except Exception as exc:
        raise ExtractError(KIND_CORRUPT, f"PDF 无法打开: {exc}") from exc
    try:
        # 只查 trailer 的 /Encrypt 标志，不触发解密与页面解析
        if reader.is_encrypted:
            raise ExtractError(KIND_ENCRYPTED, "PDF 已加密")
        try:
            pages = len(reader.pages)
        except Exception as exc:
            raise ExtractError(KIND_CORRUPT, f"PDF 页树损坏: {exc}") from exc
        if pages > PDF_MAX_PAGES:
            raise ExtractError(KIND_TOO_LARGE, f"PDF 页数 {pages} 超过 {PDF_MAX_PAGES} 页上限")
        units: list[TextUnit] = []
        total = 0
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise ExtractError(KIND_CORRUPT, f"PDF 第 {index} 页解析失败: {exc}") from exc
            total += len(text)
            if total > PDF_MAX_TEXT_CHARS:
                raise ExtractError(KIND_TOO_LARGE, f"PDF 文本超过 {PDF_MAX_TEXT_CHARS} 字符上限")
            if text.strip():
                units.append(TextUnit(text, f"p.{index}"))
        return units
    finally:
        reader.close()


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
def _document_size(path: Path, size: int | None) -> int:
    """返回文档字节数；省略 ``size`` 时自行 ``stat``，并校验读取上限。"""
    if size is None:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ExtractError(KIND_CORRUPT, f"无法读取文档: {exc}") from exc
    if size > DOCUMENT_MAX_BYTES:
        raise ExtractError(KIND_TOO_LARGE, f"文档超过 {DOCUMENT_MAX_BYTES} 字节上限")
    return size


def _extract_zip_document(path: Path, extractor: Callable[[_PartReader], list[TextUnit]]) -> list[TextUnit]:
    """打开 zip 容器并交给具体格式的抽取器（加密容器 / 损坏统一归类）。"""
    try:
        with path.open("rb") as handle:
            if handle.read(len(_OLE_MAGIC)) == _OLE_MAGIC:
                raise ExtractError(KIND_ENCRYPTED, "文档已加密（OLE 复合文档容器）")
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > ZIP_MAX_MEMBERS:
                raise ExtractError(KIND_TOO_LARGE, f"压缩成员超过 {ZIP_MAX_MEMBERS} 个上限")
            return extractor(_PartReader(archive))
    except ExtractError:
        raise
    except zipfile.BadZipFile as exc:
        raise ExtractError(KIND_CORRUPT, f"文档损坏: {exc}") from exc
    except (OSError, ET.ParseError, ValueError, RuntimeError, EOFError) as exc:
        raise ExtractError(KIND_CORRUPT, f"文档损坏: {exc}") from exc


def extract_document(path: Path, size: int | None = None) -> list[TextUnit]:
    """抽取文档文本单元；失败时抛 :class:`ExtractError`（带 ``kind``）。

    ``size`` 由遍历阶段传入，省略时自行 ``stat``。返回值至少包含一个非空文本
    单元，否则按「无文本层」抛错。
    """
    suffix = path.suffix.lower()
    extractor = _EXTRACTORS.get(suffix)
    if extractor is None and suffix not in PDF_EXTENSIONS:
        raise ExtractError(KIND_UNSUPPORTED, f"不支持解析 {suffix or '无后缀'} 文档")
    _document_size(path, size)
    units = _extract_zip_document(path, extractor) if extractor is not None else _extract_pdf(path)
    if not units:
        raise ExtractError(KIND_NO_TEXT, "文档没有可扫描的文本")
    return units
