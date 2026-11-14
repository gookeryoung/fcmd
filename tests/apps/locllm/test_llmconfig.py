"""localllm 配置单一来源测试：默认值、优先级、写入合并与旧配置迁移。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.locllm import _llmconfig


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的 FCMD_HOME，并清空全部目录覆盖环境变量。"""
    target = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(target))
    for name in ("FCMD_MODELS", "FCMD_WORK", "FCMD_SERVE_DIR", "FCMD_CONFIG", "FCMD_ENGINES"):
        monkeypatch.delenv(name, raising=False)
    return target


def _write(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- #
# 默认值与优先级
# ---------------------------------------------------------------------- #
def test_default_paths(home: Path) -> None:
    assert _llmconfig.config_path(home) == home / "serve" / "config.json"
    assert _llmconfig.serve_dir(home) == home / "serve"
    assert _llmconfig.engines_root(home) == home / "engines" / "llama.cpp"
    assert _llmconfig.models_dir(home=home) == _llmconfig.default_models_dir()
    assert _llmconfig.products_dir(home=home) == _llmconfig.default_models_dir() / "converted"
    assert _llmconfig.configured_models_dir(home) is None
    assert _llmconfig.models_dir_extra(home) == []
    assert _llmconfig.ignored_work_dir(home) is None


def test_config_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom.json"
    monkeypatch.setenv("FCMD_CONFIG", str(target))
    assert _llmconfig.config_path() == target


def test_env_overrides_config(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(home / "serve" / "config.json", {"models_dir": str(tmp_path / "cfg")})
    env_models = tmp_path / "env-models"
    monkeypatch.setenv("FCMD_MODELS", str(env_models))
    assert _llmconfig.models_dir(home=home) == env_models


def test_fcmd_work_env_is_ignored(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_WORK", str(tmp_path / "env-work"))
    assert _llmconfig.models_dir(home=home) == _llmconfig.default_models_dir()
    assert _llmconfig.products_dir(home=home) == _llmconfig.default_models_dir() / "converted"
    assert _llmconfig.ignored_work_dir(home) is None


def test_explicit_beats_env(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_MODELS", str(tmp_path / "env"))
    assert _llmconfig.models_dir(tmp_path / "explicit", home) == tmp_path / "explicit"
    assert _llmconfig.products_dir(tmp_path / "explicit", home) == tmp_path / "explicit" / "converted"


def test_engine_and_serve_env_override(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_ENGINES", str(tmp_path / "engines"))
    monkeypatch.setenv("FCMD_SERVE_DIR", str(tmp_path / "serve"))
    assert _llmconfig.engines_root() == tmp_path / "engines"
    assert _llmconfig.serve_dir() == tmp_path / "serve"


def test_config_keys_used_when_env_absent(home: Path, tmp_path: Path) -> None:
    _write(
        home / "serve" / "config.json",
        {
            "models_dir": str(tmp_path / "models"),
            "engines_root": str(tmp_path / "engines"),
            "serve_dir": str(tmp_path / "serve"),
        },
    )
    assert _llmconfig.models_dir(home=home) == tmp_path / "models"
    assert _llmconfig.engines_root(home) == tmp_path / "engines"
    assert _llmconfig.serve_dir(home) == tmp_path / "serve"


# ---------------------------------------------------------------------- #
# 写入语义
# ---------------------------------------------------------------------- #
def test_set_models_dir_preserves_other_keys(home: Path, tmp_path: Path) -> None:
    _write(home / "serve" / "config.json", {"extra": {"keep": True}})
    target = tmp_path / "models"
    assert _llmconfig.set_models_dir(target, home) == target
    saved = _read(home / "serve" / "config.json")
    assert saved["models_dir"] == str(target)
    assert saved["extra"] == {"keep": True}


def test_set_models_dir_replaces_previous_value(home: Path, tmp_path: Path) -> None:
    _llmconfig.set_models_dir(tmp_path / "first", home)
    _llmconfig.set_models_dir(tmp_path / "second", home)
    assert _llmconfig.configured_models_dir(home) == tmp_path / "second"
    assert _llmconfig.models_dir(home=home) == tmp_path / "second"


def test_set_models_dir_none_resets_to_default(home: Path, tmp_path: Path) -> None:
    _llmconfig.set_models_dir(tmp_path / "models", home)
    assert _llmconfig.set_models_dir(None, home) == _llmconfig.default_models_dir()
    saved = _read(home / "serve" / "config.json")
    assert "models_dir" not in saved


def test_set_models_dir_clears_migration_records(home: Path, tmp_path: Path) -> None:
    _write(
        home / "serve" / "config.json",
        {"models_roots": [str(tmp_path / "a"), str(tmp_path / "b")], "work_dir": str(tmp_path / "w")},
    )
    assert _llmconfig.models_dir_extra(home) == [str(tmp_path / "b")]
    assert _llmconfig.ignored_work_dir(home) == str(tmp_path / "w")
    _llmconfig.set_models_dir(tmp_path / "c", home)
    assert _llmconfig.models_dir_extra(home) == []
    assert _llmconfig.ignored_work_dir(home) is None
    saved = _read(home / "serve" / "config.json")
    assert "models_dir_extra" not in saved
    assert "ignored_work_dir" not in saved


def test_set_option_none_deletes(home: Path) -> None:
    _llmconfig.set_option("engines_root", "D:/engines", home)
    assert _llmconfig.get_option("engines_root", home) == "D:/engines"
    _llmconfig.set_option("engines_root", None, home)
    assert _llmconfig.get_option("engines_root", home) is None


def test_save_config_creates_parent(home: Path) -> None:
    path = _llmconfig.save_config({"models_dir": "D:/models"}, home)
    assert path.is_file()


# ---------------------------------------------------------------------- #
# 旧键迁移
# ---------------------------------------------------------------------- #
def test_migrates_legacy_config_keys(home: Path, tmp_path: Path) -> None:
    _write(
        home / "serve" / "config.json",
        {
            "models_root": str(tmp_path / "models"),
            "download_dir": str(tmp_path / "work"),
            "engine_dir": str(tmp_path / "engines"),
            "serve": str(tmp_path / "serve"),
        },
    )
    config = _llmconfig.load_config(home)
    assert config == {
        "models_dir": str(tmp_path / "models"),
        "ignored_work_dir": str(tmp_path / "work"),
        "engines_root": str(tmp_path / "engines"),
        "serve_dir": str(tmp_path / "serve"),
    }
    saved = _read(home / "serve" / "config.json")
    assert "models_root" not in saved
    assert "download_dir" not in saved
    assert "work_dir" not in saved


def test_migrates_legacy_list_models_to_single_dir(home: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    _write(home / "serve" / "config.json", {"models": [str(first), "", str(second)]})
    assert _llmconfig.models_dir(home=home) == first
    assert _llmconfig.models_dir_extra(home) == [str(second)]
    saved = _read(home / "serve" / "config.json")
    assert "models" not in saved
    assert saved["models_dir"] == str(first)
    assert saved["models_dir_extra"] == [str(second)]


def test_migrates_models_roots_list_records_extras(home: Path, tmp_path: Path) -> None:
    roots = [tmp_path / "one", tmp_path / "two", tmp_path / "three"]
    _write(home / "serve" / "config.json", {"models_roots": [str(path) for path in roots]})
    assert _llmconfig.models_dir(home=home) == roots[0]
    assert _llmconfig.models_dir_extra(home) == [str(roots[1]), str(roots[2])]
    saved = _read(home / "serve" / "config.json")
    assert "models_roots" not in saved
    assert saved["models_dir"] == str(roots[0])


def test_existing_models_dir_wins_over_legacy_keys(home: Path, tmp_path: Path) -> None:
    _write(
        home / "serve" / "config.json",
        {"models_dir": str(tmp_path / "current"), "models_roots": [str(tmp_path / "old")]},
    )
    assert _llmconfig.models_dir(home=home) == tmp_path / "current"
    assert _llmconfig.models_dir_extra(home) == []
    saved = _read(home / "serve" / "config.json")
    assert "models_roots" not in saved


def test_migrates_llmenvw_overrides_once(home: Path, tmp_path: Path) -> None:
    legacy = home / "ui" / "llmenvw.json"
    _write(
        legacy,
        {"serve_dir": str(tmp_path / "serve"), "engines_root": str(tmp_path / "engines"), "geometry": "900x600"},
    )
    config = _llmconfig.load_config(home)
    assert config["serve_dir"] == str(tmp_path / "serve")
    assert config["engines_root"] == str(tmp_path / "engines")
    assert not legacy.exists()
    assert (home / "ui" / "llmenvw.json.migrated").is_file()


def test_legacy_work_dir_is_ignored_and_recorded(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "work"
    _write(home / "serve" / "config.json", {"models_dir": str(tmp_path / "models"), "work_dir": str(target)})
    assert _llmconfig.models_dir(home=home) == tmp_path / "models"
    assert _llmconfig.ignored_work_dir(home) == str(target)
    saved = _read(home / "serve" / "config.json")
    assert "work_dir" not in saved
    assert saved["ignored_work_dir"] == str(target)


def test_migrates_download_dest_absolute(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "downloads"
    legacy = home / "ui" / "locllmw.json"
    _write(legacy, {"geometry": "900x600", "download": {"dest": str(target), "source": "hf"}})
    assert _llmconfig.models_dir(home=home) == _llmconfig.default_models_dir()
    assert _llmconfig.ignored_work_dir(home) == str(target)
    saved = _read(legacy)
    assert saved["download"] == {"source": "hf"}
    assert saved["geometry"] == "900x600"


def test_download_dest_relative_is_dropped(home: Path) -> None:
    legacy = home / "ui" / "locllmw.json"
    _write(legacy, {"download": {"dest": "models", "source": "hf"}})
    assert _llmconfig.ignored_work_dir(home) is None
    assert "dest" not in _read(legacy)["download"]


def test_invalid_config_returns_empty(home: Path) -> None:
    path = home / "serve" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text("{bad", encoding="utf-8")
    assert _llmconfig.load_config(home) == {}


def test_non_object_config_returns_empty(home: Path) -> None:
    _write(home / "serve" / "config.json", {"unused": True})
    (home / "serve" / "config.json").write_text("[1, 2]", encoding="utf-8")
    assert _llmconfig.load_config(home) == {}


def test_legacy_models_roots_non_list_is_dropped(home: Path) -> None:
    _write(home / "serve" / "config.json", {"models_roots": 123})
    assert _llmconfig.load_config(home) == {}


def test_legacy_models_roots_empty_list_is_dropped(home: Path) -> None:
    _write(home / "serve" / "config.json", {"models_roots": ["", 1]})
    assert _llmconfig.load_config(home) == {}


def test_legacy_llmenvw_non_string_values_are_skipped(home: Path) -> None:
    legacy = home / "ui" / "llmenvw.json"
    _write(legacy, {"serve_dir": 123, "engines_root": ""})
    assert _llmconfig.load_config(home) == {}
    assert not legacy.exists()
    assert (home / "ui" / "llmenvw.json.migrated").is_file()


def test_download_dest_non_string_is_dropped_without_warning(home: Path) -> None:
    legacy = home / "ui" / "locllmw.json"
    _write(legacy, {"download": {"dest": 123, "source": "hf"}})
    assert _llmconfig.load_config(home) == {}
    assert _read(legacy)["download"] == {"source": "hf"}


def test_default_models_dir_without_d_drive(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_exists = Path.exists

    def _exists(self: Path) -> bool:
        if self.drive == "D:":
            return False
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)
    assert _llmconfig.default_models_dir() == home / "models"


def test_write_failure_is_ignored_on_migration(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(home / "serve" / "config.json", {"models_root": "D:/models"})

    def _denied(_self: Path, *_args: object, **_kwargs: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr(Path, "write_text", _denied)
    assert _llmconfig.load_config(home) == {"models_dir": "D:/models"}
