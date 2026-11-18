"""P1a 工具的 ``--json`` 结果信封测试。

覆盖 mathtool / convtool / hashtool / codetool / urltool / calcdate：成功路径
要求 stdout 恰为一条 ``fcmd-result/1`` 信封、stderr 为空、退出码 0；输入非法
要求 stdout 为空、stderr 为 ``fcmd-error/1`` 信封、退出码 3。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import run_tool


def _run_ok(tool: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """运行 ``--json`` 成功路径并返回解析后的 stdout 信封。"""
    code = run_tool(tool, [*argv, "--json"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["schema"] == "fcmd-result/1"
    assert payload["ok"] is True
    assert payload["tool"] == tool
    return payload


def _run_err(
    tool: str,
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
    *,
    exit_code: int = 3,
) -> dict[str, Any]:
    """运行 ``--json`` 失败路径并返回解析后的 stderr 信封。"""
    code = run_tool(tool, [*argv, "--json"])
    captured = capsys.readouterr()
    assert code == exit_code
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["schema"] == "fcmd-error/1"
    assert payload["ok"] is False
    assert payload["exit_code"] == exit_code
    return payload


def test_mathtool_eval_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("mathtool", ["eval", "1 + 2 * 3"], capsys)
    assert payload["subcommand"] == "eval"
    assert payload["data"] == {"result": 7}


def test_mathtool_eval_json_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("mathtool", ["eval", "1 +"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"
    assert payload["subcommand"] == "eval"


def test_convtool_length_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("convtool", ["length", "1", "m", "ft"], capsys)
    assert payload["data"]["result"] == pytest.approx(3.280839895013123)


def test_convtool_length_json_invalid_unit(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("convtool", ["length", "1", "zzz", "m"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"


def test_hashtool_sha256_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("hashtool", ["sha256", "hello"], capsys)
    assert payload["data"] == {
        "algorithm": "sha256",
        "digest": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    }


def test_codetool_base64_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("codetool", ["base64", "hello"], capsys)
    assert payload["data"] == {"result": "aGVsbG8="}


def test_codetool_base64_json_decode_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("codetool", ["base64", "!!!", "--decode"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"
    assert "Base64 解码失败" in str(payload["message"])


def test_codetool_hex_json_decode_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("codetool", ["hex", "abc", "--decode"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"
    assert "Hex 解码失败" in str(payload["message"])


def test_urltool_parse_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("urltool", ["parse", "https://example.com/path?key=value#frag"], capsys)
    assert payload["data"] == {
        "scheme": "https",
        "netloc": "example.com",
        "path": "/path",
        "query": "key=value",
        "fragment": "frag",
    }


def test_urltool_query_missing_keeps_ok(capsys: pytest.CaptureFixture[str]) -> None:
    """参数不存在不是错误：仍为成功信封，``result`` 为 null。"""
    payload = _run_ok("urltool", ["query", "https://example.com?a=1", "z"], capsys)
    assert payload["data"] == {"result": None}


def test_urltool_baseurl_json_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("urltool", ["baseurl", "example.com/path"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"


def test_calcdate_add_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("calcdate", ["add", "2026-01-15", "30"], capsys)
    assert payload["data"] == {"result": "2026-02-14"}


def test_calcdate_compare_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("calcdate", ["compare", "2026-01-01", "2026-02-01"], capsys)
    assert payload["data"] == {"result": "before"}


def test_calcdate_add_json_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("calcdate", ["add", "invalid", "5"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------- #
# P1b：统计与文本工具
# ---------------------------------------------------------------------- #
def test_stattool_mean_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data.txt"
    data.write_text("1\n2\n3\n4\n5\n", encoding="utf-8")
    payload = _run_ok("stattool", ["mean", str(data)], capsys)
    assert payload["data"] == {"result": 3.0}


def test_stattool_summarize_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data.txt"
    data.write_text("1\n2\n3\n", encoding="utf-8")
    payload = _run_ok("stattool", ["summarize", str(data)], capsys)
    assert payload["data"]["count"] == 3
    assert payload["data"]["mean"] == 2.0


def test_stattool_missing_file_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("stattool", ["mean", str(tmp_path / "no.txt")], capsys, exit_code=4)
    assert payload["code"] == "FILE_NOT_FOUND"


def test_timetool_parse_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("timetool", ["parse", "2026-07-29 12:30:00"], capsys)
    assert payload["data"] == {"result": "2026-07-29 12:30:00"}


def test_timetool_parse_json_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("timetool", ["parse", "not a time"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"
    assert payload["field"] == "time"


def test_colortool_hex2rgb_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("colortool", ["hex2rgb", "#ff5733"], capsys)
    assert payload["data"] == {"result": [255, 87, 51]}


def test_colortool_rgb2hex_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("colortool", ["rgb2hex", "255", "87", "51"], capsys)
    assert payload["data"] == {"result": "#ff5733"}


def test_asciitool_char_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("asciitool", ["char", "A"], capsys)
    assert payload["data"] == {"char": "A", "code": 65, "hex": "0x41"}


def test_asciitool_table_json_range(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("asciitool", ["table", "--start", "48", "--end", "49"], capsys)
    assert [entry["code"] for entry in payload["data"]["entries"]] == [48, 49]


def test_casetool_snake_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("casetool", ["snake", "Hello World"], capsys)
    assert payload["data"] == {"result": "hello_world"}


def test_txttool_count_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "a.txt"
    f.write_text("one two\nthree\n", encoding="utf-8")
    payload = _run_ok("txttool", ["count", str(f)], capsys)
    assert payload["data"] == {"lines": 2, "words": 3, "chars": 14}


def test_txttool_missing_file_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("txttool", ["count", str(tmp_path / "no.txt")], capsys, exit_code=4)
    assert payload["code"] == "FILE_NOT_FOUND"


def test_regextool_match_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("regextool", ["match", r"\d+", "123abc"], capsys)
    assert payload["data"]["result"]["match"] == "123"


def test_regextool_find_json_no_match(capsys: pytest.CaptureFixture[str]) -> None:
    """无匹配不是错误：``result`` 为空列表。"""
    payload = _run_ok("regextool", ["find", r"\d+", "abc"], capsys)
    assert payload["data"] == {"result": []}


def test_regextool_invalid_pattern_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("regextool", ["match", r"[unclosed", "abc"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"
    assert payload["field"] == "pattern"


def test_regexvalidate_json(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_ok("regexvalidate", [r"\d+", "abc123"], capsys)
    data = payload["data"]
    assert data["pattern"] == r"\d+"
    assert data["is_valid"] is True
    assert data["count"] == 1
    assert data["matches"][0]["value"] == "123"
    assert data["fullmatch"] is False


def test_regexvalidate_json_invalid_pattern(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _run_err("regexvalidate", ["[bad", "x"], capsys)
    assert payload["code"] == "INVALID_ARGUMENT"
    assert payload["field"] == "pattern"
