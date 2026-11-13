"""wslman P2：``_terminal`` 与 ``open`` / ``terminate`` / ``set-default``。

外部命令与终端启动都注入假实现：``--preview`` 用例断言执行器与启动器零调用，
证明预览不产生任何副作用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import _terminal
from fcmd.apps.wslman import wslman as wslman_module
from fcmd.apps.wslman._config import WslmanConfig
from fcmd.apps.wslman._terminal import LaunchError, TerminalCommand
from fcmd.core.toolkit import _TOOL_REGISTRY, list_subcommands, run_tool
from tests.apps.wslman import _fakes

_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Running         2
  Debian          Stopped         2
  My Distro       Running         2
"""

_STOPPED_TABLE = """\
  NAME            STATE           VERSION
* Ubuntu          Stopped         2
  Debian          Stopped         2
  My Distro       Running         2
"""

_DEBIAN_DEFAULT_TABLE = """\
  NAME            STATE           VERSION
  Ubuntu          Running         2
* Debian          Stopped         2
  My Distro       Running         2
"""

WT = r"C:\Program Files\WindowsApps\wt.exe"


class FakeLauncher:
    """记录启动命令；``failures`` 次调用后抛 ``error``，用于覆盖回退路径。"""

    def __init__(self, *, failures: int = 0, pid: int = 4242) -> None:
        self.commands: list[TerminalCommand] = []
        self.failures = failures
        self.pid = pid

    def launch(self, command: TerminalCommand, *, timeout: float | None = None) -> int:
        self.commands.append(command)
        if self.failures > 0:
            self.failures -= 1
            raise LaunchError("wt.exe 退出码 1")
        return self.pid


def _env(
    runner: Any,
    *,
    launcher: FakeLauncher | None = None,
    config: WslmanConfig | None = None,
    tmp_path: Path | None = None,
) -> wslman_module.Environment:
    path = (tmp_path or Path("C:/fake")) / "wslman.json"
    return wslman_module.Environment(
        runner=runner,
        capabilities=_fakes.capabilities(),
        registry=_fakes.snapshot(),
        config=config or WslmanConfig(path=path),
        platform_ok=True,
        launcher=launcher or FakeLauncher(),
    )


def _use(monkeypatch: pytest.MonkeyPatch, env: wslman_module.Environment) -> None:
    monkeypatch.setattr(wslman_module, "_build_environment", lambda *args, **kwargs: env)


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    code = run_tool("wslman", argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _list(text: str = _TABLE) -> Any:
    return _fakes.make_result(stdout=_fakes.utf16(text))


# ---------------------------------------------------------------------- #
# _terminal：argv 构造与启动器
# ---------------------------------------------------------------------- #


def test_build_command_prefers_wt_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: WT)
    command = _terminal.build_command(
        wsl_path=_fakes.WSL,
        distro="My Distro",
        cwd=r"C:\Users\me\my proj",
        user="dev",
    )
    assert command.terminal == "wt"
    assert command.new_console is False
    assert command.argv == (
        WT,
        "new-tab",
        "--title",
        "wsl:My Distro",
        "--",
        _fakes.WSL,
        "-d",
        "My Distro",
        "--cd",
        r"C:\Users\me\my proj",
        "-u",
        "dev",
    )
    # 含空格的值始终是单个 argv 元素，不依赖 shell 引号。
    assert "My Distro" in command.argv


def test_build_command_extra_args_only_for_wt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: WT)
    command = _terminal.build_command(wsl_path=_fakes.WSL, distro="Ubuntu", extra_args=("--maximized",))
    assert command.argv[:3] == (WT, "new-tab", "--maximized")


def test_build_command_falls_back_to_conhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: None)
    command = _terminal.build_command(wsl_path=_fakes.WSL, distro="Ubuntu")
    assert command.terminal == "conhost"
    assert command.new_console is True
    assert command.wait_for_exit is False
    assert command.argv == (_fakes.WSL, "-d", "Ubuntu")


def test_build_command_forced_wt_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: None)
    with pytest.raises(LaunchError) as excinfo:
        _terminal.build_command(wsl_path=_fakes.WSL, distro="Ubuntu", program="wt")
    assert "wt.exe" in str(excinfo.value)
    assert excinfo.value.hint is not None


def test_build_command_conhost_ignores_wt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: WT)
    command = _terminal.build_command(wsl_path=_fakes.WSL, distro="Ubuntu", program="conhost")
    assert command.terminal == "conhost"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (r"C:\Users\me", True),
        ("C:/Users/me", True),
        ("/home/me", False),
        ("relative/dir", False),
        ("C:relative", False),
    ],
)
def test_uses_windows_path(value: str, expected: bool) -> None:
    assert _terminal.uses_windows_path(value) is expected


