# 0032 locllmw 启动加载速度：延迟页面构建 + 精度解析快路径 + 配置读取缓存

- 状态：已落地
- 日期：2026-11-15
- 关联：`src/fcmd/apps/llm/llmw.py`、`_llmw_ui.py`、`_llmw_chat.py`、`_llmw_convert.py`、`_llmgguf.py`、`_llmconfig.py`、
  [ADR 0025](0025-sidebar-page-navigation.md)、[ADR 0030](0030-llmw-models-page.md)、[ADR 0031](0031-llm-config-unification.md)

## 背景

`fcmd locllmw` 打开窗口要等 0.6 s 以上，窗口出来后的头几秒还会卡顿。定位到三处
与「用户实际想看的内容」无关的开销：

1. 五个分页在构造期一次性建完，模型页 / 设置页各自在后台拉起扫描线程；
2. 模型页量化状态扫描对每个 GGUF 做**默认全量解析**（`tensors=True`），
   会逐项读出 tokenizer 词表（单个 30B 模型数十万项字符串）。实测 13 个模型的
   目录里一轮扫描约 450 万次 `bytes.decode` + 小读，持续数秒占用 GIL，与 Tk
   主线程抢时间片，表现为窗口刚出来时卡顿；
3. `_llmconfig.load_config` 每次都重新读盘并跑旧键迁移（还顺带读 `ui/llmw.json`），
   窗口构造同一轮里被调用 20 余次。

目标：不动功能口径，只把「看不见的页」与「可以跳过的解析」拿出冷启动关键路径。

## 决策

### 1. 非首屏页延迟构建

- `_LocllmWindowUI` 新增 `_defer_page(key, builder)` / `_ensure_page(key)`：页面
  按钮与滚动容器照旧在 `_build_widgets` 建好（保证导航栏与切页行为不变），只有
  **页面内容**改为首次切页时构建，`_ensure_page` 幂等。
- 延迟范围只覆盖首屏不可见、且构建会拉起扫描的「模型」「设置」两页；概览 /
  运行 / 对话 / 监测保持立即构建，`_log`、`_set_busy`、健康状态条等
  跨页共享控件因此不需要额外的缓冲或存在性判断。
- `_on_page_changed`（对话页 mixin）先 `_ensure_page(key)`，再执行原有的对话页
  刷新；`_ModelBrowserPage` / `SettingsPage` 的 import 也移到各自构建函数内，
  冷启动不再拉进 `llmdown` / `llmconvert` / 引擎扫描模块。
- 代价与边界：模型页 / 设置页首次切页时多付约 0.1–0.3 s 构建（原先是冷启动付）。
  `_persist_settings` 在模型页未构建时回退到 `_settings.download`，因此
  `load_settings` 必须读回 `download` 字段，否则「没打开过模型页就关窗」会丢下载设置。

### 2. GGUF 精度解析快路径

- 列表类场景（运行页模型列表、模型页量化状态）口径统一为 `detect_precision(path, tensors=False)`
  + `PrecisionCache`：读到 `general.file_type` 即停止，结果按「路径 + mtime_ns + size」
  缓存到 `~/.fcmd/cache/gguf-precision.json`，文件替换后自动失效。
- `_skip_value` 跳过的字符串改走 `_skip_string`（读长度后 `seek`），不再 decode /
  分配；张量表的名字同样只跳不读。**全量解析（`tensors=True`）只在需要
  `expected_bytes` 的地方保留**：量化前的输入校验（`quantize_input_error`），
  因为「文件疑似不完整」判断依赖预期大小。

### 3. 配置读取进程内缓存

- `_llmconfig.load_config` 按「路径 + mtime_ns + size」缓存解析结果，命中返回
  副本；`_write_json`（含迁移写回、`save_config` / `set_option`）让缓存整体失效。
- 只缓存读取结果，不改变优先级与迁移语义：迁移仍在首次读取时执行并写回，
  外部改文件后指纹变化会重新读取。

## 测试口径

- `tests/apps/llm/test_llmconfig.py`：缓存复用（同一轮只读一次 config.json）、
  返回副本、写操作失效、外部改写可见、缺失文件缓存为空。
- `tests/apps/llm/test_llmgguf.py`：既有元数据数组 / 张量表测试覆盖 `_skip_string`
  跳过路径与 `expected_bytes` 计算。
- Tk 部分（延迟构建、切页触发构建）标注 `pragma: no cover`，本地用真实窗口冒烟：
  建窗 → 逐页切换 → 刷新模型列表 → 关窗。

## 落地状态与交接

- 已完成：延迟页面构建 + 延迟导入、精度快路径与缓存、配置读取缓存、`load_settings`
  读回 `download`。
- 实测（本机 RTX 5090 D，`D:\models` 下 13 个 GGUF，最大 58 GB）：`import + 构造`
  从 0.61 s 降到 0.35 s；启动阶段字符串解码从约 450 万次降到数百次，且模型 / 设置
  页的扫描不再出现在冷启动。
- 下一步（未做）：`fcmd.console` 的 rich 导入（约 30 ms）仍是 GUI 冷启动的一部分，
  要动就得把 `Console` / `Table` 改成模块级 `__getattr__` 懒加载，属独立改动；
  设置页「重新检测 GPU」等长任务仍走后台线程，未改同步口径。
- 后续（已由 [ADR 0057](0057-llmw-wslmanw-startup-fast.md) 承接）：本文三条决策仍有效，
  0057 在此之上把平台探测移出 GUI 线程、模型扫描加缓存、入口导入瘦身并把镜像渲染推到
  首帧之后；rich 延迟导入仍未做。
