# 0044 llmw 工作流：类型化节点 + DAG 执行 + 模板化工作流页

- 状态：C1 / C2 已落地（执行器、内置节点、持久化、工作流页、极简配置与智能推荐）；C3 画布 / C4 ComfyUI 导入待办
- 日期：2026-11-25
- 关联：[0027](0027-llmw-rename.md)（llmw 侧边栏）、[0040](0040-llmw-paint-in-chat.md)（画图服务与
  sd.cpp 配置）、[0038](0038-shared-qt-widget-boundaries.md)（共享控件边界）

## 背景

llmw 已有对话、模型管理、运行页（LLM / 画图服务启停）与画图入对话。用户希望把
「提示词 → LLM 润色 → 出图 → 保存」这类多步操作串成可复用工作流，并参考 ComfyUI
的节点式组织。ComfyUI 的整张画布、自定义 Python 节点与全量工作流兼容不适合直接
照搬：llmw 是本地单用户工具，服务与模型管理已经存在，缺的是**可编排、可复用、
可校验的执行层**。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 借鉴 ComfyUI 的「类型化端口 + DAG + 工作流 JSON」，不做 ComfyUI 复刻 | `_llmw_flow.py` 等纯逻辑模块 |
| D2 | 工作流数据结构独立于 Qt：`FlowGraph` / `FlowNode` / `FlowLink` / `NodeDefinition`；保存前做端口类型、必填输入、重复连线与环检测 | `_llmw_flow_model.py` |
| D3 | 节点只调用已有能力，不复制服务启动 / 模型扫描 / 下载：`text.input`、`llm.chat`、`image.txt2img`、`image.save`、`text.save` | `_llmw_flow_nodes.py` |
| D4 | 执行器按拓扑顺序执行，节点级缓存按「类型 + 参数 + 上游输出」哈希；取消在节点边界检查；事件只暴露 `NodeEvent`，不绑定 Qt | `_llmw_flow_executor.py` |
| D5 | 工作流持久化到 `${FCMD_HOME}/flows/<id>.json`，带 `schema_version`；重命名不产生孤儿文件 | `_llmw_flow_store.py` |
| D6 | 内置模板：文生图、提示词润色 → 出图、LLM 问答 → 保存；首次打开工作流页时落盘 | `_llmw_flow_templates.py` |
| D7 | 侧边栏新增「工作流」页；MVP 用「节点链」表单（顺序 / 上移 / 下移 / 删除 / 参数），**线性编辑、自动连线**；分支编辑留给 C3 画布 | `_llmw_qt_flow.py`、`_llmw_qt_window.py` |
| D8 | 执行走 `submit_daemon` + 公共 `LogChannelMixin`：进度写页面状态栏，明细写「帮助 → 任务日志…」；失败不弹模态框 | `_llmw_qt_flow.py` |
| D9 | 画图节点服务未就绪时按运行页「图像」配置自动启动（含自动配对组件）；LLM 节点仍要求对话服务已就绪；服务自动切换 / 独占显存留给后续阶段 | `_llmw_flow_nodes.py` |
| D10 | 工作流页配置入口极简：左侧列表与对话页同构，右侧只留「名称 + 任务属性」，场景由节点链自动判定；节点参数分「常用 / 更多参数」两层，首屏只展开会改变结果的一两项 | `_llmw_qt_flow.py` |
| D11 | 智能推荐复用概览页 LLM 评分（显存 / 上下文 / KV / 性能），叠加出图家族与任务属性评分，输出「LLM + 画图模型 + 节点参数」组合；「应用推荐」写回节点参数与运行页 `paint` 配置，运行时再按服务登记回填别名 / 地址 | `_llmw_flow_recommend.py`、`_llmw_qt_flow.py` |

### D2 模型与校验

- 端口类型：`TEXT` / `IMAGES` / `FILES`；连线必须同类型，必填输入必须被连接，
  同一输入端口不能重复连线，图必须无环。
- `FlowGraph.validate(registry)` 同时补齐节点参数的默认值；保存、执行、模板加载共用
  同一套校验，避免 UI 与执行器口径分叉。

### D3 节点边界

- `text.input`：无输入，输出 `TEXT`；
- `llm.chat`：输入 `prompt`，输出 `TEXT`；调用运行中的 OpenAI 兼容服务（默认
  `127.0.0.1:8080`），服务未就绪直接报错；
