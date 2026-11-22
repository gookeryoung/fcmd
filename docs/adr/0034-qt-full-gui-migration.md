# 0034 全量 GUI 迁移到 Qt（PySide6）：共享层先行与分批迁移

- 状态：已全部落地（B0–B7：共享层、20 个工具窗口、locllmw 与 cmderw 全部 Qt 化；Tk 窗口代码与共享层已删除；B6 体积与运行期开销已填报）
- 日期：2026-11-17
- 关联：0013（GUI 技术路线）、0033（locllmw 迁移与 Qt 基座）、0025（侧边栏导航）

## 目标与口径

把 locllmw 之外的 Tk 工具窗口迁到 PySide6，并在迁移同时完成布局优化与高 DPI 适配。
cmderw（双面板旗舰）不纳入本轮逐应用迁移，见 B7。约束：

| 编号 | 约束 |
|---|---|
| C1 | CLI 行为、输出契约、打包入口（`pyproject.toml [project.scripts]`）不变 |
| C2 | 每个批次结束时应用可实际启动；不合并「半迁移不可用」的中间态 |
| C3 | 纯逻辑、设置与 geometry 测试保持原口径；窗口胶水不追求覆盖率 |
| C4 | 主题、窗口设置与 geometry 文件继续使用 `${FCMD_HOME:-~/.fcmd}/ui/*.json` |
| C5 | Qt 只在创建窗口的路径上导入，CLI `--help` / 启动快路径不加载 PySide6 |

## 现状盘点（迁移起点：Tk 20 个工具窗口 + 1 个旗舰；下表为迁移前口径）

| 模块 | 行数 | 形态 | 批次 |
|---|---:|---|---|
| `tools/pdf/pdfcryptw.py` | 430 | 单页表单 + 进度 + 日志 | B1 |
| `tools/media/img2icow.py` | 483 | 文件 + 选项 + 预览 | B1 |
| `tools/office/docdiffw.py` | 495 | 双文件 + 差异表 | B1 |
| `apps/paramesh/parameshw.py` | 516 | 参数表 + 画布预览 | B1 |
| `apps/paramcad/paramcadw.py` | 529 | 参数表 + 画布预览 | B1 |
| `tools/pdf/img2pdfw.py` | 534 | 文件列表 + 选项 | B1 |
| `apps/peview/pevieww.py` | 547 | 树 + 详情表 + 编辑对话框 | B2 |
| `tools/pdf/pdfsplitw.py` | 572 | 文件 + 页范围 + 结果表 | B2 |
| `apps/dynacalc/dynacalcw.py` | 584 | 表单 + 矩阵表 + 汇总 | B2 |
| `tools/text/regexvalidatew.py` | 676 | 输入 + 匹配表 + 历史 | B2 |
| `apps/filesync/filesyncw.py` | 706 | 双面板比较 + 同步队列 | B2 |
| `tools/media/ncmconvertw.py` | 789 | 批量列表 + 转换进度 | B3 |
| `tools/dev/mirrortoolw.py` | 809 | 搜索 + 结果表 + 配置 | B3 |
| `tools/media/videoconvw.py` | 827 | 批量列表 + 选项 + 进度 | B3 |
| `apps/reshack/reshackw.py` | 926 | 资源树 + 编辑表 | B3 |
| `apps/renamer/renamerw.py` | 1021 | 规则列表 + 预览表 | B3 |
| `apps/pptmake/pptmakew.py` | 1261 | 多分页设计器 + 预览 | B4 |
| `tools/system/envw.py` | 1331 | 变量树 + 编辑对话框 | B4 |
| `apps/filescan/filescanw.py` | 1507 | 目录树 + 结果表 + 报告 | B4 |
| `apps/wslman/wslmanw.py` | 1753 | 多分页 + 下载 + 终端 | B5 |
| `apps/cmder/app.py` | 8735 | 双面板文件管理器 + 缩略图 + 队列 | B7（独立阶段，见 D1） |

Tk 共享层（`fcmd/gui/window.py` / `sidebar.py` 与 `theme.py` 的 ttk 渲染部分）已删除；
Qt 侧复用 `fcmd/gui/qt/{theme,sidebar,window,widgets,workers}.py`（0033 已落地），
几何纯函数统一在 `fcmd/gui/geometry.py`，分页纯函数在 `fcmd/gui/pages.py`，
主题注册表（调色板 / 持久化）保留在 `fcmd/gui/theme.py`。

## 目标结构（B0 共享层）

