# 0033 locllmw 迁移到 Qt（PySide6）：GUI 基座与分页重构

- 状态：已落地（GUI 基座、七页分页、模型行内下载 / 转换 / 量化全部为 Qt；Tk 实现已删除）
- 日期：2026-11-17
- 关联：0013（GUI 技术路线研究）、0025（侧边栏分页导航）、0027 / 0028 / 0029 / 0030 / 0031（locllmw 既有分页与配置）、0032（启动速度）、0034（全量 GUI 迁移）

## 背景与需求口径

需求原句：**「增加 pyside2 依赖，locllmw 按照 pyside2 进行改造，补齐 gui 基础设计」**。

| 编号 | 约束 | 落点 |
|---|---|---|
| C1 | 引入 Qt 依赖，locllmw 用 Qt 重写 | `pyproject.toml` 基础依赖 `PySide6-Essentials`；`src/fcmd/gui/qt/**`；`src/fcmd/apps/locllm/_locllmw_qt_*.py` |
| C2 | 补齐 GUI 基础设计（窗口骨架 / 主题 / 导航 / 后台任务 / 常用控件） | `fcmd.gui.qt.window` / `theme` / `sidebar` / `workers` / `widgets` |
| C3 | locllmw 保持既有七页能力与配置兼容 | 页面模块；`_locllmw_winsettings` 与 `locllmw.json` 口径不变 |
| C4 | 门禁不退化：lint / typecheck / test / 覆盖率照旧 | `pyrefly.toml` / `.coveragerc` 的可选依赖排除；离屏冒烟测试 |
| C5 | 不因换工具包丢失能力 | 模型页在同一 Qt 页内补齐 ModelScope 浏览 / 下载、行内转换与量化；Tk 窗口已删除 |

## 决策（D 表）

| 编号 | 决策 | 被否决的方案（原因） | 代码落点 |
|---|---|---|---|
| D1 | Python ≥3.11 使用 **PySide6**；Python 3.10 使用 **PySide2**（见 [0036](0036-python310-pyside2.md)） | 全部 Python 版本只装 PySide6：3.10 上也能跑，但无法满足「3.10 明确走 PySide2」的兼容要求 | `pyproject.toml` 按 `python_version` 标记依赖；`fcmd.gui.qt._pyside_compat` 做运行时映射 |
| D2 | Qt 依赖进入**基础依赖**（`pip install fcmd` 即可用 locllmw） | 作为可选 extra：默认入口会因缺依赖不可用，用户需额外记忆一个 extra；代价是安装体积增加约 200 MB，接受该代价 | `pyproject.toml` → `dependencies`（PySide6 / PySide2 择一） |
| D3 | 主题注册表与调色板只在 `fcmd.gui.theme` 维护一份（四套主题、别名、`ui/theme.json`） | 另建 Qt 专用主题：两套配色/字号必然漂移 | `fcmd/gui/qt/theme.py` 只做 `Palette → QSS/QPalette` 翻译 |
| D4 | 窗口设置统一为 `ui/locllmw.json` + `WxH+X+Y` 几何 + `LocllmSettings` | 各写一份设置：模型目录 / 硬件快照 / 实测记录无法共享 | `_locllmw_winsettings`；`fcmd.gui.qt.window` 复用 `fcmd.gui.config` / `fcmd.gui.geometry` |
| D5 | 后台任务用线程池 + 信号，不用 `QThread` 子类 | 每任务一个 `QThread`：线程生命周期与窗口销毁耦合，易悬空 | `fcmd/gui/qt/workers.py`（`submit` / `periodic`） |
| D6 | 页面**按需构建**（首次切页才实例化） | 建窗即构建七页：模型/设置页会立刻扫描目录与引擎，违背 0032 的冷启动目标 | `QtMainWindow.add_page(..., factory=)`；`LocllmWindow._factory` |
| D7 | 纯逻辑继续放在 `_locllmw_*` 模块，Qt 页面只做编排 | 在 Qt 里重写一套扫描/适配/推荐：两套口径漂移（如「硬件不足」判定） | `_locllmw_ui` / `_locllmw_monitor` / `_locllmw_bench` / `_locllmw_recommend` / `_locllmw_settings` / `_locllmw_models` / `_locllmw_convert` |
| D8 | Qt 模块从 **pyrefly / coverage 排除**，纯逻辑单独测试 | 在 CI 安装 PySide6：lint/test 两个 job 各多下载约 120–200 MB，违背门禁速度优先 | `pyrefly.toml` `project-excludes`；`.coveragerc` `omit`；`tests/apps/locllm/test_locllmw_qt_logic.py` |

