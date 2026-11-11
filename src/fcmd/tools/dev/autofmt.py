"""autofmt - 代码格式化与检查（ruff 包装）。

示例
----
    fcmd autofmt fmt                  # 格式化当前目录
    fcmd autofmt lint --target src    # 检查 src
    fcmd autofmt lint --fix           # 检查并自动修复（含 unsafe fixes）
"""

from __future__ import annotations

import subprocess

import fcmd as fx


@fx.tool("autofmt", subcommand="fmt", help="格式化代码 (ruff format)", cmd=["ruff", "format", "{target}"])
def fmt(target: str = ".") -> None:
    """格式化代码 (ruff format)。

    Parameters
    ----------
    target:
        目标路径（默认：当前目录）
    """


@fx.tool("autofmt", subcommand="lint", help="代码检查 (ruff check)")
def lint(target: str = ".", fix: bool = False) -> None:
    """代码检查 (ruff check)；``fix`` 为真时追加 ``--fix --unsafe-fixes``。

    Parameters
    ----------
    target:
        目标路径（默认：当前目录）
    fix:
        自动修复问题（含 unsafe fixes）
    """
    cmd = ["ruff", "check", target]
    if fix:
        cmd += ["--fix", "--unsafe-fixes"]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ruff check 失败（退出码 {result.returncode}）")


@fx.main("autofmt")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
