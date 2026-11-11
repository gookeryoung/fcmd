"""filescan CLI 测试：注册、扫描输出、规则集管理与错误退出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.filescan.filescan import rules_command, scan_command
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    _write(tmp_path, "customer.txt", "姓名 张三\n手机 13812345678\n邮箱 zhang@example.com\n")
    _write(tmp_path, "clean.txt", "没有任何敏感内容\n")
    _write(tmp_path, "note.md", "身份证 11010519491231002X\n")
    return tmp_path


def test_filescan_registers_two_subcommands() -> None:
    assert set(_TOOL_REGISTRY["filescan"]) == {"scan", "rules"}


def test_scan_text_output_is_masked(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir])
    out = capsys.readouterr().out
    assert "扫描完成" in out
    assert "customer.txt" in out
    assert "手机号" in out
    assert "13*******78" in out
    assert "13812345678" not in out


def test_scan_raw_output(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir], raw=True)
    assert "13812345678" in capsys.readouterr().out


def test_scan_json_format(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir], format="json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["stats"]["hit_files"] == 2
    assert payload["hits"][0]["matches"][0]["excerpt"] == "13*******78"


def test_scan_csv_to_file(sample_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out" / "report.csv"
    scan_command(paths=[sample_dir], format="csv", output=target)
    assert "报告已写入" in capsys.readouterr().out
    content = target.read_text(encoding="utf-8")
    assert content.startswith("\ufeff")
    assert "手机号" in content


def test_scan_stdout_csv_has_no_bom(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir], format="csv")
    assert not capsys.readouterr().out.startswith("\ufeff")


def test_scan_rule_and_path_filters(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir], rule_id=["email"], path_filter="customer")
    out = capsys.readouterr().out
    assert "邮箱地址" in out
    assert "手机号" not in out
    assert "note.md" not in out


def test_scan_custom_rule_file_and_expression(
    sample_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps(
            {
                "name": "自定义",
                "rules": [
                    {"id": "phone", "name": "手机", "matcher": {"type": "keyword", "text": "手机"}},
                    {"id": "clean", "name": "干净", "matcher": {"type": "keyword", "text": "敏感"}},
                ],
                "expression": "phone and not clean",
            }
        ),
        encoding="utf-8",
    )
    scan_command(paths=[sample_dir], rules=rules)
    out = capsys.readouterr().out
    assert "customer.txt" in out
    assert "note.md" not in out


def test_scan_include_exclude_and_non_recursive(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir], include_ext=".txt;.md", exclude_ext=".md")
    out = capsys.readouterr().out
    assert "customer.txt" in out
    assert "note.md" not in out


def test_scan_missing_root_reports_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[tmp_path / "missing"])
    assert "目录不存在" in capsys.readouterr().out


def test_scan_whitelist_skips_unknown_suffix(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(sample_dir, "dump.dat", "手机 13812345678\n")
    scan_command(paths=[sample_dir])
    out = capsys.readouterr().out
    assert "dump.dat" not in out
    assert "后缀 1" in out


def test_scan_force_ext_scans_outside_whitelist(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(sample_dir, "dump.dat", "手机 13812345678\n")
    scan_command(paths=[sample_dir], force_ext=".dat")
    assert "dump.dat" in capsys.readouterr().out


def test_scan_custom_whitelist(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir], ext_whitelist=".md")
    out = capsys.readouterr().out
    assert "note.md" in out
    assert "customer.txt" not in out


def test_scan_whitelist_star_scans_all_suffixes(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(sample_dir, "dump.dat", "手机 13812345678\n")
    scan_command(paths=[sample_dir], ext_whitelist="*")
    assert "dump.dat" in capsys.readouterr().out


def test_scan_requires_path() -> None:
    with pytest.raises(SystemExit) as exc:
        scan_command(paths=[])
    assert exc.value.code == 2


def test_scan_invalid_expression_exits(sample_dir: Path) -> None:
    with pytest.raises(SystemExit):
        scan_command(paths=[sample_dir], expr="(phone")
    with pytest.raises(SystemExit):
        scan_command(paths=[sample_dir], expr="unknown_rule")


def test_scan_invalid_rules_file(tmp_path: Path, sample_dir: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit):
        scan_command(paths=[sample_dir], rules=broken)


def test_run_tool_scan_end_to_end(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_tool("filescan", ["scan", str(sample_dir), "--format", "json"])
    assert code == 0
    out = capsys.readouterr().out
    assert '"hit_files": 2' in out
    assert '"hits": [' in out


def test_rules_list_shows_builtin_rules(capsys: pytest.CaptureFixture[str]) -> None:
    rules_command(action="list")
    out = capsys.readouterr().out
    assert "规则集：内置敏感规则" in out
    assert "cn_id" in out
    assert "[个人信息]" in out
    assert "表达式：" in out


def test_rules_export_and_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "exported.json"
    rules_command(action="export", output=target)
    assert target.is_file()
    assert "已导出" in capsys.readouterr().out
    rules_command(action="validate", rules=target)
    assert "规则集有效" in capsys.readouterr().out


def test_rules_list_empty_ruleset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"name": "空", "rules": []}), encoding="utf-8")
    rules_command(action="list", rules=empty)
    out = capsys.readouterr().out
    assert "（规则集为空）" in out or "（无启用规则）" in out


def test_rules_validate_invalid_expression(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps(
            {"rules": [{"id": "a", "name": "A", "matcher": {"type": "keyword", "text": "x"}}], "expression": "b"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        rules_command(action="validate", rules=invalid)
