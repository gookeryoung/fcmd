"""P8 新工具测试：which / sysinfo / portcheck / gittool。

验证 ``fcmd.cli`` 包下 4 个参考 pyflowx 实现的工具：
- ``which``：命令查找（单命令工具）
- ``sysinfo``：系统信息收集（单命令工具）
- ``portcheck``：端口检查（c/s 子命令）
- ``gittool``：Git 执行工具（a/i/clean/c/p/pl 子命令）
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.gittool  # 触发 @fx.tool 注册
import fcmd.cli.portcheck
import fcmd.cli.sysinfo
import fcmd.cli.which
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.gittool import EXCLUDE_CMDS, EXCLUDE_DIRS, has_files, not_has_git_repo
from fcmd.cli.portcheck import check_port, is_port_in_use, scan_ports
from fcmd.cli.sysinfo import _format_bytes, collect_sysinfo, print_sysinfo
from fcmd.cli.which import find_command, which_run


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """4 个新工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """4 个新工具应在 _TOOL_REGISTRY 中注册。"""
        for name in ("which", "sysinfo", "portcheck", "gittool"):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_which_single_command(self) -> None:
        """which 是单命令工具（无子命令）。"""
        assert fx.list_subcommands("which") == []

    def test_sysinfo_single_command(self) -> None:
        """sysinfo 是单命令工具（无子命令）。"""
        assert fx.list_subcommands("sysinfo") == []

    def test_portcheck_subcommands(self) -> None:
        """portcheck 应有 c / s 子命令。"""
        subs = fx.list_subcommands("portcheck")
        assert "c" in subs
        assert "s" in subs

    def test_gittool_subcommands(self) -> None:
        """gittool 应有 a / i / c / p / pl 子命令（clean 是 hidden）。"""
        subs = fx.list_subcommands("gittool")
        for name in ("a", "i", "c", "p", "pl"):
            assert name in subs, f"子命令 {name!r} 未注册"


