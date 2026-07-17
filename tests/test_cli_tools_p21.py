"""P21 移植工具测试：imagetool / pdftool。

验证 ``fcmd.cli`` 包下 2 个基于可选依赖的工具：
- ``imagetool``：图片处理（依赖 ``fcmd[img]`` = Pillow）
- ``pdftool``：PDF 处理（依赖 ``fcmd[pdf]`` = PyMuPDF + pypdf）

测试在已安装可选依赖的环境下运行；缺依赖分支通过 ``monkeypatch``
mock ``HAS_PIL`` / ``HAS_PYMUPDF`` / ``HAS_PYPDF`` 模块属性验证。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

import fcmd as fx
import fcmd.cli.imagetool
import fcmd.cli.pdftool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.imagetool import (
    HAS_PIL,
    _apply_exif_modifications,
    _apply_single_exif_set,
    _load_font,
    _resolve_position,
    _save_image,
)
from fcmd.cli.pdftool import HAS_PYMUPDF, HAS_PYPDF


# ---------------------------------------------------------------------- #
# fixtures: 生成测试图片与 PDF
# ---------------------------------------------------------------------- #
@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    """生成 50x50 RGB 测试图片。"""
    from PIL import Image

    img = Image.new("RGB", (50, 50), color=(255, 0, 0))
    p = tmp_path / "sample.png"
    img.save(p)
    return p


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """生成 2 页测试 PDF（用 ASCII 内容避免中文字体提取问题）。"""
    import fitz

    doc = fitz.open()
    for text in ("Page one content", "Page two content"):
        # pyrefly: ignore [missing-attribute]
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), text, fontsize=12)
    p = tmp_path / "sample.pdf"
    doc.save(str(p))
    doc.close()
    return p


@pytest.fixture
def sample_pdf_with_image(tmp_path: Path, sample_image: Path) -> Path:
    """生成包含 1 张图片的 PDF。"""
    import fitz

    doc = fitz.open()
    # pyrefly: ignore [missing-attribute]
    page = doc.new_page(width=300, height=400)
    page.insert_image(fitz.Rect(20, 20, 120, 120), filename=str(sample_image))
    p = tmp_path / "with_image.pdf"
    doc.save(str(p))
    doc.close()
    return p


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestRegistration:
    """imagetool / pdftool 注册验证。"""

    def test_both_tools_registered(self) -> None:
        """两个工具应在 _TOOL_REGISTRY 中注册。"""
        assert "imagetool" in _TOOL_REGISTRY
        assert "pdftool" in _TOOL_REGISTRY

    def test_imagetool_subcommands(self) -> None:
        """imagetool 应有 11 个子命令。"""
        subs = fx.list_subcommands("imagetool")
        for sc in ("r", "c", "ro", "fl", "cv", "wm", "cp", "i", "e", "hi", "co"):
            assert sc in subs, f"子命令 {sc!r} 未注册"

    def test_pdftool_subcommands(self) -> None:
        """pdftool 应有 15 个子命令。"""
        subs = fx.list_subcommands("pdftool")
        for sc in ("m", "s", "c", "e", "d", "xt", "xi", "w", "r", "crop", "i", "ocr", "reorder", "img", "repair"):
            assert sc in subs, f"子命令 {sc!r} 未注册"


# ---------------------------------------------------------------------- #
# imagetool 辅助函数
# ---------------------------------------------------------------------- #
class TestImagetoolHelpers:
    """imagetool 私有辅助函数测试。"""

    def test_resolve_position_all_variants(self) -> None:
        """_resolve_position 五种位置均能计算坐标。"""
        img_size = (100, 100)
        text_w, text_h, margin = 20, 10, 5
        positions = {
            "top-left": (5, 5),
            "top-right": (75, 5),
            "bottom-left": (5, 85),
            "bottom-right": (75, 85),
            "center": (40, 45),
        }
        for pos, expected in positions.items():
            assert _resolve_position(pos, img_size, text_w, text_h, margin) == expected

    def test_resolve_position_unknown_falls_back(self) -> None:
        """未知位置回退到 bottom-right。"""
        result = _resolve_position("unknown", (100, 100), 20, 10, 5)
        assert result == (75, 85)

    def test_load_font_returns_object(self) -> None:
        """_load_font 返回字体对象（truetype 或默认）。"""
        font = _load_font(32)
        assert font is not None

    def test_save_image_jpeg_convert_rgb(self, tmp_path: Path) -> None:
        """_save_image 保存 JPEG 时自动转 RGB（丢弃 alpha）。"""
        from PIL import Image

        img = Image.new("RGBA", (20, 20), color=(255, 0, 0, 128))
        out = tmp_path / "out.jpg"
        _save_image(img, out)
        assert out.exists()
        # JPEG 不应有 alpha 通道
        result = Image.open(out)
        assert result.mode in ("RGB", "L")

    def test_save_image_creates_parent_dir(self, tmp_path: Path) -> None:
        """_save_image 自动创建父目录。"""
        from PIL import Image

        img = Image.new("RGB", (10, 10))
        out = tmp_path / "nested" / "deep" / "out.png"
        _save_image(img, out)
        assert out.exists()

    def test_apply_single_exif_set_valid(self) -> None:
        """_apply_single_exif_set 正确解析 KEY=VALUE。"""

        class FakeExif(Dict[int, object]):
            """模拟 PIL.Exif 模拟对象。"""

        exif: FakeExif = FakeExif()
        _apply_single_exif_set(exif, "271=FCMD")
        assert exif[271] == "FCMD"

    def test_apply_single_exif_set_invalid_no_eq(self, capsys: pytest.CaptureFixture[str]) -> None:
        """缺少 = 的项被跳过并打印提示。"""

        class FakeExif(Dict[int, object]):
            pass

        exif: FakeExif = FakeExif()
        _apply_single_exif_set(exif, "invalid")
        out = capsys.readouterr().out
        assert "缺少 =" in out
        assert len(exif) == 0

    def test_apply_single_exif_set_invalid_tag(self, capsys: pytest.CaptureFixture[str]) -> None:
        """非数字标签号被跳过并打印提示。"""

        class FakeExif(Dict[int, object]):
            pass

        exif: FakeExif = FakeExif()
        _apply_single_exif_set(exif, "abc=value")
        out = capsys.readouterr().out
        assert "无效标签号" in out
        assert len(exif) == 0

    def test_apply_exif_modifications_clear_then_set(self) -> None:
        """clear 先清空，set 再写入。"""

        class FakeExif(Dict[int, object]):
            pass

        exif: FakeExif = FakeExif({1: "old"})
        modified = _apply_exif_modifications(exif, ["2=new"], clear=True)
        assert modified is True
        assert 1 not in exif
        assert exif[2] == "new"

    def test_apply_exif_modifications_noop(self) -> None:
        """无 set 无 clear 返回 False。"""

        class FakeExif(Dict[int, object]):
            pass

        exif: FakeExif = FakeExif({1: "old"})
        modified = _apply_exif_modifications(exif, None, clear=False)
        assert modified is False
        assert exif == {1: "old"}


# ---------------------------------------------------------------------- #
# imagetool 子命令（通过 run_tool）
# ---------------------------------------------------------------------- #
class TestImagetoolCommands:
    """imagetool 各子命令通过 run_tool 调用。"""

    def test_image_resize_keep_ratio(
        self, sample_image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """resize 子命令保持宽高比缩放。"""
        out = tmp_path / "resized.png"
        code = run_tool("imagetool", ["r", str(sample_image), str(out), "20"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "调整尺寸完成" in captured

    def test_image_resize_stretch(self, sample_image: Path, tmp_path: Path) -> None:
        """resize 拉伸模式（stretch + 不传 height 时 height=width）。"""
        out = tmp_path / "resized.png"
        code = run_tool(
            "imagetool",
            ["r", str(sample_image), str(out), "30", "--stretch"],
        )
        assert code == 0

    def test_image_crop(self, sample_image: Path, tmp_path: Path) -> None:
        """crop 子命令裁剪图片。"""
        out = tmp_path / "cropped.png"
        code = run_tool("imagetool", ["c", str(sample_image), str(out), "0", "0", "20", "20"])
        assert code == 0

    def test_image_rotate(self, sample_image: Path, tmp_path: Path) -> None:
        """rotate 子命令旋转图片。"""
        out = tmp_path / "rotated.png"
        code = run_tool("imagetool", ["ro", str(sample_image), str(out), "90"])
        assert code == 0

    def test_image_flip_horizontal(self, sample_image: Path, tmp_path: Path) -> None:
        """flip horizontal 子命令。"""
        out = tmp_path / "flipped.png"
        code = run_tool("imagetool", ["fl", str(sample_image), str(out), "--direction", "horizontal"])
        assert code == 0

    def test_image_flip_vertical(self, sample_image: Path, tmp_path: Path) -> None:
        """flip vertical 子命令。"""
        out = tmp_path / "flipped.png"
        code = run_tool("imagetool", ["fl", str(sample_image), str(out), "--direction", "vertical"])
        assert code == 0

    def test_image_convert_png_to_jpeg(self, sample_image: Path, tmp_path: Path) -> None:
        """convert 子命令 PNG → JPEG。"""
        out = tmp_path / "converted.jpg"
        code = run_tool("imagetool", ["cv", str(sample_image), str(out)])
        assert code == 0
        assert out.exists()

    def test_image_watermark(self, sample_image: Path, tmp_path: Path) -> None:
        """watermark 子命令添加文字水印。"""
        out = tmp_path / "watermarked.png"
        code = run_tool(
            "imagetool",
            ["wm", str(sample_image), str(out), "TEST", "--position", "center"],
        )
        assert code == 0
        assert out.exists()

    def test_image_compress(self, sample_image: Path, tmp_path: Path) -> None:
        """compress 子命令重新编码压缩。"""
        out = tmp_path / "compressed.png"
        code = run_tool("imagetool", ["cp", str(sample_image), str(out), "--quality", "60"])
        assert code == 0

    def test_image_info_text(self, sample_image: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """info 子命令打印图片信息（纯文本）。"""
        code = run_tool("imagetool", ["i", str(sample_image)])
        assert code == 0
        out = capsys.readouterr().out
        assert "格式" in out
        assert "尺寸" in out

    def test_image_info_json(self, sample_image: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """info 子命令 --json 输出 JSON。"""
        code = run_tool("imagetool", ["i", str(sample_image), "--json"])
        assert code == 0
        out = capsys.readouterr().out
        assert '"format"' in out
        assert '"width"' in out

    def test_image_exif_hide_only(self, sample_image: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """exif 子命令 --hide 不打印且不修改时不输出保存提示。"""
        code = run_tool("imagetool", ["e", str(sample_image), "--hide"])
        assert code == 0
        out = capsys.readouterr().out
        # 无修改时不应保存
        assert "EXIF 已保存" not in out

    def test_image_exif_show_default(self, sample_image: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """exif 子命令默认打印 EXIF（无数据时打印提示）。"""
        code = run_tool("imagetool", ["e", str(sample_image)])
        assert code == 0
        out = capsys.readouterr().out
        # PNG 无 EXIF，应打印"无 EXIF 数据"
        assert "EXIF" in out

    def test_image_exif_set_tag(self, sample_image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """exif 子命令 --set 写入标签后保存。"""
        out = tmp_path / "exif.jpg"
        code = run_tool(
            "imagetool",
            ["e", str(sample_image), "--output-path", str(out), "--hide", "--set", "271=FCMD"],
        )
        assert code == 0
        captured = capsys.readouterr().out
        assert "EXIF 已保存" in captured
        assert out.exists()

    def test_image_histogram_rgb(self, sample_image: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """histogram 子命令打印 RGB 直方图。"""
        code = run_tool("imagetool", ["hi", str(sample_image)])
        assert code == 0
        out = capsys.readouterr().out
        assert "直方图" in out

    def test_image_histogram_luminance(self, sample_image: Path) -> None:
        """histogram 子命令 luminance 通道。"""
        code = run_tool("imagetool", ["hi", str(sample_image), "--channel", "luminance"])
        assert code == 0

    def test_image_colors(self, sample_image: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """colors 子命令提取主色调。"""
        code = run_tool("imagetool", ["co", str(sample_image), "--count", "3"])
        assert code == 0
        out = capsys.readouterr().out
        assert "主色调" in out

    def test_image_resize_no_pil(
        self,
        sample_image: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pillow 未安装时打印提示。"""
        monkeypatch.setattr("fcmd.cli.imagetool.HAS_PIL", False)
        out = tmp_path / "out.png"
        code = run_tool("imagetool", ["r", str(sample_image), str(out), "10"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "未安装 Pillow" in captured


# ---------------------------------------------------------------------- #
# pdftool 子命令
# ---------------------------------------------------------------------- #
class TestPdftoolCommands:
    """pdftool 各子命令通过 run_tool 调用。"""

    def test_pdf_info(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """info 子命令打印 PDF 信息。"""
        code = run_tool("pdftool", ["i", str(sample_pdf)])
        assert code == 0
        out = capsys.readouterr().out
        assert "页数" in out
        assert "文件大小" in out

    def test_pdf_compress(self, sample_pdf: Path, tmp_path: Path) -> None:
        """compress 子命令压缩 PDF。"""
        out = tmp_path / "compressed.pdf"
        code = run_tool("pdftool", ["c", str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        assert out.exists()

    def test_pdf_extract_text(self, sample_pdf: Path, tmp_path: Path) -> None:
        """extract_text 子命令提取文本。"""
        out = tmp_path / "out.txt"
        code = run_tool("pdftool", ["xt", str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "Page one" in content

    def test_pdf_split(self, sample_pdf: Path, tmp_path: Path) -> None:
        """split 子命令拆分为单页。"""
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(sample_pdf), "--output-dir", str(out_dir)])
        assert code == 0
        pages = list(out_dir.glob("*.pdf"))
        assert len(pages) == 2

    def test_pdf_merge(self, sample_pdf: Path, tmp_path: Path) -> None:
        """merge 子命令合并多个 PDF。"""
        out = tmp_path / "merged.pdf"
        code = run_tool("pdftool", ["m", str(sample_pdf), str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        assert out.exists()

    def test_pdf_encrypt_no_password(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """encrypt 缺少 password 时打印错误。"""
        code = run_tool("pdftool", ["e", str(sample_pdf)])
        assert code == 0
        out = capsys.readouterr().out
        assert "必填参数" in out

    def test_pdf_encrypt_decrypt_roundtrip(self, sample_pdf: Path, tmp_path: Path) -> None:
        """encrypt 后 decrypt 还原出可读 PDF。"""
        enc = tmp_path / "enc.pdf"
        dec = tmp_path / "dec.pdf"
        code = run_tool("pdftool", ["e", str(sample_pdf), "--output-path", str(enc), "--password", "secret"])
        assert code == 0
        assert enc.exists()
        code = run_tool("pdftool", ["d", str(enc), "--output-path", str(dec), "--password", "secret"])
        assert code == 0
        assert dec.exists()

    def test_pdf_rotate(self, sample_pdf: Path, tmp_path: Path) -> None:
        """rotate 子命令旋转页面。"""
        out = tmp_path / "rotated.pdf"
        code = run_tool("pdftool", ["r", str(sample_pdf), "--output-path", str(out), "--rotation", "90"])
        assert code == 0
        assert out.exists()

    def test_pdf_add_watermark(self, sample_pdf: Path, tmp_path: Path) -> None:
        """add_watermark 子命令添加水印。"""
        out = tmp_path / "watermarked.pdf"
        code = run_tool("pdftool", ["w", str(sample_pdf), "--output-path", str(out), "--text", "DRAFT"])
        assert code == 0
        assert out.exists()

    def test_pdf_to_images(self, sample_pdf: Path, tmp_path: Path) -> None:
        """to_images 子命令 PDF 转图片。"""
        out_dir = tmp_path / "imgs"
        code = run_tool("pdftool", ["img", str(sample_pdf), "--output-dir", str(out_dir), "--dpi", "72"])
        assert code == 0
        pngs = list(out_dir.glob("*.png"))
        assert len(pngs) == 2

    def test_pdf_extract_images(self, sample_pdf_with_image: Path, tmp_path: Path) -> None:
        """extract_images 子命令提取 PDF 内嵌图片。"""
        out_dir = tmp_path / "imgs"
        code = run_tool("pdftool", ["xi", str(sample_pdf_with_image), "--output-dir", str(out_dir)])
        assert code == 0
        imgs = list(out_dir.glob("*"))
        assert len(imgs) >= 1

    def test_pdf_repair(self, sample_pdf: Path, tmp_path: Path) -> None:
        """repair 子命令修复 PDF。"""
        out = tmp_path / "repaired.pdf"
        code = run_tool("pdftool", ["repair", str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        assert out.exists()

    def test_pdf_compress_no_pymupdf(
        self,
        sample_pdf: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PyMuPDF 未安装时 compress 子命令打印提示。"""
        monkeypatch.setattr("fcmd.cli.pdftool.HAS_PYMUPDF", False)
        out = tmp_path / "compressed.pdf"
        code = run_tool("pdftool", ["c", str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        captured = capsys.readouterr().out
        assert "未安装 PyMuPDF" in captured

    def test_pdf_encrypt_no_password_no_pypdf(
        self,
        sample_pdf: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pypdf 未安装但 password 已传时打印提示（password 检查先于依赖检查）。"""
        monkeypatch.setattr("fcmd.cli.pdftool.HAS_PYPDF", False)
        code = run_tool("pdftool", ["e", str(sample_pdf), "--password", "secret"])
        assert code == 0
        out = capsys.readouterr().out
        assert "未安装 pypdf" in out

    def test_pdf_reorder(self, sample_pdf: Path, tmp_path: Path) -> None:
        """reorder 子命令重排页面顺序。"""
        out = tmp_path / "reordered.pdf"
        # 反向：[1, 0]
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "1", "0"])
        assert code == 0
        assert out.exists()

    def test_pdf_crop(self, sample_pdf: Path, tmp_path: Path) -> None:
        """crop 子命令裁剪页面边距。"""
        out = tmp_path / "cropped.pdf"
        code = run_tool("pdftool", ["crop", str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        assert out.exists()

    def test_pdf_info_no_pymupdf(
        self,
        sample_pdf: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PyMuPDF 未安装时打印提示。"""
        monkeypatch.setattr("fcmd.cli.pdftool.HAS_PYMUPDF", False)
        code = run_tool("pdftool", ["i", str(sample_pdf)])
        assert code == 0
        out = capsys.readouterr().out
        assert "未安装 PyMuPDF" in out

    def test_pdf_merge_no_pypdf(
        self,
        sample_pdf: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pypdf 未安装时打印提示。"""
        monkeypatch.setattr("fcmd.cli.pdftool.HAS_PYPDF", False)
        out = tmp_path / "merged.pdf"
        code = run_tool("pdftool", ["m", str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        captured = capsys.readouterr().out
        assert "未安装 pypdf" in captured


# ---------------------------------------------------------------------- #
# 可选依赖缺失 guard return 覆盖（parametrized）
# ---------------------------------------------------------------------- #
class TestNoDepsGuards:
    """可选依赖缺失时各子命令打印提示并返回。"""

    @pytest.mark.parametrize(
        "subcommand,args",
        [
            ("c", ["in.png", "out.png", "0", "0", "10", "10"]),
            ("ro", ["in.png", "out.png", "90"]),
            ("fl", ["in.png", "out.png"]),
            ("cv", ["in.png", "out.png"]),
            ("wm", ["in.png", "out.png", "T"]),
            ("cp", ["in.png", "out.png"]),
            ("i", ["in.png"]),
            ("e", ["in.png"]),
            ("hi", ["in.png"]),
            ("co", ["in.png"]),
        ],
    )
    def test_imagetool_no_pil(
        self,
        subcommand: str,
        args: list[str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Pillow 未安装时各子命令打印提示。"""
        monkeypatch.setattr("fcmd.cli.imagetool.HAS_PIL", False)
        code = run_tool("imagetool", [subcommand, *args])
        assert code == 0
        assert "未安装 Pillow" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "subcommand,args",
        [
            ("xt", ["in.pdf"]),
            ("xi", ["in.pdf"]),
            ("w", ["in.pdf"]),
            ("r", ["in.pdf"]),
            ("crop", ["in.pdf"]),
            ("img", ["in.pdf"]),
            ("repair", ["in.pdf"]),
        ],
    )
    def test_pdftool_no_pymupdf(
        self,
        subcommand: str,
        args: list[str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """PyMuPDF 未安装时各子命令打印提示。"""
        monkeypatch.setattr("fcmd.cli.pdftool.HAS_PYMUPDF", False)
        code = run_tool("pdftool", [subcommand, *args])
        assert code == 0
        assert "未安装 PyMuPDF" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "subcommand,args",
        [
            ("s", ["in.pdf"]),
            ("d", ["in.pdf", "--password", "x"]),
            ("reorder", ["in.pdf", "out.pdf", "0"]),
        ],
    )
    def test_pdftool_no_pypdf(
        self,
        subcommand: str,
        args: list[str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pypdf 未安装时各子命令打印提示。"""
        monkeypatch.setattr("fcmd.cli.pdftool.HAS_PYPDF", False)
        code = run_tool("pdftool", [subcommand, *args])
        assert code == 0
        assert "未安装 pypdf" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# 边界分支测试
# ---------------------------------------------------------------------- #
class TestEdgeCases:
    """边界分支覆盖测试。"""

    def test_pdf_merge_nonexistent_input(self, tmp_path: Path) -> None:
        """merge 跳过不存在的输入文件。"""
        out = tmp_path / "merged.pdf"
        nonexistent = tmp_path / "nope.pdf"
        code = run_tool("pdftool", ["m", str(nonexistent), "--output-path", str(out)])
        assert code == 0
        assert out.exists()

    def test_pdf_decrypt_no_password(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """decrypt 缺少 password 时打印错误。"""
        code = run_tool("pdftool", ["d", str(sample_pdf)])
        assert code == 0
        assert "必填参数" in capsys.readouterr().out

    def test_pdf_decrypt_unencrypted(self, sample_pdf: Path, tmp_path: Path) -> None:
        """decrypt 未加密 PDF 时跳过解密直接复制。"""
        out = tmp_path / "dec.pdf"
        code = run_tool("pdftool", ["d", str(sample_pdf), "--output-path", str(out), "--password", "x"])
        assert code == 0
        assert out.exists()

    def test_pdf_reorder_out_of_range(self, sample_pdf: Path, tmp_path: Path) -> None:
        """reorder 越界页号被跳过。"""
        out = tmp_path / "reordered.pdf"
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "5", "0"])
        assert code == 0
        assert out.exists()

    def test_image_resize_stretch_with_height_direct(self, sample_image: Path, tmp_path: Path) -> None:
        """resize stretch 模式显式传 height（直接调用绕过 CLI int|None 限制）。"""
        from fcmd.cli.imagetool import image_resize

        out = tmp_path / "out.png"
        image_resize(sample_image, out, width=30, height=20, stretch=True)
        assert out.exists()

    def test_image_histogram_grayscale(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """histogram 子命令处理灰度图（len(hist)==256 分支）。"""
        from PIL import Image

        img = Image.new("L", (50, 50), color=128)
        p = tmp_path / "gray.png"
        img.save(p)
        code = run_tool("imagetool", ["hi", str(p)])
        assert code == 0
        assert "直方图" in capsys.readouterr().out

    def test_print_exif_with_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_print_exif 打印有数据的 EXIF。"""
        from fcmd.cli.imagetool import _print_exif

        _print_exif({271: "FCMD", 272: "Test"})
        out = capsys.readouterr().out
        assert "271" in out
        assert "FCMD" in out


# ---------------------------------------------------------------------- #
# 兼容性标志验证
# ---------------------------------------------------------------------- #
def test_optional_deps_available() -> None:
    """测试环境应已安装可选依赖（img/pdf）。"""
    assert HAS_PIL is True, "Pillow 未安装，img extra 测试无法运行"
    assert HAS_PYMUPDF is True, "PyMuPDF 未安装，pdf extra 测试无法运行"
    assert HAS_PYPDF is True, "pypdf 未安装，pdf extra 测试无法运行"
