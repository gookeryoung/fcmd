"""工具发现测试接缝：``install_fake_registry`` / ``reset_discovery_state``。"""

from __future__ import annotations

from fcmd.cli import _discovery
from fcmd.cli._discovery import aliases_for, install_fake_registry, reset_discovery_state, resolve_tool, tool_names


def test_install_fake_registry_replaces_discovery_state() -> None:
    """通过公共接缝注入假发现结果，不直接改私有表。"""
    saved_modules = dict(_discovery._TOOL_MODULES)
    saved_aliases = dict(_discovery._TOOL_ALIASES)
    saved_discovered = _discovery._TOOLS_DISCOVERED
    try:
        install_fake_registry({"alpha": "fcmd.console"}, aliases={"a": "alpha"})
        assert tool_names() == ["alpha"]
        assert resolve_tool("a") == "alpha"
        assert aliases_for("alpha") == ["a"]
        assert _discovery._TOOL_MODULES == {"alpha": "fcmd.console"}
    finally:
        reset_discovery_state()
        _discovery._TOOL_MODULES.update(saved_modules)
        _discovery._TOOL_ALIASES.update(saved_aliases)
        _discovery._TOOLS_DISCOVERED = saved_discovered


def test_reset_discovery_state_clears_module_and_alias_tables() -> None:
    """``reset_discovery_state`` 只清发现状态，不影响已注册工具表。"""
    saved_modules = dict(_discovery._TOOL_MODULES)
    saved_aliases = dict(_discovery._TOOL_ALIASES)
    saved_discovered = _discovery._TOOLS_DISCOVERED
    try:
        install_fake_registry({"alpha": "fcmd.console"}, aliases={"a": "alpha"})
        reset_discovery_state()
        assert _discovery._TOOL_MODULES == {}
        assert _discovery._TOOL_ALIASES == {}
        assert _discovery._TOOLS_DISCOVERED is False
    finally:
        reset_discovery_state()
        _discovery._TOOL_MODULES.update(saved_modules)
        _discovery._TOOL_ALIASES.update(saved_aliases)
        _discovery._TOOLS_DISCOVERED = saved_discovered
