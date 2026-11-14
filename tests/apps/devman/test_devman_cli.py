"""devman CLI：子命令注册、人读 / 机读输出与退出码。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.devman import _env, _go, _node, _ops
from fcmd.apps.devman import devman as devman_module
from fcmd.apps.devman._errors import invalid_argument
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import Config, State, set_active
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands, run_tool
from tests.apps.devman import _fakes

PY_KEY = _fakes.PY_KEY
EMPTY_ENV = {"PATH": "C:\\Windows"}

_SUBCOMMANDS = {
    "doctor",
    "env",
    "exec",
    "export",
    "init",
    "install",
    "list",
    "repair",
    "restore",
    "size",
    "uninit",
    "uninstall",
    "upgrade",
    "use",
    "verify",
    "versions",
}


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
    warnings: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> _ops.JobContext:
    ctx = _ops.JobContext(
        layout=Layout(tmp_path / "root"),
        config=config or Config(python_uv=str(tmp_path / "uv-missing.exe")),
        state=State(),
        fetcher=fetcher,
        runner=runner,
        env_store=_fakes.FakeEnvStore(),
        env=EMPTY_ENV if env is None else env,
    )
    if warnings:
        ctx.warnings.extend(warnings)
    return ctx


def _use(monkeypatch: pytest.MonkeyPatch, ctx: _ops.JobContext) -> None:
    monkeypatch.setattr(devman_module._ops, "new_context", lambda root=None: ctx)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认写 PATH 后，任何走到真实上下文的命令都不得触碰 HKCU。"""
    monkeypatch.setattr(_env, "default_env_store", _fakes.FakeEnvStore)
    monkeypatch.setenv("PATH", "C:\\Windows")


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = run_tool("devman", argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _envelope(out: str) -> dict[str, Any]:
    payload = json.loads(out)
    assert payload["schema"] == "fcmd-result/1"
    assert payload["tool"] == "devman"
    return payload


def _install_node(ctx: _ops.JobContext, version: str = "20.11.1") -> None:
    _fakes.install_fake_node(ctx.layout, version)


def _node_setup(version: str = "20.11.1") -> tuple[_fakes.FakeFetcher, _fakes.FakeRunner]:
    data, digest = _fakes.make_node_zip(version)
    filename = _node.artifact_name(version, "x64")
    fetcher = _fakes.FakeFetcher(
        texts={
            _node.RELEASE_INDEX: _fakes.node_index_json(("20.11.1", "Iron"), (version, "Iron")),
            _node.sha_url(version): _fakes.shasums_text((filename, digest)),
        },
        payloads={f"https://nodejs.org/dist/v{version}/{filename}": data},
    )
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=f"v{version}\n"))
    return fetcher, runner


# ---------------------------------------------------------------------- #
# 注册
# ---------------------------------------------------------------------- #


def test_registers_expected_subcommands() -> None:
    assert set(_TOOL_REGISTRY["devman"]) == _SUBCOMMANDS
    assert set(list_subcommands("devman")) == _SUBCOMMANDS


# ---------------------------------------------------------------------- #
# list
# ---------------------------------------------------------------------- #


def test_list_human(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path, warnings=["配置有误"])
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _env.write_shims(ctx.layout)
    _use(monkeypatch, ctx)
    code, out, err = _run(capsys, ["list"])
    assert code == 0
    assert "root:" in out
    assert "* 20.11.1" in out
    assert "python:" in out and "(未安装)" in out
    assert "shim:" in out
    assert "warn: 配置有误" in err


def test_list_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["list", "--json"])
    assert code == 0
    payload = _envelope(out)
    data = payload["data"]
    assert payload["subcommand"] == "list"
    assert data["schema"] == "devman/list/1"
    assert data["tools"]["node"]["installed"][0]["version"] == "20.11.1"


def test_list_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _use(monkeypatch, ctx)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise invalid_argument("坏根目录", field="root")

    monkeypatch.setattr(devman_module._ops, "list_payload", boom)
    code, out, err = _run(capsys, ["list"])
    assert code == 3
    assert "坏根目录" in out + err


