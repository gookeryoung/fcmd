"""img2pdf 工具测试。

验证 ``fcmd.dsl.actions.media`` 中图片转 PDF 的核心逻辑：
- 工具注册、页面尺寸 / DPI 规范化
- 图片发现与排序、输出路径语义
- RGB / 透明通道转换、固定页面排版
- 自动页面与固定页面 PDF、EXIF 方向校正
- CLI 单命令（run_tool）与错误分支
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.dsl.actions.media import (
    IMG2PDF_DEFAULT_DPI,
    IMG2PDF_IMAGE_EXTENSIONS,
    IMG2PDF_PAGE_SIZE_CHOICES,
    IMG2PDF_PAGE_SIZES,
    _fit_pdf_page,
    _is_pdf_image,
    _to_pdf_rgb,
    collect_pdf_images,
    images_to_pdf,
    normalize_dpi,
    normalize_page_size,
    resolve_pdf_output,
)


# ---------------------------------------------------------------------- #
# fixtures: 生成测试图片
# ---------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _ensure_pillow() -> None:
    """把 Pillow 惰性导入到 media 模块全局（生产路径由 _require_pil 完成）。"""
    from fcmd.dsl.actions import media

    assert media._require_pil()


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """生成 200x100 的 PNG。"""
    from PIL import Image

    path = tmp_path / "wide.png"
    Image.new("RGB", (200, 100), (200, 30, 30)).save(path)
    return path


@pytest.fixture
def rgba_png(tmp_path: Path) -> Path:
    """生成带透明区域的 RGBA PNG（左侧全透明）。"""
    from PIL import Image

    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(20, 40):
        for y in range(40):
            image.putpixel((x, y), (10, 120, 220, 255))
    image.save(path)
    return path


@pytest.fixture
def oriented_jpeg(tmp_path: Path) -> Path:
    """生成 EXIF Orientation=6（需顺时针旋转 90°）的 100x50 JPEG。"""
    from PIL import Image

    path = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (100, 50), (30, 160, 80))
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    return path


def _page_size_points(pdf: Path, index: int = 0) -> tuple[float, float]:
    """读取 PDF 指定页的宽高（pt）。"""
    box = PdfReader(str(pdf)).pages[index].mediabox
    return float(box.width), float(box.height)


# ---------------------------------------------------------------------- #
# 注册与常量
# ---------------------------------------------------------------------- #
def test_tool_registered() -> None:
    """img2pdf 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["img2pdf"]) == {None}


def test_defaults_are_consistent() -> None:
    """默认值与页面尺寸表保持一致。"""
    assert IMG2PDF_DEFAULT_DPI == 150
    assert IMG2PDF_PAGE_SIZE_CHOICES[0] == "auto"
    assert "a4" in IMG2PDF_PAGE_SIZES
    assert ".png" in IMG2PDF_IMAGE_EXTENSIONS
    assert normalize_page_size("auto") == "auto"


# ---------------------------------------------------------------------- #
# 页面尺寸与 DPI
# ---------------------------------------------------------------------- #
class TestNormalizePageSize:
    """页面尺寸规范化。"""

    @pytest.mark.parametrize(("raw", "expected"), [("A4", "a4"), (" a3 ", "a3"), ("Auto", "auto")])
    def test_valid(self, raw: str, expected: str) -> None:
        assert normalize_page_size(raw) == expected

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的页面尺寸"):
            normalize_page_size("A0")


class TestNormalizeDpi:
    """DPI 规范化。"""

    @pytest.mark.parametrize("dpi", [36, 150, 1200])
    def test_valid(self, dpi: int) -> None:
        assert normalize_dpi(dpi) == dpi

    @pytest.mark.parametrize("dpi", [0, 35, 1201, True, "150"])
    def test_invalid_raises(self, dpi: object) -> None:
        with pytest.raises(ValueError, match="DPI"):
            normalize_dpi(dpi)  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# 图片发现
