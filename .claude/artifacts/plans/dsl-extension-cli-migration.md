# DSL 扩展与更多 CLI 脚本配置迁移 Implementation Plan

> Status: APPROVED
> Source: user request（无 spec；需求摘要见下）
> Mode: (default) Planner → Architect → Critic，1 轮迭代通过
> Iterations: 1 / 3
> Author: zhou
> Last updated: 2026-10-05

## Requirements summary

将 fcmd 中更多 exec 型 CLI 子命令迁移到 TOML 命令定义 DSL（`src/fcmd/commands/*.toml`）。
经代码核验，剩余可迁移目标被两个 DSL 能力缺口阻塞：

1. **list 参数**：`piptool i packages: list[str]` 需要 variadic positional 参数；
   引擎 argparse 层已支持 `list[X]` 注解（`apis/_tool_args.py:229,326`），但 DSL
   `_PARAM_TYPES` 未收录，且 tuple cmd 的 `{占位符}` 插值是字符串级
   （`apis/_tool_exec.py:125-145`），list 值会插成单个带空格 token，无 shell 执行下错误。
2. **bool 固定 token**：`autofmt lint --fix` 需要追加 `["--fix", "--unsafe-fixes"]`；
   现 DSL bool 只能插值 `true/false`（`_tool_exec.py:112-122`），无法表达固定 token 追加。

非候选（有 Python 逻辑，保留模块）：`dockercmd`（getpass 动态默认 + 结果打印，迁移需
为单一命令新增 env-default 能力，违反最小代码）、`reseticoncache`（平台守卫 + 文件存在
判断）、`sshcopyid`（远程脚本构造）、`bumpversion`（两阶段版本扫描）、`envdev*`、
`packtool`、`archivex`、`nettool`、`iptool`、`setenv`、`writefile`。

## Acceptance criteria

- AC-1: DSL 支持 `type="list"` 参数——positional（无 default）映射 `list[str]` 注解，
  tuple cmd 中**独占占位符项**（整项恰为 `{name}`）按元素展开，str cmd 中按空格拼接
  （沿用 `_value_to_cmd_str`）；测试覆盖两种 cmd 形态。
- AC-2: DSL 支持 bool 参数 `on` 字段——值为 truthy 时向 cmd 尾部追加固定 token 元组；
  `on` 仅 `type="bool"` 合法，非 bool 声明报 `CommandDeclError`；测试覆盖启用/未启用/
  校验失败。
- AC-3: `autofmt` 两个子命令（fmt/lint）全部迁入 `src/fcmd/commands/autofmt.toml`，
  Python 模块 `src/fcmd/cli/dev/autofmt.py` 删除。
- AC-4: `piptool` 的 `i`/`up` 迁入 `src/fcmd/commands/piptool.toml`；模块
  `src/fcmd/cli/dev/piptool.py` 仅保留 `u`/`r`/`f` + main；合并注册后
  `fcmd piptool --help` 同时可见 5 个子命令。
- AC-5: `make check` 本地全绿（ruff/pyrefly/全量测试），覆盖率不低于 95.5%。

## RALPLAN-DR

### Principles

- 最小代码：只为解锁真实迁移目标扩展 DSL，不为单一命令加能力（env-default 因
  dockercmd 一个候选而被砍）。
- 沿用既有抽象：list 展开复用引擎既有 `list[X]`→nargs 推导与 `_value_to_cmd_str`；
  bool on-token 注入沿用 `__dsl_empty_body__` 的函数属性模式，不膨胀 ToolSpec。
- 外科手术式改动：只动 DSL 三处与被迁移模块，不顺手重构 `_tool_exec` 其他逻辑。
- 行为变化显式声明：迁移导致的输出差异标注"待用户复核"。

### Decision drivers

1. 迁移收益（解锁多少子命令）vs DSL 复杂度。
2. 用户级 `~/.fcmd/commands.toml` 同样受益于 list/on（通用能力，非内置专用）。
3. 回归风险：引擎插值路径是热路径（所有 DSL 命令共用），改动必须向后兼容。

### Viable options

**Option A: 最小 DSL 扩展 + 定向迁移（选定）**
- 实现思路：DSL 增 `type="list"` 与 bool `on` 两个字段级能力；迁移 autofmt 全部 +
  piptool i/up；dockercmd 等保留 Python。
- 改动文件：`dsl/decl.py`、`dsl/synth.py`、`apis/_tool_exec.py`、新建
  `commands/autofmt.toml`、`commands/piptool.toml`、删 `cli/dev/autofmt.py`、瘦
  `cli/dev/piptool.py`、测试、README DSL 速查。
