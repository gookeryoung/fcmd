# 0033 locllmw 迁移到 Qt（PySide6）：GUI 基座与分页重构

- 状态：生效（P0 / P1 已落地；P2 待迁移）
- 日期：2026-11-17
- 关联：0013（GUI 技术路线研究）、0025（侧边栏分页导航）、0027 / 0028 / 0029 / 0030 / 0031（locllmw 既有分页与配置）、0032（启动速度）

## 背景与需求口径

需求原句：**「增加 pyside2 依赖，locllmw 按照 pyside2 进行改造，补齐 gui 基础设计」**。

| 编号 | 约束 | 落点 |
|---|---|---|
| C1 | 引入 Qt 依赖，locllmw 用 Qt 重写 | `pyproject.toml` 基础依赖 `PySide6-Essentials`；`src/fcmd/gui/qt/**`；`src/fcmd/apps/locllm/_locllmw_qt_*.py` |
| C2 | 补齐 GUI 基础设计（窗口骨架 / 主题 / 导航 / 后台任务 / 常用控件） | `fcmd.gui.qt.window` / `theme` / `sidebar` / `workers` / `widgets` |
| C3 | locllmw 保持既有七页能力与配置兼容 | 页面模块；`_locllmw_winsettings` 与 `locllmw.json` 口径不变 |
| C4 | 门禁不退化：lint / typecheck / test / 覆盖率照旧 | `pyrefly.toml` / `.coveragerc` 的可选依赖排除；离屏冒烟测试 |
| C5 | 不因换工具包丢失尚未迁移的能力 | Tk 实现保留 `locllmw --ui tk`（迁移期回退，见 P2） |

## 决策（D 表）

| 编号 | 决策 | 被否决的方案（原因） | 代码落点 |
|---|---|---|---|
| D1 | 使用 **PySide6**，不使用 PySide2 | **PySide2 不可行**：最后版本 5.15.2.1 只提供 cp36–cp310 wheel，且无 `win_amd64` / `linux_x86_64` 现代平台轮子；本项目要求 Python ≥ 3.13（`uv pip install PySide2` 直接无解）。PyQt6 为 GPL/商业许可，与 MIT 分发不一致 | `pyproject.toml` → `qt = ["PySide6-Essentials>=6.9"]` |
| D2 | Qt 依赖进入**基础依赖**（`pip install fcmd` 即可用 locllmw；按维护者要求简化安装） | 作为可选 extra `fcmd[qt]`：默认入口会因缺依赖不可用，用户需额外记忆一个 extra；代价是安装体积增加约 200 MB，与 0013「纯 Python 依赖不变」冲突，接受该代价 | `pyproject.toml` → `dependencies` 含 `PySide6-Essentials>=6.9` |
| D3 | Qt 与 Tk **共用同一份主题注册表**（四套主题、别名、`ui/theme.json`） | 另建 Qt 专用主题：两套配色/字号必然漂移，用户切换前端后观感不一致 | `fcmd/gui/qt/theme.py` 只做 `Palette → QSS/QPalette` 翻译 |
| D4 | Qt 与 Tk **共用同一份窗口设置**（`ui/locllmw.json`、`WxH+X+Y` 几何、`LocllmSettings`） | 各写一份设置：模型目录 / 硬件快照 / 实测记录无法共享 | 抽出 `_locllmw_winsettings`；`fcmd.gui.qt.window` 复用 `fcmd.gui.config` / `window.resolve_geometry` |
| D5 | 后台任务用线程池 + 信号，不用 `QThread` 子类 | 每任务一个 `QThread`：线程生命周期与窗口销毁耦合，易悬空 | `fcmd/gui/qt/workers.py`（`submit` / `periodic`） |
| D6 | 页面**按需构建**（首次切页才实例化） | 建窗即构建七页：模型/设置页会立刻扫描目录与引擎，违背 0032 的冷启动目标 | `QtMainWindow.add_page(..., factory=)`；`LocllmWindow._factory` |
| D7 | 迁移期保留 Tk 入口 `--ui tk`，默认 Qt | 一次性删除 Tk：模型下载浏览器、转换 / 量化向导尚未迁移，会丢能力 | `locllmw.run_gui(ui=...)`；`_locllmw_qt_window.run_qt` |
| D8 | Qt 模块从 **pyrefly / coverage 排除**，纯逻辑单独测试 | 在 CI 安装 PySide6：lint/test 两个 job 各多下载约 120–200 MB，违背门禁速度优先 | `pyrefly.toml` `project-excludes`；`.coveragerc` `omit`；`tests/apps/locllm/test_locllmw_qt_logic.py` |
| D9 | 数值/文本口径一律复用 Tk 版模块级函数，不复制实现 | 在 Qt 里重写一套扫描/适配/推荐：两套口径漂移（如「硬件不足」判定） | 复用 `_locllmw_ui` / `_locllmw_monitor` / `_locllmw_bench` / `_locllmw_recommend` / `_locllmw_settings` |

## GUI 基础设计（C2）

`src/fcmd/gui/qt/` 五个模块，职责与 Tk 基座一一对应：

