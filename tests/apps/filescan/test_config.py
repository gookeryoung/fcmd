"""filescan 扫描配置导入导出与容错测试。

覆盖：导出字段与版本号、导入后解析等价、旧配置缺字段回落默认、非法值逐字段
提示且不整份作废、``rules_path`` 相对解析、CLI 的 ``--save-config`` /
``run`` 往返，以及 GUI 设置与配置的双向转换。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.filescan._config import (
    CONFIG_VERSION,
    ScanConfig,
    config_from_mapping,
    config_to_mapping,
    dump_config,
    load_config,
    save_config,
)
from fcmd.apps.filescan._engine import (
    DEFAULT_ENCODINGS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_SCAN_EXTENSIONS,
    ScanOptions,
)
from fcmd.apps.filescan._rules import builtin_ruleset, ruleset_to_mapping, save_ruleset
from fcmd.apps.filescan.filescan import run_command, scan_command
from fcmd.apps.filescan.filescanw import FilescanSettings, settings_from_mapping, settings_to_mapping
from fcmd.core.toolkit import _TOOL_REGISTRY


def _write_file(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    _write_file(root, "a.txt", "手机 13812345678\n")
    _write_file(root, "b.log", "身份证 11010519491231002X\n")
    return root


def _config_payload(*, version: int = CONFIG_VERSION) -> dict[str, Any]:
    """一份合法的配置字典（结构与 CLI / GUI 导出一致）。"""
    return {
        "version": version,
        "roots": ["D:/data", "D:/other"],
        "options": {
            "recursive": True,
            "max_depth": 2,
            "follow_symlinks": True,
            "ext_whitelist": [".txt", ".log", ".md"],
            "include_exts": [".txt"],
            "force_exts": [],
            "exclude_exts": [".log"],
            "min_size": 100,
            "max_size": 1048576,
            "modified_after": "2026-01-01T00:00:00",
            "modified_before": "2026-12-31T23:59:59",
            "skip_binary": False,
            "scan_documents": True,
            "max_file_bytes": 4194304,
            "encodings": ["utf-8", "gb18030"],
            "workers": 8,
            "context_chars": 60,
            "max_hits_per_file": 300,
            "use_processes": False,
        },
        "rules": ruleset_to_mapping(builtin_ruleset()),
        "report": {"mask": False, "format": "json"},
    }


# ---------------------------------------------------------------------- #
# 导出
# ---------------------------------------------------------------------- #
def test_dump_config_has_version_and_core_sections(tmp_path: Path) -> None:
    config = ScanConfig(roots=(tmp_path,), options=ScanOptions(), ruleset=builtin_ruleset())
    payload = config_to_mapping(config)
    assert payload["version"] == CONFIG_VERSION
    assert set(payload) >= {"roots", "options", "report", "rules"}
    assert set(payload["options"]) == {
        "recursive",
        "max_depth",
        "follow_symlinks",
        "ext_whitelist",
        "include_exts",
        "force_exts",
        "exclude_exts",
        "min_size",
        "max_size",
        "modified_after",
        "modified_before",
        "skip_binary",
        "scan_documents",
        "max_file_bytes",
        "encodings",
        "workers",
        "context_chars",
        "max_hits_per_file",
        "use_processes",
    }
    assert payload["report"] == {"mask": True, "format": "text"}
    assert json.loads(dump_config(config)) == payload


def test_roundtrip_preserves_options_and_rules() -> None:
    parsed = config_from_mapping(_config_payload())
    assert parsed.warnings == ()
    reparsed = config_from_mapping(config_to_mapping(parsed))
    assert reparsed.warnings == ()
    assert reparsed.options == parsed.options
    assert reparsed.roots == parsed.roots
    assert reparsed.format == parsed.format == "json"
    assert reparsed.mask == parsed.mask is False
    assert reload_rules(parsed) == reload_rules(reparsed)


def reload_rules(config: ScanConfig) -> list[str]:
    """取出规则 id 列表（``ruleset`` 可能为 None）。"""
    assert config.ruleset is not None
    return [rule.id for rule in config.ruleset.rules]


# ---------------------------------------------------------------------- #
# 容错：旧配置缺字段
# ---------------------------------------------------------------------- #
def test_old_config_missing_fields_falls_back_to_defaults() -> None:
    parsed = config_from_mapping({"version": 1, "roots": ["D:/x"], "options": {"recursive": False}})
    assert parsed.options.recursive is False
    assert parsed.options.max_depth == 0
    assert parsed.options.follow_symlinks is False
    assert parsed.options.ext_whitelist == DEFAULT_SCAN_EXTENSIONS
    assert parsed.options.max_file_bytes == DEFAULT_MAX_FILE_BYTES
    assert parsed.options.workers == 4
    assert parsed.options.modified_after is None and parsed.options.modified_before is None
    assert parsed.options.encodings == DEFAULT_ENCODINGS
    assert parsed.ruleset is None  # 未携带规则，调用方回落内置
    assert parsed.format == "text"
    assert parsed.mask is True
    assert parsed.warnings == ()


def test_older_version_loaded_without_error() -> None:
    payload = _config_payload(version=0)
    parsed = config_from_mapping(payload)
    assert parsed.version == 0
    assert parsed.options.workers == 8  # 字段仍按当前解析器读取
    assert parsed.warnings == ()


# ---------------------------------------------------------------------- #
# 容错：非法值逐字段回落 + 明确提示
# ---------------------------------------------------------------------- #
def test_invalid_values_tolerated_field_by_field() -> None:
    payload = _config_payload()
    payload["options"]["max_depth"] = "abc"
    payload["options"]["workers"] = -5
    payload["options"]["min_size"] = -1
    payload["options"]["encodings"] = ["utf-8", "no-such-codec", 123]
    payload["options"]["modified_after"] = "不是时间"
    payload["options"]["ext_whitelist"] = []
    payload["report"]["format"] = "xml"
    payload["report"]["mask"] = "yes"
    payload["version"] = 999
    payload["roots"].append(123)
    parsed = config_from_mapping(payload)
    # 非法值回落默认：
    assert parsed.options.max_depth == 0
    assert parsed.options.workers == 4
    assert parsed.options.min_size == 0
    assert parsed.options.encodings == ("utf-8",)
    assert parsed.options.modified_after is None
    assert parsed.options.ext_whitelist == DEFAULT_SCAN_EXTENSIONS
    assert parsed.format == "text"
    assert parsed.mask is True
    assert parsed.version == 999
    assert parsed.roots == (Path("D:/data"), Path("D:/other"))
    # 合法字段不受牵连：
    assert parsed.options.follow_symlinks is True
    assert parsed.options.max_file_bytes == 4194304
    assert reload_rules(parsed)
    # 每类问题都有中文提示：
    joined = "\n".join(parsed.warnings)
    assert "max_depth" in joined and "已使用默认值" in joined
    assert "workers" in joined and "不能小于 1" in joined
    assert "min_size" in joined
    assert "no-such-codec" in joined
    assert "modified_after" in joined and "已忽略" in joined
    assert "ext_whitelist" in joined and "默认白名单" in joined
    assert "report.format" in joined
    assert "report.mask" in joined
    assert "version" in joined and "高于" in joined
    assert "roots" in joined and "1 项" in joined


def test_invalid_time_range_drops_both_bounds() -> None:
    payload = _config_payload()
    payload["options"]["modified_after"] = "2026-12-31"
    payload["options"]["modified_before"] = "2026-01-01"
    parsed = config_from_mapping(payload)
    assert parsed.options.modified_after is None
    assert parsed.options.modified_before is None
    assert "晚于" in "\n".join(parsed.warnings)


def test_broken_rules_falls_back_to_none_with_warning() -> None:
    payload = _config_payload()
    payload["rules"] = {"version": 1, "rules": [{"id": 123}], "expression": ""}
    parsed = config_from_mapping(payload)
    assert parsed.ruleset is None
    assert "rules" in "\n".join(parsed.warnings)


def test_expression_override_is_validated() -> None:
    payload = _config_payload()
    payload["expression"] = "not exist_rule"
    parsed = config_from_mapping(payload)
    assert "expression" in "\n".join(parsed.warnings)
    payload["expression"] = ""
    valid = config_from_mapping(payload)
    assert valid.ruleset is not None
    assert valid.ruleset.expression == parsed.ruleset.expression  # type: ignore[union-attr]
    assert valid.warnings == ()


def test_rules_path_resolves_relative_to_config_dir(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.json"
    save_ruleset(builtin_ruleset(), rules_file)
    payload = _config_payload()
    payload.pop("rules")
    payload["rules_path"] = "rules.json"
    parsed = config_from_mapping(payload, base_dir=tmp_path)
    assert parsed.ruleset is not None
    assert len(parsed.ruleset.rules) == len(builtin_ruleset().rules)
    assert parsed.warnings == ()


def test_missing_rules_path_warns_and_returns_none(tmp_path: Path) -> None:
    payload = _config_payload()
    payload.pop("rules")
    payload["rules_path"] = "no-such-rules.json"
    parsed = config_from_mapping(payload, base_dir=tmp_path)
    assert parsed.ruleset is None
    assert "rules_path" in "\n".join(parsed.warnings)


# ---------------------------------------------------------------------- #
# 文件级加载
# ---------------------------------------------------------------------- #
def test_load_config_rejects_non_json_or_non_object(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        load_config(bad)
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="必须是 JSON 对象"):
        load_config(array)


def test_load_config_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="无法读取配置"):
        load_config(tmp_path / "nope.json")


def test_save_and_load_config_roundtrip(tmp_path: Path) -> None:
    config = config_from_mapping(_config_payload())
    target = tmp_path / "sub" / "cfg.json"
    save_config(config, target)
    loaded = load_config(target)
    assert loaded.options == config.options
    assert loaded.roots == config.roots
    assert loaded.warnings == ()


def test_load_config_with_bom_supported(tmp_path: Path) -> None:
    target = tmp_path / "bom.json"
    target.write_bytes(b"\xef\xbb\xbf" + dump_config(config_from_mapping(_config_payload())).encode())
    assert load_config(target).options.workers == 8


# ---------------------------------------------------------------------- #
# CLI 往返
# ---------------------------------------------------------------------- #
def test_cli_save_config_then_run_roundtrip(sample_dir: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.json"
    scan_command(paths=[sample_dir], save_config=cfg, max_depth=1, workers=2, context_chars=10, format="json")
    loaded = load_config(cfg)
    assert loaded.roots == (sample_dir,)
    assert loaded.options.workers == 2
    assert loaded.options.context_chars == 10
    assert loaded.format == "json"
    assert loaded.ruleset is not None
    run_command(config=cfg, output=tmp_path / "out.json")
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["stats"]["hit_files"] >= 1


def test_cli_save_config_records_raw_choice(sample_dir: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "raw.json"
    scan_command(paths=[sample_dir], save_config=cfg, raw=True)
    assert load_config(cfg).mask is False


def test_cli_run_missing_roots_fails(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.json"
    save_config(ScanConfig(roots=(), options=ScanOptions()), cfg)
    with pytest.raises(SystemExit):
        run_command(config=cfg)


def test_cli_run_without_config_fails() -> None:
    with pytest.raises(SystemExit):
        run_command()


def test_cli_run_warns_on_invalid_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    _write_file(data, "a.txt", "手机 13812345678\n")
    payload = _config_payload()
    payload["roots"] = [str(data)]
    payload["options"]["max_depth"] = "abc"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    run_command(config=cfg)
    err = capsys.readouterr().err
    assert "警告：" in err and "max_depth" in err


def test_cli_time_filter_excludes_new_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    target = _write_file(data, "a.txt", "手机 13812345678\n")
    cfg = tmp_path / "cfg.json"
    scan_command(paths=[data], save_config=cfg, modified_after="2999-01-01")
    assert load_config(cfg).options.modified_after is not None
    run_command(config=cfg, format="json", output=tmp_path / "out.json")
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["stats"]["files_scanned"] == 0
    assert report["stats"]["files_skipped_other"] == 1
    assert target.exists()  # 只读，未改动


def test_cli_rejects_reversed_time_range(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        scan_command(paths=[tmp_path], modified_after="2026-12-31", modified_before="2026-01-01")


# ---------------------------------------------------------------------- #
# GUI 设置 ↔ 配置
# ---------------------------------------------------------------------- #
def test_gui_settings_include_scan_documents_and_config_fields() -> None:
    settings = FilescanSettings(scan_documents=False, modified_after="2026-01-01")
    payload = settings_to_mapping(settings)
    assert payload["scan_documents"] is False
    assert payload["modified_after"] == "2026-01-01"
    assert payload["use_processes"] is True
    assert payload["encodings"]
    restored = settings_from_mapping(payload)
    assert restored.scan_documents is False
    assert restored.modified_after == "2026-01-01"
    assert restored.use_processes is True


def test_gui_settings_tolerate_invalid_extra_fields() -> None:
    payload = settings_to_mapping(FilescanSettings())
    payload["report_format"] = "xml"
    assert settings_from_mapping(payload).report_format == "text"
    payload["encodings"] = "utf-8"
    assert settings_from_mapping(payload).encodings == DEFAULT_ENCODINGS
    payload["max_hits_per_file"] = 0
    payload["use_processes"] = "yes"
    restored = settings_from_mapping(payload)
    assert restored.max_hits_per_file == 1  # 越界按既有设置口径收敛到下限
    assert restored.use_processes is True


def test_gui_settings_and_config_options_agree(tmp_path: Path) -> None:
    """GUI 回填字段与配置字段同名同义：由 settings 构造 options 后往返不漂移。"""
    settings = FilescanSettings(
        paths=(str(tmp_path),),
        scan_documents=False,
        modified_after="2026-01-01T00:00:00",
        workers=3,
    )
    config = ScanConfig(
        roots=tuple(Path(item) for item in settings.paths),
        options=ScanOptions(scan_documents=settings.scan_documents, workers=settings.workers),
    )
    payload = config_to_mapping(config)
    assert payload["options"]["scan_documents"] is False
    assert payload["options"]["workers"] == 3


def test_registry_contains_run_subcommand() -> None:
    assert set(_TOOL_REGISTRY["filescan"]) == {"scan", "run", "rules"}
