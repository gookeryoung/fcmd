"""fcmd.core.hooks —— 分层钩子，供 core 在运行期调用上层实现。

``core`` 位于依赖图底层，不应导入 ``fcmd.cli`` / ``fcmd.dsl`` 等上层包。
需要上层能力的场景（工具发现、DSL 动作解析）通过本模块注册回调实现反转：
组合根（``fcmd`` 包）与 DSL 包在导入时注册实现，core 只调用已注册钩子。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = [
    "ensure_discovered",
    "register_action_resolver",
    "register_cmd_rewriter",
    "register_discovery_hook",
    "resolve_action",
    "rewrite_cmd",
]

DiscoveryHook = Callable[[], None]
ActionResolver = Callable[[str], Any]
CmdRewriter = Callable[[str], str]

_discovery_hook: DiscoveryHook | None = None
_action_resolver: ActionResolver | None = None
_cmd_rewriter: CmdRewriter | None = None


def register_discovery_hook(hook: DiscoveryHook) -> None:
    """注册工具发现回调（由组合根 ``fcmd`` 包调用）。"""
    global _discovery_hook  # noqa: PLW0603 - 钩子为模块级单例
    _discovery_hook = hook


def ensure_discovered() -> None:
    """调用已注册的工具发现回调；未注册时为无操作。"""
    if _discovery_hook is not None:
        _discovery_hook()


def register_action_resolver(resolver: ActionResolver) -> None:
    """注册 DSL 动作解析回调（由 :mod:`fcmd.dsl` 调用）。"""
    global _action_resolver  # noqa: PLW0603 - 钩子为模块级单例
    _action_resolver = resolver


def resolve_action(name: str) -> Any:
    """解析 DSL 动作；解析器未注册时抛出 ``RuntimeError``。"""
    if _action_resolver is None:
        raise RuntimeError(f"DSL 动作解析器未注册，无法解析动作: {name}")
    return _action_resolver(name)


def register_cmd_rewriter(rewriter: CmdRewriter) -> None:
    """注册 cmd 重写回调（由 :mod:`fcmd.dsl` 调用）。

    用于把「依赖本机环境」的 shell 命令改写（如 Windows 上把裸 ``ssh``
    换成检测到的 OpenSSH/plink 路径）推迟到执行期：注册期保留声明模板，
    构建任务时才调用本钩子，避免工具发现阶段付子进程探测成本。
    """
    global _cmd_rewriter  # noqa: PLW0603 - 钩子为模块级单例
    _cmd_rewriter = rewriter


def rewrite_cmd(cmd: str) -> str:
    """用已注册的回调重写 shell 命令；未注册时原样返回。"""
    if _cmd_rewriter is None:
        return cmd
    return _cmd_rewriter(cmd)