# ---------------------------------------------------------------------- #
class TestIsPdfImage:
    """文件头与扩展名校验。"""

    def test_valid_png(self, sample_png: Path) -> None:
        assert _is_pdf_image(sample_png)

    def test_directory_is_not_image(self, tmp_path: Path) -> None:
        assert not _is_pdf_image(tmp_path)

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "note.txt"
        path.write_text("PNG", encoding="utf-8")
        assert not _is_pdf_image(path)

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.png"
        path.write_bytes(b"")
        assert not _is_pdf_image(path)

    def test_corrupt_header(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.png"
        path.write_bytes(b"not-an-image")
        assert not _is_pdf_image(path)

    def test_webp_header(self, tmp_path: Path) -> None:
        path = tmp_path / "x.webp"
        path.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        assert _is_pdf_image(path)

    def test_ico_header(self, tmp_path: Path) -> None:
        path = tmp_path / "x.ico"
        path.write_bytes(b"\x00\x00\x01\x00" + b"\x00" * 12)
        assert _is_pdf_image(path)


class TestCollectPdfImages:
    """目录展开、排序与判重。"""

    def test_single_file(self, sample_png: Path) -> None:
        assert collect_pdf_images([sample_png]) == [sample_png]

    def test_directory_sorted_and_filtered(self, tmp_path: Path, sample_png: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        (source / "b.png").write_bytes(sample_png.read_bytes())
        (source / "a.png").write_bytes(sample_png.read_bytes())
        (source / "note.txt").write_text("x", encoding="utf-8")
        collected = collect_pdf_images([source])
        assert [item.name for item in collected] == ["a.png", "b.png"]

    def test_explicit_order_preserved(self, tmp_path: Path, sample_png: Path) -> None:
        second = tmp_path / "second.png"
        second.write_bytes(sample_png.read_bytes())
        assert collect_pdf_images([second, sample_png]) == [second, sample_png]

    def test_duplicates_removed(self, sample_png: Path) -> None:
        assert collect_pdf_images([sample_png, sample_png]) == [sample_png]

    def test_empty_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="未找到可转换"):
            collect_pdf_images([tmp_path])

    def test_invalid_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.png"
        path.write_bytes(b"not-an-image")
        with pytest.raises(ValueError, match="不支持"):
            collect_pdf_images([path])

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            collect_pdf_images([tmp_path / "missing.png"])

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError, match="未提供"):
            collect_pdf_images([])


class TestResolvePdfOutput:
    """输出路径语义。"""

    def test_plain_pdf(self, tmp_path: Path) -> None:
        assert resolve_pdf_output(tmp_path / "out.pdf") == tmp_path / "out.pdf"

    def test_missing_suffix(self, tmp_path: Path) -> None:
        assert resolve_pdf_output(tmp_path / "out") == tmp_path / "out.pdf"

    def test_existing_directory(self, tmp_path: Path) -> None:
        assert resolve_pdf_output(tmp_path) == tmp_path / "output.pdf"

    def test_wrong_suffix_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"\.pdf"):
            resolve_pdf_output(tmp_path / "out.txt")


