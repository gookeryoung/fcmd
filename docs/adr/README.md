# 架构决策记录（ADR）

长期有效的架构决策在此留痕；编号不复用，被替代或作废时直接删除文件与索引行（追溯看 git 历史）。

| 编号 | 主题 | 状态 |
|---|---|---|
| [0001](0001-layering.md) | 分层与依赖方向 | 已落地（P1） |
| [0002](0002-dsl-tools-boundary.md) | DSL 与 tools 的边界 | 已落地（P2-1…P2-4） |
| [0003](0003-package-layout.md) | 包内目录结构与布局不变量 | 已落地（B0–B4） |
| [0004](0004-refactor-backlog.md) | 重构待办的触发条件 | 生效（M4 / M7 暂缓） |
| [0005](0005-cmderw-totalcommander-alignment.md) | cmderw 对齐 Total Commander 的能力分期 | 生效（A–K/M） |
| [0006](0006-tool-list-fast-path.md) | 工具列表快路径（静态元数据 + 内容指纹回退） | 已落地 |
| [0007](0007-cmderw-archive-browsing.md) | cmderw 压缩包内只读浏览与按需解压 | 生效（J 批） |
| [0008](0008-cmderw-command-line.md) | cmderw 命令行历史与补全 | 生效（K 批） |
| [0009](0009-cmderw-multi-rename.md) | cmderw 多重命名工具（掩码 / 查找替换 / 预设） | 生效（M 批） |
| [0010](0010-cmderw-user-commands.md) | cmderw 用户命令与外部工具入口 | 生效（N1–N2） |
| [0011](0011-cmderw-p2-enhancements.md) | cmderw 搜索过滤、卷栏、属性、预览与队列调度 | 生效（N3–N7） |
