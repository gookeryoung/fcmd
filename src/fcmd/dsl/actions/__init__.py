"""内建动作注册表：DSL ``action`` 命令的进程内实现。

TOML 声明 ``action = "<名>"`` 的命令由本注册表分发执行：动作实现是普通
Python 函数，其签名即 CLI 参数 schema（synth 层拷贝签名注入合成函数，
零改动复用 ``fcmd.apis._tool_args._build_parser_for_tool`` 的参数推导）。
与 cmd 任务（subprocess）不同，动作在 fcmd 进程内直接执行——无进程边界，
异常由引擎任务失败机制统一捕获（任务失败汇总 + 退出码非零）。

新动作的注册方式（按类别放入对应子模块，模块导入期自动注册）::

    @action("setenv", param_help={"name": "环境变量名"})
    def _setenv(name: str, value: str, default: bool = False) -> None:
        ...

约束：实现函数的参数名不得命中全局选项保留名（dry_run/quiet/strategy，
由 :mod:`fcmd.dsl.decl` 声明期校验）。两类语义：

* **副作用型**（TOML ``action`` 键）：无返回值消费， fn 任务进程内执行；
* **计算型**（TOML ``compute`` 键）：返回值被引擎消费——注入共享变量供
  本命令模板插值（``{as 名}``），空结果任务 SKIPPED（输出管道形态）。

消息约定：单条完成/失败提示走 TOML ``message``/``fail_message``；批量
操作中逐条目的过程反馈（预览/重命名回显/缺失跳过提示）保留 ``print``——
内容依赖运行时逐条目结果，无法静态模板化，且是批量操作的必要反馈。

子模块按类别划分（导入即注册，新增动作勿放入本模块）：

* :mod:`fcmd.dsl.actions.basic` —— 环境与文件基础副作用（setenv/writefile）
* :mod:`fcmd.dsl.actions.filenames` —— 文件名批量操作（dateprefix/filerename/filelevel）
* :mod:`fcmd.dsl.actions.system` —— 系统管理（taskkill/which/sysinfo/folderback）
* :mod:`fcmd.dsl.actions.compute` —— 计算型动作（pip_expand/pip_filter，返回值进数据流）
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Action", "action", "action_names", "get_action", "has_action"]


@dataclass(frozen=True)
class Action:
    """内建动作描述符。

    参数
    ----
    name:
        动作名（TOML ``action = "<名>"`` 引用）
    func:
        进程内实现函数（签名即 CLI 参数 schema）
    param_help:
        参数帮助文本（``{参数名: 帮助}``，供 ``--help`` 展示）
    """

    name: str
    func: Callable[..., Any]
    param_help: Mapping[str, str] = field(default_factory=dict)


_ACTIONS: dict[str, Action] = {}


def action(
    name: str, param_help: Mapping[str, str] | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册内建动作的装饰器（模块导入期执行，重复注册为编程错误）。

    Parameters
    ----------
    name:
        动作名（TOML ``action = "<名>"`` 引用，须匹配工具名风格）
    param_help:
        参数帮助文本（``{参数名: 帮助}``）

    Returns
    -------
    Callable
        装饰器（原函数原样返回）
    """

    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in _ACTIONS:
            raise ValueError(f"内建动作 {name!r} 重复注册")
        _ACTIONS[name] = Action(name=name, func=func, param_help=dict(param_help or {}))
        return func

    return deco


def has_action(name: str) -> bool:
    """查询动作是否已注册（声明期校验用）。"""
    return name in _ACTIONS


def action_names() -> tuple[str, ...]:
    """全部已注册动作名（排序，供声明期错误提示）。"""
    return tuple(sorted(_ACTIONS))


def get_action(name: str) -> Action:
    """取出动作描述符（调用方须经声明期校验，未知名抛 KeyError）。"""
    return _ACTIONS[name]


# 子模块导入期执行 @action 装饰器完成注册（须位于注册表定义之后）
from . import basic as basic  # noqa: E402
from . import compute as compute  # noqa: E402
from . import filenames as filenames  # noqa: E402
from . import system as system  # noqa: E402
