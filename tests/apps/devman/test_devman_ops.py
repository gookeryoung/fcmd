"""命令编排：目标解析、加锁、安装 / 卸载 / 切换 / 升级、初始化和诊断。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.devman import _bun, _deno, _env, _go, _jdk, _maintenance, _node, _ops
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import MARKER_SCHEMA, Config, State, active_version, set_active, write_marker
from tests.apps.devman import _fakes

PY_KEY = _fakes.PY_KEY
EMPTY_ENV = {"PATH": "C:\\Windows"}


def _store(ctx: _ops.JobContext) -> _fakes.FakeEnvStore:
    store = ctx.env_store
    assert isinstance(store, _fakes.FakeEnvStore)
    return store


def _ctx(  # noqa: PLR0913 - 测试构造器集中注入替身
    tmp_path: Path,
    *,
    config: Config | None = None,
    fetcher: Any = None,
    runner: Any = None,
    store: Any = None,
    env: dict[str, str] | None = None,
) -> _ops.JobContext:
    return _ops.JobContext(
        layout=Layout(tmp_path / "root"),
        config=config or Config(python_uv=str(tmp_path / "uv-missing.exe")),
        state=State(),
        fetcher=fetcher,
        runner=runner,
        env_store=store if store is not None else _fakes.FakeEnvStore(),
        env=EMPTY_ENV if env is None else env,
    )


def _node_fetcher(version: str, *, old: str = "20.11.1") -> _fakes.FakeFetcher:
    """发布索引 + 构件 + 官方清单。"""
    data, digest = _fakes.make_node_zip(version)
    filename = _node.artifact_name(version, "x64")
    index = _fakes.node_index_json((old, "Iron"), (version, "Iron"))
    return _fakes.FakeFetcher(
        texts={
            _node.RELEASE_INDEX: index,
            _node.sha_url(version): _fakes.shasums_text((filename, digest)),
        },
        payloads={f"https://nodejs.org/dist/v{version}/{filename}": data},
    )


def _node_runner(version: str) -> _fakes.FakeRunner:
    return _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"v{version}\n"))


# ---------------------------------------------------------------------- #
# 上下文与锁
# ---------------------------------------------------------------------- #


def test_new_context_collects_warnings(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "config.json").write_text('{"node": {"mirror": 1}}', encoding="utf-8")
    (root / "state.json").write_text("broken", encoding="utf-8")
    ctx = _ops.new_context(root)
    assert ctx.config.node_mirror == "auto"
    assert len(ctx.warnings) == 2
    assert any("config.node.mirror" in item for item in ctx.warnings)
    assert any("state.json" in item for item in ctx.warnings)


def test_context_lazy_backends(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert ctx.fetch() is ctx.fetch()
    assert ctx.execute() is ctx.execute()


def test_install_lock_roundtrip(tmp_path: Path) -> None:
    layout = _ctx(tmp_path).layout
    with _ops.install_lock(layout):
        assert layout.lock_file.is_file()
    assert not layout.lock_file.exists()


def test_install_lock_busy(tmp_path: Path) -> None:
    layout = _ctx(tmp_path).layout
    layout.root.mkdir(parents=True)
    layout.lock_file.write_text("other", encoding="utf-8")
    with pytest.raises(DevmanError) as info, _ops.install_lock(layout):
        pass
    assert info.value.code == "DEVICE_BUSY"


def test_install_lock_recovers_stale_lock(tmp_path: Path) -> None:
    layout = _ctx(tmp_path).layout
    layout.root.mkdir(parents=True)
    layout.lock_file.write_text("old", encoding="utf-8")
    stale = time.time() - _ops.LOCK_STALE_SECONDS - 60
    os.utime(layout.lock_file, (stale, stale))
    with _ops.install_lock(layout):
        assert layout.lock_file.is_file()
    assert not layout.lock_file.exists()


def test_install_lock_recovers_when_stat_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """锁文件在互斥检查后消失（stat 失败）时按陈旧锁处理。"""
    layout = _ctx(tmp_path).layout
    layout.root.mkdir(parents=True)
    original_open = os.open
    calls: list[int] = []

    def flaky_open(path: Any, flags: int) -> int:
        calls.append(flags)
        if len(calls) == 1:
            raise FileExistsError
        return original_open(path, flags)

    monkeypatch.setattr(_ops.os, "open", flaky_open)
    with _ops.install_lock(layout):
        assert layout.lock_file.is_file()


# ---------------------------------------------------------------------- #
# 目标解析
# ---------------------------------------------------------------------- #


def test_resolve_target_requires_version(tmp_path: Path) -> None:
    with pytest.raises(DevmanError) as info:
        _ops.resolve_target(_ctx(tmp_path), _parse("node"))
    assert info.value.code == "INVALID_ARGUMENT"


def test_resolve_node_exact_and_installed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _fakes.install_fake_node(ctx.layout, "20.12.0")
    assert _ops.resolve_target(ctx, _parse("node@20.11.1")) == ("node", "20.11.1")
    assert _ops.resolve_target(ctx, _parse("node@20")) == ("node", "20.12.0")


def test_resolve_node_remote_channels(tmp_path: Path) -> None:
    fetcher = _fakes.FakeFetcher(
        texts={_node.RELEASE_INDEX: _fakes.node_index_json(("20.11.1", "Iron"), ("21.0.0", False))}
    )
    ctx = _ctx(tmp_path, fetcher=fetcher)
    assert _ops.resolve_target(ctx, _parse("node@latest")) == ("node", "21.0.0")
    assert _ops.resolve_target(ctx, _parse("node@lts")) == ("node", "20.11.1")
    assert _ops.resolve_target(ctx, _parse("node@20"), remote_only=True) == ("node", "20.11.1")


def test_resolve_python_installed_and_remote(tmp_path: Path) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(tmp_path, config=Config(python_uv=str(uv)))
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    assert _ops.resolve_target(ctx, _parse("python@3.12")) == ("python", PY_KEY)

    ctx.runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=_fakes.python_list_json()))
    remote = _ops.resolve_target(ctx, _parse("python@3.12"), remote_only=True)
    assert remote == ("python", PY_KEY)


# ---------------------------------------------------------------------- #
# list
# ---------------------------------------------------------------------- #


def test_list_payload_shape(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    _env.write_shims(ctx.layout)
    payload = _ops.list_payload(ctx, env={"PATH": str(ctx.layout.shim_dir)})
    assert payload["schema"] == "devman/list/1"
    assert payload["tools"]["node"]["active"] == "20.11.1"
    assert payload["tools"]["python"]["installed"] == []
    assert payload["shims"]["on_path"] is True


# ---------------------------------------------------------------------- #
# install
# ---------------------------------------------------------------------- #


def test_install_node_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.install(ctx, "node@20.11.1", dry_run=True)
    assert payload["dry_run"] is True
    assert payload["planned"]["artifact"] == "node-v20.11.1-win-x64.zip"
    assert not ctx.layout.root.exists()
    assert ctx.fetcher is None


def test_install_python_dry_run(tmp_path: Path) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(tmp_path, config=Config(python_uv=str(uv)))
    ctx.runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=_fakes.python_list_json()))
    payload = _ops.install(ctx, "python@3.12", dry_run=True)
    assert payload["planned"]["uv"] == str(uv)
    assert payload["version"] == "3.12.15"
    assert not ctx.layout.root.exists()


def test_install_node_actual(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    payload = _ops.install(ctx, "node@20.12.0")
    assert payload["changed"] is True
    assert payload["version"] == "20.12.0"
    assert (ctx.layout.version_dir("node", "20.12.0") / "node.exe").is_file()
    assert not ctx.layout.lock_file.exists()
    assert payload["user_path"]["changed"] is True
    assert (ctx.layout.shim_dir / "node.cmd").is_file()
    assert (_store(ctx).get("Path") or "").startswith(str(ctx.layout.shim_dir))
    assert ctx.state.path_added == [str(ctx.layout.shim_dir)]


def test_install_node_dry_run_reports_path_plan(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.install(ctx, "node@20.11.1", dry_run=True)
    assert payload["user_path"]["enabled"] is True
    assert payload["user_path"]["already_present"] is False
    assert _store(ctx).get("Path") is None


def test_install_node_no_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    payload = _ops.install(ctx, "node@20.12.0", write_path=False)
    assert payload["user_path"]["enabled"] is False
    assert _store(ctx).get("Path") is None
    assert not (ctx.layout.shim_dir / "node.cmd").exists()
    assert ctx.state.path_added == []


def test_install_blocks_shadowing(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "node.exe").write_bytes(b"MZ")
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"), env={"PATH": str(external)})
    with pytest.raises(DevmanError) as info:
        _ops.install(ctx, "node@20.12.0")
    assert info.value.code == "INVALID_ARGUMENT"
    assert not ctx.layout.version_dir("node", "20.12.0").exists()

    payload = _ops.install(ctx, "node@20.12.0", allow_shadow=True)
    assert payload["user_path"]["changed"] is True


def test_install_repeat_is_path_idempotent(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    _ops.install(ctx, "node@20.12.0")
    before = _store(ctx).get("Path")
    payload = _ops.install(ctx, "node@20.12.0")
    assert payload["changed"] is False
    assert payload["user_path"]["changed"] is False
    assert _store(ctx).get("Path") == before


def test_install_python_actual(tmp_path: Path) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(tmp_path, config=Config(python_uv=str(uv)))
    final = ctx.layout.version_dir("python", PY_KEY)

    def handler(argv: list[str], env: Any) -> Any:
        if argv[1:3] == ["python", "list"]:
            return _fakes.result(stdout=_fakes.python_list_json())
        if argv[1:3] == ["python", "install"]:
            final.mkdir(parents=True, exist_ok=True)
            (final / "python.exe").write_bytes(b"MZ")
            return _fakes.result()
        return _fakes.result(stdout="Python 3.12.15")

    ctx.runner = _fakes.FakeRunner(handler)
    payload = _ops.install(ctx, f"python@{PY_KEY}")
    assert payload["changed"] is True
    assert payload["key"] == PY_KEY


def test_install_node_passes_sha(tmp_path: Path) -> None:
    data, digest = _fakes.make_node_zip("20.12.0")
    filename = _node.artifact_name("20.12.0", "x64")
    fetcher = _fakes.FakeFetcher(payloads={f"https://nodejs.org/dist/v20.12.0/{filename}": data})
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=_node_runner("20.12.0"))
    payload = _ops.install(ctx, "node@20.12.0", sha256=digest)
    assert payload["changed"] is True
    assert fetcher.text_calls == []


# ---------------------------------------------------------------------- #
# uninstall
# ---------------------------------------------------------------------- #


def test_uninstall_missing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(DevmanError) as info:
        _ops.uninstall(ctx, "node@20", yes=True)
    assert info.value.code == "NOT_INSTALLED"

    payload = _ops.uninstall(ctx, "node@20", if_present=True)
    assert payload["changed"] is False
    assert payload["removed"] == []


def test_uninstall_requires_confirmation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    with pytest.raises(DevmanError) as info:
        _ops.uninstall(ctx, "node@20.11.1")
    assert info.value.code == "CONFIRM_REQUIRED"
    assert "--yes" in (info.value.hint or "")
    assert ctx.layout.version_dir("node", "20.11.1").is_dir()


def test_uninstall_dry_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _fakes.install_fake_node(ctx.layout, "20.12.0")
    payload = _ops.uninstall(ctx, "node@20", dry_run=True)
    assert payload["planned"]["targets"][0]["version"] == "20.12.0"
    assert ctx.layout.version_dir("node", "20.12.0").is_dir()


def test_uninstall_removes_highest_and_keeps_others(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _fakes.install_fake_node(ctx.layout, "20.12.0")
    payload = _ops.uninstall(ctx, "node@20", yes=True)
    assert payload["removed"] == [{"name": "20.12.0", "version": "20.12.0"}]
    assert not ctx.layout.version_dir("node", "20.12.0").exists()
    assert ctx.layout.version_dir("node", "20.11.1").is_dir()
    assert "npm 全局包" in payload["note"]


def test_uninstall_all_versions_and_cache(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _fakes.install_fake_node(ctx.layout, "20.12.0")
    ctx.layout.cache_dir.mkdir(parents=True)
    (ctx.layout.cache_dir / "x.zip").write_bytes(b"x")
    payload = _ops.uninstall(ctx, "node@20", yes=True, all_versions=True, purge_cache=True)
    assert len(payload["removed"]) == 2
    assert payload["purged_cache"] is True
    assert not ctx.layout.cache_dir.exists()


def test_uninstall_last_version_removes_recorded_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    ctx.layout.ensure_bootstrap()
    _env.write_shims(ctx.layout)
    _env.install_path(ctx.layout, store=ctx.env_store)
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    payload = _ops.uninstall(ctx, "node@20.11.1", yes=True)
    assert payload["user_path"]["changed"] is True
    assert _store(ctx).get("Path") == ""
    assert ctx.state.path_added == []


def test_uninstall_keeps_path_when_versions_remain(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    _fakes.install_fake_node(ctx.layout, "20.12.0")
    _env.install_path(ctx.layout, store=ctx.env_store)
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    payload = _ops.uninstall(ctx, "node@20", yes=True)
    assert payload["user_path"]["changed"] is False
    assert ctx.state.path_added == [str(ctx.layout.shim_dir)]
    assert (_store(ctx).get("Path") or "").startswith(str(ctx.layout.shim_dir))


def test_uninstall_dry_run_reports_path_removal(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    payload = _ops.uninstall(ctx, "node@20", dry_run=True)
    assert payload["planned"]["remove_path_entries"] == [str(ctx.layout.shim_dir)]
    assert ctx.state.path_added == [str(ctx.layout.shim_dir)]


def test_uninstall_active_requires_force(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    with pytest.raises(DevmanError) as info:
        _ops.uninstall(ctx, "node@20.11.1", yes=True)
    assert info.value.code == "INVALID_ARGUMENT"
    payload = _ops.uninstall(ctx, "node@20.11.1", yes=True, force=True)
    assert payload["cleared_active"] is True
    assert active_version(ctx.layout, "node") is None
    assert ctx.state.active == {}


def test_uninstall_active_dry_run_is_allowed(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    payload = _ops.uninstall(ctx, "node@20.11.1", dry_run=True, force=True)
    assert payload["planned"]["clear_active"] is True


# ---------------------------------------------------------------------- #
# use
# ---------------------------------------------------------------------- #


def test_use_switch_and_repeat(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.use(ctx, "node@20.11.1")
    assert payload["changed"] is True
    assert payload["active"] == "20.11.1"
    assert active_version(ctx.layout, "node") == "20.11.1"
    assert ctx.state.active["node"] == "20.11.1"
    assert (ctx.layout.state_file).is_file()

    again = _ops.use(ctx, "node@20.11.1")
    assert again["changed"] is False
    assert again["previous"] == "20.11.1"


def test_use_dry_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.use(ctx, "node@20.11.1", dry_run=True)
    assert payload["dry_run"] is True
    assert payload["changed"] is True
    assert active_version(ctx.layout, "node") is None


def test_use_clear(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    ctx.state.active["node"] = "20.11.1"
    payload = _ops.use(ctx, "node", clear=True)
    assert payload["changed"] is True
    assert payload["active"] is None
    assert active_version(ctx.layout, "node") is None
    assert ctx.state.active == {}


def test_use_clear_dry_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.use(ctx, "node", clear=True, dry_run=True)
    assert payload["dry_run"] is True
    assert payload["active"] is None


def test_use_python_hint(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    payload = _ops.use(ctx, f"python@{PY_KEY}")
    assert payload["hint"].startswith("uv venv --python")
    assert payload["path"].endswith("python.exe")


def test_use_missing_and_incomplete(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(DevmanError) as info:
        _ops.use(ctx, "node@20")
    assert info.value.code == "VERSION_NOT_FOUND"
    _fakes.install_fake_node(ctx.layout, "20.11.1", complete=False)
    with pytest.raises(DevmanError) as info:
        _ops.use(ctx, "node@20")
    assert info.value.code == "INCOMPLETE_INSTALL"


# ---------------------------------------------------------------------- #
# upgrade
# ---------------------------------------------------------------------- #
def test_upgrade_requires_installed_version(tmp_path: Path) -> None:
    with pytest.raises(DevmanError) as info:
        _ops.upgrade(_ctx(tmp_path), "node")
    assert info.value.code == "NOT_INSTALLED"


def test_upgrade_already_latest(tmp_path: Path) -> None:
    fetcher = _fakes.FakeFetcher(texts={_node.RELEASE_INDEX: _fakes.node_index_json(("20.11.1", "Iron"))})
    ctx = _ctx(tmp_path, fetcher=fetcher)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.upgrade(ctx, "node")
    assert payload["changed"] is False
    assert payload["target"] == "20.11.1"


def test_upgrade_dry_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"))
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.upgrade(ctx, "node", dry_run=True)
    assert payload["planned"]["target"] == "20.12.0"
    assert not ctx.layout.version_dir("node", "20.12.0").exists()


def test_upgrade_installs_and_switches(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.upgrade(ctx, "node")
    assert payload["changed"] is True
    assert payload["current"] == "20.11.1"
    assert payload["target"] == "20.12.0"
    assert active_version(ctx.layout, "node") == "20.12.0"


def test_upgrade_purge_requires_yes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    with pytest.raises(DevmanError) as info:
        _ops.upgrade(ctx, "node", purge_old=True)
    assert info.value.code == "CONFIRM_REQUIRED"


def test_upgrade_purge_old(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.upgrade(ctx, "node", purge_old=True, yes=True)
    assert payload["purged"]["removed"][0]["version"] == "20.11.1"
    assert not ctx.layout.version_dir("node", "20.11.1").exists()


def test_upgrade_to_explicit_version(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    payload = _ops.upgrade(ctx, "node", to="20.12.0")
    assert payload["target"] == "20.12.0"


# ---------------------------------------------------------------------- #
# init / uninit
# ---------------------------------------------------------------------- #
def test_init_dry_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.init(ctx, dry_run=True, env=EMPTY_ENV)
    assert payload["dry_run"] is True
    assert payload["planned"]["add_to_user_path"] is True
    assert not ctx.layout.shim_dir.exists()


def test_init_no_path_dry_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.init(ctx, write_path=False, dry_run=True, env=EMPTY_ENV)
    assert payload["planned"]["add_to_user_path"] is False


def test_init_writes_shims(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.init(ctx)
    assert payload["changed"] is True
    assert set(payload["shims_written"]) == set(_env.shim_scripts())
    again = _ops.init(ctx)
    assert again["changed"] is False
    assert len(again["shims_unchanged"]) == len(_env.shim_scripts())


def test_init_path_updates_store_and_state(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    store = _fakes.FakeEnvStore({"Path": "C:\\Windows"})
    payload = _ops.init(ctx, store=store, env=EMPTY_ENV)
    assert payload["user_path"]["changed"] is True
    assert store.get("Path") == f"{ctx.layout.shim_dir};C:\\Windows"
    assert ctx.state.path_added == [str(ctx.layout.shim_dir)]
    assert ctx.state.path_previous_user_path == "C:\\Windows"
    assert ctx.layout.state_file.is_file()


def test_init_path_keeps_preexisting_entry_unrecorded(tmp_path: Path) -> None:
    """PATH 里已有该目录时不算 devman 写入，不记录、不改变格式。"""
    ctx = _ctx(tmp_path)
    layout = ctx.layout
    original = f"C:\\Windows;C:\\Tools;{layout.shim_dir}"
    store = _fakes.FakeEnvStore({"Path": original})
    payload = _ops.init(ctx, store=store, env=EMPTY_ENV)
    assert payload["user_path"]["changed"] is False
    assert store.get("Path") == original
    assert ctx.state.path_added == []


def test_init_path_blocks_shadowing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "node.exe").write_bytes(b"MZ")
    env = {"PATH": str(external)}
    with pytest.raises(DevmanError) as info:
        _ops.init(ctx, store=_fakes.FakeEnvStore(), env=env)
    assert info.value.code == "INVALID_ARGUMENT"
    assert "--allow-shadow" in (info.value.hint or "")

    payload = _ops.init(ctx, allow_shadow=True, store=_fakes.FakeEnvStore(), env=env)
    assert payload["changed"] is True


def test_init_path_dry_run_reports_shadowing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "python.exe").write_bytes(b"MZ")
    payload = _ops.init(ctx, dry_run=True, env={"PATH": str(external)})
    assert "python" in payload["planned"]["shadowed"]


def test_uninit_removes_shims(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ops.init(ctx)
    payload = _ops.uninit(ctx)
    assert payload["changed"] is True
    assert not ctx.layout.shim_dir.exists()


def test_uninit_dry_run(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ops.init(ctx)
    payload = _ops.uninit(ctx, dry_run=True)
    assert payload["dry_run"] is True
    assert ctx.layout.shim_dir.is_dir()
    assert payload["planned"]["remove_path_entries"] == [str(ctx.layout.shim_dir)]


def test_uninit_removes_path_entry(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ops.init(ctx)
    store = _fakes.FakeEnvStore({"Path": f"{ctx.layout.shim_dir};C:\\Windows"})
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    payload = _ops.uninit(ctx, store=store)
    assert payload["user_path"]["changed"] is True
    assert store.get("Path") == "C:\\Windows"
    assert ctx.state.path_added == []


def test_uninit_keeps_unrecorded_entry(tmp_path: Path) -> None:
    """没有 state 记录的条目（用户手工加的）不删。"""
    ctx = _ctx(tmp_path)
    original = f"{ctx.layout.shim_dir};C:\\Windows"
    store = _fakes.FakeEnvStore({"Path": original})
    payload = _ops.uninit(ctx, store=store)
    assert payload["user_path"]["changed"] is False
    assert store.get("Path") == original


def test_uninit_restores_exact_original_text(tmp_path: Path) -> None:
    """写入再撤销应逐字回到原值（含 %VAR% 与空段）。"""
    ctx = _ctx(tmp_path)
    original = "%USERPROFILE%\\bin;;C:\\Windows;"
    store = _fakes.FakeEnvStore({"Path": original})
    _ops.init(ctx, store=store, env=EMPTY_ENV)
    assert store.get("Path") != original
    _ops.uninit(ctx, store=store)
    assert store.get("Path") == original


def test_uninit_no_path_keeps_entry(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ops.init(ctx)
    store = _fakes.FakeEnvStore({"Path": f"{ctx.layout.shim_dir};C:\\Windows"})
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    payload = _ops.uninit(ctx, write_path=False, store=store)
    assert payload["user_path"]["changed"] is False
    assert store.get("Path") == f"{ctx.layout.shim_dir};C:\\Windows"


# ---------------------------------------------------------------------- #
# env / doctor
# ---------------------------------------------------------------------- #
def test_env_payload_without_active(tmp_path: Path) -> None:
    payload = _ops.env_payload(_ctx(tmp_path))
    assert "没有活动版本" in payload["snippet"]
    assert payload["dirs"] == []


def test_env_payload_with_active(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    assert "20.11.1" in _ops.env_payload(ctx, shell="cmd")["snippet"]


def test_doctor_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 平台检查按真实平台给结论；本用例验证 Windows 正常态，显式固定平台。
    monkeypatch.setattr(_ops.sys, "platform", "win32")
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(tmp_path, config=Config(python_uv=str(uv)))
    ctx.runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout="uv 0.13.1"))
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    _ops.init(ctx)
    payload = _ops.doctor_payload(ctx, env={"PATH": str(ctx.layout.shim_dir)})
    assert payload["ok"] is True
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["shims"] == "ok"
    assert statuses["active_node"] == "ok"
    assert statuses["uv"] == "ok"
    assert statuses["platform"] == "ok"


def test_doctor_reports_missing_active_and_shims(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    set_active(ctx.layout, "node", "20.11.1")
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV)
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert payload["ok"] is False
    assert statuses["active_node"] == "fail"
    assert statuses["shims"] == "warn"
    assert statuses["root_writable"] == "ok"


def test_doctor_reports_stale_shims_and_leftovers(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ops.init(ctx)
    (ctx.layout.shim_dir / "node.cmd").write_text("stale", encoding="utf-8")
    staging = ctx.layout.node_root / ".staging-20.11.1-deadbeef"
    staging.mkdir(parents=True)
    ctx.layout.cache_dir.mkdir(parents=True)
    (ctx.layout.cache_dir / "x.zip.part").write_bytes(b"x")
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV)
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["shims"] == "warn"
    assert statuses["leftovers"] == "warn"
    assert "node.cmd" in next(item["detail"] for item in payload["checks"] if item["name"] == "shims")


def test_doctor_reports_uv_unavailable_and_markers(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.doctor_payload(ctx, env={"PATH": "", "VIRTUAL_ENV": "C:/venv"})
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["uv"] == "warn"
    assert statuses["env_virtual_env"] == "warn"
    assert statuses["shim_on_path"] == "warn"


def test_doctor_root_missing_parent_writable(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV)
    detail = next(item["detail"] for item in payload["checks"] if item["name"] == "root_writable")
    assert "尚不存在" in detail


def test_doctor_root_probe_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    original = Path.exists

    def broken(self: Path) -> bool:
        if self == ctx.layout.root:
            raise OSError("denied")
        return original(self)

    monkeypatch.setattr(Path, "exists", broken)
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV)
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["root_writable"] == "fail"
    assert payload["ok"] is False


def test_doctor_ignores_uv_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    alias = ctx.layout.python_root / "cpython-3.12-windows-x86_64-none"
    alias.mkdir(parents=True)
    (ctx.layout.python_root / ".temp").mkdir()
    original = Path.is_symlink

    def is_symlink(self: Path) -> bool:
        return self == alias or original(self)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV)
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["leftovers"] == "ok"


def test_doctor_non_windows_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(_ops.sys, "platform", "linux")
    payload = _ops.doctor_payload(ctx, env=EMPTY_ENV)
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["platform"] == "warn"


def test_uninstall_remove_version_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")

    def broken(self: Path, target: Path) -> Path:
        raise OSError("locked")

    monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises(DevmanError) as info:
        _ops.uninstall(ctx, "node@20.11.1", yes=True)
    assert "无法移走" in str(info.value)


def test_uninstall_python_delegates_to_uv(tmp_path: Path) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result())
    ctx = _ctx(tmp_path, config=Config(python_uv=str(uv)), runner=runner)
    existing = _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    payload = _ops.uninstall(ctx, f"python@{PY_KEY}", yes=True)
    assert payload["removed"] == [{"name": PY_KEY, "version": "3.12.15"}]
    assert not existing.exists()
    argv = runner.calls[0]
    assert argv[1:3] == ["python", "uninstall"]
    assert "--install-dir" in argv and str(ctx.layout.python_root) in argv


def test_uninstall_python_without_uv_still_removes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    existing = _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    payload = _ops.uninstall(ctx, f"python@{PY_KEY}", yes=True)
    assert payload["changed"] is True
    assert not existing.exists()


def test_uninstall_reports_leftovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    monkeypatch.setattr(_ops.shutil, "rmtree", lambda *args, **kwargs: None)
    payload = _ops.uninstall(ctx, "node@20.11.1", yes=True)
    assert payload["leftovers"]
    assert ".trash-" in payload["leftovers"][0]


def test_upgrade_python_switches_to_new_key(tmp_path: Path) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(tmp_path, config=Config(python_uv=str(uv)))
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    new_key = "cpython-3.13.14-windows-x86_64-none"
    final = ctx.layout.version_dir("python", new_key)
    rows = json.loads(_fakes.python_list_json())
    rows.append(
        {
            "key": new_key,
            "version": "3.13.14",
            "implementation": "cpython",
            "os": "windows",
            "arch": "x86_64",
            "variant": "default",
            "path": None,
            "url": None,
        }
    )

    def handler(argv: list[str], env: Any) -> Any:
        if argv[1:3] == ["python", "list"]:
            return _fakes.result(stdout=json.dumps(rows))
        if argv[1:3] == ["python", "install"]:
            final.mkdir(parents=True, exist_ok=True)
            (final / "python.exe").write_bytes(b"MZ")
            return _fakes.result()
        return _fakes.result(stdout="Python 3.13.14")

    ctx.runner = _fakes.FakeRunner(handler)
    payload = _ops.upgrade(ctx, "python", to="3.13")
    assert payload["current"] == "3.12.15"
    assert payload["target"] == "3.13.14"
    assert active_version(ctx.layout, "python") == new_key


def _parse(text: str) -> Any:
    from fcmd.apps.devman._toolchain import parse_spec

    return parse_spec(text)


# ---------------------------------------------------------------------- #
# PATH 记录与 shim 清理的边界
# ---------------------------------------------------------------------- #
def test_install_does_not_duplicate_recorded_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("20.12.0"), runner=_node_runner("20.12.0"))
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    payload = _ops.install(ctx, "node@20.12.0")
    assert payload["user_path"]["changed"] is True
    assert ctx.state.path_added == [str(ctx.layout.shim_dir)]


def test_uninit_with_unmatched_recorded_entry_leaves_store(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    store = _fakes.FakeEnvStore({"Path": "C:\\Windows"})
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    payload = _ops.uninit(ctx, store=store)
    assert payload["user_path"]["changed"] is False
    assert store.get("Path") == "C:\\Windows"
    assert store.writes == []
    assert ctx.state.path_added == []


def test_uninit_skips_non_file_shim_entries(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ops.init(ctx)
    nested = ctx.layout.shim_dir / "nested"
    nested.mkdir()
    payload = _ops.uninit(ctx)
    assert "nested" not in payload["removed_shims"]
    assert nested.is_dir()


# ---------------------------------------------------------------------- #
# 其余运行时的目标解析
# ---------------------------------------------------------------------- #
def test_resolve_node_prefix_without_local_match_uses_remote(tmp_path: Path) -> None:
    fetcher = _fakes.FakeFetcher(texts={_node.RELEASE_INDEX: _fakes.node_index_json(("20.11.1", "Iron"))})
    ctx = _ctx(tmp_path, fetcher=fetcher)
    assert _ops.resolve_target(ctx, _parse("node@20")) == ("node", "20.11.1")


def test_resolve_go_exact_installed_and_index(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert _ops.resolve_target(ctx, _parse("go@1.23.4")) == ("go", "1.23.4")

    _fakes.install_fake_go(ctx.layout, "1.22.9")
    assert _ops.resolve_target(ctx, _parse("go@1.22")) == ("go", "1.22.9")

    _data, digest = _fakes.make_go_zip("1.23.4")
    remote = _ctx(
        tmp_path / "remote",
        fetcher=_fakes.FakeFetcher(texts={_go.INDEX: _fakes.go_index_json("1.23.4", digest)}),
    )
    assert _ops.resolve_target(remote, _parse("go@1.23")) == ("go", "1.23.4")
    assert _ops.resolve_target(remote, _parse("go@latest")) == ("go", "1.23.4")


def test_resolve_deno_and_bun_latest(tmp_path: Path) -> None:
    deno_ctx = _ctx(tmp_path / "deno", fetcher=_fakes.FakeFetcher(texts={_deno.LATEST_URL: "v2.1.4\n"}))
    assert _ops.resolve_target(deno_ctx, _parse("deno@latest")) == ("deno", "2.1.4")

    bun_ctx = _ctx(
        tmp_path / "bun",
        fetcher=_fakes.FakeFetcher(texts={_bun.LATEST_API: json.dumps({"tag_name": "bun-v1.1.38"})}),
    )
    assert _ops.resolve_target(bun_ctx, _parse("bun@latest")) == ("bun", "1.1.38")


def test_resolve_jdk_lts(tmp_path: Path) -> None:
    _data, digest = _fakes.make_jdk_zip("21.0.5+11")
    arch = _jdk.detect_arch()
    url = f"{_jdk.API}/assets/latest/21/hotspot?architecture={arch}&image_type=jdk&os=windows&vendor=eclipse"
    fetcher = _fakes.FakeFetcher(
        texts={
            _jdk.AVAILABLE: json.dumps({"most_recent_lts": 21, "most_recent_feature_release": 23}),
            url: _fakes.jdk_assets_json(
                "21.0.5+11",
                "OpenJDK21U-jdk_x64_windows_hotspot_21.0.5_11.zip",
                "https://example.invalid/jdk.zip",
                digest,
            ),
        }
    )
    ctx = _ctx(tmp_path, fetcher=fetcher)
    assert _ops.resolve_target(ctx, _parse("jdk@lts")) == ("jdk", "21.0.5+11")


# ---------------------------------------------------------------------- #
# versions --remote 的其余运行时
# ---------------------------------------------------------------------- #
def test_versions_remote_go_marks_installed(tmp_path: Path) -> None:
    _data, digest = _fakes.make_go_zip("1.23.4")
    ctx = _ctx(tmp_path, fetcher=_fakes.FakeFetcher(texts={_go.INDEX: _fakes.go_index_json("1.23.4", digest)}))
    _fakes.install_fake_go(ctx.layout, "1.23.4")
    payload = _ops.versions_payload(ctx, "go", remote=True)
    assert payload["source"] == _go.INDEX
    assert payload["versions"] == [{"version": "1.23.4", "installed": True, "active": False}]


def test_versions_remote_deno_and_bun(tmp_path: Path) -> None:
    deno_ctx = _ctx(
        tmp_path / "deno",
        fetcher=_fakes.FakeFetcher(texts={_deno.LATEST_URL: "v2.1.4\n"}),
    )
    deno_payload = _ops.versions_payload(deno_ctx, "deno", remote=True)
    assert deno_payload["source"] == _deno.LATEST_URL
    assert deno_payload["versions"] == [{"version": "2.1.4", "installed": False, "active": False}]

    bun_ctx = _ctx(
        tmp_path / "bun",
        fetcher=_fakes.FakeFetcher(texts={_bun.LATEST_API: json.dumps({"tag_name": "bun-v1.1.38"})}),
    )
    bun_payload = _ops.versions_payload(bun_ctx, "bun", remote=True)
    assert bun_payload["source"] == _bun.LATEST_API
    assert bun_payload["versions"] == [{"version": "1.1.38", "installed": False, "active": False}]


def test_versions_remote_jdk_lists_releases(tmp_path: Path) -> None:
    arch = _jdk.detect_arch()
    ctx = _ctx(
        tmp_path,
        fetcher=_fakes.FakeFetcher(
            texts={_jdk.RELEASES.format(arch=arch): json.dumps(["jdk-21.0.5+11", "temurin-17.0.13+11"])}
        ),
    )
    payload = _ops.versions_payload(ctx, "jdk", remote=True)
    assert payload["source"] == _jdk.RELEASES.format(arch=arch)
    assert payload["versions"] == [{"version": "21.0.5+11", "installed": False, "active": False}]


# ---------------------------------------------------------------------- #
# 卸载 python / 升级默认版本线 / init 无 PATH
# ---------------------------------------------------------------------- #
def test_uninstall_python_uv_deletes_source_early(tmp_path: Path) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    final = tmp_path / "root" / "python" / PY_KEY

    def handler(argv: list[str], env: Any) -> Any:
        if argv[1:3] == ["python", "uninstall"]:
            shutil.rmtree(final)
            return _fakes.result()
        return _fakes.result()

    ctx = _ctx(tmp_path, config=Config(python_uv=str(uv)), runner=_fakes.FakeRunner(handler))
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    payload = _ops.uninstall(ctx, f"python@{PY_KEY}", yes=True)
    assert payload["leftovers"] == []
    assert not final.exists()


def test_upgrade_deno_defaults_to_latest(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_fakes.FakeFetcher(texts={_deno.LATEST_URL: "v2.1.5\n"}))
    directory = ctx.layout.version_dir("deno", "2.1.4")
    directory.mkdir(parents=True)
    (directory / "deno.exe").write_bytes(b"MZ")
    write_marker(
        directory,
        {
            "schema": MARKER_SCHEMA,
            "tool": "deno",
            "version": "2.1.4",
            "sha256": "0" * 64,
            "verify": "official-shasums",
            "installed_at": "2026-01-01",
        },
    )
    payload = _ops.upgrade(ctx, "deno", dry_run=True)
    assert payload["planned"]["target"] == "2.1.5"


def test_upgrade_go_defaults_to_same_minor(tmp_path: Path) -> None:
    _data, digest = _fakes.make_go_zip("1.22.9")
    rows = json.loads(_fakes.go_index_json("1.22.9", digest)) + json.loads(_fakes.go_index_json("1.23.4", digest))
    ctx = _ctx(tmp_path, fetcher=_fakes.FakeFetcher(texts={_go.INDEX: json.dumps(rows)}))
    _fakes.install_fake_go(ctx.layout, "1.22.1")
    payload = _ops.upgrade(ctx, "go", dry_run=True)
    assert payload["planned"]["target"] == "1.22.9"


def test_init_no_path_writes_shims_only(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    payload = _ops.init(ctx, write_path=False, env=EMPTY_ENV)
    assert payload["user_path"] == {"enabled": False, "changed": False}
    assert set(payload["shims_written"]) == set(_env.shim_scripts())
    assert ctx.state.path_added == []


# ---------------------------------------------------------------------- #
# doctor 代理配置 / export / restore
# ---------------------------------------------------------------------- #
def test_doctor_reports_proxy_configuration(tmp_path: Path) -> None:
    cases = (
        ("", "ok", "显式禁用代理"),
        ("http://127.0.0.1:7890", "ok", "显式代理 http://127.0.0.1:7890"),
        ("socks5://127.0.0.1:1080", "warn", "显式代理 socks5://127.0.0.1:1080"),
    )
    for index, (proxy, status, text) in enumerate(cases):
        ctx = _ctx(tmp_path / str(index), config=Config(python_uv=str(tmp_path / "uv-missing.exe"), proxy=proxy))
        payload = _ops.doctor_payload(ctx, env=EMPTY_ENV)
        network = next(item for item in payload["checks"] if item["name"] == "network")
        assert network["status"] == status
        assert text in network["detail"]
        if status == "warn":
            assert "SOCKS" in (network["hint"] or "")


def test_export_rejects_missing_output_parent(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(DevmanError) as info:
        _ops.export_payload(ctx, output=str(tmp_path / "missing" / "devman.lock.json"))
    assert info.value.code == "INVALID_ARGUMENT"
    assert info.value.field == "output"
    assert "输出目录不存在" in info.value.message


def test_restore_installs_and_switches_versions(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fetcher=_node_fetcher("18.20.0"), runner=_node_runner("18.20.0"))
    _fakes.install_fake_node(ctx.layout, "20.11.1")
    set_active(ctx.layout, "node", "20.11.1")
    lock = {
        "schema": _maintenance.LOCK_SCHEMA,
        "tools": {"node": {"active": "18.20.0", "versions": [{"version": "18.20.0"}, {"version": "20.11.1"}]}},
    }
    path = tmp_path / "devman.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")

    payload = _ops.restore_payload(ctx, str(path), yes=True, write_path=False)
    assert payload["installed"] == ["node@18.20.0"]
    assert payload["used"] == ["node@18.20.0"]
    assert active_version(ctx.layout, "node") == "18.20.0"
    assert (ctx.layout.version_dir("node", "18.20.0") / "node.exe").is_file()
