"""sshcopyid 工具测试（DSL 声明 commands/sshcopyid.toml）。

验证 ``fcmd sshcopyid`` 的跨平台双分支语义：

- Linux / macOS 分支（unix）：shell 字符串命令，sshpass -e 从 SSHPASS 环境
  变量读密码（密码不进命令行 argv），公钥文件通过 shell stdin 重定向
  送进远端 cat >> authorized_keys，**公钥内容完全不插值进远端 bash
  命令字符串**（避免 echo '{key}' 的引号断裂与 awk 判重复杂度）。
- Windows 分支（win）：纯 ssh（无 sshpass 依赖），shell 字符串命令，
  同样通过 stdin 重定向传公钥；tty=true 让 SSH 密码交互透传终端
  （用户手动输一次密码）；不设置 SSHPASS 环境变量。

DSL 平台级覆盖契约：
- CommandDecl.win_cmd/win_tty/win_env 按平台覆盖顶层；
- build_tool_spec(platform="win32") 产出 ToolSpec 的 passthrough=True、
  env 不含 SSHPASS、cmd 是纯 ssh 命令；
- build_tool_spec(platform="linux") 产出 ToolSpec 的 env 含 SSHPASS、
  passthrough=False、cmd 以 sshpass -e 开头（shell 字符串）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.dsl.decl import parse_command_table
from fcmd.dsl.synth import build_tool_spec

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 sshcopyid）


# ---------------------------------------------------------------------- #
# 测试辅助
# ---------------------------------------------------------------------- #
def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    """构造捕获 cmd 与 kwargs 的 subprocess.run 替身。"""

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


def _write_key(tmp_path: Path, content: str = "ssh-rsa AAAAB3NzaC1yc2E test@example.com\n") -> Path:
    """写入测试公钥文件。"""
    key_file = tmp_path / "id_rsa.pub"
    key_file.write_text(content, encoding="utf-8")
    return key_file


def _load_sshcopyid_decl():
    """直接从 toml 文件解析出 CommandDecl（绕开 _discovery 的缓存路径）。"""
    import tomllib

    toml_path = Path(__file__).resolve().parents[1] / "src" / "fcmd" / "commands" / "sshcopyid.toml"
    table = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    return parse_command_table("sshcopyid", table["commands"]["sshcopyid"])


# ---------------------------------------------------------------------- #
# 注册与声明契约
# ---------------------------------------------------------------------- #
class TestSshcopyidRegistration:
    """sshcopyid 经内置 DSL 注册，平台覆盖字段合成正确。"""

    def test_registered_as_dsl(self) -> None:
        """sshcopyid 注册为内置 DSL 单命令工具。"""
        assert "sshcopyid" in _TOOL_REGISTRY
        assert fx.list_subcommands("sshcopyid") == []

    def test_decl_contract(self) -> None:
        """when 探针 / 完成 / 失败消息经函数属性注入。"""
        spec = _TOOL_REGISTRY["sshcopyid"][None]
        when = getattr(spec.func, "__dsl_when__", None)
        assert when is not None and when.path == "{keypath}" and when.expect == "exists"
        assert getattr(spec.func, "__dsl_message__", "") == "SSH 密钥已部署到 {username}@{hostname}:{port}"
        assert (
            getattr(spec.func, "__dsl_fail_message__", "")
            == "部署失败，可手动执行: ssh-copy-id -p {port} {username}@{hostname}"
        )

    def test_platform_coverage(self) -> None:
        """DSL CommandDecl 含 unix_cmd/win_cmd + 平台级 env/tty 覆盖字段。"""
        decl = _load_sshcopyid_decl()
        assert decl.unix_cmd is not None
        assert decl.win_cmd is not None
        assert decl.unix_env == {"SSHPASS": "{password}"}
        assert decl.win_env is None  # Windows 分支不注入 SSHPASS
        assert decl.unix_tty is None  # Linux 分支继承顶层默认 False
        assert decl.win_tty is True  # Windows 分支强制交互式

    def test_unix_cmd_is_shell_string(self) -> None:
        """unix.cmd 是 shell 字符串（触发 shell=True 执行 stdin 重定向）。"""
        decl = _load_sshcopyid_decl()
        assert isinstance(decl.unix_cmd, str)
        assert decl.unix_cmd.startswith("sshpass -e ssh")

    def test_win_cmd_is_pure_ssh_shell_string(self) -> None:
        """win.cmd 是 shell 字符串，ssh 直接起头（无 sshpass 依赖）。"""
        decl = _load_sshcopyid_decl()
        assert isinstance(decl.win_cmd, str)
        assert decl.win_cmd.startswith("ssh -p {port}")


# ---------------------------------------------------------------------- #
# build_tool_spec 合成层平台覆盖
# ---------------------------------------------------------------------- #
class TestSshcopyidBuildSpec:
    """build_tool_spec 按平台正确选 cmd/tty/env。"""

    def test_linux_platform(self) -> None:
        """Linux：cmd 含 sshpass -e，env 注入 SSHPASS，passthrough=False。"""
        decl = _load_sshcopyid_decl()
        spec = build_tool_spec(decl, platform="linux")
        assert spec.cmd is not None
        assert isinstance(spec.cmd, str) and spec.cmd.startswith("sshpass -e")
        assert spec.env is not None and spec.env["SSHPASS"] == "{password}"
        assert spec.passthrough is False

    def test_win32_platform(self) -> None:
        """Win32：cmd 是纯 ssh，env 不含 SSHPASS，passthrough=True。"""
        decl = _load_sshcopyid_decl()
        spec = build_tool_spec(decl, platform="win32")
        assert spec.cmd is not None
        assert isinstance(spec.cmd, str) and spec.cmd.startswith("ssh -p")
        assert "sshpass" not in spec.cmd
        assert spec.env is None or "SSHPASS" not in spec.env
        assert spec.passthrough is True

    def test_darwin_platform_follows_unix(self) -> None:
        """darwin 与 linux 同走 unix 分支。"""
        decl = _load_sshcopyid_decl()
        spec = build_tool_spec(decl, platform="darwin")
        assert spec.cmd is not None
        assert isinstance(spec.cmd, str) and spec.cmd.startswith("sshpass -e")
        assert spec.passthrough is False

    def test_env_merge_platform_overrides_top(self) -> None:
        """平台级 env 覆盖顶层 env 同名键（平台级优先）。"""
        from dataclasses import replace

        base = _load_sshcopyid_decl()
        merged = replace(
            base,
            env={"EXIST": "top", "SSHPASS": "top_sshpass"},
            unix_env={"SSHPASS": "plat_sshpass"},
        )
        spec = build_tool_spec(merged, platform="linux")
        assert spec.env is not None
        assert spec.env["EXIST"] == "top"  # 顶层独有键保留
        assert spec.env["SSHPASS"] == "plat_sshpass"  # 平台级覆盖同名键


# ---------------------------------------------------------------------- #
# 执行语义（当前平台 = linux）
# ---------------------------------------------------------------------- #
class TestSshcopyidRun:
    """``fcmd sshcopyid`` 在当前 Linux 平台的执行语义。"""

    def test_success_deploys_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """成功部署：密码走 SSHPASS 环境变量（不进命令行），公钥文件通过 stdin 重定向送远端。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        code = run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file)])
        assert code == 0

        cmd = captured[0][0]
        assert isinstance(cmd, str)  # shell 字符串模式
        assert cmd.startswith("sshpass -e ssh")
        assert " -p 22 " in cmd
        assert "ConnectTimeout=30" in cmd
        assert "user@host" in cmd
        # stdin 重定向把公钥文件送进 ssh，远端脚本从 stdin 读 → 公钥内容不插值进远端 bash 命令
        assert '< "' + str(key_file) + '"' in cmd
        # 远端脚本统一 cat >> authorized_keys，不再 echo/awk 判重
        assert "cat >> ~/.ssh/authorized_keys" in cmd
        assert "awk" not in cmd and "grep" not in cmd

        # SSHPASS 经 env 传递
        run_kwargs = captured[0][1]
        assert run_kwargs["env"]["SSHPASS"] == "pass"
        # shell=True（字符串命令走 shell）
        assert run_kwargs.get("shell") is True

        assert "SSH 密钥已部署到 user@host:22" in capsys.readouterr().out

    def test_tilde_keypath_expanded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """默认 ``~`` keypath 在插值前展开为绝对路径。

        引号内的 ``~`` 不会被 shell 展开，Windows cmd.exe 也不识别 ``~``——
        未展开会直接报 "系统找不到指定的路径"。
        """
        home = tmp_path / "home"
        key_file = home / ".ssh" / "id_rsa.pub"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("ssh-rsa AAAAB3NzaC1yc2E test@example.com\n", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))  # Windows expanduser 回退

        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host", "user", "pass"]) == 0

        cmd = captured[0][0]
        assert '< "~' not in cmd  # 本地公钥路径已展开（远端 ~/.ssh 不受影响）
        assert '< "' + str(key_file) + '"' in cmd
        assert "SSH 密钥已部署到 user@host:22" in capsys.readouterr().out

    def test_custom_port_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--port / --timeout 插值进命令行。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        assert (
            run_tool(
                "sshcopyid", ["host", "user", "pass", "--port", "2222", "--timeout", "5", "--keypath", str(key_file)]
            )
            == 0
        )
        cmd = captured[0][0]
        assert " -p 2222 " in cmd
        assert "ConnectTimeout=5" in cmd
        assert "SSH 密钥已部署到 user@host:2222" in capsys.readouterr().out

    def test_missing_key_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """公钥缺失：when 探针跳过执行，退出码 0，不打印完成/失败消息。"""
        missing = tmp_path / "no-such-key.pub"
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(missing)]) == 0
        assert captured == []
        out = capsys.readouterr().out
        assert "已部署" not in out
        assert "手动执行" not in out

    def test_failure_prints_fail_message(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """部署失败：退出码 1，打印手动执行提示（行为变化：原版恒 exit 0）。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr(
            "fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured, returncode=1)
        )
        assert run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file)]) == 1
        out = capsys.readouterr().out
        assert "部署失败，可手动执行: ssh-copy-id -p 22 user@host" in out
        assert "SSH 密钥已部署" not in out

    def test_dry_run_skips_execution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--dry-run 不执行命令、不打印消息。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file), "--dry-run"]) == 0
        assert captured == []
        assert "已部署" not in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# Windows 分支（monkeypatch sys.platform）
# ---------------------------------------------------------------------- #
class TestSshcopyidWinBranch:
    """monkeypatch sys.platform='win32' 后验证执行路径。"""

    def test_win_cmd_pure_ssh_no_sshpass(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Win32 分支 subprocess.run 收到纯 ssh 命令，env 不含 SSHPASS。"""
        monkeypatch.setattr(sys, "platform", "win32")
        # 强制刷新 ToolSpec 缓存（_discovery 已注册过当前平台版本）
        _update_registry_for_platform("sshcopyid", "win32")

        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        code = run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file)])
        assert code == 0

        cmd = captured[0][0]
        assert isinstance(cmd, str)
        assert cmd.startswith("ssh -p")
        assert "sshpass" not in cmd
        assert "user@host" in cmd
        assert '< "' + str(key_file) + '"' in cmd  # stdin 重定向

        run_kwargs = captured[0][1]
        # Win32 分支不注入 SSHPASS 环境变量（env 为 None 即表示无额外注入）
        env = run_kwargs["env"]
        assert env is None or env.get("SSHPASS") != "pass"
        # Win32 分支 passthrough=True → capture_output=False
        assert run_kwargs.get("capture_output") is False