- Pros：解锁 4 个子命令；两个能力均为通用 DSL 语义（用户级配置同享）；改动面小。
- Cons：`_expand_cmd_placeholders` 增加元素级展开分支（热路径多一个 isinstance 判断）；
  autofmt 的完成提示 print 丢失。

**Option B: 零 DSL 扩展，仅迁今日可表达者**
- 实现思路：只迁 `piptool up`（零参数纯 exec）。
- Pros：零引擎改动。
- Cons：只解锁 1 个子命令，不满足"更多 CLI 脚本迁移"目标。
- Invalidation rationale：收益过低，rejected。

**Option C: 大而全扩展（list + on + env-default + post-run 消息钩子），迁 autofmt/piptool/dockercmd 全部**
- 实现思路：为 dockercmd 的 `getpass.getuser()` 增加 `{env:USERNAME}` 式默认值，
  为结果打印增加 post-run 消息字段。
- Cons：env-default 与 post-run 消息各只服务一个候选命令（单次使用不抽象）；
  post-run 消息钩子改变 DSL 定位（纯 exec/编排 → 混入展示逻辑）； rejected。

### Implementation steps（基于 Option A）

1. **decl.py 扩类型与参数键** — `src/fcmd/dsl/decl.py:44`（`_PARAM_TYPES` 增
   `"list"`）、`decl.py:73`（`_PARAM_KEYS` 增 `"on"`）、`decl.py:163-189`
   （`ParamDecl` 增 `on: tuple[str, ...] = ()` 字段与 docstring）、`decl.py:261-337`
   附近参数校验（`on` 仅 `type="bool"` 合法、元素非空，违反抛 `CommandDeclError`）。
2. **synth.py 类型映射与 on 注入** — `src/fcmd/dsl/synth.py:39`
   （`_TYPE_MAP["list"] = list[str]`）；`synth.py:88-122` `_synthesize_func` 末尾
   注入 `dsl_command.__dsl_param_on__ = {p.name: p.on for p in decl.args if p.on}`
   （沿用 `__dsl_empty_body__` 函数属性模式，零 ToolSpec 改动）。
3. **_tool_exec.py 展开语义** — `src/fcmd/apis/_tool_exec.py:138-145`
   `_expand_cmd_placeholders`：
   - tuple/list cmd 项若**整项恰为 `{name}`** 且对应值为 list → 以元素逐项替换该
     位置（元素级展开）；部分占位（`prefix-{name}`）且值为 list 时保持现状
     （`_value_to_cmd_str` 空格拼接），并在 decl.py 校验期对"签名内 list 参数在
     cmd 项中以非独占形式出现"给出 `CommandDeclError`（声明期即可判定）。
   - 展开后按 `getattr(spec.func, "__dsl_param_on__", {})` 对 truthy bool 值向
     cmd 尾部追加 token 元组。
4. **新建 commands/autofmt.toml** — `src/fcmd/commands/autofmt.toml`：多子命令形态；
   `fmt`（args.target type=path default="."，cmd `["ruff","format","{target}"]`）；
   `lint`（args.target 同上 + args.fix type=bool default=false
   `on=["--fix","--unsafe-fixes"]`，cmd `["ruff","check","{target}"]`）。
   注意 `default="."` 使 target 成为选项（现状为 positional 带 default，签名语义
   一致）；如需保持 positional 语义则 target 不写 default 而在 cmd 侧无占位回退——
   以现状签名（`target: str = "."` → 选项）为准。
5. **新建 commands/piptool.toml** — `src/fcmd/commands/piptool.toml`：
   `i`（args.packages type=list 无 default → positional nargs，cmd
   `["pip","install","{packages}"]`）；`up`（零参，cmd
   `["python","-m","pip","install","--upgrade","pip"]`）。
6. **瘦身/删除模块** — 删除 `src/fcmd/cli/dev/autofmt.py`；`src/fcmd/cli/dev/piptool.py`
   删除 `pip_install`/`pip_upgrade` 及 `__all__` 对应项（`_get_installed_packages` 等
   私有辅助仅 u/r/f 使用者保留）。
7. **测试** — `tests/test_dsl.py`：list 类型解析/校验（含非独占占位报错）、on 字段
   校验（非 bool 报错）、合成签名 list 注解、展开语义（tuple 独占展开 / str 空格
   拼接 / on 追加）；`tests/test_cli_autofmt*` 改走 DSL 注册入口或随模块删除；
   `tests/test_cli_piptool*` 删 i/up 用例、补合并注册后 5 子命令可见性断言
   （AC-4）。
