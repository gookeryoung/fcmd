"""P1 API 验证脚本。"""

from __future__ import annotations

import fcmd as fx


@fx.tool("demo", subcommand="hello", cmd=["python", "-c", "print('hi from tool')"])
def hello() -> None:
    """cmd 任务：打印 hi。"""


@fx.tool("demo", subcommand="greet", help="问候")
def greet(name: str, times: int = 1) -> str:
    """fn 任务：返回问候语。"""
    return f"hello {name} " * times


@fx.tool("demo", subcommand="prep", cmd=["python", "-c", "print('prep done')"])
def prep() -> None:
    """cmd 任务：聚合任务的依赖。"""


@fx.tool("demo", subcommand="all", needs=["hello", "prep"], strategy="thread")
def all_task() -> None:
    """聚合任务：依赖 hello + prep。"""


def main() -> None:
    # 1. cmd 任务
    print("=== cmd 任务 ===")
    code = fx.run_tool("demo", ["hello"])
    print(f"exit code: {code}")

    # 2. fn 任务
    print("\n=== fn 任务 ===")
    code = fx.run_tool("demo", ["greet", "world", "--times", "2"])
    print(f"exit code: {code}")

    # 3. 聚合任务
    print("\n=== 聚合任务 ===")
    code = fx.run_tool("demo", ["all"])
    print(f"exit code: {code}")

    # 4. dry-run
    print("\n=== dry-run ===")
    code = fx.run_tool("demo", ["hello", "--dry-run"])
    print(f"exit code: {code}")


if __name__ == "__main__":
    main()