def test_find_wt_checks_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_terminal.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert _terminal.find_wt() is None
    apps = tmp_path / "Microsoft" / "WindowsApps"
    apps.mkdir(parents=True)
    (apps / "wt.exe").write_bytes(b"")
    assert _terminal.find_wt() == str(apps / "wt.exe")


def test_subprocess_launcher_runs_and_reports_pid() -> None:
    launcher = _terminal.SubprocessLauncher()
    command = TerminalCommand(
        terminal="conhost",
        argv=(sys.executable, "-c", "raise SystemExit(0)"),
        new_console=False,
        wait_for_exit=True,
    )
    assert launcher.launch(command) > 0


def test_subprocess_launcher_nonzero_exit() -> None:
    launcher = _terminal.SubprocessLauncher()
    command = TerminalCommand(
        terminal="wt",
        argv=(sys.executable, "-c", "raise SystemExit(3)"),
        new_console=False,
        wait_for_exit=True,
    )
    with pytest.raises(LaunchError):
        launcher.launch(command)


def test_subprocess_launcher_missing_program(tmp_path: Path) -> None:
    launcher = _terminal.SubprocessLauncher()
    command = TerminalCommand(
        terminal="wt",
        argv=(str(tmp_path / "missing-wt.exe"),),
        new_console=False,
        wait_for_exit=True,
    )
    with pytest.raises(LaunchError) as excinfo:
        launcher.launch(command)
    assert excinfo.value.hint is not None


def test_subprocess_launcher_does_not_wait_for_wsl() -> None:
    launcher = _terminal.SubprocessLauncher()
    command = TerminalCommand(
        terminal="conhost",
        argv=(sys.executable, "-c", "import time; time.sleep(0.5)"),
        new_console=False,
        wait_for_exit=False,
    )
    assert launcher.launch(command) > 0


def test_subprocess_launcher_timeout_means_started() -> None:
    launcher = _terminal.SubprocessLauncher()
    command = TerminalCommand(
        terminal="wt",
        argv=(sys.executable, "-c", "import time; time.sleep(1)"),
        new_console=False,
        wait_for_exit=True,
    )
    assert launcher.launch(command, timeout=0.2) > 0


# ---------------------------------------------------------------------- #
# 注册
# ---------------------------------------------------------------------- #


def test_registers_p2_subcommands() -> None:
    expected = {"config", "doctor", "info", "list", "open", "set-default", "status", "terminate"}
    assert expected <= set(_TOOL_REGISTRY["wslman"])
    assert expected <= set(list_subcommands("wslman"))


# ---------------------------------------------------------------------- #
# open
# ---------------------------------------------------------------------- #


def test_open_preview_has_zero_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: WT)
    runner = _fakes.FakeRunner({})
    launcher = FakeLauncher()
    _use(monkeypatch, _env(runner, launcher=launcher, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["open", "My Distro", "--preview"])

    assert code == 0
    assert "new-tab" in out
    assert "wsl:My Distro" in out
    assert "[预览]" in out
    assert runner.calls == []
    assert launcher.commands == []


def test_open_preview_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: None)
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["open", "Ubuntu", "--preview", "--json"])

    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == "fcmd-result/1"
    assert payload["data"]["schema"] == "wslman/open/1"
    assert payload["data"]["terminal"] == "conhost"
    assert payload["data"]["preview"] is True
    assert payload["data"]["commands"] == [[_fakes.WSL, "-d", "Ubuntu"]]
    assert runner.calls == []


