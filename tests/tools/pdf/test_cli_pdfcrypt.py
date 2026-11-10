"""pdfcrypt 工具测试。

验证 ``fcmd.tools.pdf.pdfcrypt``：
- 工具注册（encrypt / decrypt 子命令）
- 密码校验、加密 / 解密往返、错误分支
- 目录扫描与加密状态筛选
- 并发批量处理与结果排序
- CLI 入口输出
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.pdf import pdfcrypt
from fcmd.tools.pdf.pdfcrypt import (
    BatchReport,
    CryptResult,
    collect_pdf_files,
    decrypt_pdf,
    encrypt_pdf,
    is_encrypted,
    process_files,
    validate_password,
)

PASSWORD = "secret12"


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """生成 2 页测试 PDF。"""
    import fitz

    doc = fitz.open()
    for text in ("Page one content", "Page two content"):
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), text, fontsize=12)
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def encrypted_pdf(tmp_path: Path, sample_pdf: Path) -> Path:
    """生成已加密的测试 PDF。"""
    return encrypt_pdf(sample_pdf, PASSWORD, tmp_path / "locked.pdf")


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestRegistration:
    """工具注册。"""

    def test_subcommands(self) -> None:
        assert set(_TOOL_REGISTRY["pdfcrypt"]) == {"encrypt", "decrypt"}


# ============================================================================ #
# 基础函数
# ============================================================================ #
class TestPassword:
    """密码校验。"""

    def test_valid(self) -> None:
        assert validate_password(PASSWORD) == PASSWORD

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="密码不能为空"):
            validate_password("")


class TestEncryptionState:
    """加密状态判断。"""

    def test_plain_pdf(self, sample_pdf: Path) -> None:
        assert is_encrypted(sample_pdf) is False

    def test_encrypted_pdf(self, encrypted_pdf: Path) -> None:
        assert is_encrypted(encrypted_pdf) is True

    def test_missing_dependency(self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfcrypt, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfcrypt, "pypdf", None)
        with pytest.raises(RuntimeError, match="pypdf"):
            is_encrypted(sample_pdf)


class TestEncryptDecrypt:
    """单文件加密 / 解密。"""

    def test_encrypt_default_output(self, sample_pdf: Path) -> None:
        output = encrypt_pdf(sample_pdf, PASSWORD)
        assert output == sample_pdf.with_suffix(".enc.pdf")
        assert output.exists()
        assert is_encrypted(output)

    def test_encrypt_explicit_output(self, sample_pdf: Path, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "enc.pdf"
        output = encrypt_pdf(sample_pdf, PASSWORD, target)
        assert output == target
        assert target.exists()

    def test_encrypt_already_encrypted(self, encrypted_pdf: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="已加密"):
            encrypt_pdf(encrypted_pdf, PASSWORD, tmp_path / "again.pdf")

    def test_encrypt_missing_dependency(self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfcrypt, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfcrypt, "pypdf", None)
        with pytest.raises(RuntimeError, match="pypdf"):
            encrypt_pdf(sample_pdf, PASSWORD)

    def test_decrypt_roundtrip_default_output(self, sample_pdf: Path, tmp_path: Path) -> None:
        locked = encrypt_pdf(sample_pdf, PASSWORD, tmp_path / "locked.pdf")
        output = decrypt_pdf(locked, PASSWORD)
        assert output == locked.with_suffix(".dec.pdf")
        assert output.exists()
        assert is_encrypted(output) is False

    def test_decrypt_explicit_output(self, encrypted_pdf: Path, tmp_path: Path) -> None:
        target = tmp_path / "out" / "plain.pdf"
        output = decrypt_pdf(encrypted_pdf, PASSWORD, target)
        assert output == target
        assert target.exists()

    def test_decrypt_wrong_password(self, encrypted_pdf: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="密码错误"):
            decrypt_pdf(encrypted_pdf, "wrong-password", tmp_path / "bad.pdf")

    def test_decrypt_plain_pdf(self, sample_pdf: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="未加密"):
            decrypt_pdf(sample_pdf, PASSWORD, tmp_path / "bad.pdf")

    def test_decrypt_missing_dependency(self, encrypted_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfcrypt, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfcrypt, "pypdf", None)
        with pytest.raises(RuntimeError, match="pypdf"):
            decrypt_pdf(encrypted_pdf, PASSWORD)


# ============================================================================ #
# 目录扫描
# ============================================================================ #
class TestCollect:
    """目录扫描与加密状态筛选。"""

    def test_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="目录不存在"):
            collect_pdf_files(tmp_path / "missing")

    def test_not_directory(self, sample_pdf: Path) -> None:
        with pytest.raises(NotADirectoryError, match="不是目录"):
            collect_pdf_files(sample_pdf)

    def test_all_pdf_case_insensitive(self, sample_pdf: Path, tmp_path: Path) -> None:
        upper = tmp_path / "UPPER.PDF"
        upper.write_bytes(sample_pdf.read_bytes())
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        found = collect_pdf_files(tmp_path)
        assert sorted(path.name for path in found) == ["UPPER.PDF", "sample.pdf"]

    def test_filter_by_state(self, sample_pdf: Path, encrypted_pdf: Path, tmp_path: Path) -> None:
        assert collect_pdf_files(tmp_path, encrypted=True) == [encrypted_pdf]
        assert collect_pdf_files(tmp_path, encrypted=False) == [sample_pdf]

    def test_recursive_flag(self, sample_pdf: Path, tmp_path: Path) -> None:
        nested = tmp_path / "nested"
        nested.mkdir()
        inner = nested / "inner.pdf"
        inner.write_bytes(sample_pdf.read_bytes())
        assert collect_pdf_files(tmp_path) == [inner, sample_pdf]
        assert collect_pdf_files(tmp_path, recursive=False) == [sample_pdf]

    def test_corrupt_pdf_skipped(self, sample_pdf: Path, tmp_path: Path) -> None:
        (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
        assert collect_pdf_files(tmp_path, encrypted=False) == [sample_pdf]
        assert collect_pdf_files(tmp_path, encrypted=True) == []


# ============================================================================ #
# 批量处理
# ============================================================================ #
class TestBatch:
    """并发批量处理。"""

    def test_result_flags(self, tmp_path: Path) -> None:
        source = tmp_path / "a.pdf"
        assert CryptResult(source, tmp_path / "a.enc.pdf").ok is True
        assert CryptResult(source, None, "boom").ok is False

    def test_empty_input(self) -> None:
        report = process_files([], PASSWORD)
        assert report == BatchReport(())
        assert report.succeeded == ()
        assert report.failed == ()

    def test_empty_password(self) -> None:
        with pytest.raises(ValueError, match="密码不能为空"):
            process_files([], "")

    def test_encrypt_batch_sorted(self, sample_pdf: Path, tmp_path: Path) -> None:
        first = tmp_path / "a.pdf"
        second = tmp_path / "b.pdf"
        first.write_bytes(sample_pdf.read_bytes())
        second.write_bytes(sample_pdf.read_bytes())
        seen: list[tuple[int, int, str, bool]] = []

        def progress(done: int, total: int, source: Path, result: CryptResult) -> None:
            seen.append((done, total, source.name, result.ok))

        report = process_files([second, first], PASSWORD, workers=2, progress=progress)
        assert [item.source.name for item in report.results] == ["b.pdf", "a.pdf"]
        assert len(report.succeeded) == 2
        assert report.failed == ()
        assert len(seen) == 2
        assert {entry[2] for entry in seen} == {"a.pdf", "b.pdf"}

    def test_decrypt_batch_with_failure(self, encrypted_pdf: Path, tmp_path: Path) -> None:
        corrupt = tmp_path / "broken.pdf"
        corrupt.write_bytes(b"not a pdf")
        report = process_files([encrypted_pdf, corrupt], PASSWORD, decrypt=True, workers=1)
        assert [item.source.name for item in report.results] == ["locked.pdf", "broken.pdf"]
        assert len(report.succeeded) == 1
        assert len(report.failed) == 1
        assert report.failed[0].error


# ============================================================================ #
# CLI
# ============================================================================ #
class TestCli:
    """CLI 子命令。"""

    def test_encrypt_and_decrypt(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfcrypt", ["encrypt", PASSWORD, "--directory", str(tmp_path)])
        assert code == 0
        encrypted = sample_pdf.with_suffix(".enc.pdf")
        assert encrypted.exists()
        assert "加密汇总" in capsys.readouterr().out

        code = run_tool("pdfcrypt", ["decrypt", PASSWORD, "--directory", str(tmp_path)])
        assert code == 0
        decrypted = encrypted.with_suffix(".dec.pdf")
        assert decrypted.exists()
        assert "解密汇总" in capsys.readouterr().out

    def test_no_matching_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfcrypt", ["encrypt", PASSWORD, "--directory", str(tmp_path)])
        assert code == 0
        assert "未找到" in capsys.readouterr().out

    def test_short_password_warning(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfcrypt", ["encrypt", "abc", "--directory", str(tmp_path)])
        assert code == 0
        assert "建议使用更强的密码" in capsys.readouterr().out

    def test_empty_password(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfcrypt", ["encrypt", "", "--directory", str(tmp_path)])
        assert code == 0
        assert "密码不能为空" in capsys.readouterr().out

    def test_missing_directory(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfcrypt", ["encrypt", PASSWORD, "--directory", str(tmp_path / "nope")])
        assert code == 0
        assert "目录不存在" in capsys.readouterr().out

    def test_decrypt_wrong_password(
        self, encrypted_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = run_tool("pdfcrypt", ["decrypt", "wrong-password", "--directory", str(tmp_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "解密失败" in out
        assert "失败 1 个" in out

    def test_missing_dependency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(pdfcrypt, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfcrypt, "pypdf", None)
        code = run_tool("pdfcrypt", ["encrypt", PASSWORD, "--directory", str(tmp_path)])
        assert code == 0
        assert "未安装 pypdf" in capsys.readouterr().out
