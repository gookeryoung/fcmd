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
from types import SimpleNamespace
from typing import Any, cast

import pytest

from fcmd.cli import _discovery
from fcmd.core import toolkit
from fcmd.infras import _toml as tomllib

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
    "tool_count": len(canonical),
    "subcommand_total": sum(len(subs) for subs in _TOOL_REGISTRY.values()),
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


# 子进程内按「非终端、80 列宽」渲染各工具顶层 --help（末尾一行 JSON）
_HELP_SCRIPT = """
import contextlib, io, json, os
os.environ["COLUMNS"] = "80"
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool

ensure_tools_discovered()
texts = {}
for name in sorted(_TOOL_REGISTRY):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        run_tool(name, ["--help"])
    texts[name] = buffer.getvalue()
print(json.dumps(texts))
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
    # 强制覆盖父进程可能设置的 PYTHONIOENCODING=cp936，与下方 utf-8 解码保持一致。
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
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


def test_counts_match_discovery(tmp_path: Path) -> None:
    """清单固化的工具数 / 子命令总数与全量发现一致（``fcmd env`` 直接取用）。"""
    data = _data()
    snap = _snapshot(tmp_path)
    assert data.TOOL_COUNT == snap["tool_count"] == len(data.TOOLS)
    assert snap["subcommand_total"] == data.SUBCOMMAND_TOTAL


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
        "assert 'fcmd.apps.llm.llmchat' not in sys.modules\n"
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


def test_graph_fast_path_skips_full_discovery(tmp_path: Path) -> None:
    """``fcmd graph <tool>`` 只按清单解析目标工具，不触发全量发现。"""
    proc = _run_py(
        "import sys\n"
        "from fcmd.cli._builtins import graph_cmd\n"
        "def _boom():\n"
        "    raise AssertionError('graph 触发了全量发现')\n"
        "graph_cmd.ensure_tools_discovered = _boom\n"
        "assert graph_cmd.run(['pymake', 'tc', '--format=mermaid']) == 0\n"
        "assert 'fcmd.tools.dev.pymake' in sys.modules\n"
        "assert 'fcmd.apps.llm.llmchat' not in sys.modules\n"
        "print('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"


def test_graph_falls_back_to_discovery_when_manifest_misses(tmp_path: Path) -> None:
    """清单未命中（如清单过期）时 graph 回退全量发现，行为不变。"""
    proc = _run_py(
        "from fcmd.cli import _manifest\n"
        "_manifest.lookup = lambda name: None\n"
        "from fcmd.cli._builtins import graph_cmd\n"
        "calls = []\n"
        "real = graph_cmd.ensure_tools_discovered\n"
        "def _spy():\n"
        "    calls.append(True)\n"
        "    real()\n"
        "graph_cmd.ensure_tools_discovered = _spy\n"
        "assert graph_cmd.run(['pymake', 'tc', '--format=layers']) == 0\n"
        "assert calls, '清单未命中时必须回退全量发现'\n"
        "print('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"


def test_info_overview_still_discovers_tools(tmp_path: Path) -> None:
    """``fcmd info --json`` 自己触发全量发现（main 不再统一发现）。"""
    proc = _run_py(
        "import io, json\n"
        "from contextlib import redirect_stdout\n"
        "from fcmd.cli import _manifest_data\n"
        "from fcmd.cli._builtins import info_cmd\n"
        "buffer = io.StringIO()\n"
        "with redirect_stdout(buffer):\n"
        "    assert info_cmd.run(['--json']) == 0\n"
        "data = json.loads(buffer.getvalue())\n"
        "assert len(data['tools']) == len(_manifest_data.TOOLS)\n"
        "print('ok')\n",
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "ok"


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


def test_strict_validation_scans_tree_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """严格校验的布局与内容指纹共用一次目录扫描（``tools`` / ``apps`` 各一次）。

    两者各扫一遍会多付一次 ``iterdir`` + ``is_dir``（本机 123 个候选文件约 6.8ms），
    这里用调用计数守住「单次遍历」。
    """
    from fcmd.cli import _manifest

    calls: list[str] = []
    real = _manifest._collect_candidates

    def _counting(root_name: str, scan_root: Path) -> list[tuple[str, Path]]:
        calls.append(root_name)
        return real(root_name, scan_root)

    monkeypatch.setattr(_manifest, "_collect_candidates", _counting)
    manifest = _manifest.load_strict()
    assert manifest is not None
    assert calls == ["tools", "apps"]


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


# ---------------------------------------------------------------------- #
# 发现流程的容错分支
# ---------------------------------------------------------------------- #
def test_scan_root_skips_missing_root(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def _raise(name: str) -> None:
        raise ImportError(f"no module {name}")

    caplog.set_level("WARNING", logger=_discovery.logger.name)
    monkeypatch.setattr(_discovery.importlib, "import_module", _raise)

    _discovery._scan_root("fcmd.tools.missing")

    assert "不可用" in caplog.text
    assert "fcmd.tools.missing" in caplog.text


def test_scan_root_registers_top_level_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(_discovery.importlib, "import_module", lambda name: SimpleNamespace(__path__=[]))
    monkeypatch.setattr(
        _discovery.pkgutil, "iter_modules", lambda path: [(None, "alpha", False), (None, "_private", False)]
    )
    monkeypatch.setattr(
        _discovery, "_register_tool", lambda module_path, tool_name: calls.append((module_path, tool_name))
    )

    _discovery._scan_root("fcmd.tools")

    assert calls == [("fcmd.tools.alpha", "alpha")]


def test_register_app_skips_oserror(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def _raise(name: str) -> None:
        raise OSError("dll missing")

    caplog.set_level("WARNING", logger=_discovery.logger.name)
    monkeypatch.setattr(_discovery.importlib, "import_module", _raise)

    _discovery._register_app("fcmd.apps.broken")

    assert "导入失败" in caplog.text
    assert "dll missing" in caplog.text


def test_discover_domain_skips_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(name: str) -> None:
        raise ImportError("optional dependency")

    monkeypatch.setattr(_discovery.importlib, "import_module", _raise)
    monkeypatch.setattr(_discovery.pkgutil, "iter_modules", lambda path: pytest.fail("should not scan"))

    _discovery._discover_domain("fcmd.tools", "broken")


def test_register_tool_keeps_cached_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    aliases = {"ghost": "cached"}
    monkeypatch.setattr(_discovery, "_TOOL_MODULES", {})
    monkeypatch.setattr(_discovery, "_TOOL_ALIASES", aliases)
    monkeypatch.setattr(toolkit, "_TOOL_REGISTRY", {})
    monkeypatch.setattr(_discovery.importlib, "import_module", lambda name: SimpleNamespace())

    _discovery._register_tool("fcmd.tools.ghost", "ghost")

    assert _discovery._TOOL_MODULES == {}
    assert aliases == {"ghost": "cached"}


def test_seed_main_module_alias_without_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "__main__", None)

    _discovery._seed_main_module_alias("fcmd.tools.not-registered")

    assert "fcmd.tools.not-registered" not in sys.modules


def test_seed_main_module_alias_without_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "__main__", SimpleNamespace(__file__=""))

    _discovery._seed_main_module_alias("fcmd.tools.not-registered")

    assert "fcmd.tools.not-registered" not in sys.modules


def test_seed_main_module_alias_ignores_find_spec_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "__main__", SimpleNamespace(__file__="entry.py"))

    def fail(_name: str) -> object:
        raise ValueError("bad")

    # 只替换 _discovery 自己的 importlib 引用：改全局 importlib.util.find_spec
    # 会波及同进程的 coverage，导致它在 tracing 时抛错、worker 崩溃。
    monkeypatch.setattr(_discovery, "importlib", SimpleNamespace(util=SimpleNamespace(find_spec=fail)))

    _discovery._seed_main_module_alias("fcmd.tools.not-registered")

    assert "fcmd.tools.not-registered" not in sys.modules


def test_resolve_tool_fast_returns_none_when_loader_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.cli import _manifest

    entry = SimpleNamespace(name="ghost", target="fcmd.tools.ghost")
    monkeypatch.setattr(_manifest, "lookup", lambda name: entry)
    monkeypatch.setattr(_discovery, "_load_python_tool", lambda entry: None)

    assert _discovery.resolve_tool_fast("ghost") is None


def test_load_python_tool_returns_none_without_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolkit, "_TOOL_REGISTRY", {})
    monkeypatch.setattr(_discovery.importlib, "import_module", lambda name: SimpleNamespace())
    entry = SimpleNamespace(name="ghost", target="fcmd.tools.ghost")

    assert _discovery._load_python_tool(cast(Any, entry)) is None


def test_drop_module_specs_removes_only_matching_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    keep = SimpleNamespace(func=SimpleNamespace(__module__="other"))
    drop = SimpleNamespace(func=SimpleNamespace(__module__="fcmd.tools.broken"))
    only = SimpleNamespace(func=SimpleNamespace(__module__="fcmd.tools.broken"))
    registry = {
        "alpha": {"keep": keep, "drop": drop},
        "beta": {"only": only},
    }
    monkeypatch.setattr(toolkit, "_TOOL_REGISTRY", registry)

    _discovery._drop_module_specs("fcmd.tools.broken")

    assert registry == {"alpha": {"keep": keep}}


# ---------------------------------------------------------------------- #
# 工具级 --help 固化文本
# ---------------------------------------------------------------------- #
def _help_payload(**overrides: Any) -> Any:
    """构造帮助快照（默认值即「当前环境可用」）。"""
    from fcmd.cli import _manifest

    fields: dict[str, Any] = {
        "width": _manifest.HELP_WIDTH,
        "python": sys.version_info[:2],
        "platform": sys.platform,
        "texts": {"stattool": "cached help\n"},
    }
    fields.update(overrides)
    return _manifest._HelpPayload(**fields)


def test_help_text_matches_runtime(tmp_path: Path) -> None:
    """清单固化帮助与实跑渲染逐字一致（同解释器 / 平台 / 80 列）。"""
    data = _data()
    if sys.platform != data.PLATFORM:
        pytest.skip(f"固化帮助由 {data.PLATFORM} 生成，仅在生成平台启用")
    if tuple(data.PY_VERSION) != sys.version_info[:2]:
        pytest.skip(f"固化帮助由 Python {data.PY_VERSION} 生成，仅在该版本启用")

    proc = _run_py(_HELP_SCRIPT, tmp_path)
    assert proc.returncode == 0, proc.stderr
    rendered = json.loads(proc.stdout.strip().splitlines()[-1])
    assert rendered == data.HELP
    assert data.HELP_WIDTH == 80


def test_help_text_hits_for_canonical_and_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """规范名与别名都命中同一条固化帮助。"""
    import io

    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "_console_width", lambda: 80)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(_manifest, "_help_data", _help_payload)
    monkeypatch.setattr(
        _manifest,
        "load_strict",
        lambda: _manifest._Manifest(
            {"stattool": _manifest.ToolEntry("stattool", "python", "fcmd.tools.stats.stattool")},
            {"st": "stattool"},
            {},
            "digest",
            1,
            1,
        ),
    )

    assert _manifest.help_text("stattool", ["--help"]) == "cached help\n"
    assert _manifest.help_text("st", ["-h"]) == "cached help\n"


def test_help_text_rejects_non_help_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """非「恰好 -h/--help」的调用一律回退（子命令帮助等）。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest, "_help_data", lambda: pytest.fail("非帮助参数不应读取快照"))

    assert _manifest.help_text("stattool", []) is None
    assert _manifest.help_text("stattool", ["--help", "x"]) is None
    assert _manifest.help_text("stattool", ["b", "--help"]) is None


