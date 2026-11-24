"""Office 文档元数据读取（docx / xlsx / pptx 的 core.xml 与 app.xml）。"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree

METADATA_KEYS: tuple[str, ...] = ("author", "last_modified_by", "title", "subject", "company")
METADATA_LABELS: dict[str, str] = {
    "author": "作者",
    "last_modified_by": "最后修改者",
    "title": "标题",
    "subject": "主题",
    "company": "公司",
}
"""元数据键 → 中文标签（GUI 下拉 / 规则摘要 / 结果位置共用）。"""

_CORE_FIELDS = {
    "creator": "author",
    "lastModifiedBy": "last_modified_by",
    "title": "title",
    "subject": "subject",
}
_APP_FIELDS = {"Company": "company"}


def _tag_name(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _read_props(archive: zipfile.ZipFile, name: str, mapping: dict[str, str], values: dict[str, str]) -> None:
    try:
        root = ElementTree.fromstring(archive.read(name))
    except (KeyError, ElementTree.ParseError):
        return
    for child in root:
        key = mapping.get(_tag_name(child))
        if key and child.text and child.text.strip():
            values[key] = child.text.strip()


def read_document_metadata(path: Path) -> dict[str, str]:
    """读取 Office 文档元数据；非 zip / 非 Office 文件返回空字典。"""
    values: dict[str, str] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            _read_props(archive, "docProps/core.xml", _CORE_FIELDS, values)
            _read_props(archive, "docProps/app.xml", _APP_FIELDS, values)
    except (OSError, zipfile.BadZipFile):
        return {}
    return values
