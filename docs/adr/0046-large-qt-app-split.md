# 0046 大型 Qt 应用拆分：wslman / llmw 的页面化与窗口瘦身（立项）

- 状态：立项（P0 未实施，本文确定分期 / 边界 / 验收，不动运行代码）
- 日期：2026-11-26
- 关联：[0003](0003-package-layout.md)（布局不变量）、[0034](0034-qt-full-gui-migration.md)（Qt 迁移）、
  [0038](0038-shared-qt-widget-boundaries.md)（共享控件边界）、[0042](0042-shared-gui-shell-and-theme.md)（窗口外壳 / 页面基类）
- 需求卡片：[docs/requirements/0046](../../requirements/0046-large-qt-app-split.md)

## 背景

结构优化 P0–P5 已完成 `core` / `infras` / `cli` / `gui` 与大文件拆分，但 `apps/` 仍是
最大的单文件聚集地：

| 文件 | 行数 | 现状 |
|---|---:|---|
| `apps/wslman/_wslmanw_qt.py` | ~5,300 | 单个 `WslmanQtWindow` 类约 3,000 行，页面方法、对话框、行卡全在一个模块 |
| `apps/wslman/wslman.py` | ~3,000 | CLI 与业务逻辑；与 Qt 共享同一批数据模型 |
| `apps/llm/_llmw_qt_center.py` | ~2,300 | 模型中心页（列表 / 详情 / 下载 / 转换 / 量化全部方法） |
| `apps/llm/_llmw_qt_run.py` | ~2,200 | 运行页 + 服务看护 + 参数表单 |
| `apps/llm/_llmw_qt_chat.py` / `_llmw_qt_paint.py` | ~1,100 / ~1,300 | 对话 / 画图页 |
| `tests/apps/wslman/test_wslmanw_qt.py` | ~3,600 | 单文件覆盖整窗所有交互 |

这不是「文件长了就拆」的审美问题：

1. **改一处要跨屏定位**：页面状态、信号连接、行卡刷新混在同一个类，新增一个页面
   动作要在 3k 行类里找现有约定；
2. **测试粒度被迫退化**：只能整窗构造 + 点击路径，难以按页面/对话框做纯逻辑测试；
3. **布局规则挡住了正确的拆法**：`tests/test_layout.py` 的 `MAX_DIR_LEVELS = 2`
   让 `apps/wslman/qt/files.py` 这类合理结构无法落地，只能继续用
   `_wslmanw_qt_xxx.py` 扁平前缀；
4. **与已完成的共享层脱节**：`gui.AppPage` / `WindowSpec` / `SettingsStore` 已具备
   页面化条件，apps 仍沿用单窗口堆叠模式。

## 目标与非目标

**目标**

- wslman / llmw 的 Qt 层按「页面 / 对话框 / 共享状态」拆分，单文件回到可维护规模；
- 保持 UI 行为、信号、设置文件与入口命令（`wslmanw` / `llmw`）不变；
- 让页面可以独立构造与测试，减少整窗 smoke 依赖；
- 用布局/行数守卫防止重新长回单文件。

**非目标**

