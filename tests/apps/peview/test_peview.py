"""peview CLI 测试：各子命令输出、错误降级与注册表。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from fcmd.apps.peview import peview
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands, run_tool
from tests.helpers import run_and_capture as _run_and_capture

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


class TestPeviewJson:
    """``--json`` 增量输出：默认人读文本保持不变，JSON 与文本同源。"""

    def test_info_json(self, pe32_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """info --json 给出概览字段。"""
        code, out = _run_and_capture("peview", ["info", str(pe32_file), "--json"], capsys)
        assert code == 0
        payload = json.loads(out)
        assert payload["schema"] == "fcmd-result/1"
        assert payload["tool"] == "peview"
        assert payload["subcommand"] == "info"
        data = payload["data"]
        assert data["name"] == "sample.exe"
        assert data["size"] == 0xA10
        assert data["machine"] == 0x014C
        assert data["machine_name"] == "x86（32 位）"
        assert data["image_base"] == 0x400000
        assert data["entry_point"] == 0x1000
        assert data["subsystems"] == ["Windows Console"]
        assert data["is_dll"] is False
        assert data["signed"] is True

    def test_sections_json(self, pe32_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """sections --json 给出节明细与总数。"""
        code, out = _run_and_capture("peview", ["sections", str(pe32_file), "--json"], capsys)
        assert code == 0
        data = json.loads(out)["data"]
        assert data["total"] == 2
        first = data["sections"][0]
        assert first == {
            "name": ".text",
            "virtual_address": 0x1000,
            "virtual_size": 0x100,
            "raw_offset": 0x200,
            "raw_size": 0x200,
            "characteristics": 0x60000020,
        }
        assert data["sections"][1]["name"] == ".rdata"

    def test_imports_json(self, pe32_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """imports --json 按库分组并统计符号数。"""
        code, out = _run_and_capture("peview", ["imports", str(pe32_file), "--json"], capsys)
        assert code == 0
        data = json.loads(out)["data"]
        assert data["total_dlls"] == 2
        assert data["total_functions"] == 3
        libraries = {entry["name"]: entry["functions"] for entry in data["dlls"]}
        assert libraries["kernel32.dll"] == ["ExitProcess", "#5"]
        assert libraries["user32.dll"] == ["MessageBoxW"]

    def test_check_json(self, pe32_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """check --json 给出逐项结论与汇总计数。"""
        code, out = _run_and_capture("peview", ["check", str(pe32_file), "--json"], capsys)
        assert code == 0
        data = json.loads(out)["data"]
        assert data["ok"] is True
        assert data["failed"] == 0
        assert data["passed"] == data["total"]
        items = [check["item"] for check in data["checks"]]
        assert items[:3] == ["校验和", "节对齐", "入口点"]
        assert items[-1] == "数字签名"
        assert ".text" in items[3]

    def test_check_json_reports_problem(
        self, pe32_data: bytes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """校验和被破坏时 check --json 标记 ok=false。"""
        data = bytearray(pe32_data)
        data[0x200] ^= 0xFF
        target = tmp_path / "broken.exe"
        target.write_bytes(bytes(data))
        code, out = _run_and_capture("peview", ["check", str(target), "--json"], capsys)
        assert code == 0
        payload = json.loads(out)["data"]
        assert payload["ok"] is False
        assert payload["failed"] >= 1
        checksum = next(check for check in payload["checks"] if check["item"] == "校验和")
        assert checksum["ok"] is False
        assert "校验和不一致" in checksum["detail"]

    def test_missing_file_json_envelope(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 下文件不存在：stdout 为空，stderr 为错误信封。"""
        assert run_tool("peview", ["info", str(tmp_path / "missing.exe"), "--json"]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        payload = json.loads(captured.err)
        assert payload["ok"] is False
        assert payload["code"] == "FILE_NOT_FOUND"
        assert payload["field"] == "file"

    def test_non_pe_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 下非 PE 文件：NOT_PE_FILE。"""
        path = tmp_path / "plain.bin"
        path.write_bytes(b"not a pe")
        assert run_tool("peview", ["sections", str(path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().err)
        assert payload["code"] == "NOT_PE_FILE"
        assert payload["field"] == "file"
        assert "PE" in payload["expected"]

    def test_imports_json_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """imports --json 加载失败时只写错误信封。"""
        path = tmp_path / "plain.bin"
        path.write_bytes(b"not a pe")
        assert run_tool("peview", ["imports", str(path), "--json"]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert json.loads(captured.err)["code"] == "NOT_PE_FILE"

    def test_check_json_missing_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """check --json 文件不存在时只写错误信封。"""
        assert run_tool("peview", ["check", str(tmp_path / "missing.exe"), "--json"]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert json.loads(captured.err)["code"] == "FILE_NOT_FOUND"