# ---------------------------------------------------------------------- #
# versions
# ---------------------------------------------------------------------- #


def test_versions_installed_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["versions", "node"])
    assert code == 0
    assert "20.11.1  (已安装" in out


def test_versions_remote_node_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher = _fakes.FakeFetcher(
        texts={_node.RELEASE_INDEX: _fakes.node_index_json(("20.11.1", "Iron"), ("21.0.0", False))}
    )
    ctx = _ctx(tmp_path, fetcher=fetcher)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["versions", "node", "--remote"])
    assert code == 0
    assert "20.11.1  (已安装, LTS Iron)" in out
    assert "21.0.0" in out
    assert "来源: https://nodejs.org/dist/index.json" in out


def test_versions_remote_python_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(
        tmp_path,
        config=Config(python_uv=str(uv)),
        runner=_fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=_fakes.python_list_json())),
    )
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["versions", "python", "--remote", "--json"])
    assert code == 0
    data = _envelope(out)["data"]
    assert data["source"] == "uv python list --all-versions"
    versions = [item["version"] for item in data["versions"]]
    assert versions == ["3.12.15", "3.13.14"]


def test_versions_invalid_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, _out, err = _run(capsys, ["versions", "ruby", "--json"])
    assert code == 3
    payload = json.loads(err)
    assert payload["code"] == "INVALID_ARGUMENT"
    assert payload["kind"] == "input"


# ---------------------------------------------------------------------- #
# install
# ---------------------------------------------------------------------- #


def test_install_dry_run_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, _err = _run(capsys, ["install", "node@20.11.1", "--dry-run"])
    assert code == 0
    assert "构件: node-v20.11.1-win-x64.zip" in out
    assert "校验清单" in out


def test_install_actual_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "node@20.12.0", "--json"])
    assert code == 0
    data = _envelope(out)["data"]
    assert data["schema"] == "devman/install/1"
    assert data["changed"] is True
    assert (ctx.layout.version_dir("node", "20.12.0") / "node.exe").is_file()


def test_install_invalid_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, err = _run(capsys, ["install", "node@../evil"])
    assert code == 3
    assert "版本规格不合法" in out + err


def test_install_default_writes_user_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "node@20.12.0", "--json"])
    assert code == 0
    data = _envelope(out)["data"]
    assert data["user_path"]["enabled"] is True
    assert data["user_path"]["changed"] is True
    assert (ctx.layout.shim_dir / "node.cmd").is_file()
    assert (_store(ctx).values.get("Path") or "").startswith(str(ctx.layout.shim_dir))


def test_install_no_path_skips_integration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "node@20.12.0", "--no-path", "--json"])
    assert code == 0
    data = _envelope(out)["data"]
    assert data["user_path"]["enabled"] is False
    assert data["user_path"]["changed"] is False
    assert _store(ctx).values.get("Path") is None
    assert not (ctx.layout.shim_dir / "node.cmd").exists()


def test_install_shadow_blocked_without_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "node.exe").write_bytes(b"MZ")
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner, env={"PATH": str(external)})
    _use(monkeypatch, ctx)
    code, out, err = _run(capsys, ["install", "node@20.12.0"])
    assert code == 3
    assert "--allow-shadow" in out + err
    assert not fetcher.download_calls


def test_install_python_dry_run_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(
        tmp_path,
        config=Config(python_uv=str(uv)),
        runner=_fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=_fakes.python_list_json())),
    )
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "python@3.12", "--dry-run"])
    assert code == 0
    assert "解释器: cpython-3.12.15-windows-x86_64-none" in out
    assert "安装目录:" in out


def test_install_from_local_zip_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, digest = _fakes.make_node_zip("20.11.1")
    artifact = tmp_path / "node-v20.11.1-win-x64.zip"
    artifact.write_bytes(data)
    artifact.with_name(artifact.name + ".sha256").write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout="v20.11.1\n"))
    ctx = _ctx(tmp_path, runner=runner)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "node@20.11.1", "--from", str(artifact), "--no-path", "--json"])
    assert code == 0
    data_out = _envelope(out)["data"]
    assert data_out["changed"] is True
    assert data_out["actions"][0]["action"] == "import"
    assert (ctx.layout.version_dir("node", "20.11.1") / "node.exe").is_file()
    assert not list(ctx.layout.cache_dir.glob("*.part"))