- 不重做 UI 视觉、不改变交互流程；
- 不合并 wslman / llmw 的业务层；
- 不把所有 Qt 文件都迁到新目录（只迁本次拆分的页面，避免一次性大爆炸）；
- 不引入新的 Qt 架构（继续使用现有 `QtMainWindow` / `AppPage` / `ItemRow`）。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | `apps/<app>/qt/` 允许第 3 层目录（仅 `apps`，其他层保持 ≤2）；`tests/test_layout.py` 按根目录配置深度 | 继续扁平 `_wslmanw_qt_xxx.py`（前缀无法表达页面归属、无法按目录找测试）；全仓库放宽（layout 指纹与发现机制不需要） | `tests/test_layout.py`、[0003](0003-package-layout.md) |
| D2 | wslman Qt 层拆为 `apps/wslman/qt/{window,state,distros,images,transfer,initenv,ssh,openers,dialogs}.py`；`qt/` 内不再建子包 | 按 `pages/` / `dialogs/` 再分层（超过 3 层，收益低） | `apps/wslman/qt/` |
| D3 | `WslmanQtWindow` 只保留外壳 + 页面组装；页面方法搬进 mixin/composition 类，共享状态收敛到 `WslmanViewState` / 现有 settings dataclass | 一个 3k 行类继续加方法（页面间共享状态不可见） | `qt/window.py`、`qt/state.py` |
| D4 | 对话框 / 行卡 / 详情视图按「无业务副作用」优先搬：先迁对话框与纯 UI 组件，再迁页面；每步保持可回滚 | 先拆最大页面（信号 / 状态耦合最多，评审与回滚成本高） | `qt/dialogs.py` 等 |
| D5 | llmw 按页面迁移，但只迁本次触达的页面；保留 `apps/llm/_llmw_qt_*.py` 兼容 shim，避免一次性重命名 60+ 模块 | 一次性全部搬入 `apps/llm/qt/`（测试与导入面过大） | `apps/llm/qt/` + shim |
| D6 | 行为冻结：不改公开命令、设置文件名、窗口标题、默认尺寸；拆分前后跑同一批 smoke + 设置 roundtrip | 借机顺手改交互（无法区分回归来源） | 测试基线 |
| D7 | 每个 PR 只迁 1 个页面/对话框，合并前 `make flow` 全绿；行数守卫白名单随拆分删除对应项 | 一个大 PR 全量迁移（review 不可行） | 提交纪律 |

### D1 布局规则

```text
允许：fcmd/apps/wslman/qt/files.py        # apps/<app>/qt/<module>.py
允许：fcmd/gui/style/qss.py              # gui/<子包>/<module>.py
拒绝：fcmd/apps/wslman/qt/pages/files.py # 超过 3 层
```

`tests/test_layout.py` 改为按根目录取深度上限：

```python
MAX_DIR_LEVELS = {"apps": 3, "default": 2}
```

`tests/test_architecture.py` 的跨层 import 规则不变；`apps/<app>/qt/` 内模块
只能 import 同 app 的 `qt/` / 业务模块 / `gui` / `infras`。

### D2 / D3 wslman 拆分边界

| 目标模块 | 内容 | 依赖 |
|---|---|---|
| `qt/window.py` | `WslmanQtWindow` 外壳、页面注册、菜单、状态栏绑定 | `QtMainWindow`、各页面 mixin |
| `qt/state.py` | `WslmanViewState`（当前 distro / 镜像列表 / 下载队列 / 运行状态缓存） | 纯 dataclass，不 import Qt |
| `qt/distros.py` | 发行版列表 / 详情 / 操作（启动、停止、注销、导出） | `state`、`_wslmanw_qt` 现有业务函数 |
| `qt/images.py` | 镜像目录 / 下载 / 导入 | `state`、`infras.download`、`mcp` 无关 |
| `qt/transfer.py` | 文件传输 / 资源管理器 / 打开方式 | `state`、`infras.fs` |
| `qt/initenv.py` | 初始化环境 section | `state`、`wslman` 业务 |
| `qt/ssh.py` | SSH 迁移 section | `state` |
| `qt/openers.py` / `qt/dialogs.py` | 打开方式、注销、关机、镜像管理、安装本地包等对话框 | 仅 Qt + 传入参数 |

约束：

- `state.py` 不 import Qt（便于单测）；页面只读写 `WslmanViewState`，不直接跨页面调方法；
- 页面之间的跳转通过窗口外壳的公开方法（如 `show_distro(name)`）或信号，不互相 import；
- 现有 `WslmanQtWindow` 的方法按「唯一状态归属」搬迁：同一状态只在一个页面里修改，
  其他页面只读；无法立即拆出的方法先留在 `window.py` 并打 `# TODO(0046)`。

