# fcmd GUI 框架选型分析：Tk 之上是否值得换栈

- 分析对象：`F:\Dev\fcmd`，HEAD `4dbe4e6`（2026-11-13），版本 `0.3.12`
- 分析日期：2026-11-14
- 边界：**只读分析，仅新增本文件**。未改业务代码、未改配置、未提交。
- 并行会话：工作区已有 wslman 下载/安装链路的未提交改动（`wslmanw.py`、`_manifest_data.py`、`uv.lock`、`2026-11-13-wslman-gap-analysis.md`），本轮未触碰、未回滚、未纳入。
- 标注约定：**【实测】** 本轮在本机测得；**【清单】** 发布清单/仓库文件；**【代码】** 静态阅读；**【文档】** 上游文档/许可证；**【推测】** 未验证。

---

## 0. 结论摘要

1. **体积问题不是 Tk 造成的。** 当前 Windows 安装包 **149.1 MB**；清单内 `site-packages` **280.9 MB**，其中 PyMuPDF 97.8、onnxruntime 45.8、numpy 35.0、Pillow 15.6，四项合计 **194.2 MB（69.1%）**。Tk 运行期（`tcl/` + `tcl86t.dll` + `tk86t.dll` + `_tkinter.pyd`）只有 **8.31 MB 原始 / 3.21 MB 压缩**，占安装包 **2.2%**。换 GUI 框架只会让安装包更大，不会更小。【实测/清单】
2. **换 Qt 的真实代价：** PySide6-Essentials 安装后 **212.8 MB**、deflate 压缩 **80.1 MB**；Essentials + Addons 安装后 **312.9 MB**、压缩 **118.8 MB**。安装包将从 149.1 MB 增至约 **229 MB（+54%）** 或 **268 MB（+79%）**，且 Qt 6 官方只支持 **Windows 10 1809+**，与仓库现有的 Win7 兼容扫描口径冲突。【实测/文档】
3. **观感问题是真的，但可以用两种方式解决，不必全仓换栈。** Tk 8.6 的能力边界（无圆角/阴影/动画/每显示器 DPI 热切换/单元格内嵌控件）确实存在；但现有 [`theme.py`](../../src/fcmd/gui/theme.py) 已经做了 4 套主题、DPI 间距、深色标题栏、CJK 字体探测与热切换。剩余差距一部分靠视觉规范补齐，另一部分只有换工具包才能补齐。
4. **首选方案：保持 Tk 为主，视觉与体积分开治理。** 默认不引入第二套 GUI 依赖；先把 `fcmd.gui.theme` 的控件密度、图标、状态色、对话框规范做统一，再做依赖裁剪/分包。代价最小、Win7/Linux/打包链不变。
5. **唯一值得投入的换栈路线：只给 `cmderw` 引入 PySide6**，以 `cmderw --ui=qt` 旁路接入，先做列表 + 缩略图 P0；通过 ADR 0013 的验收门槛后，再考虑 `paramcadw` / `parameshw` / `pptmakew` 三个预览型窗口。其余 24 个窗口保持 Tk。【推荐】
6. **明确不推荐作为主 UI：** wxPython（观感提升有限、Linux 无 wheel、+18.9 MB）、Dear PyGui（文本/IME/无障碍不适合）、Flet（桌面客户端 38.4 MB，1.1.2 改为运行时下载）、Toga（0.5.x 未成熟、Windows 走 .NET/WinForms）、pywebview（WebView2 依赖 + 双栈；只适合单窗口预览试点）。【结论】
7. **如果“现代观感”是硬指标、且能接受 Win10+ 与 +80 MB**，那就选 **PySide6，而不是 wx/Flet**；如果“体积”是硬指标，则**任何换栈都不成立**，应先做依赖裁剪。【决策条件】
8. **与 ADR 0013 一致：** 不自研 C++ 绑定；若触发换栈优先 PySide6。本文件补充了 2026-11-14 的体积/启动实测，并修正了 ADR 中“wxPython 3.14 可能需自行编译”的过时判断（现在已有 cp313/cp314/cp315 wheel，但 Linux 仍无 wheel）。【代码/实测】

---

## 1. 现状基线

### 1.1 GUI 代码规模与分层【实测】

| 项 | 数值 |
|---|---:|
| 窗口模块（27 个 `*w.py` + `cmder/app.py`） | **28 个 / 27,188 行** |
| 共享基座 `fcmd/gui/`（theme / window / config / `__init__`） | 4 个文件 / **1,497 行** |
| 最大单文件 `apps/cmder/app.py` | **7,886 行 / 29 个类 / 540 个方法** |
| 使用 `Canvas` 的窗口/预览模块 | 6 个窗口 + 1 个纯逻辑预览模块（pptmakew、paramcadw、reshackw、pevieww、parameshw、cmder/app.py、pptmake/`_preview.py`） |
| GUI 测试口径 | Tk 类普遍 `# pragma: no cover`，CI 不跑 GUI，仅校验 Tk 运行期文件齐全（release.yml L201–232） |

> 口径说明：ADR 0013（2026-11-11）记录为“26 个模块 / 27,812 行 / cmderw 8,735 行”；本轮按当前仓库复核为 28 个窗口 / 27,188 行 / cmderw 7,886 行。两者文件归类与时间点不同，**不要直接相减**；本文一律以当前仓库实测为准。

分层是干净的：`_ops.py`、`_preview.py` 等纯逻辑与 Tk 视图分离；`fcmd.gui` 不反向依赖业务层。这意味着**逐应用换视图是可行的**，纯逻辑不需要重写。【代码】

### 1.2 现有 Tk 已经做到什么【代码】

- `theme.py`：`clam` + 自定义 ttk 样式、4 套内置主题（深色科技蓝/浅色/高对比/接近原生）、24 个基础样式名（ADR 0013 口径为 36 个样式规格）、4 级字号阶梯（small/body/heading/title）；
- DPI：`enable_dpi_awareness()` 声明 Per-Monitor；间距按 `winfo_fpixels('1i')/96` 缩放（上限 2 倍）；
- 中文：优先 Microsoft YaHei UI / 苹方 / Noto / 思源 / 文泉驿，启动时做 CJK 渲染自检；
- Windows 深色标题栏：`dwmapi`，Win10 20H1+，失败静默；
- 主题热切换：菜单切换后立即重设样式；
- cmderw：斑马纹、活动面板焦点环、列宽拖拽持久化、缩略图 LRU/世代号取消。

