"""pdftool 工具测试。

验证 ``fcmd.cli.pdftool`` 模块：
- 工具注册
- 各子命令通过 run_tool 调用
- 可选依赖缺失 guard 覆盖
- 边界分支
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.pdftool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.pdftool import HAS_PYMUPDF, HAS_PYPDF


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
    """pdftool 注册验证。"""

    def test_both_tools_registered(self) -> None:
        """pdftool 应在 _TOOL_REGISTRY 中注册。"""
        assert "pdftool" in _TOOL_REGISTRY

    def test_pdftool_subcommands(self) -> None:
        """pdftool 应有 15 个子命令。"""
        subs = fx.list_subcommands("pdftool")
        for sc in ("m", "s", "c", "e", "d", "xt", "xi", "w", "r", "crop", "i", "ocr", "reorder", "img", "repair"):
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


# ---------------------------------------------------------------------- #
# 兼容性标志验证
# ---------------------------------------------------------------------- #
def test_optional_deps_available() -> None:
    """测试环境应已安装可选依赖（pdf）。"""
    assert HAS_PYMUPDF is True, "PyMuPDF 未安装，pdf extra 测试无法运行"
    assert HAS_PYPDF is True, "pypdf 未安装，pdf extra 测试无法运行"
