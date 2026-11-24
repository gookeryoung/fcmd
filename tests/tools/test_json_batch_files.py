"""`--json` 接入批次 2：archivex list / csvtool select+merge / filecheck。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import run_tool


def _payload(raw: str) -> dict[str, Any]:
    return json.loads(raw[raw.index("{") :])


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "a.csv"
    path.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
    return path


def test_archivex_list_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """list --json 给出 archive/count/entries。"""
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("one.txt", "1")
        zf.writestr("dir/two.txt", "2")
    assert run_tool("archivex", ["list", str(archive), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["archive"] == str(archive)
    assert data["count"] == 2
    assert set(data["entries"]) == {"one.txt", "dir/two.txt"}


def test_archivex_list_missing_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """归档不存在时错误信封 code=FILE_NOT_FOUND。"""
    code = run_tool("archivex", ["list", str(tmp_path / "no.zip"), "--json"])
    assert code == 4
    assert _payload(capsys.readouterr().err)["code"] == "FILE_NOT_FOUND"


def test_archivex_list_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """损坏的归档 → 错误信封（读取失败按 RUNTIME_ERROR）。"""
    bogus = tmp_path / "bogus.zip"
    bogus.write_text("not a zip", encoding="utf-8")
    code = run_tool("archivex", ["list", str(bogus), "--json"])
    assert code == 1
    assert _payload(capsys.readouterr().err)["code"] == "RUNTIME_ERROR"


def test_csvtool_select_json(csv_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """select --json 返回筛选后的表头与行。"""
    assert run_tool("csvtool", ["select", str(csv_file), "name", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["header"] == ["name"]
    assert data["rows"] == [["Alice"], ["Bob"]]
    assert data["count"] == 2


def test_csvtool_select_output_json(csv_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """select --output 时信封带 output 字段。"""
    target = tmp_path / "out.csv"
    assert run_tool("csvtool", ["select", str(csv_file), "age", "--output", str(target), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["output"] == str(target)
    assert target.read_text(encoding="utf-8").splitlines()[0] == "age"


def test_csvtool_select_empty_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """空 CSV 的 --json 给出空 header/rows。"""
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert run_tool("csvtool", ["select", str(empty), "name", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["header"] == []
    assert data["rows"] == []


def test_csvtool_select_error_json(csv_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """列不存在 → INVALID_ARGUMENT 错误信封。"""
    code = run_tool("csvtool", ["select", str(csv_file), "nope", "--json"])
    assert code == 3
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"


def test_csvtool_merge_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """merge --json 返回合并后的完整数据。"""
    first = tmp_path / "1.csv"
    second = tmp_path / "2.csv"
    first.write_text("a\n1\n", encoding="utf-8")
    second.write_text("a\n2\n", encoding="utf-8")
    assert run_tool("csvtool", ["merge", str(first), str(second), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["header"] == ["a"]
    assert data["rows"] == [["1"], ["2"]]
    assert data["mode"] == "union"


def test_csvtool_merge_missing_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """输入文件缺失 → FILE_NOT_FOUND。"""
    code = run_tool("csvtool", ["merge", str(tmp_path / "no1.csv"), str(tmp_path / "no2.csv"), "--json"])
    assert code == 4
    assert _payload(capsys.readouterr().err)["code"] == "FILE_NOT_FOUND"


def test_filecheck_json_does_not_write_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--json 输出结构化结果且不生成报告文件。"""
    (tmp_path / "ok.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "empty.txt").write_bytes(b"")
    assert run_tool("filecheck", ["--path", str(tmp_path), "--all-files", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["root"] == str(tmp_path.resolve())
    assert data["summary"]["checked"] == 2
    assert data["summary"]["errors"] == 1
    assert any(item["path"].endswith("empty.txt") for item in data["issues"])
    assert not list(tmp_path.glob("文件检查报告_*.txt"))


def test_filecheck_json_missing_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """目录不存在 → FILE_NOT_FOUND 错误信封。"""
    code = run_tool("filecheck", ["--path", str(tmp_path / "nope"), "--json"])
    assert code == 4
    assert _payload(capsys.readouterr().err)["code"] == "FILE_NOT_FOUND"
