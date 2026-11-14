"""locllmw 设置页纯逻辑测试：目录 / 引擎 / 界面三组与写入行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.locllm import _llmconfig
from fcmd.apps.locllm import _locllmw_settings as settings_page


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(target))
    for name in ("FCMD_MODELS", "FCMD_WORK", "FCMD_ENGINES", "FCMD_SERVE_DIR", "FCMD_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    return target


def _write_config(home: Path, data: dict[str, object]) -> Path:
    path = home / "serve" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- #
# 目录组
# ---------------------------------------------------------------------- #
def test_directory_rows_default_values(home: Path) -> None:
    rows = {label: (value, source) for label, value, source in settings_page.directory_rows(home)}
    assert rows["模型目录"] == (str(_llmconfig.default_models_dir()), "默认")
    assert rows["工作目录"] == (str(_llmconfig.default_models_dir()), "默认")


def test_directory_rows_marks_config_source(home: Path, tmp_path: Path) -> None:
    models, work = tmp_path / "models", tmp_path / "work"
    _write_config(home, {"models_dir": str(models), "work_dir": str(work)})
    rows = {label: value for label, value, _source in settings_page.directory_rows(home)}
    assert rows == {"模型目录": str(models), "工作目录": str(work)}


def test_directory_rows_marks_env_source(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_models, env_work = tmp_path / "env-models", tmp_path / "env-work"
    monkeypatch.setenv("FCMD_MODELS", str(env_models))
    monkeypatch.setenv("FCMD_WORK", str(env_work))
    rows = {label: (value, source) for label, value, source in settings_page.directory_rows()}
    assert rows == {"模型目录": (str(env_models), "环境变量"), "工作目录": (str(env_work), "环境变量")}


# ---------------------------------------------------------------------- #
# 引擎组
# ---------------------------------------------------------------------- #
def test_engine_rows_default(home: Path) -> None:
    assert settings_page.engine_rows(home) == [("引擎根目录", str(home / "engines" / "llama.cpp"), "默认")]


def test_engine_rows_marks_config(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "engines"
    _write_config(home, {"engines_root": str(target)})
    assert settings_page.engine_rows(home) == [("引擎根目录", str(target), "配置")]


# ---------------------------------------------------------------------- #
# 界面组
# ---------------------------------------------------------------------- #
def test_ui_rows_default_geometry(home: Path) -> None:
    rows = {label: value for label, value, _source in settings_page.ui_rows()}
    assert rows["窗口配置文件"] == str(home / "ui" / "locllmw.json")
    assert rows["窗口位置与大小"] == "未保存（使用默认尺寸）"


def test_ui_rows_reads_saved_geometry(home: Path) -> None:
    path = home / "ui" / "locllmw.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"geometry": "1024x768+10+20"}), encoding="utf-8")
    rows = {label: value for label, value, _source in settings_page.ui_rows()}
    assert rows["窗口位置与大小"] == "1024x768+10+20"


# ---------------------------------------------------------------------- #
# 迁移提示
# ---------------------------------------------------------------------- #
def test_migration_note_empty_by_default(home: Path) -> None:
    assert settings_page.migration_note(home) == ""


def test_migration_note_lists_dropped_roots(home: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    _write_config(home, {"models_roots": [str(first), str(second)]})
    note = settings_page.migration_note(home)
    assert str(second) in note
    assert str(first) not in note


# ---------------------------------------------------------------------- #
# 写入
# ---------------------------------------------------------------------- #
def test_apply_models_dir_writes_single_value(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "models"
    assert settings_page.apply_path("models_dir", str(target), home) == target
    saved = _read(home / "serve" / "config.json")
    assert saved["models_dir"] == str(target)


def test_apply_work_dir_writes_value(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "work"
    assert settings_page.apply_path("work_dir", str(target), home) == target
    assert _read(home / "serve" / "config.json")["work_dir"] == str(target)


def test_apply_engines_root_writes_value(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "engines"
    assert settings_page.apply_path("engines_root", str(target), home) == target
    assert _read(home / "serve" / "config.json")["engines_root"] == str(target)


def test_apply_none_restores_defaults(home: Path, tmp_path: Path) -> None:
    _llmconfig.set_models_dir(tmp_path / "models", home)
    _llmconfig.set_option("work_dir", str(tmp_path / "work"), home)
    _llmconfig.set_option("engines_root", str(tmp_path / "engines"), home)
    assert settings_page.apply_path("models_dir", None, home) == _llmconfig.default_models_dir()
    assert settings_page.apply_path("work_dir", None, home) == _llmconfig.default_models_dir()
    assert settings_page.apply_path("engines_root", None, home) == home / "engines" / "llama.cpp"


def test_apply_unknown_key_rejected(home: Path) -> None:
    with pytest.raises(ValueError, match="未知设置项"):
        settings_page.apply_path("serve_dir", "D:/serve", home)