| 模块 | 职责 |
|---|---|
| `gui/qt/app.py` | `QApplication` 复用、高 DPI 舍入策略（PassThrough）、主题应用、`run_window(factory)` 统一入口 |
| `gui/qt/toolwindow.py` | `QtToolWindow`：单页工具窗口骨架（文件 / 视图 / 帮助菜单、状态栏、几何记忆、关闭落盘、可选滚动容器） |
| `gui/qt/theme_menu.py` | 主题子菜单 + 即时切换 + 持久化，供 `QtMainWindow` 与 `QtToolWindow` 共用 |
| `gui/qt/layout.py` | 纯函数：中间省略、按比例分配列宽、分栏尺寸读写（可单测，不导入 Qt） |
| `gui/qt/widgets.py` | 增补 `ElidedLabel`（长路径中间省略 + tooltip）、`make_splitter` / `splitter_sizes` / `apply_splitter_sizes`；`make_table` 支持 `stretch_last` |
| `gui/qt/workers.py`、`theme.py` | 沿用 0033：线程池回投、`Palette → QSS` |

## 布局与适配规范（随每个应用迁移落实）

| 编号 | 规范 |
|---|---|
| L1 | 根内容可缩放：纵向用 `PageScroll`，可拖动分栏用 `QSplitter`，禁止用固定高度硬塞 |
| L2 | 表格 / 树：主文本列 `Stretch`，数值列 `Interactive` + 初始宽度；沿用 `make_table` 的只读、整行选择、斑马纹 |
| L3 | 长文本与长路径：`ElidedLabel`（中间省略 + 完整 tooltip）；路径输入用 `PathRow`，只读路径可选中复制 |
| L4 | 高 DPI：Qt6 自动缩放 + `PassThrough` 舍入；间距与图标用逻辑像素，禁止按 96 DPI 写死像素 |
| L5 | 主题：色值只从 `Palette` 取；深浅主题切换即时生效，不重开窗口；不硬编码颜色 |
| L6 | 分栏与列宽记忆：`QSplitter` 尺寸、表头宽度写入各自 `ui/<name>.json`，越界回退默认 |
| L7 | 键盘：`Ctrl+Q` 退出、主题菜单与「关于」位置一致；分页窗口沿用 `Alt+1..9` |
| L8 | 控件高度：按钮、单行输入、下拉、数值框统一由 `gui/qt/theme.py` 的字体行高基准推导（`QFontMetrics` 逻辑像素 + `CONTROL_HEIGHT_EXTRA`），窗口内不单独写死高度 |
| L9 | 字体：GUI 英文（拉丁 / 数字 / 符号）一律 `Cascadia Code`，中文按 `UI_FONT_CANDIDATES` 逐字回退；`apply_theme` 用 `QFont.setFamilies` 组字体链，等宽控件（日志 / 代码 / 十六进制）同样优先 `Cascadia Code` |

## 分批计划与验收

| 批次 | 内容 | 验收 |
|---|---|---|
| B0 | 共享层五模块 + 单测 / 离屏冒烟 | 纯函数单测通过；离屏构建 `QtToolWindow` 并断言菜单 / 状态栏 / 几何回写；`make flow` |
| B1 | 6 个单页小工具 | 每个应用可启动；长路径省略、窗口缩放不裁控件；纯逻辑测试保留 |
| B2 | 5 个表单 / 画布类 | 画布改用 `QPainter`，缩放与 DPI 下不模糊；比较 / 匹配表列宽自适应 |
| B3 | 5 个中型表格 / 树 | 批量列表滚动流畅；操作按钮固定底栏，不随列表压缩 |
| B4 | 4 个大型表格 / 树 | 5000+ 行列表刷新不阻塞 GUI 线程；报告 / 导出路径可用 |
| B5 | `wslmanw`（多分页 + 下载 + 终端） | 分页完整迁移；终端与下载不阻塞 GUI |
| B6 | 收尾：locllmw 回退删除、alarmclock / checksum Qt 化 | README / CHANGELOG、体积与运行期开销实测 |
| B7 | `cmderw` 独立阶段（已完成） | 外壳 + 双面板、缩略图后台解码、队列与全部对话框迁到 Qt；Tk 实现删除 |

每批独立提交、可独立回退；批次内每迁完一个应用即可单独启动验收，不攒到最后。
B7 与 B1–B6 平行排期，迁移期间任何一批的提交都不应把 cmderw 改坏。

进度：B0–B7 已全部完成。

- B1 六个应用（pdfcryptw / img2icow / docdiffw / parameshw / paramcadw / img2pdfw）、
  B2 五个应用（pevieww / pdfsplitw / dynacalcw / regexvalidatew / filesyncw）、
  B3 五个应用（ncmconvertw / mirrortoolw / videoconvw / reshackw / renamerw）、
  B4 四个应用（pptmakew / envw / filescanw / wslmanw）各自单独提交，旧实现已删除，
  GUI 入口统一委托 ``_<tool>w_qt.py``；
