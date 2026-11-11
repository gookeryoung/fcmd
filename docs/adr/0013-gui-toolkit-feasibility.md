# 0013 GUI 技术路线：不自研 C++ 绑定，优先榨干 Tk 路径

- 状态：生效（研究结论：不自研 C++ GUI 绑定）
- 日期：2026-11-11
- 关联：0005（cmderw 对齐 Total Commander）、0012（cmderw P3 文件工具）

## 背景

问题：fcmd 是否有必要自研一套 C++ 层的 Python 绑定库，用轻量、现代的 GUI 提升现有界面的
性能与观感。

本文只做可行性研究，不改代码。结论基于本机实测数据与现有实现的事实，测量脚本仅存在于
系统临时目录，不随仓库提交。

## 测量方法与环境

- 机器：Intel Core Ultra 9 285K（24 核）、63.4 GiB RAM、Windows 11 10.0.26300、125% 缩放
  （`winfo_fpixels('1i')=120`，`tk scaling=1.666`）。
- 运行时：Python 3.13.13、Tk 8.6、Pillow 13.0.0（仓库 `.venv`）。
- 目录读取用仓库现有 `list_directory` + `sort_entries`；列表渲染用与
  `FilePanel` 相同的 5 列 `ttk.Treeview`（名称/扩展名/大小/时间/属性，行高 24px）；
  缩略图用现有 `load_thumbnail_payload(96px)`；主题用 `fcmd.gui.theme.apply_theme`。
- 每组取 3–5 次中位数；目录扫描在系统目录（`C:\Windows\System32`，4,900 项）与临时目录
  （5,000 / 20,000 项）分别测量。临时目录受 Windows 实时防护影响，绝对耗时偏高，
  System32 读数更接近日常目录。

## 实测结果

### 目录读取与列表渲染

| 场景 | 实测 |
|---|---|
| `list_directory` + 排序，System32 4,900 项 | **731 ms** |
| 同上，SysWOW64 3,185 项 | **531 ms** |
| 同上，临时目录 5,000 项 / 20,000 项 | **1,215 ms / 8,704 ms** |
| 同一目录改用 `os.scandir` + `DirEntry` 缓存 stat | **16.7 ms**（System32，44×）/ **49 ms**（20k，177×） |
| Treeview 插入 1,000 / 5,000 / 20,000 行 | **4.2 / 22.1 / 93.8 ms** |
| Treeview 全量刷新（delete + 重新插入）5,000 / 20,000 行 | **23.2 / 108.7 ms** |
| 20,000 行首次绘制（`update()`） | **9–24 ms** |
| 20,000 行滚动一屏（含 `update`，20 次平均） | **18.6 ms/次** |
| 20,000 行键盘上下移动（`see` + 选中） | **1.4 ms/步** |
| 20,000 行全选 / 掩码过滤 | **4 ms / 14.7 ms** |
| 20,000 行 Treeview 常驻内存 | **+19.2 MiB（约 1.0 KiB/行）** |

### 颜色规则、主题、字体、缩略图

| 场景 | 实测 |
|---|---|
| 颜色规则匹配 5,000 项 × 3 条规则 | **26.6 ms** |
| `apply_theme`（含 36 个 ttk 样式、8 个具名字体） | **7.4 ms** |
| 正文字体 | Microsoft YaHei UI 9pt；`中文测试` 宽 60px |
| 缩略图解码 3000×2000 JPEG → 96px PNG | **19.5 ms/张**（Pillow，单线程） |
| `PhotoImage(data=PNG)` 创建 96px 图 | **0.26 ms/张** |
| Canvas 创建 120 个缩略图单元格（矩形 + 2 文本） | **6.8 ms** |

### 代码与分发现状

| 项 | 现状 |
|---|---|
| Tk GUI 规模 | 26 个模块、**27,812 行**；`cmderw/app.py` 单文件 **8,735 行**，其余为 23 个工具/共享 GUI 模块（2026-11-11 复核） |
| 业务逻辑分层 | 文件比较/同步/重命名/搜索/队列等纯逻辑已在 `_ops.py` 等模块并有单测；Tk 类标注 `pragma: no cover` |
| Python 依赖 | 纯 Python，`hatchling` 构建 `py3-none-any` wheel，`pip install fcmd` 直接可用 |
| 发布物 | PyPI wheel + Windows/Linux 便携 zip；当前 Windows 0.3.11 zip **74.78 MiB** |
| CI | ubuntu 上 Python 3.13/3.14 跑 lint/typecheck/test；**不跑 GUI 测试**，发布包只查 Tk 文件齐全 |
| 主题切换 | 菜单选择后**立即生效**（`apply_theme` 幂等 + 各窗口 restyle），同时写入配置 |

