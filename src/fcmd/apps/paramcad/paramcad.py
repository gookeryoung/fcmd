"""paramcad - 参数化建模与几何预览（CLI）。

只产出 ``dynacalc-model/1`` 模型描述，不求解、不生成网格、不输出 LS-DYNA 关键字。

示例
----
    fcmd paramcad example model.yaml
    fcmd paramcad inspect model.yaml
    fcmd paramcad digest model.yaml
    fcmd paramcad export model.yaml out.yaml

``export`` 会先按契约重新解析并校验，再原样生成规范化 YAML；``--overwrite``
未给出时不会覆盖已有文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fcmd
from fcmd.apps.paramcad import _export
from fcmd.apps.paramcad._model import format_summary
from fcmd.apps.paramcad._quantity import ParamcadError
from fcmd.console import print_error

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["contract", "digest", "example", "export", "inspect"]

__version__ = "0.1.0"

_DESCRIPTION = (
    "参数化建模与几何预览：管坯几何、拉拔工艺、密度分布与缺陷参数 → dynacalc-model/1 "
    "模型描述；不求解、不生成网格、不输出关键字"
)


def _run(action: Callable[[], None]) -> None:
    """执行子命令，把 :class:`ParamcadError` 转为中文错误输出。"""
    try:
        action()
    except ParamcadError as exc:
        print_error(str(exc))


@fcmd.tool("paramcad", subcommand="example", help="写入示例模型（虚构占位，用于跑通链路）")
def example(output: Path, overwrite: bool = False) -> None:
    """把内置示例模型写到 ``output``。

    Parameters
    ----------
    output:
        目标 YAML 路径
    overwrite:
        已存在时是否覆盖
    """

    def action() -> None:
        path = _export.export_model(_export.example_model(), output, overwrite=overwrite)
        print(f"已写入示例模型：{path}")

    _run(action)


@fcmd.tool("paramcad", subcommand="inspect", help="查看模型的几何、工艺、分布场与 TBD 清单")
def inspect(model_file: Path) -> None:
    """打印模型摘要；文件含多个模型时逐个输出。

    Parameters
    ----------
    model_file:
        ``dynacalc-model/1`` YAML 文件
    """

    def action() -> None:
        models = _export.load_models(model_file)
        print("\n\n".join(format_summary(model) for model in models))

    _run(action)


@fcmd.tool("paramcad", subcommand="export", help="校验并导出规范化 dynacalc 模型文件")
def export(model_file: Path, output: Path, overwrite: bool = False) -> None:
    """读取源文件、按契约校验后写出规范化 YAML。

    Parameters
    ----------
    model_file:
        源模型文件
    output:
        目标文件
    overwrite:
        已存在时是否覆盖
    """

    def action() -> None:
        models = _export.load_models(model_file)
        path = _export.export_models(models, output, overwrite=overwrite)
        print(f"已导出 {len(models)} 个模型：{path}")
        for model in models:
            print(f"  {model.model_id}  digest={_export.model_digest(model)}")

    _run(action)


@fcmd.tool("paramcad", subcommand="digest", help="计算模型指纹（SHA-256 全 64 位）")
def digest(model_file: Path) -> None:
    """输出每个模型的 ``model_digest``。

    Parameters
    ----------
    model_file:
        模型文件
    """

    def action() -> None:
        for model in _export.load_models(model_file):
            print(f"{model.model_id}\t{_export.model_digest(model)}")

    _run(action)


@fcmd.tool("paramcad", subcommand="contract", help="输出导出契约摘要（供 dynacalc 侧核对）")
def contract() -> None:
    """打印当前导出器对齐的契约版本与字段清单。"""

    def action() -> None:
        print(_export.format_contract().rstrip())

    _run(action)


@fcmd.main("paramcad")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体


if __name__ == "__main__":  # pragma: no cover
    main()
