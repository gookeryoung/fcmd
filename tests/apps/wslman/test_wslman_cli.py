"""wslman P1 CLI 测试：只读子命令、双读输出与三种环境异常的稳定退出码。

所有外部命令都经 ``FakeRunner`` 注入；测试不执行真实 ``wsl.exe``，
也不读取测试机上的真实注册表与 ``.wslconfig``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import wslman as wslman_module
from fcmd.apps.wslman._config import WslmanConfig
from fcmd.apps.wslman._disk import VolumeInfo
from fcmd.apps.wslman._registry import unavailable_snapshot
from fcmd.apps.wslman._runner import Capabilities
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands, run_tool
from tests.apps.wslman import _fakes

_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Running         2
  Debian          Stopped         2
"""

_SUBCOMMANDS = {"config", "doctor", "info", "list", "status"}


def _env(  # noqa: PLR0913 - 测试工厂按需覆盖各字段
    runner: _fakes.FakeRunner,
    *,
    capabilities: Capabilities | None = None,
    registry: Any = None,
    config: WslmanConfig | None = None,
    platform_ok: bool = True,
    tmp_path: Path | None = None,
) -> wslman_module.Environment:
    path = (tmp_path or Path("C:/fake")) / "wslman.json"
    return wslman_module.Environment(
        runner=runner,
        capabilities=capabilities or _fakes.capabilities(),
        registry=registry if registry is not None else _fakes.snapshot(),
        config=config or WslmanConfig(path=path),
        platform_ok=platform_ok,
    )


def _use(monkeypatch: pytest.MonkeyPatch, env: wslman_module.Environment) -> None:
    monkeypatch.setattr(wslman_module, "_build_environment", lambda *args, **kwargs: env)


