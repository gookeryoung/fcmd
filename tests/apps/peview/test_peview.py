"""peview CLI 测试：各子命令输出、错误降级与注册表。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from fcmd.apps.peview import peview
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands

_SUBCOMMANDS = {
    "check",
    "debug",
    "directories",
    "edit",
    "exports",
    "fields",
    "headers",
    "hex",
    "imports",
    "info",
    "relocations",
    "resources",
    "sections",
}


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (peview.info, "可执行文件"),
        (peview.headers, "e_lfanew"),
        (peview.sections, ".text"),
        (peview.directories, "导出表"),
        (peview.imports, "ExitProcess"),
        (peview.exports, "peview_test.dll"),
        (peview.resources, "#101"),
        (peview.relocations, "HIGHLOW"),
        (peview.debug, "CodeView"),
        (peview.check, "校验和"),
        (peview.fields, "entry_point"),
    ],
)
def test_command_output(
    command: Callable[[Path], None],
    expected: str,
    pe32_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """每个只读子命令都能输出关键内容。"""
    command(pe32_file)
    captured = capsys.readouterr()
    assert expected in captured.out


def test_hexdump(pe32_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    peview.hexdump(pe32_file, offset=0, length=4)
    out = capsys.readouterr().out
    assert "4D 5A 90 00" in out
    assert "|MZ..|" in out


def test_hexdump_offset_out_of_range(pe32_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    peview.hexdump(pe32_file, offset=0x2000, length=8)
    captured = capsys.readouterr()
    assert "超出文件大小" in captured.out + captured.err


def test_hexdump_negative_length(pe32_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    peview.hexdump(pe32_file, offset=0, length=-1)
    captured = capsys.readouterr()
    assert "长度不能为负" in captured.out + captured.err


def test_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    peview.info(tmp_path / "missing.exe")
    captured = capsys.readouterr()
    assert "文件不存在" in captured.out + captured.err


def test_non_pe_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "plain.bin"
    path.write_bytes(b"not a pe")
    peview.check(path)
    captured = capsys.readouterr()
    assert "DOS 头" in captured.out + captured.err


def test_registry() -> None:
    assert set(_TOOL_REGISTRY["peview"]) == _SUBCOMMANDS
    assert set(list_subcommands("peview")) == _SUBCOMMANDS


def test_edit_dry_run(pe32_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "app.exe"
    target.write_bytes(pe32_file.read_bytes())
    peview.edit(target, assign=["subsystem=2"], apply=False, allow_signature_break=True)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "预演完成" in output
    assert "0x3" in output and "0x2" in output
    assert target.read_bytes() == pe32_file.read_bytes()


def test_edit_apply(pe32_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "app.exe"
    target.write_bytes(pe32_file.read_bytes())
    peview.edit(target, assign=["subsystem=2"], apply=True, allow_signature_break=True)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "已写入" in output
    assert "备份" in output
    assert "签名" in output
    assert (tmp_path / "app.exe.backup1").exists()


def test_edit_signature_guard(pe32_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "app.exe"
    target.write_bytes(pe32_file.read_bytes())
    peview.edit(target, assign=["subsystem=2"], apply=True)
    captured = capsys.readouterr()
    assert "Authenticode" in captured.out + captured.err
    assert target.read_bytes() == pe32_file.read_bytes()


@pytest.mark.parametrize(
    ("assign", "message"),
    [
        (["subsystem"], "NAME=VALUE"),
        (["subsystem=abc"], "无法解析"),
        (["size_of_image=0x4000"], "改变文件布局"),
        (["missing=1"], "未知字段"),
    ],
)
def test_edit_rejections(
    pe32_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    assign: list[str],
    message: str,
) -> None:
    target = tmp_path / "app.exe"
    target.write_bytes(pe32_file.read_bytes())
    peview.edit(target, assign=assign, apply=True, allow_signature_break=True)
    captured = capsys.readouterr()
    assert message in captured.out + captured.err
    assert target.read_bytes() == pe32_file.read_bytes()


def test_edit_reports_problems(pe32_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "app.exe"
    target.write_bytes(pe32_file.read_bytes())
    peview.edit(
        target,
        assign=["subsystem=2"],
        apply=False,
        allow_signature_break=True,
        recompute_checksum=False,
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "结构告警" in output
    assert "校验和不一致" in output


def test_version() -> None:
    assert peview.__version__ == "0.1.0"
