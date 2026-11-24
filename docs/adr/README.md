# 架构决策记录（ADR）

长期有效的架构决策在此留痕；编号不复用，被替代或作废时直接删除文件与索引行（追溯看 git 历史）。

> 命名说明：0026–0034 涉及的应用原名为 `locllm` / `locllmw`，现包路径统一为 `apps/llm`、
> 命令名为 `llmw`；旧命令 `locllmw` 已移除，窗口设置 `ui/locllmw.json` 会在首次访问时
> 一次性搬迁为 `ui/llmw.json`（ADR 文件名已统一为 `llm`，仅保留历史编号）。

| 编号 | 主题 | 状态 |
|---|---|---|
| [0001](0001-layering.md) | 分层与依赖方向 | 已落地（P1 / P2） |
| [0003](0003-package-layout.md) | 包内目录结构与布局不变量 | 已落地（B0–B7） |
| [0004](0004-refactor-backlog.md) | 重构待办的触发条件 | 生效（M3/M6 待决策，M7 暂缓） |
| [0005](0005-cmderw-totalcommander-alignment.md) | cmderw 对齐 Total Commander 的能力分期 | 生效（A–K/M） |
| [0006](0006-tool-list-fast-path.md) | 工具列表快路径（静态元数据 + 内容指纹回退） | 已落地 |
| [0007](0007-cmderw-archive-browsing.md) | cmderw 压缩包内浏览与写入 | 生效（J 批 / P3-1） |
| [0008](0008-cmderw-command-line.md) | cmderw 命令行历史与补全 | 生效（K 批） |
| [0009](0009-cmderw-multi-rename.md) | cmderw 多重命名工具（掩码 / 查找替换 / 预设） | 生效（M 批） |
| [0010](0010-cmderw-user-commands.md) | cmderw 用户命令与外部工具入口 | 生效（N1–N2） |
| [0011](0011-cmderw-p2-enhancements.md) | cmderw 搜索过滤、卷栏、属性、预览与队列调度 | 生效（N3–N7） |
| [0012](0012-cmderw-p3-file-tools.md) | cmderw P3 文件工具：归档写入、分卷、重复文件、包内搜索与标签交互 | 生效（P3） |
| [0013](0013-gui-toolkit-feasibility.md) | GUI 技术路线：不自研 C++ 绑定（研究结论，落地选择见 0033 / 0034） | 生效（研究结论；Tk 实现已按 0034 全部移除） |
| [0014](0014-batch-renamer.md) | 批量重命名：规则流水线 + 正则重排 / 搬移 | 已落地（v1） |
| [0015](0015-llm-serve-tuning.md) | llmserve 启动参数调优策略（基准实测） | 已落地 |
| [0016](0016-llmdown-model-browser.md) | llmdown 模型浏览器：ModelScope 搜索 / 详情 / 下载管理 | 已落地（v1） |
| [0017](0017-pptmake.md) | pptmake：PPT 模板设计器（布局 / 风格 / PPTX 导入导出） | 已落地（v1） |
| [0018](0018-peview.md) | peview：PE 文件查看与安全编辑 | 设计中（分阶段落地） |
| [0019](0019-filescan.md) | filescan：目录敏感内容扫描（规则表达式 / 后缀白名单 / 并行扫描 / 打码报告 / 性能基准） | 已实施 |
| [0020](0020-dynacalc.md) | dynacalc：装药密度—尺寸—工艺缺陷→爆轰传递参数化算例（矩阵 / 模型映射 / deck 生成 / 调度 / 汇总 / 门控） | 已实施 |
| [0021](0021-paramcad.md) | paramcad：参数化建模与几何预览（几何两态推导 / 分布场 / 缺陷参数 / Qt 预览 / 模型导出） | 最小原型已落地 |
| [0022](0022-paramesh.md) | paramesh：参数化模型到仿真网格（尺度策略 / 区域与边界 / 质量指标 / 网格包交接契约 / Qt 预览） | 最小原型已落地 |
| [0023](0023-wslman.md) | wslman：Windows 上的 WSL 发行版管理（注册表 / `.wslconfig` 解析 / 导出导入 / 下载源降级 / 终端启动 / 双读输出） | P1 已落地（只读 CLI） |
| [0025](0025-sidebar-page-navigation.md) | GUI 侧边栏分页导航（`SidebarNav`）：locllmw 等整窗分页（原环境、引擎、转换 / 量化窗口已并入 locllmw，见 0031） | 已落地 |
| [0026](0026-llm-app-migration.md) | locllm：本地 LLM 工具族从 `tools/llm` 迁到 `apps/llm`，命令名不变 | 已落地 |
| [0027](0027-llmw-rename.md) | locllmw：`llmservew` 改名与五页侧边栏（概览 / 模型 / 运行 / 对话 / 设置） | 已落地（实验室占位页已移除） |
| [0028](0028-llmw-overview.md) | locllmw 概览：硬件检测 / 模型盘 / 智能推荐（预测与实测口径） | 已落地 |
| [0029](0029-llmw-chat-page.md) | locllmw 对话页：整合 llmchatw 的多轮流式对话，移除独立窗口工具 | 已落地 |
| [0030](0030-llmw-models-page.md) | locllmw 模型页：整合 llmdownw 的浏览与行内下载 / 转换 / 量化，移除独立窗口工具 | 已落地 |
| [0031](0031-llm-config-unification.md) | localllm 配置单一来源：模型 / 工作 / 引擎 / 服务目录统一由 `_llmconfig` 解析；模型目录支持多目录列表与主目录（见 0043）；设置页卡片式单页，存储 / 引擎独立卡片（引擎位于存储下方）并承接原引擎管理窗口 | 已落地 |
| [0032](0032-llmw-startup-latency.md) | locllmw 启动加载速度：非首屏页延迟构建 + GGUF 精度快路径与缓存 + 配置读取缓存 | 已落地 |
| [0033](0033-qt-gui-migration.md) | locllmw 迁移到 Qt：GUI 基座（主题 / 导航 / 窗口 / 控件 / 后台任务）、五页与模型下载 / 转换 / 量化向导重构；Qt 绑定选择见 0036 | 生效（已全部落地，Tk 实现已删除） |
| [0034](0034-qt-full-gui-migration.md) | 全量 GUI 迁移到 Qt：共享层（应用入口 / 单页窗口 / 主题菜单 / 布局纯函数）、布局与高 DPI 规范、B0–B7 分批 | 已落地（全部窗口 Qt 化，Tk 代码已删除） |
| [0035](0035-cmder-filesync-compare-unify.md) | 目录比较 / 同步引擎统一：cmderw 与 filesync 共用 filesync `_core`，分 P0–P3 独立实施 | 设计中（独立阶段，未实施） |
| [0036](0036-python310-pyside2.md) | Python 3.10 支持与 PySide2 兼容：按版本选择 Qt 绑定、运行时兼容层与 CI/tox 矩阵 | 已落地 |
| [0037](0037-unified-result-envelope.md) | 统一结果信封（`--json`）：框架用法错误机读化、MCP 白名单对齐、只读工具分期接入 | P0 / P1 已落地（P2 / P3 待办） |
| [0038](0038-shared-qt-widget-boundaries.md) | Qt 共享控件的抽象边界：可展开行卡骨架 `ItemRow`、不做 TaskDialog，ChoiceCard / StatusTag / LogDialog / ProgressRow 的适用范围 | 生效（边界已确定） |
| [0040](0040-llmw-paint-in-chat.md) | llmw 画图并入对话：取消独立画图页，对话页「画图」开关 + 画图配置合并进对话配置窗口，画图引擎安装 / 状态移到运行页 | 已落地 |
| [0041](0041-cli-progress-output.md) | CLI 执行过程输出用户化：内部任务名不外泄、命令回显改 `$ …`、单行步骤结果 + 一行收尾，移除任务汇总表 | 已落地 |
| [0042](0042-shared-gui-shell-and-theme.md) | 通用 GUI 组件与主题整合：窗口外壳 mixin、页面基类 `AppPage` / `PageSpec`、主题 `Metrics` 与按窗口覆盖 | 已落地 |
| [0043](0043-llmw-multi-model-dirs.md) | llmw 多模型目录：`models_dirs` 列表 + 首个主目录，设置页参考 Steam 并排展示盘符 / 文件系统 / 剩余空间的磁盘卡片 | 已落地 |
| [0044](0044-llmw-workflow.md) | llmw 工作流：类型化节点 + DAG 执行 + 模板化工作流页（C1 / C2 落地，C3 画布 / C4 ComfyUI 导入待办） | C1 / C2 已落地 |
| [0045](0045-fx-tool-execution-primitives.md) | `fx.tool` 执行原语：前置断言 `preconditions`、有序步骤 `steps`、显式聚合 `aggregate`；`gittool` 的 `a` / `i` 改为步骤声明，内部开关不再泄漏到 CLI | 已落地 |
| [0046](0046-large-qt-app-split.md) | 大型 Qt 应用拆分：wslman / llmw 页面化与窗口瘦身；`apps/<app>/qt/` 布局例外、状态归属、P0–P5 分期与行数守卫 | 已落地（wslman + llmw 模型/运行页拆分；chat/paint 按触达再迁） |
| [0047](0047-confirm-gate-and-filetime.md) | 破坏性写操作的确认闸门（`requires_confirmation` + `--confirm`）与 `filetime` 工具（mtime/atime 读/写）；schema 增加 `json` / `requires_confirmation` 标记 | 已落地 |
| [0048](0048-config-format-convert.md) | 配置格式跨转换：`tomltool convert`（TOML → JSON/YAML）与 `yamtool convert`（YAML → JSON/TOML），自实现 `to_toml` 写方向序列化 | 已落地 |
