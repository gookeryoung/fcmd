"""llmenvw 窗口版测试：纯逻辑（目录布局 / 模型目录来源 / 配置文本 / 环境变量格式 / 设置）与 GUI 入口。

窗口本身不创建 Tk（CI 无显示环境），只覆盖可脱离界面验证的部分。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.llm import llmenv, llmenvw
from fcmd.tools.llm.llmbase import DEFAULT_API_KEY, ServeError, ServeState


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
    for name in ("FCMD_MODELS", "FCMD_SERVE_DIR", "FCMD_CONFIG", "FCMD_ENGINES"):
        monkeypatch.delenv(name, raising=False)
    return target


# ---------------------------------------------------------------------- #
# 注册
# ---------------------------------------------------------------------- #
def test_tool_registered_without_subcommand() -> None:
    assert set(_TOOL_REGISTRY["llmenvw"]) == {None}


# ---------------------------------------------------------------------- #
# 目录布局
# ---------------------------------------------------------------------- #
def test_layout_rows_follows_home(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "models"
    llmenv.add_models_root(root, home)
    llmenv.save_state(_state(), home)
    rows = {label: (value, source) for label, value, source in llmenvw.layout_rows(home)}
    assert rows["FCMD_HOME"] == (str(home), "环境变量")
    assert rows["服务状态目录"] == (str(home / "serve"), "默认")
    assert rows["配置文件"] == (str(home / "serve" / "config.json"), "默认")
    assert rows["引擎根目录"] == (str(home / "engines" / "llama.cpp"), "默认")
    assert rows["模型目录"][0] == str(root)
    assert rows["已登记服务"] == ("1", "自动发现")


def test_layout_rows_joins_multiple_roots(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    llmenv.add_models_root(first, home)
    llmenv.add_models_root(second, home)
    assert {label: value for label, value, _source in llmenvw.layout_rows(home)}["模型目录"] == (f"{first}, {second}")


def test_layout_rows_marks_window_override(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve = tmp_path / "serve-override"
    monkeypatch.setenv("FCMD_SERVE_DIR", str(serve))
    settings = llmenvw.EnvSettings(serve_dir=str(serve))
    rows = {label: (value, source) for label, value, source in llmenvw.layout_rows(None, settings)}
    assert rows["服务状态目录"] == (str(serve), "窗口覆盖")


def test_layout_rows_marks_process_env(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engines = tmp_path / "engines"
    monkeypatch.setenv("FCMD_ENGINES", str(engines))
    rows = {label: (value, source) for label, value, source in llmenvw.layout_rows()}
    assert rows["引擎根目录"] == (str(engines), "环境变量")


# ---------------------------------------------------------------------- #
# 目录覆盖
# ---------------------------------------------------------------------- #
def test_apply_overrides_sets_only_non_empty(tmp_path: Path) -> None:
    environ: dict[str, str] = {"FCMD_ENGINES": "keep"}
    settings = llmenvw.EnvSettings(fcmd_home=str(tmp_path / "h"), config_file=str(tmp_path / "c.json"))
    llmenvw.apply_overrides(settings, environ)
    assert environ["FCMD_HOME"] == str(tmp_path / "h")
    assert environ["FCMD_CONFIG"] == str(tmp_path / "c.json")
    assert environ["FCMD_ENGINES"] == "keep"
    assert "FCMD_SERVE_DIR" not in environ


def test_apply_overrides_ignores_empty_values() -> None:
    environ: dict[str, str] = {}
    llmenvw.apply_overrides(llmenvw.EnvSettings(), environ)
    assert environ == {}


# ---------------------------------------------------------------------- #
# 模型目录来源与编辑
# ---------------------------------------------------------------------- #
def test_model_root_rows_default_source(home: Path) -> None:
    rows = llmenvw.model_root_rows(home)
    assert len(rows) == 1
    assert rows[0][0] == str(llmenv.default_models_root())
    assert rows[0][2] == "默认"


def test_model_root_rows_configured_source(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "models"
    target.mkdir()
    llmenv.add_models_root(target, home)
    assert llmenvw.model_root_rows(home) == [(str(target), "存在", "已登记")]


def test_model_root_rows_marks_missing_directory(home: Path, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    llmenv.add_models_root(missing, home)
    assert llmenvw.model_root_rows(home) == [(str(missing), "不存在", "已登记")]


def test_model_root_rows_env_override(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_root = tmp_path / "env-models"
    env_root.mkdir()
    monkeypatch.setenv("FCMD_MODELS", str(env_root))
    assert llmenvw.model_root_rows(home) == [(str(env_root), "存在", "环境变量 FCMD_MODELS")]


def test_replace_models_root_keeps_order_and_other_keys(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    llmenv.add_models_root(first, home)
    llmenv.add_models_root(second, home)
    config = llmenv.load_config(home)
    config["extra"] = {"keep": True}
    llmenv.save_config(config, home)

    moved = tmp_path / "moved"
    updated = llmenvw.replace_models_root(first, moved, home)
    assert updated == [moved, second]
    saved = llmenv.load_config(home)
    assert saved["models_roots"] == [str(moved), str(second)]
    assert saved["extra"] == {"keep": True}


def test_replace_models_root_deduplicates_target(home: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    llmenv.add_models_root(first, home)
    llmenv.add_models_root(second, home)
    assert llmenvw.replace_models_root(first, second, home) == [second]


def test_replace_models_root_requires_registered_source(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="配置中没有模型目录"):
        llmenvw.replace_models_root(tmp_path / "missing", tmp_path / "new", home)


# ---------------------------------------------------------------------- #
# 配置文件文本
# ---------------------------------------------------------------------- #
def test_config_text_missing_returns_empty_object(home: Path) -> None:
    assert llmenvw.config_text(home) == "{}\n"


def test_config_text_reads_raw_file(home: Path) -> None:
    llmenv.add_models_root(Path("D:/models"), home)
    text = llmenvw.config_text(home)
    assert '"models_roots"' in text
    assert text.endswith("\n")


def test_config_text_unreadable_returns_empty(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llmenv.save_config({"models_roots": []}, home)

    def _denied(_self: Path, *_args: object, **_kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_text", _denied)
    assert llmenvw.config_text(home) == "{}\n"


def test_parse_config_text_accepts_object() -> None:
    assert llmenvw.parse_config_text('{"models_roots": ["D:/models"]}') == {"models_roots": ["D:/models"]}


def test_parse_config_text_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="JSON 解析失败"):
        llmenvw.parse_config_text('{"models_roots": }')


def test_parse_config_text_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="顶层必须是 JSON 对象"):
        llmenvw.parse_config_text('["D:/models"]')


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
    lines = llmenvw.shell_env_lines(_state(), shell)
    assert len(lines) == 3
    assert lines[0].startswith(prefix)


def test_shell_env_lines_values() -> None:
    assert llmenvw.shell_env_lines(_state(), "plain") == [
        "OPENAI_BASE_URL=http://127.0.0.1:8080/v1",
        f"OPENAI_API_KEY={DEFAULT_API_KEY}",
        "OPENAI_MODEL=qwen3-coder-30b-a3b",
    ]


def test_shell_env_lines_rejects_unknown_shell() -> None:
    with pytest.raises(ValueError, match="不支持的输出格式"):
        llmenvw.shell_env_lines(_state(), "cmd")


def test_env_text_joins_lines() -> None:
    text = llmenvw.env_text(_state(), "bash")
    assert text.splitlines()[0] == 'export OPENAI_BASE_URL="http://127.0.0.1:8080/v1"'
    assert all(line.startswith("export ") for line in text.splitlines())


# ---------------------------------------------------------------------- #
# GUI 入口
# ---------------------------------------------------------------------- #
class TestEnvwGuiEntry:
    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_window() -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(llmenvw, "EnvWindow", fake_window)
        llmenvw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(llmenvw, "EnvWindow", _Window)
        llmenvw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(llmenvw, "run_gui", lambda: calls.append(True))
        llmenvw.launch_cmd()
        assert calls == [True]


# ---------------------------------------------------------------------- #
# 窗口设置持久化
# ---------------------------------------------------------------------- #
class TestEnvwSettings:
    """llmenvw 窗口位置/大小与目录覆盖持久化（纯逻辑，不创建 Tk）。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert llmenvw.config_path() == tmp_path / "ui" / "llmenvw.json"

    def test_load_missing_file_returns_default(self, tmp_path: Path) -> None:
        assert llmenvw.load_settings(tmp_path / "none.json") == llmenvw.EnvSettings()

    def test_load_ignores_invalid_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ui.json"
        path.write_text(
            '{"geometry": "bad", "serve_dir": 123, "engines_root": "  ", "fcmd_home": " D:/h "}',
            encoding="utf-8",
        )
        settings = llmenvw.load_settings(path)
        assert settings.geometry is None
        assert settings.serve_dir is None
        assert settings.engines_root is None
        assert settings.fcmd_home == "D:/h"

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "ui.json"
        settings = llmenvw.EnvSettings(
            geometry="960x660+10+20",
            fcmd_home="D:/home",
            serve_dir="D:/serve",
            config_file="D:/serve/config.json",
            engines_root="D:/engines",
        )
        llmenvw.save_settings(settings, path)
        assert llmenvw.load_settings(path) == settings

    def test_save_ignores_write_error(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        llmenvw.save_settings(llmenvw.EnvSettings(geometry="960x660"), blocker / "ui.json")

    def test_resolve_geometry_keeps_visible_position(self) -> None:
        assert llmenvw.resolve_geometry("960x660+100+80", 1920, 1080) == "960x660+100+80"

    def test_resolve_geometry_pulls_back_offscreen(self) -> None:
        assert llmenvw.resolve_geometry("960x660+5000+5000", 1920, 1080) == "960x660+960+420"

    def test_resolve_geometry_enforces_minimum_size(self) -> None:
        assert llmenvw.resolve_geometry("200x100+0+0", 1920, 1080) == "820x600+0+0"

    def test_resolve_geometry_falls_back_to_default_size(self) -> None:
        assert llmenvw.resolve_geometry(None, 1920, 1080) == "980x700"
        assert llmenvw.resolve_geometry("bad", 1920, 1080) == "980x700"

    def test_resolve_geometry_clamps_default_to_small_screen(self) -> None:
        assert llmenvw.resolve_geometry(None, 800, 600) == "800x600"
