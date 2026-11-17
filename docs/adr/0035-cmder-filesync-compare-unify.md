# 0035 目录比较 / 同步引擎统一：cmderw 与 filesync 共用一套纯逻辑

- 状态：设计中（独立立项，尚未实施）
- 日期：2026-11-19
- 关联：0005 / 0012（cmderw 比较与同步入口）、0034（全量 Qt 迁移后的代码收敛）、0004（重构待办与触发条件）

## 背景

仓库里现有两套互相独立、语义高度重叠的「目录比较 + 同步」实现：

| 位置 | 规模 | 模型 | 主要调用方 |
|---|---:|---|---|
| `src/fcmd/apps/cmder/_ops.py` | 约 560 行（`compare_trees` / `build_sync_plan` / `revalidate_sync_items` / `execute_sync_deletes` 及 DTO） | `CompareOptions` / `CompareEntry` / `CompareReport` / `SyncOptions` / `SyncItem` / `SyncPlan` / `SyncRunReport` | `_cmder_qt_dialogs.CompareDialog` / `SyncDialog`；`tests/apps/cmder/test_ops.py` |
| `src/fcmd/apps/filesync/_core.py` | 约 400 行（`scan_tree` / `compare_trees` / `build_sync_plan` / `execute_plan`） | `CompareOptions` / `FileEntry` / `DiffItem` / `CompareResult` / `SyncAction` / `SyncPlan` / `SyncResult` | `filesync` CLI 与 `filesyncw` Qt 窗口；`tests/apps/filesync/` |

两套实现都在维护目录遍历、隐藏项 / 掩码过滤、大小 / 时间 / 内容比较、同步方向与
冲突策略、删除策略、错误收集与进度回调；差异只在外围开关与 UI 需要的 DTO 形状。
新增比较口径（例如「仅比较文件」「符号链接策略」「目录删除」）必须改两处、测两遍，
已有漂移风险。

本轮（0034 全量 Qt 化 + 0029/0030 窗口合并）之后，GUI 已不再是迁移阻碍；
本 ADR 只处理这组纯逻辑的合并，**不改 CLI 行为与 UI 交互**。

## 口径对照

| 能力 | cmder (`_ops`) | filesync (`_core`) | 统一后归属 |
|---|---|---|---|
| 递归遍历 | 有 | 有 | filesync `scan_tree` |
| 包含 / 排除掩码 | `mask` / `exclude_mask`（分号 / 空白分隔，`_ops.split_masks`） | `include` / `exclude`（元组 glob） | filesync 模型 + cmder 适配层解析掩码 |
| 隐藏项 | `include_hidden` | `skip_hidden`（反向） | filesync 模型 |
| 比较口径 | 名称 / 大小 / 时间 / 内容 | 时间+大小 / 大小 / 内容 | 取并集：名称、大小、时间、内容 |
| 时间容差 | `mtime_tolerance` | `time_tolerance` | 单字段 `time_tolerance` |
| 仅比较文件 | `files_only` | 无（由掩码覆盖） | filesync `CompareOptions` 增加 `files_only` |
| 符号链接 | 不跟随，按节点类型比较 | 不跟随 | 沿用 filesync 语义 |
| 错误收集 | `errors: list[(Path, str)]` | `errors: list[ScanError]` | filesync `ScanError`（保留相对路径） |
| 同步方向 | 左→右 / 右→左 / 双向 | 双向 / 镜像 / 更新 | 取并集（新增 `ltr` / `rtl` 到 filesync 模式表） |
| 冲突策略 | 总是覆盖 / 仅较新 / 跳过 | 双向按较新、无策略枚举 | filesync 增加冲突策略字段 |
| 删除目标端多余项 | 可选，含目录开关 | 镜像模式内建删除 | filesync `SyncOptions` 增加「删除多余项 / 目录」开关 |
| 执行前复核 | `revalidate_sync_items`（大小 / 时间漂移则跳过） | 执行时逐项检查 | 复用 cmder 的复核口径收进 filesync |
| 执行方式 | 复制入 cmder 传输队列；删除走回收站 | 直接复制 / 删除，支持 `dry_run` 与进度 | 执行器拆「计划 → 执行项」，由调用方提供复制 / 删除原语 |
| 进度回调 | 队列逐项进度 | `progress` 回调 | 统一为 `(done, total, relative)` |

## 决策（D 表）

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | 以 **`filesync/_core.py` 为唯一纯逻辑引擎**，cmder 只保留 UI 适配 | 以 cmder `_ops` 为唯一引擎：filesync CLI 的 `two_way` / `mirror` / `update` 语义与 `--preview` 是既有契约，迁到 cmder 模型会改 CLI 输出 | `src/fcmd/apps/filesync/_core.py` |
| D2 | 统一模型命名为 `CompareOptions` / `DiffItem` / `CompareResult` / `SyncPlan` / `SyncAction`，cmder 直接消费，不再保留 `CompareEntry` 等 DTO | 保留两套 DTO + 双向转换：只是把重复从算法挪到映射，漂移仍存在 | cmder `_cmder_qt_dialogs` / `_ops` 删除对比 / 同步段 |
| D3 | 同步执行拆成「计划 + 执行原语」：`filesync` 提供 `plan → execute(plan, copy=…, delete=…)`，复制 / 删除由调用方注入 | 统一执行器直接复制 / 删除：cmder 无法复用传输队列（逐项取消 / 重试），filesync 也无法保留 `dry_run` | `filesync._core.execute_plan` 增加原语注入；cmder 传队列入队与回收站函数 |
| D4 | cmder 的「执行前复核」下沉为 `revalidate_items(plan)` 供两处共用；filesync CLI 也启用 | 只给 cmder 保留复核：CLI 预演后到执行前的漂移仍无保护 | `filesync._core.revalidate_items` |
| D5 | 分四期落地，每期独立可回退：**P0 契约测试 → P1 filesync 增强 → P2 cmder 切换 → P3 删除旧实现** | 一次性替换：两套语义差异（掩码、方向、删除开关）无法在单次提交内核对，失败时无法定位 | 见「分期」 |
| D6 | 暂不合并 `filesync` 的 `diff_lines`（文本比较）与 cmder `_diff` | 文本 diff 与目录 diff 数据结构不同，合并收益低于风险 | 保持现状 |

