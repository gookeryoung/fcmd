# 0027 locllmw：llmservew 改名与侧边栏信息架构

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

### 2. 七页侧边栏信息架构

侧边栏复用 [ADR 0025](0025-sidebar-page-navigation.md) 的 `SidebarNav`。多模态能力
尚未落地时合并为一个「实验室」入口，不再让空壳页各占一个主导航项：

| 分页 | key | 内容 | 状态 |
|---|---|---|---|
| 概览 | `overview` | 硬件信息（含显存 / 内存占用条）、模型目录只读摘要、智能推荐、已加载模型（见 [ADR 0028](0028-locllmw-overview.md)） | 已落地 |
| 模型 | `models` | 浏览 / 已安装 / 下载 / 转换与量化（整合原 llmdownw，见 [ADR 0030](0030-locllmw-models-page.md)） | 已落地 |
| 运行 | `run` | GGUF 模型选择、档位 / 上下文 / KV / batch / 线程等启动参数、分组操作栏、启动计划预览与页内进度 | 已落地 |
| 对话 | `chat` | 服务选择、流式会话、思考过程、图片附件（整合原 llmchatw，见 [ADR 0029](0029-locllmw-chat-page.md)） | 已落地 |
| 实验室 | `lab` | 代理 / 画图 / 视频 / 音频四项规划中的卡片式说明 | 规划中（合并占位） |
| 监测 | `monitor` | `/metrics`、系统负载、GPU、日志；性能测试折叠分区 | 已落地 |
| 设置 | `settings` | 存储（模型目录 / 引擎根目录 / 服务目录 / 配置文件）、引擎安装与日志、关于与诊断（见 [ADR 0031](0031-localllm-config-unification.md)） | 已落地 |

约定：

- 运行页操作栏分「服务」（预览计划 / 启动 / 停止选中 / 停止全部 / 刷新进程状态）
  与「查看」（打开浏览器 / 查看日志）两组，进度条在第二行右侧常驻可见；
  启动 / 停止不再强制切换到监测页，进度与结果就地显示。
- 启动计划在运行页内展示摘要与命令，同时写入监测页日志；清空日志只在监测页。
- 日志区统一为只读文本 + 「复制 / 清空」工具条（`_guiwindow.create_log(toolbar=True)`），
  分页级日志的标题写明对象（服务日志 / 引擎安装日志 / 转换输出日志 / 量化输出日志 /
  下载日志），不再清一色叫「输出日志」。
- 刷新类按钮必须写明对象：刷新模型列表 / 刷新进程状态 / 刷新仓库列表 /
  刷新引擎列表 / 刷新服务列表；硬件快照用「重新检测硬件」，引擎版本用「检测版本」。
- 规划中的能力不再各占一个分页：实验室页只说明定位并列出当前可用的 CLI 命令；
  功能落地后在该页替换为真实入口，导航结构不变。
- 原「性能测试」独立页并入 `monitor`，用折叠分区保留原有场景选择、参数与
  结果表；分页内容高于窗口时用纵向滚动兜底，不压缩关键控件。

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
  `overview/models/run/chat/lab/monitor/settings`，切页与关闭正常。
- 全量测试：`tests/apps/locllm` 与 manifest / GUI 用例通过。

## 交接

- 对话页落地时优先复用 `llmchat` 的 `ChatSession`，把流式增量经队列回传主线程；
  代理页需要先定义任务编排的数据结构与取消语义，再谈 UI。
- 实验室页是代理 / 画图 / 视频 / 音频的共同占位：某一项落地时先在该页做真实
  入口，等多项都可用后再评估是否拆回独立分页。
- 画图 / 视频 / 音频页目前没有对应的本地推理后端，落地前不要放可点击的「开始」
  按钮，保持占位状态即可。
