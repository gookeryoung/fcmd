"""img2ico - SVG 转 Windows/Mac 图标工具。

将 SVG 矢量图渲染为多尺寸 Windows ICO 或 macOS ICNS 图标文件。
Windows 格式默认尺寸参考 GreenFish Icon Editor 的设计，Mac 格式
尺寸遵循 Apple Human Interface Guidelines 标准。

依赖 ``fcmd[img]``（Pillow）和 ``fcmd[svg]``（cairosvg）extra。

示例
----
    fcmd img2ico svg/logo.svg icon.ico              # 默认 Windows ICO
    fcmd img2ico svg/logo.svg icon.icns --format mac  # Mac ICNS
    fcmd img2ico svg/logo.svg out/ --format auto    # 根据输出后缀推断格式
    fcmd img2ico svg/logo.svg icon.ico --sizes 16 32 48 256  # 自定义尺寸
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fcmd
from fcmd.apis.errors import FcmdError

__all__ = [
    "SVG_TO_ICO_MAC_SIZES",
    "SVG_TO_ICO_WIN_SIZES",
    "icon_build",
]

# ============================================================================
# 尺寸常量
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


# ============================================================================
# 可选依赖检查
# ============================================================================

if TYPE_CHECKING:
    from PIL import Image

try:
    from PIL import Image

    _HAS_PIL = True
except ImportError:  # pragma: no cover - 仅在未安装 pillow 时触发
    _HAS_PIL = False

try:
    import cairosvg  # type: ignore[import-untyped]

    _HAS_CAIROSVG = True
except ImportError:  # pragma: no cover - 仅在未安装 cairosvg 时触发
    _HAS_CAIROSVG = False


def _require_deps() -> bool:
    """检查 PIL + cairosvg 依赖，缺失时打印提示并返回 False。"""
    missing: list[str] = []
    if not _HAS_PIL:
        missing.append("pillow (安装: fcmd[img])")
    if not _HAS_CAIROSVG:
        missing.append("cairosvg (安装: fcmd[svg])")
    if missing:
        print(f"缺少依赖: {', '.join(missing)}")
        return False
    return True


# ============================================================================
# 核心转换逻辑
# ============================================================================


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


def _render_svg_to_png(svg_path: Path, max_size: int) -> Image.Image:
    """用 cairosvg 将 SVG 渲染为 RGBA 位图。

    Parameters
    ----------
    svg_path:
        SVG 文件路径
    max_size:
        渲染的像素尺寸（正方形）

    Returns
    -------
    Image.Image
        RGBA 模式的 Pillow 图像
    """
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
    """将 SVG 转换为 ICO / ICNS 图标文件。

    Parameters
    ----------
    input_path:
        输入 SVG 文件路径
    output_path:
        输出图标文件路径（后缀应为 ``.ico`` / ``.icns``）
    fmt:
        目标格式：``"win"``/``"ico"``（默认 Windows）、``"mac"``/``"icns"``，
        ``None`` 时按输出路径后缀推断
    sizes:
        自定义尺寸列表（如 ``[16, 32, 48, 256]``），``None`` 时使用默认尺寸

    Returns
    -------
    tuple[str, list[tuple[int, int]]]
        (格式描述, 实际使用的尺寸列表)

    Raises
    ------
    ValueError
        格式非法、输出路径后缀无法推断或尺寸列表为空时
    FileNotFoundError
        输入文件不存在时
    """
    if not input_path.exists():
        raise FileNotFoundError(f"SVG 文件不存在: {input_path}")

    resolved_fmt = _detect_format(output_path, fmt)
    default_sizes = _default_sizes_for(resolved_fmt)
    actual_sizes = _parse_sizes(sizes) if sizes is not None else list(default_sizes)

    if not actual_sizes:
        raise ValueError("尺寸列表为空，请至少指定一个尺寸")

    max_size = max(s for s, _ in actual_sizes)
    render_size = max(max_size, 32)  # 过小尺寸也至少渲染到 32px 保证清晰度
    render_size = min(render_size, _MAX_RENDER_SIZE)

    img = _render_svg_to_png(input_path, render_size)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    save_format = "ICO" if resolved_fmt in ("win", "windows", "ico") else "ICNS"
    img.save(output_path, format=save_format, sizes=actual_sizes)

    canonical = _resolve_format_alias(resolved_fmt)
    return canonical, actual_sizes


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("img2ico", subcommand="gen", help="SVG 转图标文件")
def icon_gen(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    sizes: list[int] | None = None,
) -> None:
    """SVG 转 Windows ICO 或 macOS ICNS 图标。

    Parameters
    ----------
    input_path:
        输入 SVG 文件路径
    output_path:
        输出图标文件路径（``.ico`` 或 ``.icns``）
    format:
        目标格式：``win`` / ``mac``（默认根据输出后缀推断，再默认 Windows）
    sizes:
        自定义尺寸（不传使用格式默认）
    """
    if not _require_deps():
        raise FcmdError("缺少依赖，请安装 fcmd[img,svg]")

    try:
        canonical, used_sizes = icon_build(input_path, output_path, fmt=format, sizes=sizes)
    except (FileNotFoundError, ValueError) as exc:
        raise FcmdError(str(exc)) from exc

    print(f"图标已生成: {output_path}")
    print(f"格式: {canonical}")
    print(f"包含 {len(used_sizes)} 种尺寸: {', '.join(f'{w}x{h}' for w, h in used_sizes)}")


@fcmd.main("img2ico")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
