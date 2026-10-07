"""媒体类 DSL 动作：img2ico / imagetool / pdftool。

三个工具原属 ``fcmd.cli.media`` 包，全部为纯 Python 实现（Pillow /
cairosvg / PyMuPDF / pypdf），无子进程调用。迁移后由 ``@action`` 装饰器
注册，TOML 声明 ``action = "<名>"`` 直接引用；CLI 参数 schema 从动作签名
自动推导（synth 层拷贝签名注入合成函数）。

外部库按需延迟导入——工具发现阶段不触发重型 PDF/PIL 库加载；动作执行期
import 失败时打印友好提示并返回，退出码保持 0。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..actions import action

if TYPE_CHECKING:
    import fitz  # PyMuPDF
    import pypdf
    from PIL import Image, ImageDraw, ImageFont

# ============================================================================
# 可选依赖检查（工具发现阶段不触发真正 import）
# ============================================================================

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:  # pragma: no cover - 仅在未安装 pillow 时触发
    HAS_PIL = False

try:
    import cairosvg  # type: ignore[import-untyped]

    HAS_CAIROSVG = True
except ImportError:  # pragma: no cover - 仅在未安装 cairosvg 时触发
    HAS_CAIROSVG = False

HAS_PYMUPDF = importlib.util.find_spec("fitz") is not None
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


# ============================================================================
# img2ico 尺寸常量
# ============================================================================

# Windows ICO 默认尺寸，参考 GreenFish Icon Editor 的标准：
# 覆盖从浏览器 favicon 到高 DPI 应用图标的全部常见场景。
SVG_TO_ICO_WIN_SIZES: tuple[tuple[int, int], ...] = (
    (16, 16),
    (24, 24),
    (32, 32),
    (40, 40),
    (48, 48),
    (64, 64),
    (72, 72),
    (96, 96),
    (128, 128),
    (256, 256),
)

# macOS ICNS 默认尺寸，遵循 Apple Human Interface Guidelines：
# 对应 icp4/icp5/icp6/icp7/icp8/icp9 六种基础尺寸，Pillow 会
# 自动追加 2x 分辨率条目（icp10/icp11/icp12/icp13/icp14/icp15）。
SVG_TO_ICO_MAC_SIZES: tuple[tuple[int, int], ...] = (
    (16, 16),
    (32, 32),
    (128, 128),
    (256, 256),
    (512, 512),
    (1024, 1024),
)

# 最大渲染尺寸：SVG 矢量可无损缩放，先渲染到最大需求尺寸再由 Pillow
# 内部 resize 生成各子尺寸，避免 cairosvg 重复渲染造成性能浪费。
_MAX_RENDER_SIZE = 1024


def _require_deps() -> bool:
    """检查 PIL + cairosvg 依赖，缺失时打印提示并返回 False。"""
    missing: list[str] = []
    if not HAS_PIL:
        missing.append("pillow (安装: fcmd[img])")
    if not HAS_CAIROSVG:
        missing.append("cairosvg (安装: fcmd[svg])")
    if missing:
        print(f"缺少依赖: {', '.join(missing)}")
        return False
    return True


def _parse_sizes(sizes: list[int] | None) -> list[tuple[int, int]] | None:
    """将整数尺寸列表转为 (w, h) 元组列表；None 输入返回 None。"""
    if sizes is None:
        return None
    return [(s, s) for s in sizes]


def _detect_format(output: Path, fmt: str | None) -> str:
    """根据显式参数或输出文件后缀推断目标格式。"""
    if fmt is not None:
        fmt_lower = fmt.lower()
        if fmt_lower not in ("win", "windows", "ico", "mac", "darwin", "icns"):
            raise ValueError(f"不支持的格式: {fmt}，支持: win/ico, mac/icns, auto")
        return fmt_lower

    suffix = output.suffix.lower().lstrip(".")
    if suffix == "ico":
        return "ico"
    if suffix == "icns":
        return "icns"
    raise ValueError("无法推断目标格式，请使用 --format win/mac 指定，或让输出路径以 .ico / .icns 结尾")


def _default_sizes_for(fmt: str) -> tuple[tuple[int, int], ...]:
    """返回指定格式的默认尺寸列表。"""
    return SVG_TO_ICO_MAC_SIZES if fmt in ("mac", "darwin", "icns") else SVG_TO_ICO_WIN_SIZES


def _resolve_format_alias(fmt: str) -> str:
    """将格式别名统一到规范名 ``windows`` / ``mac``。"""
    if fmt in ("win", "windows", "ico"):
        return "windows"
    return "mac"


def _render_svg_to_png(svg_path: Path, max_size: int):
    """用 cairosvg 将 SVG 渲染为 RGBA 位图。"""
    import io

    png_bytes = cairosvg.svg2png(url=str(svg_path), output_width=max_size, output_height=max_size)
    assert isinstance(png_bytes, bytes)
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def icon_build(
    input_path: Path,
    output_path: Path,
    fmt: str | None = None,
    sizes: list[int] | None = None,
) -> tuple[str, list[tuple[int, int]]]:
    """将 SVG 转换为 ICO / ICNS 图标文件。"""
    if not input_path.exists():
        raise FileNotFoundError(f"SVG 文件不存在: {input_path}")

    resolved_fmt = _detect_format(output_path, fmt)
    default_sizes = _default_sizes_for(resolved_fmt)
    actual_sizes = _parse_sizes(sizes) if sizes is not None else list(default_sizes)

    if not actual_sizes:
        raise ValueError("尺寸列表为空，请至少指定一个尺寸")

    max_size = max(s for s, _ in actual_sizes)
    render_size = max(max_size, 32)
    render_size = min(render_size, _MAX_RENDER_SIZE)

    img = _render_svg_to_png(input_path, render_size)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    save_format = "ICO" if resolved_fmt in ("win", "windows", "ico") else "ICNS"
    img.save(output_path, format=save_format, sizes=actual_sizes)

    canonical = _resolve_format_alias(resolved_fmt)
    return canonical, actual_sizes


@action(
    "img2ico_gen",
    param_help={
        "input_path": "输入 SVG 文件路径",
        "output_path": "输出图标文件路径（.ico 或 .icns）",
        "format": "目标格式：win 或 mac（默认根据输出后缀推断，再默认 Windows）",
        "sizes": "自定义尺寸（不传使用格式默认）",
    },
)
def img2ico_gen(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    sizes: list[int] | None = None,
) -> None:
    """SVG 转 Windows ICO 或 macOS ICNS 图标（gen 子命令）。"""
    if not _require_deps():
        return

    try:
        canonical, used_sizes = icon_build(input_path, output_path, fmt=format, sizes=sizes)
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}")
        return

    print(f"图标已生成: {output_path}")
    print(f"格式: {canonical}")
    print(f"包含 {len(used_sizes)} 种尺寸: {', '.join(f'{w}x{h}' for w, h in used_sizes)}")


# ============================================================================
# imagetool 公共逻辑
# ============================================================================


def _require_pil() -> bool:
    """Pillow 未安装时打印提示，返回是否可用。"""
    if not HAS_PIL:
        print("未安装 Pillow 库，请安装: pip install fcmd[img]")
        return False
    return True


def _save_image(img: Any, output: Path, fmt: str | None = None, quality: int = 85) -> None:
    """保存图片，自动处理 JPEG 不支持 alpha 通道的情况。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    save_fmt = fmt or output.suffix.lstrip(".").upper()
    if save_fmt == "JPG":
        save_fmt = "JPEG"
    if save_fmt == "JPEG":
        img = img.convert("RGB")
    if save_fmt in ("JPEG", "WEBP"):
        img.save(output, format=save_fmt, quality=quality)
    else:
        img.save(output, format=save_fmt)