**这些都不是“没做美化”，而是 Tk 8.6 的能力上限**：没有圆角、阴影、60 fps 动画、每显示器 DPI 热切换、Treeview 单元格内嵌控件。ADR 0013 已把这条边界写清楚。【代码/文档】

### 1.3 分发物体积（以本机 0.3.12 发布清单为准）【实测/清单】

| 组成 | 未压缩 | 文件数 | 说明 |
|---|---:|---:|---|
| `site-packages` | 280.9 MB | 6,126 | 第三方依赖 |
| `runtime`（CPython 3.14.6 + Tcl/Tk + stdlib） | 31.9 MB | 1,073 | 含 Tk 8.31 MB |
| `src`（fcmd 源码） | 9.7 MB | 625 | — |
| 每命令 launcher（125 个 × 153,911 B） | ≈ 19.2 MB | 125 | 共享 runtime/site-packages |
| **合计（未压缩）** | **≈ 342 MB** | 7,949 | — |
| **安装包** `fcmd-0.3.12-py3.14.6-windows-x86_64-slim-setup.exe` | **149.1 MB** | 1 | fspack + NSIS |

> 说明：`dist/site-packages` 与发布清单一致；`dist/release` 里的文件名带 `slim`，但实际仍包含 `[tool.fspack] extras` 的全部依赖。当前安装包压缩率约 43%。ADR 0013 中的 74.78 MiB 是 0.3.11 的 Windows 便携 zip，与本机 0.3.12 的 `slim-setup.exe` 口径不同，不可直接相减。

### 1.4 体积归因：GUI 不是主要矛盾【实测/清单】

| 依赖 | 未压缩 | 占比 |
|---|---:|---:|
| pymupdf | 97.8 MB | 34.8%（占 site-packages） |
| onnxruntime | 45.8 MB | 16.3% |
| numpy + numpy.libs | 35.0 MB | 12.5% |
| PIL (Pillow) | 15.6 MB | 5.6% |
| cryptography | 10.5 MB | 3.7% |
| pygments | 8.5 MB | 3.0% |
| networkx | 8.3 MB | 3.0% |
| Cryptodome | 6.7 MB | 2.4% |
| lxml | 6.2 MB | 2.2% |
| pywin32（win32/pythonwin/win32comext） | 13.2 MB | 4.7% |
| **Tk（在 runtime 内）** | **8.31 MB** | **2.2%（占安装包；占 runtime 26%）** |

**关键事实：**
- Tk 的压缩体积 3.21 MB，只占 149.1 MB 安装包的 **2.2%**；即使完全去掉 Tk，安装包也只降到约 146 MB。
- 第一项 pymupdf 一项就是 Tk 的 **11.8 倍**。
- 清单里包含 `onnxruntime`（45.8 MB）和 `numpy`（35.0 MB），但源码中静态搜索 **0 处** `onnxruntime` / `numpy` 引用。归属已查明：**onnxruntime 是 PyMuPDF 2.1 的硬依赖**，numpy/networkx 等同链带入；根因是 dependabot 把 `pymupdf<2` 放宽成 `<3`，而打包脚本不走 `uv.lock`。详见[依赖审计](2026-11-14-fcmd-dependency-audit-pymupdf-onnxruntime.md)。【代码/清单/提交】

> 结论：如果目标是“安装包更小”，正确动作是依赖裁剪/分包，不是换 UI 框架。详见 §7。

### 1.5 现状的“观感天花板”具体在哪【代码】

| 能做的（Tk 内可解） | 做不到的（Tk 能力边界） |
|---|---|
| 统一配色、字体阶梯、间距栅格 | 圆角、阴影、毛玻璃 |
| 扁平化 ttk 样式、hover/pressed/focus 状态 | 60 fps 过渡动画 |
| 斑马纹、行高、对齐、焦点环 | 每显示器 DPI 热切换 |
| 图标（PNG/SVG 转 PhotoImage） | 单元格内嵌按钮/进度条/富文本 |
| 深色标题栏（Windows） | 原生级的无障碍（UIA/AT-SPI） |
| 对话框尺寸/按钮顺序统一 | 复杂的 2D/3D 自绘与高 DPI 图标自动换源 |

---

## 2. 测量口径

### 2.1 体积【实测】

- 用 `uv pip install --target <临时目录> <包>` 安装到系统临时目录（不污染仓库），统计**安装后全部文件字节数**；
- 用 `System.IO.Compression.ZipFile` 以 Optimal（Deflate）压缩同一目录，统计 **zip 字节数**，作为“进安装包后的增量”的保守估计（NSIS 用 LZMA，通常略低，但量级不变）；
- 同时用 PyPI JSON API 记录 Windows wheel 下载体积，作为交叉校验；
- 当前基线用 `dist/release/fcmd-0.3.12-py3.14.6-windows-manifest.json` 的 `entries[].size` 聚合，避免把本地残留文件算进去。

### 2.2 启动【实测】

- 机器：Windows 11（10.0.26300），Intel Core Ultra 9 285K，63.4 GiB RAM，125% 缩放；
- 解释器：系统 CPython **3.13.13**（conda-forge），热文件缓存；
- 每个用例 5 次取中位数，单位 ms；**含约 21 ms 解释器基线**；
- “窗口创建”包含 toolkit 初始化、创建并显示一个空窗口、处理一次事件、销毁；
- 打包运行时是 CPython 3.14.6；PySide6 是 `cp310-abi3`、wxPython 有 `cp314` wheel，版本可用性不是障碍。未在 embeddable runtime 内复测（其 `._pth` 隔离 `sys.path`，不能直接 `PYTHONPATH` 注入临时包），故以系统 Python 的同口径数字为准。

### 2.3 观感/能力【文档/代码】

