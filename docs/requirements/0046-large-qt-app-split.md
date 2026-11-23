# 需求卡片：大型 Qt 应用拆分（wslman / llmw）单独立项

- 编号：0046（与 ADR 同号）
- 来源 / 提出人：用户
- 日期：2026-11-26
- 关联 ADR：[0046](../adr/0046-large-qt-app-split.md)

> 本卡片是需求原文与约束编号的唯一副本；ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 请单独立项解决

> 注：上句承接前序结构优化结论——`apps/wslman/_wslmanw_qt.py`（约 5.3k 行）、
> `apps/llm/_llmw_qt_center.py`（约 2.3k 行）等 Qt 大文件不适合混在 P0–P5
> 结构优化里一次做完，用户要求「单独立项解决」。

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 单独立项 | 以需求卡片 + ADR 形式立项，登记 ADR 索引；不改运行代码 | `docs/adr/0046-*.md`、`docs/adr/README.md`、`docs/requirements/0046-*.md` | `make adr-check`、评审通过 |
| C2 | 解决大文件 | 给出 wslman / llmw 的目标模块边界、状态归属与迁移顺序，不按行数盲目切分 | ADR D2–D5 | 评审通过 |
| C3 | 单独立项 | 明确分期 P0–P5，每期只迁 1 个页面/对话框，独立 PR 可回滚 | ADR「分期」 | 提交记录与 ADR 状态一致 |
| C4 | 解决布局挡住拆法 | `apps/<app>/qt/<module>.py` 允许 3 层目录；其他层保持 ≤2；`qt/` 内不再嵌套 | `tests/test_layout.py`、`docs/adr/0003` | layout 守卫用例 |
| C5 | 不改行为 | 公开命令、设置文件名、窗口标题、默认尺寸不变；拆分前后同一批 smoke 通过 | `apps/wslman/*`、`apps/llm/*` | smoke + 设置 roundtrip |
| C6 | 可测试 | 页面可独立构造；页面级测试覆盖列表刷新、详情切换、对话框确认/取消、设置 roundtrip | `tests/apps/wslman/`、`tests/apps/llm/` | 新增页面测试 |
| C7 | 防重新长回 | 拆分后从行数白名单删除对应文件；新增页面直接放 `apps/<app>/qt/` | `tests/test_architecture.py` 白名单 | 行数守卫 |
| C8 | 兼容外部 import | `apps/wslman/_wslmanw_qt.py`、`apps/llm/_llmw_qt_*.py` 保留一版兼容 shim | 同上 | 导入路径用例 |

## 现状影响面

| # | 受影响能力 | 位置 | 备注 |
|---|---|---|---|
| 1 | wslman 整窗 | `apps/wslman/_wslmanw_qt.py` | `WslmanQtWindow` 约 3k 行，页面/对话框混排 |
| 2 | wslman 业务/CLI | `apps/wslman/wslman.py`、`wslmanw.py` | Qt 拆分不改业务 API |
| 3 | llmw 模型页 | `apps/llm/_llmw_qt_center.py` | 列表/详情/下载/转换/量化 |
| 4 | llmw 运行页 | `apps/llm/_llmw_qt_run.py` | 服务看护 + 参数 + 日志 |
| 5 | 布局守卫 | `tests/test_layout.py` | 需按根目录配置深度上限 |
| 6 | 行数守卫 | `tests/test_architecture.py` | 白名单随拆分递减 |
| 7 | 窗口测试 | `tests/apps/wslman/test_wslmanw_qt.py`（约 3.6k 行） | 后续拆为页面级测试 |

## 未决问题

- [x] 是否放宽整个仓库的目录深度？→ 否，只对 `apps` 放宽到 3 层（ADR D1）
- [x] 是否一次性迁移 llmw 全部 `_llmw_qt_*.py`？→ 否，只迁触达页面，保留 shim（ADR D5）
- [x] 是否借机改 UI？→ 否，行为冻结（ADR D6）
- [x] 先拆哪个页面？→ 先对话框/行卡/详情等低耦合组件，再拆列表页（ADR D4）

## 卡点

- [x] 每条需求句都有 C 编号约束
- [x] 未决问题全部有结论
- [x] P0 布局例外与 `apps/*/qt/` 骨架已落地（ADR 0046）
- [x] P1–P3：wslman 对话框 / 页面 mixin / 行卡 / 初始化 / SSH 迁移完成
- [ ] P4–P5：llmw 页面拆分与最终清理