def _isolate_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把 ``.wslconfig`` 与推荐备份目录都指到临时目录。"""
    monkeypatch.setattr(wslman_module, "wslconfig_default_path", lambda: tmp_path / ".wslconfig")
    monkeypatch.setattr(wslman_module, "recommended_backup_dir", lambda: tmp_path / "wsl-backups")


def _list_runner(table: str = _TABLE) -> _fakes.FakeRunner:
    return _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(table))})


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = run_tool("wslman", argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------- #
# 注册与构建
# ---------------------------------------------------------------------- #


def test_p1_registers_five_readonly_subcommands() -> None:
    assert set(_TOOL_REGISTRY["wslman"]) == _SUBCOMMANDS
    assert set(list_subcommands("wslman")) == _SUBCOMMANDS


def test_build_environment_on_non_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wslman_module, "probe", lambda runner, wsl_path=None: _fakes.capabilities())
    monkeypatch.setattr(wslman_module, "is_windows", lambda: False)
    monkeypatch.setattr(wslman_module, "load_config", lambda path=None: WslmanConfig(path=tmp_path / "c.json"))
    environment = wslman_module._build_environment()
    assert environment.platform_ok is False
    assert environment.registry.available is False
    assert "非 Windows" in (environment.registry.error or "")


def test_build_environment_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wslman_module, "probe", lambda runner, wsl_path=None: _fakes.capabilities())
    monkeypatch.setattr(wslman_module, "is_windows", lambda: True)
    monkeypatch.setattr(wslman_module, "load_config", lambda path=None: WslmanConfig(path=tmp_path / "c.json"))

    class Reader:
        def snapshot(self) -> Any:
            return _fakes.snapshot(available=True)

    monkeypatch.setattr(wslman_module, "WindowsRegistryReader", Reader)
    environment = wslman_module._build_environment()
    assert environment.platform_ok is True
    assert environment.registry.available is True


# ---------------------------------------------------------------------- #
# list
# ---------------------------------------------------------------------- #


def test_list_human_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = _fakes.snapshot(
        distros=(
            _fakes.registry_distro("Ubuntu", base_path=str(tmp_path / "Ubuntu")),
            _fakes.registry_distro("Debian", base_path=str(tmp_path / "Debian")),
        ),
        default_distribution="{Ubuntu}",
    )
    _use(monkeypatch, _env(_list_runner(), registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["list"])
    assert code == 0
    assert "*" in out
    assert "Ubuntu" in out and "运行中" in out
    assert "Debian" in out and "已停止" in out
    assert "默认发行版：Ubuntu" in out


def test_list_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = _fakes.snapshot(
        distros=(_fakes.registry_distro("Ubuntu", base_path=str(tmp_path / "Ubuntu")),),
        default_distribution="{Ubuntu}",
    )
    _use(monkeypatch, _env(_list_runner(), registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["list", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == "fcmd-result/1"
    assert payload["tool"] == "wslman"
    assert payload["subcommand"] == "list"
    data = payload["data"]
    assert data["schema"] == "wslman/list/1"
    assert data["count"] == 2
    assert data["default"] == "Ubuntu"
    ubuntu = next(item for item in data["distros"] if item["name"] == "Ubuntu")
    assert ubuntu["state"] == "running"
    assert ubuntu["version"] == 2
    assert ubuntu["default"] is True
    assert ubuntu["base_path"] == str(tmp_path / "Ubuntu")
    assert ubuntu["registered"] is True
    assert "config_warnings" in data


def test_list_with_config_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = WslmanConfig(path=tmp_path / "wslman.json", warnings=("坏字段",))
    _use(monkeypatch, _env(_list_runner(), config=config, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["list"])
    assert code == 0
    assert "[配置警告] 坏字段" in out


def test_list_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "Ubuntu"
    base.mkdir()
    (base / "ext4.vhdx").write_bytes(b"x" * 2048)
    registry = _fakes.snapshot(distros=(_fakes.registry_distro("Ubuntu", base_path=str(base)),))
    _use(monkeypatch, _env(_list_runner("* Ubuntu  Running  2\n"), registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["list", "--disk"])
    assert code == 0
    assert "≈2.0 KiB" in out


def test_list_no_distributions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _fakes.make_result(
                returncode=1, stderr=_fakes.utf16("适用于 Linux 的 Windows 子系统没有已安装的分发版。\n")
            )
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["list"])
    assert code == 0
    assert "没有已安装的 WSL 发行版" in out


def test_list_no_distributions_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("no installed distributions"))}
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["list", "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["count"] == 0
    assert "尚未安装" in " ".join(data["warnings"])


def test_list_wsl_not_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("WSL is not installed"))}
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, err = _run(capsys, ["list"])
    assert code == 1
    assert "WSL 未启用" in out + err
    assert "wsl --install" in out + err


def test_list_wsl_not_enabled_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("WSL is not installed"))}
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, err = _run(capsys, ["list", "--json"])
    assert code == 1
    assert out == ""
    payload = json.loads(err)
    assert payload["code"] == "RUNTIME_ERROR"
    assert payload["exit_code"] == 1


def test_list_wsl_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner(), capabilities=_fakes.capabilities(wsl_path=None), tmp_path=tmp_path))
    code, out, err = _run(capsys, ["list"])
    assert code == 4
    assert "未找到 wsl.exe" in out + err


def test_list_wsl_missing_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner(), capabilities=_fakes.capabilities(wsl_path=None), tmp_path=tmp_path))
    code, _out, err = _run(capsys, ["list", "--json"])
    assert code == 4
    payload = json.loads(err)
    assert payload["code"] == "NOT_FOUND"
    assert payload["kind"] == "not_found"


def test_list_non_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner(), platform_ok=False, tmp_path=tmp_path))
    code, out, err = _run(capsys, ["list"])
    assert code == 4
    assert "仅支持 Windows" in out + err


def test_list_generic_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("unexpected failure"))}
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, err = _run(capsys, ["list"])
    assert code == 1
    assert "unexpected failure" in out + err


def test_list_runner_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): OSError("无法启动进程")})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, err = _run(capsys, ["list"])
    assert code == 1
    assert "无法启动进程" in out + err


# ---------------------------------------------------------------------- #
# status
# ---------------------------------------------------------------------- #


def _status_runner() -> _fakes.FakeRunner:
    return _fakes.FakeRunner(
        {
            ("--status",): _fakes.make_result(stdout=_fakes.utf16("默认版本: 2\n默认分发版: Ubuntu\n")),
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
        }
    )


def test_status_human(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _isolate_files(monkeypatch, tmp_path)
    (tmp_path / ".wslconfig").write_text("[wsl2]\nmemory=8GB\n", encoding="utf-8")
    registry = _fakes.snapshot(
        distros=(_fakes.registry_distro("Ubuntu"), _fakes.registry_distro("Debian")),
        default_distribution="{Ubuntu}",
    )
    _use(monkeypatch, _env(_status_runner(), registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["status"])
    assert code == 0
    assert "WSL 路径" in out
    assert "WSL 版本: 2.3.26.0" in out
    assert "默认发行版: Ubuntu" in out
    assert "运行中 1：Ubuntu" in out
    assert "DefaultVersion=2" in out
    assert "[wsl2]" in out and "memory = 8GB" in out
    assert "默认分发版: Ubuntu" in out


def test_status_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _isolate_files(monkeypatch, tmp_path)
    _use(monkeypatch, _env(_status_runner(), registry=_fakes.snapshot(default_version=2), tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["status", "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/status/1"
    assert {"label": "默认版本", "value": "2"} in data["wsl"]["status_pairs"]
    assert data["wsl"]["version"] == "2.3.26.0"
    assert data["distros"]["count"] == 2
    assert data["distros"]["running"] == ["Ubuntu"]
    assert data["wslconfig"]["exists"] is False


def test_status_not_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    runner = _fakes.FakeRunner(
        {
            ("--status",): _fakes.make_result(returncode=1, stderr=_fakes.utf16("WSL is not installed")),
            ("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("WSL is not installed")),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, err = _run(capsys, ["status"])
    assert code == 1
    assert "WSL 未启用" in out + err


def test_status_empty_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    runner = _fakes.FakeRunner({("--status",): _fakes.make_result(returncode=1)})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, err = _run(capsys, ["status"])
    assert code == 1
    assert "无输出" in out + err


# ---------------------------------------------------------------------- #
# info
# ---------------------------------------------------------------------- #


def test_info_human(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "Ubuntu"
    base.mkdir()
    (base / "ext4.vhdx").write_bytes(b"x" * 1024)
    registry = _fakes.snapshot(distros=(_fakes.registry_distro("Ubuntu", base_path=str(base)),))
    _use(monkeypatch, _env(_list_runner(), registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["info", "ubuntu"])
    assert code == 0
    assert "名称: Ubuntu" in out
    assert "状态: 运行中" in out
    assert f"BasePath: {base}" in out
    assert "默认用户 UID: 1000" in out
    assert "Flags: 0x0000000f" in out
    assert "≈1.0 KiB" in out
    assert "wsl --list --verbose + Lxss 注册表" in out


def test_info_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = _fakes.snapshot(distros=(_fakes.registry_distro("Ubuntu"),))
    _use(monkeypatch, _env(_list_runner(), registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["info", "Ubuntu", "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/info/1"
    assert data["distro"]["name"] == "Ubuntu"
    assert data["distro"]["state"] == "running"
    assert data["wslconf"] == {"requested": False, "text": None, "error": None}


def test_info_unknown_distro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _env(_list_runner(), tmp_path=tmp_path))
    code, out, err = _run(capsys, ["info", "Missing"])
    assert code == 4
    assert "未找到发行版 Missing" in out + err


def test_info_include_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
            ("-d", "Ubuntu", "-u", "root", "--", "cat", "/etc/wsl.conf"): _fakes.make_result(
                stdout=b"[boot]\nsystemd=true\n"
            ),
        }
    )
    registry = _fakes.snapshot(distros=(_fakes.registry_distro("Ubuntu"),))
    _use(monkeypatch, _env(runner, registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["info", "Ubuntu", "--include-config"])
    assert code == 0
    assert "/etc/wsl.conf:" in out
    assert "systemd=true" in out


def test_info_include_config_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _fakes.make_result(stdout=_fakes.utf16(_TABLE)),
            ("-d", "Ubuntu", "-u", "root", "--", "cat", "/etc/wsl.conf"): _fakes.make_result(
                returncode=1, stderr=b"cat: /etc/wsl.conf: No such file or directory"
            ),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["info", "Ubuntu", "--include-config", "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["wslconf"]["requested"] is True
    assert data["wslconf"]["text"] is None
    assert "No such file" in (data["wslconf"]["error"] or "")


# ---------------------------------------------------------------------- #
# config
# ---------------------------------------------------------------------- #


def test_config_missing_default_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    _use(monkeypatch, _env(_fakes.FakeRunner(), tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["config"])
    assert code == 0
    assert "状态: 不存在" in out


def test_config_explicit_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use(monkeypatch, _env(_fakes.FakeRunner(), tmp_path=tmp_path))
    missing = tmp_path / "missing.ini"
    code, out, err = _run(capsys, ["config", "--path", str(missing)])
    assert code == 4
    assert "配置文件不存在" in out + err


def test_config_parse_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.ini"
    broken.write_text("[wsl2\nmemory=1\n", encoding="utf-8")
    _use(monkeypatch, _env(_fakes.FakeRunner(), tmp_path=tmp_path))
    code, out, err = _run(capsys, ["config", "--path", str(broken)])
    assert code == 1
    assert "解析失败" in out + err


def test_config_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / ".wslconfig"
    path.write_text("[wsl2]\nmemory=8GB\nbogus=1\n", encoding="utf-8")
    _use(monkeypatch, _env(_fakes.FakeRunner(), tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["config", "--path", str(path), "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/config/1"
    assert data["exists"] is True
    assert data["sections"]["wsl2"]["memory"] == "8GB"
    assert any("bogus" in warning for warning in data["warnings"])


# ---------------------------------------------------------------------- #
# doctor
# ---------------------------------------------------------------------- #


def _doctor_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _fakes.FakeRunner:
    _isolate_files(monkeypatch, tmp_path)
    (tmp_path / "wsl-backups").mkdir()
    registry = _fakes.snapshot(
        distros=(_fakes.registry_distro("Ubuntu"), _fakes.registry_distro("Debian")),
        default_distribution="{Ubuntu}",
    )
    runner = _list_runner()
    _use(monkeypatch, _env(runner, registry=registry, tmp_path=tmp_path))
    return runner


def test_doctor_all_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _doctor_ready(monkeypatch, tmp_path)
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "[通过] wsl.exe" in out
    assert "检查通过" in out
    assert "推荐" in out and "剩余" in out


def test_doctor_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _doctor_ready(monkeypatch, tmp_path)
    code, out, _err = _run(capsys, ["doctor", "--json"])
    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/doctor/1"
    assert data["failed"] == 0
    names = [check["name"] for check in data["checks"]]
    assert "wsl.exe" in names
    assert "备份目录" in names
    assert data["backup_dir"]["configured"] == ""
    assert data["backup_dir"]["recommended"].endswith("wsl-backups")


def test_doctor_wsl_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    _use(monkeypatch, _env(_fakes.FakeRunner(), capabilities=_fakes.capabilities(wsl_path=None), tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 5
    assert "检查未通过" in out
    assert "[失败] wsl.exe" in out


def test_doctor_wsl_missing_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    _use(monkeypatch, _env(_fakes.FakeRunner(), capabilities=_fakes.capabilities(wsl_path=None), tmp_path=tmp_path))
    code, out, err = _run(capsys, ["doctor", "--json"])
    assert code == 5
    assert err == ""
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["data"]["failed"] >= 1


def test_doctor_wsl_not_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("WSL is not installed"))}
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 5
    assert "[失败] 发行版列表" in out


def test_doctor_no_distributions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    runner = _fakes.FakeRunner(
        {("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=_fakes.utf16("no installed distributions"))}
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "[警告] 发行版列表" in out
    assert "没有已安装的发行版" in out


def test_doctor_registry_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    _use(
        monkeypatch,
        _env(_list_runner(), registry=unavailable_snapshot("拒绝访问"), tmp_path=tmp_path),
    )
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "[警告] Lxss 注册表" in out
    assert "拒绝访问" in out


def test_doctor_default_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    registry = _fakes.snapshot(
        distros=(_fakes.registry_distro("Ubuntu"), _fakes.registry_distro("Debian")),
        default_distribution="{Debian}",
    )
    _use(monkeypatch, _env(_list_runner(), registry=registry, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "wsl 标记为 Ubuntu，注册表为 Debian" in out


def test_doctor_wslconfig_parse_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _doctor_ready(monkeypatch, tmp_path)
    (tmp_path / ".wslconfig").write_text("[wsl2\nmemory=1\n", encoding="utf-8")
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 5
    assert "[失败] .wslconfig" in out


def test_doctor_wslconfig_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _doctor_ready(monkeypatch, tmp_path)
    (tmp_path / ".wslconfig").write_text("[wsl2]\nbogus=1\n", encoding="utf-8")
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "[警告] .wslconfig" in out
    assert "fcmd wslman config" in out


def test_doctor_backup_configured_small_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _doctor_ready(monkeypatch, tmp_path)
    config = WslmanConfig(path=tmp_path / "wslman.json", backups_dir="D:/backups")
    env = _env(_list_runner(), registry=_fakes.snapshot(), config=config, tmp_path=tmp_path)
    _use(monkeypatch, env)
    monkeypatch.setattr(
        wslman_module,
        "volume_info",
        lambda path: VolumeInfo(path=str(path), free_bytes=1024, total_bytes=2048, error=None),
    )
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "D:\\backups" in out or "D:/backups" in out
    assert "剩余空间不足 2 GiB" in out


def test_doctor_backup_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _doctor_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(
        wslman_module,
        "volume_info",
        lambda path: VolumeInfo(path=str(path), free_bytes=None, total_bytes=None, error="卷不可用"),
    )
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 0
    assert "备份目录" in out
    assert "卷不可用" in out


def test_doctor_platform_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate_files(monkeypatch, tmp_path)
    _use(monkeypatch, _env(_fakes.FakeRunner(), platform_ok=False, tmp_path=tmp_path))
    code, out, _err = _run(capsys, ["doctor"])
    assert code == 5
    assert "[失败] 平台" in out