- locllmw 的 P2 已落地：模型页补齐 ModelScope 浏览 / 下载、行内转换与量化，
  `--ui tk` 与全部 Tk 模块（`locllmw.LocllmWindow`、`_llmw_ui` / `_llmw_bench` /
  `_llmw_chat` / `_llmw_monitor` / `_llmw_overview` / `_llmw_settings` /
  `_llmw_models` / `_llmw_convert` 的 Tk 类）已删除（见 0033）；
- B6 收尾还包含 `alarmclock` / `checksum` 两个工具窗口的 Qt 化；
- B7 已完成：`cmderw` 拆为 `_cmder_qt.py`（窗口 / 菜单 / 队列 / 命令行）、
  `_cmder_qt_panels.py`（双面板 / 缩略图 / 快速查看）与 `_cmder_qt_dialogs.py`
  （多重命名 / 搜索 / 比较 / 同步 / 用户命令 / 颜色规则等 17 个对话框），
  `app.py` 只保留纯逻辑（设置、扫描、排序、历史、传输计划、归档成员、预览与格式化）；
- 断言 Tk 内部实现的入口测试已改写为 Qt 等价断言（对话页服务选择、概览页已加载服务、
  量化勾选、闹钟时钟与提醒、校验和比较等），纯逻辑与设置测试保持原样；
- 字体口径（L9）：`gui/theme.py` 新增 `LATIN_FONT_CANDIDATES`（`Cascadia Code` →
  `Cascadia Mono`）与 `ui_font_families()`，`apply_theme` 把「拉丁字体 + CJK 字体」链写进
  `QFont.setFamilies`（Qt 5 退化为单族），英文 / 数字落在 `Cascadia Code`、中文逐字回退；
  `mono_font()` 同样优先该族，系统未安装时回退原 CJK / 系统等宽字体，行为不变。
- 共享层同时落地统一控件高度基准（L8）：按钮 / 单行输入 / 下拉 / 数值框按字体行高
  推导逻辑像素，高 DPI 下随缩放同比换算；
- 单页工具窗口的内容统一放进纵向滚动页面，窗口缩矮到内容最小高度以下时改为整页滚动，
  不再把表格 / 工具栏压出卡片边界（B4 期间的回溯修复）。

## 每应用交付时的布局适配点

每迁完一个应用，随该应用的提交一并报出以下四类适配点，不攒到批次末尾：

| 类别 | 检查项 |
|---|---|
| 长路径 / 长文本 | 路径与长标签用 `ElidedLabel`（中间省略 + 完整 tooltip）或 `PathRow`；只读文本可选中复制 |
| 列宽 / 分栏 | 主文本列 `Stretch`、数值列 `Interactive` + 初始宽度；分栏用 `QSplitter` 且尺寸可记忆 |
| 高 DPI | 间距 / 图标用逻辑像素；离屏与 150% 缩放下不裁切、不模糊 |
| 深浅主题 | 无硬编码色值；切换后无需重开窗口，日志 / 表格 / 状态栏同色系 |

## 测试与门禁

- 纯逻辑（省略算法、列宽分配、分栏尺寸解析、设置迁移）进 `tests/gui/` 普通单测。
- Qt 窗口冒烟用 `QT_QPA_PLATFORM=offscreen`，断言页面可构建、关键控件存在、几何可回写；
  Qt 控件胶水与其它 GUI 窗口同口径，不加覆盖率断言。
- `pyrefly.toml` 对 Qt 绑定模块的排除沿用 0033 的做法（Qt 胶水不做类型检查）；
  新增模块按 `src/fcmd/tools/*/*_qt.py` / `src/fcmd/apps/*/*_qt*.py` 归入排除。
- 每批至少 `make flow`；收尾跑 `make check`（含覆盖率与 linux 类型检查）。

## 体积与运行期开销（B6 填报）

测量环境：Windows 11 x64、Python 3.14.6、PySide6 6.12.0、NVMe，2026-11-18。
Tk 基线取自迁移前提交 `5dbcef4`（用 `git worktree` 检出，同一解释器、同一份临时设置目录）；
Qt 使用原生 `windows` 平台插件。每个窗口独立进程，冷启动口径为「解释器启动 → 窗口显示 →
首轮事件处理完成」（Tk 为 `root.update()`），5 次取中位数；RSS 为窗口显示后的进程常驻内存，
5 次取中位数。