def _load_font(size: int):
    """加载字体，优先 truetype，失败回退默认字体。"""
    candidates = ("DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf")
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _resolve_position(
    position: str,
    img_size: tuple[int, int],
    text_w: int,
    text_h: int,
    margin: int,
) -> tuple[int, int]:
    """根据位置描述符计算水印坐标。"""
    w, h = img_size
    pos_map = {
        "top-left": (margin, margin),
        "top-right": (w - text_w - margin, margin),
        "bottom-left": (margin, h - text_h - margin),
        "bottom-right": (w - text_w - margin, h - text_h - margin),
        "center": ((w - text_w) // 2, (h - text_h) // 2),
    }
    return pos_map.get(position, pos_map["bottom-right"])


def _print_exif(exif: Any) -> None:
    """打印 EXIF 标签。"""
    if exif:
        for tag, value in exif.items():
            print(f"  {tag}: {value}")
    else:
        print("  (无 EXIF 数据)")


def _apply_exif_modifications(exif: Any, set_items: list[str] | None, clear: bool) -> bool:
    """应用 EXIF 修改（clear + set），返回是否有改动。"""
    if clear:
        for tag in list(exif.keys()):
            del exif[tag]
    if set_items:
        for item in set_items:
            _apply_single_exif_set(exif, item)
    return bool(set_items or clear)


def _apply_single_exif_set(exif: Any, item: str) -> None:
    """解析并应用单个 KEY=VALUE 设置项。"""
    if "=" not in item:
        print(f"跳过无效项 (缺少 =): {item}")
        return
    key_str, value = item.split("=", 1)
    try:
        tag = int(key_str)
    except ValueError:
        print(f"跳过无效标签号: {key_str}")
        return
    exif[tag] = value


def _save_exif(img: Any, exif: Any, output_path: Path) -> None:
    """保存图片与 EXIF。"""
    exif_bytes = exif.tobytes() if exif else b""
    img.save(output_path, exif=exif_bytes)
    print(f"EXIF 已保存: {output_path}")


def _print_histogram_buckets(channel_hist: list[int], buckets: int, name: str) -> None:
    """将单通道 256 桶直方图聚合为指定桶数并打印。"""
    bucket_size = max(1, len(channel_hist) // buckets)
    print(f"  {name}:")
    for i in range(buckets):
        start = i * bucket_size
        end = min((i + 1) * bucket_size, len(channel_hist))
        count = sum(channel_hist[start:end])
        slice_max = max(channel_hist[start:end] or [1])
        bar = "#" * min(40, count * 40 // max(1, slice_max))
        print(f"    [{start:3d}-{end:3d}] {count:>8d} {bar}")


# ---------------------------------------------------------------------- #
# imagetool 公共函数（原 __all__ 导出，保持签名兼容）
# ---------------------------------------------------------------------- #


def image_resize(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int | None = None,
    keep_ratio: bool = True,
) -> None:
    """调整图片尺寸。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        if keep_ratio:
            target_height = height if height is not None else width
            img.thumbnail((width, target_height))
        else:
            if height is None:
                height = width
            img = img.resize((width, height))
        _save_image(img, output_path)
        print(f"调整尺寸完成: {output_path} ({img.size[0]}x{img.size[1]})")
    finally:
        img.close()


def image_crop(  # noqa: PLR0913
    input_path: Path,
    output_path: Path,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> None:
    """裁剪图片到指定矩形。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        cropped = img.crop((left, top, right, bottom))
        _save_image(cropped, output_path)
        print(f"裁剪完成: {output_path} ({right - left}x{bottom - top})")
    finally:
        img.close()


def image_rotate(
    input_path: Path,
    output_path: Path,
    degrees: float,
    expand: bool = False,
) -> None:
    """旋转图片。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        rotated = img.rotate(degrees, expand=expand)
        _save_image(rotated, output_path)
        print(f"旋转完成: {output_path} ({degrees}度)")
    finally:
        img.close()


def image_flip(
    input_path: Path,
    output_path: Path,
    direction: str = "horizontal",
) -> None:
    """翻转图片。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        method = Image.Transpose.FLIP_LEFT_RIGHT if direction == "horizontal" else Image.Transpose.FLIP_TOP_BOTTOM
        flipped = img.transpose(method)
        _save_image(flipped, output_path)
        print(f"翻转完成: {output_path} ({direction})")
    finally:
        img.close()


def image_convert(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    quality: int = 85,
) -> None:
    """转换图片格式。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        _save_image(img, output_path, fmt=format, quality=quality)
        actual_fmt = format or output_path.suffix.lstrip(".").upper()
        print(f"格式转换完成: {output_path} ({actual_fmt})")
    finally:
        img.close()


def image_watermark(  # noqa: PLR0913
    input_path: Path,
    output_path: Path,
    text: str,
    position: str = "bottom-right",
    opacity: float = 0.5,
    font_size: int = 32,
) -> None:
    """添加文字水印。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        img = img.convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font = _load_font(font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = int(bbox[2] - bbox[0])
        text_h = int(bbox[3] - bbox[1])
        margin = 10
        x, y = _resolve_position(position, img.size, text_w, text_h, margin)

        alpha = int(255 * max(0.0, min(1.0, opacity)))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))
        result = Image.alpha_composite(img, overlay)
        _save_image(result, output_path)
        print(f"水印添加完成: {output_path}")
    finally:
        img.close()


def image_compress(
    input_path: Path,
    output_path: Path,
    quality: int = 85,
) -> None:
    """压缩图片（重新编码以减小体积）。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        fmt = input_path.suffix.lstrip(".").upper()
        _save_image(img, output_path, fmt=fmt, quality=quality)
        in_size = input_path.stat().st_size
        out_size = output_path.stat().st_size
        ratio = (1 - out_size / in_size) * 100 if in_size > 0 else 0.0
        print(f"压缩完成: {output_path} (原 {in_size}B → 新 {out_size}B, 节省 {ratio:.1f}%)")
    finally:
        img.close()


def image_info(input_path: Path, json: bool = False) -> None:
    """打印图片信息（尺寸/格式/模式/EXIF 摘要）。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        exif = img.getexif()
        exif_count = len(exif) if exif else 0

        import json as json_mod

        data = {
            "path": str(input_path),
            "format": img.format,
            "mode": img.mode,
            "width": img.size[0],
            "height": img.size[1],
            "exif_tags": exif_count,
        }
        if json:
            print(json_mod.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"文件: {data['path']}")
            print(f"格式: {data['format']}")
            print(f"模式: {data['mode']}")
            print(f"尺寸: {data['width']}x{data['height']}")
            print(f"EXIF 标签数: {data['exif_tags']}")
    finally:
        img.close()


def image_exif(
    input_path: Path,
    output_path: Path | None = None,
    show: bool = True,
    set: list[str] | None = None,
    clear: bool = False,
) -> None:
    """读取或修改 EXIF 元数据。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        exif = img.getexif()

        if show:
            _print_exif(exif)

        modified = _apply_exif_modifications(exif, set, clear)
        if modified:
            _save_exif(img, exif, output_path if output_path is not None else input_path)
    finally:
        img.close()


def image_histogram(
    input_path: Path,
    channel: str = "rgb",
) -> None:
    """打印颜色直方图统计（每通道 8 桶）。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        hist = img.histogram()
        buckets = 8

        if channel == "luminance":
            gray = img.convert("L")
            gray_hist = gray.histogram()
            print("亮度直方图 (8 桶):")
            _print_histogram_buckets(gray_hist, buckets, "L")
        else:
            print("RGB 直方图 (8 桶):")
            if len(hist) == 256:
                _print_histogram_buckets(hist, buckets, "L")
            else:
                for idx, name in enumerate(("R", "G", "B")):
                    start = idx * 256
                    _print_histogram_buckets(hist[start : start + 256], buckets, name)
    finally:
        img.close()


def image_colors(
    input_path: Path,
    count: int = 5,
) -> None:
    """提取并打印主色调。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        img = img.convert("RGB")
        quantized = img.quantize(colors=count)
        palette = quantized.getpalette()
        if palette is None:  # pragma: no cover - quantize() 总会生成调色板，防御性守卫
            print("无法提取调色板")
            return

        actual_count = len(palette) // 3
        print(f"主色调 (前 {min(count, actual_count)} 色):")
        for i in range(min(count, actual_count)):
            r = palette[i * 3]
            g = palette[i * 3 + 1]
            b = palette[i * 3 + 2]
            hex_color = f"#{r:02X}{g:02X}{b:02X}"
            print(f"  {i + 1}. {hex_color}  rgb({r}, {g}, {b})")
    finally:
        img.close()


# ---------------------------------------------------------------------- #
# imagetool DSL 动作
# ---------------------------------------------------------------------- #


@action(
    "imagetool_r",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "width": "目标宽度",
        "height": "目标高度（keep_ratio=True 时仅作上限，None 表示按宽度等比）",
        "keep_ratio": "是否保持宽高比（默认 True）",
    },
)
def imagetool_r(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int | None = None,
    keep_ratio: bool = True,
) -> None:
    """调整图片尺寸（r 子命令）。"""
    image_resize(input_path, output_path, width, height, keep_ratio)


@action(
    "imagetool_c",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "left": "裁剪矩形左边界",
        "top": "裁剪矩形上边界",
        "right": "裁剪矩形右边界",
        "bottom": "裁剪矩形下边界",
    },
)
def imagetool_c(  # noqa: PLR0913
    input_path: Path,
    output_path: Path,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> None:
    """裁剪图片（c 子命令）。"""
    image_crop(input_path, output_path, left, top, right, bottom)


@action(
    "imagetool_ro",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "degrees": "旋转角度（正值逆时针）",
        "expand": "是否扩展画布以容纳整个旋转后的图片（默认 False）",
    },
)
def imagetool_ro(
    input_path: Path,
    output_path: Path,
    degrees: float,
    expand: bool = False,
) -> None:
    """旋转图片（ro 子命令）。"""
    image_rotate(input_path, output_path, degrees, expand)


@action(
    "imagetool_fl",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "direction": "翻转方向：horizontal 或 vertical（默认 horizontal）",
    },
)
def imagetool_fl(
    input_path: Path,
    output_path: Path,
    direction: str = "horizontal",
) -> None:
    """翻转图片（fl 子命令）。"""
    image_flip(input_path, output_path, direction)


@action(
    "imagetool_cv",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径（后缀决定格式）",
        "format": "目标格式（如 PNG/JPEG/WEBP），None 时按 output_path 后缀推断",
        "quality": "压缩质量（1-100，仅对 JPEG/WEBP 有效，默认 85）",
    },
)
def imagetool_cv(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    quality: int = 85,
) -> None:
    """格式转换（cv 子命令）。"""
    image_convert(input_path, output_path, format, quality)


@action(
    "imagetool_wm",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "text": "水印文字",
        "position": "水印位置：top-left/top-right/bottom-left/bottom-right/center（默认 bottom-right）",
        "opacity": "不透明度（0.0-1.0，默认 0.5）",
        "font_size": "字体大小（默认 32）",
    },
)
def imagetool_wm(  # noqa: PLR0913
    input_path: Path,
    output_path: Path,
    text: str,
    position: str = "bottom-right",
    opacity: float = 0.5,
    font_size: int = 32,
) -> None:
    """添加文字水印（wm 子命令）。"""
    image_watermark(input_path, output_path, text, position, opacity, font_size)


@action(
    "imagetool_cp",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "quality": "压缩质量（1-100，默认 85）",
    },
)
def imagetool_cp(
    input_path: Path,
    output_path: Path,
    quality: int = 85,
) -> None:
    """压缩图片（cp 子命令）。"""
    image_compress(input_path, output_path, quality)


@action(
    "imagetool_i",
    param_help={
        "input_path": "输入图片路径",
        "json": "是否以 JSON 格式输出（默认 False=纯文本）",
    },
)
def imagetool_i(input_path: Path, json: bool = False) -> None:
    """查看图片信息（i 子命令）。"""
    image_info(input_path, json)


@action(
    "imagetool_e",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出路径（None 时原地覆盖；仅 show=True 时可省略）",
        "show": "是否打印 EXIF 标签（默认 True）",
        "set": "设置标签，格式 KEY=VALUE（KEY 为数字标签号）",
        "clear": "清空所有 EXIF 标签（在 set 之前执行）",
    },
)
def imagetool_e(
    input_path: Path,
    output_path: Path | None = None,
    show: bool = True,
    set: list[str] | None = None,
    clear: bool = False,
) -> None:
    """读取/修改 EXIF（e 子命令）。"""
    image_exif(input_path, output_path, show, set, clear)


@action(
    "imagetool_hi",
    param_help={
        "input_path": "输入图片路径",
        "channel": "通道：rgb 或 luminance（默认 rgb）",
    },
)
def imagetool_hi(
    input_path: Path,
    channel: str = "rgb",
) -> None:
    """颜色直方图（hi 子命令）。"""
    image_histogram(input_path, channel)


@action(
    "imagetool_co",
    param_help={
        "input_path": "输入图片路径",
        "count": "提取的颜色数（默认 5）",
    },
)
def imagetool_co(
    input_path: Path,
    count: int = 5,
) -> None:
    """提取主色调（co 子命令）。"""
    image_colors(input_path, count)


# ============================================================================
# pdftool 公共逻辑
# ============================================================================

from fcmd.models.pdf import MergeSpec, PageSelection, SplitSpec  # noqa: E402


def _require_pymupdf() -> bool:
    """PyMuPDF 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYMUPDF:
        print("未安装 PyMuPDF 库，请安装: pip install fcmd[pdf]")
        return False
    global fitz  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    import fitz

    return True


def _require_pypdf() -> bool:
    """pypdf 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYPDF:
        print("未安装 pypdf 库，请安装: pip install fcmd[pdf]")
        return False
    global pypdf  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    import pypdf

    return True


def _resolve_page_indices(pages: str, page_count: int) -> tuple[int, ...] | None:
    """解析页码筛选表达式为 0-based 页索引序列。"""
    try:
        return PageSelection.parse(pages).resolve(page_count)
    except ValueError as exc:
        print(f"错误: {exc}")
        return None


# ---------------------------------------------------------------------- #
# pdftool 公共函数（原 __all__ 导出）
# ---------------------------------------------------------------------- #


def pdf_merge(
    input_paths: list[Path],
    output_path: Path = Path("merged.pdf"),
    mode: Literal["concat", "interleave"] = "concat",
    orders: list[str] | None = None,
    pages: list[str] | None = None,
) -> None:
    """合并多个 PDF 文件。"""
    if not _require_pypdf():
        return

    order_list = list(orders or [])
    page_list = list(pages or [])
    kept: list[tuple[Path, str, str]] = []
    for i, input_path in enumerate(input_paths):
        if input_path.exists():
            kept_order = order_list[i] if i < len(order_list) else "forward"
            kept_pages = page_list[i] if i < len(page_list) else ""
            kept.append((input_path, kept_order, kept_pages))

    try:
        spec = MergeSpec.from_cli(
            [item[0] for item in kept],
            mode,
            [item[1] for item in kept],
            [item[2] for item in kept],
        )
    except ValueError as exc:
        print(f"错误: {exc}")
        return

    readers = [pypdf.PdfReader(str(item[0])) for item in kept]
    plan = spec.resolve([len(reader.pages) for reader in readers])

    writer = pypdf.PdfWriter()
    for input_idx, page_idx in plan:
        writer.add_page(readers[input_idx].pages[page_idx])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"合并完成: {output_path}")


def pdf_split(
    input_path: Path,
    output_dir: Path = Path("split"),
    order: str = "forward",
    every: int = 1,
    groups: str = "",
) -> None:
    """拆分 PDF 文件。"""
    if not _require_pypdf():
        return

    try:
        spec = SplitSpec.parse(order, every, groups)
    except ValueError as exc:
        print(f"错误: {exc}")
        return

    reader = pypdf.PdfReader(str(input_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    chunks = spec.resolve(len(reader.pages))
    for seq, chunk in enumerate(chunks, start=1):
        writer = pypdf.PdfWriter()
        for page_idx in chunk:
            writer.add_page(reader.pages[page_idx])
        suffix = "page" if len(chunk) == 1 else "part"
        output_file = output_dir / f"{input_path.stem}_{suffix}_{seq}.pdf"
        with output_file.open("wb") as f:
            writer.write(f)

    print(f"拆分完成: {output_dir} (共 {len(chunks)} 份)")


def pdf_compress(input_path: Path, output_path: Path = Path("compressed.pdf")) -> None:
    """压缩 PDF 文件。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    original_size = input_path.stat().st_size
    new_size = output_path.stat().st_size
    ratio = (1 - new_size / original_size) * 100 if original_size > 0 else 0.0
    print(f"压缩完成: {output_path} (缩小 {ratio:.1f}%)")


def pdf_encrypt(
    input_path: Path,
    output_path: Path = Path("encrypted.pdf"),
    password: str = "",
) -> None:
    """加密 PDF 文件。"""
    if not password:
        print("错误: --password 为必填参数")
        return
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    writer = pypdf.PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    writer.encrypt(user_password=password, owner_password=password, use_128bit=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"加密完成: {output_path}")


def pdf_decrypt(
    input_path: Path,
    output_path: Path = Path("decrypted.pdf"),
    password: str = "",
) -> None:
    """解密 PDF 文件。"""
    if not password:
        print("错误: --password 为必填参数")
        return
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    if reader.is_encrypted:
        reader.decrypt(password)

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"解密完成: {output_path}")


def pdf_extract_text(
    input_path: Path,
    output_path: Path = Path("output.txt"),
    pages: str = "",
) -> None:
    """提取 PDF 文本。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        parts = [str(doc[i].get_text()) + "\n\n" for i in indices]
    finally:
        doc.close()
    text = "".join(parts)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"文本提取完成: {output_path}")


def pdf_extract_images(
    input_path: Path,
    output_dir: Path = Path("images"),
    pages: str = "",
) -> None:
    """提取 PDF 图片。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    indices = _resolve_page_indices(pages, doc.page_count)
    if indices is None:
        doc.close()
        return

    image_count = 0
    try:
        for page_idx in indices:
            page = doc[page_idx]
            images = page.get_images(full=True)
            for img_idx, img in enumerate(images):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_data = base_image["image"]
                image_ext = base_image["ext"]
                image_path = output_dir / f"page_{page_idx + 1}_img_{img_idx + 1}.{image_ext}"
                image_path.write_bytes(image_data)
                image_count += 1
    finally:
        doc.close()
    print(f"图片提取完成: {output_dir} (共 {image_count} 张)")


def pdf_add_watermark(
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
) -> None:
    """添加 PDF 水印。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        for page_idx in indices:
            page = doc[page_idx]
            rect = page.rect
            text_width = fitz.get_text_length(text, fontsize=48)
            x = (rect.width - text_width) / 2
            y = rect.height / 2
            page.insert_text((x, y), text, fontsize=48, rotate=0, color=(0, 0, 0))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()
    print(f"水印添加完成: {output_path}")


def pdf_rotate(
    input_path: Path,
    output_path: Path = Path("rotated.pdf"),
    rotation: int = 90,
    pages: str = "",
) -> None:
    """旋转 PDF 页面。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        for page_idx in indices:
            doc[page_idx].set_rotation(rotation)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()
    print(f"旋转完成: {output_path}")


def pdf_crop(
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
) -> None:
    """裁剪 PDF 页面。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    left, top, right, bottom = margins

    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        for page_idx in indices:
            page = doc[page_idx]
            rect = page.rect
            new_rect = fitz.Rect(
                rect.x0 + left,
                rect.y0 + top,
                rect.x1 - right,
                rect.y1 - bottom,
            )
            page.set_cropbox(new_rect)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()
    print(f"裁剪完成: {output_path}")


def pdf_info(input_path: Path) -> None:
    """显示 PDF 信息。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        print(f"文件: {input_path}")
        print(f"页数: {doc.page_count}")
        meta = doc.metadata or {}
        print(f"标题: {meta.get('title', 'N/A')}")
        print(f"作者: {meta.get('author', 'N/A')}")
        print(f"创建日期: {meta.get('creationDate', 'N/A')}")
        print(f"修改日期: {meta.get('modDate', 'N/A')}")
        print(f"文件大小: {input_path.stat().st_size / 1024:.1f} KB")
    finally:
        doc.close()


def pdf_ocr(  # pragma: no cover - 需系统级 tesseract 可执行文件，测试环境不可用
    input_path: Path,
    output_path: Path = Path("ocr.pdf"),
    lang: str = "chi_sim+eng",
) -> None:
    """PDF OCR 识别。"""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:  # pragma: no cover - 仅在未安装 ocr extras 时触发
        print("未安装 OCR 相关库，请安装: pip install fcmd[ocr]")
        return

    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    new_doc = fitz.open()
    try:
        for page in doc:
            pix = page.get_pixmap()
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            try:
                ocr_text = pytesseract.image_to_string(img, lang=lang)
            finally:
                img.close()

            new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, pixmap=pix)
            text_rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
            new_page.insert_textbox(text_rect, str(ocr_text), fontname="china-ss", fontsize=11)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        new_doc.save(str(output_path))
    finally:
        new_doc.close()
        doc.close()
    print(f"OCR 识别完成: {output_path}")


