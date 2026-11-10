"""pdftool 工具测试。

验证 ``fcmd.tools.pdf.pdftool`` 子命令门面与 ``fcmd.tools.pdf.pdfbase`` 公共逻辑：
- 工具注册
- 各子命令通过 run_tool 调用
- 可选依赖缺失 guard 覆盖
- 边界分支
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fcmd as fx
import fcmd.tools.pdf.pdftool
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.pdf.pdfbase import HAS_PYMUPDF, HAS_PYPDF, pdf_to_images


# ---------------------------------------------------------------------- #
# fixtures: 生成测试 PDF
# ---------------------------------------------------------------------- #
@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    """生成 50x50 RGB 测试图片（供 sample_pdf_with_image 使用）。"""
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
    page = doc.new_page(width=300, height=400)
    page.insert_image(fitz.Rect(20, 20, 120, 120), filename=str(sample_image))
    p = tmp_path / "with_image.pdf"
    doc.save(str(p))
    doc.close()
    return p


@pytest.fixture
def sample_pdf_ab(tmp_path: Path) -> tuple[Path, Path]:
    """生成两个 2 页、页文本可区分的 PDF（A1/A2 与 B1/B2）。"""
    import fitz

    paths: list[Path] = []
    for prefix in ("A", "B"):
        doc = fitz.open()
        for num in (1, 2):
            page = doc.new_page(width=200, height=300)
            page.insert_text((20, 50), f"{prefix}{num} content", fontsize=12)
        p = tmp_path / f"{prefix.lower()}.pdf"
        doc.save(str(p))
        doc.close()
        paths.append(p)
    return paths[0], paths[1]


def _page_texts(pdf_path: Path) -> list[str]:
    """提取 PDF 各页文本（供页序断言）。"""
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    return [(page.extract_text() or "").strip() for page in reader.pages]


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestRegistration:
    """pdftool 注册验证。"""

    def test_both_tools_registered(self) -> None:
        """pdftool 应在 _TOOL_REGISTRY 中注册。"""
        assert "pdftool" in _TOOL_REGISTRY

    def test_pdftool_subcommands(self) -> None:
        """pdftool 应有 18 个子命令。"""
        subs = fx.list_subcommands("pdftool")
        expected = (
            "m",
            "s",
            "c",
            "e",
            "d",
            "xt",
            "xi",
            "w",
            "r",
            "crop",
            "i",
            "ocr",
            "reorder",
            "img",
            "repair",
            "attach",
            "compare",
            "layout",
        )
        for sc in expected:
            assert sc in subs, f"子命令 {sc!r} 未注册"


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
        monkeypatch.setattr("fcmd.tools.pdf.pdfbase.HAS_PYMUPDF", False)
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
        monkeypatch.setattr("fcmd.tools.pdf.pdfbase.HAS_PYPDF", False)
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
        monkeypatch.setattr("fcmd.tools.pdf.pdfbase.HAS_PYMUPDF", False)
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
        monkeypatch.setattr("fcmd.tools.pdf.pdfbase.HAS_PYPDF", False)
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
            ("xt", ["in.pdf"]),
            ("xi", ["in.pdf"]),
            ("w", ["in.pdf"]),
            ("r", ["in.pdf"]),
            ("crop", ["in.pdf"]),
            ("img", ["in.pdf"]),
            ("repair", ["in.pdf"]),
            ("compare", ["a.pdf", "b.pdf"]),
            ("layout", ["in.pdf"]),
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
        monkeypatch.setattr("fcmd.tools.pdf.pdfbase.HAS_PYMUPDF", False)
        code = run_tool("pdftool", [subcommand, *args])
        assert code == 0
        assert "未安装 PyMuPDF" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "subcommand,args",
        [
            ("s", ["in.pdf"]),
            ("d", ["in.pdf", "--password", "x"]),
            ("reorder", ["in.pdf", "out.pdf", "0"]),
            ("attach", ["add", "in.pdf"]),
            ("attach", ["extract", "in.pdf"]),
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
        monkeypatch.setattr("fcmd.tools.pdf.pdfbase.HAS_PYPDF", False)
        code = run_tool("pdftool", [subcommand, *args])
        assert code == 0
        assert "未安装 pypdf" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# 拆分/合并高级参数测试
# ---------------------------------------------------------------------- #
class TestSplitParams:
    """拆分子命令的高级参数（页序/步长/分组）。"""

    def test_split_default_single_pages(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """默认拆分为单页（与既有行为兼容）。"""
        a, _ = sample_pdf_ab
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(a), "--output-dir", str(out_dir)])
        assert code == 0
        pages = sorted(out_dir.glob("*.pdf"))
        assert len(pages) == 2
        assert _page_texts(out_dir / "a_page_1.pdf") == ["A1 content"]

    def test_split_every(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """固定步长拆分为多页一份。"""
        a, _ = sample_pdf_ab
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(a), "--output-dir", str(out_dir), "--every", "2"])
        assert code == 0
        parts = list(out_dir.glob("*.pdf"))
        assert len(parts) == 1
        assert _page_texts(out_dir / "a_part_1.pdf") == ["A1 content", "A2 content"]

    def test_split_reverse(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """倒序拆分：第一份为最后一页。"""
        a, _ = sample_pdf_ab
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(a), "--output-dir", str(out_dir), "--order", "r"])
        assert code == 0
        assert _page_texts(out_dir / "a_page_1.pdf") == ["A2 content"]
        assert _page_texts(out_dir / "a_page_2.pdf") == ["A1 content"]

    def test_split_groups(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """自定义分组（不定步长）：1-2 一份、3 一份。"""
        a, _ = sample_pdf_ab
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(a), "--output-dir", str(out_dir), "--groups", "1-2"])
        assert code == 0
        parts = list(out_dir.glob("*.pdf"))
        assert len(parts) == 1
        assert _page_texts(out_dir / "a_part_1.pdf") == ["A1 content", "A2 content"]

    def test_split_groups_reverse(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """分组 + 倒序：组内页码倒排。"""
        a, _ = sample_pdf_ab
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(a), "--output-dir", str(out_dir), "--order", "r", "--groups", "1-2"])
        assert code == 0
        assert _page_texts(out_dir / "a_part_1.pdf") == ["A2 content", "A1 content"]

    def test_split_invalid_order(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非法页序打印错误并返回。"""
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(sample_pdf), "--output-dir", str(out_dir), "--order", "x"])
        assert code == 0
        assert "错误" in capsys.readouterr().out
        assert not out_dir.exists()

    def test_split_invalid_every(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非正步长打印错误并返回。"""
        out_dir = tmp_path / "split"
        code = run_tool("pdftool", ["s", str(sample_pdf), "--output-dir", str(out_dir), "--every", "0"])
        assert code == 0
        assert "错误" in capsys.readouterr().out


class TestMergeParams:
    """合并子命令的高级参数（模式/逐文件页序/页码筛选）。"""

    def test_merge_default_concat(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """默认顺序拼接（与既有行为兼容）。"""
        a, b = sample_pdf_ab
        out = tmp_path / "merged.pdf"
        code = run_tool("pdftool", ["m", str(a), str(b), "--output-path", str(out)])
        assert code == 0
        assert _page_texts(out) == ["A1 content", "A2 content", "B1 content", "B2 content"]

    def test_merge_interleave_mixed_order(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """混合交叉合并：01.pdf 正序 + 02.pdf 倒序轮流取页。"""
        a, b = sample_pdf_ab
        out = tmp_path / "merged.pdf"
        code = run_tool(
            "pdftool",
            ["m", str(a), str(b), "--output-path", str(out), "--mode", "interleave", "--orders", "f", "r"],
        )
        assert code == 0
        assert _page_texts(out) == ["A1 content", "B2 content", "A2 content", "B1 content"]

    def test_merge_orders_reverse(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """逐文件页序：两文件均倒序拼接。"""
        a, b = sample_pdf_ab
        out = tmp_path / "merged.pdf"
        code = run_tool(
            "pdftool",
            ["m", str(a), str(b), "--output-path", str(out), "--orders", "r", "r"],
        )
        assert code == 0
        assert _page_texts(out) == ["A2 content", "A1 content", "B2 content", "B1 content"]

    def test_merge_pages_filter(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """逐文件页码筛选：a 取第 1 页、b 全选。"""
        a, b = sample_pdf_ab
        out = tmp_path / "merged.pdf"
        code = run_tool(
            "pdftool",
            ["m", str(a), str(b), "--output-path", str(out), "--pages", "1", "-"],
        )
        assert code == 0
        assert _page_texts(out) == ["A1 content", "B1 content", "B2 content"]

    def test_merge_nonexistent_keeps_alignment(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """不存在的输入跳过时，orders/pages 按原始位置对齐。"""
        a, b = sample_pdf_ab
        nonexistent = tmp_path / "nope.pdf"
        out = tmp_path / "merged.pdf"
        code = run_tool(
            "pdftool",
            ["m", str(nonexistent), str(a), str(b), "--output-path", str(out), "--orders", "r", "r", "r"],
        )
        assert code == 0
        assert _page_texts(out) == ["A2 content", "A1 content", "B2 content", "B1 content"]

    def test_merge_invalid_order(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非法页序打印错误并返回。"""
        out = tmp_path / "merged.pdf"
        code = run_tool(
            "pdftool",
            ["m", str(sample_pdf), "--output-path", str(out), "--orders", "x"],
        )
        assert code == 0
        assert "错误" in capsys.readouterr().out
        assert not out.exists()


class TestPageFilterParams:
    """页码筛选参数（--pages）在各子命令的行为。"""

    def test_extract_text_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        """提取文本仅包含筛选页。"""
        out = tmp_path / "out.txt"
        code = run_tool("pdftool", ["xt", str(sample_pdf), "--output-path", str(out), "--pages", "1"])
        assert code == 0
        content = out.read_text(encoding="utf-8")
        assert "Page one" in content
        assert "Page two" not in content

    def test_to_images_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        """转图片仅输出筛选页。"""
        out_dir = tmp_path / "imgs"
        code = run_tool(
            "pdftool", ["img", str(sample_pdf), "--output-dir", str(out_dir), "--dpi", "72", "--pages", "2"]
        )
        assert code == 0
        pngs = list(out_dir.glob("*.png"))
        assert len(pngs) == 1
        assert pngs[0].name.endswith("_page_2.png")

    def test_rotate_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        """旋转仅作用于筛选页（其余页保留）。"""
        out = tmp_path / "rotated.pdf"
        code = run_tool("pdftool", ["r", str(sample_pdf), "--output-path", str(out), "--pages", "1"])
        assert code == 0
        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 2
            assert doc[0].rotation == 90
            assert doc[1].rotation == 0
        finally:
            doc.close()

    def test_extract_images_invalid_pages(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非法页码表达式打印错误并返回。"""
        out_dir = tmp_path / "imgs"
        code = run_tool("pdftool", ["xi", str(sample_pdf), "--output-dir", str(out_dir), "--pages", "x"])
        assert code == 0
        assert "错误" in capsys.readouterr().out

    def test_watermark_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        """水印仅添加到筛选页（其余页保留）。"""
        out = tmp_path / "watermarked.pdf"
        code = run_tool("pdftool", ["w", str(sample_pdf), "--output-path", str(out), "--text", "DRAFT", "--pages", "2"])
        assert code == 0
        assert out.exists()
        texts = _page_texts(out)
        assert "DRAFT" not in texts[0]
        assert "DRAFT" in texts[1]

    def test_crop_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        """裁剪仅作用于筛选页（其余页保留）。"""
        out = tmp_path / "cropped.pdf"
        code = run_tool("pdftool", ["crop", str(sample_pdf), "--output-path", str(out), "--pages", "1"])
        assert code == 0
        assert out.exists()


# ---------------------------------------------------------------------- #
# 附件管理 / 差异比较 / 页面布局
# ---------------------------------------------------------------------- #
class TestAttachParams:
    """attach 子命令（添加/提取内嵌附件）。"""

    def test_attach_add_and_extract_roundtrip(self, sample_pdf: Path, sample_image: Path, tmp_path: Path) -> None:
        """添加附件后可原样提取。"""
        attached = tmp_path / "attached.pdf"
        code = run_tool(
            "pdftool",
            ["attach", "add", str(sample_pdf), "--output-path", str(attached), "--files", str(sample_image)],
        )
        assert code == 0
        assert attached.exists()

        out_dir = tmp_path / "attachments"
        code = run_tool("pdftool", ["attach", "extract", str(attached), "--output-dir", str(out_dir)])
        assert code == 0
        assert (out_dir / sample_image.name).read_bytes() == sample_image.read_bytes()

    def test_attach_add_default_output(self, sample_pdf: Path, sample_image: Path) -> None:
        """未指定输出时默认生成 <输入名>.attached.pdf。"""
        code = run_tool("pdftool", ["attach", "add", str(sample_pdf), "--files", str(sample_image)])
        assert code == 0
        assert (sample_pdf.parent / "sample.attached.pdf").exists()

    def test_attach_add_missing_files(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """未提供附件文件时打印错误且不生成输出。"""
        out = tmp_path / "attached.pdf"
        code = run_tool("pdftool", ["attach", "add", str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        assert "没有可添加的附件文件" in capsys.readouterr().out
        assert not out.exists()

    def test_attach_add_skips_nonexistent(
        self,
        sample_pdf: Path,
        sample_image: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """不存在的附件被跳过，已存在的仍正常添加。"""
        out = tmp_path / "attached.pdf"
        missing = tmp_path / "missing.txt"
        code = run_tool(
            "pdftool",
            [
                "attach",
                "add",
                str(sample_pdf),
                "--output-path",
                str(out),
                "--files",
                str(missing),
                str(sample_image),
            ],
        )
        assert code == 0
        assert "已跳过" in capsys.readouterr().out
        assert out.exists()

    def test_attach_add_preserves_existing(self, sample_pdf: Path, sample_image: Path, tmp_path: Path) -> None:
        """再次添加附件时保留已有附件。"""
        first = tmp_path / "first.pdf"
        run_tool(
            "pdftool",
            ["attach", "add", str(sample_pdf), "--output-path", str(first), "--files", str(sample_image)],
        )

        note = tmp_path / "note.txt"
        note.write_text("hello", encoding="utf-8")
        second = tmp_path / "second.pdf"
        run_tool(
            "pdftool",
            ["attach", "add", str(first), "--output-path", str(second), "--files", str(note)],
        )

        out_dir = tmp_path / "extracted"
        run_tool("pdftool", ["attach", "extract", str(second), "--output-dir", str(out_dir)])
        assert (out_dir / sample_image.name).exists()
        assert (out_dir / "note.txt").read_text(encoding="utf-8") == "hello"

    def test_attach_extract_no_attachments(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """无附件时打印提示且不创建输出目录。"""
        out_dir = tmp_path / "attachments"
        code = run_tool("pdftool", ["attach", "extract", str(sample_pdf), "--output-dir", str(out_dir)])
        assert code == 0
        assert "没有附件" in capsys.readouterr().out
        assert not out_dir.exists()


class TestCompareParams:
    """compare 子命令（差异高亮输出）。"""

    def test_compare_identical(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """相同文件无差异页、无高亮标注。"""
        out = tmp_path / "diff.pdf"
        code = run_tool("pdftool", ["compare", str(sample_pdf), str(sample_pdf), "--output-path", str(out)])
        assert code == 0
        assert "差异页 0 页" in capsys.readouterr().out

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 2
            assert list(doc[0].annots() or []) == []
        finally:
            doc.close()

    def test_compare_highlight_added_words(
        self,
        sample_pdf_ab: tuple[Path, Path],
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """文本不同时输出差异页并生成高亮标注。"""
        a, b = sample_pdf_ab
        out = tmp_path / "diff.pdf"
        code = run_tool("pdftool", ["compare", str(a), str(b), "--output-path", str(out)])
        assert code == 0
        assert "差异页 2 页" in capsys.readouterr().out

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 2
            assert len(list(doc[0].annots() or [])) >= 1
        finally:
            doc.close()

    def test_compare_default_output(self, sample_pdf_ab: tuple[Path, Path]) -> None:
        """未指定输出时默认生成 <基准名>.diff.pdf。"""
        a, b = sample_pdf_ab
        code = run_tool("pdftool", ["compare", str(a), str(b)])
        assert code == 0
        assert (a.parent / "a.diff.pdf").exists()

    def test_compare_page_count_mismatch(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """页数不一致时：多出的页提示未纳入，缺少的页按全量新增高亮。"""
        out_dir = tmp_path / "split"
        run_tool("pdftool", ["s", str(sample_pdf), "--output-dir", str(out_dir)])
        first = out_dir / "sample_page_1.pdf"

        longer = tmp_path / "longer.pdf"
        code = run_tool("pdftool", ["compare", str(sample_pdf), str(first), "--output-path", str(longer)])
        assert code == 0
        assert "未纳入输出" in capsys.readouterr().out

        shorter = tmp_path / "shorter.pdf"
        code = run_tool("pdftool", ["compare", str(first), str(sample_pdf), "--output-path", str(shorter)])
        assert code == 0
        assert "差异页 1 页" in capsys.readouterr().out


class TestLayoutParams:
    """layout 子命令（尺寸/方向/边距）。"""

    def test_layout_a4_landscape(self, sample_pdf: Path, tmp_path: Path) -> None:
        """A4 横向输出页面尺寸为 842x595。"""
        out = tmp_path / "layout.pdf"
        code = run_tool(
            "pdftool",
            ["layout", str(sample_pdf), "--output-path", str(out), "--page-size", "a4", "--orientation", "landscape"],
        )
        assert code == 0
        assert out.exists()

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 2
            assert round(doc[0].rect.width) == 842
            assert round(doc[0].rect.height) == 595
        finally:
            doc.close()

    def test_layout_default_keeps_size(self, sample_pdf: Path) -> None:
        """未指定尺寸/方向时保持原页面尺寸，默认输出 <输入名>.layout.pdf。"""
        code = run_tool("pdftool", ["layout", str(sample_pdf)])
        assert code == 0
        out = sample_pdf.parent / "sample.layout.pdf"
        assert out.exists()

        import fitz

        doc = fitz.open(str(out))
        try:
            assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (200, 300)
        finally:
            doc.close()

    def test_layout_margins(self, sample_pdf: Path, tmp_path: Path) -> None:
        """指定四边距后正常输出。"""
        out = tmp_path / "layout.pdf"
        code = run_tool(
            "pdftool",
            [
                "layout",
                str(sample_pdf),
                "--output-path",
                str(out),
                "--page-size",
                "a4",
                "--margin-left",
                "10",
                "--margin-right",
                "10",
                "--margin-top",
                "20",
                "--margin-bottom",
                "20",
            ],
        )
        assert code == 0
        assert out.exists()

    def test_layout_margin_too_large(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """边距超过页面尺寸时打印错误且不生成输出。"""
        out = tmp_path / "layout.pdf"
        code = run_tool("pdftool", ["layout", str(sample_pdf), "--output-path", str(out), "--margin-left", "500"])
        assert code == 0
        assert "无法排版" in capsys.readouterr().out
        assert not out.exists()


class TestToImagesFormat:
    """img 子命令的格式与质量参数。"""

    def test_to_images_jpeg(self, sample_pdf: Path, tmp_path: Path) -> None:
        """jpeg 格式输出 .jpg 且文件头为 JPEG。"""
        out_dir = tmp_path / "jpgs"
        code = run_tool(
            "pdftool",
            [
                "img",
                str(sample_pdf),
                "--output-dir",
                str(out_dir),
                "--dpi",
                "72",
                "--image-format",
                "jpeg",
                "--quality",
                "60",
            ],
        )
        assert code == 0
        images = sorted(out_dir.glob("*.jpg"))
        assert len(images) == 2
        assert images[0].read_bytes()[:2] == b"\xff\xd8"

    def test_to_images_invalid_format(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """不支持的格式打印错误且不生成输出。"""
        out_dir = tmp_path / "bad"
        pdf_to_images(sample_pdf, out_dir, image_format="gif")
        assert "不支持的图片格式" in capsys.readouterr().out
        assert not out_dir.exists()

    def test_to_images_invalid_quality(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """质量超出 1-100 时打印错误。"""
        out_dir = tmp_path / "bad"
        pdf_to_images(sample_pdf, out_dir, quality=0)
        assert "取值范围为 1-100" in capsys.readouterr().out
        assert not out_dir.exists()


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


# ---------------------------------------------------------------------- #
# 兼容性标志验证
# ---------------------------------------------------------------------- #
def test_optional_deps_available() -> None:
    """测试环境应已安装可选依赖（pdf）。"""
    assert HAS_PYMUPDF is True, "PyMuPDF 未安装，pdf extra 测试无法运行"
    assert HAS_PYPDF is True, "pypdf 未安装，pdf extra 测试无法运行"
