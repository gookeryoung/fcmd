"""API 验证脚本。"""
from __future__ import annotations

import fcmd as fx


@fx.task
def extract() -> list[int]:
    return [1, 2, 3]


@fx.task
def double(extract: list[int]) -> list[int]:
    return [x * 2 for x in extract]


def main() -> None:
    g = fx.graph(extract, double)
    r = fx.run(g)
    print("double:", r["double"])
    print("extract:", r["extract"])
    print("success:", r.success)


if __name__ == "__main__":
    main()
