"""wslman CLI / GUI 辅助函数的边界分支补充：状态、open、terminate、导入/复制与下载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import _images, _source, _terminal, _transfer, wslmanw
from fcmd.apps.wslman import wslman as wslman_module
from fcmd.apps.wslman._config import WslmanConfig
from fcmd.apps.wslman._distro import Distro
from fcmd.apps.wslman._errors import WslmanError
from fcmd.apps.wslman._terminal import LaunchError
from fcmd.core.toolkit import run_tool
from tests.apps.wslman import _fakes

_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Running         2
  Debian          Stopped         2
"""

_STOPPED_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Stopped         2
  Debian          Stopped         2
"""

_INSTALLING_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Installing      2
"""

_AFTER_IMPORT_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Stopped         2
  Debian          Stopped         2
"""

_EMPTY_TABLE = """\
  NAME            STATE           VERSION
"""

_AFTER_IMPORT_TABLE2 = """\
  NAME            STATE           VERSION
* Ubuntu          Stopped         2
  Ubuntu2         Stopped         2
"""


class _FakeLauncher:
    def __init__(self, *, failures: int = 0) -> None:
        self.commands: list[Any] = []
        self._failures = failures

    def launch(self, command: Any, *, timeout: float | None = None) -> int:
        self.commands.append(command)
        if self._failures:
            self._failures -= 1
            raise LaunchError("conhost 退出码 1")
        return 4242


def _list(table: str = _TABLE) -> _fakes.FakeRunner:
    return _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(table))})


def _env(
    runner: _fakes.FakeRunner,
    *,
    tmp_path: Path,
    registry: Any = None,
    config: WslmanConfig | None = None,
    launcher: Any = None,
) -> wslman_module.Environment:
    return wslman_module.Environment(
        runner=runner,
        capabilities=_fakes.capabilities(),
        registry=registry if registry is not None else _fakes.snapshot(),
        config=config or WslmanConfig(path=tmp_path / "wslman.json"),
        platform_ok=True,
        launcher=launcher if launcher is not None else _FakeLauncher(),
        lock_dir=tmp_path / "locks",
    )


def _use(monkeypatch: pytest.MonkeyPatch, env: wslman_module.Environment) -> None:
    monkeypatch.setattr(wslman_module, "_build_environment", lambda *args, **kwargs: env)


def _isolate_wslconfig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wslman_module, "wslconfig_default_path", lambda: tmp_path / ".wslconfig")


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = run_tool("wslman", argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _preflight_result(path: Path, *, ok: bool, estimated: bool = False) -> _transfer.SpacePreflight:
    return _transfer.SpacePreflight(
        target=path,
        free_bytes=10,
        required_bytes=1,
        estimated=estimated,
        reserve_bytes=0,
        ok=ok,
        detail="空间充足" if ok else "空间不足",
    )


# ---------------------------------------------------------------------- #
# 文本辅助
# ---------------------------------------------------------------------- #


def test_first_line_skips_blank_lines() -> None:
    assert wslman_module._first_line("\n  \n真实内容\n") == "真实内容"


def test_slug_falls_back_for_non_alnum_names() -> None:
    assert wslman_module._slug("") == "distro"
    assert wslman_module._slug("Ubuntu 24") == "Ubuntu_24"


def test_temp_tar_uses_config_temp_dir_and_target_parent(tmp_path: Path) -> None:
    configured = _env(
        _fakes.FakeRunner({}),
        tmp_path=tmp_path,
        config=WslmanConfig(path=tmp_path / "wslman.json", temp_dir=str(tmp_path / "tmp")),
    )
    explicit = wslman_module._temp_tar(None, configured, tmp_path / "inst", "Ubuntu")
    assert explicit.parent == tmp_path / "tmp"
    assert explicit.name.startswith("wslman-copy-Ubuntu-")
    assert explicit.suffix == ".tar"

    plain = _env(_fakes.FakeRunner({}), tmp_path=tmp_path)
    fallback = wslman_module._temp_tar(None, plain, tmp_path / "inst", "Ubuntu")
    assert fallback.parent == tmp_path


def test_validated_name_rejects_blank_name() -> None:
    with pytest.raises(WslmanError) as excinfo:
        wslman_module._validated_name("   ")

    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert excinfo.value.field == "distro"


def test_abort_parts_reports_when_nothing_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "a.tar.part"
    target.write_bytes(b"x")

    def boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", boom)

    assert wslman_module._abort_parts([target], keep=False) == "未能清理未完成文件"


# ---------------------------------------------------------------------- #
# 只读命令
# ---------------------------------------------------------------------- #


def test_status_reports_unreadable_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    _isolate_wslconfig(monkeypatch, tmp_path)
    runner = _fakes.FakeRunner(
        {
            ("--status",): _fakes.make_result(stdout=_fakes.utf16("默认版本: 2\n")),
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
        }
    )
    registry = _fakes.snapshot(available=False, error="拒绝访问")
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, registry=registry))

    code, out, _err = _run(capsys, ["status"])

    assert code == 0
    assert "注册表: 不可读（拒绝访问）" in out


def test_status_prints_failed_status_and_merge_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    _isolate_wslconfig(monkeypatch, tmp_path)
    runner = _fakes.FakeRunner(
        {
            ("--status",): _fakes.make_result(returncode=1, stdout=_fakes.utf16("部分信息\n")),
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
        }
    )
    registry = _fakes.snapshot(distros=(_fakes.registry_distro("Arch"),), default_distribution=None)
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, registry=registry))

    code, out, _err = _run(capsys, ["status"])

    assert code == 0
    assert "[警告] wsl --status 退出码 1" in out
    assert "[警告] Arch 已注册但未出现在 wsl --list --verbose 中，状态未知" in out


def test_info_reports_wslconf_read_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
            ("-d", "Ubuntu", "-u", "root", "--", "cat", "/etc/wsl.conf"): _fakes.make_result(
                returncode=1, stderr=b"cat: no such file"
            ),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["info", "Ubuntu", "--include-config"])

    assert code == 0
    assert "[错误] 读取 /etc/wsl.conf 失败（退出码 1）：cat: no such file" in out


# ---------------------------------------------------------------------- #
# open / terminate / set-default
# ---------------------------------------------------------------------- #


def test_open_rejects_transitional_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    launcher = _FakeLauncher()
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_INSTALLING_TABLE))})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, launcher=launcher))

    code, out, err = _run(capsys, ["open", "Ubuntu"])

    assert code == 3
    assert "Ubuntu 正在安装中，暂不能打开" in out + err
    assert launcher.commands == []


def test_open_reports_conhost_launch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: None)
    launcher = _FakeLauncher(failures=1)
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE))})
    config = WslmanConfig(path=tmp_path / "wslman.json", terminal_program="conhost")
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, config=config, launcher=launcher))

    code, out, err = _run(capsys, ["open", "Ubuntu"])

    assert code == 1
    assert "启动终端失败：conhost 退出码 1" in out + err
    assert len(launcher.commands) == 1


def test_terminate_when_post_check_fails_reports_unknown_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [
                _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
                _fakes.make_result(returncode=1, stderr=b"boom"),
            ],
            ("--terminate", "Ubuntu"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["terminate", "Ubuntu"])

    assert code == 0
    assert "已终止 Ubuntu（当前状态：未知）" in out


def test_terminate_warns_when_still_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
            ("--terminate", "Ubuntu"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["terminate", "Ubuntu"])

    assert code == 0
    assert "已终止 Ubuntu（当前状态：running）" in out
    assert "[警告] Ubuntu 仍显示为运行中，可能还有进程正在退出" in out


def test_set_default_reports_execution_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
            ("--set-default", "Debian"): _fakes.make_result(returncode=1, stderr=b"denied"),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["set-default", "Debian"])

    assert code == 1
    assert "wsl --set-default Debian 失败（退出码 1）：denied" in out + err


def test_export_reports_unknown_distro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE))})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["export", "Ghost", str(tmp_path / "ghost.tar")])

    assert code == 4
    assert "未找到发行版 Ghost" in out + err


# ---------------------------------------------------------------------- #
# 目录与替换守卫
# ---------------------------------------------------------------------- #


def test_import_rejects_target_dir_that_is_a_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a dir")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, err = _run(capsys, ["import", "Ubuntu", str(archive), "--dir", str(blocker)])

    assert code == 3
    assert "目标路径不是目录" in out + err


def test_import_reports_unreadable_target_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    install = tmp_path / "inst"
    install.mkdir()

    def boom(self: Path, *args: object, **kwargs: object) -> Any:
        raise OSError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, err = _run(capsys, ["import", "Ubuntu", str(archive), "--dir", str(install)])

    assert code == 3
    assert "无法读取目标目录" in out + err


def test_guard_replace_allows_missing_backup_when_not_required(tmp_path: Path) -> None:
    env = _env(
        _fakes.FakeRunner({}),
        tmp_path=tmp_path,
        config=WslmanConfig(path=tmp_path / "wslman.json", require_backup_on_replace=False),
    )
    existing = Distro(name="Ubuntu", state="stopped", state_raw="Stopped", version=2, default=True)

    wslman_module._guard_replace(
        env, existing=existing, yes=True, backup=None, terminate_first=False, command="fcmd wslman import"
    )

    running = Distro(name="Ubuntu", state="running", state_raw="Running", version=2, default=True)
    with pytest.raises(WslmanError) as excinfo:
        wslman_module._guard_replace(
            env, existing=running, yes=True, backup=None, terminate_first=False, command="fcmd wslman import"
        )
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert "替换前必须先停机" in str(excinfo.value)


def test_guard_replace_rejects_existing_backup_and_relative_backup(tmp_path: Path) -> None:
    env = _env(
        _fakes.FakeRunner({}),
        tmp_path=tmp_path,
        config=WslmanConfig(path=tmp_path / "wslman.json", require_backup_on_replace=False),
    )
    existing = Distro(name="Ubuntu", state="stopped", state_raw="Stopped", version=2, default=True)

    taken = tmp_path / "old.tar"
    taken.write_bytes(b"x")
    with pytest.raises(WslmanError) as excinfo:
        wslman_module._guard_replace(
            env, existing=existing, yes=True, backup=taken, terminate_first=False, command="fcmd wslman import"
        )
    assert excinfo.value.field == "backup"
    assert "备份文件已存在" in str(excinfo.value)

    with pytest.raises(WslmanError) as excinfo:
        wslman_module._guard_replace(
            env,
            existing=existing,
            yes=True,
            backup=Path("rel.tar"),
            terminate_first=False,
            command="fcmd wslman import",
        )
    assert excinfo.value.field == "backup"
    assert "绝对路径" in str(excinfo.value)


# ---------------------------------------------------------------------- #
# import / copy
# ---------------------------------------------------------------------- #


def test_import_backup_requires_replace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, err = _run(
        capsys,
        ["import", "Ubuntu2", str(archive), "--dir", str(tmp_path / "inst"), "--backup", str(tmp_path / "b.tar")],
    )

    assert code == 3
    assert "--backup 只用于 --replace" in out + err


def test_import_replaces_without_backup_or_terminate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    install = tmp_path / "inst"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [
                _fakes.make_result(stdout=_fakes.utf16(_STOPPED_TABLE)),
                _fakes.make_result(stdout=_fakes.utf16(_AFTER_IMPORT_TABLE)),
            ],
            ("--unregister", "Ubuntu"): _fakes.make_result(),
            ("--import", "Ubuntu", str(install), str(archive)): _fakes.make_result(),
        }
    )
    config = WslmanConfig(path=tmp_path / "wslman.json", require_backup_on_replace=False)
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, config=config))

    code, out, _err = _run(capsys, ["import", "Ubuntu", str(archive), "--dir", str(install), "--replace", "--yes"])

    assert code == 0
    assert "导入完成: Ubuntu" in out
    assert [call[1] for call in runner.calls] == ["--list", "--unregister", "--import", "--list"]


def test_import_warns_when_verification_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    install = tmp_path / "inst"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [
                _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
                _fakes.make_result(stdout=_fakes.utf16(_EMPTY_TABLE)),
            ],
            ("--import", "Ubuntu2", str(install), str(archive)): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["import", "Ubuntu2", str(archive), "--dir", str(install)])

    assert code == 0
    assert "[警告] 导入命令已成功，但复核时尚未在 wsl -l -v 中看到该发行版" in out


def test_import_passes_explicit_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    install = tmp_path / "inst"
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [
                _fakes.make_result(stdout=_fakes.utf16(_STOPPED_TABLE)),
                _fakes.make_result(stdout=_fakes.utf16(_AFTER_IMPORT_TABLE2)),
            ],
            ("--import", "Ubuntu2", str(install), str(archive), "--version", "2"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, _out, _err = _run(capsys, ["import", "Ubuntu2", str(archive), "--dir", str(install), "--version", "2"])

    assert code == 0
    assert ("--import", "Ubuntu2", str(install), str(archive), "--version", "2") in [call[1:] for call in runner.calls]


def test_import_backup_preflight_failure_stops_before_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    install = tmp_path / "inst"
    backup = tmp_path / "backup.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_STOPPED_TABLE))})
    config = WslmanConfig(path=tmp_path / "wslman.json", require_backup_on_replace=False)

    def fake_preflight(env: Any, path: Path, needed: int | None, *, estimated: bool) -> _transfer.SpacePreflight:
        return _preflight_result(path, ok=Path(path) != backup, estimated=estimated)

    monkeypatch.setattr(wslman_module, "_preflight_for", fake_preflight)
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, config=config))

    code, out, err = _run(
        capsys,
        [
            "import",
            "Ubuntu",
            str(archive),
            "--dir",
            str(install),
            "--replace",
            "--yes",
            "--backup",
            str(backup),
        ],
    )

    assert code == 1
    assert "备份路径空间不足" in out + err
    assert ("--unregister", "Ubuntu") not in [call[1:] for call in runner.calls]


def test_import_target_preflight_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    archive = tmp_path / "a.tar"
    archive.write_bytes(b"tar")
    install = tmp_path / "inst"
    monkeypatch.setattr(
        wslman_module,
        "_preflight_for",
        lambda env, path, needed, *, estimated: _preflight_result(Path(path), ok=False, estimated=estimated),
    )
    _use(
        monkeypatch,
        _env(
            _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE))}),
            tmp_path=tmp_path,
        ),
    )

    code, out, err = _run(capsys, ["import", "Ubuntu2", str(archive), "--dir", str(install)])

    assert code == 1
    assert "目标卷空间不足" in out + err


def test_copy_rejects_file_target_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a dir")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, err = _run(capsys, ["copy", "Ubuntu", "Ubuntu2", "--dir", str(blocker)])

    assert code == 3
    assert "目标路径不是目录" in out + err


def test_copy_backup_requires_replace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_STOPPED_TABLE))})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(
        capsys,
        ["copy", "Ubuntu", "Ubuntu2", "--dir", str(tmp_path / "inst"), "--backup", str(tmp_path / "b.tar")],
    )

    assert code == 3
    assert "--backup 只用于 --replace" in out + err


def test_copy_preview_without_version_for_registry_only_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE))})
    registry = _fakes.snapshot(distros=(_fakes.registry_distro("Arch", version=None),))
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, registry=registry))

    code, out, _err = _run(capsys, ["copy", "Arch", "Arch2", "--dir", str(tmp_path / "inst"), "--preview", "--json"])

    assert code == 0
    payload = json.loads(out)["data"]
    assert payload["version"] is None
    assert "--version" not in payload["commands"][-1]


def test_copy_replaces_without_backup_or_terminate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    install = tmp_path / "inst"
    temp = tmp_path / "copy.tar"

    def fallback(command: tuple[str, ...]) -> Any:
        if command[1] == "--export":
            Path(command[-1]).write_bytes(b"tar-data")
        return _fakes.make_result()

    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_STOPPED_TABLE)),
            ("--unregister", "Debian"): _fakes.make_result(),
        },
        fallback=fallback,
    )
    config = WslmanConfig(path=tmp_path / "wslman.json", require_backup_on_replace=False)
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, config=config))

    code, out, _err = _run(
        capsys,
        [
            "copy",
            "Ubuntu",
            "Debian",
            "--dir",
            str(install),
            "--temp",
            str(temp),
            "--replace",
            "--yes",
        ],
    )

    assert code == 0
    assert "复制完成: Ubuntu → Debian" in out
    assert ("--unregister", "Debian") in [call[1:] for call in runner.calls]


def test_copy_backup_preflight_failure_and_temp_preflight_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    install = tmp_path / "inst"
    backup = tmp_path / "backup.tar"
    temp = tmp_path / "copy.tar"
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_STOPPED_TABLE))})
    config = WslmanConfig(path=tmp_path / "wslman.json", require_backup_on_replace=False)
    _use(monkeypatch, _env(runner, tmp_path=tmp_path, config=config))

    def fake_preflight(env: Any, path: Path, needed: int | None, *, estimated: bool) -> _transfer.SpacePreflight:
        return _preflight_result(path, ok=Path(path) != backup, estimated=estimated)

    monkeypatch.setattr(wslman_module, "_preflight_for", fake_preflight)
    code, out, err = _run(
        capsys,
        [
            "copy",
            "Ubuntu",
            "Debian",
            "--dir",
            str(install),
            "--temp",
            str(temp),
            "--replace",
            "--yes",
            "--backup",
            str(backup),
        ],
    )
    assert code == 1
    assert "备份路径空间不足" in out + err

    monkeypatch.setattr(
        wslman_module,
        "_preflight_for",
        lambda env, path, needed, *, estimated: _preflight_result(Path(path), ok=False, estimated=estimated),
    )
    code, out, err = _run(capsys, ["copy", "Ubuntu", "Ubuntu2", "--dir", str(install), "--temp", str(temp)])
    assert code == 1
    assert "临时目录所在卷空间不足" in out + err


# ---------------------------------------------------------------------- #
# 镜像清单与下载
# ---------------------------------------------------------------------- #


def test_manifest_images_parses_fetched_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {"ModernDistributions": {"Ubuntu": [{"Name": "Ubuntu", "Amd64Url": "https://example.com/u.wsl"}]}}
    ).encode()
    monkeypatch.setattr(_source, "fetch_manifest", lambda url, **kwargs: payload)

    entries = wslman_module._manifest_images()

    assert [entry.name for entry in entries] == ["Ubuntu"]
    assert entries[0].arches == ("x86_64",)


def test_backup_names_lists_archives_only(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "Ubuntu.tar").write_bytes(b"x")
    (backups / "Debian.tar.gz").write_bytes(b"x")
    (backups / "note.txt").write_text("x", encoding="utf-8")
    (backups / "nested.tar").mkdir()
    env = _env(
        _fakes.FakeRunner({}),
        tmp_path=tmp_path,
        config=WslmanConfig(path=tmp_path / "wslman.json", backups_dir=str(backups)),
    )

    names = wslman_module._backup_names(env)

    assert sorted(names) == ["Debian", "Ubuntu"]


def test_download_rejects_unknown_mirror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, err = _run(capsys, ["download", "Ubuntu", "--mirror", "bogus", "--preview"])

    assert code == 3
    assert "不支持的 --mirror：bogus" in out + err


def _image_entry(*downloads: _images.ImageDownload, name: str = "Ubuntu") -> _images.ImageEntry:
    return _images.ImageEntry(name, name, ("manifest",), downloads)


def test_official_image_prefers_current_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    arm = _images.ImageDownload("aarch64", "https://example.com/arm.wsl", "b" * 64)
    amd = _images.ImageDownload("x86_64", "https://example.com/amd.wsl", "a" * 64)
    monkeypatch.setattr(wslman_module, "_manifest_images", lambda: (_image_entry(arm, amd),))
    monkeypatch.setattr(_source, "current_arch", lambda: "arm64")

    image, download = wslman_module._official_image("ubuntu")

    assert (image.name, download.arch) == ("Ubuntu", "aarch64")


def test_official_image_falls_back_to_first_download(monkeypatch: pytest.MonkeyPatch) -> None:
    only = _images.ImageDownload("riscv64", "https://example.com/r.wsl")
    monkeypatch.setattr(wslman_module, "_manifest_images", lambda: (_image_entry(only, name="Debian"),))
    monkeypatch.setattr(_source, "current_arch", lambda: "x86_64")

    _image, download = wslman_module._official_image("Debian")

    assert download.arch == "riscv64"


def test_official_image_store_only_entry_has_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _images.ImageEntry("Oracle", "Oracle", ("store",))
    monkeypatch.setattr(wslman_module, "_manifest_images", lambda: (entry,))

    _image, download = wslman_module._official_image("Oracle")

    assert (download.arch, download.url) == ("any", "")


def test_official_image_uses_cache_when_manifest_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _image_entry(_images.ImageDownload("x86_64", "https://example.com/u.wsl", "a" * 64))
    cache = _images.ImageCache(entries=(entry,), fetched_at=1.0, sources=("manifest",))
    boom = wslman_module.runtime_error("清单不可达")
    monkeypatch.setattr(wslman_module, "_manifest_images", lambda: (_ for _ in ()).throw(boom))
    monkeypatch.setattr(_images, "load_cache", lambda path=None: cache)
    monkeypatch.setattr(_source, "current_arch", lambda: "x86_64")

    image, download = wslman_module._official_image("Ubuntu")

    assert (image.name, download.arch) == ("Ubuntu", "x86_64")


def test_official_image_reraises_without_usable_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    boom = wslman_module.runtime_error("清单不可达")
    monkeypatch.setattr(wslman_module, "_manifest_images", lambda: (_ for _ in ()).throw(boom))
    monkeypatch.setattr(_images, "load_cache", lambda path=None: None)

    with pytest.raises(WslmanError) as excinfo:
        wslman_module._official_image("Ubuntu")
    assert excinfo.value is boom

    empty = _images.ImageCache(entries=(), fetched_at=1.0, sources=())
    monkeypatch.setattr(_images, "load_cache", lambda path=None: empty)

    with pytest.raises(WslmanError) as excinfo:
        wslman_module._official_image("Ubuntu")
    assert excinfo.value is boom


def test_official_image_reports_missing_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wslman_module, "_manifest_images", lambda: (_image_entry(),))

    with pytest.raises(WslmanError) as excinfo:
        wslman_module._official_image("Fedora")

    assert excinfo.value.code == "NOT_FOUND"
    assert "官方清单里没有发行版：Fedora" in str(excinfo.value)
    assert "Ubuntu" in (excinfo.value.hint or "")


def test_download_uses_official_manifest_without_custom_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    entry = _image_entry(_images.ImageDownload("x86_64", "https://example.com/u.wsl", "a" * 64))
    target = tmp_path / "dl" / "Ubuntu-x86_64.tar"
    monkeypatch.setattr(wslman_module, "_manifest_images", lambda: (entry,))
    monkeypatch.setattr(_source, "current_arch", lambda: "x86_64")
    monkeypatch.setattr(
        _source,
        "download",
        lambda url, path, **kwargs: _source.DownloadResult(
            path=Path(path), sha256="a" * 64, size=10, verified=True, resumed=False
        ),
    )
    config = WslmanConfig(path=tmp_path / "wslman.json", download_dir=str(tmp_path / "dl"))
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path, config=config))

    code, out, _err = _run(capsys, ["download", "Ubuntu", "--source", "manifest"])

    assert code == 0
    assert "使用官方发行版清单：Ubuntu（x86_64）" in out
    assert str(target) in out


def test_download_rejects_store_only_official_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    monkeypatch.setattr(
        wslman_module, "_manifest_images", lambda: (_images.ImageEntry("Oracle", "Oracle", ("store",)),)
    )
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, err = _run(capsys, ["download", "Oracle", "--source", "manifest", "--dir", str(tmp_path / "dl")])

    assert code == 1
    assert "Oracle 没有可直接下载的镜像（只有 Store 包）" in out + err


def test_download_warns_when_manifest_lacks_sha256(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    payload = json.dumps({"distros": [{"name": "Ubuntu", "url": "https://example.com/u.tar"}]}).encode()
    monkeypatch.setattr(_source, "fetch_manifest", lambda url, **kwargs: payload)
    monkeypatch.setattr(
        _source,
        "download",
        lambda url, path, **kwargs: _source.DownloadResult(
            path=Path(path), sha256="b" * 64, size=5, verified=False, resumed=False
        ),
    )
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, _err = _run(
        capsys,
        [
            "download",
            "Ubuntu",
            "--source",
            "manifest",
            "--manifest-url",
            "https://example.com/m.json",
            "--dir",
            str(tmp_path / "dl"),
        ],
    )

    assert code == 0
    assert "[警告] 清单未提供 sha256，只能走官方源（不做字节校验）" in out


def test_download_warns_to_prefer_domestic_mirrors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    payload = json.dumps(
        {
            "distros": [
                {
                    "name": "Ubuntu",
                    "url": "https://releases.ubuntu.com/24.04/ubuntu-24.04-wsl-amd64.wsl",
                    "sha256": "a" * 64,
                }
            ]
        }
    ).encode()
    monkeypatch.setattr(_source, "fetch_manifest", lambda url, **kwargs: payload)
    monkeypatch.setattr(
        _source,
        "download",
        lambda url, path, **kwargs: _source.DownloadResult(
            path=Path(path), sha256="a" * 64, size=5, verified=True, resumed=False
        ),
    )
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, _err = _run(
        capsys,
        [
            "download",
            "Ubuntu",
            "--source",
            "manifest",
            "--manifest-url",
            "https://example.com/m.json",
            "--dir",
            str(tmp_path / "dl"),
        ],
    )

    assert code == 0
    assert "[警告] 优先使用国内镜像：中科大 USTC；校验不过自动回退官方源" in out


def test_download_preview_with_configured_manifest_and_no_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    config = WslmanConfig(path=tmp_path / "wslman.json", manifest_url="https://example.com/m.json")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path, config=config))

    code, out, _err = _run(capsys, ["download", "Ubuntu", "--source", "manifest", "--preview"])

    assert code == 0
    assert "清单: https://example.com/m.json" in out
    assert "目标: <未指定 --dir>" in out


def test_download_preview_with_dir_uses_current_arch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    monkeypatch.setattr(_source, "current_arch", lambda: "x86_64")
    _use(monkeypatch, _env(_fakes.FakeRunner({}), tmp_path=tmp_path))

    code, out, _err = _run(
        capsys, ["download", "Ubuntu", "--source", "manifest", "--dir", str(tmp_path / "dl"), "--preview"]
    )

    assert code == 0
    assert "清单: 官方发行版清单" in out
    assert f"目标: {tmp_path / 'dl' / 'Ubuntu-x86_64.tar'}" in out


# ---------------------------------------------------------------------- #
# wslmanw 辅助函数
# ---------------------------------------------------------------------- #


def test_mirror_choices_lists_mirrors_for_verified_ubuntu() -> None:
    entry = _image_entry(
        _images.ImageDownload("x86_64", "https://releases.ubuntu.com/24.04/ubuntu-24.04-wsl-amd64.wsl", "a" * 64)
    )

    assert wslmanw.mirror_choices(entry) == "中科大 USTC、阿里云、华为云"


def test_local_archives_degrades_on_scan_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Path, *args: object, **kwargs: object) -> Any:
        raise OSError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)

    assert wslmanw.local_archives(tmp_path) == ()


def test_guess_distro_name_strips_arch_tail_without_archive_suffix() -> None:
    assert wslmanw.guess_distro_name(Path("Ubuntu-amd64")) == "Ubuntu"