# ---------------------------------------------------------------------- #
# OpenSSH vs plink 兼容：resolve_windows_openssh + build_tool_spec 路径替换
# ---------------------------------------------------------------------- #
class TestWindowsOpenSshResolution:
    """resolve_windows_openssh 检测逻辑 + build_tool_spec 对 win32 的 ssh 路径替换。

    背景：Windows 用户 PATH 中 ``plink.exe``（PuTTY）常排在 OpenSSH
    ``ssh.exe`` 前面，导致 ``ssh -p ... -o ...`` 被 plink 执行。plink
    不识别 ``-p``（用 ``-P`` 大写）、``-o StrictHostKeyChecking=no`` 等
    OpenSSH 参数。修复方案：在 synth 层检测真正的 OpenSSH 路径并替换
    shell 命令模板中的裸 ``ssh``。
    """

    def _reset_cache(self) -> None:
        """清除 resolve_windows_openssh 的 lru_cache（测试隔离必需）。"""
        from fcmd.dsl.synth import resolve_windows_openssh

        resolve_windows_openssh.cache_clear()

    # --- resolve_windows_openssh ---

    def test_non_win32_platform_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 win32 平台直接返回 None，不做任何检测（Linux/macOS 不涉及 PATH plink 劫持）。"""
        self._reset_cache()
        monkeypatch.setattr(sys, "platform", "linux")
        from fcmd.dsl.synth import resolve_windows_openssh

        assert resolve_windows_openssh() is None

    def test_system32_openssh_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 标准安装路径 ``System32\\OpenSSH\\ssh.exe`` 存在时直接返回。"""
        self._reset_cache()
        monkeypatch.setattr(sys, "platform", "win32")

        monkeypatch.setattr(Path, "is_file", lambda _self: True)

        # ssh -V 版本检测也需要返回 OpenSSH（标准路径命中后仍需通过版本校验）
        import subprocess as _sp

        monkeypatch.setattr(
            "fcmd.dsl.synth.subprocess.run",
            lambda cmd, **kw: _sp.CompletedProcess(
                cmd, 0, stdout="", stderr="OpenSSH_for_Windows_8.1p1 LibreSSL, 24 Jan 2022\n"
            ),
        )

        from fcmd.dsl.synth import resolve_windows_openssh

        result = resolve_windows_openssh()
        # Linux 上 PosixPath 把 C:\... 路径解析时反斜杠转义丢失，
        # 只需断言返回值含 OpenSSH\ssh.exe（核心语义）
        assert result is not None
        assert "OpenSSH" in result
        assert result.lower().endswith("ssh.exe")

    def test_plink_in_path_skipped_openssh_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PATH 中 plink.exe 先被命中但 ssh -V 显示 PuTTY 版本 → 跳过，
        继续找 PATH 中的真正 OpenSSH。"""
        self._reset_cache()
        monkeypatch.setattr(sys, "platform", "win32")

        # 标准 System32 路径不存在，强制走 where ssh 分支
        monkeypatch.setattr(Path, "is_file", lambda _self: False)

        plink_path = r"C:\PuTTY\plink.exe"
        openssh_path = r"C:\Tools\OpenSSH\ssh.exe"

        def fake_subprocess_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            import subprocess as _sp

            if cmd and cmd[0] == "where":
                # where ssh 返回 PATH 中按顺序的 .exe：plink 优先
                return _sp.CompletedProcess(cmd, 0, stdout=f"{plink_path}\n{openssh_path}\n", stderr="")
            # ssh -V：plink 输出 PuTTY 版本，openssh 输出 OpenSSH 版本
            if cmd and str(cmd[0]) == plink_path:
                return _sp.CompletedProcess(
                    cmd, 0, stdout="", stderr="plink: PuTTY-Open Source SSH client, version 0.78\n"
                )
            if cmd and str(cmd[0]) == openssh_path:
                return _sp.CompletedProcess(
                    cmd, 0, stdout="", stderr="OpenSSH_for_Windows_8.1p1 LibreSSL, 24 Jan 2022\n"
                )

            return _sp.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr("fcmd.dsl.synth.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr(Path, "is_file", lambda _self: True)

        from fcmd.dsl.synth import resolve_windows_openssh

        result = resolve_windows_openssh()
        assert result == openssh_path

    def test_no_openssh_anywhere_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全环境找不到 OpenSSH → 返回 None（裸 ssh 可能仍被 plink 劫持，属用户环境问题）。"""
        self._reset_cache()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "is_file", lambda _self: False)

        def fake_subprocess_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            import subprocess as _sp

            # where ssh 找不到（无 .exe）
            return _sp.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr("fcmd.dsl.synth.subprocess.run", fake_subprocess_run)

        from fcmd.dsl.synth import resolve_windows_openssh

        assert resolve_windows_openssh() is None

    # --- build_tool_spec 路径替换 ---

    def test_win32_build_spec_replaces_bare_ssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """win32 平台 build_tool_spec 发现 OpenSSH 后，把 cmd 开头裸 ``ssh`` 替换
        为完整路径——plink 劫持场景下命令实际调用真正的 OpenSSH。"""
        self._reset_cache()
        monkeypatch.setattr(sys, "platform", "win32")

        fake_openssh = r"C:\Windows\System32\OpenSSH\ssh.exe"
        monkeypatch.setattr("fcmd.dsl.synth.resolve_windows_openssh", lambda: fake_openssh)

        decl = _load_sshcopyid_decl()
        spec = build_tool_spec(decl, platform="win32")

        assert spec.cmd is not None
        assert isinstance(spec.cmd, str)
        assert spec.cmd.startswith(fake_openssh + " ")
        assert " -p {port} " in spec.cmd  # 模板占位符保留，后续 OpenSSH 参数完整
        assert "StrictHostKeyChecking" in spec.cmd

    def test_linux_build_spec_no_replacement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 win32 平台不做 ssh 路径替换，unix_cmd 保持原样。"""
        monkeypatch.setattr(sys, "platform", "linux")
        # resolve_windows_openssh 在非 win32 返回 None，不应触发替换

        decl = _load_sshcopyid_decl()
        spec = build_tool_spec(decl, platform="linux")

        assert spec.cmd is not None
        assert isinstance(spec.cmd, str)
        assert spec.cmd.startswith("sshpass -e ssh ")  # unix 分支 sshpass 前缀完整保留

    def test_win32_no_openssh_fallback_bare_ssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """win32 平台但 resolve_windows_openssh 返回 None 时，回退裸 ssh（
        用户环境未装 OpenSSH，可能被 plink 劫持，属用户需自行处理的边缘场景）。"""
        self._reset_cache()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("fcmd.dsl.synth.resolve_windows_openssh", lambda: None)

        decl = _load_sshcopyid_decl()
        spec = build_tool_spec(decl, platform="win32")

        assert spec.cmd is not None
        assert isinstance(spec.cmd, str)
        assert spec.cmd.startswith("ssh -p")  # 回退裸 ssh，不替换

    def test_lru_cache_prevents_repeated_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """resolve_windows_openssh 的 lru_cache 生效：首次调用做检测，后续
        直接返回缓存值（同一进程内 ssh 路径不会变化）。"""
        self._reset_cache()
        monkeypatch.setattr(sys, "platform", "win32")

        calls: list[tuple[Any, Any]] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            import subprocess as _sp

            return _sp.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr("fcmd.dsl.synth.subprocess.run", fake_run)
        monkeypatch.setattr(Path, "is_file", lambda _self: False)

        from fcmd.dsl.synth import resolve_windows_openssh

        first = resolve_windows_openssh()
        second = resolve_windows_openssh()

        assert first == second is None
        # 缓存命中 → 第二次直接返回，calls 里只有第一次 where ssh 调用
        assert len(calls) == 1


# ---------------------------------------------------------------------- #
# 内部：强制刷新注册表中某工具的 ToolSpec（换平台时重建）
# ---------------------------------------------------------------------- #
def _update_registry_for_platform(tool_name: str, platform: str) -> None:
    """从 toml 重新 build_tool_spec（指定 platform），替换 _TOOL_REGISTRY 缓存。

    _discovery 初始化时用 ``sys.platform`` 合成 ToolSpec，monkeypatch 后
    不会自动重建；此函数强制按指定平台重建并写回注册表。
    """
    from fcmd.core.toolkit import ToolSpec

    decl = _load_sshcopyid_decl()
    new_spec: ToolSpec = build_tool_spec(decl, platform=platform)
    _TOOL_REGISTRY[tool_name] = {None: new_spec}