# ---------------------------------------------------------------------- #
# 像素处理
# ---------------------------------------------------------------------- #
class TestPdfPixels:
    """RGB 转换与页面排版。"""

    def test_rgba_gets_white_background(self, rgba_png: Path) -> None:
        from PIL import Image

        with Image.open(rgba_png) as image:
            rgb = _to_pdf_rgb(image)
        assert rgb.mode == "RGB"
        assert rgb.getpixel((0, 0)) == (255, 255, 255)
        assert rgb.getpixel((39, 0)) == (10, 120, 220)
        rgb.close()

    def test_la_mode(self) -> None:
        from PIL import Image

        image = Image.new("LA", (2, 2), (0, 0))
        rgb = _to_pdf_rgb(image)
        assert rgb.mode == "RGB"
        assert rgb.getpixel((0, 0)) == (255, 255, 255)

    def test_palette_with_transparency(self) -> None:
        from PIL import Image

        image = Image.new("P", (2, 2), 0)
        image.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
        image.info["transparency"] = 0
        rgb = _to_pdf_rgb(image)
        assert rgb.mode == "RGB"
        assert rgb.getpixel((0, 0)) == (255, 255, 255)

    def test_palette_without_transparency(self) -> None:
        from PIL import Image

        image = Image.new("P", (2, 2), 1)
        image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        rgb = _to_pdf_rgb(image)
        assert rgb.getpixel((0, 0)) == (255, 0, 0)

    def test_other_mode_converts(self) -> None:
        from PIL import Image

        image = Image.new("L", (2, 2), 128)
        rgb = _to_pdf_rgb(image)
        assert rgb.mode == "RGB"

    def test_fit_landscape_into_portrait_page(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (200, 100), (255, 0, 0))
        page = _fit_pdf_page(image, (300, 300))
        assert page.size == (300, 300)
        assert page.getpixel((0, 0)) == (255, 255, 255)
        assert page.getpixel((150, 150)) == (255, 0, 0)

    def test_fit_tall_image_centered_horizontally(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (50, 400), (0, 0, 255))
        page = _fit_pdf_page(image, (300, 300))
        assert page.size == (300, 300)
        assert page.getpixel((0, 0)) == (255, 255, 255)

    def test_fit_exact_size_skips_resize(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (300, 300), (0, 255, 0))
        page = _fit_pdf_page(image, (300, 300))
        assert page.size == (300, 300)


