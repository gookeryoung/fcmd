"""console script 通用入口（``fcmd.cli._entry``）测试。"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from fcmd.cli import _discovery as discovery_mod
from fcmd.cli._entry import infer_tool_name, run_named


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [
        (r"C:\Python\Scripts\clr.exe", "clr"),
        ("/usr/local/bin/clr", "clr"),
        ("clr", "clr"),
        ("./bin/my-tool", "my-tool"),
    ],
)
def test_infer_tool_name(argv0: str, expected: str) -> None:
    """basename 去扩展推断工具名。"""
    assert infer_tool_name(argv0) == expected


def test_run_named_forwards_to_run_tool(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """run_named 按 argv[0] 推断工具名并转发 run_tool，退出码透传。"""
    seen: dict[str, Any] = {}

    def fake_run_tool(name: str, argv: list[str]) -> int:
        seen["name"] = name
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(discovery_mod, "ensure_tools_discovered", lambda: None)
    monkeypatch.setattr(discovery_mod, "resolve_tool", lambda name: name)
    monkeypatch.setattr("fcmd.core.toolkit.run_tool", fake_run_tool)
    monkeypatch.setattr(sys, "argv", [r"C:\Scripts\clr.exe", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        run_named()
    assert exc_info.value.code == 0
    assert seen == {"name": "clr", "argv": ["--help"]}
    assert capsys.readouterr().out == ""


def test_run_named_unknown_tool_exits_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """入口名未对应已注册工具 → 打印错误并以退出码 2 终止。"""
    monkeypatch.setattr(discovery_mod, "ensure_tools_discovered", lambda: None)
    monkeypatch.setattr(discovery_mod, "resolve_tool", lambda _name: None)
    monkeypatch.setattr(sys, "argv", ["nosuch.exe"])
    with pytest.raises(SystemExit) as exc_info:
        run_named()
    assert exc_info.value.code == 2
    out = capsys.readouterr().out
    assert "未对应任何已注册工具" in out
    assert "查看可用工具列表" in out


def test_run_named_falls_back_to_executable_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """runpy 打包入口（argv[0] 被改写为入口模块路径）时从 sys.executable 回退推断。

    回归测试：fspack 包装器以 run_module(alter_sys=True) 执行入口模块，argv[0]
    沦为入口文件路径；工具名应退回从加载器 exe（sys.executable，如
    img2ico.exe）推断，否则打包后的单工具 exe 静默失效。
    """
    seen: dict[str, Any] = {}

    def fake_run_tool(name: str, argv: list[str]) -> int:
        seen["name"] = name
        seen["argv"] = argv
        return 0

    def fake_resolve(name: str) -> str | None:
        return name if name == "img2ico" else None

    monkeypatch.setattr(discovery_mod, "ensure_tools_discovered", lambda: None)
    monkeypatch.setattr(discovery_mod, "resolve_tool", fake_resolve)
    monkeypatch.setattr("fcmd.core.toolkit.run_tool", fake_run_tool)
    monkeypatch.setattr(sys, "argv", [r"F:\app\dist\src\fcmd\cli\_entry.py", "gen", "in.svg"])
    monkeypatch.setattr(sys, "executable", r"F:\app\dist\img2ico.exe")
    with pytest.raises(SystemExit) as exc_info:
        run_named()
    assert exc_info.value.code == 0
    assert seen == {"name": "img2ico", "argv": ["gen", "in.svg"]}
    assert capsys.readouterr().out == ""


def test_run_path_does_not_load_heavy_libs() -> None:
    """入口 + 工具发现不触发重型库加载。

    回归测试：每个 console script 入口统一经 ``fcmd.cli._entry`` 启动，
    media 模块顶层 eager 导入 SVG 渲染库/PIL、envdev 顶层导入
    urllib.request(→ssl) 会使所有命令启动多付约 230ms。子进程隔离断言
    重型库不出现在 sys.modules。
    """
    code = (
        "import sys\n"
        "import fcmd.cli._entry\n"
        "from fcmd.cli._discovery import ensure_tools_discovered\n"
        "ensure_tools_discovered()\n"
        "heavy = [\n"
        "    m\n"
        "    for m in (\n"
        "        'resvg_py', 'PIL.Image', 'ssl', 'fitz', 'pypdf',\n"
        "        'psutil', 'cryptography', 'pyzipper', 'puremagic', 'coloraide',\n"
        "    )\n"
        "    if m in sys.modules\n"
        "]\n"
        "assert not heavy, f'工具发现阶段加载了重型库: {heavy}'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr


def test_import_fcmd_does_not_load_heavy_libs() -> None:
    """``import fcmd`` 不加载 rich 与任何可选重型库。"""
    code = (
        "import sys\n"
        "import fcmd\n"
        "heavy = [\n"
        "    m\n"
        "    for m in (\n"
        "        'rich', 'resvg_py', 'PIL.Image', 'ssl', 'fitz', 'pypdf',\n"
        "        'psutil', 'cryptography', 'pyzipper', 'puremagic', 'coloraide',\n"
        "    )\n"
        "    if m in sys.modules\n"
        "]\n"
        "assert not heavy, f'import fcmd 加载了重型库: {heavy}'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