def test_help_text_rejects_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """终端输出需要颜色，回退真实渲染。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: True))
    assert _manifest.help_text("stattool", ["--help"]) is None


def test_help_text_rejects_unavailable_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """快照缺失（旧清单 / 损坏）时回退。"""
    import io

    from fcmd.cli import _manifest

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(_manifest, "_help_data", lambda: None)
    assert _manifest.help_text("stattool", ["--help"]) is None


def test_help_text_rejects_width_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """渲染宽度不是 80（如 COLUMNS=120）时回退，保证排版一致。"""
    import io

    from fcmd.cli import _manifest

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(_manifest, "_help_data", _help_payload)
    monkeypatch.setattr(_manifest, "_console_width", lambda: 120)
    assert _manifest.help_text("stattool", ["--help"]) is None


def test_help_text_rejects_env_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """解释器版本 / 平台与生成期不一致时回退。"""
    import io

    from fcmd.cli import _manifest

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(_manifest, "_console_width", lambda: 80)
    monkeypatch.setattr(_manifest, "_help_data", lambda: _help_payload(python=(3, 7)))
    assert _manifest.help_text("stattool", ["--help"]) is None

    monkeypatch.setattr(_manifest, "_help_data", lambda: _help_payload(platform="other-os"))
    assert _manifest.help_text("stattool", ["--help"]) is None


def test_help_text_rejects_stale_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单严格校验不通过（源码已改未重生成）时回退。"""
    import io

    from fcmd.cli import _manifest

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(_manifest, "_console_width", lambda: 80)
    monkeypatch.setattr(_manifest, "_help_data", _help_payload)
    monkeypatch.setattr(_manifest, "load_strict", lambda: None)
    assert _manifest.help_text("stattool", ["--help"]) is None