## 分期与验收

### P0：行为对照测试（先测后改）

- 新建 `tests/apps/filesync/test_compare_contract.py`：用临时目录树覆盖
  「仅左 / 仅右 / 大小不同 / 时间不同 / 内容不同 / 相同 / 隐藏 / 掩码 / 目录 / 符号链接 / 权限错误」，
  对同一输入分别调用两套 `compare_trees`，断言差异集合一致（允许已知口径差：`files_only`、方向名）。
- 同一组目录树对 `build_sync_plan` 做计划对照（复制 / 覆盖 / 跳过 / 删除计数）。
- 本阶段只新增测试，不改实现；两套实现先共同锁定行为。

**验收**：对照测试通过（允许显式列出的差异用例跳过并标注），`make flow` 全绿。

### P1：filesync 引擎补齐 cmder 口径

- `CompareOptions` 增加 `files_only`、`mode="name"`；冲突策略字段
  `conflict`（`overwrite` / `newer` / `skip`）与同步方向 `ltr` / `rtl` / `both`。
- `SyncOptions` 增加 `delete_target_extra` / `delete_dirs`；`build_sync_plan` 保持
  `two_way` / `mirror` / `update` 旧入口为参数映射（CLI 行为不变）。
- 抽出 `revalidate_items(plan)` 与 `execute_plan(plan, copy=…, delete=…)`；
  `filesync` 默认原语沿用现有实现。
- cmder 掩码解析（`_ops.split_masks`）保留在 cmder 适配层，转成 `include` / `exclude` 元组。

**验收**：filesync CLI 与 `filesyncw` 现有测试不改断言即通过；新增口径有单测；
P0 对照测试覆盖新增字段。

### P2：cmder 切换到统一引擎

- cmder `CompareDialog` 使用 `filesync._core.CompareResult / DiffItem`；`SyncDialog`
  使用统一 `SyncPlan`；复制入队与删除原语注入，传输队列行为不变。
- 删除 cmder `_ops` 中 `CompareOptions` / `CompareEntry` / `CompareReport` /
  `SyncOptions` / `SyncItem` / `SyncPlan` / `SyncRunReport` 及 `compare_trees` /
  `build_sync_plan` / `revalidate_sync_items` / `execute_sync_deletes`。
- `tests/apps/cmder/test_ops.py` 的比较 / 同步用例改为对统一引擎的行为断言；
  cmder 专属（掩码解析、队列入队、回收站）继续保留。

**验收**：cmder 比较 / 同步的既有用户路径（含 Ctrl+Shift+C、预演、执行、结果窗口）
行为不变；`make flow` 与 `make cov` 全绿。

### P3：清理与文档

- 删除本 ADR 的过渡说明；更新 0005 / 0012「比较与同步」实现落点、
  0004 的重构待办；README 中 cmderw / filesync 的能力描述不改（行为不变）。
- 记录净减行数（预期 `_ops.py` 减少约 500 行，`_core.py` 增加约 150–250 行）。

## 测试与门禁

- 纯逻辑单测：统一引擎的遍历、比较、计划、复核、执行原语、错误收集；
- 对照测试（P0）在 P3 前保留，P3 后改为仅针对统一引擎的单测（对照价值已完成）；
- Qt 胶水仍不追求覆盖率；`make flow` 每期必跑，收尾 `make check`；
- 不新增可选依赖，不改变 `pyproject.toml` 入口与 CLI 参数。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 两套时间容差 / 内容比较细节不一致导致结果变化 | P0 对照测试按用例列出差异；P1 明确字段语义并在 ADR 记录 |
| cmder 传输队列的逐项取消 / 重试在统一执行器里丢失 | D3 原语注入；队列仍在 cmder 侧，执行器只逐项调用 |
| 双向同步的较新 / 冲突判定与 filesync 旧行为不同 | P1 保留旧入口参数映射，CLI 测试断言不改；新字段只在 cmder 启用 |
| 大目录树性能回退 | 对照测试加入 5k 条目的基准用例；P2 前后各测一次（不作为慢测试默认跑） |
| 删除目录的安全边界 | 沿用 `filesync` 现有「删除逐项、失败即停、只删目标端多余项」逻辑；cmder 额外确认框保留 |

## 交接

- 开工前先按 `docs/AI_DRIVEN_DEV_FLOW.md` 的 D 阶段补齐需求卡片（若需要新能力），
  再从 P0 开始；P1 之前不改任何调用方。
- 每期独立提交，提交信息用 `refactor(filesync,cmder): …`；P2 的提交不得同时改
  filesync CLI 行为。
- 相关代码入口：`src/fcmd/apps/cmder/_ops.py`（比较 / 同步段）、
  `src/fcmd/apps/cmder/_cmder_qt_dialogs.py`（CompareDialog / SyncDialog）、
  `src/fcmd/apps/filesync/_core.py`、`src/fcmd/apps/filesync/_filesyncw_qt.py`。
- 现状基线（2026-11-19）：`_ops.py` 2,579 行、`_core.py` 824 行、
  `tests/apps/cmder/test_ops.py` 1,905 行；`make test` 约 20 秒。