- 按工具包公开能力与许可证判断，不做主观截图评分；
- 中文渲染、HiDPI、自绘能力按“是否内建/是否需要手工补齐”分级；
- 迁移成本按**改动行数与涉及窗口数**分级，不按工期估算。

---

## 3. 候选框架逐项对比

### 3.1 能力对比总表

| 候选 | 观感/主题能力 | HiDPI + 中文 | Windows 优先 + 跨平台 | Canvas/自绘与预览 | 许可证 | 成熟度/生态 |
|---|---|---|---|---|---|---|
| **Tk 8.6（现状）** | 统一配色/字体/间距；无圆角、阴影、动画 | DPI 仅启动时读取；中文依赖字体与 Xft | Win/Linux/macOS；Win7 可运行 | Canvas 无抗锯齿/透明；当前预览够用 | PSF | 标准库，零供应链风险 |
| **ttkbootstrap** | 20+ 主题、bootstyle；控件形状仍是 ttk | 继承 Tk | 同 Tk | 同 Tk | MIT | 纯主题层，低风险 |
| **CustomTkinter** | 圆角、深色模式、现代配色；无阴影/动画 | 有缩放钩子；文本仍走 Tk | 同 Tk | 同 Tk（自带控件是 Canvas 绘制） | MIT | 社区活跃，单人维护风险 |
| **PySide6 / Qt 6** | QSS、圆角阴影、动画、原生对话框、托盘 | 每显示器 DPI、HarfBuzz 文本整形、IME/无障碍完整 | Win10 1809+ / Linux / macOS；**不支持 Win7** | QPainter/QGraphicsView 抗锯齿、透明、变换、OpenGL | LGPLv3（动态链接需合规） | 官方 Qt，生态/文档最完整 |
| **wxPython** | 原生控件观感；自定义主题能力弱 | 原生 DPI/中文，随平台 | Win/macOS 有 wheel；**Linux 仅 sdist** | wx.GraphicsContext（GDI+/Direct2D），中等 | wxWindows Licence（LGPL 类） | 成熟但生态小，发布节奏慢 |
| **Dear PyGui** | GPU 即时模式，调试器/游戏风 | 需手工字体图集；IME/无障碍弱 | Win/Linux/macOS wheel；DX11/GL | GPU drawlist，2D/3D 预览最强 | MIT | 小众，文档/生态一般 |
| **Flet** | Flutter/Material 3，观感最强档 | Flutter 文本引擎，中文/IME 好 | Win10+ / Linux GTK / macOS；桌面客户端独立进程 | Flutter Canvas/CustomPaint | Apache-2.0 | 1.x 较新，API 仍在演进 |
| **Toga** | 原生控件外观，主题能力有限 | WinForms 原生文本/IME | Win（.NET/WinForms）/ Linux GTK / macOS | **无通用 Canvas** | BSD-3 | 0.5.x，未到 1.0 |
| **pywebview + 本地 Web** | HTML/CSS 上限最高 | WebView2/Chromium，中文/IME 最好 | Win10/11 WebView2；Linux WebKitGTK；Win7 仅旧版 WebView2 | Canvas/SVG/WebGL 最强 | BSD-3 | 6.x，够用；双栈维护 |
| **PyQt6** | 同 Qt | 同 Qt | 同 Qt | 同 Qt | **GPLv3 / 商业** | 同 Qt，但许可证不适合 MIT 分发 |

### 3.2 体积与启动实测

| 候选（Windows / CPython 3.13） | 安装后 raw | Deflate zip | wheel 下载（参考） | import 中位数 | 空窗口创建中位数 |
|---|---:|---:|---:|---:|---:|
| 解释器基线（`pass`） | — | — | — | **21 ms** | — |
| Tk（现状，stdlib） | **8.31 MB** | **3.21 MB** | 随 CPython | **42 ms** | **149 ms** |
| ttkbootstrap + CustomTkinter + darkdetect | **4.4 MB** | **1.4 MB** | 0.5 + 0.3 MB | ttkbootstrap **172 ms** / CTk **224 ms** | 未单独测（Tk 基线 + 控件构建） |
| PySide6-Essentials + shiboken6 | **212.8 MB** | **80.1 MB** | 78.3 + 1.2 MB | **135 ms** | **336 ms** |
| PySide6-Essentials + Addons | **312.9 MB** | **118.8 MB** | 118.1 MB | — | — |
| wxPython（仅 `wx/`，numpy 已随项目存在） | **58.3 MB** | **18.9 MB** | 18.8 MB（cp314） | **57 ms** | **148 ms** |
| Dear PyGui | **5.2 MB** | **2.0 MB** | 1.9 MB（cp314） | **61 ms** | 未测（需 viewport） |
| Flet（Python 侧，不含桌面客户端） | **5.2 MB** | 未单独测 | 0.6 MB | **119 ms** | 客户端启动未测（明显更高） |
| Flet 桌面客户端（0.28.3 内置版） | — | **38.4 MB** wheel | 38.4 MB | — | — |
| Toga + winforms 栈（不含 Pillow） | **3.8 MB** | **1.0 MB** | ≈1.1 MB（仅 Python 包） | **90 ms** | 未测（需 .NET） |
| pywebview + pythonnet 栈 | **3.1 MB** | **1.0 MB** | ≈1.2 MB（仅 Python 包） | **128 ms** | 未测（需 WebView2） |

> 说明：
> - Tk 行是仓库 runtime 内的实测占用（不是 pip 安装包），其余行为 `uv pip install --target` 的安装后体积。
> - PySide6 的约 3,800 个文件是“Essentials + Addons”的总数（实测 3,832）；仅 Essentials 也有 2,584 个文件，安装/杀软扫描/写入放大的代价明显。
> - wxPython 的 98.8 MB 里包含 numpy 35 MB；项目已自带 numpy，故表中只计 `wx/` 的 58.3 MB。
> - CTk/ttkbootstrap 的增量不含 Pillow（项目已依赖 Pillow）。
> - Flet 1.1.2 的 `flet-desktop` wheel 只有 **54 KB、8 个条目**，源码注释明确写着“provision the desktop client at runtime”（运行时下载/缓存客户端）；0.28.3 才是 38.4 MB 的内置客户端。对离线安装包这是实质性风险。