### D4 / D5 llmw 拆分边界

- 先迁 `ModelCenterPage`（`_llmw_qt_center.py`）：列表 / 详情 / 下载 / 转换 / 量化
  四个子视图拆到 `apps/llm/qt/models_{list,detail,jobs}.py`；
- `RunPage`（`_llmw_qt_run.py`）：服务看护、参数表单、日志/看护拆开；
- `ChatPage` / `PaintPage` 在触达时再迁，未触达模块保持原路径；
- `apps/llm/_llmw_qt_*.py` 保留一行兼容 shim（`sys.modules` 或 re-export），
  避免第三方 / 测试 import 断链。

## 分期

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 布局规则支持 `apps/**` 3 层；新增 `apps/<app>/qt/` 空骨架 + 测试深度守卫；行数白名单条目保留但标注 0046 | `test_layout` / `test_architecture` 全绿，无行为变化 |
| P1 | wslman：对话框 / 行卡 / 详情视图先迁 `qt/dialogs.py`、`qt/state.py`；`window.py` 保留原类 | 对话框独立测试，整窗 smoke 全绿 |
| P2 | wslman：`distros` / `images` / `transfer` 页面迁移；`WslmanQtWindow` 方法数下降 50% | 页面级测试 + wslmanw smoke；单文件 ≤1,500 行 |
| P3 | wslman：`initenv` / `ssh` / `openers` 收尾；`window.py` 只留外壳 | `window.py` ≤1,000 行，行数白名单删除 `_wslmanw_qt.py` |
| P4 | llmw：`ModelCenterPage` 先拆；随后 `RunPage`（按触达顺序） | 单文件 ≤1,200 行；llmw smoke 全绿 |
| P5 | 清理 `apps/llm/_llmw_qt_*.py` 兼容 shim、更新 ADR 状态与交接 | `make flow` + 启动基准 + 文档同步 |

## 风险与对策

| 风险 | 对策 |
|---|---|
| 页面间共享 `self.xxx` 状态搬迁后不一致 | 先建 `WslmanViewState` 并逐字段登记 owner；迁移时只允许 owner 写 |
| 信号/槽在拆分后连接两次或丢失 | 每个页面提供 `bind(window)` / `connect_signals()`，窗口统一调用一次；页面测试断言连接数 |
| 循环 import（页面 ↔ 窗口） | 页面只依赖 `state` / 共享控件；窗口向页面注入回调，不反向 import |
| Qt 测试需要真实事件循环，拆分后 smoke 时间上升 | 页面级测试用 `QApplication` fixture；整窗 smoke 只保留关键路径，避免逐控件点击 |
| 布局规则放宽导致目录失控 | 只对 `apps` 放宽到 3 层；`test_layout` 同时断言 `qt/` 内无子目录 |
| 一次性重命名破坏外部 import | `apps/llm/_llmw_qt_*.py` / `apps/wslman/_wslmanw_qt.py` 保留 shim 一版 |

## 验收口径（整体）

1. `tests/test_layout.py` 按根目录深度守卫通过；`apps/*/qt/` 无第 4 层；
2. `_wslmanw_qt.py` 与 `_llmw_qt_center.py` 从行数白名单移除；
3. wslmanw / llmw 的命令、设置文件、窗口标题与默认尺寸不变；
4. 页面级测试覆盖至少：列表刷新、详情切换、对话框确认/取消、设置 roundtrip；
5. `make flow`、启动基准、`_manifest_data` 一致性全绿。

## 交接

- 本 ADR 只立项，不改运行代码；P0 开始前先评审 D1 的布局例外。
- 每个 PR 在描述里写「本 PR 迁移的页面 / 状态 owner / 删除的白名单条目」；
- 页面拆分后更新 `docs/adr/0046` 的落地状态与 `tests/test_layout.py` 的深度配置；
- 不再新增 `_<app>_qt_xxx.py` 扁平前缀模块，新页面直接放 `apps/<app>/qt/`。