## 瓶颈归因

改造前，以 5,000 项目录为例，一次面板刷新的主线程耗时构成约为：

```
目录扫描 731 ms  ████████████████████████████████████████  96%
颜色规则  27 ms  █
列表插入  23 ms  █
首次绘制  10 ms  █
```

20,000 项时扫描（临时目录 8.7 s）占比更高，Treeview 插入仍只有 94–109 ms。
结论：**当时的瓶颈是「每条目多次 stat 的目录枚举」，不是 Tk 的渲染或控件能力。**
这也是决定「先做局部优化、不换工具包」的依据。

具体到代码（括号内为本轮改造结果）：

- [list_directory](file:///f:/Dev/fcmd/src/fcmd/apps/cmder/app.py#L840-L868) 原先对每个条目依次调用
  `child.stat()`、`child.is_dir()`、`child.is_symlink()`，在 Windows 上等于每个条目
  **3 次独立的元数据查询**；改用 `os.scandir` 的 `DirEntry`（已缓存 `FindFirstFile` 结果）
  后只需 ~1 次查询。System32 实测 731–918 ms → 19.6 ms。
- [FilePanel.refresh](file:///f:/Dev/fcmd/src/fcmd/apps/cmder/app.py#L2090-L2173) 原先在 Tk 主线程里
  同步完成扫描、颜色规则、全量删除与重新插入；现在扫描移到每面板一个后台线程，结果按世代号
  回投，主线程分块插入。20,000 项单次阻塞从 910–1,194 ms 降到 0.3–5.4 ms。
- [分支视图](file:///f:/Dev/fcmd/src/fcmd/apps/cmder/app.py#L2336-L2376) 原先把逐条 stat 与
  `pending.pop(0)`（O(n²)）叠加，现改用 `deque` 与共用扫描函数；10,000 项 3,854 ms → 337 ms。
- 缩略图架构本来就是对的：后台解码、世代号取消、只画可视区、缓存有上限。原先吞吐被主线程
  `_THUMB_APPLY_PER_TICK=2` / `60 ms` 限制在约 33 张/秒；提高到 8 张 / 4 worker 后实测
  41.4 张/秒（120 格 5.87 s → 2.90 s）。
- DPI/字体：`enable_dpi_awareness()` 已声明 Per-Monitor，但 Tk 8.6 只在启动时读取一次缩放；
  字体用磅值，[Spacing](file:///f:/Dev/fcmd/src/fcmd/gui/theme.py#L176-L186) 原先固定像素，
  高 DPI 下字大间距小。现已按 `winfo_fpixels('1i') / 96` 等比放大（上限 2.0）；
  Windows 深色标题栏由 `dwmapi` 补齐（Win10 20H1+，失败静默）。
- 观感天花板：`clam` + 调色板/字体/行高可以做到统一配色、斑马纹与焦点环，但 Tk 8.6 没有圆角、
  阴影、动画、逐行/单元格内嵌控件、每显示器热切换 DPI；原生深色标题栏只能走 DWM，且仅 Windows。
  这些是工具包能力边界，不是优化能解决的。

## 备选路线对比

| 路线 | 性能上限 | 观感能力 | 体积与分发 | CI / 跨平台 | 学习与维护 |
|---|---|---|---|---|---|
| **Tk 8.6 + 局部优化（现状）** | 5k 行插入 23 ms、20k 行 109 ms；滚动约 19 ms/帧；扫描优化后 4.9k 目录约 17 ms | 统一配色/字体/行高；无圆角、阴影、动画、逐显示器 DPI 热切换 | 随 Python 自带，现有 zip 74.78 MiB | 纯 Python wheel，Windows/Linux 两套 zip；CI 不跑 GUI | 已熟悉；改动局限在 `fcmd.gui` 与 cmderw |
| **PySide6 / PyQt6** | 模型/视图虚拟化，10 万行级列表；Qt 原生绘制 | QSS、动画、圆角阴影、HarfBuzz 文本整形、每显示器 DPI、系统托盘/对话框齐全 | PySide6-Essentials 约 70–100 MB 下载、安装后约 200 MB；便携包预计 250–300 MiB | 官方 wheel 覆盖 Windows/Linux/macOS；无头测试用 `QT_QPA_PLATFORM=offscreen`；LGPLv3（PyQt 为 GPL/商业） | Qt Model/View、信号槽；API 面大但文档完善 |
| **wxWidgets (wxPython)** | 原生 `wxListCtrl` 虚拟模式，10 万行级 | 原生控件观感，跟随系统；自定义视觉能力弱 | 约 +20–60 MB（随平台） | 官方 wheel 对新 CPython 跟进较慢（3.14 可能需自行编译）；Linux 包管理差异 | 中等；原生控件行为随平台变化 |
| **Dear ImGui** | 即时模式 + GPU，帧率极高 | 游戏/调试器风格；CJK 需手工字形范围/字体图集，IME 与无障碍弱 | 绑定 + GLFW/SDL，约数 MB | pyimgui wheel 覆盖有限，需自带窗口/GL 后端 | 中等，但不适合文件管理器这类文本密集、需 IME/无障碍的应用 |
| **egui / iced（Rust）** | 60 fps，wgpu 渲染 | 现代视觉，但 CJK 字体需自带，文本整形/IME 生态较弱 | 二进制约 5–20 MB | maturin + 每目标 wheel；Rust 工具链、符号与崩溃调试成本高 | 高；跨语言 FFI 与 GUI 生态都需重新学习 |
| **WebView2 / Tauri 式前端** | 浏览器引擎虚拟化，1 万行 DOM 无压力；IPC 有开销 | CSS 能做到最强视觉与动画 | 应用 3–10 MB，但依赖系统 WebView2（Win）/WebKitGTK（Linux） | 双进程架构；Linux 需确保 WebKitGTK；调试用浏览器 DevTools + Python 后端桥 | 高；前后端两套技术栈与 IPC 协议长期维护 |
| **自研 C++ 渲染层（Skia + 自绘控件 + pybind11/nanobind）** | 上限最高，可 120 fps | 上限最高，但圆角阴影只是起点；文本整形、IME、无障碍、拖放、剪贴板、原生菜单都要自建 | Skia 预编译约 10–30 MB/平台，加自研代码 | 每平台/每 Python 版本 wheel；MSVC/GCC 工具链、符号服务器、崩溃转储 | 极高；本质是造一个 GUI 工具包，不是「写一层绑定」 |

## 自研 C++ 绑定的真实代价

1. **工具链与 ABI**：pybind11/nanobind + CMake/scikit-build-core；Windows 用 MSVC、Linux 用
   GCC/Clang。nanobind 可用稳定 ABI（abi3）减少 wheel 数量，但会限制可用 CPython 特性；
   Python 3.14 的自由线程构建还需要单独验证 GIL 语义。
2. **wheel 与 CI 矩阵**：当前 `pip install fcmd` 是 `py3-none-any`。引入扩展后至少要
   cp313/cp314 × win_amd64/linux_x86_64（若支持 ARM 再翻倍），加 sdist；每个 wheel 都要在
   原生 runner 构建，fspack 还要能解析并打包对应 wheel。发布流程从「一次 `uv build`」变成
   「多平台构建 + 冒烟 + 校验」。
3. **GIL 与线程模型**：渲染线程不能持有 GIL，回调 Python（数据源、格式化、事件）必须
   `PyGILState_Ensure`；Python 侧调用 C++ 时又要考虑释放 GIL。Tk 的单线程亲和模型将被替换为
   自建的事件循环、跨线程队列与取消语义，任何一处锁顺序错误都会表现为随机卡死。
4. **异常与生命周期**：C++ 异常跨边界要翻译成 Python 异常；Python 对象在渲染线程的引用计数、
   窗口销毁与后台任务竞态、临时缓冲区的所有权，都需要比纯 Python 更严格的约定与测试。
5. **调试与可观测性**：混合栈（C++ + Python）用 gdb/lldb，`py-spy` 无法覆盖渲染线程；
   需要崩溃转储、符号表、sanitizer、GPU 驱动差异排查。CI 里跑 GUI 还要虚拟显示或离屏渲染。
6. **功能债**：文件管理器不只「画列表」——文本整形与字体回退、IME 组字、无障碍（UIA/AT-SPI）、
   拖放、剪贴板、原生菜单与文件对话框、高 DPI、焦点与键盘导航、输入法候选窗定位，都要自己实现
   或再绑定一套库。这部分工作量远大于绑定层本身。
7. **长期维护**：编译器/SDK 升级、平台 API 变化、字体与 IME 回归、每个新 CPython 版本的
   wheel 重建，都会成为发布阻塞项；单人维护的项目尤其如此。
8. **迁移与共存**：Tk 主循环与自研渲染循环只能二选一；若想「只替换列表视图」，Windows 可靠
   HWND 子窗口嵌入，Linux 需要 XEmbed/独立窗口，跨平台一致性立刻破功。焦点、DPI、IME、
   快捷键路由都要在兩套 UI 之间协调。

## 迁移策略选项

| 方案 | 代价 | 收益 | 风险 |
|---|---|---|---|
| **全量替换**（26 个模块、27,813 行） | 最高；等于重写全部 GUI，且其余工具窗口与 cmderw 一起推倒 | 观感与性能上限最高 | 一次性切换，回归面最大；发布体积与 CI 成本永久上升 |
| **只替换高危视图**（列表 + 缩略图） | 中高；仍要引入 Qt/自研渲染依赖；跨平台嵌入困难 | 只解决已测得占比 <5% 的渲染耗时 | 两套事件循环/DPI/焦点/IME 共存，长期复杂度高；扫描瓶颈未解决时收益接近于零 |
| **维持 Tk + 局部优化**（推荐） | 低；改 `list_directory`、`_list_branch`、缩略图节流、DPI 间距 | 实测有 **10–30×** 的刷新空间与 **约 4×** 的缩略图填充空间；零新依赖、零分发变化 | 观感上限仍在：无圆角/阴影/动画/每显示器 DPI 热切换 |

## 决策

**不做自研 C++ 绑定库。** 理由：

1. 实测瓶颈 96% 在目录枚举，不在 Tk 渲染；换 GUI 工具包不会让 `stat` 变快。
2. 「自研 C++ 绑定」实际是「自建 GUI 工具包」，需要同时解决文本整形、IME、无障碍、DPI、
   原生对话框与多平台 wheel，投入与长期维护成本远超收益。
3. fcmd 的 GUI 是 26 个模块、2.8 万行的工具集合，不是单一旗舰应用；全量替换会拖垮发布节奏，
   只替换视图又无法跨平台干净共存。
4. 当前分发是纯 Python wheel + 两套便携 zip；引入二进制 GUI 层会把发布、CI、体积与
   安装成功率一起复杂化，收益却只落在少数视图上。

**推荐路线：先做 Tk 局部优化**（见下节），本轮不引入 Qt/WebView/自研渲染。

## 现有 Tk 路径还能挖出多少空间

按实测拆解，5,000 项目录一次刷新：

| 阶段 | 改造前 | 改造后（实测） | 手段 |
|---|---|---|---|
| 目录扫描 | 731–918 ms（System32 4,898 项） | **19.6 ms** | `os.scandir` + `DirEntry` 缓存 stat，一次查询拿到类型/大小/时间 |
| 颜色规则 | 27 ms | 无规则时 **0 ms** | 规则为空直接跳过 `ColorSubject` 与样式解析 |
| 列表插入 | 23 ms | 23 ms（分块后每块 ≤ 18.5 ms） | 每 1,000 行让出一次事件循环 |
| 首次绘制 | 10 ms | 10 ms | 无需改 |
| **合计** | **约 790 ms** | **约 55 ms** | **约 14×** |

20,000 项（临时目录）：扫描 8.7 s → **101.5 ms**；主线程单次阻塞 910–1,194 ms →
**0.3–5.4 ms**（扫描在后台线程）；后台结果返回后分块填充总耗时 410–560 ms，期间界面可响应。

分支视图：deque 替代 `pending.pop(0)` 后，1,000 目录 × 10 文件（10,000 项）从
3,854 ms 降到 337 ms（约 10.5×）。

缩略图：`_THUMB_APPLY_PER_TICK` 2 → 8、worker 2 → 4 后，120 个可视缩略格实测
5.87 s（102/120，约 17.4 张/秒）→ **2.90 s（120/120，约 41.4 张/秒）**，约 2.4×。

观感（不引入新依赖也能做，均已落地）：

- 按 `tk scaling` 同步放大 [Spacing](file:///f:/Dev/fcmd/src/fcmd/gui/theme.py#L176-L186)，
  解决高 DPI 下「字大间距小」；
- 斑马纹交替行、活动面板焦点环（`Active.Treeview`）；
- Windows 深色标题栏（`dwmapi`，仅 Windows，失败静默回退）；
- 主题热切换：`install_theme_switcher` 与 cmderw「配置 → 主题」都立即重设样式，无需重开窗口；
- 这些都不改变工具包能力边界：圆角、阴影、动画、每显示器 DPI 热切换仍然做不到。

## 触发条件（何时重新评估）

满足任意一条再启动工具包替换评估，且**优先 PySide6，而不是自研 C++ 绑定**：

1. 单目录常态 ≥ 50,000 项，且完成扫描/分块优化后滚动 p95 仍 > 16 ms；
2. 圆角、阴影、60 fps 动画、每显示器 DPI 热切换被列为验收硬指标；
3. 完成 Tk 优化后，目标机器上 5,000 项目录刷新仍 > 300 ms；
4. 列表/表格内嵌控件、富文本或无障碍（UIA/AT-SPI）成为硬需求；
5. 维护者明确接受新增约 200 MB 依赖、便携包增至 250–300 MiB，并愿意维护第二套 GUI 技术栈。

## 若触发：分阶段路线与最小验证

- **P0（2 周 spike，最小验证）**：只做 PySide6 `QListView + QAbstractTableModel` 的 5 万行列表
  + 缩略图网格，复用现有 `list_directory`/`_ops` 数据层。验收门槛：5 万行滚动 p95 < 16 ms；
  切目录刷新 < 100 ms；RSS < Tk 方案的 2 倍；便携包 < 300 MiB；Windows/Linux 都能离屏跑通冒烟。
- **P1**：cmderw 外壳（双面板/标签/菜单/命令行）以 `cmderw --ui=qt` 旁路接入，Tk 仍为默认。
- **P2**：列表、缩略图、目录树、对话框逐个迁移，纯逻辑与测试不动。
- **P3**：对照检查表通过后切换默认；其余 Tk 工具窗口保持不动，长期共存。
- **自研 C++ 绑定不在路线内**：只有 P0–P3 证明 PySide6 仍不满足时，才重新评估，并需单独 ADR。

## 落地状态

- 研究结论与 Tk 局部优化（T1–T7）已全部落地，实测数字见上节：
  T1 `os.scandir` + `DirEntry` 扫描；T2 分支视图 deque BFS；T3 后台扫描 + 世代号取消；
  T4 分块插入与选择恢复；T5 缩略图 worker/节流；T6 DPI 间距、斑马纹、焦点环、深色标题栏；
  T7 主题热切换。
- 纯逻辑（扫描/过滤/分支/世代号通道/斑马纹 tag、DPI 间距换算、亮度判定、DWM 属性回退）
  均有单测；Tk 类与 `dwmapi` 调用仍标注不追求覆盖率。
- 基准脚本只在本机临时目录，不随仓库提交。

## 交接

- 复测方法：用现有 `list_directory`/`sort_entries`、5 列 `ttk.Treeview`、
  `load_thumbnail_payload(96)` 与 `apply_theme`，在 `C:\Windows\System32`（约 4,900 项）和
  5,000/20,000 项目录上取中位数；注意临时目录受实时防护影响，需与系统目录分开看。
- 扫描层：`FileEntry` 字段与 `sort_entries` 行为不变；`is_hidden` 语义不变；符号链接
  `stat()` 跟随目标（保持旧 `Path.stat()` 语义），断链回退 0；分支视图与普通列表共用
  `_entry_from_dir_entry`。不要退回 `Path.is_dir()`/`is_symlink()` 的逐条查询。
- 异步刷新：每面板一个单线程执行器 + `PanelScanChannel` 世代号；旧行在新结果到达前保持
  可见；选择意图经 `refresh(select=)`、`_restore_selection`、`_pending_select`、
  `_last_selection` 四路汇合。改动时注意 `add_done_callback` 必须捕获提交时的世代号。
- 分块插入：`_INSERT_CHUNK = 1000`（每块约 10–19 ms）；若常态 > 50,000 项可下调块大小，
  但不要退回一次性插入。
- 缩略图：4 worker + 每 60 ms 应用 8 张；再提高需同步验证主线程单次创建 `PhotoImage` 的
  耗时，否则会重新出现掉帧。
- DPI/观感：间距按 `winfo_fpixels('1i') / 96` 缩放（上限 2.0）；主题热切换只重设样式与
  自建 tag，已按旧间距布局的 padding 要下次启动才更新；深色标题栏需 Windows 10 20H1+，
  属性 19 为旧系统回退。
- 若未来启动 PySide6 评估：先做 P0 spike 并记录上述门槛，不要直接改默认入口；新增依赖与
  打包体积变化需同步更新 README、pyproject 与发布 workflow。