### 3.3 逐项评述

#### 3.3.1 Tk 8.6 + 现有 theme

- **优点**：零新依赖、零体积增量、Win7/Linux 现有打包链完全不变；现有主题系统已经覆盖了大部分“统一感”问题；启动最快（149 ms）。
- **缺点**：圆角/阴影/动画/每显示器 DPI/内嵌控件是硬边界；Canvas 无抗锯齿，预览缩放/旋转的观感一般。
- **适合**：当前 28 个窗口中的绝大多数（表单、日志、简单表格）。
- **不适合**：`cmderw` 这类旗舰文件管理器、需要精细自绘的预览窗口。
- **结论**：默认继续用；先把“视觉规范”做扎实，而不是换栈。

#### 3.3.2 ttkbootstrap

- **优点**：纯 Python、MIT、0.5 MB wheel / 2.9 MB 安装；能快速拿到 20+ 主题和一致的 `bootstyle`；不改变 Tk 运行期依赖。
- **缺点**：控件形状仍是 ttk 的方角；import 172 ms，比 Tk 多约 130 ms；与现有 `theme.py` 的 4 套主题/自定义样式**功能重叠**，接入意味着替换或融合现有 1,091 行主题层。
- **结论**：不建议作为全局依赖引入；可作为“主题配色参考”。如果只想快速换色，现有 `theme.py` 加一套调色板更划算。

#### 3.3.3 CustomTkinter

- **优点**：圆角、深色模式、现代配色，观感比 ttk 明显好；纯 Python、MIT；4.4 MB 增量（含 ttkbootstrap）中 CTk 自身只占 1.5 MB。
- **缺点**：CTk 控件是 Canvas 自绘，**Treeview、Text、Canvas 预览仍然是 ttk/tk**，文件管理器/预览类窗口只能“半个现代化”；import 224 ms（Tk 的 5 倍多），对短命窗口不友好；无阴影/动画；无障碍与 IME 支持一般；逐窗口重写，无法只换主题。
- **结论**：适合 1–2 个表单型窗口做试点（例如 `llmchatw` 507 行），不适合全仓替换，也不适合 `cmderw`。

#### 3.3.4 PySide6（Qt 6）

- **优点**：桌面端观感/能力上限最高的一档——QSS、圆角阴影、动画、每显示器 DPI、HarfBuzz 中文整形、原生 IME/无障碍、`QTableView` 模型视图虚拟化、`QGraphicsView` 抗锯齿自绘、原生文件对话框/托盘。官方 wheel 覆盖 Win/Linux/macOS，`cp310-abi3` 对 3.14 友好。
- **缺点**：
  - **体积**：Essentials 212.8 MB raw / 80.1 MB deflate；完整 312.9 MB / 118.8 MB；安装包 +54%~+79%。
  - **平台**：Qt 6 只支持 Windows 10 1809+，**Win7 直接出局**；Linux 便携 zip 需要额外带 xcb 相关系统库，当前 Tk 方案没有这个问题。
  - **文件数**：约 3,800 个文件（Essentials 2,584），安装时间、杀软扫描、SBOM/manifest 体积都会上升；Win7 导入表扫描会命中大量 Qt6 DLL，且无法像 onnxruntime 那样改写。
  - **许可证**：LGPLv3，动态链接分发需要保留声明、允许替换库；MIT 项目可以合规，但需要法务确认与打包规范。
  - **第二套栈**：Tk 与 Qt 不能在同一进程里稳定共存；只能按应用/按入口切换，长期维护两套 UI 规范。
- **结论**：不是“全仓替换”的候选，而是“旗舰窗口单点升级”的候选。

#### 3.3.5 wxPython

- **优点**：原生控件、启动快（148 ms，与 Tk 同级）、cp314 wheel 已存在（修正 ADR 0013 的旧判断）；18.9 MB 压缩增量，体积可接受。
- **缺点**：**观感提升有限**——原生控件意味着跟随系统，做不出 Qt/Flet 那种现代感；自定义主题能力弱；**Linux 没有 wheel**（PyPI 只有 sdist），Linux 便携 zip 需要在 CI 里编译 wxGTK，发布链复杂度显著上升；Win7 无官方承诺。
- **结论**：如果目标是“更像原生 Windows 程序”，可以评估；如果目标是“更好看”，不值得。当前不推荐。

#### 3.3.6 Dear PyGui

- **优点**：2.0 MB 压缩增量、61 ms import、GPU 即时模式，做实时曲线/3D 线框/大点数绘制非常强。
- **缺点**：不是桌面表单工具包；CJK 需要手工加载字体图集并指定 glyph range；IME、无障碍、文本选择/复制、原生菜单都弱；观感是“调试器/游戏工具”风，不是文件管理器风；与 Tk 并存只能开独立窗口，嵌入困难。
- **结论**：只在“`parameshw` 需要 60 fps 3D 视口”这种单点需求下才值得考虑；不作为主 UI。

#### 3.3.7 Flet

- **优点**：Flutter/Material 3，观感最强档；中文/IME 好；Python 侧 API 简单；Python 包本身只有 5.2 MB。
- **缺点**：
  - **桌面客户端不在 Python 包里**：0.28.3 的 `flet-desktop` wheel 38.4 MB；1.1.2 的 `flet-desktop` 只有 54 KB，运行时下载/缓存 Flutter 客户端。对离线安装包、内网机器、CI 复现都不友好。
  - 双进程架构（Python 服务 + Flutter 客户端），启动明显慢于 Tk/Qt；打包/签名/端口/防火墙都要额外处理。
  - Windows 10+；Linux 需要 GTK/Flutter 依赖；Win7 不支持。
- **结论**：不适合 fcmd 这种“一个安装包、离线可用、100+ 入口”的分发模型。除非未来改为在线安装器。

#### 3.3.8 Toga

