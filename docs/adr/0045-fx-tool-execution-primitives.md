# 0045 `fx.tool` 执行原语：前置断言、有序步骤与显式聚合

- 状态：已落地（`preconditions` / `steps` / `aggregate`）
- 日期：2026-11-24
- 关联：[0041](0041-cli-progress-output.md)（CLI 执行输出）、[0037](0037-unified-result-envelope.md)（错误信封）

## 背景

`gittool` 的 `a`（添加并提交）与 `i`（初始化并提交）曾用一条隐藏链
`_init → _add → _commit` 表达顺序，再用 `init: bool` 参数决定 `_init` 是
「校验」还是「创建」：

```python
def add_and_commit(message: str = "chore: update", init: bool = False) -> None: ...
def init_and_commit(message: str = "init commit", init: bool = True) -> None: ...
```

问题不在 gittool 本身，而在 `@fx.tool` 缺三类原语：

1. **前置断言**：只有 `needs`（必跑）与 `conditions`（不满足则 SKIPPED），
   没有「必须成立，否则失败」的位置，只能把校验塞进一个带开关的步骤；
2. **依赖参数注入**：依赖任务的参数只能靠「同名变量」从目标签名隐式流入，
   于是内部开关 `init` 必须声明在 `a` / `i` 上，泄漏成 `--init` / `--no-init`，
   并进入 `--help`、`fcmd info --json` 与静态清单；
3. **线性流水线**：只能手写 `_add.needs = ["_init"]`、`_commit.needs = ["_add"]`，
   节点关系散落在多个装饰器里；换一条链（如 `a` 不要 init）就得复制节点或加开关。

另外，聚合任务是靠 AST 扫函数体推断的（`_has_function_logic`），给壳函数加一行
代码就会改变任务类型，显式声明更可靠。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | 新增 `preconditions=[callable, ...]`：只对目标子命令生效，解析完参数、构建 DAG 前执行；按签名注入 CLI 变量；抛异常即失败，不进 DAG、不计步数、`--dry-run` 跳过 | 用 `conditions` 表达（skip 语义，会被吞成 SKIPPED，用户看不到错误）；给 `needs` 加 `verify=True`（把校验混进任务节点） | `ToolSpec.preconditions`、`exec._run_preconditions` |
| D2 | 新增 `steps=[...]`：有序流水线，自动生成链式依赖；元素为任务名或 `step("name", **kwargs)`，`kwargs` 只注入该步骤，不进入 CLI / schema；与 `cmd` 互斥，非空即视为聚合任务 | 继续在 `_add` / `_commit` 里写 `needs`（共享节点无法按链差异化）；给依赖任务开放用户可见参数（内部开关泄漏） | `ToolSpec.steps`、`ToolStep` / `step()`、`exec._plan_steps` |
| D3 | 新增 `aggregate: bool \| None`：显式声明编排壳；`None` 保留历史 AST 推断 | 继续只靠 `_has_function_logic`（壳函数加一行日志就变成 fn 任务） | `ToolSpec.aggregate`、`exec._is_aggregate` |

四类编排原语的分工：

| 参数 | 语义 | 失败 / 不满足时 |
|---|---|---|
| `needs` | 硬依赖：必须先完成的任务 | 上游失败则本任务终止 |
| `steps` | 顺序流水线：第 n 步依赖第 n-1 步，目标任务依赖最后一步；目标自身的 `needs` 作为流水线前缀 | 同上 |
| `preconditions` | 前置断言：目标命令开始前检查环境 / 参数 | 失败即中止（`PRECONDITION_FAILED`，退出码 1） |
| `conditions`（TaskSpec） | 可选条件 | 不满足则 SKIPPED，不算失败 |

### gittool 落地形态

```python
def _require_repo() -> None:
    if _repo_root() is None:
        raise RuntimeError("当前目录不是 Git 仓库；请先运行 `fcmd gitt i` 初始化")


@fx.tool("gittool", subcommand="_init", hidden=True)
def git_init(create: bool = False) -> None: ...


@fx.tool(
    "gittool",
    subcommand="a",
    help="添加并提交（要求已是 Git 仓库）",
    aggregate=True,
    preconditions=[_require_repo],
    steps=["_add", "_commit"],
)
def add_and_commit(message: str = "chore: update") -> None: ...


@fx.tool(
    "gittool",
    subcommand="i",
    help="初始化并提交",
    aggregate=True,
    steps=[fx.step("_init", create=True), "_add", "_commit"],
)
def init_and_commit(message: str = "init commit") -> None: ...
```

- `a` / `i` 的命令行只剩 `--message`，`--init` / `--no-init` 移除；
- `create=True` 是步骤级内部参数，只注入 `_init`，不出现在 `param_schema`；
- `a` 在非仓库目录由 `_require_repo` 直接报错，不会隐式 `git init`；
- `i` 的 `_init` 在仓库子目录也 no-op（`git rev-parse --show-toplevel` 判定），
  不产生嵌套仓库；
- `_add` / `_commit` 不再声明 `needs`，顺序只在 `a` / `i` 的 `steps` 中声明一次。

## 验证

- `tests/core/test_toolkit.py`：`preconditions` 变量注入 / 失败中止 / dry-run 跳过；
  `steps` 顺序、步骤级参数不外溢（`param_schema` 为空）、前缀边与收集顺序、
  参数冲突、循环引用；`aggregate=True/False` 与 `steps` 的显式语义；
- `tests/tools/dev/test_cli_gittool.py`：`a` / `i` 只剩 `message` 参数、
  `a` 在非仓库报错且不建 `.git`、`a --init` 被拒绝、`i` 初始化并提交、
  仓库子目录不建嵌套仓库；
- `tests/cli/test_tools_manifest.py`：静态清单与运行时帮助一致（已重生成
  `cli/_manifest_data.py`）。

## 后果

- 常见「线性步骤 + 前置检查」不再需要隐藏节点开关，`ToolSpec` 的表达力与
  CLI 表面一致；`needs` / `steps` / `preconditions` / `conditions` 边界清晰；
- 旧工具零改动：新字段默认空，`aggregate=None` 保留 AST 推断；
- `steps` 的目标任务依赖最后一步，步骤节点的进度行按真实动作名显示；
  纯聚合壳仍不计步数（0041 口径不变）。

## 交接

- 新工具若需要「先检查环境再执行」，优先写 `preconditions`，不要用
  `conditions` 或带开关的隐藏步骤；
- 顺序执行用 `steps`，需要并行的前置任务再补 `needs`；`steps` 与 `cmd` 互斥；
- 内部开关只通过 `step("task", key=value)` 注入，不要声明在用户可见签名上；
- 编排壳建议显式写 `aggregate=True`（或直接用 `steps`），不要依赖函数体推断。
