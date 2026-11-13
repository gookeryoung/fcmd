"""filescan CLI 测试：注册、扫描输出、规则集管理与错误退出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.filescan import filescan as filescan_module
from fcmd.apps.filescan.filescan import rules_command, run_command, scan_command
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool


class _BrokenCache:
    """打不开的缓存替身：``enabled`` 为假，提示后可降级继续扫描。"""

    error: str | None = "缓存不可用（模拟）"
    enabled = False

    def clear(self) -> int:
        return 0

    def close(self) -> None:
        pass


class _DisabledCache:
    """可用但已降级的缓存替身：``enabled`` 为真，运行结束提示错误。"""

    error: str | None = "缓存已禁用（模拟）"
    enabled = True

    def lookup_many(self, _cache_key: str, _items: object) -> dict[Path, object]:
        return {}

    def store_many(self, _cache_key: str, _items: object) -> None:
        pass

    def clear(self) -> int:
        return 0

    def close(self) -> None:
        pass


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


def test_filescan_registers_three_subcommands() -> None:
    assert set(_TOOL_REGISTRY["filescan"]) == {"scan", "run", "rules"}


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


def test_scan_second_run_reports_cache_reuse(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """第二次扫描全部命中缓存，文本与 JSON 都能看出复用数。"""
    scan_command(paths=[sample_dir])
    capsys.readouterr()
    scan_command(paths=[sample_dir], format="json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["stats"]["files_seen"] == 3
    assert payload["stats"]["files_reused"] == 3
    assert payload["stats"]["files_scanned"] == 0
    assert payload["stats"]["hit_files"] == 2

    scan_command(paths=[sample_dir])
    assert "缓存复用 3" in capsys.readouterr().out


def test_scan_no_cache_forces_rescan(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir])
    capsys.readouterr()
    scan_command(paths=[sample_dir], cache=False)
    out = capsys.readouterr().out
    assert "实扫 3" in out
    assert "缓存复用 0" in out


def test_scan_clear_cache_reports_count(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan_command(paths=[sample_dir])
    capsys.readouterr()
    scan_command(paths=[sample_dir], clear_cache=True)
    out = capsys.readouterr().out
    assert "扫描缓存已清理（3 条）" in out
    assert "缓存复用 0" in out
    assert "实扫 3" in out


def test_scan_whitelist_skips_unknown_suffix(sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(sample_dir, "dump.dat", "手机 13812345678\n")
    scan_command(paths=[sample_dir])
    out = capsys.readouterr().out
    assert "dump.dat" not in out
    assert "范围外 1（后缀白名单 1" in out


def test_scan_only_out_of_scope_files_explains_zero_hits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """只有白名单外文件时，0 命中要说明「范围外」并给出可操作提示，而不是只报跳过。"""
    _write(tmp_path, "dump.dat", "手机 13812345678\n")
    scan_command(paths=[tmp_path])
    out = capsys.readouterr().out
    assert "命中 0 个文件 / 0 条" in out
    assert "范围外 1（后缀白名单 1" in out
    assert "未发现命中" in out
    assert "不在扫描范围" in out
    assert "检查后缀白名单与规则启用状态" in out


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


class TestScanFailurePaths:
    """scan 子命令的校验与运行期失败路径。"""

    def test_invalid_time_filter(self, sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            scan_command(paths=[sample_dir], modified_after="not-a-date")
        assert excinfo.value.code == 2
        assert "时间过滤无效" in capsys.readouterr().out

    def test_time_filter_order(self, sample_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            scan_command(paths=[sample_dir], modified_after="2026-02-01", modified_before="2026-01-01")
        assert excinfo.value.code == 2
        assert "晚于" in capsys.readouterr().out

    def test_save_config_failure(
        self,
        sample_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _boom(_config: object, _path: Path) -> None:
            raise OSError("磁盘已满")

        monkeypatch.setattr(filescan_module, "save_config_file", _boom)
        with pytest.raises(SystemExit) as excinfo:
            scan_command(paths=[sample_dir], save_config=tmp_path / "team.json")
        assert excinfo.value.code == 2
        assert "无法写入配置" in capsys.readouterr().out

    def test_write_report_failure(
        self,
        sample_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("只读文件系统")

        monkeypatch.setattr(filescan_module, "write_report", _boom)
        with pytest.raises(SystemExit) as excinfo:
            scan_command(paths=[sample_dir], output=tmp_path / "report.txt")
        assert excinfo.value.code == 2
        assert "无法写入报告" in capsys.readouterr().out

    def test_keyboard_interrupt_exits_130(
        self, sample_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(filescan_module, "scan_paths", _interrupt)
        with pytest.raises(SystemExit) as excinfo:
            scan_command(paths=[sample_dir])
        assert excinfo.value.code == 130
        assert "扫描已取消" in capsys.readouterr().out

    def test_degraded_cache_warns_but_still_scans(
        self, sample_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(filescan_module, "ScanCache", _BrokenCache)
        scan_command(paths=[sample_dir], clear_cache=True)
        captured = capsys.readouterr()
        assert "缓存不可用" in captured.err
        assert "扫描缓存已清理（0 条）" in captured.out
        assert "customer.txt" in captured.out

    def test_cache_disabled_after_open(
        self, sample_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(filescan_module, "ScanCache", _DisabledCache)
        scan_command(paths=[sample_dir])
        captured = capsys.readouterr()
        assert "缓存已禁用" in captured.err
        assert "customer.txt" in captured.out


class TestRunFailurePaths:
    """run 子命令的配置校验与中断路径。"""

    def test_requires_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            run_command(config=None)
        assert excinfo.value.code == 2
        assert "--config" in capsys.readouterr().out

    def test_missing_config_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            run_command(config=tmp_path / "missing.json")
        assert excinfo.value.code == 2
        assert "无法读取配置" in capsys.readouterr().out

    def test_empty_roots(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = tmp_path / "empty.json"
        config.write_text(json.dumps({"roots": []}), encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            run_command(config=config)
        assert excinfo.value.code == 2
        assert "未包含扫描目录" in capsys.readouterr().out

    def test_saved_config_with_filters(
        self, sample_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = tmp_path / "team.json"
        scan_command(paths=[sample_dir], save_config=config)
        capsys.readouterr()

        run_command(config=config, rule_id=["email"], path_filter="customer")
        out = capsys.readouterr().out
        assert "邮箱地址" in out
        assert "手机号" not in out
        assert "note.md" not in out

    def test_saved_config_warnings_and_no_filters(
        self, sample_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """配置里的非法字段逐条提示，未指定过滤时按配置原样输出。"""
        config = tmp_path / "warn.json"
        config.write_text(json.dumps({"roots": [str(sample_dir)], "options": {"workers": "many"}}), encoding="utf-8")

        run_command(config=config)
        captured = capsys.readouterr()
        assert "警告：options.workers" in captured.err
        assert "扫描完成" in captured.out
        assert "customer.txt" in captured.out

    def test_keyboard_interrupt_exits_130(
        self,
        sample_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = tmp_path / "team.json"
        scan_command(paths=[sample_dir], save_config=config)
        capsys.readouterr()

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(filescan_module, "scan_paths", _interrupt)
        with pytest.raises(SystemExit) as excinfo:
            run_command(config=config)
        assert excinfo.value.code == 130
        assert "扫描已取消" in capsys.readouterr().out


class TestRulesFailurePaths:
    """rules 子命令的导出失败路径。"""

    def test_export_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _boom(_ruleset: object, _path: Path) -> None:
            raise OSError("没有写权限")

        monkeypatch.setattr(filescan_module, "save_ruleset", _boom)
        with pytest.raises(SystemExit) as excinfo:
            rules_command(action="export", output=tmp_path / "rules.json")
        assert excinfo.value.code == 2
        assert "无法写入规则集" in capsys.readouterr().out