- **优点**：Python 原生 API，BSD；Windows 后端走 WinForms（pythonnet），Linux 走 GTK，macOS 走 Cocoa；增量只有 1.0 MB 压缩（不含系统运行时）。
- **缺点**：**0.5.x 仍未到 1.0**，API/文档/生态都薄；Windows 需要 .NET/WinForms（+WebView2 用于 WebView 控件），Linux 需要 PyGObject/GTK 系统库，便携 zip 不友好；**没有通用 Canvas**，现有 `paramcad`/`paramesh` 预览无法直接迁移；观感偏原生，不会比 Tk 现代化多少。
- **结论**：不推荐。用它等于用“更不成熟的 Tk 替代品”换一点原生观感。

#### 3.3.9 pywebview / 本地 Web UI

- **优点**：**体积/观感比最高**——Python 侧 3.1 MB raw / 1.0 MB zip；HTML/CSS/SVG/Canvas/WebGL 的视觉上限最高；中文/IME 由 Chromium 保证；调试可用 DevTools。
- **缺点**：依赖系统 **WebView2 Runtime**（Win11 自带，Win10 多数已装；Win7 只能停在旧版 109）；Linux 需要 WebKitGTK；本质是**前后端两套技术栈 + IPC 协议**，状态同步、错误处理、打包（前端资源）都要长期维护；窗口内嵌/多窗口/原生菜单的体验取决于桥接层。
- **结论**：不建议作为主 UI；可以作为 1–2 个“纯预览/可视化”窗口的可选实验（例如 `parameshw` 的 3D 线框用 WebGL），但会引入第二套栈，收益要单独立项验证。

#### 3.3.10 其他候选（简要排除）

- **PyQt6**：与 PySide6 同体积、同能力，但 **GPLv3/商业许可**，MIT 项目分发二进制不合适；除非购买商业许可，否则不选。
- **Kivy**：移动端优先、全自绘、桌面观感不原生，打包体积与依赖都不小；不适合表单/文件管理器。
- **PySimpleGUI**：Tk/Qt/wx 的封装，观感不会超过底层；5.x 起商用需要注册许可；不选。
- **GTK / PyGObject**：Windows 打包与主题一致性差，Linux 系统依赖重；不选。
- **Electron / Tauri**：非 Python 原生，进程/体积/构建链都更重；不选。

---

## 4. 硬约束筛选：没有“全能赢家”

把用户关心的点转成可判定的门槛：

| 门槛 | 含义 |
|---|---|
| **G1 体积** | 安装包增量 ≤ 20 MB（当前 149.1 MB，用户明确在意体积） |
| **G2 Win7** | 仍能在 Win7 SP1 运行（仓库发布链有 `win7-compat-report.txt`） |
| **G3 Linux 便携** | Linux zip 不依赖目标机预装的 GUI 系统库 |
| **G4 现代观感** | 圆角/阴影/动画/每显示器 DPI/内嵌控件达到“明显更好看” |
| **G5 单栈** | 不引入第二套 GUI 技术栈/独立客户端进程 |

| 候选 | G1 体积 | G2 Win7 | G3 Linux 便携 | G4 现代观感 | G5 单栈 | 判定 |
|---|---|---|---|---|---|---|
| Tk + theme 深化 | ✅ (+0) | ✅ | ✅ | ❌ | ✅ | 只差观感 |
| ttkbootstrap | ✅ (+1.4) | ✅ | ✅ | ⚠️ 仅配色 | ✅ | 观感提升有限 |
| CustomTkinter | ✅ (+1.4) | ✅ | ✅ | ⚠️ 圆角/深色，无动画/DPI 热切换 | ✅ | 局部可用 |
| PySide6 | ❌ (+80.1) | ❌ (Win10+) | ❌ (xcb) | ✅ | ❌ (第二套栈) | 只在放宽 G1/G2/G3 时成立 |
| wxPython | ✅ (+18.9) | ⚠️ 无承诺 | ❌ (无 Linux wheel) | ❌ | ❌ | 不成立 |
| Dear PyGui | ✅ (+2.0) | ⚠️ | ⚠️ (GL) | ❌ (工具风) | ❌ | 不成立 |
| Flet | ❌ (+38.4 客户端) | ❌ (Win10+) | ❌ (GTK) | ✅ | ❌ (客户端进程) | 不成立 |
| Toga | ✅ (+1.0 + 系统) | ⚠️ | ❌ (GTK) | ❌ (无 Canvas) | ✅ | 不成立 |
| pywebview | ✅ (+1.0 + WebView2) | ❌ (旧版 WebView2) | ❌ (WebKitGTK) | ✅ | ❌ (JS 桥) | 仅单窗口实验 |

**这张表就是选型的核心结论：**

- 如果 **G4 不是硬指标** → **Tk** 是唯一同时满足 G1/G2/G3/G5 的方案；
- 如果 **G4 是硬指标** → 必须放宽 **G1/G2/G3/G5** 中的至少三条 → 只剩 **PySide6** 能同时满足“桌面原生 + 现代观感 + Windows 优先”；
- 如果 **G1 是硬指标** → 任何换栈都不成立，应先做依赖裁剪；
- 如果只想“少量窗口更好看”且能接受 WebView2/双栈 → **pywebview** 可作为预览窗口的实验，但不能解决主 UI。

---

## 5. 推荐方案

### 5.1 首选：Tk 为主，视觉与体积分开治理（默认路线）

**推荐理由：**

1. 28 个窗口中，绝大多数是表单/日志/简单表格，Tk 的能力足够；
2. 体积瓶颈 69.1% 来自 PyMuPDF/onnxruntime/numpy/Pillow，Tk 只占 2.2%；换 UI 与“减小体积”目标相反；
3. Win7 兼容扫描、Linux 便携 zip、纯 Python wheel、单栈维护、CI 不跑 GUI——这些现有约束在 Tk 下全部成立，换栈会同时破坏多项；
4. 观感问题中“配色/字体/间距/图标/状态/对话框规范”可以在 Tk 内解决，剩下做不到的部分（圆角/动画/每显示器 DPI）只有少数旗舰窗口真正需要。

**A 档具体动作（不新增 GUI 依赖）：**

