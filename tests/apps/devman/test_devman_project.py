"""项目级版本绑定：``devman.toml`` 与 ``.node-version`` / ``.python-version``。

覆盖解析/冲突优先级、只读快照、``use --project`` 的写入与清除，以及
``env`` / ``list`` / ``doctor`` 的接入；不联网、不碰真实注册表。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.devman import _env, _ops, _project
from fcmd.apps.devman import devman as devman_module
from fcmd.apps.devman._errors import NOT_INSTALLED, DevmanError
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import Config, State, set_active
from fcmd.core.toolkit import run_tool
from tests.apps.devman import _fakes

PY_KEY = _fakes.PY_KEY
EMPTY_ENV = {"PATH": "C:\\Windows"}


def _ctx(tmp_path: Path, *, env: dict[str, str] | None = None) -> _ops.JobContext:
    return _ops.JobContext(
        layout=Layout(tmp_path / "root"),
        config=Config(python_uv=str(tmp_path / "uv-missing.exe")),
        state=State(),
        fetcher=_fakes.FakeFetcher(),
        runner=_fakes.FakeRunner(),
        env_store=_fakes.FakeEnvStore(),
        env=EMPTY_ENV if env is None else env,
    )


# ---------------------------------------------------------------------- #
# 文件解析
# ---------------------------------------------------------------------- #
def test_read_project_file_parses_versions(tmp_path: Path) -> None:
    path = tmp_path / "devman.toml"
    path.write_text('[versions]\nnode = "v20"\npython = "3.12"\n', encoding="utf-8")
    pins, warnings = _project.read_project_file(path)
    assert warnings == []
    assert pins["node"].raw == "20"
    assert pins["python"].raw == "3.12"
    assert pins["node"].kind == "devman.toml"


def test_read_project_file_skips_bad_fields_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "devman.toml"
    path.write_text('[versions]\nnode = "??"\npython = 312\n', encoding="utf-8")
    pins, warnings = _project.read_project_file(path)
    assert pins == {}
    assert any("node" in item for item in warnings)
    assert any("python" in item for item in warnings)


def test_read_project_file_tolerates_missing_table_and_broken_toml(tmp_path: Path) -> None:
    empty = tmp_path / "empty.toml"
    empty.write_text('title = "demo"\n', encoding="utf-8")
    assert _project.read_project_file(empty) == ({}, [])

    broken = tmp_path / "broken.toml"
    broken.write_text("[versions\nnode = 20", encoding="utf-8")
    pins, warnings = _project.read_project_file(broken)
    assert pins == {}
    assert warnings and "TOML" in warnings[0]

    missing = tmp_path / "missing.toml"
    pins, warnings = _project.read_project_file(missing)
    assert pins == {}
    assert "无法读取" in warnings[0]


def test_read_version_file_accepts_v_prefix_and_skips_comments(tmp_path: Path) -> None:
    path = tmp_path / ".node-version"
    path.write_text("# 注释\n\nv20.11.1\n", encoding="utf-8")
    pin, warnings = _project.read_version_file("node", path)
    assert warnings == []
    assert pin is not None
    assert (pin.raw, pin.kind, pin.source) == ("20.11.1", ".node-version", str(path))


def test_read_version_file_reports_invalid_first_value(tmp_path: Path) -> None:
    path = tmp_path / ".python-version"
    path.write_text("not-a-version\n", encoding="utf-8")
    pin, warnings = _project.read_version_file("python", path)
    assert pin is None
    assert warnings and "不合法" in warnings[0]


def test_read_directory_prefers_devman_toml(tmp_path: Path) -> None:
    (tmp_path / "devman.toml").write_text('[versions]\nnode = "22"\n', encoding="utf-8")
    (tmp_path / ".node-version").write_text("20.11.1\n", encoding="utf-8")
    (tmp_path / ".python-version").write_text("3.12\n", encoding="utf-8")
    pins, warnings = _project.read_directory(tmp_path)
    assert warnings == []
    assert pins["node"].raw == "22"
    assert pins["node"].kind == "devman.toml"
    assert pins["python"].kind == ".python-version"


def test_discover_nearest_pin_wins_per_tool(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "packages" / "app"
    nested.mkdir(parents=True)
    (root / "devman.toml").write_text('[versions]\nnode = "20"\n', encoding="utf-8")
    (nested / ".node-version").write_text("22.1.0\n", encoding="utf-8")
    pins, _warnings = _project.discover(start=nested)
    assert pins["node"].raw == "22.1.0"
    assert pins["node"].source == str(nested / ".node-version")


def test_discover_stops_at_boundary(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / ".node-version").write_text("20\n", encoding="utf-8")
    pins, _warnings = _project.discover(start=inner, stop=inner)
    assert pins == {}


def test_write_and_remove_pin(tmp_path: Path) -> None:
    path = _project.write_pin(tmp_path, "node", "20")
    assert path.read_text(encoding="utf-8") == "20\n"
    assert _project.remove_pin(tmp_path, "node") is True
    assert _project.remove_pin(tmp_path, "node") is False
    assert not path.exists()


# ---------------------------------------------------------------------- #
# 只读快照与解析
# ---------------------------------------------------------------------- #
def test_project_snapshot_reports_installed_and_missing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    (tmp_path / "devman.toml").write_text('[versions]\nnode = "20"\npython = "3.13"\n', encoding="utf-8")
    snapshot = _ops.project_snapshot(ctx, start=tmp_path)
    assert snapshot["schema"] == "devman-project/1"
    assert snapshot["pins"]["node"]["installed"] is True
    assert snapshot["pins"]["node"]["resolved"] == "20.11.1"
    assert snapshot["pins"]["python"]["installed"] is False
    assert snapshot["files"] == [str(tmp_path / "devman.toml")]


def test_project_versions_returns_names_or_raises_not_installed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    (tmp_path / ".node-version").write_text("20\n", encoding="utf-8")
    assert _ops.project_versions(ctx, start=tmp_path) == {"node": "20.11.1"}

    (tmp_path / ".node-version").write_text("18\n", encoding="utf-8")
    with pytest.raises(DevmanError) as caught:
        _ops.project_versions(ctx, start=tmp_path)
    assert caught.value.code == NOT_INSTALLED
    assert "install node@18" in (caught.value.hint or "")


# ---------------------------------------------------------------------- #
# use --project
# ---------------------------------------------------------------------- #
def test_use_project_writes_pin_file_without_touching_active(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    payload = _ops.use(ctx, "node@20", project=True, cwd=project_dir)
    assert payload["project"]["file"] == str(project_dir / ".node-version")
    assert payload["project"]["raw"] == "20"
    assert payload["project"]["resolved"] == "20.11.1"
    assert payload["project"]["changed"] is True
    assert payload["active"] == "20.11.1"
    assert (project_dir / ".node-version").read_text(encoding="utf-8") == "20\n"

    again = _ops.use(ctx, "node@20", project=True, cwd=project_dir)
    assert again["project"]["changed"] is False


def test_use_project_dry_run_does_not_write(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.use(ctx, "node@20", project=True, dry_run=True, cwd=tmp_path)
    assert payload["dry_run"] is True
    assert payload["project"]["changed"] is True
    assert not (tmp_path / ".node-version").exists()


def test_use_project_clear_removes_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    (tmp_path / ".node-version").write_text("20\n", encoding="utf-8")
    payload = _ops.use(ctx, "node", clear=True, project=True, cwd=tmp_path)
    assert payload["project"]["changed"] is True
    assert payload["project"]["raw"] is None
    assert not (tmp_path / ".node-version").exists()

    again = _ops.use(ctx, "node", clear=True, project=True, cwd=tmp_path)
    assert again["project"]["changed"] is False


def test_use_project_warns_when_same_dir_toml_shadows(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    (tmp_path / "devman.toml").write_text('[versions]\nnode = "20"\n', encoding="utf-8")
    payload = _ops.use(ctx, "node@20", project=True, cwd=tmp_path)
    assert payload["project"]["changed"] is True
    assert any("不会生效" in item for item in payload["warnings"])


def test_cli_use_project_reports_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    monkeypatch.setattr(devman_module._ops, "new_context", lambda root=None: ctx)
    monkeypatch.chdir(tmp_path)
    code = run_tool("devman", ["use", "node@20", "--project"])
    captured = capsys.readouterr()
    assert code == 0
    assert "项目绑定已写入" in captured.out
    assert (tmp_path / ".node-version").read_text(encoding="utf-8") == "20\n"


# ---------------------------------------------------------------------- #
# env / list / doctor 接入
# ---------------------------------------------------------------------- #
def test_env_and_exec_follow_project_pins(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    set_active(ctx.layout, "python", PY_KEY)
    (tmp_path / ".node-version").write_text("20\n", encoding="utf-8")

    payload = _ops.env_payload(ctx, shell="pwsh", cwd=tmp_path)
    assert payload["active"] == {
        "node": "20.11.1",
        "python": PY_KEY,
        "go": None,
        "deno": None,
        "bun": None,
        "jdk": None,
    }
    assert payload["project"]["pins"]["node"]["resolved"] == "20.11.1"
    assert str(ctx.layout.version_dir("node", "20.11.1")) in payload["dirs"][0]

    tagged = _env.child_env(ctx.layout, {"PATH": "C:\\Windows"}, pins={"node": "20.11.1"})
    assert str(ctx.layout.version_dir("node", "20.11.1")) in tagged["PATH"]


def test_list_payload_includes_project_section(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    (tmp_path / "devman.toml").write_text('[versions]\nnode = "20"\n', encoding="utf-8")
    payload = _ops.list_payload(ctx, cwd=tmp_path)
    assert payload["project"]["pins"]["node"]["raw"] == "20"
    json.dumps(payload)  # 必须可 JSON 序列化


def test_doctor_project_check_warns_for_missing_version(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (tmp_path / ".node-version").write_text("18\n", encoding="utf-8")
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV, cwd=tmp_path)
    project = next(item for item in payload["checks"] if item["name"] == "project")
    assert project["status"] == "warn"
    assert "18" in project["detail"]


def test_doctor_project_check_ok_when_pinned_version_installed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    (tmp_path / "devman.toml").write_text('[versions]\nnode = "20"\n', encoding="utf-8")
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV, cwd=tmp_path)
    project = next(item for item in payload["checks"] if item["name"] == "project")
    assert project["status"] == "ok"
    assert "20.11.1" in project["detail"]
