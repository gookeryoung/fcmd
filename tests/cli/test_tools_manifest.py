"""静态工具清单（B1）一致性、快路径与回退行为测试。

清单是生成文件（:mod:`fcmd.cli._manifest_data`），必须与实跑全量发现严格
一致；任何不一致都会让 ``fcmd <tool>`` 快路径与真实注册表分叉。此处用
隔离子进程跑一次全量发现做对照，避免测试进程内的注册表/导入状态影响结果。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# 子进程内做一次全量发现并输出对照快照（末尾一行 JSON）
_SNAPSHOT_SCRIPT = """
import json
from fcmd.cli._discovery import (
    _TOOL_ALIASES, _TOOL_MODULES, ensure_tools_discovered, tool_description,
)
from fcmd.core.toolkit import _TOOL_REGISTRY

ensure_tools_discovered()
registered = set(_TOOL_REGISTRY)
canonical = sorted(set(_TOOL_ALIASES.values()))
print(json.dumps({
    "modules": _TOOL_MODULES,
    "registered": sorted(registered),
    "aliases": {alias: target for alias, target in _TOOL_ALIASES.items() if alias != target and target in registered},
    "descriptions": {name: tool_description(name) for name in canonical},
}))
"""

# 子进程内对照「清单列表快路径」与「全量发现列表数据」（末尾一行 JSON）
_LIST_ROWS_SCRIPT = """
import json
from fcmd.cli import _manifest
from fcmd.cli._discovery import _TOOL_ALIASES, aliases_for, ensure_tools_discovered, tool_description

rows = _manifest.list_rows()
ensure_tools_discovered()
canonical = sorted(set(_TOOL_ALIASES.values()))
expected = [(name, aliases_for(name), tool_description(name)) for name in canonical]
print(json.dumps({
    "rows": rows,
    "expected": expected,
}))
"""


@pytest.fixture(autouse=True)
def _reset_manifest_cache() -> Iterator[None]:
    """每个用例前后清空清单缓存（用例可能 patch 布局 / 清单模块）。"""
    from fcmd.cli import _manifest

    _manifest.clear_cache()
    yield
    _manifest.clear_cache()


def _run_py(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """在隔离子进程中执行代码（``FCMD_HOME`` 指向不存在的目录）。"""
    env = dict(os.environ)
    env["FCMD_HOME"] = str(tmp_path / "fcmd_home")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def _snapshot(tmp_path: Path) -> dict[str, Any]:
    """跑一次全量发现，返回注册表快照。"""
    proc = _run_py(_SNAPSHOT_SCRIPT, tmp_path)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _data() -> Any:
    from fcmd.cli import _manifest_data

    return _manifest_data


def test_python_entries_match_discovery(tmp_path: Path) -> None:
    """清单 Python 条目与全量发现结果一致（模块路径逐项相同）。"""
    data = _data()
    snap = _snapshot(tmp_path)
    registered = set(snap["registered"])
    modules: dict[str, str] = snap["modules"]
    discovered = {name: path for name, path in modules.items() if name in registered}
    manifest_python = {name: entry.target for name, entry in data.TOOLS.items() if entry.kind == "python"}

    missing = set(discovered) - set(manifest_python)
    assert not missing, f"清单缺少工具 {sorted(missing)}，请运行 python -m fcmd.cli._manifest_data 重新生成"
    for name, path in discovered.items():
        assert manifest_python[name] == path, f"工具 {name!r} 的模块路径与发现结果不一致"
    # 清单多出的条目只允许是「模块存在但当前环境导入失败」（可选依赖缺失），
    # 快路径导入失败会自动回退全量发现；模块映射中都没有说明清单真的过期了。
    extra = set(manifest_python) - set(discovered)
    assert extra <= set(modules), f"清单条目已失效 {sorted(extra - set(modules))}"


def test_aliases_match_discovery(tmp_path: Path) -> None:
    """清单别名表与全量发现登记的别名一致（只比较成功注册的工具）。"""
    data = _data()
    snap = _snapshot(tmp_path)
    registered = set(snap["registered"])
    manifest_aliases = {alias: target for alias, target in data.ALIASES.items() if target in registered}
    assert manifest_aliases == snap["aliases"]


def test_layout_fingerprint_matches_tree() -> None:
    """清单记录的目录布局指纹与当前源码树一致（新增/删除工具模块即过期）。"""
    from fcmd.cli import _manifest

    layout = _manifest.tool_layout()
    assert layout is not None
    assert layout == frozenset(_data().LAYOUT)


def test_project_scripts_cover_every_tool() -> None:
    """``[project.scripts]`` 必须为每个工具（及其别名）生成全局入口。

    清单与布局指纹只覆盖 ``fcmd <tool>`` 的分发，全局命令名来自
    ``pyproject.toml``：漏写一条不会有任何运行期报错，也没有其他测试守护，
    用户侧表现为「工具存在但命令不存在」。与系统命令同名的 ``taskkill`` /
    ``which`` 按 ADR 0002 D2 只保留短名脚本，故显式豁免。
    """
    data = _data()
    scripts = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    exempt = {"taskkill", "which"}
    missing = sorted(set(data.TOOLS) - exempt - set(scripts))
    assert not missing, f"工具缺少全局脚本入口: {missing}"
    unknown = sorted(set(scripts) - set(data.TOOLS) - {"fcmd"} - set(data.ALIASES))
    assert not unknown, f"脚本条目既不是工具也不是别名: {unknown}"


def test_manifest_import_does_not_trigger_discovery(tmp_path: Path) -> None:
    """导入清单数据本身不触发全量发现（快路径的前提）。"""
    proc = _run_py(
        "import sys\n"
        "import fcmd.cli._manifest_data  # noqa: F401\n"
        "import fcmd.cli._discovery as d\n"
        "assert d._TOOLS_DISCOVERED is False\n"
        "assert not [m for m in sys.modules if m.startswith('fcmd.tools.')]\n"
        "print('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"


def test_python_fast_path_imports_only_target(tmp_path: Path) -> None:
    """Python 工具快路径只导入目标模块，不做全量发现。"""
    proc = _run_py(
        "import sys\n"
        "import fcmd.cli._discovery as d\n"
        "assert d.resolve_tool_fast('img2icow') == 'img2icow'\n"
        "assert d._TOOLS_DISCOVERED is False\n"
        "assert d._TOOL_MODULES['img2icow'] == 'fcmd.tools.media.img2icow'\n"
        "assert 'fcmd.tools.llm.llmchat' not in sys.modules\n"
        "assert 'fcmd.tools.dev.pymake' not in sys.modules\n"
        "print('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"


def test_alias_resolves_to_canonical(tmp_path: Path) -> None:
    """别名经清单解析到同一规范名。"""
    data = _data()
    aliases: dict[str, str] = data.ALIASES
    assert aliases, "清单未登记任何别名"
    alias, canonical = next(iter(sorted(aliases.items())))
    proc = _run_py(
        f"import fcmd.cli._discovery as d\nassert d.resolve_tool_fast({alias!r}) == {canonical!r}\nprint('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr


def test_unknown_name_returns_none() -> None:
    """清单未登记的名字返回 None，由调用方回退全量发现。"""
    from fcmd.cli import _discovery

    assert _discovery.resolve_tool_fast("no-such-tool-xyz") is None


def test_missing_manifest_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单模块不可导入时整体失效（回退全量发现）。"""
    from fcmd.cli import _manifest

    monkeypatch.setitem(sys.modules, "fcmd.cli._manifest_data", None)
    assert _manifest.lookup("codetool") is None


