"""filescan 文档抽取测试：docx / xlsx / pptx / odt / ods 与五类降级。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fcmd.apps.filescan import _extract
from fcmd.apps.filescan._extract import (
    KIND_CORRUPT,
    KIND_ENCRYPTED,
    KIND_NO_TEXT,
    KIND_TOO_LARGE,
    KIND_UNSUPPORTED,
    ExtractError,
    extract_document,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _zip(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


def _docx(path: Path, paragraphs: list[str], extra: dict[str, str] | None = None) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    parts: dict[str, str] = {
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
        )
    }
    if extra:
        parts.update(extra)
    return _zip(path, parts)


def _xlsx(path: Path, *, shared: list[str], cells: str, comments: str = "") -> Path:
    strings = "".join(f"<si><t>{text}</t></si>" for text in shared)
    parts: dict[str, str] = {
        "xl/workbook.xml": (
            f'<?xml version="1.0"?><workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
            '<sheet name="名单" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0"?><Relationships xmlns="{REL}">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/sharedStrings.xml": f'<?xml version="1.0"?><sst xmlns="{S}">{strings}</sst>',
        "xl/worksheets/sheet1.xml": (
            f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>{cells}</sheetData></worksheet>'
        ),
    }
    if comments:
        parts["xl/comments1.xml"] = comments
    return _zip(path, parts)


def _pptx(path: Path, paragraphs: list[str], notes: list[str] | None = None) -> Path:
    def slide(texts: list[str]) -> str:
        body = "".join(f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>" for text in texts)
        return (
            f'<?xml version="1.0"?><p:sld xmlns:p="{P}" xmlns:a="{A}">'
            f"<p:cSld><p:spTree><p:sp><p:txBody>{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )

    parts = {"ppt/slides/slide1.xml": slide(paragraphs)}
    if notes:
        parts["ppt/notesSlides/notesSlide1.xml"] = slide(notes)
    return _zip(path, parts)


def _odt(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<text:p>{text}</text:p>" for text in paragraphs)
    content = (
        f'<?xml version="1.0"?><office:document-content xmlns:office="{OFFICE}" '
        f'xmlns:text="{TEXT}"><office:body><office:text>{body}</office:text>'
        "</office:body></office:document-content>"
    )
    return _zip(path, {"content.xml": content})


def _ods(path: Path, rows: list[list[str]], sheet: str = "名单") -> Path:
    body = "".join(
        "<table:table-row>"
        + "".join(f"<table:table-cell><text:p>{cell}</text:p></table:table-cell>" for cell in row)
        + "</table:table-row>"
        for row in rows
    )
    content = (
        f'<?xml version="1.0"?><office:document-content xmlns:office="{OFFICE}" '
        f'xmlns:text="{TEXT}" xmlns:table="{TABLE}"><office:body><office:spreadsheet>'
        f'<table:table table:name="{sheet}">{body}</table:table>'
        "</office:spreadsheet></office:body></office:document-content>"
    )
    return _zip(path, {"content.xml": content})


def _locations(units: list[_extract.TextUnit]) -> list[str]:
    return [unit.location for unit in units]


def test_extract_docx_paragraphs_and_headers(tmp_path: Path) -> None:
    header = f'<?xml version="1.0"?><w:hdr xmlns:w="{W}"><w:p><w:r><w:t>页眉机密</w:t></w:r></w:p></w:hdr>'
    path = _docx(tmp_path / "a.docx", ["姓名 张三", "手机 13812345678"], {"word/header1.xml": header})
    units = extract_document(path)
    assert _locations(units) == ["段落 1", "段落 2", "页眉 1 段落 1"]
    assert units[1].text == "手机 13812345678"


def test_extract_docx_comments(tmp_path: Path) -> None:
    comments = (
        f'<?xml version="1.0"?><w:comments xmlns:w="{W}"><w:comment w:id="2">'
        "<w:p><w:r><w:t>批注手机 13812345678</w:t></w:r></w:p></w:comment></w:comments>"
    )
    path = _docx(tmp_path / "a.docx", ["正文"], {"word/comments.xml": comments})
    units = extract_document(path)
    assert _locations(units) == ["段落 1", "批注 2"]


def test_extract_xlsx_shared_strings_and_cells(tmp_path: Path) -> None:
    cells = (
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c>'
        '<c r="C1" t="inlineStr"><is><t>内联 13812345678</t></is></c>'
        '<c r="D1" t="str"><v>公式 13912345678</v></c>'
        '<c r="E1" t="s"><v>not-a-number</v></c></row>'
    )
    path = _xlsx(tmp_path / "a.xlsx", shared=["姓名 张三"], cells=cells)
    units = extract_document(path)
    assert _locations(units) == ["名单!A1", "名单!C1", "名单!D1"]
    assert units[0].text == "姓名 张三"


def test_extract_xlsx_without_workbook_falls_back_to_sheet_files(tmp_path: Path) -> None:
    sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData><row r="1">'
        '<c r="A1" t="inlineStr"><is><t>13812345678</t></is></c>'
        "</row></sheetData></worksheet>"
    )
    path = _zip(tmp_path / "a.xlsx", {"xl/worksheets/sheet2.xml": sheet})
    units = extract_document(path)
    assert _locations(units) == ["sheet2!A1"]


def test_extract_xlsx_absolute_relationship_target(tmp_path: Path) -> None:
    sheet = f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData/></worksheet>'
    parts = {
        "xl/workbook.xml": (
            f'<?xml version="1.0"?><workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
            '<sheet name="名单" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0"?><Relationships xmlns="{REL}">'
            '<Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/comments1.xml": (
            f'<?xml version="1.0"?><commentsList xmlns="{S}"><comment ref="A1">'
            "<text><t>13812345678</t></text></comment></commentsList>"
        ),
    }
    path = _zip(tmp_path / "a.xlsx", parts)
    units = extract_document(path)
    assert _locations(units) == ["批注 A1"]


def test_extract_docx_tabs_and_breaks(tmp_path: Path) -> None:
    paragraph = "<w:p><w:r><w:t>手机</w:t><w:tab/><w:t>13812345678</w:t><w:br/><w:t>第二行</w:t></w:r></w:p>"
    path = _zip(
        tmp_path / "a.docx",
        {"word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{paragraph}</w:body></w:document>'},
    )
    units = extract_document(path)
    assert units[0].text == "手机\t13812345678\n第二行"


def test_extract_docx_comment_tabs(tmp_path: Path) -> None:
    comments = (
        f'<?xml version="1.0"?><w:comments xmlns:w="{W}"><w:comment w:id="7">'
        "<w:p><w:r><w:t>批注</w:t><w:tab/><w:t>末尾</w:t></w:r></w:p></w:comment></w:comments>"
    )
    path = _docx(tmp_path / "a.docx", ["正文"], {"word/comments.xml": comments})
    units = extract_document(path)
    assert units[1].text == "批注\t末尾"


def test_extract_odt_preserves_spacing_elements(tmp_path: Path) -> None:
    body = "<text:p>甲<text:tab/>乙<text:s/>丙<text:line-break/>丁<text:span>戊</text:span></text:p>"
    content = (
        f'<?xml version="1.0"?><office:document-content xmlns:office="{OFFICE}" '
        f'xmlns:text="{TEXT}"><office:body><office:text>{body}</office:text>'
        "</office:body></office:document-content>"
    )
    path = _zip(tmp_path / "a.odt", {"content.xml": content})
    units = extract_document(path)
    assert units[0].text == "甲\t乙 丙\n丁戊"


def test_extract_corrupt_xml(tmp_path: Path) -> None:
    path = _zip(tmp_path / "a.docx", {"word/document.xml": "<w:document"})
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_CORRUPT


def test_extract_xlsx_comments(tmp_path: Path) -> None:
    cells = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
    comments = (
        f'<?xml version="1.0"?><commentsList xmlns="{S}"><comment ref="A1">'
        "<text><t>批注 13812345678</t></text></comment></commentsList>"
    )
    path = _xlsx(tmp_path / "a.xlsx", shared=["正文"], cells=cells, comments=comments)
    units = extract_document(path)
    assert _locations(units) == ["名单!A1", "批注 A1"]


def test_extract_pptx_slide_and_notes(tmp_path: Path) -> None:
    path = _pptx(tmp_path / "a.pptx", ["第一页", "手机 13812345678"], ["备注 13912345678"])
    units = extract_document(path)
    assert _locations(units) == ["幻灯片 1", "幻灯片 1", "备注 1"]


def test_extract_odt_paragraphs(tmp_path: Path) -> None:
    path = _odt(tmp_path / "a.odt", ["姓名 张三", "手机 13812345678"])
    units = extract_document(path)
    assert _locations(units) == ["段落 1", "段落 2"]


def test_extract_ods_rows(tmp_path: Path) -> None:
    path = _ods(tmp_path / "a.ods", [["姓名", "手机"], ["张三", "13812345678"]])
    units = extract_document(path)
    assert _locations(units) == ["工作表 名单 第 1 行", "工作表 名单 第 2 行"]
    assert "13812345678" in units[1].text


def test_extract_encrypted_ole_container(tmp_path: Path) -> None:
    path = tmp_path / "a.docx"
    path.write_bytes(OLE_MAGIC + b"\x00" * 512)
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_ENCRYPTED


def _mark_encrypted(path: Path) -> Path:
    """把 zip 的通用标志位 bit0 打开，模拟加密成员。"""
    data = bytearray(path.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while (index := data.find(signature, start)) >= 0:
            flags = int.from_bytes(data[index + offset : index + offset + 2], "little") | 1
            data[index + offset : index + offset + 2] = flags.to_bytes(2, "little")
            start = index + 4
    path.write_bytes(bytes(data))
    return path


def test_extract_encrypted_zip_member(tmp_path: Path) -> None:
    path = _docx(tmp_path / "a.docx", ["正文"])
    _mark_encrypted(path)
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_ENCRYPTED


def test_extract_corrupt_zip(tmp_path: Path) -> None:
    path = tmp_path / "a.docx"
    path.write_bytes(b"PK\x03\x04 truncated")
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_CORRUPT


def test_extract_no_text_layer(tmp_path: Path) -> None:
    path = _docx(tmp_path / "a.docx", [])
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_NO_TEXT


def test_extract_unsupported_suffix(tmp_path: Path) -> None:
    with pytest.raises(ExtractError) as excinfo:
        extract_document(tmp_path / "a.pdf")
    assert excinfo.value.kind == KIND_UNSUPPORTED


def test_extract_rejects_oversize_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _docx(tmp_path / "a.docx", ["正文"])
    monkeypatch.setattr(_extract, "DOCUMENT_MAX_BYTES", 16)
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_TOO_LARGE


def test_extract_rejects_oversize_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _docx(tmp_path / "a.docx", ["正文" * 100])
    monkeypatch.setattr(_extract, "PART_MAX_BYTES", 32)
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_TOO_LARGE


def test_extract_rejects_decompressed_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _docx(tmp_path / "a.docx", ["正文"])
    monkeypatch.setattr(_extract, "PART_MAX_BYTES", 4096)
    monkeypatch.setattr(_extract, "DECOMPRESSED_MAX_BYTES", 32)
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_TOO_LARGE


def test_extract_rejects_member_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _zip(tmp_path / "a.docx", {"word/document.xml": "<x/>", "word/extra.xml": "<y/>"})
    monkeypatch.setattr(_extract, "ZIP_MAX_MEMBERS", 1)
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_TOO_LARGE


def test_extract_missing_content_part(tmp_path: Path) -> None:
    path = _zip(tmp_path / "a.odt", {"other.xml": "<x/>"})
    with pytest.raises(ExtractError) as excinfo:
        extract_document(path)
    assert excinfo.value.kind == KIND_CORRUPT
