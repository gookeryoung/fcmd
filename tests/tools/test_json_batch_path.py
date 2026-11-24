"""`--json` 接入批次 1：which / pathtool / tomltool / inifile。

仅验证机读信封（schema、字段、退出码），人类可读输出由各自的工具测试覆盖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import run_tool


def _payload(raw: str) -> dict[str, Any]:
    return json.loads(raw[raw.index("{") :])


@pytest.fixture()
def ini_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.ini"
    path.write_text("[database]\nhost = localhost\nport = 5432\n", encoding="utf-8")
    return path


@pytest.fixture()
def toml_file(tmp_path: Path) -> Path:
    path = tmp_path / "cfg.toml"
    path.write_text('[project]\nname = "demo"\nversion = "1.0"\n', encoding="utf-8")
    return path


def test_which_json(capsys: pytest.CaptureFixture[str]) -> None:
    """which --json 给出 commands 与 missing。"""
    code = run_tool("which", [sys.executable, "fcmd-no-such-cmd-xyz", "--json"])
    payload = _payload(capsys.readouterr().out)
    assert code == 0
    assert payload["schema"] == "fcmd-result/1"
    names = [item["name"] for item in payload["data"]["commands"]]
    assert sys.executable in names
    assert payload["data"]["missing"] == ["fcmd-no-such-cmd-xyz"]


def test_pathtool_show_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """show --json 输出 path_parts 的完整字典。"""
    target = tmp_path / "a" / "b.txt"
    code = run_tool("pathtool", ["show", str(target), "--json"])
    payload = _payload(capsys.readouterr().out)
    assert code == 0
    assert payload["subcommand"] == "show"
    assert payload["data"]["name"] == "b.txt"
    assert payload["data"]["stem"] == "b"


def test_pathtool_norm_rel_diff_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """norm / rel / diff 的 --json 字段。"""
    base = tmp_path / "base"
    target = base / "x" / "file.txt"
    assert run_tool("pathtool", ["norm", str(target), "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"]["result"].endswith("file.txt")

    assert run_tool("pathtool", ["rel", str(target), str(base), "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"]["result"] == str(Path("x") / "file.txt")

    assert run_tool("pathtool", ["diff", str(base / "a"), str(base / "b"), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["only1"] == ["a"]
    assert data["only2"] == ["b"]


def test_pathtool_rel_error_json(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """rel 无法计算时写 stderr 错误信封，退出 3。"""
    code = run_tool("pathtool", ["rel", str(tmp_path / "x"), str(tmp_path / "y"), "--json"])
    payload = _payload(capsys.readouterr().err)
    assert code == 3
    assert payload["schema"] == "fcmd-error/1"
    assert payload["code"] == "INVALID_ARGUMENT"


def test_tomltool_get_keys_json(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """get 返回 key/value，keys 返回键列表。"""
    assert run_tool("tomltool", ["get", str(toml_file), "project.name", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data == {"key": "project.name", "value": "demo"}

    assert run_tool("tomltool", ["keys", str(toml_file), "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"] == {"keys": ["project"]}


def test_tomltool_format_validate_json(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """format 返回解析后的映射，validate 返回 valid。"""
    assert run_tool("tomltool", ["format", str(toml_file), "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"]["toml"]["project"]["name"] == "demo"

    assert run_tool("tomltool", ["validate", str(toml_file), "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"]["valid"] is True


def test_tomltool_get_missing_key_json(toml_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """键不存在时错误信封 code=NOT_FOUND。"""
    code = run_tool("tomltool", ["get", str(toml_file), "project.nope", "--json"])
    payload = _payload(capsys.readouterr().err)
    assert code == 4
    assert payload["code"] == "NOT_FOUND"


def test_inifile_json(ini_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """inifile 四个子命令的 --json 字段。"""
    assert run_tool("inifile", ["get", str(ini_file), "database", "host", "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"]["value"] == "localhost"

    assert run_tool("inifile", ["list", str(ini_file), "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"] == {"sections": ["database"]}

    assert run_tool("inifile", ["keys", str(ini_file), "database", "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"] == {"section": "database", "keys": ["host", "port"]}

    assert run_tool("inifile", ["set", str(ini_file), "database", "port", "3306", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["value"] == "3306"
    assert data["file"] == str(ini_file)


def test_inifile_errors_json(ini_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """文件缺失 4、section 缺失 4、解析失败 3 都走错误信封。"""
    assert run_tool("inifile", ["get", str(tmp_path / "no.ini"), "a", "b", "--json"]) == 4
    assert _payload(capsys.readouterr().err)["code"] == "FILE_NOT_FOUND"

    assert run_tool("inifile", ["keys", str(ini_file), "nope", "--json"]) == 4
    assert _payload(capsys.readouterr().err)["code"] == "NOT_FOUND"

    broken = tmp_path / "broken.ini"
    broken.write_text("no section\nkey = value\n", encoding="utf-8")
    assert run_tool("inifile", ["list", str(broken), "--json"]) == 3
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"
