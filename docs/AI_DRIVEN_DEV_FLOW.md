# AI 自动驱动开发流（fcmd）

> 目标：把「需求意图 → 方案 → 代码 → 测试 → 发布」全流程交给 AI 编排，由**机器门禁**裁定对错。

## 1. 设计原则

| 原则 | 落地 |
|---|---|
| 门禁即裁判 | AI 不声称「做完了」，只看命令退出码：`make check-docs` / `check-fast` / `flow` / `check`、CI |
| 阶段化 + 卡点 | 每阶段：AI 动作 → 产出物 → 客观门禁 →（人类）审批点 |
| 失败自愈闭环 | 门禁失败输出回灌 AI → 定位修复 → 重跑，直到全绿（见 [AGENTS.md](../AGENTS.md)） |
| 上下文常驻 | [AGENTS.md](../AGENTS.md)（门禁分级 + 开工/收工）、`docs/adr/`（决策与交接）作为每轮必读 |

流水线：

```
需求意图 →[需求卡片]→ 方案 →[实现]→ 测试 →[门禁]→ 文档/版本 → 推送 → 发布
        ▲需求确认        ▲评审                          ▲上线 tag
```

## 2. 阶段总览

| 阶段 | 输入 / 触发 | AI 动作 | 产出物 | 客观门禁 | 人工卡点 |
|---|---|---|---|---|---|
| A 需求意图 | 用户口语需求 | 需求拆句（C1..Cn）、粗查影响面 | 需求卡片 `docs/requirements/NNNN-*.md` | 每条需求句有 C 编号，未决问题清零 | ✅ 需求确认 |
| B 方案制定 | 需求卡片 | 逐条核对代码、定不变量、收决策 D 表、分期 | ADR `docs/adr/NNNN-*.md`（长期决策才写） | 约束逐条有落点、D 表无「待定」 | ✅ 方案评审 |
| C 代码编写 | ADR / 需求卡片 | 按 P0→P1→P2 小步实现，每期回填 ADR 落地状态 | `src/fcmd/**` | `make check-fast` | — |
| D 测试编写 | 实现同轮 | 单测 / CLI 集成 / DSL 合成同步写 | `tests/test_*.py` | `make test` → `make cov`（≥95%） | — |
| E 集成与文档 | 全绿 | 更新 README / docs、回填 ADR 交接、bump 版本 | README、docs、`.bumpversion.toml` | `make check` + CI | — |
| F 推送与发布 | 评审通过 | 推全部 remote、打 tag | PyPI 包 + GitHub Release | `make push` + [release.yml](../.github/workflows/release.yml) | ✅ 上线 tag |

## 3. 阶段详解

### A 需求意图接入

把口语需求转成**需求卡片**（可实施约束 C1..Cn）：

1. 需求原句逐字保留（唯一副本）；
2. 逐句拆成 C1..Cn 可实施约束表；
3. 粗查疑似受影响的现有能力，按编号给出候选 `file:line`（精查留给 B）。

模板见 [REQUIREMENT_CARD_TEMPLATE.md](REQUIREMENT_CARD_TEMPLATE.md)。

**门禁**：每条需求句都有 C 编号约束，未决问题清零。**人工卡点【需求确认】**。

### B 方案制定

读代码逐条核实影响面（确认 / 排除 / 新增，补 `file:line`）。需要长期留痕的设计决策写成 ADR：

- 口径表沿用 C1..Cn，只补「设计落点」，不复制原文；
- 决策结论写进 D1..Dn 台账（含被否决项与代码落点）；
- 分期按 P0（核心能力）→ P1（外围接入）→ P2（体验完善）。

**门禁**：约定逐条有落点、D 表无「待定」。**人工卡点【方案评审】**。

### C 代码编写

按分期小步走，每改完即 `make check-fast`（gitkeep + adr-check + ruff + pyrefly，秒级反馈）。
每期完成时回填 ADR 的「落地状态」与「交接」。

**强制**：不跳 prek、不 `--no-verify`、commit message 用 conventional commits。

### D 测试编写与验证

与实现同轮写测试：核心逻辑单测、CLI 集成、DSL 命令合成。

命令链：`make test` → `make cov`（阈值 95%）。覆盖率口径固定于
[.coveragerc](../.coveragerc)（`fail_under = 95`，仅统计 `fcmd`），**不得注水**。

### E 集成门禁与文档

`make check`（check-fast + pyrefly linux + cov）。

文档更新：README / `docs/*` / ADR 的「落地状态 + 交接」；`make bump` 同步版本号与 `uv.lock`。

**门禁**：[ci.yml](../.github/workflows/ci.yml) 通过。

### F 推送与发布

`make push`（推全部 remote + tags）→ 打 tag `vX.Y.Z` →
[release.yml](../.github/workflows/release.yml)：构建 + PyPI（OIDC）+ GitHub Release。

**人工卡点【上线 tag】**：打 tag 前必须人工确认。

## 4. 命令速查

| 命令 | 用途 |
|---|---|
| `make check-docs` | L0 纯文档：gitkeep + adr-check |
| `make check-fast` | L1 配置 / 内环：gitkeep + adr + ruff + pyrefly |
| `make flow` | L2 代码：check-fast + test |
| `make check` | 全量：check-fast + pyrefly(linux) + cov |
| `make bump [minor\|major]` | 版本号 + tag + 同步 `uv.lock` |
| `make push` | 推送全部 remote 与 tags |

## 5. 项目特定护栏

- 禁 `--no-verify`、禁跳过 prek；conventional commits。
- 门禁按改动类型分级，混合改动与拿不准的情况按 L2 全链执行。
- 覆盖率不得注水（阈值 95%，omit 白名单固定）。
- 需求移除 / 变更先清理 ADR：删失效段落与决策行，不保留历史方案段；ADR 被替代或需求作废则**直接删除**该文件与全仓引用，编号不复用（git 历史可追溯）。