def test_open_launches_with_wt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: WT)
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    launcher = FakeLauncher(pid=99)
    _use(monkeypatch, _env(runner, launcher=launcher, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["open", "ubuntu"])

    assert code == 0
    assert "已启动（PID 99）" in out
    assert len(launcher.commands) == 1
    assert launcher.commands[0].terminal == "wt"
    # 预览之外执行时才读取列表，并把名称规范化为注册表/列表中的大小写。
    assert launcher.commands[0].argv[5:7] == (_fakes.WSL, "-d")
    assert "Ubuntu" in launcher.commands[0].argv


def test_open_falls_back_to_conhost_when_wt_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: WT)
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    launcher = FakeLauncher(failures=1)
    _use(monkeypatch, _env(runner, launcher=launcher, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["open", "Ubuntu"])

    assert code == 0
    assert len(launcher.commands) == 2
    assert launcher.commands[0].terminal == "wt"
    assert launcher.commands[1].terminal == "conhost"
    assert "已回退新控制台" in out


def test_open_rejects_relative_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({})
    launcher = FakeLauncher()
    _use(monkeypatch, _env(runner, launcher=launcher, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["open", "Ubuntu", "--workdir", "/home/me"])

    assert code == 3
    assert "Windows 路径" in out + err
    assert runner.calls == []
    assert launcher.commands == []


def test_open_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    launcher = FakeLauncher()
    _use(monkeypatch, _env(runner, launcher=launcher, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["open", "openSUSE"])

    assert code == 4
    assert "未找到发行版" in out + err
    assert launcher.commands == []


def test_open_proceeds_when_list_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """只读列表失败不该阻断启动；存在性检查只是尽力而为。"""
    runner = _fakes.FakeRunner({("--list", "--verbose"): _fakes.make_result(returncode=1, stderr=b"boom")})
    launcher = FakeLauncher()
    _use(monkeypatch, _env(runner, launcher=launcher, tmp_path=tmp_path))

    code, _out, _err = _run(capsys, ["open", "Ubuntu"])

    assert code == 0
    assert len(launcher.commands) == 1


def test_open_forced_wt_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_terminal, "find_wt", lambda: None)
    config = WslmanConfig(path=tmp_path / "wslman.json", terminal_program="wt")
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, config=config, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["open", "Ubuntu"])

    assert code == 1
    assert "wt.exe" in out + err


# ---------------------------------------------------------------------- #
# terminate
# ---------------------------------------------------------------------- #


def test_terminate_preview_has_zero_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["terminate", "Ubuntu", "--preview"])

    assert code == 0
    assert "--terminate" in out
    assert "[预览]" in out
    assert runner.calls == []


def test_terminate_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(_TABLE), _list(_STOPPED_TABLE)],
            ("--terminate", "Ubuntu"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["terminate", "Ubuntu"])

    assert code == 0
    assert "已终止 Ubuntu" in out
    assert [call[1:] for call in runner.calls] == [
        ("--list", "--verbose"),
        ("--terminate", "Ubuntu"),
        ("--list", "--verbose"),
    ]


def test_terminate_already_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["terminate", "Debian"])

    assert code == 0
    assert "无需终止" in out
    assert [call[1:] for call in runner.calls] == [("--list", "--verbose")]


def test_terminate_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["terminate", "openSUSE"])

    assert code == 4
    assert "未找到发行版" in out + err


def test_terminate_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): _list(),
            ("--terminate", "Ubuntu"): _fakes.make_result(returncode=1, stderr=b"access denied"),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["terminate", "Ubuntu"])

    assert code == 1
    assert "access denied" in out + err


def test_terminate_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(_TABLE), _list(_STOPPED_TABLE)],
            ("--terminate", "Ubuntu"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["terminate", "Ubuntu", "--json"])

    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/terminate/1"
    assert data["executed"] is True
    assert data["state_before"] == "running"
    assert data["state_after"] == "stopped"
    assert data["commands"] == [[_fakes.WSL, "--terminate", "Ubuntu"]]


# ---------------------------------------------------------------------- #
# set-default
# ---------------------------------------------------------------------- #


def test_set_default_preview_has_zero_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["set-default", "Debian", "--preview"])

    assert code == 0
    assert "--set-default" in out
    assert runner.calls == []


def test_set_default_changes_and_verifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(_TABLE), _list(_DEBIAN_DEFAULT_TABLE)],
            ("--set-default", "Debian"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["set-default", "Debian"])

    assert code == 0
    assert "已将默认发行版从 Ubuntu 改为 Debian" in out
    assert [call[1:] for call in runner.calls] == [
        ("--list", "--verbose"),
        ("--set-default", "Debian"),
        ("--list", "--verbose"),
    ]


def test_set_default_already_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["set-default", "Ubuntu"])

    assert code == 0
    assert "已是默认发行版" in out
    assert [call[1:] for call in runner.calls] == [("--list", "--verbose")]


def test_set_default_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner({("--list", "--verbose"): _list()})
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["set-default", "openSUSE"])

    assert code == 4
    assert "未找到发行版" in out + err


def test_set_default_verification_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(_TABLE), _list(_TABLE)],
            ("--set-default", "Debian"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, err = _run(capsys, ["set-default", "Debian"])

    assert code == 1
    assert "复核" in out + err


def test_set_default_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = _fakes.FakeRunner(
        {
            ("--list", "--verbose"): [_list(_TABLE), _list(_DEBIAN_DEFAULT_TABLE)],
            ("--set-default", "Debian"): _fakes.make_result(),
        }
    )
    _use(monkeypatch, _env(runner, tmp_path=tmp_path))

    code, out, _err = _run(capsys, ["set-default", "Debian", "--json"])

    assert code == 0
    data = json.loads(out)["data"]
    assert data["schema"] == "wslman/set-default/1"
    assert data["previous"] == "Ubuntu"
    assert data["default"] == "Debian"
    assert data["verified"] is True
    assert data["commands"] == [[_fakes.WSL, "--set-default", "Debian"]]