def test_install_from_local_zip_dry_run_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, _digest = _fakes.make_node_zip("20.11.1")
    artifact = tmp_path / "node-v20.11.1-win-x64.zip"
    artifact.write_bytes(data)
    _use(monkeypatch, _ctx(tmp_path))
    code, out, _err = _run(capsys, ["install", "node@20.11.1", "--from", str(artifact), "--dry-run"])
    assert code == 0
    assert f"本地来源: {artifact}" in out
    assert "校验: --sha256 或同目录校验文件" in out


def test_install_from_local_rejects_non_exact_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, _digest = _fakes.make_node_zip("20.11.1")
    artifact = tmp_path / "node.zip"
    artifact.write_bytes(data)
    _use(monkeypatch, _ctx(tmp_path))
    code, out, err = _run(capsys, ["install", "node@20", "--from", str(artifact)])
    assert code == 3
    assert "精确版本号" in out + err


def test_install_from_local_rejects_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = tmp_path / "python.zip"
    artifact.write_bytes(b"x")
    _use(monkeypatch, _ctx(tmp_path))
    code, out, err = _run(capsys, ["install", "python@3.12.15", "--from", str(artifact)])
    assert code == 3
    assert "--from 仅支持 node" in out + err


def test_install_go_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data, digest = _fakes.make_go_zip("1.23.4")
    fetcher = _fakes.FakeFetcher(
        texts={_go.INDEX: _fakes.go_index_json("1.23.4", digest)},
        payloads={f"{_go.DIST}/go1.23.4.windows-amd64.zip": data},
    )
    runner = _fakes.FakeRunner(lambda argv, env: _fakes.result(stdout="go version go1.23.4 windows/amd64\n"))
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "go@1.23", "--no-path", "--json"])
    assert code == 0
    payload = _envelope(out)["data"]
    assert payload["tool"] == "go"
    assert payload["version"] == "1.23.4"
    assert payload["changed"] is True
    assert (ctx.layout.version_dir("go", "1.23.4") / "bin" / "go.exe").is_file()


def test_install_go_dry_run_does_not_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _data, digest = _fakes.make_go_zip("1.23.4")
    fetcher = _fakes.FakeFetcher(texts={_go.INDEX: _fakes.go_index_json("1.23.4", digest)})
    ctx = _ctx(tmp_path, fetcher=fetcher)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "go@latest", "--dry-run", "--json"])
    assert code == 0
    payload = _envelope(out)["data"]
    assert payload["dry_run"] is True
    assert payload["planned"]["artifact"] == "go1.23.4.windows-amd64.zip"
    assert fetcher.download_calls == []


# ---------------------------------------------------------------------- #
# uninstall
# ---------------------------------------------------------------------- #


def test_uninstall_requires_yes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, err = _run(capsys, ["uninstall", "node@20.11.1"])
    assert code == 2
    assert "--yes" in out + err


def test_uninstall_dry_run_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["uninstall", "node@20", "--dry-run"])
    assert code == 0
    assert "计划卸载" in out
    assert "20.11.1" in out


def test_uninstall_human_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["uninstall", "node@20", "--yes"])
    assert code == 0
    assert "已卸载 node 20.11.1" in out
    assert "npm 全局包" in out


def test_uninstall_missing_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, _out, err = _run(capsys, ["uninstall", "node@20", "--yes", "--json"])
    assert code == 4
    assert json.loads(err)["code"] == "NOT_INSTALLED"


# ---------------------------------------------------------------------- #
# use
# ---------------------------------------------------------------------- #


def test_use_human_switch_and_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["use", "node@20"])
    assert code == 0
    assert "已切换: node 20.11.1" in out
    code, out, _err = _run(capsys, ["use", "node", "--clear"])
    assert code == 0
    assert "活动版本已清空" in out


def test_use_json_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["use", "node@20", "--dry-run", "--json"])
    assert code == 0
    data = _envelope(out)["data"]
    assert data["dry_run"] is True
    assert data["active"] == "20.11.1"


