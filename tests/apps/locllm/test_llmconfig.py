"""localllm 配置单一来源测试：默认值、优先级、写入合并与旧配置迁移。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.locllm import _llmconfig
from fcmd.apps.locllm.llmbase import ServeError


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
    assert _llmconfig.models_roots(home=home) == [_llmconfig.default_models_root()]
    assert _llmconfig.work_dir(home=home) == _llmconfig.default_models_root()
    assert _llmconfig.work_dir(home=home) in _llmconfig.models_roots(home=home)


def test_config_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom.json"
    monkeypatch.setenv("FCMD_CONFIG", str(target))
    assert _llmconfig.config_path() == target


def test_env_overrides_config(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(home / "serve" / "config.json", {"models_roots": [str(tmp_path / "cfg")], "work_dir": str(tmp_path / "w")})
    env_models = tmp_path / "env-models"
    env_work = tmp_path / "env-work"
    monkeypatch.setenv("FCMD_MODELS", str(env_models))
    monkeypatch.setenv("FCMD_WORK", str(env_work))
    assert _llmconfig.models_roots(home=home) == [env_models]
    assert _llmconfig.work_dir(home=home) == env_work


def test_explicit_beats_env(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_MODELS", str(tmp_path / "env"))
    monkeypatch.setenv("FCMD_WORK", str(tmp_path / "env"))
    assert _llmconfig.models_roots(tmp_path / "explicit", home) == [tmp_path / "explicit"]
    assert _llmconfig.work_dir(tmp_path / "explicit", home) == tmp_path / "explicit"


def test_engine_and_serve_env_override(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_ENGINES", str(tmp_path / "engines"))
    monkeypatch.setenv("FCMD_SERVE_DIR", str(tmp_path / "serve"))
    assert _llmconfig.engines_root() == tmp_path / "engines"
    assert _llmconfig.serve_dir() == tmp_path / "serve"


def test_config_keys_used_when_env_absent(home: Path, tmp_path: Path) -> None:
    _write(
        home / "serve" / "config.json",
        {
            "models_roots": [str(tmp_path / "models")],
            "work_dir": str(tmp_path / "work"),
            "engines_root": str(tmp_path / "engines"),
            "serve_dir": str(tmp_path / "serve"),
        },
    )
    assert _llmconfig.models_roots(home=home) == [tmp_path / "models"]
    assert _llmconfig.work_dir(home=home) == tmp_path / "work"
    assert _llmconfig.engines_root(home) == tmp_path / "engines"
    assert _llmconfig.serve_dir(home) == tmp_path / "serve"


# ---------------------------------------------------------------------- #
# 写入语义
# ---------------------------------------------------------------------- #
def test_add_and_remove_preserve_other_keys(home: Path, tmp_path: Path) -> None:
    _write(home / "serve" / "config.json", {"extra": {"keep": True}})
    first = tmp_path / "first"
    second = tmp_path / "second"
    _llmconfig.add_models_root(first, home)
    _llmconfig.add_models_root(second, home)
    assert _llmconfig.configured_roots(home) == [first, second]
    assert _read(home / "serve" / "config.json")["extra"] == {"keep": True}

    _llmconfig.remove_models_root(first, home)
    assert _llmconfig.configured_roots(home) == [second]
    assert _read(home / "serve" / "config.json")["extra"] == {"keep": True}


def test_add_deduplicates(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "models"
    _llmconfig.add_models_root(target, home)
    _llmconfig.add_models_root(target, home)
    assert _llmconfig.configured_roots(home) == [target]


def test_remove_missing_raises(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="配置中没有模型目录"):
        _llmconfig.remove_models_root(tmp_path / "missing", home)


def test_reset_only_removes_models_key(home: Path) -> None:
    _write(home / "serve" / "config.json", {"models_roots": ["D:/models"], "extra": {"keep": True}})
    assert _llmconfig.reset_models_roots(home) == [_llmconfig.default_models_root()]
    saved = _read(home / "serve" / "config.json")
    assert "models_roots" not in saved
    assert saved["extra"] == {"keep": True}


def test_set_option_none_deletes(home: Path) -> None:
    _llmconfig.set_option("work_dir", "D:/work", home)
    assert _llmconfig.get_option("work_dir", home) == "D:/work"
    _llmconfig.set_option("work_dir", None, home)
    assert _llmconfig.get_option("work_dir", home) is None


def test_save_config_creates_parent(home: Path) -> None:
    path = _llmconfig.save_config({"models_roots": []}, home)
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
        "models_roots": [str(tmp_path / "models")],
        "work_dir": str(tmp_path / "work"),
        "engines_root": str(tmp_path / "engines"),
        "serve_dir": str(tmp_path / "serve"),
    }
    saved = _read(home / "serve" / "config.json")
    assert "models_root" not in saved
    assert "download_dir" not in saved


def test_migrates_legacy_list_models(home: Path, tmp_path: Path) -> None:
    _write(home / "serve" / "config.json", {"models": [str(tmp_path / "a"), "", str(tmp_path / "b")]})
    assert _llmconfig.configured_roots(home) == [tmp_path / "a", tmp_path / "b"]


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


def test_migrates_download_dest_absolute(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "downloads"
    legacy = home / "ui" / "locllmw.json"
    _write(legacy, {"geometry": "900x600", "download": {"dest": str(target), "source": "hf"}})
    assert _llmconfig.work_dir(home=home) == target
    saved = _read(legacy)
    assert saved["download"] == {"source": "hf"}
    assert saved["geometry"] == "900x600"


def test_download_dest_relative_is_dropped(home: Path) -> None:
    legacy = home / "ui" / "locllmw.json"
    _write(legacy, {"download": {"dest": "models", "source": "hf"}})
    assert _llmconfig.work_dir(home=home) == _llmconfig.default_models_root()
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


def test_write_failure_is_ignored_on_migration(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(home / "serve" / "config.json", {"models_root": "D:/models"})

    def _denied(_self: Path, *_args: object, **_kwargs: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr(Path, "write_text", _denied)
    assert _llmconfig.load_config(home) == {"models_roots": ["D:/models"]}