def test_help_text_rejects_unknown_or_non_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """未登记工具与非 Python 工具（导入失败）都回退。"""
    import io

    from fcmd.cli import _manifest

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(_manifest, "_console_width", lambda: 80)
    monkeypatch.setattr(_manifest, "_help_data", _help_payload)

    unknown = _manifest._Manifest({}, {}, {}, "digest", 0, 0)
    monkeypatch.setattr(_manifest, "load_strict", lambda: unknown)
    assert _manifest.help_text("stattool", ["--help"]) is None

    broken = _manifest._Manifest({"stattool": _manifest.ToolEntry("stattool", "unknown", "")}, {}, {}, "digest", 1, 0)
    monkeypatch.setattr(_manifest, "load_strict", lambda: broken)
    assert _manifest.help_text("stattool", ["--help"]) is None


def test_help_text_missing_entry_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """快照里没有该工具文本时回退。"""
    import io

    from fcmd.cli import _manifest

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(_manifest, "_console_width", lambda: 80)
    monkeypatch.setattr(_manifest, "_help_data", lambda: _help_payload(texts={}))
    manifest = _manifest._Manifest(
        {"stattool": _manifest.ToolEntry("stattool", "python", "fcmd.tools.stats.stattool")}, {}, {}, "d", 1, 1
    )
    monkeypatch.setattr(_manifest, "load_strict", lambda: manifest)
    assert _manifest.help_text("stattool", ["--help"]) is None