def test_use_missing_version_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, _out, err = _run(capsys, ["use", "node@20", "--json"])
    assert code == 4
    assert json.loads(err)["kind"] == "not_found"


# ---------------------------------------------------------------------- #
# upgrade
# ---------------------------------------------------------------------- #


def test_upgrade_dry_run_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["upgrade", "node", "--dry-run", "--purge-old"])
    assert code == 0
    assert "计划升级 20.11.1 → 20.12.0" in out
    assert "升级后卸载 20.11.1" in out


def test_upgrade_already_latest_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(
        tmp_path,
        fetcher=_fakes.FakeFetcher(texts={_node.RELEASE_INDEX: _fakes.node_index_json(("20.11.1", "Iron"))}),
    )
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["upgrade", "node"])
    assert code == 0
    assert "已是最新" in out


def test_upgrade_success_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["upgrade", "node", "--purge-old", "--yes"])
    assert code == 0
    assert "已升级 node 20.11.1 → 20.12.0" in out
    assert "旧版本已卸载" in out


# ---------------------------------------------------------------------- #
# init / uninit
# ---------------------------------------------------------------------- #


def test_init_dry_run_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["init", "--dry-run"])
    assert code == 0
    assert "shim 目录:" in out
    assert "将生成: node.cmd" in out
    assert "用户 PATH: 将前置" in out
    assert not ctx.layout.shim_dir.exists()


def test_init_human_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["init"])
    assert code == 0
    assert "已生成 shim" in out
    assert "用户 PATH: 已前置" in out
    code, out, _err = _run(capsys, ["init"])
    assert code == 0
    assert "shim 已是最新" in out
    assert "用户 PATH: 无需修改" in out


def test_init_shadow_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "node.exe").write_bytes(b"MZ")
    monkeypatch.setenv("PATH", str(external))
    _use(monkeypatch, _ctx(tmp_path, env={"PATH": str(external)}))
    code, out, err = _run(capsys, ["init"])
    assert code == 3
    assert "--allow-shadow" in out + err


def test_init_allow_shadow_writes_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "node.exe").write_bytes(b"MZ")
    ctx = _ctx(tmp_path, env={"PATH": str(external)})
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["init", "--allow-shadow", "--json"])
    assert code == 0
    data = _envelope(out)["data"]
    assert data["user_path"]["changed"] is True
    assert data["user_path"]["shadowed"]


def test_uninit_human(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _use(monkeypatch, ctx)
    _run(capsys, ["init"])
    code, out, _err = _run(capsys, ["uninit", "--dry-run"])
    assert code == 0
    assert "将删除 shim" in out
    assert "将移除用户 PATH 条目" in out
    code, out, _err = _run(capsys, ["uninit"])
    assert code == 0
    assert "已删除 shim" in out
    assert "已精确移除 1 个 devman 条目" in out
    assert not (_store(ctx).values.get("Path") or "").strip()


# ---------------------------------------------------------------------- #
# env / exec
# ---------------------------------------------------------------------- #


def test_env_human_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["env", "--shell", "cmd"])
    assert code == 0
    assert 'set "PATH=' in out
    code, out, _err = _run(capsys, ["env", "--json"])
    assert code == 0
    assert _envelope(out)["data"]["schema"] == "devman/env/1"


def test_exec_without_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, err = _run(capsys, ["exec", "--", sys.executable, "-V"])
    assert code == 4
    assert "没有活动版本" in out + err


def test_exec_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    seen: list[str] = []

    def which(name: str, path: str | None = None) -> str | None:
        seen.append(path or "")
        return sys.executable

    monkeypatch.setattr(devman_module.shutil, "which", which)
    code, out, _err = _run(capsys, ["exec", "--json", "--", "node", "-c", "print('ok')"])
    assert code == 0
    data = _envelope(out)["data"]
    assert data["schema"] == "devman/exec/1"
    assert data["exit_code"] == 0
    assert str(ctx.layout.version_dir("node", "20.11.1")) in seen[0]


