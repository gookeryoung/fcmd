"""内建动作注册表：DSL ``action`` 命令的进程内实现。

TOML 声明 ``action = "<名>"`` 的命令由本注册表分发执行：动作实现是普通
Python 函数，其签名即 CLI 参数 schema（synth 层拷贝签名注入合成函数，
零改动复用 ``fcmd.core.toolkit.args._build_parser_for_tool`` 的参数推导）。
与 cmd 任务（subprocess）不同，动作在 fcmd 进程内直接执行——无进程边界，
异常由引擎任务失败机制统一捕获（任务失败汇总 + 退出码非零）。

新动作的注册方式（按类别放入对应子模块，模块导入期自动注册）::

    @action("setenv", param_help={"name": "环境变量名"})
    def _setenv(name: str, value: str, default: bool = False) -> None:
        ...

错误呈现：整段函数体处于同一 try/except、且「错误信息即结果」的动作，
经 ``errors`` 声明预期内的用户输入错误类型，注册期由 :func:`_guarded`
统一包装（打印 ``错误: {异常}`` 并保持退出码 0）；未列入 ``errors`` 的
异常继续冒泡，由引擎任务失败机制捕获。分支内局部 try/except（如需自定义
错误前缀或错误后继续执行）保留手写。

约束：实现函数的参数名不得命中全局选项保留名（dry_run/quiet/strategy，
由 :mod:`fcmd.dsl.decl` 声明期校验）。两类语义：

* **副作用型**（TOML ``action`` 键）：无返回值消费， fn 任务进程内执行；
* **计算型**（TOML ``compute`` 键）：返回值被引擎消费——注入共享变量供
  本命令模板插值（``{as 名}``），空结果任务 SKIPPED（输出管道形态）。

消息约定：单条完成/失败提示走 TOML ``message``/``fail_message``；批量
操作中逐条目的过程反馈（预览/重命名回显/缺失跳过提示）保留 ``print``——
内容依赖运行时逐条目结果，无法静态模板化，且是批量操作的必要反馈。

子模块分两层（按需导入，新增动作勿放入本模块）。动作名 → 子模块的
索引由 :mod:`fcmd.dsl.actions._index` 静态维护：新增动作后须执行
``uv run python -m fcmd.dsl.actions._index`` 重新生成，否则动作在按需
加载时定位不到（一致性测试会拦截）。

**通用动作层（包顶层）**——与具体工具无关、可被任意声明复用：

* :mod:`fcmd.dsl.actions.basic` —— 环境与文件基础副作用（setenv/writefile）
* :mod:`fcmd.dsl.actions.filenames` —— 文件名批量操作（dateprefix/filerename/filelevel）
* :mod:`fcmd.dsl.actions.fileops` —— 目录检索与路径处理（filesearch/pathtool）
* :mod:`fcmd.dsl.actions.system` —— 系统管理（taskkill/which/sysinfo/folderback）
* :mod:`fcmd.dsl.actions.genid` —— ID 生成（UUID/时间戳/随机字符串，idtool）

**工具专用命令层（:mod:`fcmd.dsl.actions.toolcmd`）**——只服务单一工具域，
本质是把 ``fcmd.tools.<域>`` 的能力包一层 CLI 外壳，模块内只提供命令：

* :mod:`fcmd.dsl.actions.toolcmd.archive` —— 目录压缩（folderzip）
* :mod:`fcmd.dsl.actions.toolcmd.compute` —— 计算型动作（pip_expand/pip_filter，返回值进数据流）
* :mod:`fcmd.dsl.actions.toolcmd.hashing` —— 字符串与文件哈希（hashtool/hashfile）
* :mod:`fcmd.dsl.actions.toolcmd.conv` —— 命名风格/颜色换算/数学计算（casetool/colortool/mathtool）
* :mod:`fcmd.dsl.actions.toolcmd.codec` —— 编解码/单位换算/URL（codetool/convtool/urltool）
* :mod:`fcmd.dsl.actions.toolcmd.calc` —— 随机/统计/时间工具（randtool/stattool/timetool）
* :mod:`fcmd.dsl.actions.toolcmd.calcdate` —— 日期计算（calcdate）
* :mod:`fcmd.dsl.actions.toolcmd.crypto` —— 加密与摘要（cryptool）
* :mod:`fcmd.dsl.actions.toolcmd.data` —— 结构化数据转换（csvtool/jsontool/tomltool/xmltool/yamtool/inifile）
* :mod:`fcmd.dsl.actions.toolcmd.media` —— 图片处理（imagetool/img2ico）
* :mod:`fcmd.dsl.actions.toolcmd.net` —— 网络工具（websave/nettool/iptool/urlcheck）
* :mod:`fcmd.dsl.actions.toolcmd.text` —— 文本处理（txttool/asciitool/padtool/regextool/textdiff）
"""

from __future__ import annotations

import importlib
import inspect
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from ._index import ACTION_MODULES, ACTION_SPECS

__all__ = [
    "Action",
    "action",
    "action_names",
    "action_param_help",
    "action_param_names",
    "action_signature",
    "get_action",
    "has_action",
    "run_and_print",
]


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

# 已导入的动作子模块名（懒加载幂等标记）
_LOADED_MODULES: set[str] = set()


def _load_module(module: str) -> None:
    """导入单个动作子模块（幂等；导入期执行 ``@action`` 完成注册）。"""
    if module in _LOADED_MODULES:
        return
    _LOADED_MODULES.add(module)
    importlib.import_module(f"{__name__}.{module}")


def _load_action(name: str) -> None:
    """按需加载动作名所属子模块；未知名不导入任何模块（交由调用方报错）。"""
    module = ACTION_MODULES.get(name)
    if module is not None:
        _load_module(module)