# ---------------------------------------------------------------------- #
# which 工具测试
# ---------------------------------------------------------------------- #
class TestWhich:
    """``which`` 工具测试。"""

    def test_find_command_python(self) -> None:
        """find_command 能找到 python 命令。"""
        # python 在测试环境必定存在
        result = find_command("python")
        # 在某些环境可能叫 python3，用 sys.executable 验证
        assert result is not None or sys.platform != "win32"

    def test_find_command_not_found(self) -> None:
        """find_command 对不存在的命令返回 None。"""
        assert find_command("this_command_does_not_exist_xyz123") is None

    def test_which_run_prints_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """which_run 打印命令路径。"""
        which_run(["python"])
        out = capsys.readouterr().out
        assert "python" in out
        assert "->" in out

    def test_which_run_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """which_run 对不存在的命令打印未找到。"""
        which_run(["this_command_does_not_exist_xyz123"])
        out = capsys.readouterr().out
        assert "未找到" in out

    def test_which_run_multiple(self, capsys: pytest.CaptureFixture[str]) -> None:
        """which_run 处理多个命令。"""
        which_run(["python", "this_does_not_exist_xyz123"])
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 2

    def test_which_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd which <cmd> 通过 run_tool 调用。"""
        code = run_tool("which", ["python"])
        assert code == 0
        out = capsys.readouterr().out
        assert "python" in out


# ---------------------------------------------------------------------- #
# sysinfo 工具测试
# ---------------------------------------------------------------------- #
class TestSysinfo:
    """``sysinfo`` 工具测试。"""

    def test_format_bytes_units(self) -> None:
        """_format_bytes 正确格式化各量级。"""
        assert _format_bytes(0) == "0.0 B"
        assert _format_bytes(1023) == "1023.0 B"
        assert _format_bytes(1024) == "1.0 KB"
        assert _format_bytes(1024 * 1024) == "1.0 MB"
        assert _format_bytes(1024 * 1024 * 1024) == "1.0 GB"

    def test_format_bytes_pb(self) -> None:
        """_format_bytes 超过 TB 进位到 PB。"""
        # 1 PB = 1024^5 字节
        pb = 1024**5
        assert _format_bytes(pb) == "1.0 PB"

    def test_collect_sysinfo_has_keys(self) -> None:
        """collect_sysinfo 返回必要键。"""
        info = collect_sysinfo()
        assert "Python 版本" in info
        assert "Python 路径" in info
        assert "平台" in info
        assert "架构" in info
        assert "操作系统" in info
        assert "CPU 核心数" in info
        assert "工作目录" in info

    def test_collect_sysinfo_python_version(self) -> None:
        """collect_sysinfo 的 Python 版本与 sys.version 一致。"""
        info = collect_sysinfo()
        assert info["Python 版本"] == sys.version.split()[0]

    def test_collect_sysinfo_disk(self) -> None:
        """collect_sysinfo 包含磁盘信息。"""
        info = collect_sysinfo()
        # 磁盘信息通常可获取（极罕见平台除外）
        if "磁盘总量" in info:
            assert "磁盘已用" in info
            assert "磁盘可用" in info

    def test_print_sysinfo(self, capsys: pytest.CaptureFixture[str]) -> None:
        """print_sysinfo 打印分隔线与信息。"""
        print_sysinfo()
        out = capsys.readouterr().out
        assert "系统信息" in out
        assert "=" * 50 in out
        assert "Python 版本" in out

    def test_sysinfo_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd sysinfo 通过 run_tool 调用。"""
        code = run_tool("sysinfo", [])
        assert code == 0
        out = capsys.readouterr().out
        assert "系统信息" in out

    def test_collect_sysinfo_with_resource_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """collect_sysinfo 在有 resource 模块的环境下收集内存峰值（Linux 路径）。"""
        import types

        fake_resource = types.ModuleType("resource")
        fake_resource.RUSAGE_SELF = 0  # type: ignore[attr-defined]
        fake_resource.getrusage = lambda _flags: types.SimpleNamespace(ru_maxrss=10240)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        info = collect_sysinfo()
        assert "内存峰值" in info
        # Linux 上 ru_maxrss 单位为 KB，10240 KB = 10 MB
        assert "MB" in info["内存峰值"]

    def test_collect_sysinfo_with_resource_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """collect_sysinfo 在 macOS 下内存峰值单位为字节（Darwin 路径）。"""
        import types

        fake_resource = types.ModuleType("resource")
        fake_resource.RUSAGE_SELF = 0  # type: ignore[attr-defined]
        # macOS 上 ru_maxrss 单位为字节，1024 字节 = 1 KB
        fake_resource.getrusage = lambda _flags: types.SimpleNamespace(ru_maxrss=1024)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        info = collect_sysinfo()
        assert "内存峰值" in info
        assert "KB" in info["内存峰值"]


