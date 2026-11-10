"""reshackw 的无界面逻辑：预览解码、缩放计算与剪贴板转换。

从 :mod:`fcmd.tools.system.reshackw` 拆出，便于在无显示环境单独测试；
窗口模块继续以原名 re-export，保持既有导入路径与 monkeypatch 目标不变。
"""

from __future__ import annotations

import sys
from typing import Any, cast

from fcmd.tools.system.reshack import (
    RT_BITMAP,
    RT_ICON,
    ResourceEntry,
    bitmap_to_bmp,
    icon_to_ico,
    resource_extension,
)

__all__ = [
    "FIT_MAX_UPSCALE",
    "HEX_PREVIEW_LIMIT",
    "MAX_ZOOM",
    "MIN_ZOOM",
    "TEXT_PREVIEW_LIMIT",
    "ZOOM_STEP",
    "clamp_zoom",
    "copy_image_to_clipboard",
    "decode_text",
    "default_export_name",
    "fit_zoom",
    "format_hex_preview",
    "group_resources",
    "image_to_dib",
    "is_text_resource",
    "load_preview_image",
    "zoom_in",
    "zoom_out",
]

HEX_PREVIEW_LIMIT = 4096
TEXT_PREVIEW_LIMIT = 512 * 1024
MIN_ZOOM = 0.1
MAX_ZOOM = 16.0
ZOOM_STEP = 1.25
FIT_MAX_UPSCALE = 8.0

_TEXT_RESOURCE_TYPES = frozenset({4, 5, 6, 9, 10, 11, 16, 17, 23, 24})


def is_text_resource(type_id: int) -> bool:
    """判断资源类型是否适合文本预览。"""
    return type_id in _TEXT_RESOURCE_TYPES


def decode_text(data: bytes) -> str:
    """按 BOM / 零字节比例 / UTF-8 / GBK 的顺序解码文本资源。"""
    if not data:
        return "(空资源)"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if data.count(0) / len(data) > 0.3:
        try:
            return data.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def format_hex_preview(data: bytes, limit: int = HEX_PREVIEW_LIMIT) -> str:
    """格式化十六进制预览（每行 16 字节，含 ASCII 列，超长截断）。"""
    if not data:
        return "(空资源)"
    view = data[:limit]
    lines: list[str] = []
    for offset in range(0, len(view), 16):
        chunk = view[offset : offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hex_part:<47}  {ascii_part}")
    if len(data) > limit:
        lines.append(f"... 仅显示前 {limit} 字节，共 {len(data)} 字节")
    return "\n".join(lines)


def group_resources(
    resources: list[ResourceEntry],
) -> list[tuple[int, str, list[ResourceEntry]]]:
    """按资源类型分组，返回 ``(类型 ID, 类型名, 资源列表)`` 并按类型 ID 排序。"""
    grouped: dict[int, tuple[str, list[ResourceEntry]]] = {}
    for resource in resources:
        _, items = grouped.setdefault(resource.type_id, (resource.type_name, []))
        items.append(resource)
    return [
        (type_id, name, sorted(items, key=lambda item: (item.name_id, item.language_id)))
        for type_id, (name, items) in sorted(grouped.items())
    ]


def default_export_name(resource: ResourceEntry) -> str:
    """生成默认导出文件名（类型名去空格 + 名称 + 语言 + 扩展名）。"""
    type_part = resource.type_name.replace(" ", "_")
    return f"{type_part}_{resource.name_id}_{resource.language_id}{resource_extension(resource.type_id)}"


def load_preview_image(data: bytes, type_id: int) -> Any:
    """把资源数据解码为 PIL Image；不支持或未安装 Pillow 时返回 ``None``。"""
    if not data:
        return None
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        return None

    candidates: list[bytes] = []
    if type_id == RT_BITMAP:
        bmp = bitmap_to_bmp(data)
        if bmp is not None:
            candidates.append(bmp)
    elif type_id == RT_ICON:
        ico = icon_to_ico(data)
        if ico is not None:
            candidates.append(ico)
    candidates.append(data)
    for payload in candidates:
        try:
            image = Image.open(BytesIO(payload))
            image.load()
        except Exception:  # Pillow 对不支持的数据抛多种异常
            continue
        return image
    return None


def clamp_zoom(value: float) -> float:
    """把缩放倍率限制在 ``MIN_ZOOM`` ~ ``MAX_ZOOM`` 之间。"""
    return min(MAX_ZOOM, max(MIN_ZOOM, value))


def zoom_in(value: float) -> float:
    """放大一档（``ZOOM_STEP`` 倍）。"""
    return clamp_zoom(value * ZOOM_STEP)


def zoom_out(value: float) -> float:
    """缩小一档（``ZOOM_STEP`` 倍）。"""
    return clamp_zoom(value / ZOOM_STEP)


def fit_zoom(
    image_width: int,
    image_height: int,
    view_width: int,
    view_height: int,
    max_upscale: float = FIT_MAX_UPSCALE,
) -> float:
    """计算“适应窗口”倍率：完整显示图像，且放大不超过 ``max_upscale``。

    任一尺寸无效（<=0）时返回 ``1.0``。
    """
    if image_width <= 0 or image_height <= 0 or view_width <= 0 or view_height <= 0:
        return 1.0
    scale = min(view_width / image_width, view_height / image_height)
    return clamp_zoom(min(scale, max_upscale))


def image_to_dib(image: Any) -> bytes | None:
    """把 PIL 图像转换为 Windows ``CF_DIB`` 数据（BITMAPINFOHEADER + 像素）。

    ``CF_DIB`` 就是去掉 14 字节 ``BITMAPFILEHEADER`` 的 BMP；Pillow 不可用或
    转换失败时返回 ``None``。
    """
    try:
        from io import BytesIO

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="BMP")
    except Exception:  # Pillow 缺失或图像模式不支持
        return None
    payload = buffer.getvalue()
    if len(payload) <= 14 or payload[:2] != b"BM":
        return None
    return payload[14:]


def copy_image_to_clipboard(image: Any) -> bool:  # noqa: PLR0911  # pragma: no cover - 依赖 Windows 剪贴板
    """把 PIL 图像写入 Windows 剪贴板（``CF_DIB``）。

    非 Windows、缺少 Pillow 或剪贴板被占用时返回 ``False``。
    """
    if sys.platform != "win32":
        return False
    dib = image_to_dib(image)
    if dib is None:
        return False
    data = cast("bytes", dib)
    import ctypes
    from ctypes import wintypes

    windll = cast("Any", ctypes).windll  # windll 仅 Windows 存在，Linux 类型检查按 Any 处理
    user32 = windll.user32
    kernel32 = windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    gmem_moveable = 0x0002
    cf_dib = 8
    if not user32.OpenClipboard(None):
        return False
    try:
        if not user32.EmptyClipboard():
            return False
        handle = kernel32.GlobalAlloc(gmem_moveable, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(cf_dib, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()
