"""img2ico - 图像/SVG 转 Windows ICO 或 macOS ICNS 图标。

支持单文件与目录批量转换，可自定义尺寸与重采样质量。

示例
----
    fcmd img2ico gen logo.png logo.ico
    fcmd img2ico gen icons/ out/ --format win --sizes 16,32,256
    fcmd img2ico gen logo.svg app.icns --quality high
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import fcmd as fx
from fcmd.infras import media as med
from fcmd.tools._deps import missing_dependency

__all__ = ["img2ico_gen"]


def _require_deps() -> bool:
    """检查 PIL + resvg-py 依赖，缺失时打印提示并返回 False。"""
    if not med.HAS_PIL:
        return missing_dependency("Pillow", "img")
    if not med.HAS_RESVG:
        return missing_dependency("resvg-py", "svg")
    med.ensure_pil()
    med.ensure_resvg()
    return True


@fx.tool(
    "img2ico",
    subcommand="gen",
    help="图像/SVG 转图标文件（支持目录批量）",
    param_help={
        "input_path": "输入图像/SVG 文件或目录路径",
        "output_path": "输出图标文件或目录（目录输入时输出到该目录）",
        "format": "目标格式：win 或 mac（默认根据输出后缀推断，再默认 Windows）",
        "sizes": "自定义尺寸（逗号或空格分隔，不传使用格式默认）",
        "quality": "重采样质量：low / medium / high（默认 high）",
    },
)
def img2ico_gen(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    sizes: list[str] | None = None,
    quality: Literal["low", "medium", "high"] = "high",
) -> None:
    """图像/SVG 转 Windows ICO 或 macOS ICNS 图标（gen 子命令，支持目录批量）。"""
    if not _require_deps():
        return

    try:
        parsed_sizes = med.parse_size_tokens(sizes)
        result = med.convert_images(
            input_path,
            output_path,
            fmt=format,
            sizes=parsed_sizes,
            quality=quality,
        )
    except (OSError, ValueError) as exc:
        print(f"错误: {exc}")
        return

    for image_name, message in result.skipped:
        print(f"跳过 {image_name}: {message}")

    if not result.outputs:
        print(f"未转换任何文件: {input_path}")
        return
    if input_path.is_dir():
        print(f"批量转换完成: {input_path}（{len(result.outputs)} 个图标）")
        for output in result.outputs:
            print(f"  {output}")
    else:
        print(f"图标已生成: {result.outputs[0]}")
    print(f"格式: {result.canonical}")
    print(f"包含 {len(result.used_sizes)} 种尺寸: {', '.join(f'{w}x{h}' for w, h in result.used_sizes)}")


@fx.main("img2ico")
def main() -> None:
    pass  # pragma: no cover - @fx.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
