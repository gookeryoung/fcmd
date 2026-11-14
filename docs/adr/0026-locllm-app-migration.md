# 0026 locllm：本地 LLM 工具族迁移到 `apps/locllm`

- 状态：已落地
- 日期：2026-11-14
- 相关文档：[ADR 0001](0001-layering.md)、[ADR 0003](0003-package-layout.md)、[ADR 0015](0015-llm-serve-tuning.md)、[ADR 0016](0016-llmdown-model-browser.md)、[ADR 0025](0025-sidebar-page-navigation.md)

## 背景

`fcmd/tools/llm/` 到 v0.3.12 已含 17 个可注册工具加 5 个内部模块，彼此共享
`llmbase` 数据类型、`llmenv` 的路径与状态文件、`llmengine` 的引擎元数据与
`_llmtune` 策略；GUI 版还要复用同一套扫描 / 计划 / 监控实现。它已经不是
「一个领域里各管各的工具」，而是一族围绕同一台本机模型目录运行的应用：

- 跨工具调用频繁：`llmchat` / `llmserve` / `llmbench` / `llmdoctor` 都依赖
  `llmbase.Endpoint` 与 `llmenv` 的服务状态；
- 依赖边界一致：同一组可选依赖（`llama.cpp` 二进制、GGUF 解析）与同一套
  环境变量，没有按工具拆 extras 的需求；
- 工具命名已自带 `llm` 前缀（`llmchat`、`llmserve`…），域名 `llm` 只增加
  路径层级，不提供额外归属信息。

`fcmd.apps` 的定位是「一包一应用，包内多个公开模块即多个命令」，正好匹配
这种「应用族 + 共享内部模块」的形状。迁移后包名取 `locllm`（local LLM），
与将来可能出现的远程 / 云端 LLM 工具区分。

## 决策

### 1. 路径与测试镜像

| 旧 | 新 |
|---|---|
| `src/fcmd/tools/llm/` | `src/fcmd/apps/locllm/` |
| `tests/tools/llm/` | `tests/apps/locllm/` |
| `fcmd.tools.llm.*` | `fcmd.apps.locllm.*` |

内部模块继续用 `_` 前缀（`_llmtune` / `_llmgguf` / `_locllmw_*`），不参与
工具发现；公开模块即工具，模块名不变。

### 2. 命令名保持不变

`llmchat` / `llmserve` / `llmdown` 等**用户可见命令不改名**（`llmservew` → `locllmw` 的改名见 [ADR 0027](0027-locllmw-rename.md)），也不需要别名：
`fcmd.apps.locllm` 未声明 `__tool_name__`，发现逻辑按「包内公开模块名即工具名」
注册，迁移前后命令完全一致。改名只发生在 Python 导入路径。

### 3. 入口、清单与文档同步

- `pyproject.toml` 的 `[project.scripts]` 全部改为 `fcmd.apps.locllm.<模块>:main`；
- 重新生成 `fcmd.cli._manifest_data`：`ToolEntry.target` 与 `LAYOUT` 指纹改到
  `apps/locllm/*`，`fcmd <tool>` 快路径继续只加载目标模块；
- 测试、ADR 与 README 中的模块路径同步更新；工具名 / 描述不动。

### 4. 新增 LLM 能力的落点

新工具直接放 `src/fcmd/apps/locllm/<工具名>.py`；纯计算或可被非 LLM 工具复用的
部分放 `fcmd/infras/`，不要反向依赖 `apps.locllm`。`tools/<域>` 继续承载其余
领域的单工具实现，本迁移不外推到它们。

## 影响与验证

- 发现与清单：`fcmd tools` / `fcmd llmchat` / `fcmd list` 与迁移前一致；
  `tests/cli/test_tools_manifest.py` 校验布局指纹与内容指纹。
- 测试：`tests/apps/locllm` 12 个文件全部改为新导入路径，覆盖 CLI、GUI 纯逻辑、
  视觉与 GGUF / 调优策略；`tests/gui` 的侧边栏分页不受影响。
- 打包：`packages=["src/fcmd"]` 自动包含新包；`dist/` 下的旧发布物属于历史
  制品，不参与源码构建。
