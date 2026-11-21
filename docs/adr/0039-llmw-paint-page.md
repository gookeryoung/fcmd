# 0039 llmw 画图页：托管 stable-diffusion.cpp 的本地文生图

- 状态：已落地（首期最小闭环）
- 日期：2026-11-21
- 关联：[0027](0027-llmw-rename.md)（llmw 六页侧边栏）、[0031](0031-llm-config-unification.md)（配置单一来源）、[0038](0038-shared-qt-widget-boundaries.md)（Qt 共享控件边界）

## 背景

llmw 的实验室页一直保留「画图」占位卡，但没有任何可用入口。本地 LLM 链路（llama.cpp /
`llama-server`）只能产出文本，扩散模型推理需要另一套运行时。首期要回答的问题：

1. 用什么后端跑本地扩散模型，才能既复用现有引擎下载 / 解压能力，又不改 llama.cpp 链路；
2. 画图能力只放在 Qt 里，还是同时提供可脚本化的 CLI；
3. 扩散模型的资产识别（checkpoint / diffusion / VAE / 文本编码器）与组件配对如何做到「够用且可兜底」；
4. 服务为单模型常驻，如何避免与 `llama-server` 的端口、状态文件、显存占用互相干扰。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 推理后端统一走 stable-diffusion.cpp 的 `sd-server`（A1111 兼容 `/sdapi/v1/txt2img`） | `_llmpaint_server.py` |
| D2 | 引擎装在 `${home}/engines/sd.cpp/<tag>`，复用 `llmengine` 的镜像下载与解压 | `_llmpaint_engine.py` |
| D3 | 同一套纯逻辑同时服务 Qt 页与 `llmpaint` CLI，页面只做编排 | `_llmpaint.py` / `llmpaint.py` |
| D4 | 资产按「父目录 + 文件名 token + 家族槽位表」识别与配对，允许手动改选与额外参数兜底 | `_llmpaint.py` |
| D5 | 服务单实例常驻：状态 `paint/server.json`、默认端口 `1234`、日志 `paint/logs/sd-server.log` | `_llmpaint_server.py` |
| D6 | 生成前按组件指纹自动启停；Qt 关窗只停「本窗口自动拉起」的服务 | `_llmw_qt_paint.py` |
| D7 | 画图从实验室占位卡提升为独立侧边栏页（对话与实验室之间） | `_llmw_qt_window.py` |

### D1 为什么选 sd.cpp

- 官方提供 Windows x64 预编译包（`cuda12` / `vulkan` / `cpu` / `rocm`），解压即用，不需要
  Python 侧新增推理依赖；
- `sd-server` 暴露的 `/sdapi/v1/*` 与 A1111 兼容，`txt2img` / `progress` / `interrupt` 都有
  对应端点，缺失时可按 best-effort 降级；
- 与 llama.cpp 同属 ggml 生态，参数风格（`-m` / `--diffusion-model` / `--vae` / `--t5xxl`）
  与现有 `llmengine` 引擎管理习惯一致。

Diffusers / ComfyUI 需要 Python 运行时与额外依赖，ComfyUI 的图工作流也不适合首期的
「提示词 → 出图」最小闭环，因此都不作为首期后端。

### D2 引擎与变体

- 资产正则只认 `sd-*-bin-win-(cuda\d+|vulkan|cpu|rocm-[\d.]+)-x64.zip` 与
  `cudart-sd-bin-win-cu\d+-x64.zip`，arm64 / 非 Windows 包直接过滤；
- 变体默认值按 GPU 推断：检测到 NVIDIA → `cuda12`（自动附带 cudart 运行库），否则 `vulkan`；
- Linux / macOS 首期不做自动下载，支持「本地 zip / 目录导入」；
- 下载不续传，失败从镜像列表重新开始（与既有 `llmengine` 行为一致）。

### D3 纯逻辑与 CLI 共享

`_llmpaint.py` 不依赖 Qt、网络与子进程，资产识别、参数构建、结果解码、保存与图库
都在这里；`llmpaint` CLI 与 Qt 画图页都只做组合。这样 CLI 可以脚本化与测试，页面层
只剩控件绑定，纯逻辑能被 `test_llmw_qt_logic.py` / `test_cli_llmpaint.py` 直接覆盖。

### D4 资产配对

- 角色：`checkpoint` / `diffusion` / `vae` / `clip_l` / `clip_g` / `t5xxl` / `llm` / `lora`；
  先看父目录（`vae`、`text_encoders`、`diffusion_models`、`checkpoints`、`loras`），再看
  文件名 token，`mmproj*` 一律跳过；
- 家族：`sd15` / `sd2` / `sdxl` / `sd3` / `flux` / `qwen-image` / `z-image` /
  `minimax-h3` / `hidream` / `hunyuan-image` / `kolors` / `unknown`，token 边界匹配，
  避免 `qwen2.5` 误判成 `qwen-image`；
- 家族识别按「文件名 → 最近两级父目录」依次尝试：HF / ModelScope 常见
  `<repo>/model.safetensors`、`<repo>/model-Q4_K_M.gguf` 这类通用文件名，家族信息只在
  仓库目录名里（`qwen-image-2.1` / `MiniMax-H3-Image`），不回看父目录会把主流模型
  误判为未知权重而直接从画图下拉里消失；
- 同一扩散仓库里的 Qwen / Mistral GGUF 识别为 `llm` 文本编码器，普通聊天仓库里的
  同名文件仍为 `unknown`，避免模型页把聊天模型误判成扩散权重；
