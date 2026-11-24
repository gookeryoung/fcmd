# 需求卡片：pptmake 布局设计与功能完善

- 编号：0055（卡片存 `docs/requirements/0055-pptmake-editing-experience.md`；与 ADR 同号）
- 来源 / 提出人：用户（会话内提出）
- 日期：2026-11-15
- 关联 ADR：[0055](../adr/0055-pptmake-editing-experience.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 请结合最佳时间，对pptmake的布局设计和功能完善，给出建议方案

> 可以

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 「布局设计」 | 保留侧边栏「设计 / 样式」两页，不再加「导出」等页面；导出继续走页标题行主按钮 + 对话框；全屏放映用 `F5` 覆盖层 | `_pptmakew_qt.py` | 目视 + 离屏测试 |
| C2 | 「布局设计」 | 设计页收敛为单一导航源：缩略图排序器（网格 / 列表可切换）、检查器改为「内容 / 版式 / 备注」页签、布局库用版式缩略图 | `_pptmakew_qt.py`、`_design.py` | 结构断言 / 目视 |
| C3 | 「布局设计」 | 样式页宽屏左右并排（设置卡 | 实时预览）、窄屏堆叠；预览随手改即时刷新；提供「应用到全部页面 / 仅当前页」 | `_pptmakew_qt.py` | 离屏测试 |
| C4 | 「功能完善」 | 反馈可见：缩略图右上角溢出角标、设计页状态栏「第 i/n 页 · k 处溢出」、导出前检查（溢出 / 字体 / 图片缺失） | `_pptmakew_qt.py`、`_preview.py` | 单测 + 离屏测试 |
| C5 | 「功能完善」 | 编辑体验：撤销 / 重做（≥50 步）、dirty 标记、关闭前确认、60s 自动保存草稿与恢复、最近模板、拖拽重排与多选 | 新增 `_history.py`、窗口设置 | 纯逻辑单测 + 离屏测试 |
| C6 | 「功能完善」 | 内容模型 v3：图片（路径 + 填充方式 + alt）、要点层级、每页强调色入口、页脚 / 页码 / Logo、标题与正文字体选择器 | `_core.py`、`_design.py`、`_pptx.py`、`_preview.py` | 覆盖率 / golden / 导出回归 |
| C7 | 「最佳实践」 | 响应式与可访问性：动作条用 `FlowLayout` 自动换行、分栏尺寸持久化、`≥1200 / 900–1199 / <900` 断点、图标按钮全带 tooltip、键盘快捷键 | `_pptmakew_qt.py`、`pptmakew.py` | 960×560 与 125% / 150% DPI 无裁切 |
| C8 | 「功能完善」 | 导出与 CLI：导出对话框（页码范围 / 备注 / PDF）、`pptmake validate`、`pptmake doctor`、`pptmake build` | `_pptx.py`、`_office.py`、`pptmake.py` | CLI 测试 + `make flow` |
| C9 | 「方案」技术口径 | 纯逻辑与 Qt 分离：新增 `_history.py` / `_assets.py` / `_brand.py` / `_bullets.py`；模板 schema v3 向后兼容；Qt 窗口按页面拆分 | `apps/pptmake/*` | 覆盖率门槛 / 结构测试 |
| C10 | 「方案」范围 | 分 S0–S3 落地：本轮先交付 S0（低风险布局与反馈），S1–S3 按优先级推进；不做自由形状、动画 / 转场、协作编辑 | 全链 | `make flow`、ADR 交接 |

## 现状影响面（候选，待 ADR 核实）

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | 设计页三栏与动作条 | `_pptmakew_qt.py:_build_design_page` | 动作条固定横排，窄窗口易裁切 |
| 2 | 分栏尺寸 | `_pptmakew_qt.py`、`pptmakew.py` | `QSplitter` 未持久化 |
| 3 | 样式页布局 | `_pptmakew_qt.py:_build_style_page` | 预览在表单下方，需滚动 |
| 4 | 溢出反馈 | `_preview.py:overflow_issues`、`_refresh_thumbnails` | 只在画布底部提示 |
| 5 | 页面导航 | `_slides_list` / `_thumbs` | 列表与缩略图重复承担跳页 |
| 6 | 内容模型 | `_core.py:SlideSpec` | 无图片 / 要点层级 / 品牌字段 |
| 7 | 设置字段 | `pptmakew.py:PptmakeSettings` | 已含页面 / 侧边栏，需补分栏 |
| 8 | 测试口径 | `tests/apps/pptmake/test_pptmakew_qt.py` | 需覆盖 S0 结构 |

## 未决问题

- [x] 范围：S0 本轮落地（用户确认「可以」），S1–S3 分期推进。
- [x] 图片资产口径：模板 JSON 存相对路径 + 哈希，旁边 `*.assets/`，导出时内嵌（S2 决策）。
- [x] 是否加第三个侧边栏页：不加，导出走对话框、放映走 `F5`。

## 卡点

- [x] 每条需求句都有 C 编号约束（无遗漏、无「待定」）
- [x] 未决问题全部有结论，需求方确认
