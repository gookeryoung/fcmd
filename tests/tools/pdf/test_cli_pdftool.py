"""pdftool 工具测试。

验证 ``fcmd.tools.pdf.pdftool`` 子命令门面与 ``fcmd.tools.pdf.pdfbase`` 公共逻辑：
- 工具注册
- 各子命令通过 run_tool 调用
- 可选依赖缺失 guard 覆盖
- 边界分支
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast

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


class TestInfoMetadata:
    """i 子命令的元数据与详细统计。"""

    def test_info_full_metadata(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """输出主题/关键词/创建者/生成器并格式化日期。"""
        import fitz

        src = tmp_path / "meta.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.set_metadata(
            {
                "title": "T",
                "author": "A",
                "subject": "S",
                "keywords": "K",
                "creator": "C",
                "producer": "P",
                "creationDate": "D:20260102030405",
                "modDate": "D:20260102",
            }
        )
        doc.save(str(src))
        doc.close()

        code = run_tool("pdftool", ["i", str(src)])
        assert code == 0
        out = capsys.readouterr().out
        assert "主题: S" in out
        assert "关键词: K" in out
        assert "创建者: C" in out
        assert "生成器: P" in out
        assert "创建日期: 2026-01-02 03:04:05" in out
        assert "修改日期: 2026-01-02" in out

    def test_info_verbose(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """verbose 输出 PDF 版本、加密与线性化状态。"""
        code = run_tool("pdftool", ["i", str(sample_pdf), "--verbose"])
        assert code == 0
        out = capsys.readouterr().out
        assert "PDF 版本:" in out
        assert "是否加密: 否" in out
        assert "是否线性化:" in out
        assert "文件大小:" in out


class TestWatermarkFeatures:
    """w 子命令的图片水印与字号/旋转/缩放。"""

    def test_watermark_text_font_size(self, sample_pdf: Path, tmp_path: Path) -> None:
        """文本水印使用指定字号。"""
        out = tmp_path / "wm.pdf"
        code = run_tool(
            "pdftool",
            ["w", str(sample_pdf), "--output-path", str(out), "--text", "DRAFT", "--font-size", "20"],
        )
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            text_dict = cast("dict[str, Any]", doc[0].get_text("dict"))
            sizes = [
                span["size"]
                for block in text_dict["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
                if "DRAFT" in span["text"]
            ]
            assert sizes
            assert abs(sizes[0] - 20) < 1
        finally:
            doc.close()

    def test_watermark_text_rotation(self, sample_pdf: Path, tmp_path: Path) -> None:
        """文本水印可按 90 度倍数旋转。"""
        out = tmp_path / "wm.pdf"
        code = run_tool(
            "pdftool",
            ["w", str(sample_pdf), "--output-path", str(out), "--rotation", "90"],
        )
        assert code == 0
        assert out.exists()

    def test_watermark_image(self, sample_pdf: Path, sample_image: Path, tmp_path: Path) -> None:
        """图片水印嵌入到页面中。"""
        out = tmp_path / "wm.pdf"
        code = run_tool(
            "pdftool",
            ["w", str(sample_pdf), "--output-path", str(out), "--image", str(sample_image), "--scale", "0.4"],
        )
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc[0].get_images()
        finally:
            doc.close()

    def test_watermark_image_rotation(self, sample_pdf: Path, sample_image: Path, tmp_path: Path) -> None:
        """图片水印支持 90 度旋转且不报错。"""
        out = tmp_path / "wm.pdf"
        code = run_tool(
            "pdftool",
            [
                "w",
                str(sample_pdf),
                "--output-path",
                str(out),
                "--image",
                str(sample_image),
                "--rotation",
                "90",
            ],
        )
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc[0].get_images()
        finally:
            doc.close()

    def test_watermark_tall_image_fits_height(self, sample_pdf: Path, tmp_path: Path) -> None:
        """高瘦图片按页面高度等比收缩。"""
        import fitz

        img_path = tmp_path / "tall.png"
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 200), False)
        pix.clear_with(200)
        pix.save(str(img_path))

        out = tmp_path / "wm.pdf"
        code = run_tool(
            "pdftool",
            ["w", str(sample_pdf), "--output-path", str(out), "--image", str(img_path), "--scale", "1"],
        )
        assert code == 0

        doc = fitz.open(str(out))
        try:
            assert doc[0].get_images()
        finally:
            doc.close()

    def test_watermark_invalid_rotation(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非 90 倍数旋转打印错误且不生成输出。"""
        out = tmp_path / "wm.pdf"
        code = run_tool("pdftool", ["w", str(sample_pdf), "--output-path", str(out), "--rotation", "45"])
        assert code == 0
        assert "90 的倍数" in capsys.readouterr().out
        assert not out.exists()

    def test_watermark_invalid_scale(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """缩放比例越界打印错误。"""
        out = tmp_path / "wm.pdf"
        code = run_tool("pdftool", ["w", str(sample_pdf), "--output-path", str(out), "--scale", "0"])
        assert code == 0
        assert "取值范围" in capsys.readouterr().out
        assert not out.exists()

    def test_watermark_missing_image(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """水印图片不存在时打印错误。"""
        out = tmp_path / "wm.pdf"
        missing = tmp_path / "missing.png"
        code = run_tool(
            "pdftool",
            ["w", str(sample_pdf), "--output-path", str(out), "--image", str(missing)],
        )
        assert code == 0
        assert "水印图片不存在" in capsys.readouterr().out
        assert not out.exists()

    def test_watermark_invalid_image(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非图片文件作为水印时打印可读错误。"""
        out = tmp_path / "wm.pdf"
        code = run_tool(
            "pdftool",
            ["w", str(sample_pdf), "--output-path", str(out), "--image", str(sample_pdf)],
        )
        assert code == 0
        assert "无法读取水印图片" in capsys.readouterr().out
        assert not out.exists()


class TestReorderFeatures:
    """reorder 子命令的删除页与插入空白页。"""

    def test_reorder_delete_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        """all + --delete 按 1 基页码删除。"""
        out = tmp_path / "out.pdf"
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "all", "--delete", "1"])
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 1
            assert "Page two" in doc[0].get_text()
        finally:
            doc.close()

    def test_reorder_insert_blank(self, sample_pdf: Path, tmp_path: Path) -> None:
        """--insert-blank 在指定位置插入空白页。"""
        out = tmp_path / "out.pdf"
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "all", "--insert-blank", "2:1"])
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 3
            assert cast("str", doc[1].get_text()).strip() == ""
            assert "Page two" in doc[2].get_text()
        finally:
            doc.close()

    def test_reorder_delete_and_insert(self, sample_pdf: Path, tmp_path: Path) -> None:
        """重排、删除、插入可组合使用。"""
        out = tmp_path / "out.pdf"
        code = run_tool(
            "pdftool",
            ["reorder", str(sample_pdf), str(out), "1", "0", "--delete", "1", "--insert-blank", "1:1"],
        )
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 2
            assert cast("str", doc[0].get_text()).strip() == ""
            assert "Page two" in doc[1].get_text()
        finally:
            doc.close()

    def test_reorder_invalid_insert_spec(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非法插入表达式打印错误且不生成输出。"""
        out = tmp_path / "out.pdf"
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "all", "--insert-blank", "0:1"])
        assert code == 0
        assert "不合法" in capsys.readouterr().out
        assert not out.exists()

    def test_reorder_malformed_insert_spec(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """非数字插入表达式打印格式错误。"""
        out = tmp_path / "out.pdf"
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "all", "--insert-blank", "abc"])
        assert code == 0
        assert "无效的插入空白页格式" in capsys.readouterr().out
        assert not out.exists()

    def test_reorder_skips_non_numeric_order(self, sample_pdf: Path, tmp_path: Path) -> None:
        """order 中的非数字项被跳过，其余页正常输出。"""
        out = tmp_path / "out.pdf"
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "bogus", "0"])
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            assert doc.page_count == 1
            assert "Page one" in doc[0].get_text()
        finally:
            doc.close()

    def test_reorder_invalid_delete(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """非法删除表达式打印错误且不生成输出。"""
        out = tmp_path / "out.pdf"
        code = run_tool("pdftool", ["reorder", str(sample_pdf), str(out), "all", "--delete", "abc"])
        assert code == 0
        assert capsys.readouterr().out
        assert not out.exists()


class TestCropAuto:
    """crop 子命令的自动内容边界裁剪。"""

    def test_crop_auto_shrinks_to_content(self, sample_pdf: Path, tmp_path: Path) -> None:
        """自动模式按文本边界裁剪，页面尺寸变小。"""
        out = tmp_path / "auto.pdf"
        code = run_tool(
            "pdftool",
            ["crop", str(sample_pdf), "--output-path", str(out), "--auto", "--margin", "5"],
        )
        assert code == 0

        import fitz

        doc = fitz.open(str(out))
        try:
            rect = doc[0].rect
            assert rect.width < 200
            assert rect.height < 300
            assert rect.width > 60
        finally:
            doc.close()

    def test_crop_auto_blank_page_keeps_size(self, tmp_path: Path) -> None:
        """无文本/图片内容时保持原页面尺寸。"""
        import fitz

        src = tmp_path / "blank.pdf"
        doc = fitz.open()
        doc.new_page(width=200, height=300)
        doc.save(str(src))
        doc.close()

        out = tmp_path / "auto.pdf"
        code = run_tool("pdftool", ["crop", str(src), "--output-path", str(out), "--auto"])
        assert code == 0

        doc = fitz.open(str(out))
        try:
            assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (200, 300)
        finally:
            doc.close()


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
# 目录批处理（P3）
# ---------------------------------------------------------------------- #
class TestBatchProcessing:
    """目录输入、递归、跳过已存在、静默输出与目录配对。"""

    @staticmethod
    def _copy_pdfs(files: list[Path], target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        for src in files:
            shutil.copy(src, target_dir / src.name)
        return target_dir

    def test_split_directory_batch(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """目录输入：每个 PDF 拆分到独立子目录，非 PDF 文件被忽略。"""
        a, b = sample_pdf_ab
        src_dir = self._copy_pdfs([a, b], tmp_path / "src")
        (src_dir / "note.txt").write_text("skip me", encoding="utf-8")
        out_dir = tmp_path / "out"
        code = run_tool("pdftool", ["s", str(src_dir), "--output-dir", str(out_dir)])
        assert code == 0
        assert (out_dir / "a" / "a_page_1.pdf").exists()
        assert (out_dir / "b" / "b_page_2.pdf").exists()
        assert "批处理完成: 2/2 个文件" in capsys.readouterr().out

    def test_split_directory_single_pdf_no_summary(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """目录中仅一个 PDF 时不打印批处理汇总。"""
        src_dir = self._copy_pdfs([sample_pdf], tmp_path / "src")
        out_dir = tmp_path / "out"
        code = run_tool("pdftool", ["s", str(src_dir), "--output-dir", str(out_dir)])
        assert code == 0
        assert (out_dir / "sample" / "sample_page_1.pdf").exists()
        assert "批处理完成" not in capsys.readouterr().out

    def test_split_recursive(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """--recursive 递归子目录；默认只处理顶层。"""
        a, b = sample_pdf_ab
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        shutil.copy(a, src_dir / "a.pdf")
        nested = src_dir / "nested"
        nested.mkdir()
        shutil.copy(b, nested / "b.pdf")

        flat_out = tmp_path / "flat"
        run_tool("pdftool", ["s", str(src_dir), "--output-dir", str(flat_out)])
        assert (flat_out / "a").exists()
        assert not (flat_out / "b").exists()

        deep_out = tmp_path / "deep"
        run_tool("pdftool", ["s", str(src_dir), "--output-dir", str(deep_out), "--recursive"])
        assert (deep_out / "b" / "b_page_1.pdf").exists()

    def test_split_skip_existing(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--skip-existing 跳过已有输出的 PDF，其余照常处理。"""
        a, b = sample_pdf_ab
        src_dir = self._copy_pdfs([a, b], tmp_path / "src")
        out_dir = tmp_path / "out"
        stale = out_dir / "a" / "stale.pdf"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
        code = run_tool("pdftool", ["s", str(src_dir), "--output-dir", str(out_dir), "--skip-existing"])
        assert code == 0
        assert stale.read_bytes() == b"stale"
        assert [p.name for p in (out_dir / "a").iterdir()] == ["stale.pdf"]
        assert (out_dir / "b" / "b_page_1.pdf").exists()
        out = capsys.readouterr().out
        assert "跳过已存在" in out
        assert "批处理完成: 1/2 个文件" in out

    def test_split_silent(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--silent 不输出逐文件日志与汇总。"""
        a, b = sample_pdf_ab
        src_dir = self._copy_pdfs([a, b], tmp_path / "src")
        out_dir = tmp_path / "out"
        code = run_tool("pdftool", ["s", str(src_dir), "--output-dir", str(out_dir), "--silent"])
        assert code == 0
        out = capsys.readouterr().out
        assert "拆分完成" not in out
        assert "批处理完成" not in out
        assert (out_dir / "a" / "a_page_1.pdf").exists()

    def test_directory_without_pdfs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """空目录打印错误且不产生输出。"""
        src_dir = tmp_path / "empty"
        src_dir.mkdir()
        out_dir = tmp_path / "out"
        code = run_tool("pdftool", ["s", str(src_dir), "--output-dir", str(out_dir)])
        assert code == 0
        assert "目录中没有 PDF 文件" in capsys.readouterr().out
        assert not out_dir.exists()

    def test_xt_directory_batch(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """xt 目录输入：每个 PDF 输出同名 .txt。"""
        a, b = sample_pdf_ab
        src_dir = self._copy_pdfs([a, b], tmp_path / "src")
        out_dir = tmp_path / "txt"
        code = run_tool("pdftool", ["xt", str(src_dir), "--output-dir", str(out_dir)])
        assert code == 0
        assert "A1 content" in (out_dir / "a.txt").read_text(encoding="utf-8")
        assert "B2 content" in (out_dir / "b.txt").read_text(encoding="utf-8")

    def test_split_single_skip_existing(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """单文件模式 --skip-existing 检测同前缀输出。"""
        out_dir = tmp_path / "split"
        out_dir.mkdir()
        stale = out_dir / "sample_page_1.pdf"
        stale.write_bytes(b"stale")
        code = run_tool("pdftool", ["s", str(sample_pdf), "--output-dir", str(out_dir), "--skip-existing"])
        assert code == 0
        assert stale.read_bytes() == b"stale"
        assert "跳过已存在" in capsys.readouterr().out

    def test_xt_single_skip_existing(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """单文件模式 --skip-existing 保留已有输出。"""
        out = tmp_path / "out.txt"
        out.write_text("keep", encoding="utf-8")
        code = run_tool("pdftool", ["xt", str(sample_pdf), "--output-path", str(out), "--skip-existing"])
        assert code == 0
        assert out.read_text(encoding="utf-8") == "keep"
        assert "跳过已存在" in capsys.readouterr().out

    def test_xt_default_output(self, sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """未指定输出且目标不存在时写入默认 output.txt。"""
        monkeypatch.chdir(tmp_path)
        code = run_tool("pdftool", ["xt", str(sample_pdf), "--skip-existing"])
        assert code == 0
        assert (tmp_path / "output.txt").exists()

    def test_img_directory_batch(self, sample_pdf: Path, tmp_path: Path) -> None:
        """img 目录输入：每个 PDF 输出到以 stem 命名的子目录。"""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        shutil.copy(sample_pdf, src_dir / "p1.pdf")
        shutil.copy(sample_pdf, src_dir / "p2.pdf")
        out_dir = tmp_path / "imgs"
        code = run_tool("pdftool", ["img", str(src_dir), "--output-dir", str(out_dir), "--dpi", "72"])
        assert code == 0
        assert sorted(p.name for p in (out_dir / "p1").glob("*.png")) == ["p1_page_1.png", "p1_page_2.png"]
        assert (out_dir / "p2" / "p2_page_2.png").exists()

    def test_img_single_skip_existing(self, sample_pdf: Path, tmp_path: Path) -> None:
        """img 单文件模式 --skip-existing 无匹配输出时正常转换。"""
        out_dir = tmp_path / "imgs"
        out_dir.mkdir()
        (out_dir / "other.txt").write_text("x", encoding="utf-8")
        code = run_tool(
            "pdftool", ["img", str(sample_pdf), "--output-dir", str(out_dir), "--dpi", "72", "--skip-existing"]
        )
        assert code == 0
        assert (out_dir / "sample_page_1.png").exists()

    def test_xi_directory_batch(self, sample_pdf_with_image: Path, tmp_path: Path) -> None:
        """xi 目录输入：每个 PDF 输出到以 stem 命名的子目录。"""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        shutil.copy(sample_pdf_with_image, src_dir / "pic1.pdf")
        shutil.copy(sample_pdf_with_image, src_dir / "pic2.pdf")
        out_dir = tmp_path / "imgs"
        code = run_tool("pdftool", ["xi", str(src_dir), "--output-dir", str(out_dir)])
        assert code == 0
        assert list((out_dir / "pic1").glob("page_*"))
        assert list((out_dir / "pic2").glob("page_*"))

    def test_xi_single_skip_existing(
        self, sample_pdf_with_image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """xi 单文件模式 --skip-existing 检测已提取图片。"""
        out_dir = tmp_path / "imgs"
        out_dir.mkdir()
        stale = out_dir / "page_1_img_1.png"
        stale.write_bytes(b"stale")
        code = run_tool("pdftool", ["xi", str(sample_pdf_with_image), "--output-dir", str(out_dir), "--skip-existing"])
        assert code == 0
        assert stale.read_bytes() == b"stale"
        assert "跳过已存在" in capsys.readouterr().out

    def test_compare_directory_batch(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """两个目录按同名文件配对批量比较。"""
        a, b = sample_pdf_ab
        dir1 = tmp_path / "v1"
        dir2 = tmp_path / "v2"
        dir1.mkdir()
        dir2.mkdir()
        shutil.copy(a, dir1 / "doc.pdf")
        shutil.copy(b, dir2 / "doc.pdf")
        out_dir = tmp_path / "diff"
        code = run_tool("pdftool", ["compare", str(dir1), str(dir2), "--output-dir", str(out_dir)])
        assert code == 0
        assert (out_dir / "doc.diff.pdf").exists()
        assert "批处理完成: 1/1 个文件" not in capsys.readouterr().out

    def test_compare_directory_missing_counterpart(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """第二个目录缺少同名文件时打印警告并跳过。"""
        a, b = sample_pdf_ab
        dir1 = tmp_path / "v1"
        dir2 = tmp_path / "v2"
        dir1.mkdir()
        dir2.mkdir()
        shutil.copy(a, dir1 / "a.pdf")
        shutil.copy(b, dir1 / "b.pdf")
        shutil.copy(a, dir2 / "a.pdf")
        out_dir = tmp_path / "diff"
        code = run_tool("pdftool", ["compare", str(dir1), str(dir2), "--output-dir", str(out_dir)])
        assert code == 0
        out = capsys.readouterr().out
        assert "警告: 第二个目录缺少对应文件: b.pdf" in out
        assert "批处理完成: 1/2 个文件" in out
        assert (out_dir / "a.diff.pdf").exists()
        assert not (out_dir / "b.diff.pdf").exists()

    def test_compare_mixed_inputs_error(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """目录与文件混用时打印错误。"""
        a, b = sample_pdf_ab
        dir1 = tmp_path / "v1"
        dir1.mkdir()
        shutil.copy(a, dir1 / "a.pdf")
        code = run_tool("pdftool", ["compare", str(dir1), str(b)])
        assert code == 0
        assert "两个输入都必须是目录" in capsys.readouterr().out

    def test_compare_directory_skip_existing(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """批量比较时 --skip-existing 保留已有差异文件。"""
        a, b = sample_pdf_ab
        dir1 = tmp_path / "v1"
        dir2 = tmp_path / "v2"
        dir1.mkdir()
        dir2.mkdir()
        shutil.copy(a, dir1 / "doc.pdf")
        shutil.copy(b, dir2 / "doc.pdf")
        out_dir = tmp_path / "diff"
        stale = out_dir / "doc.diff.pdf"
        stale.parent.mkdir()
        stale.write_bytes(b"stale")
        code = run_tool(
            "pdftool",
            ["compare", str(dir1), str(dir2), "--output-dir", str(out_dir), "--skip-existing"],
        )
        assert code == 0
        assert stale.read_bytes() == b"stale"
        assert "跳过已存在" in capsys.readouterr().out

    def test_compare_empty_directory(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """第一个目录没有 PDF 时打印错误。"""
        dir1 = tmp_path / "v1"
        dir2 = tmp_path / "v2"
        dir1.mkdir()
        dir2.mkdir()
        shutil.copy(sample_pdf, dir2 / "doc.pdf")
        code = run_tool("pdftool", ["compare", str(dir1), str(dir2)])
        assert code == 0
        assert "目录中没有 PDF 文件" in capsys.readouterr().out

    def test_compare_directory_silent(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--silent 抑制警告、跳过与汇总；无已有输出时正常比较。"""
        a, b = sample_pdf_ab
        dir1 = tmp_path / "v1"
        dir2 = tmp_path / "v2"
        dir1.mkdir()
        dir2.mkdir()
        shutil.copy(a, dir1 / "doc.pdf")
        shutil.copy(b, dir2 / "doc.pdf")
        shutil.copy(a, dir1 / "extra.pdf")
        shutil.copy(a, dir1 / "doc2.pdf")
        shutil.copy(a, dir2 / "doc2.pdf")
        out_dir = tmp_path / "diff"
        stale = out_dir / "doc.diff.pdf"
        stale.parent.mkdir()
        stale.write_bytes(b"stale")
        code = run_tool(
            "pdftool",
            [
                "compare",
                str(dir1),
                str(dir2),
                "--output-dir",
                str(out_dir),
                "--skip-existing",
                "--silent",
            ],
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "警告" not in out
        assert "跳过已存在" not in out
        assert "批处理完成" not in out
        assert stale.read_bytes() == b"stale"
        assert (out_dir / "doc2.diff.pdf").exists()

    def test_compare_single_output_dir(self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path) -> None:
        """单文件模式支持 --output-dir。"""
        a, b = sample_pdf_ab
        out_dir = tmp_path / "diff"
        code = run_tool("pdftool", ["compare", str(a), str(b), "--output-dir", str(out_dir)])
        assert code == 0
        assert (out_dir / "a.diff.pdf").exists()

    def test_compare_single_skip_existing(
        self, sample_pdf_ab: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """单文件模式 --skip-existing 保留已有差异文件。"""
        a, b = sample_pdf_ab
        out = tmp_path / "diff.pdf"
        out.write_bytes(b"stale")
        code = run_tool("pdftool", ["compare", str(a), str(b), "--output-path", str(out), "--skip-existing"])
        assert code == 0
        assert out.read_bytes() == b"stale"
        assert "跳过已存在" in capsys.readouterr().out

    def test_layout_directory_batch(self, sample_pdf: Path, tmp_path: Path) -> None:
        """layout 目录输入：每个 PDF 输出 <stem>.layout.pdf。"""
        src_dir = self._copy_pdfs([sample_pdf], tmp_path / "src")
        out_dir = tmp_path / "out"
        code = run_tool("pdftool", ["layout", str(src_dir), "--output-dir", str(out_dir), "--page-size", "a4"])
        assert code == 0
        assert (out_dir / "sample.layout.pdf").exists()

    def test_layout_recursive(self, sample_pdf: Path, tmp_path: Path) -> None:
        """layout --recursive 处理子目录中的 PDF。"""
        nested = tmp_path / "src" / "nested"
        nested.mkdir(parents=True)
        shutil.copy(sample_pdf, nested / "doc.pdf")

        flat_out = tmp_path / "flat"
        run_tool("pdftool", ["layout", str(tmp_path / "src"), "--output-dir", str(flat_out)])
        assert not (flat_out / "doc.layout.pdf").exists()

        deep_out = tmp_path / "deep"
        run_tool("pdftool", ["layout", str(tmp_path / "src"), "--output-dir", str(deep_out), "--recursive"])
        assert (deep_out / "doc.layout.pdf").exists()

    def test_layout_single_skip_existing(
        self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """layout 单文件模式 --skip-existing 保留已有输出。"""
        out = tmp_path / "out.layout.pdf"
        out.write_bytes(b"stale")
        code = run_tool("pdftool", ["layout", str(sample_pdf), "--output-path", str(out), "--skip-existing"])
        assert code == 0
        assert out.read_bytes() == b"stale"
        assert "跳过已存在" in capsys.readouterr().out


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