def test_exec_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    code, out, err = _run(capsys, ["exec", "--", sys.executable, "-c", "raise SystemExit(4)"])
    assert code == 1
    assert "退出码 4" in out + err


def test_exec_missing_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    code, out, err = _run(capsys, ["exec", "--", "definitely-not-a-real-command-xyz"])
    assert code == 4
    assert "找不到命令" in out + err


# ---------------------------------------------------------------------- #
# doctor
# ---------------------------------------------------------------------- #


def test_doctor_pass_human(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    ctx = _ctx(
        tmp_path,
        config=Config(python_uv=str(uv)),
        runner=_fakes.FakeRunner(lambda argv, env: _fakes.result(stdout="uv 0.13.1")),
    )
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _ops.init(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "结论: 通过" in out


def test_doctor_fail_json_exit_five(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["doctor", "--json"])
    assert code == 5
    data = _envelope(out)["data"]
    assert data["ok"] is False


def test_install_actual_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    _use(monkeypatch, _ctx(tmp_path, fetcher=fetcher, runner=runner))
    code, out, _err = _run(capsys, ["install", "node@20.12.0"])
    assert code == 0
    assert "已安装" in out
    assert "路径:" in out


def test_versions_empty_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, _err = _run(capsys, ["versions", "python"])
    assert code == 0
    assert "没有可显示的版本" in out


def test_versions_python_installed_shows_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    set_active(ctx.layout, "python", PY_KEY)
    ctx.state.active["python"] = PY_KEY
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["versions", "python"])
    assert code == 0
    assert "3.12.15  (已安装, 活动)" in out


def test_versions_remote_python_deduplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"MZ")
    rows = json.loads(_fakes.python_list_json())
    rows.append(dict(rows[0], key=rows[0]["key"].replace("none", "freethreaded"), variant="default"))
    ctx = _ctx(
        tmp_path,
        config=Config(python_uv=str(uv)),
        runner=_fakes.FakeRunner(lambda argv, env: _fakes.result(stdout=json.dumps(rows))),
    )
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["versions", "python", "--remote"])
    assert code == 0
    assert out.count("3.12.15") == 1


def test_uninstall_dry_run_reports_active_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["uninstall", "node@20", "--dry-run", "--force", "--purge-cache"])
    assert code == 0
    assert "将清空活动标记" in out
    assert "同时清空下载缓存" in out


def test_uninstall_if_present_without_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, _err = _run(capsys, ["uninstall", "node@20", "--if-present"])
    assert code == 0
    assert "没有可卸载的版本" in out


def test_uninstall_force_and_purge_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    ctx.layout.cache_dir.mkdir(parents=True)
    (ctx.layout.cache_dir / "x.zip").write_bytes(b"x")
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["uninstall", "node@20", "--yes", "--force", "--purge-cache"])
    assert code == 0
    assert "活动版本已清空" in out
    assert "下载缓存已清空" in out
    assert "说明:" in out


def test_use_python_hint_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _fakes.install_fake_python(ctx.layout, PY_KEY, "3.12.15")
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["use", f"python@{PY_KEY}"])
    assert code == 0
    assert "uv venv --python" in out


def test_upgrade_purge_requires_yes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, err = _run(capsys, ["upgrade", "node", "--purge-old"])
    assert code == 2
    assert "--yes" in out + err


def test_init_dry_run_without_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, _err = _run(capsys, ["init", "--no-path", "--dry-run"])
    assert code == 0
    assert "将生成" in out
    assert "用户 PATH" not in out


def test_uninit_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _use(monkeypatch, ctx)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise invalid_argument("无法撤销", field="root")

    monkeypatch.setattr(devman_module._ops, "uninit", boom)
    code, out, err = _run(capsys, ["uninit"])
    assert code == 3
    assert "无法撤销" in out + err


def test_exec_spawn_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("spawn failed")

    # 假安装里的 node.exe 在 POSIX 主机上没有可执行位，shutil.which 会返回 None；
    # 本用例只验证 spawn 失败的处理，直接固定命令解析结果。
    monkeypatch.setattr(
        devman_module.shutil,
        "which",
        lambda *_args, **_kwargs: str(ctx.layout.version_dir("node", "20.11.1") / "node.exe"),
    )
    monkeypatch.setattr(devman_module.subprocess, "run", boom)
    code, out, err = _run(capsys, ["exec", "--", "node", "-v"])
    assert code == 1
    assert "spawn failed" in out + err


