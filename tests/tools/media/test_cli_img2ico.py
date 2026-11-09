"""img2ico 工具测试。

验证 ``fcmd.tools.media.mediabase`` 模块：
- 工具注册与常量
- icon_build 核心转换函数（SVG / 位图 / ICO / ICNS）
- 尺寸解析、质量档位、输出路径语义
- 目录批量转换与 CLI 子命令（run_tool）
- 边界分支与错误处理
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.media.mediabase import (
    IMG2ICO_IMAGE_EXTENSIONS,
    SVG_TO_ICO_MAC_SIZES,
    SVG_TO_ICO_WIN_SIZES,
    _resize_for_icon,
    collect_images,
    convert_images,
    icon_build,
    is_supported_image,
    normalize_quality,
    parse_size_tokens,
    resolve_output_target,
)


# ---------------------------------------------------------------------- #
# fixtures: 生成测试 SVG / PNG
# ---------------------------------------------------------------------- #
@pytest.fixture
def sample_svg(tmp_path: Path) -> Path:
    """生成简单 SVG 文件。"""
    content = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<circle cx="50" cy="50" r="40" fill="red"/>'
        "</svg>"
    )
    p = tmp_path / "icon.svg"
    p.write_text(content)
    return p


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """生成 200x100 的非正方形 PNG（用于宽高比验证）。"""
    from PIL import Image

    p = tmp_path / "wide.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(p)
    return p


# ---------------------------------------------------------------------- #
# 常量与注册验证
# ---------------------------------------------------------------------- #
class TestRegistration:
    """常量与注册验证。"""

    def test_tool_registered(self) -> None:
        """img2ico 应在 _TOOL_REGISTRY 中注册。"""
        assert "img2ico" in _TOOL_REGISTRY

    def test_gen_subcommand_exists(self) -> None:
        """img2ico 应注册 gen 子命令。"""
        subs = _TOOL_REGISTRY["img2ico"]
        assert "gen" in subs

    def test_win_sizes_count(self) -> None:
        """Windows ICO 默认尺寸应为 10 种。"""
        assert len(SVG_TO_ICO_WIN_SIZES) == 10
        # GreenFish Icon Editor 标准
        expected = {16, 24, 32, 40, 48, 64, 72, 96, 128, 256}
        actual = {w for w, _ in SVG_TO_ICO_WIN_SIZES}
        assert actual == expected

    def test_mac_sizes_count(self) -> None:
        """macOS ICNS 默认尺寸应为 6 种。"""
        assert len(SVG_TO_ICO_MAC_SIZES) == 6
        expected = {16, 32, 128, 256, 512, 1024}
        actual = {w for w, _ in SVG_TO_ICO_MAC_SIZES}
        assert actual == expected


# ---------------------------------------------------------------------- #
# icon_build 核心函数
# ---------------------------------------------------------------------- #
class TestIconBuild:
    """icon_build 核心转换函数测试。"""

    def test_build_windows_ico_default(self, sample_svg: Path, tmp_path: Path) -> None:
        """默认格式生成 Windows ICO，应包含全部 10 种尺寸。"""
        out = tmp_path / "out.ico"
        fmt, sizes = icon_build(sample_svg, out)
        assert fmt == "windows"
        assert len(sizes) == 10
        assert out.exists()
        assert out.stat().st_size > 0

    def test_build_mac_icns(self, sample_svg: Path, tmp_path: Path) -> None:
        """显式 --format mac 生成 ICNS。"""
        out = tmp_path / "out.icns"
        fmt, sizes = icon_build(sample_svg, out, fmt="mac")
        assert fmt == "mac"
        assert len(sizes) == 6
        assert out.exists()
        assert out.stat().st_size > 0

    def test_format_alias_win(self, sample_svg: Path, tmp_path: Path) -> None:
        """fmt=win 应等价于 fmt=windows。"""
        out = tmp_path / "out.ico"
        fmt, sizes = icon_build(sample_svg, out, fmt="win")
        assert fmt == "windows"
        assert len(sizes) == 10

    def test_format_alias_ico(self, sample_svg: Path, tmp_path: Path) -> None:
        """fmt=ico 应等价于 fmt=windows。"""
        out = tmp_path / "out.ico"
        fmt, _sizes = icon_build(sample_svg, out, fmt="ico")
        assert fmt == "windows"

    def test_format_alias_darwin(self, sample_svg: Path, tmp_path: Path) -> None:
        """fmt=darwin 应等价于 fmt=mac。"""
        out = tmp_path / "out.icns"
        fmt, _sizes = icon_build(sample_svg, out, fmt="darwin")
        assert fmt == "mac"

    def test_format_infer_by_suffix_ico(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出后缀 .ico 应自动推断为 Windows 格式。"""
        out = tmp_path / "auto.ico"
        fmt, _ = icon_build(sample_svg, out)
        assert fmt == "windows"

    def test_format_infer_by_suffix_icns(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出后缀 .icns 应自动推断为 Mac 格式。"""
        out = tmp_path / "auto.icns"
        fmt, _ = icon_build(sample_svg, out)
        assert fmt == "mac"

    def test_invalid_format_raises(self, sample_svg: Path, tmp_path: Path) -> None:
        """非法格式应抛 ValueError。"""
        out = tmp_path / "out.ico"
        with pytest.raises(ValueError, match="不支持的格式"):
            icon_build(sample_svg, out, fmt="linux")

    def test_custom_sizes(self, sample_svg: Path, tmp_path: Path) -> None:
        """自定义尺寸列表应覆盖默认值。"""
        out = tmp_path / "custom.ico"
        fmt, sizes = icon_build(sample_svg, out, sizes=[16, 32, 64])
        assert fmt == "windows"
        assert sizes == [(16, 16), (32, 32), (64, 64)]
        assert out.exists()

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        """不存在的 SVG 文件应抛 FileNotFoundError。"""
        missing = tmp_path / "nonexistent.svg"
        out = tmp_path / "out.ico"
        with pytest.raises(FileNotFoundError):
            icon_build(missing, out)

    def test_empty_sizes_raises(self, sample_svg: Path, tmp_path: Path) -> None:
        """空尺寸列表应抛 ValueError。"""
        out = tmp_path / "out.ico"
        with pytest.raises(ValueError, match="尺寸列表为空"):
            icon_build(sample_svg, out, sizes=[])

    def test_output_parent_auto_create(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出路径的父目录应自动创建。"""
        out = tmp_path / "subdir" / "nested" / "out.ico"
        icon_build(sample_svg, out)
        assert out.exists()

    def test_no_suffix_no_format_raises(self, sample_svg: Path, tmp_path: Path) -> None:
        """无后缀也未显式指定格式应抛 ValueError。"""
        out = tmp_path / "icon"
        with pytest.raises(ValueError, match="无法推断目标格式"):
            icon_build(sample_svg, out)


# ---------------------------------------------------------------------- #
# 尺寸 / 质量 / 位图输入
# ---------------------------------------------------------------------- #
class TestSizeParsing:
    """尺寸参数解析。"""

    def test_none_returns_none(self) -> None:
        assert parse_size_tokens(None) is None

    def test_comma_and_space(self) -> None:
        assert parse_size_tokens(["16, 32", "256"]) == [16, 32, 256]

    def test_fullwidth_comma(self) -> None:
        assert parse_size_tokens(["16，32，64"]) == [16, 32, 64]

    def test_deduplicate_and_sort(self) -> None:
        assert parse_size_tokens(["32,16,32"]) == [16, 32]

    @pytest.mark.parametrize("items", [[], [""], [" , "]])
    def test_empty_raises(self, items: list[str]) -> None:
        with pytest.raises(ValueError, match="未提供有效"):
            parse_size_tokens(items)

    def test_non_integer_raises(self) -> None:
        with pytest.raises(ValueError, match="尺寸格式无效"):
            parse_size_tokens(["16", "abc"])

    def test_non_positive_raises(self) -> None:
        with pytest.raises(ValueError, match="正整数"):
            parse_size_tokens(["0", "16"])


class TestQuality:
    """质量档位规范化。"""

    @pytest.mark.parametrize(("raw", "expected"), [("high", "high"), (" LOW ", "low"), ("Medium", "medium")])
    def test_valid(self, raw: str, expected: str) -> None:
        assert normalize_quality(raw) == expected

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的质量"):
            normalize_quality("ultra")


class TestRasterInput:
    """位图输入（PNG 等）与宽高比处理。"""

    def test_png_to_ico(self, sample_png: Path, tmp_path: Path) -> None:
        """PNG 输入应生成目标尺寸的 ICO。"""
        out = tmp_path / "from_png.ico"
        fmt, sizes = icon_build(sample_png, out, sizes=[16, 32, 64])
        assert fmt == "windows"
        assert sizes == [(16, 16), (32, 32), (64, 64)]
        assert out.exists()

    def test_png_to_icns(self, sample_png: Path, tmp_path: Path) -> None:
        """位图输入同样支持 ICNS。"""
        out = tmp_path / "from_png.icns"
        fmt, sizes = icon_build(sample_png, out, fmt="mac")
        assert fmt == "mac"
        assert len(sizes) == 6
        assert out.exists()

    def test_aspect_ratio_kept_and_centered(self, sample_png: Path) -> None:
        """非正方形图像缩放后应保持比例、上下透明、居中。"""
        from PIL import Image

        resized = _resize_for_icon(Image.open(sample_png).convert("RGBA"), 256, "high")
        assert resized.size == (256, 256)
        assert resized.getpixel((0, 0))[3] == 0
        assert resized.getpixel((128, 64))[3] == 255
        assert resized.getpixel((128, 128)) == (255, 0, 0, 255)

    @pytest.mark.parametrize("quality", ["low", "medium", "high"])
    def test_quality_variants(self, sample_png: Path, tmp_path: Path, quality: str) -> None:
        out = tmp_path / f"{quality}.ico"
        icon_build(sample_png, out, sizes=[16, 32], quality=quality)
        assert out.exists()

    def test_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "icon.txt"
        path.write_text("not an image")
        with pytest.raises(ValueError, match="不支持的图像格式"):
            icon_build(path, tmp_path / "out.ico")

    def test_corrupt_bitmap_raises(self, tmp_path: Path) -> None:
        """扩展名正确但内容损坏时报无法读取。"""
        path = tmp_path / "broken.png"
        path.write_text("not a png")
        with pytest.raises(ValueError, match="无法读取图像"):
            icon_build(path, tmp_path / "out.ico")


class TestImageScanning:
    """目录扫描与文件头校验。"""

    def test_supported_extensions(self, sample_png: Path, sample_svg: Path) -> None:
        assert sample_png.suffix in IMG2ICO_IMAGE_EXTENSIONS
        assert is_supported_image(sample_png)
        assert is_supported_image(sample_svg)

    def test_bad_magic_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "fake.png"
        path.write_text("not a png")
        assert not is_supported_image(path)

    def test_empty_file_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.png"
        path.write_bytes(b"")
        assert not is_supported_image(path)

    def test_invalid_svg_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "fake.svg"
        path.write_text("<html></html>")
        assert not is_supported_image(path)

    def test_webp_magic(self, tmp_path: Path) -> None:
        path = tmp_path / "image.webp"
        path.write_bytes(b"RIFF0000WEBPVP8 ")
        assert is_supported_image(path)

    def test_collect_images_filters(self, tmp_path: Path, sample_png: Path) -> None:
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        assert collect_images(tmp_path) == [sample_png]
        assert collect_images(tmp_path / "missing") == []


class TestOutputTargets:
    """输出路径语义。"""

    def test_file_default_same_dir(self, sample_png: Path) -> None:
        target, fmt = resolve_output_target(sample_png)
        assert target == sample_png.with_suffix(".ico")
        assert fmt == "windows"

    def test_file_default_mac(self, sample_png: Path) -> None:
        target, fmt = resolve_output_target(sample_png, fmt="mac")
        assert target == sample_png.with_suffix(".icns")
        assert fmt == "mac"

    def test_output_existing_dir(self, sample_png: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "icons"
        out_dir.mkdir()
        target, _fmt = resolve_output_target(sample_png, out_dir)
        assert target == out_dir / "wide.ico"

    def test_output_no_suffix_treated_as_dir(self, sample_png: Path, tmp_path: Path) -> None:
        target, _fmt = resolve_output_target(sample_png, tmp_path / "outdir")
        assert target == tmp_path / "outdir" / "wide.ico"

    def test_directory_input_default_output(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        target, _fmt = resolve_output_target(source)
        assert target == source / "ico_output"

    def test_directory_input_with_file_output_raises(self, tmp_path: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        existing = tmp_path / "file.ico"
        existing.write_bytes(b"x")
        with pytest.raises(ValueError, match="必须是目录"):
            resolve_output_target(source, existing)

    def test_unknown_suffix_raises(self, sample_png: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="无法推断目标格式"):
            resolve_output_target(sample_png, tmp_path / "out.png")


class TestBatchConversion:
    """目录批量转换。"""

    def test_convert_directory(self, tmp_path: Path, sample_png: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        (source / "a.png").write_bytes(sample_png.read_bytes())
        (source / "b.png").write_bytes(sample_png.read_bytes())
        (source / "note.txt").write_text("skip")

        canonical, outputs, sizes = convert_images(source, sizes=[16, 32])
        assert canonical == "windows"
        assert [path.name for path in outputs] == ["a.ico", "b.ico"]
        assert all(path.exists() for path in outputs)
        assert sizes == [(16, 16), (32, 32)]

    def test_convert_empty_directory_raises(self, tmp_path: Path) -> None:
        source = tmp_path / "empty"
        source.mkdir()
        with pytest.raises(ValueError, match="未找到受支持"):
            convert_images(source)

    def test_progress_callback(self, tmp_path: Path, sample_png: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        (source / "a.png").write_bytes(sample_png.read_bytes())
        events: list[tuple[int, int, str]] = []
        convert_images(source, sizes=[16], progress=lambda done, total, msg: events.append((done, total, msg)))
        assert events[-1][:2] == (1, 1)
        assert "a.ico" in events[-1][2]

    def test_convert_missing_input_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            convert_images(tmp_path / "missing.png")

    def test_empty_sizes_raises(self, sample_png: Path) -> None:
        with pytest.raises(ValueError, match="尺寸列表为空"):
            convert_images(sample_png, sizes=[])

    def test_convert_single_with_progress(self, sample_png: Path, tmp_path: Path) -> None:
        events: list[int] = []
        _canonical, outputs, _sizes = convert_images(
            sample_png,
            tmp_path / "single.ico",
            sizes=[16],
            progress=lambda done, total, msg: events.append(done),
        )
        assert outputs == [tmp_path / "single.ico"]
        assert events == [1]


# ---------------------------------------------------------------------- #
# CLI 子命令（run_tool）
# ---------------------------------------------------------------------- #
class TestGenSubcommand:
    """gen 子命令通过 run_tool 调用。"""

    def test_gen_windows_default(self, sample_svg: Path, tmp_path: Path) -> None:
        """无 --format 时默认生成 Windows ICO。"""
        out = tmp_path / "gen.ico"
        code = run_tool("img2ico", ["gen", str(sample_svg), str(out)])
        assert code == 0
        assert out.exists()
        assert out.stat().st_size > 0

    def test_gen_mac_format(self, sample_svg: Path, tmp_path: Path) -> None:
        """--format mac 生成 ICNS。"""
        out = tmp_path / "gen.icns"
        code = run_tool("img2ico", ["gen", str(sample_svg), str(out), "--format", "mac"])
        assert code == 0
        assert out.exists()

    def test_gen_custom_sizes(self, sample_svg: Path, tmp_path: Path) -> None:
        """--sizes 可自定义尺寸。"""
        out = tmp_path / "gen_small.ico"
        code = run_tool(
            "img2ico",
            ["gen", str(sample_svg), str(out), "--sizes", "16", "32", "256"],
        )
        assert code == 0
        assert out.exists()

    def test_gen_comma_sizes_and_quality(self, sample_svg: Path, tmp_path: Path) -> None:
        """--sizes 支持逗号分隔，--quality 可选档位。"""
        out = tmp_path / "comma.ico"
        code = run_tool(
            "img2ico",
            ["gen", str(sample_svg), str(out), "--sizes", "16,32,64", "--quality", "low"],
        )
        assert code == 0
        assert out.exists()

    def test_gen_output_omitted_via_library(self, sample_svg: Path) -> None:
        """库入口省略输出路径时输出到输入同目录（CLI 仍要求显式输出）。"""
        _canonical, outputs, _sizes = convert_images(sample_svg, sizes=[16])
        assert outputs == [sample_svg.with_suffix(".ico")]
        assert outputs[0].exists()

    def test_gen_png_input(self, sample_png: Path, tmp_path: Path) -> None:
        """位图输入可直接转换。"""
        out = tmp_path / "png.ico"
        code = run_tool("img2ico", ["gen", str(sample_png), str(out), "--sizes", "16", "32"])
        assert code == 0
        assert out.exists()

    def test_gen_directory_batch(self, tmp_path: Path, sample_png: Path) -> None:
        """目录输入批量输出到指定目录。"""
        source = tmp_path / "src"
        source.mkdir()
        (source / "a.png").write_bytes(sample_png.read_bytes())
        (source / "b.png").write_bytes(sample_png.read_bytes())
        out_dir = tmp_path / "out"
        code = run_tool("img2ico", ["gen", str(source), str(out_dir), "--sizes", "16"])
        assert code == 0
        assert (out_dir / "a.ico").exists()
        assert (out_dir / "b.ico").exists()

    def test_gen_invalid_sizes_error(
        self, sample_svg: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非法尺寸应打印错误提示。"""
        out = tmp_path / "bad.ico"
        code = run_tool("img2ico", ["gen", str(sample_svg), str(out), "--sizes", "abc"])
        assert code == 0
        assert "错误" in capsys.readouterr().out

    def test_gen_missing_input_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """不存在的输入文件应打印错误提示（DSL action 不 raise，退出码保持 0）。"""
        missing = tmp_path / "nope.svg"
        out = tmp_path / "out.ico"
        code = run_tool("img2ico", ["gen", str(missing), str(out)])
        assert code == 0
        assert "错误" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# 输出内容验证（Pillow 解析）
# ---------------------------------------------------------------------- #
class TestOutputValidation:
    """用 Pillow 解析输出文件验证格式正确性。"""

    def test_ico_contains_expected_subimages(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出 ICO 应包含全部默认尺寸的子图像。"""
        from PIL import Image

        out = tmp_path / "val.ico"
        icon_build(sample_svg, out)
        img = Image.open(out)
        subimages = img.ico.sizes()  # type: ignore[union-attr]
        expected = {(w, h) for w, h in SVG_TO_ICO_WIN_SIZES}
        assert subimages == expected

    def test_icns_is_valid_container(self, sample_svg: Path, tmp_path: Path) -> None:
        """输出 ICNS 应可被 Pillow 打开。"""
        from PIL import Image

        out = tmp_path / "val.icns"
        icon_build(sample_svg, out, fmt="mac")
        img = Image.open(out)
        assert img.format == "ICNS"