def test_help_data_returns_none_on_broken_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """生成文件缺字段 / 不可导入时快照不可用（不抛异常）。"""
    from fcmd.cli import _manifest

    monkeypatch.setitem(sys.modules, "fcmd.cli._manifest_data", None)
    assert _manifest._help_data() is None


def test_console_width_uses_columns_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """宽度算法与 rich 一致：终端尺寸 → COLUMNS 覆盖 → 80 兜底。"""
    from fcmd.cli import _manifest

    monkeypatch.setattr(_manifest.os, "get_terminal_size", lambda fd: os.terminal_size((91, 25)))
    monkeypatch.delenv("COLUMNS", raising=False)
    assert _manifest._console_width() == 91

    monkeypatch.setenv("COLUMNS", "57")
    assert _manifest._console_width() == 57


def test_console_width_falls_back_to_80(monkeypatch: pytest.MonkeyPatch) -> None:
    """无终端且 COLUMNS 非法时回退 80。"""
    from fcmd.cli import _manifest

    def _raise(_fd: int) -> Any:
        raise OSError("not a terminal")

    monkeypatch.setattr(_manifest.os, "get_terminal_size", _raise)
    monkeypatch.setenv("COLUMNS", "abc")
    assert _manifest._console_width() == 80


def test_fcmd_app_tool_help_writes_manifest_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``fcmd <tool> --help`` 命中固化文本：不做全量发现、输出逐字一致。"""
    from fcmd.cli import _discovery, _manifest
    from fcmd.cli.main import FcmdApp

    data = _data()
    if sys.platform != data.PLATFORM or tuple(data.PY_VERSION) != sys.version_info[:2]:
        pytest.skip("固化帮助仅在生成平台/解释器启用")

    monkeypatch.setattr(_manifest, "_console_width", lambda: 80)
    monkeypatch.setattr(_discovery, "resolve_tool_fast", lambda name: pytest.fail("不应导入工具模块"))
    assert FcmdApp(["pymake", "--help"]).run() == 0
    assert capsys.readouterr().out == data.HELP["pymake"]