def pdf_reorder(input_path: Path, output_path: Path, order: list[int]) -> None:
    """重排 PDF 页面顺序。"""
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    writer = pypdf.PdfWriter()

    for page_num in order:
        if 0 <= page_num < len(reader.pages):
            writer.add_page(reader.pages[page_num])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"重排完成: {output_path}")


def pdf_to_images(
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
) -> None:
    """PDF 转图片。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    indices = _resolve_page_indices(pages, doc.page_count)
    if indices is None:
        doc.close()
        return

    try:
        for page_idx in indices:
            pix = doc[page_idx].get_pixmap(dpi=dpi)
            image_path = output_dir / f"{input_path.stem}_page_{page_idx + 1}.png"
            pix.save(str(image_path))
    finally:
        doc.close()
    print(f"转换完成: {output_dir}")


def pdf_repair(input_path: Path, output_path: Path = Path("repaired.pdf")) -> None:
    """修复 PDF 文件。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    print(f"修复完成: {output_path}")


# ---------------------------------------------------------------------- #
# pdftool DSL 动作
# ---------------------------------------------------------------------- #


@action(
    "pdftool_m",
    param_help={
        "input_paths": "输入 PDF 文件列表",
        "output_path": "输出文件路径（默认 merged.pdf）",
        "mode": "合并模式：concat（默认）或 interleave",
        "orders": "各文件页序（forward/reverse 或 f/r），按位置对应输入文件",
        "pages": "各文件页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_m(
    input_paths: list[Path],
    output_path: Path = Path("merged.pdf"),
    mode: Literal["concat", "interleave"] = "concat",
    orders: list[str] | None = None,
    pages: list[str] | None = None,
) -> None:
    """合并多个 PDF 文件（m 子命令）。"""
    pdf_merge(input_paths, output_path, mode, orders, pages)


@action(
    "pdftool_s",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_dir": "输出目录（默认 split）",
        "order": "页序：forward/f 正序（默认），reverse/r 倒序",
        "every": "固定步长，每份页数（默认 1 = 每页一份）",
        "groups": "自定义分组表达式（分号分隔的页码选择，如 1-2;3,4,5）",
    },
)
def pdftool_s(
    input_path: Path,
    output_dir: Path = Path("split"),
    order: str = "forward",
    every: int = 1,
    groups: str = "",
) -> None:
    """拆分 PDF 文件（s 子命令）。"""
    pdf_split(input_path, output_dir, order, every, groups)