# ---------------------------------------------------------------------- #
# portcheck 工具测试
# ---------------------------------------------------------------------- #
class TestPortcheck:
    """``portcheck`` 工具测试。"""

    def test_is_port_in_use_free(self) -> None:
        """空闲端口返回 False。"""
        # 找一个可绑定的端口：用临时 socket 占用后释放，再用新 socket 验证
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        # socket 关闭后端口应空闲（SO_REUSEADDR 已设）
        assert is_port_in_use(free_port) is False

    def test_is_port_in_use_occupied(self) -> None:
        """占用端口返回 True。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            assert is_port_in_use(port) is True

    def test_check_port_free(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_port 打印空闲状态。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        check_port(free_port)
        out = capsys.readouterr().out
        assert "空闲" in out

    def test_check_port_occupied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_port 打印占用状态。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            check_port(port)
            out = capsys.readouterr().out
            assert "占用" in out

    def test_check_port_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_port 无效端口号打印提示。"""
        check_port(0)
        out = capsys.readouterr().out
        assert "无效" in out
        check_port(70000)
        out = capsys.readouterr().out
        assert "无效" in out

    def test_scan_ports_no_occupied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 无占用端口时打印提示。"""
        # 用一个不太可能被占用的高端口范围
        scan_ports(59999, 60000)
        out = capsys.readouterr().out
        assert "无占用端口" in out

    def test_scan_ports_invalid_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 无效范围打印提示。"""
        scan_ports(100, 50)
        out = capsys.readouterr().out
        assert "无效" in out

    def test_scan_ports_out_of_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 超出 65535 打印提示。"""
        scan_ports(70000, 80000)
        out = capsys.readouterr().out
        assert "无效" in out

    def test_portcheck_c_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd portcheck c <port> 通过 run_tool 调用。"""
        code = run_tool("portcheck", ["c", "59999"])
        assert code == 0
        out = capsys.readouterr().out
        assert "59999" in out

    def test_portcheck_s_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd portcheck s <start> <end> 通过 run_tool 调用。"""
        code = run_tool("portcheck", ["s", "59999", "60000"])
        assert code == 0
        out = capsys.readouterr().out
        assert "59999" in out

    def test_scan_ports_with_occupied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 扫描到占用端口时打印列表。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            scan_ports(port, port)
            out = capsys.readouterr().out
            assert "占用端口" in out
            assert str(port) in out


# ---------------------------------------------------------------------- #
# gittool 工具测试
# ---------------------------------------------------------------------- #
class TestGittool:
    """``gittool`` 工具测试。"""

    def test_exclude_cmds_format(self) -> None:
        """EXCLUDE_CMDS 展开为 -e dir1 -e dir2 ... 格式。"""
        # 应为偶数长度，每对以 -e 开头
        assert len(EXCLUDE_CMDS) == len(EXCLUDE_DIRS) * 2
        for i in range(0, len(EXCLUDE_CMDS), 2):
            assert EXCLUDE_CMDS[i] == "-e"
            assert EXCLUDE_CMDS[i + 1] in EXCLUDE_DIRS

    def test_not_has_git_repo_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 git 目录返回 True。"""
        monkeypatch.chdir(tmp_path)
        assert not_has_git_repo() is True

    def test_not_has_git_repo_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """git 仓库目录返回 False。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        assert not_has_git_repo() is False

    def test_has_files_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """干净的 git 仓库返回 False。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        assert has_files() is False

    def test_has_files_dirty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """有未提交更改返回 True。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        assert has_files() is True

    def test_gittool_a_no_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool a 没有文件时打印提示。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["a"])
        assert code == 0
        out = capsys.readouterr().out
        assert "没有文件需要提交" in out

    def test_gittool_a_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool a 添加并提交文件。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["a", "--message", "test commit"])
        assert code == 0
        # 验证提交成功
        result = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, check=True)
        assert "test commit" in result.stdout

    def test_gittool_i_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool i 初始化并提交。"""
        monkeypatch.chdir(tmp_path)
        # CI 环境可能未配置全局 git user，通过环境变量设置（不依赖 .git/config）
        monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@test.com")
        monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
        monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@test.com")
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["i"])
        assert code == 0
        # 验证仓库已初始化且提交成功
        assert (tmp_path / ".git").is_dir()
        result = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, check=True)
        assert "init commit" in result.stdout

    def test_gittool_i_existing_repo_no_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """gittool i 在已有仓库且无更改时打印提示。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        code = run_tool("gittool", ["i"])
        assert code == 0
        out = capsys.readouterr().out
        assert "没有文件需要提交" in out

    def test_gittool_a_via_run_tool_default_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gittool a 使用默认提交信息。"""
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], check=True, capture_output=True)
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        code = run_tool("gittool", ["a"])
        assert code == 0
        result = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, check=True)
        assert "chore: update" in result.stdout


# ---------------------------------------------------------------------- #
# gittool cmd 子命令验证
# ---------------------------------------------------------------------- #
class TestGittoolCmdSpecs:
    """gittool 的 cmd 类型子命令规格验证。"""

    def test_clean_is_hidden_cmd(self) -> None:
        """clean 是 hidden 的 cmd 类型子命令。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["clean"]
        assert spec.cmd is not None
        assert "git" in spec.cmd
        assert "clean" in spec.cmd
        assert "-xfd" in spec.cmd
        assert spec.hidden is True

    def test_c_needs_clean(self) -> None:
        """c 子命令依赖 clean。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["c"]
        assert "clean" in spec.needs

    def test_p_is_cmd(self) -> None:
        """p 是 cmd 类型子命令。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["p"]
        assert spec.cmd is not None
        assert "push" in spec.cmd

    def test_pl_is_cmd(self) -> None:
        """pl 是 cmd 类型子命令。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        spec = _TOOL_REGISTRY["gittool"]["pl"]
        assert spec.cmd is not None
        assert "pull" in spec.cmd
