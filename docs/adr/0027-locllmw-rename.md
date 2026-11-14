# 0027 locllmw：llmservew 改名与十页侧边栏信息架构

- 状态：已落地
- 日期：2026-11-14
- 相关文档：[ADR 0015](0015-llm-serve-tuning.md)、[ADR 0025](0025-sidebar-page-navigation.md)、[ADR 0026](0026-locllm-app-migration.md)

## 背景

`llmservew` 最初只做「服务启动参数 + 运行监控」两件事；随功能增长，它已经
变成本地 LLM 的统一入口：模型目录与显存适配、服务生命周期、指标与日志、
性能测试，以及后续要接的对话、代理、画图、视频、音频。原名字与三页
（启动配置 / 运行监控 / 性能测试）都不再匹配职责：

- 「serve」只覆盖运行服务，不覆盖模型选择与多模态场景；
- `llmservew` 与 CLI `llmserve` 强绑定，容易让人以为它只是服务的图形外壳；
- 页面到 10 个量级时，顶部标签条会挤压内容宽度，且新增场景无落点。

## 决策

### 1. 改名 `llmservew` → `locllmw`

- 模块：`src/fcmd/apps/locllm/llmservew.py` → `locllmw.py`；
- 内部拆分：`_llmservew_bench/monitor/ui.py` → `_locllmw_bench/monitor/ui.py`；
- 类：`ServeWindow` / `_ServeWindowUI` / `_ServeWindowBench` / `_ServeWindowMonitor`
  / `ServeSettings` → `LocllmWindow` / `_LocllmWindow*` / `LocllmSettings`；
- 命令：`fcmd llmservew` → `fcmd locllmw`（`pyproject` 入口、工具注册、静态清单同步）；
- CLI `llmserve` 不改名，二者职责分离：`llmserve` 负责脚本化的启动计划与进程
  生命周期，`locllmw` 负责本机窗口化的本地 LLM 工作台。

### 2. 十页侧边栏信息架构

侧边栏复用 [ADR 0025](0025-sidebar-page-navigation.md) 的 `SidebarNav`，页面与
现有能力的映射：

| 分页 | key | 内容 | 状态 |
|---|---|---|---|
| 概览 | `overview` | 硬件信息（含显存 / 内存占用条）、模型盘、智能推荐、已加载模型（见 [ADR 0028](0028-locllmw-overview.md)） | 已落地 |
| 模型 | `models` | GGUF 扫描、能力标签、显存适配与提示 | 已落地 |
| 运行 | `run` | 档位 / 上下文 / KV / batch / 线程等启动参数 | 已落地 |
| 对话 | `chat` | 服务选择、流式会话、思考过程、图片附件（整合原 llmchatw） | 已落地（见 [ADR 0029](0029-locllmw-chat-page.md)） |
| 代理 | `agent` | 工具调用、多步任务编排与轨迹 | 规划中（占位） |
| 画图 | `image` | 文生图 / 图生图的提示词与批量任务 | 规划中（占位） |
| 视频 | `video` | 视频生成 / 视频理解 | 规划中（占位 + `videoconvw` 指引） |
| 音频 | `audio` | 语音识别 / 合成 | 规划中（占位） |
| 监测 | `monitor` | `/metrics`、系统负载、GPU、日志；性能测试折叠分区 | 已落地 |
| 设置 | `settings` | 配置文件路径、窗口记忆、引擎 / 版本 / GPU 环境 | 已落地 |

约定：

- 操作栏与进度条放在「运行」页底部（预览计划 / 启动 / 停止 / 状态 / 日志 /
  浏览器 / 清空日志），不再全局常驻；「查看日志」与后台任务仍跳到 `monitor`，
  启动 / 停止入口统一在「运行」页。
- 规划中分页不伪造按钮：只说明定位，并列出当前可用的 CLI 命令；功能落地时
  直接替换占位内容，不改导航结构。
- 原「性能测试」独立页并入 `monitor`，用折叠分区保留原有场景选择、参数与
  结果表，避免监测页纵向过长。

### 3. 同步项

- `pyproject.toml` 入口、`fcmd.cli._manifest_data`（目标模块与描述）、README
  工具表、ADR 0015 / 0024 / 0025 / 0026 的引用统一为 `locllmw`；
- 测试继续由 `tests/apps/locllm/test_cli_llmserve.py` 覆盖（CLI `llmserve` 与
  窗口纯逻辑同文件），只更新模块与类名；
- 标题与 `run_window` 文案改为「本地 LLM 服务」。

## 验证

- 工具发现与清单：`locllmw` 条目指向 `fcmd.apps.locllm.locllmw`；`fcmd llmserve`
  行为不变。
- 窗口冒烟：真实 Tk 下创建窗口，导航 key 为
  `overview/models/run/chat/agent/image/video/audio/monitor/settings`，切页与关闭正常。
- 全量测试：`tests/apps/locllm` 与 manifest / GUI 用例通过。

## 交接

- 对话页落地时优先复用 `llmchat` 的 `ChatSession`，把流式增量经队列回传主线程；
  代理页需要先定义任务编排的数据结构与取消语义，再谈 UI。
- 画图 / 视频 / 音频页目前没有对应的本地推理后端，落地前不要放可点击的「开始」
  按钮，保持占位状态即可。