| 模块 | 提供 | 对应 Tk 基座 |
|---|---|---|
| `theme.py` | `Palette → QSS` / `QPalette`、字体挑选、主题持久化；纯函数可单测 | `fcmd.gui.theme` |
| `sidebar.py` | 左侧导航（`SidebarNav`）：页登记、`Alt+1..9`、`Alt+↑/↓` 循环 | `fcmd.gui.sidebar` |
| `window.py` | `QtMainWindow`：导航 + `QStackedWidget` + 状态栏 + 主题 / 关于菜单 + 几何记忆；几何解析为纯函数 | `fcmd.gui.window` |
| `widgets.py` | `Card` / `PageScroll` / `Collapsible` / `FormGrid` / `MetricGrid` / `KeyValueGrid` / `PathRow` / `LogView` / `make_table` | `create_log` / `collapsible` / `pair_row` |
| `workers.py` | `submit(...)`（线程池 + 信号回投）、`periodic(...)` | daemon 线程 + `after` 轮询 |

约定：Qt 只在**创建窗口的路径**上延迟导入；`theme.py` / `window.py` 的纯函数不导入 PySide6，CI（无 Qt）仍可单测。

## locllmw 分页与能力映射

| 页面 | Qt 实现 | 复用逻辑 |
|---|---|---|
| 概览 | `_locllmw_qt_overview`：硬件卡、显存/内存占用条、智能推荐、已加载服务 | `_locllmw_hardware` / `_locllmw_recommend` |
| 模型 | `_locllmw_qt_models`：分组模型树、精度备注、适配着色、选择/浏览/打开目录 | `_locllmw_ui`（扫描、分组、精度缓存） |
| 运行 | `_locllmw_qt_run`：参数表单、调优联动、计划预览、启停、浏览器/日志 | `llmserve` / `llmmodel` / `_llmtune` / `_locllmw_monitor` |
| 对话 | `_locllmw_qt_chat`：服务回填、流式回复、思考过程、新对话、导出 Markdown | `llmchat.ChatSession` / `_locllmw_chat.format_session_markdown` |
| 实验室 | `_locllmw_qt_lab`：四张规划卡片 | — |
| 监测 | `_locllmw_qt_monitor`：`/metrics` + 系统负载 + GPU + 性能测试 + 日志 | `_locllmw_monitor` / `_locllmw_bench` |
| 设置 | `_locllmw_qt_settings`：模型目录、引擎根目录与已安装引擎、关于与诊断 | `_locllmw_settings` 纯逻辑 / `_llmconfig` |

## 分期与落地状态

- **P0（已落地）**：`PySide6-Essentials` 基础依赖、GUI 基座五模块、离屏冒烟测试与纯函数测试。
- **P1（已落地）**：locllmw 七页 Qt 化，默认入口切到 Qt；`--ui tk` 保留；设置与主题共用；
  监测页承担性能测试与日志；运行页承担服务启停与健康横幅。
- **P2（未落地）**：模型页的 ModelScope 搜索 / 下载管理、转换（HF safetensors → GGUF）与
  量化向导迁到 Qt。迁移完成并在 README 标注后，删除 Tk 实现（`locllmw.py` 中的
  `LocllmWindow` 与 `_locllmw_ui` / `_locllmw_bench` / `_locllmw_chat` / `_locllmw_monitor` /
  `_locllmw_overview` / `_locllmw_settings` / `_locllmw_models` / `_locllmw_convert` 的 Tk 类），
  同时移除 `--ui tk` 与 `locllmw.py` 的 Tk 导入。

## 已知取舍

- 发布物 / 安装体积：`PySide6-Essentials` 进入基础依赖，wheel 安装与便携 zip 均增加约 150–200 MB；
  这是「默认入口开箱可用」换来的代价（D2）。
- 覆盖率：Qt 控件胶水不追求覆盖率（与 Tk 窗口同口径），纯逻辑由
  `tests/apps/locllm/test_locllmw_qt_logic.py` 覆盖；类型检查对 Qt 绑定模块关闭
  （CI 不装 PySide6，与 `mcp_adapter` 同处理）。
- 对话页暂不支持图片附件（Tk 版支持）；需要时用 `--ui tk` 或 `fcmd llmchat --image`。
- `PySide6-Essentials` 只含 QtCore/QtGui/QtWidgets 等基础模块，够 locllmw 使用；引入 WebEngine/Charts
  等 Addons 时才需要额外依赖。
- 模型页在 Qt 下只做扫描 / 选择 / 打开目录，下载与转换指向 `fcmd llmdown` / `fcmd llmconvert`。

## 交接

- 新增页面：继承 `QWidget`，只接收 `LocllmState`，通过其信号联动；在
  `LocllmWindow._factory` 登记，重页面用工厂延迟构建。
- 页面布局：内容放进 `PageScroll`（窗口变矮时滚动、变高时拉伸），需要常驻的操作
  放固定的 `QFrame#ActionBar`（如运行页的启停按钮），不要靠压缩列表高度换空间；
  列表控件给最小高度（模型树 240px）+ 展开 / 收起全部入口，避免「显示不全」。
- 新增后台动作：一律 `fcmd.gui.qt.workers.submit(...)`，禁止在 GUI 线程做阻塞 IO；
  长任务的日志用 `state.log`（信号自动排队回主线程）。
- 主题：新增控件样式改 `fcmd/gui/qt/theme.py` 的 QSS 模板，色值只从 `Palette` 取。
- 关闭：`QtMainWindow.closeEvent` → `on_close()`（确认进行中任务）→ `persist(geometry)`；
  采样 / 生成线程必须在这里停掉。
- 复测：`QT_QPA_PLATFORM=offscreen uv run pytest tests/apps/locllm/test_locllmw_qt_smoke.py`；
  真实窗口用 `uv run fcmd locllmw`（`PySide6-Essentials` 随 fcmd 安装）。
