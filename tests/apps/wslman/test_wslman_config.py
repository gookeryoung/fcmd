"""wslman 配置测试：``.wslconfig`` 解析与 ``wslman.json`` 字段级容错。

两者都只读：``.wslconfig`` 只解析并给警告，``wslman.json`` 单字段非法只回落该字段。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import _config, _wslconfig

# ---------------------------------------------------------------------- #
# .wslconfig
# ---------------------------------------------------------------------- #


def test_wslconfig_missing_file(tmp_path: Path) -> None:
    config = _wslconfig.load_wslconfig(tmp_path / ".wslconfig")
    assert config.exists is False
    assert config.ok is True
    assert config.get("wsl2", "memory") is None
    assert _wslconfig.summary_lines(config) == [f"未找到 {tmp_path / '.wslconfig'}（可选文件，WSL 会使用默认值）"]


def test_wslconfig_parse_known_and_unknown(tmp_path: Path) -> None:
    path = tmp_path / ".wslconfig"
    path.write_text(
        "[wsl2]\nmemory=8GB\nprocessors=4\nunknownkey=1\n\n[experimental]\nautoMemoryReclaim=gradual\n\n[foo]\nbar=1\n",
        encoding="utf-8",
    )
    config = _wslconfig.load_wslconfig(path)
    assert config.exists is True
    assert config.error is None
    assert config.get("wsl2", "memory") == "8GB"
    assert config.get("experimental", "autoMemoryReclaim") == "gradual"
    joined = "\n".join(config.warnings)
    assert "未知键 unknownkey" in joined
    assert "未知小节 [foo]" in joined


def test_wslconfig_bom_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / ".wslconfig"
    path.write_bytes("\ufeff[wsl2]\nmemory=4GB\n".encode())
    config = _wslconfig.load_wslconfig(path)
    assert config.get("wsl2", "memory") == "4GB"


def test_wslconfig_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / ".wslconfig"
    path.write_text("[wsl2]\nmemory=4GB\nmemory=6GB\n", encoding="utf-8")
    config = _wslconfig.load_wslconfig(path)
    assert config.get("wsl2", "memory") == "6GB"
    assert "重复" in "\n".join(config.warnings)


def test_wslconfig_orphan_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / ".wslconfig"
    path.write_text("top=1\n[wsl2]\nmemory=4GB\n", encoding="utf-8")
    config = _wslconfig.load_wslconfig(path)
    assert config.ok is False
    assert config.error is not None
    assert "小节之外" in "\n".join(config.warnings)


def test_wslconfig_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / ".wslconfig"
    path.write_text("[wsl2\nmemory=4GB\n", encoding="utf-8")
    config = _wslconfig.load_wslconfig(path)
    assert config.ok is False
    assert config.error is not None
    assert "解析失败" in config.error
    assert _wslconfig.summary_lines(config)[0].startswith("[错误]")


def test_wslconfig_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".wslconfig"
    path.write_text("[wsl2]\n", encoding="utf-8")

    def boom(self: Path, encoding: str | None = None) -> str:
        raise OSError("拒绝访问")

    monkeypatch.setattr(Path, "read_text", boom)
    config = _wslconfig.load_wslconfig(path)
    assert config.exists is True
    assert config.error is not None
    assert "拒绝访问" in config.error


def test_wslconfig_empty_and_summary(tmp_path: Path) -> None:
    path = tmp_path / ".wslconfig"
    path.write_text("", encoding="utf-8")
    config = _wslconfig.load_wslconfig(path)
    assert config.ok is True
    lines = _wslconfig.summary_lines(config)
    assert lines == [f"{path} 为空"]

    path.write_text("[wsl2]\nprocessors=4\nmemory=8GB\n", encoding="utf-8")
    lines = _wslconfig.summary_lines(_wslconfig.load_wslconfig(path))
    assert lines[0] == "[wsl2]"
    assert lines[1] == "  memory = 8GB"
    assert lines[2] == "  processors = 4"


def test_wslconfig_default_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert _wslconfig.default_path() == tmp_path / ".wslconfig"


def test_wslconfig_default_path_without_userprofile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("C:/Users/demo")))
    assert _wslconfig.default_path() == Path("C:/Users/demo/.wslconfig")


# ---------------------------------------------------------------------- #
# wslman.json
# ---------------------------------------------------------------------- #


def _write_json(path: Path, payload: Any) -> Path:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_config_default_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert _config.default_path() == tmp_path / "wslman.json"
    monkeypatch.delenv("FCMD_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("C:/Users/demo")))
    assert _config.default_path() == Path("C:/Users/demo/.fcmd/wslman.json")


def test_config_missing_file_uses_defaults(tmp_path: Path) -> None:
    config = _config.load_config(tmp_path / "wslman.json")
    assert config.download_dir == ""
    assert config.manifest_url is None
    assert config.sha256 == {}
    assert config.safety_margin == 1.1
    assert config.min_free_after_mb == 512
    assert config.keep_partial is False
    assert config.terminal_program == "auto"
    assert config.terminal_extra_args == ()
    assert config.require_backup_on_replace is True
    assert config.backups_dir == ""
    assert config.warnings == ()


def test_config_reads_settings(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "wslman.json",
        {
            "download": {
                "dir": "D:/images",
                "manifest_url": " https://example.test/manifest.json ",
                "sha256": {"Ubuntu": " ABCDEF "},
            },
            "transfer": {"temp_dir": "E:/tmp", "safety_margin": 1.5, "min_free_after_mb": 1024, "keep_partial": True},
            "terminal": {"program": "WT", "extra_args": ["--maximized"]},
            "safety": {"require_backup_on_replace": False},
            "backups": {"dir": "D:/backups"},
            "unknown": {"ignored": True},
        },
    )
    config = _config.load_config(path)
    assert config.download_dir == "D:/images"
    assert config.manifest_url == "https://example.test/manifest.json"
    assert config.sha256 == {"Ubuntu": "abcdef"}
    assert config.temp_dir == "E:/tmp"
    assert config.safety_margin == 1.5
    assert config.min_free_after_mb == 1024
    assert config.keep_partial is True
    assert config.terminal_program == "wt"
    assert config.terminal_extra_args == ("--maximized",)
    assert config.require_backup_on_replace is False
    assert config.backups_dir == "D:/backups"
    assert config.warnings == ()


def test_config_broken_json_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "wslman.json"
    path.write_text("{not json", encoding="utf-8")
    config = _config.load_config(path)
    assert config.path == path
    assert config.safety_margin == 1.1


def test_config_non_object_falls_back(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "wslman.json", ["not", "an", "object"])
    config = _config.load_config(path)
    assert config.path == path
    assert config.backups_dir == ""


def test_config_field_level_fallbacks(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "wslman.json",
        {
            "download": {"dir": 1, "manifest_url": "  ", "sha256": {"ok": 2, "keep": ""}},
            "transfer": {
                "temp_dir": 2,
                "safety_margin": "big",
                "min_free_after_mb": True,
                "keep_partial": "yes",
            },
            "terminal": {"program": "tmux", "extra_args": ["--x", 1]},
            "safety": {"require_backup_on_replace": 1},
            "backups": {"dir": []},
        },
    )
    config = _config.load_config(path)
    assert config.download_dir == ""
    assert config.manifest_url is None
    assert config.sha256 == {}
    assert config.temp_dir == ""
    assert config.safety_margin == 1.1
    assert config.min_free_after_mb == 512
    assert config.keep_partial is False
    assert config.terminal_program == "auto"
    assert config.terminal_extra_args == ()
    assert config.require_backup_on_replace is True
    assert config.backups_dir == ""
    joined = "\n".join(config.warnings)
    for label in (
        "download.dir",
        "download.manifest_url",
        "download.sha256",
        "transfer.temp_dir",
        "transfer.safety_margin",
        "transfer.min_free_after_mb",
        "transfer.keep_partial",
        "terminal.program",
        "terminal.extra_args",
        "safety.require_backup_on_replace",
        "backups.dir",
    ):
        assert label in joined


def test_config_section_not_object(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "wslman.json", {"download": [], "transfer": "x", "terminal": 1})
    config = _config.load_config(path)
    joined = "\n".join(config.warnings)
    assert "download 不是对象" in joined
    assert "transfer 不是对象" in joined
    assert "terminal 不是对象" in joined


def test_config_margin_out_of_range(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "wslman.json", {"transfer": {"safety_margin": 9.0}})
    config = _config.load_config(path)
    assert config.safety_margin == 1.1
    assert "超出" in "\n".join(config.warnings)


def test_config_negative_min_free(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "wslman.json", {"transfer": {"min_free_after_mb": -1}})
    config = _config.load_config(path)
    assert config.min_free_after_mb == 512
    assert "不能为负" in "\n".join(config.warnings)


def test_config_sha256_not_object(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "wslman.json", {"download": {"sha256": "abc"}})
    config = _config.load_config(path)
    assert config.sha256 == {}
    assert "download.sha256 不是对象" in "\n".join(config.warnings)


def test_config_extra_args_not_list(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "wslman.json", {"terminal": {"extra_args": "--maximized"}})
    config = _config.load_config(path)
    assert config.terminal_extra_args == ()
    assert "terminal.extra_args" in "\n".join(config.warnings)
