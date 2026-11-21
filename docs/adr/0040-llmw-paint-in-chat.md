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
| D3 | 画图配置拆成两段：采样 / 输出（:class:`PaintSampleSection`）嵌对话的「模型与生成配置」窗口；引擎 / 模型组件（:class:`PaintEngineSection`）放运行页「画图」页签 | `_llmw_qt_paint.py` |
| D4 | 运行页按用途分「对话 / 编程 / 画图」；画图页签只配启动参数与启停（启停样式对齐对话 / 编程的模型卡片） | `_llmw_qt_run.py` |
| D5 | 出图结果作为图片消息写进当前对话（历史记录保存路径，重开时按路径重绘） | `_llmw_qt_chat.py` |
| D6 | sd.cpp 引擎安装 / 设为默认移到设置页的「画图引擎（sd.cpp）」卡片，UI 与推理引擎（llama.cpp）卡片同构 | `_llmw_qt_paint.PaintEngineCard` + `_llmw_qt_settings.py` |
| D7 | 设置仍持久化在 `ui/llmw.json` 的 `paint` 字段，新增 `enabled` 表示对话页勾选状态 | `_llmw_winsettings.py` / `_llmw_qt_paint.py` |

### D2 / D5 交互

- 「画图」是 composer 工具行里的勾选框（默认关闭，状态持久化）；勾选后输入框
  占位提示切换为画图提示词，发送按钮走 `generate_images` 而不是 `ChatSession.ask`。
- 出图走线程池：成功后在对话正文追加「助手（图片）：」+ 图片（宽 320 自适应），
  历史块类型为 `image`，只存路径；文件被删时重绘为「[图片已不存在] 路径」。
- 失败（缺模型 / 缺组件 / 服务启动失败）写进对话正文 + 底部状态栏，不弹模态框，
  与文本生成的失败口径一致。

### D3 配置

- 画图设置按使用位置拆成两个分区控件，读写的仍是同一份 `paint` 字段：
  - `PaintSampleSection`（对话配置窗口）：尺寸 / 步数 / CFG / 采样器 / 调度器 /
    种子 / 批量 / 输出目录，即"这次怎么画、存到哪"；
  - `PaintEngineSection`（运行页「画图」页签）：服务端口 / 自动启动、模型与组件
    （加载方式 / 主模型 / VAE / CLIP / T5 / 文本编码器 / CPU 卸载 / VAE 分块 /
    Diffusion FA / 额外参数 / 命令预览 / 自动配对），即"用哪个模型、怎么加载"。
- 资产扫描在后台线程执行；未扫描完成前组合框沿用已保存路径，避免误清空；
  已保存主模型不在扫描结果里时补一条下拉项，任何一次信号触发都不会把 model 回落成空。
- 画图页签的说明统一写到底部状态栏（切到画图页签 / 扫描完成 / 缺模型时刷新），
  「选择模型」下方不再占一行小字。没有可用扩散主模型时为可执行提示：无候选时提示
  去「模型」页下载或在设置里登记目录；扫描到 ``*.part``（未下载完成）时列出文件名
  并提示继续下载——启动失败不再只报「请选择主模型」。
- 「自动配对」先补主模型（未选模型时取扫描到的第一个扩散模型），再按家族补
  VAE / 文本编码器；组件优先同家族，只有其它家族时才回退到该角色第一个候选——
  避免配到不兼容的 VAE / 文本编码器导致 sd-server 启动即退出。
- 两个分区各自发 `changed`，由所在页面延迟 400ms 合并写盘；读配置时先取
  `paint` 字段再覆盖本分区字段，互不覆盖。

### D4 引擎

- 运行页按用途分页：**对话 / 编程 / 画图**。
  - 对话、编程共用同一套 llama.cpp 启动参数表单（切页签时把共享表单挂到当前
    页签），差异是模型候选：编程只看命中「代码」能力标签的模型，对话排除纯代码
    模型，任一用途无候选时回退全部；切页签会先持久化当前模型参数，再按新用途
    自动选中候选模型（参数按模型分开保存）。
  - 画图页签与对话 / 编程同构：顶部「选择模型」卡片（扩散模型下拉，与 LLM 模型
    下拉同一套自绘弹层；「启动服务 / 停止服务」主按钮 + 「重启服务」，不再额外放
    服务状态胶囊——状态只看按钮文案），下方「启动参数」卡片
    （`PaintEngineSection`：加载方式、组件、CPU 卸载 / VAE 分块 / Diffusion FA、
    额外参数、命令预览、服务端口 / 自动启动）。启停按钮的文案 / 配色 / 禁用逻辑
    与 LLM 模型卡片一致（`RunPrimary` / `RunDanger` + `set_button_text`）。
- 引擎管理统一在设置页：新增「画图引擎（sd.cpp）」卡片，布局对齐推理引擎
  （llama.cpp）卡片——标题行右侧「刷新」、目录行「引擎目录 + 路径 + 打开 / 安装引擎…」、
  已安装引擎一律紧凑单行（版本号 + 可执行文件 + 「默认 / 设为默认」+ 后端胶囊；
  版本号取 release 资产名里的 ``master-...``，后端胶囊优先读 ``engine.json`` 的
  backend，缺失时从 tag 反推 ``cuda12`` / ``vulkan`` / ``cpu`` / ``rocm-x``）、
  列表高度随内容伸缩且超过 6 行才滚动、空态由提示行承接；安装对话框沿用
  `PaintEngineDialog`（版本 / 变体下载、本地 zip / 目录导入）。运行页不再放安装
  入口，只配置启动参数。
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
  配置弹窗里、运行页「对话 / 编程 / 画图」三页签（对话 / 编程共享表单随页签搬家、
  画图页签含选模型 + 启停 + 启动参数）、设置页含「画图引擎（sd.cpp）」卡片
  （模型组件控件只在运行页，采样 / 输出只在对话配置）；
- `make check-fast` / `make test` / `make cov`（覆盖率 ≥95%）全绿。

## 交接与后续

- 自动下载仅覆盖 Windows x64；Linux / macOS 走本地导入；
- sd.cpp 的 CLI 与 HTTP 字段仍可能变动：以「命令预览 + 额外参数 + 未知字段重试」兜底；
- 对话里的图片消息只存路径，不做缩略图缓存 / 图库；需要批量出图管理时再评估；
- 视频家族（Wan / LTX 等）仍未接入，留给后续「视频」页。