8. **README 同步** — README「命令定义 DSL」字段速查补 `type="list"` 与 `on`。

### Workspace setup

- 实施前运行 `git status --short` 与 `git branch --show-current`。
- 本项目惯例为 main 分支直接小步提交（`make push`），历次迭代未用 worktree；沿用
  项目惯例，不建 worktree。若工作区不干净，先与用户确认是否混入既有改动。

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 元素级展开改变既有 str 插值行为（回归） | 仅"独占占位符 + list 值"走新分支，str 值路径不变；`tests/test_dsl.py` 补回归用例（既有 pymake/gittool 全量用例作回归网） |
| 非 shell tuple cmd 中 list 部分占位产生错误单 token | decl.py 声明期校验直接报错（签名内类型可查），不给运行时静默错误机会 |
| `__dsl_param_on__` 依赖函数属性约定，跨模块脆弱 | 与 `__dsl_empty_body__` 同模式且同文件族维护；在 synth.py docstring 注明契约 |
| autofmt 迁移后丢失完成提示输出 | 行为变化显式记录，标注"待用户复核"；如需保留，后续评估 post-run 消息能力（本次不做） |
| piptool i 空 packages → `pip install` 无参报错 | 与 Python 版现状一致（positional 至少一参，argparse 拦截），行为不变 |
| 覆盖率回落（新分支未覆盖） | 新分支逐条配测试；AC-5 以 cov ≥ 95.5% 为门禁 |

## Verification steps

- AC-1/AC-2：`uv run pytest tests/test_dsl.py -k "list or on_token"` 全绿。
- AC-3：`uv run fcmd autofmt fmt --target src`、`uv run fcmd autofmt lint --fix`
  行为正确；仓库内 `grep -r "autofmt" src/fcmd/cli` 无残留。
- AC-4：`uv run fcmd piptool --help` 可见 i/u/r/f/up 五个子命令；`uv run fcmd piptool i --help`
  显示 packages positional。
- AC-5：`make check` 本地全绿，cov ≥ 95.5%。

## ADR

- **Decision**: DSL 新增 `type="list"` 参数与 bool 参数 `on` 固定 token 两个通用能力，
  据此将 autofmt 全部子命令与 piptool 的 i/up 迁入内置 TOML；其余含 Python 逻辑的
  模块保留。
- **Drivers**: 迁移收益/复杂度比（driver 1 起决定作用）；用户级配置同享通用能力
  （driver 2）；热路径向后兼容（driver 3）。
- **Alternatives considered**: Option B（零扩展）rejected——收益过低；Option C
  （大而全）rejected——env-default/post-run 均为单次使用抽象且改变 DSL 定位。
- **Why chosen**: 两个扩展均为声明式 exec DSL 的通用语义（非单一命令定制），改动
  集中在三个文件 + 声明期校验，风险可控；迁移目标全部达成。
- **Consequences**: 用户级 commands.toml 获得同能力；`_expand_cmd_placeholders`
  语义文档化义务增加；autofmt 输出行为变化需用户复核。
- **Follow-ups**: dockercmd 若后续确认要迁，需先设计 env-default 语义（进 backlog，
  本次明确不做）。

## Open questions

- autofmt 迁移后不再打印"ruff format 完成"类提示——**待用户复核**（默认接受丢失，
  与 pymake/gittool 已迁移子命令行为一致）。

## Review trail

- Planner draft v1: 提出 A/B/C 三选项，选定 A（最小扩展 + 定向迁移），步骤 8 条
  全部 cite 文件与行号。
- Architect challenge v1: steelman "on-token 是为 autofmt lint 一个命令加的能力"
  → 反驳成立性不足（bool flag→token 是用户级配置通用场景）；确认唯一真 tension
  是热路径 `_expand_cmd_placeholders` 复杂度 vs DSL 表达力，取舍依据 = 新分支仅
  窄条件命中 + 声明期校验拦截非法形态；采纳"非独占 list 占位声明期报错"。
- Critic verdict v1: APPROVED（7 维度全过）。Reservations：(1) autofmt 输出变化
  必须显式标注待用户复核（已列入 Open questions）；(2) `__dsl_param_on__` 函数
  属性契约需在 synth.py docstring 写明（已并入 step 2）。
- Final iterations: 1 / 3。
