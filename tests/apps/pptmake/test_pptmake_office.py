"""pptmake 精确预览测试：LibreOffice 探测与 PDF 转换的错误路径。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fcmd.apps.pptmake import _office


def _touch(path: Path) -> Path:
    path.write_text("", encoding="utf-8")
    return path


def test_find_soffice_env_override(tmp_path: Path) -> None:
    fake = _touch(tmp_path / "soffice.exe")
    assert _office.find_soffice({"PPTMAKE_SOFFICE": str(fake)}) == fake


def test_find_soffice_env_override_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_office.shutil, "which", lambda _name: "/usr/bin/soffice")
    assert _office.find_soffice({"PPTMAKE_SOFFICE": str(tmp_path / "missing.exe")}) is None


def test_find_soffice_uses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_office.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "soffice" else None)
    assert _office.find_soffice({}) == Path("/usr/bin/soffice")


def test_find_soffice_uses_common_windows_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _touch(tmp_path / "soffice.exe")
    monkeypatch.setattr(_office.shutil, "which", lambda _name: None)
    monkeypatch.setattr(_office, "_WINDOWS_PATHS", (str(fake),))
    assert _office.find_soffice({}) == fake


def test_find_soffice_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_office.shutil, "which", lambda _name: None)
    monkeypatch.setattr(_office, "_WINDOWS_PATHS", ())
    assert _office.find_soffice({}) is None


def test_convert_without_soffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_office, "find_soffice", lambda: None)
    with pytest.raises(_office.OfficeError, match="未检测到 LibreOffice"):
        _office.convert_to_pdf(tmp_path / "deck.pptx", tmp_path)


def test_convert_returns_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _touch(tmp_path / "deck.pptx")
    commands: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        out_dir = Path(command[command.index("--outdir") + 1])
        (out_dir / f"{source.stem}.pdf").write_bytes(b"%PDF-1.4")
        return subprocess.CompletedProcess(command, 0, stdout="converted", stderr="")

    monkeypatch.setattr(_office, "find_soffice", lambda: Path("soffice"))
    monkeypatch.setattr(_office.subprocess, "run", fake_run)
    pdf = _office.convert_to_pdf(source, tmp_path / "out")
    assert pdf == tmp_path / "out" / "deck.pdf"
    assert pdf.is_file()
    assert commands[0][:2] == ("soffice", "--headless")
    assert "--convert-to" in commands[0]


def test_convert_reports_missing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _touch(tmp_path / "deck.pptx")

    def fake_run(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="source file could not be loaded")

    monkeypatch.setattr(_office, "find_soffice", lambda: Path("soffice"))
    monkeypatch.setattr(_office.subprocess, "run", fake_run)
    with pytest.raises(_office.OfficeError, match=r"未生成 PDF.*could not be loaded"):
        _office.convert_to_pdf(source, tmp_path / "out")


def test_convert_wraps_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _touch(tmp_path / "deck.pptx")

    def fake_run(_command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("soffice")

    monkeypatch.setattr(_office, "find_soffice", lambda: Path("soffice"))
    monkeypatch.setattr(_office.subprocess, "run", fake_run)
    with pytest.raises(_office.OfficeError, match="调用 LibreOffice 失败"):
        _office.convert_to_pdf(source, tmp_path / "out")
