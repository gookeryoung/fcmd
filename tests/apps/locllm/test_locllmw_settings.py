"""locllmw 设置页纯逻辑测试：目录 / 引擎 / 界面三页与写入行为。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from fcmd.apps.locllm import _llmconfig, llmengine
from fcmd.apps.locllm import _locllmw_settings as settings_page
from fcmd.apps.locllm.llmbase import Check, EngineInfo, ServeError


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(target))
    for name in ("FCMD_MODELS", "FCMD_ENGINES", "FCMD_SERVE_DIR", "FCMD_CONFIG"):
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
    assert rows == {"模型目录": (str(_llmconfig.default_models_dir()), "默认")}


def test_directory_rows_marks_config_source(home: Path, tmp_path: Path) -> None:
    models = tmp_path / "models"
    _write_config(home, {"models_dir": str(models)})
    rows = {label: (value, source) for label, value, source in settings_page.directory_rows(home)}
    assert rows == {"模型目录": (str(models), "配置")}


def test_directory_rows_marks_env_source(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_models = tmp_path / "env-models"
    monkeypatch.setenv("FCMD_MODELS", str(env_models))
    rows = {label: (value, source) for label, value, source in settings_page.directory_rows()}
    assert rows == {"模型目录": (str(env_models), "环境变量")}


def test_directory_rows_ignores_fcmd_work(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_WORK", str(tmp_path / "legacy-work"))
    rows = {label: value for label, value, _source in settings_page.directory_rows(home)}
    assert list(rows) == ["模型目录"]


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
    assert rows["程序版本"]
    assert rows["配置文件"] == str(_llmconfig.config_path())


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


def test_migration_note_reports_ignored_work_dir(home: Path, tmp_path: Path) -> None:
    work = tmp_path / "old-work"
    _write_config(home, {"models_dir": str(tmp_path / "models"), "work_dir": str(work)})
    note = settings_page.migration_note(home)
    assert "旧工作目录已忽略" in note
    assert str(work) in note
    assert "work_dir" not in _read(home / "serve" / "config.json")


def test_migration_note_reports_legacy_download_dest(home: Path, tmp_path: Path) -> None:
    dest = tmp_path / "downloads"
    legacy = home / "ui" / "locllmw.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"download": {"dest": str(dest), "workers": 4}}), encoding="utf-8")
    note = settings_page.migration_note(home)
    assert str(dest) in note
    saved = json.loads(legacy.read_text(encoding="utf-8"))
    assert "dest" not in saved["download"]
    assert saved["download"]["workers"] == 4


# ---------------------------------------------------------------------- #
# 写入
# ---------------------------------------------------------------------- #
def test_apply_models_dir_writes_single_value(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "models"
    assert settings_page.apply_path("models_dir", str(target), home) == target
    saved = _read(home / "serve" / "config.json")
    assert saved["models_dir"] == str(target)


def test_apply_work_dir_rejected(home: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知设置项"):
        settings_page.apply_path("work_dir", str(tmp_path / "work"), home)


def test_apply_engines_root_writes_value(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "engines"
    assert settings_page.apply_path("engines_root", str(target), home) == target
    assert _read(home / "serve" / "config.json")["engines_root"] == str(target)


def test_apply_none_restores_defaults(home: Path, tmp_path: Path) -> None:
    _llmconfig.set_models_dir(tmp_path / "models", home)
    _llmconfig.set_option("engines_root", str(tmp_path / "engines"), home)
    assert settings_page.apply_path("models_dir", None, home) == _llmconfig.default_models_dir()
    assert settings_page.apply_path("engines_root", None, home) == home / "engines" / "llama.cpp"


def test_apply_unknown_key_rejected(home: Path) -> None:
    with pytest.raises(ValueError, match="未知设置项"):
        settings_page.apply_path("serve_dir", "D:/serve", home)


# ---------------------------------------------------------------------- #
# 引擎页：列表 / 切换 / 体检 / 安装
# ---------------------------------------------------------------------- #
def test_installed_engines_passes_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path | None] = []
    info = EngineInfo("b1", Path("llama-server.exe"))

    def fake_list(target: Path | None = None) -> list[EngineInfo]:
        calls.append(target)
        return [info]

    monkeypatch.setattr(llmengine, "list_engines", fake_list)
    assert settings_page.installed_engines(home) == [info]
    assert calls == [home]


def test_engine_status_defaults_without_engines(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [])

    def fail(tag: str | None = None, home: Path | None = None) -> EngineInfo:
        raise ServeError("未找到 llama.cpp 引擎")

    monkeypatch.setattr(llmengine, "resolve_engine", fail)
    status = settings_page.engine_status(home)
    assert status.root == home / "engines" / "llama.cpp"
    assert status.installed == 0
    assert status.current.startswith("未找到（未找到 llama.cpp 引擎）")


def test_engine_status_reports_current(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    info = EngineInfo("b2", Path("D:/engines/b2/llama-server.exe"), "current")
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [info])
    monkeypatch.setattr(llmengine, "resolve_engine", lambda tag=None, home=None: info)
    status = settings_page.engine_status(home)
    assert status.installed == 1
    assert status.current == f"b2 · {info.exe}"


def test_select_engine_uses_engine_use(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Path | None]] = []
    info = EngineInfo("b1", Path("x.exe"))

    def fake_use(tag: str, target: Path | None = None) -> EngineInfo:
        calls.append((tag, target))
        return info

    monkeypatch.setattr(llmengine, "use_engine", fake_use)
    assert settings_page.select_engine("b1", home) is info
    assert calls == [("b1", home)]


def test_check_engine_tag_runs_version(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    info = EngineInfo("b1", Path("x.exe"))
    monkeypatch.setattr(llmengine, "resolve_engine", lambda tag=None, home=None: info)
    seen: list[EngineInfo] = []

    def fake_check(engine: EngineInfo, _runner: object = None) -> Check:
        seen.append(engine)
        return Check("推理引擎", "ok", "version ok")

    monkeypatch.setattr(llmengine, "check_engine", fake_check)
    assert settings_page.check_engine_tag("b1", home) == Check("推理引擎", "ok", "version ok")
    assert seen == [info]


def test_install_online_sets_default_when_missing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    info = EngineInfo("b9", Path("x.exe"))
    events: list[tuple[object, ...]] = []
    markers: list[str] = []

    def fake_install(tag: str, **kwargs: object) -> EngineInfo:
        markers.append(f"install:{tag}:{kwargs.get('variant')}:{kwargs.get('home')}")
        progress = kwargs.get("on_progress")
        assert callable(progress)
        progress("x.zip", 5, 10)
        return info

    monkeypatch.setattr(llmengine, "install_engine", fake_install)
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [])
    monkeypatch.setattr(llmengine, "use_engine", lambda tag, _home=None: markers.append(f"use:{tag}") or info)
    assert settings_page.install_online("b9", "cpu", home, on_progress=lambda *args: events.append(args)) is info
    assert markers == [f"install:b9:cpu:{home}", "use:b9"]
    assert events == [("x.zip", 5, 10)]


def test_install_online_keeps_existing_default(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    info = EngineInfo("b9", Path("x.exe"))
    monkeypatch.setattr(llmengine, "install_engine", lambda tag, **_kwargs: info)
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [EngineInfo("b1", Path("y.exe"), "current")])

    def fail(*_args: object) -> EngineInfo:
        raise AssertionError("已有默认引擎时不应切换")

    monkeypatch.setattr(llmengine, "use_engine", fail)
    assert settings_page.install_online("b9", "cuda", home) is info


def test_install_offline_from_zip(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "engine.zip"
    archive.write_bytes(b"x")
    info = EngineInfo("b7", Path("x.exe"))
    seen: list[tuple[str, list[Path] | None, Path | None]] = []

    def fake_install(tag: str, **kwargs: object) -> EngineInfo:
        seen.append((tag, cast("list[Path] | None", kwargs.get("archives")), cast("Path | None", kwargs.get("path"))))
        return info

    monkeypatch.setattr(llmengine, "install_engine", fake_install)
    monkeypatch.setattr(llmengine, "inspect_zips", lambda _paths, deep=False: [])
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [EngineInfo("b1", Path("y.exe"), "current")])
    assert settings_page.install_offline("b7", archive, home) == (info, "b7")
    assert seen == [("b7", [archive], None)]


def test_install_offline_from_directory_prefers_zips(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"
    first.write_bytes(b"x")
    second.write_bytes(b"y")
    info = EngineInfo("b7", Path("x.exe"))
    seen: list[list[Path] | None] = []

    def fake_install(tag: str, **kwargs: object) -> EngineInfo:
        seen.append(cast("list[Path] | None", kwargs.get("archives")))
        return info

    monkeypatch.setattr(llmengine, "install_engine", fake_install)
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [EngineInfo("b1", Path("y.exe"), "current")])
    assert settings_page.install_offline("b7", tmp_path, home) == (info, "b7")
    assert seen == [[first, second]]


def test_install_offline_directory_without_zips_registers_path(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = EngineInfo("b7", Path("x.exe"))
    seen: list[Path | None] = []

    def fake_install(tag: str, **kwargs: object) -> EngineInfo:
        seen.append(cast("Path | None", kwargs.get("path")))
        return info

    monkeypatch.setattr(llmengine, "install_engine", fake_install)
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [EngineInfo("b1", Path("y.exe"), "current")])
    assert settings_page.install_offline("b7", tmp_path, home) == (info, "b7")
    assert seen == [tmp_path]


def test_install_offline_infers_tag(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "engine.zip"
    archive.write_bytes(b"x")
    info = EngineInfo("b42", Path("x.exe"))
    monkeypatch.setattr(
        llmengine,
        "inspect_zips",
        lambda _paths, deep=False: [llmengine.ZipInspection(archive, "engine", True, tag="b42")],
    )
    monkeypatch.setattr(llmengine, "install_engine", lambda tag, **_kwargs: info)
    monkeypatch.setattr(llmengine, "list_engines", lambda _home=None: [EngineInfo("b1", Path("y.exe"), "current")])
    assert settings_page.install_offline("b7", archive, home, infer_tag=True) == (info, "b42")


def test_inspect_offline_expands_directory(tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "engine.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("llama-server.exe", b"x")
    inspections = settings_page.inspect_offline(tmp_path)
    assert [item.path for item in inspections] == [archive]
    assert inspections[0].kind == "engine"


def test_format_inspection_marks_state_and_notes() -> None:
    item = llmengine.ZipInspection(
        Path("cudart.zip"),
        "cudart",
        True,
        tag=None,
        variant="cuda",
        total_size=2048,
        problems=(),
        notes=("CUDA 运行库包，需与引擎包一起安装",),
    )
    text = settings_page.format_inspection(item)
    assert "CUDA 运行库" in text
    assert "不可安装" in text
    assert "需与引擎包一起安装" in text