| 动作 | 落点 | 预期效果 |
|---|---|---|
| 统一 8 px 间距栅格、控件高度、按钮顺序 | `fcmd/gui/theme.py` + `window.py` | 界面“整齐”，消除参差 |
| 扁平化 clam、去 3D 边框、统一 hover/pressed/focus | `theme.py` 样式表 | 减少“Win98 感” |
| 图标体系（PNG/SVG → PhotoImage，多尺寸） | `theme.py` + 各窗口工具栏 | 观感提升最大、成本最低 |
| 表格：行高、斑马纹、对齐、空状态/加载态 | `theme.py`、cmderw 等 | 列表更易读 |
| 对话框：统一宽度、主/次按钮位置、危险色 | `window.py` + 各窗口 | 一致性 |
| 深色主题对比度与禁用态 | `theme.py` | 可读性 |
| 高 DPI 图标与间距截图回归 | 人工验收清单 | 防止回归 |

> 这些动作的工作量是 **S**（单文件/少量文件，不改业务逻辑）。不会让 Tk 变成 Qt，但能把“参差、老旧”变成“统一、干净”。

### 5.2 条件式升级：只给 `cmderw` 引入 PySide6（B 档）

**触发条件（全部满足才做）：**

1. “现代观感/交互”被列为验收硬指标，且接受 **Windows 10+**；
2. 接受安装包 **+80 MB 左右**（Essentials 压缩实测 80.1 MB），或愿意为 GUI 单独出一个安装包变体；
3. 接受长期维护 Tk + Qt 两套 UI 规范；
4. 接受 Linux 便携 zip 需要额外处理 xcb 依赖，或 Linux 版继续用 Tk；
5. 以 ADR 0013 的 P0 门槛作为验收标准：5 万行列表滚动 p95 < 16 ms、切目录 < 100 ms、RSS < Tk 方案 2 倍、便携包 < 300 MiB。

**接入方式：**

- 新增 `src/fcmd/apps/cmder/cmderw_qt.py`（或 `_qt/` 子包），只做视图层；
- 复用现有 `list_directory` / `sort_entries` / `_ops.py` / 缩略图解码，不改数据层；
- 入口用 `cmderw --ui=qt` 旁路，默认仍是 Tk；**不要在同一进程里同时启动 Tk 与 Qt 主循环**；
- 先做 P0：`QTableView + QAbstractTableModel`（详细列表）+ `QListView` icon mode（缩略图），不做全部对话框；
- P0 通过后再逐项迁移标签页、命令行、比较/同步对话框；纯逻辑与测试不动。

**为什么只给 cmderw：** 它是唯一同时具备“高频使用、视觉密集、交互复杂、现状 7,886 行难维护”的窗口；Qt 的模型/视图、抗锯齿缩略图、每显示器 DPI 对它有真实收益。其余窗口换 Qt 的收益主要是“看起来新一点”，不足以抵消 +80 MB 与第二套栈。

### 5.3 应用分级：值得换 / 可局部增强 / 不值得换

| 分级 | 窗口 | 行数 | 建议 |
|---|---|---:|---|
| **值得换（若启动 B 档）** | `cmderw` | 7,886 | Qt 首选；P0 只做列表+缩略图 |
| | `paramcadw` | 457 | QGraphicsView 抗锯齿截面预览；小窗口、收益直观 |
| | `parameshw` | 445 | 线框预览可用 QPainter；若需 60 fps 再评估 OpenGL |
| | `pptmakew` | 1,150 | 预览 Canvas + 表单；Qt 收益中等 |
| | `pevieww` | 476 | 十六进制/结构表用 QTableView 更整齐 |
| **可局部增强（留在 Tk）** | `reshackw` | 828 | 资源树/表；Tk 够用，优先主题/图标 |
| | `filescanw` | 1,344 | 结果表；Tk 够用 |
| | `renamerw` | 914 | 预览表；Tk 够用 |
| | `filesyncw` | 621 | 表单/结果；Tk 够用 |
| | `dynacalcw` | 499 | 网格；Tk 够用 |
| | `wslmanw` | 1,526 | 列表/表单；**正在并行开发，本轮不动** |
| **不值得换** | `llmchatw` / `llmconvertw` / `llmdownw` / `llmenginew` / `llmenvw` / `llmqntw` / `llmservew` | 5,069 | 表单+日志；观感收益低 |
| | `pdfsplitw` / `pdfcryptw` / `img2pdfw` | 1,314 | 表单+文件选择；Tk 足够 |
| | `envw` / `docdiffw` / `regexvalidatew` / `videoconvw` / `ncmconvertw` / `mirrortoolw` / `img2icow` | 4,659 | 表单/日志/简单预览；Tk 足够 |

> 计数口径：只统计 `*w.py`（排除 `peview.py`、`_preview.py`、`_view.py`、`window.py`）与 `cmder/app.py`；另有 `checksum` / `alarmclock` 两个小工具窗口（742 行）同样留在 Tk，不在这 28 个窗口内。分级依据是“视觉密度 + 交互复杂度 + 使用频率”，不是代码行数本身。

### 5.4 明确不推荐

| 方案 | 不推荐理由 |
|---|---|
| **全仓换 PySide6** | +80~119 MB、Win10+、Linux 打包复杂、28 个窗口重写、两套栈长期维护；绝大多数表单窗口收益接近于零 |
| **wxPython** | 观感不会明显变好；Linux 无 wheel，破坏现有 Linux 便携 zip；Win7 无承诺 |
| **Dear PyGui** | 文本/IME/无障碍不适合文件管理与表单；只适合实时图形视口 |
| **Flet** | 桌面客户端 38.4 MB 且 1.1.2 运行时下载；双进程；Win10+；离线安装包风险高 |
| **Toga** | 0.5.x 未成熟；Windows 依赖 .NET/WinForms，Linux 依赖 GTK；无通用 Canvas |
| **pywebview 作为主 UI** | 双栈 + WebView2 依赖 + Win7 旧运行时；只适合单窗口预览实验 |
| **PyQt6** | GPL/商业许可，不适合 MIT 项目分发 |
| **自研 C++ 绑定** | ADR 0013 已论证：等于自建 GUI 工具包，长期成本远超收益 |

### 5.5 什么条件下改选别的