## GUI 基础设计（C2）

`src/fcmd/gui/qt/` 模块职责：

| 模块 | 提供 |
|---|---|
| `app.py` | `QApplication` 复用、高 DPI 舍入（PassThrough）、主题应用、`run_window(factory)` |
| `theme.py` | `Palette → QSS` / `QPalette`、字体挑选、主题持久化；纯函数可单测 |
| `sidebar.py` | 左侧导航（`SidebarNav`）：页登记、`Alt+1..9`、`Alt+↑/↓` 循环 |
| `window.py` | `QtMainWindow`：导航 + `QStackedWidget` + 状态栏 + 主题 / 关于菜单 + 几何记忆 |
| `toolwindow.py` | `QtToolWindow`：单页工具窗口骨架（文件 / 视图 / 帮助菜单、状态栏、几何记忆、关闭落盘） |
| `widgets.py` | `Card` / `PageScroll` / `Collapsible` / `FormGrid` / `MetricGrid` / `KeyValueGrid` / `PathRow` / `LogView` / `LoadingBar` / `ElidedLabel` / `make_table` / `QSplitter` 辅助 |
| `layout.py` | 纯函数：中间省略、按比例分配列宽、分栏尺寸读写（不导入 Qt） |
| `workers.py` | `submit(...)`（线程池 + 信号回投）、`periodic(...)` |

约定：Qt 只在**创建窗口的路径**上延迟导入；`theme.py` / `layout.py` / `geometry.py` 的纯函数不导入 PySide6，CI（无 Qt）仍可单测。

## locllmw 分页与能力映射

| 页面 | Qt 实现 | 复用逻辑 |
|---|---|---|
| 概览 | `_locllmw_qt_overview`：硬件卡（环形显存 / 内存占用表）、智能推荐 | `_locllmw_hardware` / `_locllmw_recommend` |
| 模型 | `_locllmw_qt_center`（浏览页，行内下载 / 转换 / 量化） | `_locllmw_models` / `_locllmw_convert` / `llmdown` / `llmconvert` / `llmqnt` |
| 运行 | `_locllmw_qt_run`：参数表单、调优联动、计划预览、启停、浏览器/日志 | `llmserve` / `llmmodel` / `_llmtune` / `_locllmw_monitor` |
| 对话 | `_locllmw_qt_chat`：服务回填（含进程退出提示）、流式回复、思考过程、新对话、导出 Markdown | `llmchat.ChatSession` / `_locllmw_chat.format_session_markdown` |
| 实验室 | `_locllmw_qt_lab`：四张规划卡片 | — |
| 监测 | `_locllmw_qt_monitor`：`/metrics` + 系统负载 + GPU + 性能测试 + 日志 | `_locllmw_monitor` / `_locllmw_bench` |
| 设置 | `_locllmw_qt_settings`：模型目录、下载参数、转换参数、引擎根目录与已安装引擎 | `_locllmw_settings` 纯逻辑 / `_llmconfig` |

### 模型页浏览与行内操作（C5）

| 页面 | 能力 | 复用逻辑 |
|---|---|---|
| 浏览 | ModelScope 筛选 / 排序 / 翻页、行内下载 / 转换 / 量化、行内详情元数据、底部任务进度 | `llmdown.search_models` / `fetch_model_info` / `select_source` / `filter_files` / `download_files` |

### 加载过程提示（模型页 / 概览页）

| 场景 | 行为 |
|---|---|
| 切到模型页 | 本地目录扫描与在线列表搜索都放线程池；`QtMainWindow` 状态栏右侧的 `LoadingBar` 显示当前任务（正在扫描本地模型 / 正在加载第 N 页）与已用时（每 100ms 刷新），任务全部结束才收起；左侧状态文字不被覆盖 |
| 概览页硬件检测 | 与模型页共用状态栏右侧加载条：`LocllmState.begin_loading("hardware", …)` / `end_loading("hardware")`，完成或失败后收起，不再占用概览页内容区 |
| 其它后台加载 | 统一用 `LocllmState.begin_loading(key, text)` / `end_loading(key)` → `QtMainWindow.loading_bar`；状态层按 key 聚合，多任务并存时显示最近开始的说明，任务结束回退到仍进行中的任务，避免互相覆盖 |

