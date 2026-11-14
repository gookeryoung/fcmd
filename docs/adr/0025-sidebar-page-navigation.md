# 0025 GUI 侧边栏分页导航（`SidebarNav`）

- 状态：已落地（locllmw 等整窗分页；原环境窗口、引擎窗口与转换 / 量化窗口已并入 locllmw，见 0031）
- 日期：2026-11-14
- 关联：`src/fcmd/gui/sidebar.py`、`src/fcmd/gui/theme.py`、[ADR 0013](0013-gui-toolkit-feasibility.md)、[ADR 0016](0016-llmdown-model-browser.md)

## 背景

locllmw 原先与其他整窗工具一样，用顶部
`ttk.Notebook` 承载「配置 / 监控 / 日志」这类页面：

1. 标签条与内容抢横向空间：页数到 3–4 个、或标题有中文长词时，标签被截断，
   内容区左侧被挤压。
2. 选中态弱：只在标签底部一条浅色描边，深色主题下不易一眼看出当前页。
3. 每个窗口各自写 `Notebook` + `Frame` + `add/select`，页面 key 用控件对象
   传递（`select(self._roots_page)`），跨页跳转与「切页触发加载」散落在各处。

要求：**以 locllmw 为目标改成侧边栏分页，并把实现收敛成通用 GUI 组件**，
其他同类窗口直接复用，不再各自拼 `Notebook`。

## 决策

### 1. `fcmd.gui.sidebar.SidebarNav` 作为唯一分页容器

- 结构：左侧固定宽度导航栏（可选标题 + 页按钮 + 当前页提示），右侧页面区
  同一时刻只 `grid` 一个页面；导航栏用 `grid_propagate(False)` 固定宽度。
- 页面用**字符串 key** 标识：`add(key, label, hint=...) -> 页面控件`，
  `select(target)` 接受 key / 索引 / 页面控件，`step(delta)` 循环切换，
  `keys()` / `selected()` 读取状态。
- `on_change(key)` 只在**真正切页**时回调；首个页面自动选中但不回调，
  避免构造期回调访问尚未建好的控件（与 `Notebook` 事件异步触发的效果对齐）。
- 快捷键 `enable_shortcuts()`：`Ctrl+Tab` / `Ctrl+Shift+Tab` /
  `Ctrl+PageDown` / `Ctrl+PageUp`。
- tkinter 仍在实例化时延迟导入，保持 CLI 快路径不加载图形库；纯逻辑
  （`resolve_page_key` / `next_page`）拆在模块级，CI 可单测。

### 2. 样式集中在主题层

`fcmd.gui.theme` 新增 5 个样式，全部主题共用：

| 样式 | 用途 |
|---|---|
| `Sidebar.TFrame` | 导航栏底色（`palette.surface`，与内容区 `background` 分层） |
| `SidebarTitle.TLabel` | 导航栏标题 |
| `SidebarHint.TLabel` | 当前页说明（`palette.muted`） |
| `Sidebar.TButton` | 未选中页（muted 前景 + hover/按下态） |
| `SidebarActive.TButton` | 选中页（`surface_active` + 强调色边框） |

`native` 主题的系统按钮不响应背景色，选中页改用强调色**文字**标记，与
`Accent.TButton` 的 native 处理一致。

### 3. 迁移范围

已迁移「整窗主页面」型窗口：locllmw（模型页内部再分浏览 / 已安装 / 下载 / 转换）。
原引擎管理窗口与独立环境窗口已并入 locllmw 设置页的「引擎」「目录」分页，
转换与量化窗口已并入 locllmw 模型页的「转换」分页，不再单独开窗。跨页跳转改为 `_nav.select("log")` 之类的 key 调用。主容器
`ttk.Frame(self.root)` **不加 padding**，让侧边栏与状态栏贴窗边；内容间距统一
由各页面自己的 `page_padding` 负责，避免外层再叠一层边距。

**不迁移**以下 `Notebook`：它们是同屏子视图而不是顶层页，或受窗口高度约束，
侧边栏会二次挤压内容：

- locllmw 模型页的详情 / 文件列表子页（右侧 `PanedWindow` 内）；
- wslmanw 详情/下载/日志（主网格中部，纵向空间优先）；
- peview、reshack、paramcad 的预览/字段子页；
- filescan、dynacalc 的多视图切换。

这些窗口需要时可按同一 API 迁移，不存在私有分支。

### 4. 测试口径

- `tests/gui/test_sidebar.py`：key / 索引解析、越界与非法类型、循环切换。
- `tests/gui/test_theme.py`：5 个新样式在四套主题下存在；
  选中页前景与底色对比度、muted 与 surface 对比度均 ≥ 4.5。
- Tk 控件部分标注 `pragma: no cover`（CI 无显示环境），本地可用
  `Tk()` 冒烟：建窗、切页、快捷键绑定、销毁。

## 落地状态与交接

- 已完成：组件、主题样式、5 个窗口迁移、单测与 ADR 口径。
- 视觉回归点：侧边栏宽度（默认 172，原环境窗口 210）按最长页名调整；
  长页名加宽而不是换行，`ttk.Button` 不支持多行文本。
- 下一步（未做）：若把 wslmanw 迁移过来，需要先决定导航栏放在主网格左侧
  还是顶部横向，并补一次 900×680 最小窗口的截图验收。
