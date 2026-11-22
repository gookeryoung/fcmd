# 0041 CLI 执行过程输出用户化

- 状态：已落地
- 日期：2026-11-25
- 关联：[0037](0037-unified-result-envelope.md)（`--json` 机读信封与 `错误: / 提示:` 文案契约）

## 背景

`fcmd <tool> <subcommand>` 默认会打印框架的执行进度，这些行是「给开发者看的」：

```
> '_init' 开始执行...
OK '_init' 成功 (0.002s)
> '_add' 开始执行...
> 执行命令: git add .
返回码: 0
OK '_add' 成功 (0.025s)
执行汇总
+--------+------+--------+------+
| 任务   | 状态 | 耗时   | 重试 |
+--------+------+--------+------+
| _init  | 成功 | 0.002s |  -   |
```

问题集中在四点：

1. **内部标识外泄**：`_init` / `_add` / `_commit` 是实现细节（`hidden=True` 的守卫链），
   用户看到的是下划线任务名 + `repr` 引号，不知道它们在干什么；
2. **术语偏机器**：`开始执行` / `OK … 成功` / `执行命令: …` / `返回码: 0` 是调试口吻，
   同一个动作打两行（开始 + 成功），信息密度低；
3. **汇总表偏审计**：`任务 / 状态 / 耗时 / 重试` 四列在交互式场景里没人看，
   主信息（做了什么、用了多久）反而藏在表格里；
4. **异常直出 Python**：`subprocess` 的 `CalledProcessError` 会把
   `Command '['git', 'commit', '-m', 'x']' returned non-zero exit status 128` 原样抛给用户。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 步骤名用子命令的 `help`（中文动作名），去掉「（内部）」后缀；缺失时回退子命令名 / 工具名。不再出现内部任务名与 `repr` 引号 | `core/toolkit/exec.py::_display_label` |
| D2 | 每个步骤只在**结束时**打一行：`完成  提交暂存更改  0.05s` / `失败  …` / `跳过  …（原因）`；取消「开始执行…」行 | `console.print_step_*` |
| D3 | 命令回显改为 shell 习惯的暗色 `$ git add .`；成功时不再打印「返回码: 0」，失败时把返回码写进错误句 | `core/execution/task_command.py` |
| D4 | 多步成功只补一行 `共 3 步，用时 0.08s`；任务汇总表移除。纯聚合壳任务（如 `gitt a`，只做编排）不计入步数、不单独占行 | `console.print_run_summary` |
| D5 | 失败路径保持 `错误: …` + `提示: …`（0037 已定的契约），并把 `CalledProcessError` 翻译成 `命令执行失败: git push（返回码 1）` | `core/toolkit/exec.py::_describe_exception` |
| D6 | `--quiet` 依旧完全静默框架行（工具自身输出保留）；`--json` 不受影响（stdout 必须能整体解析） | `core/toolkit/exec.py` |
| D7 | cmd 步骤在 `$ …` 之后实时透传子进程输出（不缓冲），长命令仍能看到进展 | `core/toolkit/exec.py` |
| D8 | 框架行统一由 `fcmd.console` 产出，CLI 与 `fx.run(verbose=True)` 共用同一套措辞；测试用 `tests/helpers.strip_framework_lines` 过滤 | `console.py`、`tests/helpers.py` |

目标输出（`fcmd gitt a`）：

```
完成  初始化 Git 仓库  0.00s
$ git add .
完成  暂存全部更改  0.02s
[main fac721d] chore: update
 8 files changed, 94 insertions(+), 22 deletions(-)
完成  检查并提交更改  0.05s
共 3 步，用时 0.08s
```

## 落地状态

- 已落地：D1–D8 全部实施，`gittool` 的「无更改」分支补一句
  `没有需要提交的更改`（避免“完成提交”与事实不符）；`gitt isub` 跳过 `.git` / `.venv`
  这类隐藏目录（原先会把 `.git` 当子项目再 init）。
- 守卫测试：`tests/core/test_toolkit.py`（P19 段）覆盖内部名不外泄、`$` 回显、
  单步不补收尾行、`--quiet` 静默、`CalledProcessError` 友好化；
  `tests/core/test_engine_*` 覆盖 verbose 单行结果。
- 未改动：`--json` 信封、退出码契约、`错误: / 提示:` 前缀（0037 口径）。

## 交接

- 新增工具不需要额外配置：`help` 即步骤名；确实想要不同措辞时在 `@fx.tool(help=…)`
  写成动词短语即可（如 `help="推送"`）。
- 工具函数内自己 `print` 的提示语仍在步骤行之前输出；「无事可做」这类分支建议
  显式打一句人话，别让「完成 …」变成假阳性。