| 指标 | 实测 |
|---|---:|
| `site-packages` 总量（开发 venv） | 366.9 MB |
| 其中 PySide6 + shiboken6 | 211.6 MB（208.6 + 3.0） |
| 其中 fcmd 自身（`.py`） | 4.4 MB |
| 便携包 zip（Windows，slim） | 98.0 MB |
| 便携包解包后 | 237.9 MB（site-packages 186.5 MB / runtime 20.3 MB / 其余为各命令 exe） |
| 便携包内 PySide6 + shiboken6 | 53.0 MB（50.4 + 2.6，fspack 已裁掉未用插件） |

冷启动（中位数，ms）：

| 窗口 | Tk | Qt | 差值 |
|---|---:|---:|---:|
| 空窗口（QWidget / Tk root） | 104.3 | 256.9 | +152.6 |
| 5000 行表格 | 184.4 | 306.8 | +122.4 |
| envw | 217.1 | 298.6 | +81.5 |
| filescanw | 301.7 | 357.8 | +56.1 |
| wslmanw | 286.5 | 434.5 | +148.0 |
| cmderw | 378.5 | 372.7 | -5.8 |

常驻 RSS（中位数，MB）：

| 窗口 | Tk | Qt | 倍数 |
|---|---:|---:|---:|
| 空窗口 | 30.0 | 60.4 | 2.01× |
| 5000 行表格 | 38.4 | 73.9 | 1.92× |
| envw | 37.2 | 72.0 | 1.94× |
| filescanw | 43.1 | 81.1 | 1.88× |
| wslmanw | 42.6 | 82.6 | 1.94× |
| cmderw | 46.3 | 84.1 | 1.82× |

结论：

- 冷启动的固定成本主要来自 `import PySide6`（约 150–165 ms；Tk 侧 `import tkinter` 约 12 ms），
  单窗口构建本身不比 Tk 慢（envw 约 40 ms 对 163 ms，filescanw 约 97 ms 对 224 ms）；
- 常驻内存增量约 30–43 MB，除空窗口 2.01× 外均在 0013 的「< 2 倍 Tk」口径内；
  空窗口略超的部分是 `QApplication` 与平台插件的固定开销，与窗口内容无关；
- 便携包 98.0 MB 中 PySide6 占 50.4 MB（已裁剪），Tk 版便携包未在本机留存，
  同口径差值无法实测，只记当前绝对值。

## 已定决策

| 编号 | 结论 | 落实方式 |
|---|---|---|
| D1 | cmderw（8,735 行）**不纳入本轮**逐应用迁移，拆为独立阶段 B7 | 先迁外壳 + 双面板；列表虚拟化 / 缩略图队列在 B7 内单独评估。B1–B6 不触碰 `apps/cmder/app.py` 的 Tk 实现 |
| D2 | 应用迁完即删除 Tk 窗口代码，不留双份实现 | locllmw 的 `--ui tk` 与 cmderw 的 Tk 实现均已删除；仓库不再包含 Tk 窗口 |
| D3 | 现有窗口测试**只改写断言 Tk 内部实现**的用例 | 改为对 Qt 的等价行为断言；纯逻辑、设置、geometry 记忆等测试不动，不放宽条件、不删用例 |

## 交接

- 新增窗口：继承 `QtToolWindow`（单页）或 `QtMainWindow`（分页），不要各自复制菜单 / 状态栏 / 几何逻辑。
- 后台动作一律 `gui.qt.workers.submit(...)`；禁止在 GUI 线程做扫描 / 转换 / 网络。
- 设置文件沿用 `fcmd.gui.config`，字段与旧版保持同名同义，便于读取历史文件。
- 迁移一个应用时同步更新其模块 docstring 与工具 `help` 文本中的「Tk」字样。
- 提交 `eff8c36`（`feat(locllm): 新增引擎兼容性与启动错误诊断`）除 `llmbase.py` / `llmserve.py` /
  `tests/apps/llm/test_cli_llmserve.py` 三项主题改动外，还含并行会话的附带改动：README 6 行、
  locllmw 图标 9 个、devman / filesync / paramcad / paramesh / peview / reshack / videoconvw 各 1 行
  QTimer 生命周期、`src/fcmd/cli/_manifest_data.py` 1 行。已定**不改写历史**：该提交之后另有 161 个提交，
  拆分需要 rebase 全部重写，收益不抵与并行会话提交冲突的风险。回滚 locllmw 启动修复时按
  `llmbase.py` / `llmserve.py` / `test_cli_llmserve.py` 挑拣，不要整体 revert 该提交。
