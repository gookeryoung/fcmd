"""内建动作注册表：DSL ``action`` 命令的进程内实现。

TOML 声明 ``action = "<名>"`` 的命令由本注册表分发执行：动作实现是普通
Python 函数，其签名即 CLI 参数 schema（synth 层拷贝签名注入合成函数，
零改动复用 ``fcmd.apis._tool_args._build_parser_for_tool`` 的参数推导）。
与 cmd 任务（subprocess）不同，动作在 fcmd 进程内直接执行——无进程边界，
异常由引擎任务失败机制统一捕获（任务失败汇总 + 退出码非零）。

新动作的注册方式::

    @action("setenv", param_help={"name": "环境变量名"})
    def _setenv(name: str, value: str, default: bool = False) -> None:
        ...

约束：实现函数的参数名不得命中全局选项保留名（dry_run/quiet/strategy，
由 :mod:`fcmd.dsl.decl` 声明期校验）；副作用型语义（无返回值消费），
需产出计算结果的命令不适用动作原语。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
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


# ---------------------------------------------------------------------- #
# 内建动作实现（print 一律移除，完成/失败提示走 TOML message/fail_message）
# ---------------------------------------------------------------------- #
@action(
    "setenv",
    param_help={
        "name": "环境变量名",
        "value": "环境变量值",
        "default": "为 True 时使用 setdefault 不覆盖已有值",
    },
)
def _setenv(name: str, value: str, default: bool = False) -> None:
    """设置当前进程环境变量（仅影响 fcmd 进程及其后续子任务）。"""
    if default:
        os.environ.setdefault(name, value)
    else:
        os.environ[name] = value


@action(
    "writefile",
    param_help={
        "path": "目标文件路径",
        "content": "写入内容",
        "encoding": "文件编码（默认 utf-8）",
    },
)
def _writefile(path: str, content: str, encoding: str = "utf-8") -> None:
    """将文本内容写入指定路径的文件。"""
    Path(path).write_text(content, encoding=encoding)