# ---------------------------------------------------------------------- #
# 人读渲染与失败分支补充
# ---------------------------------------------------------------------- #
def test_install_dry_run_path_already_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _store(ctx).values["Path"] = f"{ctx.layout.shim_dir};C:\\Windows"
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["install", "node@20.11.1", "--dry-run"])
    assert code == 0
    assert "用户 PATH: 已包含 devman（无需修改）" in out


def test_uninstall_dry_run_reports_path_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["uninstall", "node@20", "--dry-run"])
    assert code == 0
    assert f"卸载最后一个版本，将移除用户 PATH 条目: {ctx.layout.shim_dir}" in out


def test_uninstall_reports_leftovers_and_path_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _store(ctx).values["Path"] = f"{ctx.layout.shim_dir};C:\\Windows"
    ctx.state.path_added = [str(ctx.layout.shim_dir)]
    monkeypatch.setattr(_ops.shutil, "rmtree", lambda *args, **kwargs: None)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["uninstall", "node@20", "--yes"])
    assert code == 0
    assert "残留（可手工删除）" in out
    assert "用户 PATH: 已精确移除 1 个 devman 条目" in out
    assert "说明:" in out


def test_use_project_binding_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "devman.toml").write_text('[versions]\nnode = "20"\n', encoding="utf-8")
    (tmp_path / ".node-version").write_text("20\n", encoding="utf-8")
    code, out, _err = _run(capsys, ["use", "node", "--project", "--clear"])
    assert code == 0
    assert "项目绑定已删除" in out


def test_list_human_external_and_project_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    external = tmp_path / "external"
    external.mkdir()
    (external / "node.exe").write_bytes(b"MZ")
    monkeypatch.setenv("PATH", str(external))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "devman.toml").write_text('[versions]\nnode = "20"\n', encoding="utf-8")
    _use(monkeypatch, ctx)

    code, out, _err = _run(capsys, ["list"])
    assert code == 0
    assert f"外部 node: {external / 'node.exe'}" in out
    assert "项目 node: 20 → 20.11.1" in out
    assert "devman.toml" in out


def test_upgrade_dry_run_without_purge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["upgrade", "node", "--dry-run"])
    assert code == 0
    assert "计划升级 20.11.1 → 20.12.0" in out
    assert "升级后卸载" not in out


