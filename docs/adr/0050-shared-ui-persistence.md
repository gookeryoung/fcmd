# 0050 GUI 数据持久化共用方案：设置、界面状态与路径历史

- 状态：P0 已落地（P1 / P2 待办）
- 日期：2026-11-24
- 关联：[0042](0042-shared-gui-shell-and-theme.md)（GUI 外壳）、[0046](0046-large-qt-app-split.md)（状态归属）、
  [0019](0019-filescan.md)（filescan 已落地部分）、[0018](0018-peview.md)（recent_files）

## 背景

各 Qt 窗口目前都能持久化设置，但口径分散：

- 底层已有 `fcmd.gui.settings.SettingsStore`（`ui/<app>.json`、`.bak` 回退、原子写入），
  但上层字段命名、解析与写出仍由各应用自己维护；
- 窗口几何 / 侧边栏折叠由 `QtMainWindow` 统一处理，页面、页签、分栏宽度、表格列宽、
  筛选与排序等「下次打开还在这里」的界面状态多数没有落盘；
- 文件 / 目录对话框的上次位置没有统一能力，filescan 刚用 `last_task_dir` /
  `last_import_dir` / `last_export_dir` 单独实现，其它应用仍需重复；
- 最近文件只有 peview 有 `recent_files`，cmder 的 `open_with` 是另一套写法；
- 结果 / 缓存这类可重建的大数据与用户偏好混在同一层目录里，缺少「该放 `ui/` 还是
  `cache/`」的明确边界。

目标：在不改变现有设置文件名与字段兼容性的前提下，把「用户偏好 + 轻量界面状态 +
路径历史」收敛成一套共用能力，让每个窗口以最小成本获得一致的使用体验。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | 沿用 **一个应用一个设置文件**：`ui/<app>.json`，继续通过 `SettingsStore` 原子写入并保留 `.bak`；不引入全局设置库 | 集中式 SQLite / 单文件存全部应用（任一应用写坏会影响所有窗口；迁移成本与耦合都高） | `gui/settings.py` 扩展 |
| D2 | 明确存储分层：`ui/` 只放**用户偏好 + 轻量界面状态 + 路径历史**；扫描结果、模型列表缓存、日志等**可重建数据**放 `cache/<app>-*` | 大结果继续塞进 `ui/*.json`（启动解析慢、写盘放大、损坏时一并丢失偏好） | 各应用数据文件路径 |
| D3 | 新增共用模块 `fcmd.gui.uistate`（暂定名），提供 `PathHistory`、`RecentFiles`、`WindowState`、`DebouncedSaver`；字段级校验仍由 shared 模块统一实现 | 每应用继续手写 `_dialog_dir` / `_remember_dir`（重复且行为不一致） | `src/fcmd/gui/uistate.py` |
| D4 | 对话框路径按**用途**命名：`last_open_dir` / `last_save_dir` / `last_source_dir` / `last_output_dir`；领域语义更强时可加专用键（如 filescan 的 `last_task_dir`），同一应用内复用同一键 | 全应用共用一个 `last_dir`（扫描目录与导出目录互相覆盖，体验反而变差） | `PathHistory` |
| D5 | 允许保存的界面状态限定为小体积、可重建：当前页 / 页签、分栏尺寸、表格列宽、筛选与排序、紧凑模式；**不保存**临时展开项、滚动像素位置、选中行索引 | 保存全部控件状态（字段爆炸、版本稍变就失效，且恢复出用户不认识的旧状态） | `WindowState` + 各页面 |
| D6 | 写入节流：文本 / 筛选类改动 300 ms 防抖，开关与对话框完成立即写；窗口关闭强制 flush；批量重建期间暂停写盘 | 每次 `textChanged` 都写 JSON（输入卡顿、SSD 放大）；只在关闭时写（崩溃丢失整场状态） | `DebouncedSaver` |
| D7 | 字段解析沿用「逐字段容错」：缺失 / 类型不符回落默认值，未知字段忽略；新增可选 `schema_version`（缺失视为 1），未来版本只读已知字段、不破坏性重写 | 整份设置版本不符即丢弃（升级一次丢全部偏好）；严格校验整份 JSON（一个坏字段作废全部设置） | `SettingsStore` / 各 app mapping |
| D8 | 最近文件 / 目录用共用列表规则：去重（Windows 路径大小写不敏感）、最新置顶、上限 10、写入前过滤不存在路径；不在 `ui/` 保存敏感信息（API Key / token / 命中原文） | 只追加不去重（列表迅速膨胀且重复）；保存已删除路径（点击才报错） | `RecentFiles` |
| D9 | filescan 已落地的 `last_task_dir` / `last_import_dir` / `last_export_dir` 保留字段名，内部改走 `PathHistory`；其它应用按 D4 的标准键接入，不强制改名 | 为统一命名做一次性迁移（破坏旧设置读取，收益仅是代码好看） | `apps/filescan`、其它 app |

