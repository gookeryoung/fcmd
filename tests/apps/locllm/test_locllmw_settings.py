"""locllmw 设置页纯逻辑测试：目录布局、模型目录来源、配置文本与环境变量格式。

设置页的 Tk 控件不创建（CI 无显示环境），只覆盖可脱离界面验证的部分。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.locllm import _locllmw_settings as settings_page
from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm.llmbase import DEFAULT_API_KEY, ServeError, ServeState


def _state(name: str = "qwen") -> ServeState:
    """构造状态对象。"""
    return ServeState(
        name=name,
        pid=4321,
        create_time=0.0,
        host="127.0.0.1",
        port=8080,
        model="D:/models/m.gguf",
        profile="balanced",
        context=65536,
        engine="llama-server.exe",
        alias="qwen3-coder-30b-a3b",
        api_key=DEFAULT_API_KEY,
        log="log.txt",
        started_at="2026-11-09T12:00:00+08:00",
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的 FCMD_HOME，并清空全部目录覆盖环境变量。"""
    target = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(target))
    for name in ("FCMD_MODELS", "FCMD_WORK", "FCMD_SERVE_DIR", "FCMD_CONFIG", "FCMD_ENGINES"):
        monkeypatch.delenv(name, raising=False)
    return target


# ---------------------------------------------------------------------- #
# 目录布局
# ---------------------------------------------------------------------- #
def test_layout_rows_follows_home(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "models"
    llmenv.add_models_root(root, home)
    llmenv.save_state(_state(), home)
    rows = {label: (value, source) for label, value, source in settings_page.layout_rows(home)}
    assert rows["FCMD_HOME"] == (str(home), "环境变量")
    assert rows["服务状态目录"] == (str(home / "serve"), "默认")
    assert rows["配置文件"] == (str(home / "serve" / "config.json"), "默认")
    assert rows["引擎根目录"] == (str(home / "engines" / "llama.cpp"), "默认")
    assert rows["工作目录"] == (str(root), "默认")
    assert rows["模型目录"][0] == str(root)
    assert rows["已登记服务"] == ("1", "自动发现")


def test_layout_rows_joins_multiple_roots(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    llmenv.add_models_root(first, home)
    llmenv.add_models_root(second, home)
    rows = {label: value for label, value, _source in settings_page.layout_rows(home)}
    assert rows["模型目录"] == f"{first}, {second}"


def test_layout_rows_marks_config_source(home: Path, tmp_path: Path) -> None:
    _llmconfig_set("engines_root", str(tmp_path / "engines"), home)
    _llmconfig_set("serve_dir", str(tmp_path / "serve"), home)
    _llmconfig_set("work_dir", str(tmp_path / "work"), home)
    rows = {label: (value, source) for label, value, source in settings_page.layout_rows(home)}
    assert rows["引擎根目录"] == (str(tmp_path / "engines"), "配置")
    assert rows["服务状态目录"] == (str(tmp_path / "serve"), "配置")
    assert rows["工作目录"] == (str(tmp_path / "work"), "配置")


def test_layout_rows_marks_process_env(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engines = tmp_path / "engines"
    monkeypatch.setenv("FCMD_ENGINES", str(engines))
    rows = {label: (value, source) for label, value, source in settings_page.layout_rows()}
    assert rows["引擎根目录"] == (str(engines), "环境变量")


# ---------------------------------------------------------------------- #
# 模型目录来源与编辑
# ---------------------------------------------------------------------- #
def test_model_root_rows_default_source(home: Path) -> None:
    rows = settings_page.model_root_rows(home)
    assert len(rows) == 1
    assert rows[0][0] == str(llmenv.default_models_root())
    assert rows[0][2] == "默认"


def test_model_root_rows_configured_source(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "models"
    target.mkdir()
    llmenv.add_models_root(target, home)
    assert settings_page.model_root_rows(home) == [(str(target), "存在", "已登记")]


def test_model_root_rows_marks_missing_directory(home: Path, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    llmenv.add_models_root(missing, home)
    assert settings_page.model_root_rows(home) == [(str(missing), "不存在", "已登记")]


def test_model_root_rows_env_override(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_root = tmp_path / "env-models"
    env_root.mkdir()
    monkeypatch.setenv("FCMD_MODELS", str(env_root))
    assert settings_page.model_root_rows(home) == [(str(env_root), "存在", "环境变量 FCMD_MODELS")]


def test_replace_models_root_keeps_order_and_other_keys(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    llmenv.add_models_root(first, home)
    llmenv.add_models_root(second, home)
    config = llmenv.load_config(home)
    config["extra"] = {"keep": True}
    llmenv.save_config(config, home)

    moved = tmp_path / "moved"
    updated = settings_page.replace_models_root(first, moved, home)
    assert updated == [moved, second]
    saved = llmenv.load_config(home)
    assert saved["models_roots"] == [str(moved), str(second)]
    assert saved["extra"] == {"keep": True}


def test_replace_models_root_deduplicates_target(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    llmenv.add_models_root(first, home)
    llmenv.add_models_root(second, home)
    assert settings_page.replace_models_root(first, second, home) == [second]


def test_replace_models_root_requires_registered_source(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="配置中没有模型目录"):
        settings_page.replace_models_root(tmp_path / "missing", tmp_path / "new", home)


# ---------------------------------------------------------------------- #
# 配置文件文本
# ---------------------------------------------------------------------- #
def test_config_text_missing_returns_empty_object(home: Path) -> None:
    assert settings_page.config_text(home) == "{}\n"


def test_config_text_reads_raw_file(home: Path) -> None:
    llmenv.add_models_root(Path("D:/models"), home)
    text = settings_page.config_text(home)
    assert '"models_roots"' in text
    assert text.endswith("\n")


def test_config_text_unreadable_returns_empty(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llmenv.save_config({"models_roots": []}, home)

    def _denied(_self: Path, *_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_text", _denied)
    assert settings_page.config_text(home) == "{}\n"


def test_parse_config_text_accepts_object() -> None:
    assert settings_page.parse_config_text('{"models_roots": ["D:/models"]}') == {"models_roots": ["D:/models"]}


def test_parse_config_text_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="JSON 解析失败"):
        settings_page.parse_config_text('{"models_roots": }')


def test_parse_config_text_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="顶层必须是 JSON 对象"):
        settings_page.parse_config_text('["D:/models"]')


# ---------------------------------------------------------------------- #
# 客户端环境变量
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("shell", "prefix"),
    [
        ("plain", "OPENAI_BASE_URL="),
        ("powershell", '$env:OPENAI_BASE_URL="'),
        ("bash", 'export OPENAI_BASE_URL="'),
    ],
)
def test_shell_env_lines_prefix(shell: str, prefix: str) -> None:
    lines = settings_page.shell_env_lines(_state(), shell)
    assert len(lines) == 3
    assert lines[0].startswith(prefix)


def test_shell_env_lines_values() -> None:
    assert settings_page.shell_env_lines(_state(), "plain") == [
        "OPENAI_BASE_URL=http://127.0.0.1:8080/v1",
        f"OPENAI_API_KEY={DEFAULT_API_KEY}",
        "OPENAI_MODEL=qwen3-coder-30b-a3b",
    ]


def test_shell_env_lines_rejects_unknown_shell() -> None:
    with pytest.raises(ValueError, match="不支持的输出格式"):
        settings_page.shell_env_lines(_state(), "cmd")


def test_env_text_joins_lines() -> None:
    text = settings_page.env_text(_state(), "bash")
    assert text.splitlines()[0] == 'export OPENAI_BASE_URL="http://127.0.0.1:8080/v1"'
    assert all(line.startswith("export ") for line in text.splitlines())


def _llmconfig_set(key: str, value: str, home: Path) -> None:
    """写入单个权威配置键（测试辅助）。"""
    llmenv.set_option(key, value, home)