def test_upgrade_success_without_purge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("20.12.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["upgrade", "node", "--yes"])
    assert code == 0
    assert "已升级 node 20.11.1 → 20.12.0" in out
    assert "旧版本已卸载" not in out


def test_init_dry_run_reports_shadowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (external / "node.exe").write_bytes(b"MZ")
    monkeypatch.setenv("PATH", str(external))
    _use(monkeypatch, _ctx(tmp_path, env={"PATH": str(external)}))
    code, out, _err = _run(capsys, ["init", "--dry-run"])
    assert code == 0
    assert "会遮蔽 node" in out


def test_uninit_no_path_skips_user_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    _run(capsys, ["init"])
    code, out, _err = _run(capsys, ["uninit", "--no-path"])
    assert code == 0
    assert "已删除 shim" in out
    assert "用户 PATH" not in out


def test_uninit_user_path_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, _err = _run(capsys, ["uninit"])
    assert code == 0
    assert "用户 PATH: 无需修改" in out


def test_env_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _use(monkeypatch, _ctx(tmp_path))

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise invalid_argument("shell 不受支持", field="shell")

    monkeypatch.setattr(devman_module._ops, "env_payload", boom)
    code, out, err = _run(capsys, ["env"])
    assert code == 3
    assert "shell 不受支持" in out + err


def test_exec_success_human_has_no_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    monkeypatch.setattr(devman_module.shutil, "which", lambda name, path=None: sys.executable)
    monkeypatch.setattr(devman_module.subprocess, "run", lambda *args, **kwargs: _fakes.result())
    code, out, err = _run(capsys, ["exec", "--", sys.executable, "-v"])
    assert code == 0
    assert "devman/exec/1" not in out
    assert err == ""


def test_exec_empty_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    devman_module.exec_command([], root=str(tmp_path / "root"))
    captured = capsys.readouterr()
    assert "缺少要执行的命令" in captured.out + captured.err


def test_exec_file_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("node")

    monkeypatch.setattr(devman_module.shutil, "which", lambda name, path=None: sys.executable)
    monkeypatch.setattr(devman_module.subprocess, "run", boom)
    code, out, err = _run(capsys, ["exec", "--", "node", "-v"])
    assert code == 1
    assert "找不到命令" in out + err


def test_doctor_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _use(monkeypatch, _ctx(tmp_path))

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise invalid_argument("无法诊断", field="root")

    monkeypatch.setattr(devman_module._ops, "doctor_payload", boom)
    code, out, err = _run(capsys, ["doctor"])
    assert code == 3
    assert "无法诊断" in out + err


def test_size_human_formats_large_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    (ctx.layout.version_dir("node", "20.11.1") / "blob.bin").write_bytes(b"x" * 4096)
    _use(monkeypatch, ctx)
    code, out, _err = _run(capsys, ["size"])
    assert code == 0
    assert "KiB" in out


def test_size_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _use(monkeypatch, _ctx(tmp_path))

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise invalid_argument("无法统计", field="tool")

    monkeypatch.setattr(devman_module._ops, "size_payload", boom)
    code, out, err = _run(capsys, ["size"])
    assert code == 3
    assert "无法统计" in out + err


def test_verify_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _use(monkeypatch, _ctx(tmp_path))

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise invalid_argument("无法校验", field="tool")

    monkeypatch.setattr(devman_module._ops, "verify_payload", boom)
    code, out, err = _run(capsys, ["verify"])
    assert code == 3
    assert "无法校验" in out + err


def test_human_verify_without_hint(capsys: pytest.CaptureFixture[str]) -> None:
    devman_module._human_verify(
        {"ok": False, "issues": [{"kind": "custom", "name": "node", "detail": "自定义问题", "tool": None}]}
    )
    out = capsys.readouterr().out
    assert "发现 1 个问题：" in out
    assert "[custom] node: 自定义问题" in out


def test_human_uninstall_without_note(capsys: pytest.CaptureFixture[str]) -> None:
    devman_module._human_uninstall({"tool": "node", "removed": [{"version": "1.0.0"}], "user_path": {}})
    assert capsys.readouterr().out == "已卸载 node 1.0.0\n"


def test_export_existing_target_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _ctx(tmp_path)
    _install_node(ctx)
    _use(monkeypatch, ctx)
    target = tmp_path / "devman.lock.json"
    target.write_text("{}", encoding="utf-8")
    code, out, err = _run(capsys, ["export", "--output", str(target)])
    assert code == 3
    assert "目标已存在" in out + err


def test_restore_installs_and_switches_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher, runner = _node_setup("18.20.0")
    ctx = _ctx(tmp_path, fetcher=fetcher, runner=runner)
    _install_node(ctx)
    set_active(ctx.layout, "node", "20.11.1")
    _use(monkeypatch, ctx)
    lock = {
        "schema": "devman-lock/1",
        "tools": {"node": {"active": "18.20.0", "versions": [{"version": "18.20.0"}, {"version": "20.11.1"}]}},
    }
    path = tmp_path / "devman.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")

    code, out, _err = _run(capsys, ["restore", str(path), "--yes"])
    assert code == 0
    assert "恢复完成" in out
    assert "安装 node@18.20.0" in out
    assert "切换 node@18.20.0" in out
    assert (ctx.layout.version_dir("node", "18.20.0") / "node.exe").is_file()


def test_restore_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _use(monkeypatch, _ctx(tmp_path))
    code, out, err = _run(capsys, ["restore", str(tmp_path / "missing.lock.json"), "--yes"])
    assert code == 3
    assert "锁文件无法读取" in out + err