- 家族只用于预选槽位与加载方式（checkpoint 用 `-m`，diffusion 用 `--diffusion-model`），
  不强制必填；页面允许手动改选，并用「额外参数」原样追加到启动命令；
- `llmmodel.scan_models` 惰性调用 `is_diffusion_weight` 跳过扩散权重与文本编码器 GGUF，
  避免模型页把扩散模型当成聊天模型列出。

### D5 / D6 生命周期

- 单实例：状态文件固定 `paint/server.json`，不做多服务命名空间；端口冲突时提示改端口；
- 生成前比对组件与参数指纹：一致则复用，不一致先停后启；同一时刻只允许一个出图任务；
- CLI `gen` 自动启动后保留服务，用 `stop` 显式停止；Qt 关窗只停本窗口自动拉起过的服务，
  避免残留显存占用；
- 进度 / 中断依赖服务端实现，缺失时只显示「生成中」并允许等待完成。

### D7 页面位置

画图是 llmw 的独立能力入口，与「对话 / 模型 / 运行」同级更符合使用频率；实验室页保留
代理 / 视频 / 音频三张规划卡，不再包含画图。

## 落地

```
src/fcmd/apps/llm/
├─ _llmpaint.py                # 纯逻辑：资产识别、组件/请求、参数与结果处理
├─ _llmpaint_engine.py         # sd.cpp 引擎：release 解析、安装、切换、检查
├─ _llmpaint_server.py         # sd-server：计划、状态、健康检查、txt2img 客户端
├─ _llmw_qt_paint.py           # 画图页 PaintPage + 模块级纯函数
├─ _llmw_qt_paint_engine.py    # 引擎下载 / 导入对话框
└─ llmpaint.py                 # CLI：models / gen / serve / stop / status / engine-*
```

- 路径单一来源仍是 `_llmconfig.py`：`paint_dir()`（`paint_dir` → `FCMD_PAINT_DIR` →
  `${home}/paint`）与 `paint_engines_root()`（`FCMD_SD_ENGINES` → `sd_engines_root` →
  `${home}/engines/sd.cpp`）；
- `llmengine` 新增公开包装 `download_file()` / `extract_zip()`，`llmserve` 新增
  `terminate_process()` / `spawn_flags()`，供 sd.cpp 模块复用镜像与进程生命周期；
- 画图页设置持久化到 `ui/llmw.json` 的 `paint` 字段（引擎 tag、组件、参数、输出目录、
  自动启动、端口），由 `_llmw_winsettings.load_settings` / `save_settings` 逐字段校验；
- `pyproject.toml` 注册 `llmpaint`，`cli/_manifest_data.py` 重新生成后包含全部子命令。
- MSVC 运行库：sd.cpp 使用 MSVC 2022 构建，系统只有 VC++ 2015（`msvcp140.dll 14.0`）
  时 sd-server 会在加载阶段 `c0000005` 崩溃且日志为空；启动前从本机 Visual Studio /
  VC Redist 复制新版 `msvcp140` / `vcruntime140` / `vcruntime140_1`，找不到时提示安装最新
  VC++ 2015-2022 Redistributable。
- Z-Image / Qwen-Image / MiniMax-H3 / HiDream / HunyuanImage 等家族要求独立 VAE 与
  LLM 文本编码器；启动前校验缺失组件并给出对应 Comfy-Org 仓库路径提示，避免
  sd-server 以「VAE tensor not in model metadata」启动即退出。
- 引擎安装对齐 LocModel 的做法：CUDA 引擎包与 `cudart-sd-bin-win-cu12-x64.zip` 一起
  解压；本地导入会优先带上同目录 cudart 包，GUI 支持多选 zip / 直接导入已编译引擎
  目录（可直接指向 LocModel 的 `engines/sd/<tag>`）；缺少 `cublas64_12.dll` 等
  运行库时启动前给出明确提示，而不是让 sd-server 静默崩溃。

## 验证

- `tests/apps/llm/test_cli_llmpaint.py`：资产识别、参数构建、引擎 release 解析与假 zip
  安装、状态读写、健康双探针、`txt2img` 成功 / 500 / 未知字段重试、CLI 各子命令；
  另覆盖 `qwen-image-2.1` / `MiniMax-H3-Image` 的父目录家族识别与扫描可见性；
- `tests/apps/llm/test_llmw_qt_logic.py`：画图页模块级纯函数（设置解析、额外参数、
  组件 / 请求、命令预览、服务标签、进度与尺寸预设、扫描根与出图目录、变体说明）；
- `tests/apps/llm/test_llmw_qt_smoke.py`：七页导航、切到画图页不崩、实验室页不再含
  「画图」卡片；
- `make check-fast` / `make test` / `make cov`（覆盖率 ≥95%）全绿。

## 交接与后续

- 自动下载仅覆盖 Windows x64；Linux / macOS 走本地导入；
- sd.cpp 的 CLI 与 HTTP 字段仍可能变动：以「命令预览 + 额外参数 + 未知字段重试」兜底，
  不追求覆盖全部家族；
- 服务为单模型常驻，切换模型 / 组件需重启并重新加载（FLUX 等大模型首次加载较慢，
  等待上限 300s）；
- 视频家族（Wan / LTX 等）不进首期图片下拉，留给后续「视频」页；
- 需要多任务队列、批量图库管理或服务多实例时，再评估状态目录与调度层的扩展。
