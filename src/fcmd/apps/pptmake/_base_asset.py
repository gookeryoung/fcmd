"""离线生成随包基础模板 ``assets/pptmake-base-16x9.pptx``。

python-pptx 只能消费既有母版 / 版式，不能凭空创建，因此基础模板作为二进制
资源入库，由本模块在维护期重新生成::

    uv run python -m fcmd.apps.pptmake._base_asset

生成口径与 :mod:`fcmd.apps.pptmake._design` 的 16:9 默认令牌一致：每个
``LAYOUTS`` 项生成一个同名版式（中文名取 ``LAYOUT_TEXT``），版式带
``title`` 与 ``subTitle`` 占位符，页面尺寸 1280×720 设计单位
（12192000×6858000 EMU），主题色由 ``default_template()`` 重写。导出时仅把
标题 / 副标题写进占位符，正文与装饰仍由 draw list 绘制，保证预览与成品同源。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

from fcmd.apps.pptmake._base import _ASSET_DIR, _BASE_FILES
from fcmd.apps.pptmake._core import ASPECT_16_9, LAYOUT_TEXT, LAYOUTS, default_template

__all__ = ["EMU_PER_UNIT", "build", "main"]

EMU_PER_UNIT: Final[int] = 9525
_NS: Final[dict[str, str]] = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
_LAYOUT_CT: Final[str] = "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"
_REL_LAYOUT: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"

_W, _H = 1280.0, 720.0
_MARGIN = round(_W * 0.075)  # 96
_TOP = round(_H * 0.10)  # 72
_TITLE = 48.0


@dataclass(frozen=True, slots=True)
class _Placement:
    """一个版式的标题 / 副标题占位符几何与字号（设计坐标）。"""

    title: tuple[float, float, float, float]
    subtitle: tuple[float, float, float, float]
    title_size: float
    subtitle_size: float
    align: str = "l"
    anchor: str = "t"

    @property
    def title_xml(self) -> str:
        return _shape("标题", "title", "", self.title, self.title_size, self.align, self.anchor, "title")

    @property
    def subtitle_xml(self) -> str:
        return _shape("副标题", "subTitle", ' idx="1"', self.subtitle, self.subtitle_size, self.align, "t", "body")


_DEFAULT = _Placement(
    title=(_MARGIN, _TOP, _W - _MARGIN * 2, round(_TITLE * 1.45)),
    subtitle=(_MARGIN, _TOP + round(_TITLE * 2.1), _W - _MARGIN * 2, round(28 * 1.45)),
    title_size=_TITLE,
    subtitle_size=28,
)

_PLACEMENTS: Final[dict[str, _Placement]] = {
    "cover": _Placement(
        title=(_MARGIN, round(_H * 0.34), round(_W - _MARGIN * 2.6), round(_H * 0.24)),
        subtitle=(_MARGIN, round(_H * 0.61), round(_W - _MARGIN * 2.2), round(_H * 0.12)),
        title_size=64,
        subtitle_size=32,
    ),
    "section": _Placement(
        title=(_MARGIN + 38, round(_H * 0.45), _W - _MARGIN * 2 - 38, 64),
        subtitle=(_MARGIN + 38, round(_H * 0.56), _W - _MARGIN * 2 - 38, 80),
        title_size=round(_TITLE * 1.1),
        subtitle_size=24,
    ),
    "quote": _Placement(
        title=(_MARGIN + 30, round(_H * 0.30), _W - _MARGIN * 2 - 60, round(_H * 0.34)),
        subtitle=(_MARGIN + 30, round(_H * 0.72), _W - _MARGIN * 2 - 60, 40),
        title_size=44,
        subtitle_size=28,
    ),
    "closing": _Placement(
        title=(_MARGIN, round(_H * 0.34), _W - _MARGIN * 2, round(_H * 0.2)),
        subtitle=(_MARGIN, round(_H * 0.60), _W - _MARGIN * 2, 60),
        title_size=64,
        subtitle_size=24,
        align="ctr",
        anchor="ctr",
    ),
    "image": _Placement(
        title=(_MARGIN, _TOP + round(_TITLE * 1.2), round((_W - _MARGIN * 2) * 0.46), round(64 * 2.4)),
        subtitle=(_MARGIN, _TOP + round(64 * 3.1), round((_W - _MARGIN * 2) * 0.46), round(24 * 3.2)),
        title_size=round(64 * 0.82),
        subtitle_size=24,
    ),
    "contact": _Placement(
        title=(_MARGIN, _H * 0.18, _W * 0.5, round(64 * 1.6)),
        subtitle=(_MARGIN, _H * 0.18 + round(64 * 2.1), _W * 0.46, round(24 * 2.4)),
        title_size=round(64 * 0.85),
        subtitle_size=24,
    ),
    "photo_cover": _Placement(
        title=(_MARGIN, _H * 0.55, round(_W - _MARGIN * 2.6), round(_H * 0.20)),
        subtitle=(_MARGIN, _H * 0.78, round(_W - _MARGIN * 2.2), round(32 * 1.6)),
        title_size=64,
        subtitle_size=32,
    ),
    "photo_section": _Placement(
        title=(_MARGIN + 38, round(_H * 0.45), _W - _MARGIN * 2 - 38, 64),
        subtitle=(_MARGIN + 38, round(_H * 0.56), _W - _MARGIN * 2 - 38, 80),
        title_size=round(_TITLE * 1.1),
        subtitle_size=24,
    ),
    "testimonial": _Placement(
        title=(_MARGIN, _H * 0.26, _W - _MARGIN * 2, round(_H * 0.34)),
        subtitle=(_MARGIN, _H * 0.72, _W - _MARGIN * 2, round(28 * 1.6)),
        title_size=44,
        subtitle_size=28,
    ),
}


def _emu(value: float) -> int:
    """设计坐标转 EMU（四舍五入到整数）。"""
    return round(value * EMU_PER_UNIT)


def _size(value: float) -> int:
    """设计坐标字号转 OOXML 百分之一磅。"""
    return round(value * 0.75 * 100)


def _shape(  # noqa: PLR0913, PLR0917 - 占位符 XML 工厂，参数与 ph 属性一一对应
    name: str,
    kind: str,
    idx: str,
    rect: tuple[float, float, float, float],
    size: float,
    align: str,
    anchor: str,
    role: str,
) -> str:
    """生成一个占位符 ``p:sp``；``role`` 决定主题字体与颜色。"""
    x, y, w, h = rect
    bold = role == "title"
    color = "tx1" if bold else "tx2"
    latin = "+mj-lt" if bold else "+mn-lt"
    east = "+mj-ea" if bold else "+mn-ea"
    prompt = "单击此处编辑标题" if bold else "单击此处编辑副标题"
    sz = _size(size)
    weight = ' b="1"' if bold else ""
    fmt = (
        f'<a:solidFill><a:schemeClr val="{color}"/></a:solidFill><a:latin typeface="{latin}"/><a:ea typeface="{east}"/>'
    )
    paragraph = (
        f'<a:p><a:pPr algn="{align}"><a:defRPr sz="{sz}"{weight}>{fmt}</a:defRPr></a:pPr>'
        f'<a:r><a:rPr lang="zh-CN" sz="{sz}"{weight}>{fmt}</a:rPr><a:t>{prompt}</a:t></a:r></a:p>'
    )
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="2" name="{name}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        f'<p:nvPr><p:ph type="{kind}"{idx}/></p:nvPr>'
        "</p:nvSpPr><p:spPr>"
        f'<a:xfrm><a:off x="{_emu(x)}" y="{_emu(y)}"/><a:ext cx="{_emu(w)}" cy="{_emu(h)}"/></a:xfrm>'
        "</p:spPr><p:txBody>"
        f'<a:bodyPr lIns="0" tIns="0" rIns="0" bIns="0" anchor="{anchor}"/>'
        f"<a:lstStyle/>{paragraph}</p:txBody></p:sp>"
    )


def _layout_xml(key: str) -> bytes:
    """按版式键生成 ``slideLayout`` 部件字节。"""
    placement = _PLACEMENTS.get(key, _DEFAULT)
    shapes = placement.title_xml + placement.subtitle_xml
    body = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" preserve="1">'
        f'<p:cSld name="{LAYOUT_TEXT.get(key, key)}"><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f"{shapes}</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )
    return body.encode("utf-8")


def _rels_xml(master_target: str) -> bytes:
    """版式到母版的关系部件。"""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{_REL_LAYOUT}" Target="{master_target}"/>'
        "</Relationships>"
    ).encode()


def _rewrite_content_types(data: bytes, count: int) -> bytes:
    """为新增版式补 ``Override``，已存在则保持原样。"""
    text = data.decode("utf-8")
    missing = "".join(
        f'<Override PartName="/ppt/slideLayouts/slideLayout{index}.xml" ContentType="{_LAYOUT_CT}"/>'
        for index in range(1, count + 1)
        if f"slideLayout{index}.xml" not in text
    )
    return text.replace("</Types>", f"{missing}</Types>").encode("utf-8")


def _layout_rid(index: int) -> str:
    """版式在母版关系中的 rId；保留默认模板的 rId12（主题）不被占用。"""
    return f"rId{index if index <= 11 else index + 1}"


def _rewrite_master_rels(data: bytes, count: int) -> bytes:
    """补齐母版到 12/13 号版式的关系。"""
    text = data.decode("utf-8")
    missing = "".join(
        f'<Relationship Id="{_layout_rid(index)}" Type="{_REL_LAYOUT}" '
        f'Target="../slideLayouts/slideLayout{index}.xml"/>'
        for index in range(12, count + 1)
        if f"slideLayout{index}.xml" not in text
    )
    return text.replace("</Relationships>", f"{missing}</Relationships>").encode("utf-8")


def _rewrite_master_layout_list(data: bytes, count: int) -> bytes:
    """重写 ``p:sldLayoutIdLst``，使版式数量与名称顺序可控。"""
    for prefix, uri in _NS.items():
        ElementTree.register_namespace(prefix, uri)
    root = ElementTree.fromstring(data)
    listing = root.find("p:sldLayoutIdLst", _NS)
    if listing is None:  # pragma: no cover - 默认模板总会带版式列表
        raise ValueError("母版缺少 p:sldLayoutIdLst")
    for child in list(listing):
        listing.remove(child)
    for index in range(1, count + 1):
        node = ElementTree.SubElement(listing, f"{{{_NS['p']}}}sldLayoutId")
        node.set("id", str(2147483648 + index))
        node.set(f"{{{_NS['r']}}}id", _layout_rid(index))
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _rewrite_presentation(data: bytes) -> bytes:
    """页面尺寸改为 16:9；默认模板是 4:3。"""
    for prefix, uri in _NS.items():
        ElementTree.register_namespace(prefix, uri)
    root = ElementTree.fromstring(data)
    size = root.find("p:sldSz", _NS)
    if size is None:  # pragma: no cover - 默认模板总会带 p:sldSz
        raise ValueError("演示文稿缺少 p:sldSz")
    size.set("cx", str(round(_W * EMU_PER_UNIT)))
    size.set("cy", str(round(_H * EMU_PER_UNIT)))
    size.set("type", "screen16x9")
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _rewrite_app(data: bytes) -> bytes:
    """同步扩展属性里的画幅描述。"""
    return (
        data.decode("utf-8")
        .replace(
            "<PresentationFormat>On-screen Show (4:3)</PresentationFormat>",
            "<PresentationFormat>Widescreen</PresentationFormat>",
        )
        .encode("utf-8")
    )


def build(target: Path) -> int:
    """按默认模板生成基础模板并写入 ``target``，返回版式数量。"""
    import pptx

    from fcmd.apps.pptmake._pptx import _rewrite_theme_xml

    source = Path(pptx.__file__).parent / "templates" / "default.pptx"
    with zipfile.ZipFile(source) as archive:
        entries = [(info.filename, archive.read(info.filename)) for info in archive.infolist()]
    payloads = dict(entries)
    count = len(LAYOUTS)
    for index, key in enumerate(LAYOUTS, start=1):
        payloads[f"ppt/slideLayouts/slideLayout{index}.xml"] = _layout_xml(key)
    for index in range(12, count + 1):
        payloads[f"ppt/slideLayouts/_rels/slideLayout{index}.xml.rels"] = _rels_xml("../slideMasters/slideMaster1.xml")
    payloads["[Content_Types].xml"] = _rewrite_content_types(payloads["[Content_Types].xml"], count)
    payloads["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rewrite_master_rels(
        payloads["ppt/slideMasters/_rels/slideMaster1.xml.rels"], count
    )
    payloads["ppt/slideMasters/slideMaster1.xml"] = _rewrite_master_layout_list(
        payloads["ppt/slideMasters/slideMaster1.xml"], count
    )
    payloads["ppt/presentation.xml"] = _rewrite_presentation(payloads["ppt/presentation.xml"])
    payloads["docProps/app.xml"] = _rewrite_app(payloads["docProps/app.xml"])
    payloads["ppt/theme/theme1.xml"] = _rewrite_theme_xml(payloads["ppt/theme/theme1.xml"], default_template())
    target.parent.mkdir(parents=True, exist_ok=True)
    order = [name for name, _ in entries]
    extra = [name for name in payloads if name not in set(order)]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in order:
            archive.writestr(name, payloads[name])
        for name in extra:
            archive.writestr(name, payloads[name])
    return count


def main() -> None:
    """维护入口：重新生成随包基础模板。"""
    target = _ASSET_DIR / _BASE_FILES[ASPECT_16_9]
    count = build(target)
    print(f"生成 {target}（{count} 个版式，{target.stat().st_size} 字节）")


if __name__ == "__main__":  # pragma: no cover - 维护脚本入口
    main()