## 测试与门禁

- 纯逻辑（设置、硬件快照、监控采样、性能场景、分组、下载参数、转换路径）在
  `tests/apps/locllm/` 单测；Qt 冒烟用 `QT_QPA_PLATFORM=offscreen`
  （`test_locllmw_qt_smoke.py`），断言七页可构建、模型树 / 分页内容与几何落盘。
- 断言 Tk 内部实现的用例已改写为 Qt 等价断言：对话页服务选择改为 `ChatPage`，
  概览页推荐动作改为 `OverviewPage`，量化勾选改为 `_QuantPanel`，
  `state_tag` 改为 `_locllmw_ui.state_tag` 纯函数。
- `pyrefly.toml` / `.coveragerc` 继续排除 `src/fcmd/gui/qt/**` 与
  `src/fcmd/apps/locllm/_locllmw_qt_*.py`（CI 不装 PySide6，与 `mcp_adapter` 同处理）。

## 已知取舍

- 发布物 / 安装体积：PySide6 / PySide2 进入基础依赖，wheel 安装与便携 zip 均增加约 150–200 MB；
  这是「默认入口开箱可用」换来的代价（D2）。
- 覆盖率：Qt 控件胶水不追求覆盖率，纯逻辑由普通单测覆盖。
- 对话页暂不支持图片附件；需要时用 `fcmd llmchat --image`。
- PySide6-Essentials（≥3.11）/ PySide2（3.10）只含 QtCore/QtGui/QtWidgets 等基础模块，够 locllmw 使用；
  引入 WebEngine/Charts 等 Addons 时才需要额外依赖。

## 交接

- 新增页面：继承 `QWidget`，只接收 `LocllmState`，通过其信号联动；在
  `LocllmWindow._factory` 登记，重页面用工厂延迟构建。
- 运行页的模型选择：顶部下拉框显式选择优先，长文件名在项内截断（tooltip 保留完整路径）；未选择时 `pick_model` 先找文件名含档位
  量化的最大文件，再退回最大 GGUF（`scan` 完成后也会自动带入一个），避免「有可用
  模型但按默认量化找不到」导致启动报错。
- 页面布局：内容放进 `PageScroll`（窗口变矮时滚动、变高时拉伸），需要常驻的操作
  放固定的 `QFrame#ActionBar`（如运行页的启停按钮），不要靠压缩列表高度换空间。
- 新增后台动作：一律 `fcmd.gui.qt.workers.submit(...)`，禁止在 GUI 线程做阻塞 IO；
  长任务的日志用 `state.log`（信号自动排队回主线程）。
- 主题：新增控件样式改 `fcmd/gui/qt/theme.py` 的 QSS 模板，色值只从 `Palette` 取。
  选中态不能只加粗边框：卡片 / 选项 / 筛选块的内部底色改用同色系
  `*_soft_strong`（32%），未选中仍用 `*_soft`（16%），保证深浅差异可见；
  模型列表选中行边框使用 `select_bg`（深蓝）而非普通 `border`。
- 关闭：`QtMainWindow.closeEvent` → `on_close()`（确认进行中任务）→ `persist(geometry)`；
  采样 / 生成 / 下载线程必须在这里停掉。
- 窗口位置与大小：`geometry` 存客户区（`QWidget.geometry()`，不含标题栏与边框），
  恢复走 `restore_geometry` 的 `setGeometry`，两侧同一口径，避免每次启动叠加窗口装饰导致窗口漂移。
  locllmw 主窗口高度下限放宽到 600px（`MIN_SIZE` / `MIN_HEIGHT` 同步），页面继续靠
  `PageScroll` 吸收压缩，不靠抬高窗口下限换取布局空间。
- 后台加载：需要等待的场景统一挂到状态栏右侧（`LocllmState.begin_loading` /
  `end_loading`，窗口用 `LoadingBar` 渲染说明 + 忙碌动画 + 已用时）；不要把阻塞
  扫描留在页面构造函数里，也不要在内容区顶部放大段进度条盖住标题或分页控件。
- 复测：`QT_QPA_PLATFORM=offscreen uv run pytest tests/apps/locllm/test_locllmw_qt_smoke.py`；
  真实窗口用 `uv run fcmd locllmw`（`PySide6-Essentials` 随 fcmd 安装）。