def test_stale_layout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """目录布局与清单指纹不一致时整体失效。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "tool_layout", lambda: frozenset({"tools/__new_tool__.py"}))
    assert _manifest.lookup("codetool") is None


def test_missing_tool_module_returns_none() -> None:
    """清单条目指向不存在的模块时返回 None 且不污染注册表映射。"""
    from fcmd.cli import _discovery
    from fcmd.cli._manifest import ToolEntry

    entry = ToolEntry("ghost", "python", "fcmd.tools.__ghost__")
    assert _discovery._load_python_tool(entry) is None
    assert "ghost" not in _discovery._TOOL_MODULES


# ---------------------------------------------------------------------- #
# 列表快路径（fcmd 无参）：静态元数据 + 内容指纹
# ---------------------------------------------------------------------- #
def test_descriptions_match_discovery(tmp_path: Path) -> None:
    """清单描述与全量发现逐条一致（列表页文案的对照口径）。"""
    data = _data()
    snap = _snapshot(tmp_path)
    assert snap["descriptions"] == data.DESCRIPTIONS


def test_digest_matches_sources() -> None:
    """清单内容指纹与当前源码树一致（改任一工具模块即过期）。"""
    from fcmd.cli import _manifest

    assert _manifest.tool_digest() == _data().DIGEST


def test_list_rows_match_discovery(tmp_path: Path) -> None:
    """列表快路径的 (名字, 别名, 描述) 与全量发现逐条一致。"""
    proc = _run_py(_LIST_ROWS_SCRIPT, tmp_path)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["rows"] is not None, "清单严格校验未通过，列表快路径未生效"
    assert payload["rows"] == payload["expected"]


def _capture_listing(app: Any) -> str:
    """执行一次列表并返回标准输出（rich 每次写入动态解析 ``sys.stdout``）。"""
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert app.run() == 0
    return buffer.getvalue()


def test_list_output_matches_full_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fcmd`` 列表输出（快路径）与回退全量发现的输出逐字一致。"""
    from fcmd.cli import _discovery
    from fcmd.cli.main import FcmdApp

    fast = _capture_listing(FcmdApp([]))
    monkeypatch.setattr(_discovery, "list_tool_rows", lambda: None)
    slow = _capture_listing(FcmdApp([]))
    assert fast == slow
    assert "可用工具" in fast


def test_missing_manifest_disables_list_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单模块不可导入时列表回退全量发现。"""
    from fcmd.cli import _manifest

    monkeypatch.setitem(sys.modules, "fcmd.cli._manifest_data", None)
    assert _manifest.list_rows() is None


def test_schema_mismatch_disables_list_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单版本与运行期 ``SCHEMA`` 不一致时列表回退全量发现。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "SCHEMA", _manifest.SCHEMA + 1)
    assert _manifest.list_rows() is None


def test_stale_digest_disables_list_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """内容指纹不一致（改了描述但没重生成清单）时回退全量发现。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "tool_digest", lambda: "stale")
    assert _manifest.load() is not None, "布局校验仍应通过，只有列表路径需要内容指纹"
    assert _manifest.list_rows() is None


def test_unreadable_digest_disables_list_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """源码不可读（返回 ``None``）时列表回退全量发现。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "tool_digest", lambda: None)
    assert _manifest.list_rows() is None


def test_digest_unreadable_source_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具源码读取失败时指纹不可用（不抛异常）。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "_source_files", lambda: [("tools/ghost.py", Path("/nonexistent/ghost.py"))])
    assert _manifest.tool_digest() is None


def test_layout_unreadable_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fcmd/tools`` 目录不可读时布局指纹为 ``None``（清单整体不可用）。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "_PACKAGE_ROOT", Path("/nonexistent/fcmd"))
    assert _manifest.tool_layout() is None
    assert _manifest.lookup("codetool") is None
