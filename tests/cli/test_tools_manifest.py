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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# 子进程内做一次全量发现并输出对照快照（末尾一行 JSON）
_SNAPSHOT_SCRIPT = """
import json
from fcmd.cli._discovery import _DSL_TOOL_SOURCES, _TOOL_ALIASES, _TOOL_MODULES, ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY

ensure_tools_discovered()
registered = set(_TOOL_REGISTRY)
print(json.dumps({
    "modules": _TOOL_MODULES,
    "registered": sorted(registered),
    "dsl_builtin": sorted(name for name, source in _DSL_TOOL_SOURCES.items() if source == "builtin"),
    "aliases": {alias: target for alias, target in _TOOL_ALIASES.items() if alias != target and target in registered},
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


def test_dsl_entries_match_discovery(tmp_path: Path) -> None:
    """清单 DSL 条目与已注册的内置 DSL 工具一致，且指向的文件确有其声明。"""
    from fcmd.dsl import builtin_tool_decl

    data = _data()
    snap = _snapshot(tmp_path)
    manifest_dsl = {name: entry.target for name, entry in data.TOOLS.items() if entry.kind == "dsl"}
    expected = set(snap["dsl_builtin"]) - set(data.AMBIGUOUS)
    assert set(manifest_dsl) == expected

    for name, file_name in manifest_dsl.items():
        assert builtin_tool_decl(file_name, name) is not None, f"清单把 {name!r} 指向 {file_name}，但该文件无此声明"


def test_ambiguous_names_match_discovery(tmp_path: Path) -> None:
    """同时被 Python 与内置 DSL 声明的工具名集合与实跑发现一致。"""
    data = _data()
    snap = _snapshot(tmp_path)
    registered = set(snap["registered"])
    ambiguous = {name for name in snap["dsl_builtin"] if name in snap["modules"] and name in registered}
    assert ambiguous == set(data.AMBIGUOUS)


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


def test_dsl_fast_path_registers_only_target(tmp_path: Path) -> None:
    """DSL 工具快路径只注册目标工具的声明，不导入任何 Python 工具模块。"""
    proc = _run_py(
        "import sys\n"
        "import fcmd.cli._discovery as d\n"
        "from fcmd.core.toolkit import _TOOL_REGISTRY\n"
        "assert d.resolve_tool_fast('codetool') == 'codetool'\n"
        "assert d._TOOLS_DISCOVERED is False\n"
        "assert set(_TOOL_REGISTRY) == {'codetool'}\n"
        "assert 'fcmd.tools.llm.llmchat' not in sys.modules\n"
        "print('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"


def test_alias_resolves_to_canonical(tmp_path: Path) -> None:
    """别名经清单解析到同一规范名（重名工具除外，它们走全量发现）。"""
    data = _data()
    aliases: dict[str, str] = data.ALIASES
    candidates = {alias: target for alias, target in aliases.items() if target not in data.AMBIGUOUS}
    assert candidates, "清单未登记任何可走快路径的别名"
    alias, canonical = next(iter(sorted(candidates.items())))
    proc = _run_py(
        f"import fcmd.cli._discovery as d\nassert d.resolve_tool_fast({alias!r}) == {canonical!r}\nprint('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr


def test_ambiguous_names_use_full_discovery() -> None:
    """Python 与 DSL 重名的工具不走快路径（子命令需全量合并）。"""
    from fcmd.cli import _discovery

    for name in _data().AMBIGUOUS:
        assert _discovery.resolve_tool_fast(name) is None


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


def test_user_commands_file_disables_dsl_fast_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """用户级 commands.toml 存在时不走 DSL 快路径（用户声明可覆盖内置）。"""
    from fcmd.cli import _discovery

    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    (tmp_path / "commands.toml").write_text('[commands.codetool]\nhelp = "x"\ncmd = ["echo"]\n', encoding="utf-8")
    assert _discovery.resolve_tool_fast("codetool") is None


def test_missing_tool_module_returns_none() -> None:
    """清单条目指向不存在的模块时返回 None 且不污染注册表映射。"""
    from fcmd.cli import _discovery
    from fcmd.cli._manifest import ToolEntry

    entry = ToolEntry("ghost", "python", "fcmd.tools.__ghost__")
    assert _discovery._load_python_tool(entry) is None
    assert "ghost" not in _discovery._TOOL_MODULES