## 共用 API（拟定）

```python
from fcmd.gui.settings import SettingsStore
from fcmd.gui.uistate import DebouncedSaver, PathHistory, RecentFiles

store = SettingsStore("filescanw")
payload = store.load() or {}
paths = PathHistory(payload)
paths.start("last_open_dir")
paths.remember("last_open_dir", chosen)
recent = RecentFiles(payload)
recent.add(path)
saver = DebouncedSaver(lambda: store.save(payload), delay_ms=300)
saver.touch()
saver.flush()
```

`PathHistory` / `RecentFiles` 只负责「读映射 → 校验 → 写映射」，不依赖具体控件；
各窗口仍用自己的 dataclass 承载业务设置，避免 shared 层知道应用字段。P1 的
`WindowState` 沿用同一口径，在实现后补进示例。

## 应用接入矩阵

| 应用 | 已持久化 | 本方案补充 |
|---|---|---|
| filescanw | 设置、任务、结果、几何、紧凑模式、三个对话框目录 | 当前页 / 筛选 / 列宽改走 `WindowState`；`PathHistory` 替换本地实现 |
| cmderw | 设置、`open_with` 最近程序、几何 | `last_open_dir` / `last_save_dir`、当前页、列宽、最近目录 |
| wslmanw | 设置、几何、侧边栏 | 上次选中的发行版 / 页面、导入导出目录 |
| llmw | 设置、几何、模型列表缓存 | 当前页 / 页签、上次模型、`last_open_dir` / `last_save_dir` |
| pevieww | 设置、`recent_files` | 改用 `RecentFiles`；补上次打开目录 |
| renamerw / reshackw / pptmakew | 设置、几何 | `last_open_dir` / `last_save_dir`、上次预设 |
| dynacalc / paramcad / paramesh | 设置、几何 | 上次输入 / 输出目录、上次算例 / 模板 |
| 各窗口缓存 | `cache/`（部分） | 统一按 D2 归位，不写进 `ui/` |

## 分期

- **P0（路径历史 + 防抖）**：新增 `PathHistory` / `DebouncedSaver`；filescan 改走共用实现；
  peview 最近文件接入 `RecentFiles`；补单测覆盖路径失效、去重、上限、flush。
- **P1（窗口界面状态）**：新增 `WindowState`；filescanw、llmw、wslmanw、cmderw 接入
  当前页 / 页签、分栏尺寸、表格列宽；重开窗口恢复上次页面布局。
- **P2（最近列表 + 缓存分层 + schema）**：各文件工具接入最近目录 / 文件；`ui/` 与
  `cache/` 边界清理；加入可选 `schema_version` 与一次迁移测试。

## 验证

- `PathHistory`：路径存在 → 返回目录；文件 → 返回父目录；不存在 → 返回 `""`；
  保存后立即从同一映射读回。
- `RecentFiles`：去重（大小写不敏感）、最新置顶、上限裁剪、过滤不存在路径、
  非法映射回落空列表。
- `DebouncedSaver`：连续 `touch` 只写一次；`flush` 立即写；关闭路径不抛异常。
- `WindowState`：缺失 / 类型不符回落默认值；未知字段与未来 `schema_version` 不丢
  已知字段、不破坏性重写。
- 各应用冒烟：设置文件缺失 / 损坏（仅 `.bak` 可用）时正常启动；恢复上次目录 /
  页面 / 列宽；窗口关闭后重开一致。
- 门禁：共享模块改动按 L2 跑 `make flow`；纯文档阶段跑 `make check-docs`。

## 后果

- 用户体验统一：文件对话框落在上次位置，窗口重开回到上次页面与列宽，最近文件
  不用重新找；
- 存储风险收敛到 `SettingsStore` 一处：原子写、`.bak` 回退、逐字段容错继续有效；
- 不改变现有设置文件名与已发布字段：旧配置可继续读取，未迁移的应用不受影响；
- 新增 shared 模块后，各应用只保留业务字段与页面绑定，减少重复的路径 / 列表 /
  防抖代码；代价是 shared 层需要维护一套稳定的字段命名与上限约定。

## 落地状态

- P0：已落地（`src/fcmd/gui/uistate.py` 提供 `PathHistory` / `RecentFiles` /
  `DebouncedSaver`；filescanw 的对话框目录改走 `PathHistory`，peview 的最近文件改走
  `merge_recent` / `normalize_recent`；单测见 `tests/gui/test_uistate.py`）。
- P1：待办（`WindowState` 与页面 / 列宽恢复）。
- P2：待办（各文件工具接入、`ui/` 与 `cache/` 边界清理、`schema_version` 迁移）。
