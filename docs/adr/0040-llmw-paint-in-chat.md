# 0040 llmw 画图并入对话：取消独立页、引擎移入运行页

- 状态：已落地
- 日期：2026-11-24
- 关联：[0027](0027-llmw-rename.md)（llmw 六页侧边栏）、[0029](0029-llmw-chat-page.md)（对话页）、
  [0031](0031-llm-config-unification.md)（配置单一来源）、[0038](0038-shared-qt-widget-boundaries.md)（Qt 共享控件边界）

## 背景

画图首期是独立分页（原 ADR 0039）：页面自带 composer、结果预览、最近图库，
引擎安装 / 模型组件 / 采样参数 / 输出目录全在「⚙ 画图配置」弹窗里。实际使用中：

1. 画图与对话是同一类交互（输入提示词 → 得到结果），分页让用户在两处重复学习；
2. 画图页的参数 / 模型表单占满整页，绝大多数时间只是"输入提示词、出图"；
3. 引擎安装本质是"装一个本地推理后端"，与运行页的引擎 / 服务语境更近。

因此把画图从独立分页收回对话页，引擎相关入口移到运行页。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 取消独立画图页，侧边栏回到六页 | `_llmw_qt_window.py` |
| D2 | 对话页 composer 增加「画图」启用项：勾选后发送即按画图配置出图 | `_llmw_qt_chat.py` |
| D3 | 画图配置（服务 / 模型组件 / 采样 / 输出）作为分区嵌入对话的「模型与生成配置」窗口 | `_llmw_qt_paint.PaintConfigSection` |
| D4 | 运行页改为「服务 / 画图」分页；画图引擎（sd.cpp）状态、安装 / 设为默认与画图服务启停收进「画图」页签 | `_llmw_qt_run.py` + `_llmw_qt_paint_engine.py` |
| D5 | 出图结果作为图片消息写进当前对话（历史记录保存路径，重开时按路径重绘） | `_llmw_qt_chat.py` |
| D6 | 设置仍持久化在 `ui/llmw.json` 的 `paint` 字段，新增 `enabled` 表示对话页勾选状态 | `_llmw_winsettings.py` / `_llmw_qt_paint.py` |

### D2 / D5 交互

- 「画图」是 composer 工具行里的勾选框（默认关闭，状态持久化）；勾选后输入框
  占位提示切换为画图提示词，发送按钮走 `generate_images` 而不是 `ChatSession.ask`。
- 出图走线程池：成功后在对话正文追加「助手（图片）：」+ 图片（宽 320 自适应），
  历史块类型为 `image`，只存路径；文件被删时重绘为「[图片已不存在] 路径」。
- 失败（缺模型 / 缺组件 / 服务启动失败）写进对话正文 + 底部状态栏，不弹模态框，
  与文本生成的失败口径一致。

### D3 配置

- `PaintConfigSection` 是有状态的分区控件：服务端口 / 自动启动、模型与组件
  （加载方式 / 主模型 / VAE / CLIP / T5 / 文本编码器 / CPU 卸载 / VAE 分块 /
  Diffusion FA / 额外参数 / 命令预览 / 自动配对）、采样参数（尺寸 / 步数 / CFG /
  采样器 / 调度器 / 种子 / 批量）、输出目录。
- 资产扫描在后台线程执行；未扫描完成前组合框沿用已保存路径，避免误清空。
- 分区改动经 `changed` 信号交给对话页：延迟 400ms 写盘，避免逐字符 IO。

### D4 引擎

- 运行页按分页组织：「服务」页签放 llama.cpp 的模型选择 / 启动参数 / 计划预览，
  「画图」页签放 sd.cpp 的引擎与画图服务。
- 「画图」页签包含两张卡片：**画图引擎（sd.cpp）**（引擎 / 服务状态胶囊、刷新、
  安装引擎…、已安装引擎列表按 tag 列出并可直接「设为默认」）与**画图服务**
  （「启动服务 / 停止服务」，按对话配置里的模型 / 端口启动 sd-server）；安装对话框
  沿用 `PaintEngineDialog`（版本 / 变体下载、本地 zip / 目录导入）。
- 画图服务仍由 `_llmpaint_server` 管理（单实例、状态 `paint/server.json`、
  默认端口 1234），生成前按组件指纹自动启停（对话配置里的「自动启动」可关闭）。

## 落地

```
src/fcmd/apps/llm/
├─ _llmpaint.py                # 纯逻辑：资产识别、组件/请求、参数与结果处理
├─ _llmpaint_engine.py         # sd.cpp 引擎：release 解析、安装、切换、检查
├─ _llmpaint_server.py         # sd-server：计划、状态、健康检查、txt2img 客户端
├─ _llmw_qt_paint.py           # 纯逻辑 + generate_images / start_paint_service + PaintConfigSection
├─ _llmw_qt_paint_engine.py    # 引擎下载 / 导入对话框（运行页调用）
├─ _llmw_qt_chat.py            # 「画图」开关 + 图片消息 + 画图配置分区
├─ _llmw_qt_run.py             # 画图引擎状态 / 安装入口
└─ llmpaint.py                 # CLI：models / gen / serve / stop / status / engine-*
```

- `llmpaint` CLI 不受影响，仍是脚本化入口；
- 路径单一来源仍是 `_llmconfig.py`：`paint_dir()`（`paint_dir` → `FCMD_PAINT_DIR` →
  `${home}/paint`）与 `paint_engines_root()`（`FCMD_SD_ENGINES` → `sd_engines_root` →
  `${home}/engines/sd.cpp`）；
- 画图设置字段：引擎 tag、组件、采样参数、输出目录、自动启动、端口、`enabled`，
  由 `_llmw_winsettings.load_settings` / `save_settings` 逐字段校验。

## 验证

- `tests/apps/llm/test_cli_llmpaint.py`：资产识别、参数构建、引擎 release 解析与假 zip
  安装、状态读写、健康双探针、`txt2img` 成功 / 500 / 未知字段重试、CLI 各子命令；
- `tests/apps/llm/test_llmw_qt_logic.py`：画图模块级纯函数（设置解析、额外参数、
  组件 / 请求、命令预览、服务标签、进度 / 尺寸预设 / 配置摘要、扫描根与出图目录、
  变体说明）；
- `tests/apps/llm/test_llmw_qt_smoke.py`：六页导航（无画图页）、对话页「画图」开关
  切换占位提示与持久化、缺模型时发送只报错不派后台任务、画图配置控件挂在对话
  配置弹窗里、运行页「服务 / 画图」两页签且画图页签含引擎与画图服务卡片；
- `make check-fast` / `make test` / `make cov`（覆盖率 ≥95%）全绿。

## 交接与后续

- 自动下载仅覆盖 Windows x64；Linux / macOS 走本地导入；
- sd.cpp 的 CLI 与 HTTP 字段仍可能变动：以「命令预览 + 额外参数 + 未知字段重试」兜底；
- 对话里的图片消息只存路径，不做缩略图缓存 / 图库；需要批量出图管理时再评估；
- 视频家族（Wan / LTX 等）仍未接入，留给后续「视频」页。