@action(
    "pdftool_c",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 compressed.pdf）",
    },
)
def pdftool_c(input_path: Path, output_path: Path = Path("compressed.pdf")) -> None:
    """压缩 PDF 文件（c 子命令）。"""
    pdf_compress(input_path, output_path)


@action(
    "pdftool_e",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 encrypted.pdf）",
        "password": "密码（必填）",
    },
)
def pdftool_e(
    input_path: Path,
    output_path: Path = Path("encrypted.pdf"),
    password: str = "",
) -> None:
    """加密 PDF 文件（e 子命令）。"""
    pdf_encrypt(input_path, output_path, password)


@action(
    "pdftool_d",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 decrypted.pdf）",
        "password": "密码（必填）",
    },
)
def pdftool_d(
    input_path: Path,
    output_path: Path = Path("decrypted.pdf"),
    password: str = "",
) -> None:
    """解密 PDF 文件（d 子命令）。"""
    pdf_decrypt(input_path, output_path, password)


@action(
    "pdftool_xt",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 output.txt）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_xt(
    input_path: Path,
    output_path: Path = Path("output.txt"),
    pages: str = "",
) -> None:
    """提取 PDF 文本（xt 子命令）。"""
    pdf_extract_text(input_path, output_path, pages)


@action(
    "pdftool_xi",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_dir": "输出目录（默认 images）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_xi(
    input_path: Path,
    output_dir: Path = Path("images"),
    pages: str = "",
) -> None:
    """提取 PDF 图片（xi 子命令）。"""
    pdf_extract_images(input_path, output_dir, pages)


@action(
    "pdftool_w",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 watermarked.pdf）",
        "text": "水印文字（默认 CONFIDENTIAL）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_w(
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
) -> None:
    """添加 PDF 水印（w 子命令）。"""
    pdf_add_watermark(input_path, output_path, text, pages)


@action(
    "pdftool_r",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 rotated.pdf）",
        "rotation": "旋转角度（默认 90）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_r(
    input_path: Path,
    output_path: Path = Path("rotated.pdf"),
    rotation: int = 90,
    pages: str = "",
) -> None:
    """旋转 PDF 页面（r 子命令）。"""
    pdf_rotate(input_path, output_path, rotation, pages)


@action(
    "pdftool_crop",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 cropped.pdf）",
        "margins": "边距（左, 上, 右, 下），默认 (10, 10, 10, 10)",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_crop(
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
) -> None:
    """裁剪 PDF 页面（crop 子命令）。"""
    pdf_crop(input_path, output_path, margins, pages)


@action("pdftool_i", param_help={"input_path": "输入 PDF 文件"})
def pdftool_i(input_path: Path) -> None:
    """查看 PDF 信息（i 子命令）。"""
    pdf_info(input_path)


@action(
    "pdftool_ocr",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 ocr.pdf）",
        "lang": "识别语言（默认 chi_sim+eng）",
    },
)
def pdftool_ocr(  # pragma: no cover
    input_path: Path,
    output_path: Path = Path("ocr.pdf"),
    lang: str = "chi_sim+eng",
) -> None:
    """PDF OCR 识别（ocr 子命令）。"""
    pdf_ocr(input_path, output_path, lang)


@action(
    "pdftool_reorder",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径",
        "order": "页面顺序列表（0-based）",
    },
)
def pdftool_reorder(input_path: Path, output_path: Path, order: list[int]) -> None:
    """重排 PDF 页面（reorder 子命令）。"""
    pdf_reorder(input_path, output_path, order)


@action(
    "pdftool_img",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_dir": "输出目录（默认 images）",
        "dpi": "DPI（默认 300）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_img(
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
) -> None:
    """PDF 转图片（img 子命令）。"""
    pdf_to_images(input_path, output_dir, dpi, pages)


@action(
    "pdftool_repair",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 repaired.pdf）",
    },
)
def pdftool_repair(input_path: Path, output_path: Path = Path("repaired.pdf")) -> None:
    """修复 PDF 文件（repair 子命令）。"""
    pdf_repair(input_path, output_path)