# 静态注解标记 → 类型对象（``_index._ann_token`` 的逆变换；见 ACTION_SPECS）
_ANNOTATION_BASES: dict[str, Any] = {"str": str, "int": int, "float": float, "bool": bool, "path": Path}


def _annotation_from_token(token: str) -> Any:
    """静态注解标记 → 类型对象；空标记表示无注解。"""
    if not token:
        return inspect.Parameter.empty
    base = _ANNOTATION_BASES.get(token)
    if base is not None:
        return base
    if token.startswith("list:"):
        inner = _annotation_from_token(token.removeprefix("list:"))
        return list[inner]
    if token.startswith("opt:"):
        inner = _annotation_from_token(token.removeprefix("opt:"))
        return inner | None
    if token.startswith("literal:"):
        values = tuple(token.removeprefix("literal:").split("|"))
        return typing.Literal[values]  # type: ignore[not-a-type] - 运行期按字面量集合动态构造
    raise ValueError(f"未知的注解标记: {token!r}")


def action_param_names(name: str) -> tuple[str, ...]:
    """动作参数名（静态表，声明期校验用；不导入实现模块）。"""
    return tuple(param[0] for param in ACTION_SPECS[name][1])


def action_param_help(name: str) -> dict[str, str]:
    """动作参数帮助文本（静态表；不导入实现模块）。"""
    return dict(ACTION_SPECS[name][0])


def action_signature(name: str) -> tuple[inspect.Signature, dict[str, Any]]:
    """按静态表重建动作签名与注解（参数 schema 合成用；不导入实现模块）。"""
    params: list[inspect.Parameter] = []
    hints: dict[str, Any] = {}
    for pname, token, has_default, default in ACTION_SPECS[name][1]:
        annotation = _annotation_from_token(token)
        params.append(
            inspect.Parameter(
                pname,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default if has_default else inspect.Parameter.empty,
                annotation=annotation,
            )
        )
        hints[pname] = annotation
    return inspect.Signature(params), hints


def _guarded(
    func: Callable[..., Any],
    errors: tuple[type[BaseException], ...],
    error_prefix: str,
) -> Callable[..., Any]:
    """包装动作实现：捕获 ``errors`` 内的异常并以 ``错误: {前缀}{异常}`` 输出。

    错误信息即结果（保持退出码 0），与既有手写 try/except 语义一致；
    未列入 ``errors`` 的异常继续冒泡，由引擎按任务失败处理。
    ``functools.wraps`` 保留 ``__wrapped__``，``inspect.signature`` 仍返回
    原函数签名（:mod:`fcmd.dsl.synth` 的签名拷贝依赖该语义）。
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except errors as error:
            print(f"错误: {error_prefix}{error}")
            return None

    return wrapper


def action(
    name: str,
    param_help: Mapping[str, str] | None = None,
    *,
    errors: tuple[type[BaseException], ...] = (),
    error_prefix: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册内建动作的装饰器（模块导入期执行，重复注册为编程错误）。

    Parameters
    ----------
    name:
        动作名（TOML ``action = "<名>"`` 引用，须匹配工具名风格）
    param_help:
        参数帮助文本（``{参数名: 帮助}``）
    errors:
        预期内的用户输入错误类型：捕获后打印 ``错误: {异常}`` 并保持
        退出码 0；为空表示不包装（异常照常冒泡）
    error_prefix:
        错误消息前缀（如 ``"请求失败: "``），仅在声明 ``errors`` 时生效

    Returns
    -------
    Callable
        装饰器（返回注册的实现函数；声明 ``errors`` 时为其包装函数）
    """

    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in _ACTIONS:
            raise ValueError(f"内建动作 {name!r} 重复注册")
        target = _guarded(func, errors, error_prefix) if errors else func
        _ACTIONS[name] = Action(name=name, func=target, param_help=dict(param_help or {}))
        return target

    return deco


def has_action(name: str) -> bool:
    """查询动作是否存在（静态映射表查询，**不导入任何子模块**）。"""
    return name in _ACTIONS or name in ACTION_MODULES


def action_names() -> tuple[str, ...]:
    """全部动作名（排序，供声明期错误提示）。

    静态表与已注册动作的并集——只查表，不导入子模块。
    """
    return tuple(sorted(set(ACTION_MODULES) | set(_ACTIONS)))


def get_action(name: str) -> Action:
    """取出动作描述符并导入其所属子模块（执行期入口；未知名抛 KeyError）。"""
    _load_action(name)
    return _ACTIONS[name]


def run_and_print(
    fn: Callable[..., Any],
    *args: Any,
    exc: tuple[type[BaseException], ...] = (ValueError,),
    **kwargs: Any,
) -> None:
    """执行换算/计算型动作并打印结果，``exc`` 内的异常转为错误行输出。

    换算类动作的共用外壳：参数或输入非法时抛 ``ValueError``，此处捕获后以
    ``[red]错误:[/red]`` 呈现并保持退出码 0（错误信息即结果，非任务失败）。
    """
    from fcmd.console import print_error  # 延迟导入避免包级循环

    try:
        result = fn(*args, **kwargs)
    except exc as error:
        print_error(str(error))
        return
    print(result)


# 子模块从不在包导入期加载：声明期只读 ``_index`` 静态表（``has_action`` /
# ``action_names`` / ``action_param_*`` / ``action_signature``）；只有动作真正
# 执行时 ``get_action`` 才导入其所属子模块，``import fcmd.dsl`` 与命令发现
# 都不再承担 17 个子模块的传递依赖（含 PIL / yaml 等）。
