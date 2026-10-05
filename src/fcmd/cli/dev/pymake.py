"""pymake - 项目构建工具入口。

提供构建/测试/清理/检查/格式化/发布等子命令。
``pymake <args>`` 与 ``fcmd pymake <args>`` 行为完全一致。

子命令来源
----------
纯 exec 型子命令（b/bump/bumpma/bumpmi/c/doc/lint/fmt/fmtc/pyrefly_check/
chk/tc/t/tn/cov/tf/ts/tox/sync/upload）由 ``src/fcmd/commands/pymake.toml``
DSL 声明，发现时与本模块的子命令合并注册（见 ``fcmd.cli._discovery``）。
本模块仅保留含 Python 逻辑的子命令：

- ``push``：遍历所有 git remote 推送代码 + tags（callable cmd）

示例
----
    pymake b          # 构建 (uv build)
    pymake t          # 运行测试
    pymake chk        # 类型检查（聚合：pyrefly + lint + fmt + tf，thread 策略）
    pymake cov        # 测试并生成覆盖率
    pymake bump       # 升级 patch 版本号
    pymake push       # 推送代码到所有远程仓库
"""

from __future__ import annotations

from pathlib import Path

import fcmd

# 工具别名：fcmd pm <args> 等价于 fcmd pymake <args>
__tool_aliases__: list[str] = ["pm"]


# ============================================================================
# 推送相关
# ============================================================================


def _push_all_remotes() -> None:
    """遍历所有 git remote，逐个推送代码 + 标签。

    与 Makefile ``push`` target 行为一致；任一 remote 推送失败立即抛
    RuntimeError 阻断后续，便于 CI 检测。
    """
    import subprocess as sp

    remotes = sp.check_output(["git", "remote"], text=True).split()
    if not remotes:
        raise RuntimeError("未检测到 git remote，无法推送")
    for remote in remotes:
        print(f"推送 {remote}...", flush=True)
        sp.run(["git", "push", remote], check=True)
        sp.run(["git", "push", remote, "--tags"], check=True)


@fcmd.tool(
    "pymake",
    subcommand="push",
    help="推送代码到所有远程仓库",
    cmd=_push_all_remotes,
)
def push(cwd: Path = Path()) -> None:
    """推送代码到所有远程仓库（含 tags）。"""


@fcmd.main("pymake")
def main() -> None:
    """pymake 主程序."""


if __name__ == "__main__":
    main()