| 条件 | 改选 |
|---|---|
| “现代观感”成为硬指标，接受 Win10+ 与 +80 MB | **PySide6**（cmderw 先行，其他窗口按 §5.3 分级） |
| 体积是硬指标，观感可接受现状 | **Tk + 依赖裁剪/分包**（§7），不换 GUI |
| Win7 仍是硬要求 | **只能 Tk**；Qt/Flet 出局；wx 需实测且 Linux 仍有 wheel 问题 |
| 只需 1–2 个预览窗口更强，且目标机有 WebView2 | **pywebview 单窗口实验**（不是主 UI） |
| 需要 60 fps 3D 视口且可接受独立窗口 | **Dear PyGui 单窗口实验**（不是主 UI） |
| 团队愿意维护 JS + Python 两套栈，且改为在线安装器 | **Flet**（当前分发模型不匹配） |

---

## 6. 迁移路径与工作量分级

### 6.1 分级定义

| 级别 | 判定 | 典型改动 |
|---|---|---|
| **S** | < 500 行，1–3 个文件，不改业务逻辑 | 主题/间距/图标/对话框规范 |
| **M** | 1 个窗口，300–800 行视图代码 | CTk 试点或单个 Qt 预览窗口 |
| **L** | 1 个旗舰窗口的核心视图，1,500–3,000 行 | cmderw Qt P0（列表 + 缩略图） |
| **XL** | 7,000+ 行或跨 10+ 窗口 | 全量 cmderw Qt / 全仓换栈 |

### 6.2 推荐路线（按阶段，不按工期）

| 阶段 | 内容 | 级别 | 验收 |
|---|---|---|---|
| **P0 口径确认** | 确认 G1–G5 哪些是硬约束；确认 Win7 是否仍是发布要求；确认是否接受第二套栈 | — | 用户拍板（§8） |
| **P1 视觉规范（默认路线）** | `theme.py` 扁平化、间距栅格、图标、对话框规范；补截图验收清单 | S | 28 个窗口抽检；无业务行为变化；安装包体积不变 |
| **P2 体积治理** | 依赖归属审计；PyMuPDF/onnxruntime/numpy 分包或按需下载；保留 Tk | M | 安装包实测下降；PDF/LLM 工具功能不回退 |
| **P3 cmderw Qt P0（仅当 B 档触发）** | QTableView/QListView + 模型适配；复用 `_ops`/扫描；`--ui=qt` 旁路 | L | ADR 0013 P0 门槛 + Win10/11 冒烟 |
| **P4 预览窗口评估（可选）** | `paramcadw`/`parameshw`/`pptmakew`/`pevieww` 逐个评估，不批量迁移 | M/个 | 每个窗口单独截图/交互验收 |
| **P5 决策** | 若 P3/P4 通过且维护成本可接受，再讨论默认入口；否则保留 Tk 默认 | — | 更新 ADR 0013 落地状态 |

### 6.3 迁移护栏

1. **纯逻辑不动**：`_ops.py`、`_preview.py`、`list_directory`、缩略图解码保持 toolkit 无关；
2. **按应用/按入口切换**：每个 `*w` 是独立 exe，Tk 与 Qt 可在不同进程共存；**不要在同一进程混跑两个主循环**；
3. **打包变体**：fspack 的 extras 是全局的，一个应用用 Qt 会让所有安装包变大；要么接受，要么出“Tk 版/完整版”两个安装包（发布矩阵翻倍）；
4. **Win7 闸门**：若 Win7 仍是硬要求，Qt 路线不启动；wx 需先做真机验证；
5. **测试**：Tk GUI 目前不在 CI 覆盖率内；Qt 视图可用 `QT_QPA_PLATFORM=offscreen` 做冒烟，但需要新增 CI job/依赖，不能假设“换了 Qt 就有自动化保障”；
6. **视觉验收**：GUI 没有像素级回归，必须准备固定窗口尺寸/主题/缩放的截图清单，人工对照。

---

## 7. 体积治理（与 UI 解耦，优先级高于换栈）

### 7.1 可核查的账

| 项 | 未压缩 | 备注 |
|---|---:|---|
| pymupdf | 97.8 MB | PDF 工具真实需要；2.1 版会硬拉 onnxruntime，1.28.2 为零依赖（48.1 MB） |
| onnxruntime | 45.8 MB | 源码 0 处引用；PyMuPDF 2.1 的 `Requires-Dist`，恢复 `pymupdf<2` 后消失 |
| numpy + numpy.libs | 35.0 MB | 随 PyMuPDF 2.x / onnxruntime / networkx 进入；源码 0 处静态引用 |
| PIL | 15.6 MB | 多工具需要，建议保留 |
| pygments | 8.5 MB | 需确认消费者 |
| networkx | 8.3 MB | 随 PyMuPDF 2.x 进入；恢复 `pymupdf<2` 后消失 |
| **小计** | **≈ 211 MB** | 占 site-packages 75% |

### 7.2 建议动作

1. **依赖归属审计（已完成）**：onnxruntime 是 PyMuPDF 2.1 的硬依赖，numpy/networkx 同链带入；详见[依赖审计](2026-11-14-fcmd-dependency-audit-pymupdf-onnxruntime.md)；
2. **恢复 `pymupdf<2`（P0）**：改一行 `pdf` extra 约束即可移除 onnxruntime/numpy/networkx/pymupdf4llm 等，实测少 **143.3 MB raw / 72.3 MB deflate**，安装包 **149.1 → 约 76.8 MB**；
3. **拆分安装包**：`slim`（CLI + Tk + 常用工具）与 `full`（PDF/LLM/Office）两个变体，或首启动按需下载；
4. **PDF 依赖后置**：`pymupdf` 只在 PDF 工具首次使用时提示安装/下载；
5. **不要动 Tk**：Tk 只占 2.2%，去掉它既解决不了体积，又会破坏现有 28 个窗口；
6. **量化验收**：以当前 149.1 MB 为基线，记录每个变体的 raw/安装包体积、启动时间、功能冒烟结果。

> 这一节是“体积”问题的正确解法；换 GUI 框架与它方向相反。是否执行需要用户单独立项（涉及发布矩阵与依赖策略，超出本文范围）。

---

## 8. 决策点（需要拍板）