- `image.txt2img`：输入 `prompt` / 可选 `negative`，输出 `IMAGES`；调用 `sd-server`
  （默认 `127.0.0.1:1234`）。服务未就绪时默认按运行页「图像」配置自动启动
  （复用 `start_paint_service`，自动配对的 VAE / 文本编码器一起生效）；节点参数
  `auto_start` 可关闭该行为；
- `image.save` / `text.save`：把 `IMAGES` / `TEXT` 落盘，输出 `FILES`。
- 不开放任意 Python 自定义节点；需要新能力时在 `_llmw_flow_nodes.py` 注册内置节点。

### D4 / D8 执行与反馈

- 拓扑顺序执行；`cache` 为调用方持有的字典（页面跨多次运行复用）；
- 取消：执行器在节点边界检查 `threading.Event`；正在进行的网络请求按自身超时结束；
- 事件：`start` / `done` / `cached`，页面据此更新状态栏；执行完成把输出文件写入
  「运行结果」列表，双击打开文件、按钮打开输出目录；
- 整个过程不阻塞 GUI 线程；错误写入状态栏与任务日志。

### D7 工作流页与 C3 边界

MVP 的「节点链」是线性链：节点按顺序自动连线，添加节点时从末尾向前寻找第一个
能保持端口兼容的位置；不兼容时拒绝添加并提示。`FlowGraph` 本身已支持分支图，
C3 画布只需替换 UI 的连线编辑，不改模型 / 执行器 / 存储。

### D10 / D11 极简配置与智能推荐

- 场景（提示词润色出图 / 文生图 / LLM 问答 / 文本处理）与默认任务属性均由节点链
  推导，不新增需要用户维护的字段；用户切换属性只影响推荐，不写配置。
- 推荐分数 = LLM 适配分（沿用概览页口径）× 0.6 + 出图家族匹配分 × 0.4；只有一侧
  节点时按单侧计分。出图参数按「重家族 / 中家族 / 快家族 × 画质 / 均衡 / 速度」查表，
  避免 sd15 被推 1024、Flux 被推高 CFG。
- 应用推荐：LLM 节点写 `temperature` / `max_tokens` / `system`，若该模型已登记服务则
  一并回填别名 / 地址 / 端口；出图节点写尺寸 / 步数 / CFG；画图模型写入运行页
  `paint`（有扫描结果时用 `suggest_components` 配对组件并清空多余槽位）。
- 扩散模型扫描走后台线程，先用运行页当前模型兜底，扫描完成后再刷新推荐。

## 验证

- `tests/apps/llm/test_llmw_flow.py`：端口类型、缺输入、未知节点、环、序列化版本、
  执行 / 缓存 / 取消 / 失败包装、持久化、模板合法性；
- `tests/apps/llm/test_llmw_qt_smoke.py`：工作流页构建、模板落盘、节点插入位置；
- `tests/apps/llm/test_llmw_flow_recommend.py`：场景 / 属性推导、属性影响出图参数、
  无扩散模型时仍给参数、文本场景与缺 LLM 时返回空、显存不足模型不进候选；
- `make flow` 全绿。

## 交接

- C3：`QGraphicsScene` 画布 + 拖拽连线 + 子图 / 框选；直接读写 `FlowGraph.links`，
  保留当前 `rechain` 作为「整理成链」动作。
- C4：ComfyUI 工作流子集导入（`CheckpointLoader` / `CLIPTextEncode` / `KSampler` /
  `VAEDecode` / `SaveImage` → sd-server）；不支持节点显式报错，不静默改图。
- 服务编排：`image.txt2img` 已支持自动启动画图服务；`llm.chat` 的自动启动 /
  独占显存调度留给后续阶段，当前需先在运行页启动对话模型。
- 新增节点：实现 `NodeDefinition.run`，参数用 `ParamSpec` 描述；同时补模型/执行器
  与页面测试，不直接改 UI 特判。
- 推荐后续：当前一次展示最优组合并提示备选数量；若要展开备选列表或多目标优化
  （显存 / 时延 / 画质三方权衡），在 `_llmw_flow_recommend.py` 增加排序权重参数，
  页面只消费排序结果。
