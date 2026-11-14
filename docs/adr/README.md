# 架构决策记录（ADR）

长期有效的架构决策在此留痕；编号不复用，被替代或作废时直接删除文件与索引行（追溯看 git 历史）。

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
| [0013](0013-gui-toolkit-feasibility.md) | GUI 技术路线：不自研 C++ 绑定，优先优化 Tk 路径 | 生效（研究结论） |
| [0014](0014-batch-renamer.md) | 批量重命名：规则流水线 + 正则重排 / 搬移 | 已落地（v1） |
| [0015](0015-llm-serve-tuning.md) | llmserve 启动参数调优策略（基准实测） | 已落地 |
| [0016](0016-llmdown-model-browser.md) | llmdown 模型浏览器：ModelScope 搜索 / 详情 / 下载管理 | 已落地（v1） |
| [0017](0017-pptmake.md) | pptmake：PPT 模板设计器（布局 / 风格 / PPTX 导入导出） | 已落地（v1） |
| [0018](0018-peview.md) | peview：PE 文件查看与安全编辑 | 设计中（分阶段落地） |
| [0019](0019-filescan.md) | filescan：目录敏感内容扫描（规则表达式 / 后缀白名单 / 并行扫描 / 打码报告 / 性能基准） | 已实施 |
| [0020](0020-dynacalc.md) | dynacalc：装药密度—尺寸—工艺缺陷→爆轰传递参数化算例（矩阵 / 模型映射 / deck 生成 / 调度 / 汇总 / 门控） | 已实施 |
| [0021](0021-paramcad.md) | paramcad：参数化建模与几何预览（几何两态推导 / 分布场 / 缺陷参数 / Tk 预览 / 模型导出） | 最小原型已落地 |
| [0022](0022-paramesh.md) | paramesh：参数化模型到仿真网格（尺度策略 / 区域与边界 / 质量指标 / 网格包交接契约 / Tk 预览） | 最小原型已落地 |
| [0023](0023-wslman.md) | wslman：Windows 上的 WSL 发行版管理（注册表 / `.wslconfig` 解析 / 导出导入 / 下载源降级 / 终端启动 / 双读输出） | P1 已落地（只读 CLI） |
| [0024](0024-devman.md) | devman：Windows 开发环境管理（node/python 多版本安装、切换、卸载、冲突检测） | 已实施（P1 CLI 已落地，P2 GUI 待实施） |
| [0025](0025-sidebar-page-navigation.md) | GUI 侧边栏分页导航（`SidebarNav`）：llmservew / llmdownw / llmconvertw / llmenginew / llmenvw | 已落地 |
| [0026](0026-locllm-app-migration.md) | locllm：本地 LLM 工具族从 `tools/llm` 迁到 `apps/locllm`，命令名不变 | 已落地 |