1. **Win7 是否仍是硬要求？** 是 → 排除 Qt/Flet，Tk 或 wx（需实测）；否 → Qt 才进入候选。
2. **“现代观感”是否是验收硬指标？** 是 → 需要接受 +80 MB 与第二套栈；否 → A 档视觉规范即可。
3. **安装包体积上限是多少？** 当前 149.1 MB；若上限 < 200 MB，Qt Essentials（约 229 MB）已超过；若上限 < 170 MB，Qt 直接出局。
4. **是否接受两套 UI 规范长期维护？** 接受 → 可以启动 cmderw Qt P0；不接受 → 只做 A 档。
5. **是否接受发布矩阵翻倍（Tk 版 + Qt 版）？** 不接受 → 一个应用用 Qt 会让所有用户多付 80 MB。
6. **体积治理是否单独立项？** 若目标是“更小”，应先做 §7，而不是换 UI。

---

## 附录 A：复测命令

```powershell
# 1) 当前发布物体积
Get-ChildItem dist\release -File | Select-Object Name,Length
# 2) 从清单聚合 site-packages 体积
$m = 'dist\release\fcmd-0.3.12-py3.14.6-windows-manifest.json'
$j = Get-Content $m -Raw | ConvertFrom-Json
$j.entries | Where-Object { $_.path -like 'site-packages/*' } |
  Group-Object { ($_.path -split '/')[1] } |
  ForEach-Object { [pscustomobject]@{
      Name = $_.Name
      MB = [math]::Round((($_.Group | Measure-Object size -Sum).Sum)/1MB, 1)
  } } | Sort-Object MB -Descending | Select-Object -First 20
# 3) 安装候选框架到临时目录（不污染仓库）
$t = Join-Path $env:TEMP 'fcmd-ui-bench'
uv pip install --target "$t\qt_ess" PySide6-Essentials
uv pip install --target "$t\wx" wxPython
uv pip install --target "$t\ctk" customtkinter ttkbootstrap
# 4) 统计 raw 与 deflate
Add-Type -AssemblyName System.IO.Compression.FileSystem
$s = (Get-ChildItem "$t\qt_ess" -Recurse -File | Measure-Object Length -Sum).Sum
"raw = {0:N1} MB" -f ($s/1MB)
[System.IO.Compression.ZipFile]::CreateFromDirectory(
  "$t\qt_ess", "$t\qt_ess.zip",
  [System.IO.Compression.CompressionLevel]::Optimal, $false)
"zip = {0:N1} MB" -f ((Get-Item "$t\qt_ess.zip").Length/1MB)
# 5) 启动测量：subprocess 5 次取中位数（Python 脚本见下）
```

启动基准脚本（放到临时目录，不提交）：

```python
import os, subprocess, statistics, sys, time
T = os.path.join(os.environ["TEMP"], "fcmd-ui-bench")
def run(label, code, target=None, n=5):
    env = dict(os.environ); env.pop("PYTHONPATH", None)
    if target:
        env["PYTHONPATH"] = os.path.join(T, target)
    ts = []
    for _ in range(n):
        s = time.perf_counter()
        subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=120)
        ts.append((time.perf_counter() - s) * 1000)
    print(f"{label:<28} median {statistics.median(ts):7.0f} ms")
run("baseline", "pass")
run("tkinter import", "import tkinter")
run("tk window", "import tkinter as tk; r=tk.Tk(); r.withdraw(); r.update_idletasks(); r.destroy()")
run("Qt import", "from PySide6 import QtWidgets", "qt_ess")
run("Qt window", "from PySide6 import QtWidgets; a=QtWidgets.QApplication([]); w=QtWidgets.QWidget(); w.show(); a.processEvents(); w.close(); a.quit()", "qt_ess")
```

## 附录 B：与 ADR 0013 的关系与需要修正的事实

- ADR 0013 的结论是“不自研 C++ 绑定，先榨干 Tk；若触发换栈优先 PySide6”。本文**不推翻**该结论，补充了 2026-11-14 的体积/启动实测与更多候选。
- 需要修正/补充的点：
  1. **wxPython 3.14 可用性**：ADR 写“3.14 可能需自行编译”；实测 PyPI 已有 `cp313/cp314/cp315` Windows/macOS wheel，但 **Linux 仍只有 sdist**，所以“Linux 发布不可行”这一点仍然成立，理由应从“没有 3.14 wheel”改成“没有 Linux wheel”。
  2. **体积数字**：ADR 的“PySide6-Essentials 约 70–100 MB 下载、安装后约 200 MB”与实测一致（78.3 MB wheel / 212.8 MB raw）；本文补充了 deflate 80.1 MB 与完整 Qt 118.8 MB 的安装包增量。
  3. **新增候选**：ttkbootstrap、CustomTkinter、Flet、Toga、pywebview 在 ADR 中未逐项测量；本文给出可核查数字。
  4. **触发条件可量化**：ADR 0013 的触发条件 5（接受 +200 MB、便携包 250–300 MiB）与本文实测一致；Qt Essentials 路线约 229 MB 安装包，仍在 300 MiB 内。
- 本文**不修改** ADR 0013；若后续启动 B 档，应在其“落地状态”中追加实际结果。

## 附录 C：本轮未纳入/未验证的事项

- 未在真实 Win7 机器上验证 wxPython/Toga/pywebview 的可运行性；G2 的判定基于官方支持口径与依赖形态。
- 未在 Linux 上实测 Qt 的 xcb 依赖缺失场景；G3 的判定基于 Qt 6 Linux 部署的已知系统库要求。
- 未实测 Flet 桌面客户端的首次下载/启动耗时；1.1.2 的运行时下载行为已通过 wheel 内容与源码注释确认。
- 未实测 Qt 的 `pytest-qt`/offscreen 冒烟在 fspack 打包环境中的可行性；若启动 B 档，需要单独 spike。
- 未把“视觉规范”落到具体截图；A 档执行时需要固定主题/缩放/窗口尺寸的验收清单。
- 未改动任何业务代码、配置、依赖或发布流程；`dist/`、`pyproject.toml`、`.github/workflows/` 均未触碰。