# ---------------------------------------------------------------------- #
# 核心转换
# ---------------------------------------------------------------------- #
class TestImagesToPdf:
    """images_to_pdf 主流程。"""

    def test_auto_pages(self, tmp_path: Path, sample_png: Path, rgba_png: Path) -> None:
        output = tmp_path / "auto.pdf"
        result = images_to_pdf([sample_png, rgba_png], output, dpi=150)
        assert result == output
        assert output.read_bytes().startswith(b"%PDF")
        assert len(PdfReader(str(output)).pages) == 2

    def test_fixed_a4_page_size(self, tmp_path: Path, sample_png: Path) -> None:
        output = tmp_path / "a4.pdf"
        images_to_pdf([sample_png], output, page_size="A4", dpi=150)
        width, height = _page_size_points(output)
        assert width == pytest.approx(8.27 * 72, abs=1.5)
        assert height == pytest.approx(11.69 * 72, abs=1.5)

    def test_output_directory_and_missing_parent(self, tmp_path: Path, sample_png: Path) -> None:
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        assert images_to_pdf([sample_png], out_dir).name == "output.pdf"
        deep = tmp_path / "a" / "b" / "deep"
        result = images_to_pdf([sample_png], deep)
        assert result == deep.with_suffix(".pdf")
        assert result.exists()

    def test_progress_events(self, tmp_path: Path, sample_png: Path) -> None:
        second = tmp_path / "second.png"
        second.write_bytes(sample_png.read_bytes())
        events: list[tuple[int, int, str]] = []
        images_to_pdf(
            [sample_png, second],
            tmp_path / "progress.pdf",
            progress=lambda done, total, message: events.append((done, total, message)),
        )
        assert events[0][0] == 0
        assert events[-1][0] == 2
        assert all(total == 2 for _, total, _ in events)
        assert "已加入第 2 页" in events[-1][2]

    def test_exif_orientation_corrected(self, tmp_path: Path, oriented_jpeg: Path) -> None:
        corrected = tmp_path / "corrected.pdf"
        images_to_pdf([oriented_jpeg], corrected, normalize=True, dpi=150)
        width, height = _page_size_points(corrected)
        assert width < height

        raw = tmp_path / "raw.pdf"
        images_to_pdf([oriented_jpeg], raw, normalize=False, dpi=150)
        width, height = _page_size_points(raw)
        assert width > height

    def test_corrupt_image_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"junk")
        with pytest.raises(ValueError, match="无法读取图像"):
            images_to_pdf([path], tmp_path / "out.pdf")

    def test_missing_dependency_raises(self, tmp_path: Path, sample_png: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from fcmd.dsl.actions import media

        monkeypatch.setattr(media, "HAS_PIL", False)
        with pytest.raises(RuntimeError, match="pillow"):
            images_to_pdf([sample_png], tmp_path / "out.pdf")

    @pytest.mark.parametrize(("kwargs", "match"), [({"dpi": 0}, "DPI"), ({"page_size": "A0"}, "页面尺寸")])
    def test_invalid_options_raise(
        self, tmp_path: Path, sample_png: Path, kwargs: dict[str, object], match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            images_to_pdf([sample_png], tmp_path / "out.pdf", **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# CLI 单命令
# ---------------------------------------------------------------------- #
class TestCli:
    """run_tool 单命令入口。"""

    def test_basic_conversion(self, sample_png: Path, rgba_png: Path, tmp_path: Path) -> None:
        output = tmp_path / "cli.pdf"
        code = run_tool("img2pdf", [str(sample_png), str(rgba_png), "--output-path", str(output)])
        assert code == 0
        assert output.exists()
        assert len(PdfReader(str(output)).pages) == 2

    def test_directory_input(self, tmp_path: Path, sample_png: Path) -> None:
        source = tmp_path / "src"
        source.mkdir()
        (source / "b.png").write_bytes(sample_png.read_bytes())
        (source / "a.png").write_bytes(sample_png.read_bytes())
        output = tmp_path / "dir.pdf"
        code = run_tool("img2pdf", [str(source), "--output-path", str(output)])
        assert code == 0
        assert len(PdfReader(str(output)).pages) == 2

    def test_options(self, sample_png: Path, tmp_path: Path) -> None:
        output = tmp_path / "opts.pdf"
        code = run_tool(
            "img2pdf",
            [
                str(sample_png),
                "--output-path",
                str(output),
                "--page-size",
                "a4",
                "--dpi",
                "200",
                "--no-normalize",
            ],
        )
        assert code == 0
        width, _height = _page_size_points(output)
        assert width == pytest.approx(8.27 * 72, abs=1.5)

    def test_default_output(self, sample_png: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        code = run_tool("img2pdf", [str(sample_png)])
        assert code == 0
        assert (tmp_path / "output.pdf").exists()

    def test_invalid_page_size(self, sample_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("img2pdf", [str(sample_png), "--output-path", str(tmp_path / "x.pdf"), "--page-size", "A0"])
        assert code == 0
        assert "错误" in capsys.readouterr().out

    def test_invalid_dpi(self, sample_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("img2pdf", [str(sample_png), "--output-path", str(tmp_path / "x.pdf"), "--dpi", "0"])
        assert code == 0
        assert "错误" in capsys.readouterr().out

    def test_missing_input(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("img2pdf", [str(tmp_path / "missing.png"), "--output-path", str(tmp_path / "x.pdf")])
        assert code == 0
        assert "错误" in capsys.readouterr().out

    def test_wrong_output_suffix(self, sample_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("img2pdf", [str(sample_png), "--output-path", str(tmp_path / "x.txt")])
        assert code == 0
        assert "错误" in capsys.readouterr().out

    def test_missing_pillow_returns_early(
        self, sample_png: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Pillow 不可用时动作打印提示并直接返回，不生成文件。"""
        from fcmd.dsl.actions import media

        monkeypatch.setattr(media, "_require_pil", lambda: False)
        output = tmp_path / "skip.pdf"
        code = run_tool("img2pdf", [str(sample_png), "--output-path", str(output)])
        assert code == 0
        assert not output.exists()
        assert "错误" not in capsys.readouterr().out
