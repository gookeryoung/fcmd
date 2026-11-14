"""可运维性：磁盘占用、完整性校验与修复、锁文件导出与恢复。

全部使用临时目录与假环境；不联网、不读写真实注册表。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.devman import _env, _maintenance, _ops
from fcmd.apps.devman import devman as devman_module
from fcmd.apps.devman._errors import CONFIRM_REQUIRED, DevmanError
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import Config, State, set_active
from fcmd.core.toolkit import run_tool
from tests.apps.devman import _fakes

PY_KEY = _fakes.PY_KEY
EMPTY_ENV = {"PATH": "C:\\Windows"}


def _ctx(tmp_path: Path) -> _ops.JobContext:
    return _ops.JobContext(
        layout=Layout(tmp_path / "root"),
        config=Config(python_uv=str(tmp_path / "uv-missing.exe")),
        state=State(),
        fetcher=_fakes.FakeFetcher(),
        runner=_fakes.FakeRunner(),
        env_store=_fakes.FakeEnvStore(),
        env=EMPTY_ENV,
    )


# ---------------------------------------------------------------------- #
# size
# ---------------------------------------------------------------------- #
def test_path_size_counts_files_and_tolerates_missing(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.bin").write_bytes(b"12345")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "two.bin").write_bytes(b"123")
    assert _maintenance.path_size(tmp_path / "a") == (8, 2)
    assert _maintenance.path_size(tmp_path / "missing") == (0, 0)


def test_size_groups_versions_cache_and_shims(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    _fakes.install_fake_node(layout, "20.11.1")
    layout.cache_dir.mkdir(parents=True)
    (layout.cache_dir / "node.zip").write_bytes(b"0123456789")
    _env.write_shims(layout)

    payload = _maintenance.size(layout)
    assert payload["tools"]["node"]["bytes"] > 0
    assert payload["tools"]["node"]["versions"][0]["version"] == "20.11.1"
    assert payload["tools"]["python"]["versions"] == []
    assert payload["cache"]["files"] == 1
    assert payload["cache"]["bytes"] == 10
    assert payload["shims"]["bytes"] > 0
    assert payload["total_bytes"] > payload["cache"]["bytes"]

    only_python = _maintenance.size(layout, tool="python")
    assert set(only_python["tools"]) == {"python"}


# ---------------------------------------------------------------------- #
# verify
# ---------------------------------------------------------------------- #
def test_verify_clean_install_passes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    report = _maintenance.verify(ctx.layout)
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["checked"] == {"node": 1, "python": 0, "go": 0, "deno": 0, "bun": 0, "jdk": 0}


def test_verify_flags_missing_marker_exe_and_broken_active(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    _fakes.install_fake_node(layout, "20.11.1")
    (layout.version_dir("node", "20.11.1") / "node.exe").unlink()
    _fakes.install_fake_node(layout, "18.20.0", complete=False)
    set_active(layout, "node", "22.0.0")

    kinds = {issue["kind"] for issue in _maintenance.verify(layout)["issues"]}
    assert {"exe-missing", "marker-missing", "active-broken"} <= kinds


def test_verify_flags_stale_shim_and_leftover(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    _fakes.install_fake_node(layout, "20.11.1")
    _env.write_shims(layout)
    shim = layout.shim_dir / "node.cmd"
    shim.write_text("@echo off\r\nrem 旧内容\r\n", encoding="utf-8")
    staging = layout.node_root / ".staging-20.11.1-deadbeef"
    staging.mkdir(parents=True)

    issues = _maintenance.verify(layout)["issues"]
    assert any(issue["kind"] == "shim-stale" and issue["name"] == "node.cmd" for issue in issues)
    assert any(issue["kind"] == "leftover" and ".staging-" in issue["name"] for issue in issues)


def test_verify_ignores_shims_when_not_initialized(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.layout.shim_dir.mkdir(parents=True)
    assert _maintenance.verify(ctx.layout)["ok"] is True


# ---------------------------------------------------------------------- #
# repair
# ---------------------------------------------------------------------- #
def test_repair_dry_run_reports_without_touching_files(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    _fakes.install_fake_node(layout, "20.11.1")
    _env.write_shims(layout)
    stale = layout.shim_dir / "npm.cmd"
    stale.write_text("broken", encoding="utf-8")
    staging = layout.node_root / ".staging-20.11.1-cafe"
    staging.mkdir(parents=True)

    payload = _maintenance.repair(layout, dry_run=True)
    assert payload["dry_run"] is True
    assert {action["kind"] for action in payload["actions"]} == {"shim", "leftover"}
    assert stale.read_text(encoding="utf-8") == "broken"
    assert staging.exists()


def test_repair_fixes_shims_and_leftovers(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    _fakes.install_fake_node(layout, "20.11.1")
    _env.write_shims(layout)
    (layout.shim_dir / "npm.cmd").write_text("broken", encoding="utf-8")
    staging = layout.node_root / ".trash-20.11.1-1"
    staging.mkdir(parents=True)

    payload = _maintenance.repair(layout)
    assert payload["ok"] is True
    assert payload["done"]
    assert not staging.exists()
    assert (layout.shim_dir / "npm.cmd").read_bytes() == _env.shim_scripts()["npm.cmd"].encode("utf-8")


def test_repair_clears_broken_active(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    set_active(layout, "node", "22.0.0")
    payload = _maintenance.repair(layout)
    assert payload["ok"] is True
    assert not layout.active_file("node").exists()


def test_repair_purges_incomplete_only_with_flag(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    broken = _fakes.install_fake_node(layout, "18.20.0", complete=False)

    keep = _maintenance.repair(layout)
    assert broken.exists()
    assert "marker-missing" in {issue["kind"] for issue in keep["remaining"]}

    purged = _maintenance.repair(layout, purge_incomplete=True)
    assert not broken.exists()
    assert purged["ok"] is True


def test_repair_payload_requires_confirmation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(DevmanError) as caught:
        _ops.repair_payload(ctx)
    assert caught.value.code == CONFIRM_REQUIRED
    planned = _ops.repair_payload(ctx, dry_run=True)
    assert planned["dry_run"] is True


# ---------------------------------------------------------------------- #
# export / lock
# ---------------------------------------------------------------------- #
def test_export_lock_contains_versions_and_active(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    set_active(ctx.layout, "node", "20.11.1")

    lock = _maintenance.export_lock(ctx.layout)
    assert lock["schema"] == _maintenance.LOCK_SCHEMA
    assert lock["tools"]["node"]["active"] == "20.11.1"
    node = lock["tools"]["node"]["versions"][0]
    assert node["sha256"] == "0" * 64
    assert node["verify"] == "official-shasums"
    json.dumps(lock)


def test_export_payload_writes_file_and_refuses_overwrite(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    target = tmp_path / "devman.lock.json"

    payload = _ops.export_payload(ctx, output=str(target))
    assert payload["written"] is True
    assert json.loads(target.read_text(encoding="utf-8"))["schema"] == _maintenance.LOCK_SCHEMA

    with pytest.raises(DevmanError) as caught:
        _ops.export_payload(ctx, output=str(target))
    assert caught.value.code == "INVALID_ARGUMENT"

    assert _ops.export_payload(ctx, output=str(target), force=True, dry_run=True)["written"] is False
    assert _ops.export_payload(ctx, force=True)["written"] is False


def test_load_lock_rejects_bad_input(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(DevmanError) as caught:
        _maintenance.load_lock(missing)
    assert caught.value.code == "INVALID_ARGUMENT"

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(DevmanError) as caught:
        _maintenance.load_lock(bad)
    assert caught.value.code == "INVALID_ARGUMENT"

    bad_bodies: tuple[dict[str, Any], ...] = (
        {"schema": "other", "tools": {}},
        {"schema": _maintenance.LOCK_SCHEMA},
        {"schema": _maintenance.LOCK_SCHEMA, "tools": {"unknown": {}}},
        {"schema": _maintenance.LOCK_SCHEMA, "tools": {"node": {"versions": [{"name": "x"}]}}},
    )
    for body in bad_bodies:
        bad.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(DevmanError):
            _maintenance.load_lock(bad)


def test_lock_plan_detects_missing_installs_and_active(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    lock = {
        "schema": _maintenance.LOCK_SCHEMA,
        "tools": {
            "node": {
                "active": "20.11.1",
                "versions": [{"version": "20.11.1"}, {"version": "18.20.0"}],
            }
        },
    }
    plan = _maintenance.lock_plan(ctx.layout, lock)
    assert [item["spec"] for item in plan["installs"]] == ["node@18.20.0"]
    assert plan["installs"][0]["force"] is False
    assert [item["spec"] for item in plan["use"]] == ["node@20.11.1"]
    assert plan["skipped"] == [{"tool": "node", "version": "20.11.1", "reason": "已安装"}]


def test_lock_plan_marks_force_for_incomplete(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "18.20.0", complete=False)
    lock = {
        "schema": _maintenance.LOCK_SCHEMA,
        "tools": {"node": {"active": None, "versions": [{"version": "18.20.0"}]}},
    }
    plan = _maintenance.lock_plan(ctx.layout, lock)
    assert plan["installs"][0]["force"] is True


def test_restore_dry_run_and_confirmation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    lock_path = tmp_path / "devman.lock.json"
    lock = _maintenance.export_lock(ctx.layout)
    lock["tools"]["node"]["versions"].append({"version": "18.20.0"})
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(DevmanError) as caught:
        _ops.restore_payload(ctx, str(lock_path))
    assert caught.value.code == CONFIRM_REQUIRED

    plan = _ops.restore_payload(ctx, str(lock_path), dry_run=True)
    assert plan["changed"] is True
    assert [item["spec"] for item in plan["planned"]["installs"]] == ["node@18.20.0"]
    assert plan["installed"] == []


def test_restore_without_changes_is_noop(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    lock_path = tmp_path / "devman.lock.json"
    lock_path.write_text(json.dumps(_maintenance.export_lock(ctx.layout)), encoding="utf-8")

    payload = _ops.restore_payload(ctx, str(lock_path), yes=True)
    assert payload["changed"] is False
    assert payload["installed"] == []
    assert payload["used"] == []


# ---------------------------------------------------------------------- #
# CLI 接线
# ---------------------------------------------------------------------- #
def _install_ctx(monkeypatch: pytest.MonkeyPatch, ctx: _ops.JobContext) -> None:
    monkeypatch.setattr(devman_module._ops, "new_context", lambda root=None: ctx)


def test_cli_size_and_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _install_ctx(monkeypatch, ctx)

    assert run_tool("devman", ["size"]) == 0
    assert "node:" in capsys.readouterr().out

    assert run_tool("devman", ["verify"]) == 0
    assert "完整性检查通过" in capsys.readouterr().out

    (ctx.layout.version_dir("node", "20.11.1") / "node.exe").unlink()
    assert run_tool("devman", ["verify"]) == 5
    assert "exe-missing" in capsys.readouterr().out


def test_cli_repair_requires_yes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    (ctx.layout.node_root / ".staging-x").mkdir(parents=True)
    _install_ctx(monkeypatch, ctx)

    assert run_tool("devman", ["repair"]) == 2
    captured = capsys.readouterr()
    assert "repair 会修改" in (captured.out + captured.err)

    assert run_tool("devman", ["repair", "--dry-run", "--json"]) == 5
    assert (ctx.layout.node_root / ".staging-x").exists()


def test_cli_export_and_restore_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _install_ctx(monkeypatch, ctx)
    target = tmp_path / "devman.lock.json"

    assert run_tool("devman", ["export", "--output", str(target)]) == 0
    assert target.is_file()
    capsys.readouterr()

    assert run_tool("devman", ["export", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["lock"]["schema"] == _maintenance.LOCK_SCHEMA

    assert run_tool("devman", ["restore", str(target), "--dry-run"]) == 0
    assert "恢复计划" in capsys.readouterr().out
