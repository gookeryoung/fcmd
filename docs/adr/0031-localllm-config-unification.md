# 0031 localllm 配置单一来源与设置页整合

- 状态：已落地
- 日期：2026-11-14
- 关联：`src/fcmd/apps/locllm/_llmconfig.py`、`src/fcmd/apps/locllm/_locllmw_settings.py`、
  `src/fcmd/apps/locllm/llmenv.py`、[ADR 0027](0027-locllmw-rename.md)、
  [ADR 0030](0030-locllmw-models-page.md)

## 背景

localllm 工具族此前各自解析目录：`llmenv` 从 `config.json` / 环境变量读取模型与引擎
目录；`llmdown` 未传 `--dest` 时写死 `./models`；locllmw 模型页把下载目标存在
`locllmw.json` 的 `download.dest`（默认同样是相对工作目录的 `models`）。结果是 GUI
下载的模型与 `llmserve` / `llmmodel` 扫描的模型目录不一致，运行页可能看不到刚下载
的模型。

原独立环境窗口还把目录覆盖写入 `ui/llmenvw.json` 并注入当前进程环境变量，形成第二
份配置与第二优先级：窗口内生效、其他终端不生效。另有多处写入语义不一致：增删模型
目录只回写模型键会丢其他键，概览页直接覆盖整个配置文件，恢复默认直接删除配置文件。

## 决策

### 1. 唯一权威定义

新增 `_llmconfig`，由它统一解析与落盘；`llmenv` 保留为命令层与兼容门面（re-export），
现有导入路径、CLI 子命令、输出与退出码不变。任何模块不再自带私有默认值。

`config.json`（`${FCMD_HOME:-~/.fcmd}/serve/config.json`，`FCMD_CONFIG` 可覆盖）权威键：

| 键 | 含义 | 默认 |
| --- | --- | --- |
| `models_dir` | **唯一**模型目录 | `D:/models`（存在时）否则 `${home}/models` |
| `work_dir` | 下载 / 转换等工作目录 | 模型目录 |
| `engines_root` | llama.cpp 引擎根目录 | `${home}/engines/llama.cpp` |
| `serve_dir` | 服务状态与日志目录 | `${home}/serve` |

优先级统一为「显式参数 > 环境变量 > `config.json` > 内置默认」：

- 模型目录：`--models-root` → `FCMD_MODELS` → `models_dir` → 默认；
- 工作目录：显式 dest → `FCMD_WORK` → `work_dir` → 模型目录；
- 引擎目录：`FCMD_ENGINES` → `engines_root` → 默认；
- 服务目录：`FCMD_SERVE_DIR` → `serve_dir` → 默认；
- 配置文件路径只由 `FCMD_CONFIG` / `FCMD_HOME` 决定，避免自引用。

所有写入改为合并式：保留其他键；恢复默认只删除对应目录键，不删除配置文件。

### 2. 模型目录收为单一值

模型目录不再支持列表。`models_roots`、`models_root`、`model_roots`、`models` 等旧键
仍可读入，但只取第一个作为 `models_dir`：

- 旧列表中的其余目录写入 `models_dir_extra` 迁移记录，作为设置页与 `llmenv show`
  的提示展示，**不再参与扫描**，避免静默丢弃；
- 迁移后旧键会被移除，此后只写 `models_dir`；再次设置模型目录会清除迁移记录；
- 读取点（`llmmodel`、`llmdoctor`、`llmconvert`、`llmconvertw`、`llmqntw`、locllmw
  模型页 / 运行页 / 概览页）统一调用 `_llmconfig.models_dir()`，不再各自遍历列表。

选择单一目录的理由：真实使用中模型目录只有一个主盘；多值带来的“主目录/次目录”
优先级、跨目录去重与下载落点歧义，比它解决的问题更多，也让设置页与 CLI 难以解释。
需要换盘时改一个目录即可，其余路径由迁移记录保留可追溯。

### 3. 旧配置一次性迁移

`load_config` 读入时迁移并写回一次，之后不再读取旧键：

- 模型旧键 → `models_dir`（列表取首项，其余记入 `models_dir_extra`）；
- `download_dir` / `workdir` / `work_root` → `work_dir`；
- `engines` / `engine_dir` / `llama_cpp_dir` → `engines_root`；
- `serve` → `serve_dir`；
- `ui/llmenvw.json` 的 `serve_dir` / `engines_root` 并入配置，旧文件改名为
  `.migrated`（保留数据，不再读取）；
- `ui/locllmw.json` 的 `download.dest` 仅当为绝对路径时迁移为 `work_dir`，随后移除
  该键；旧的相对默认值 `models` 不迁移，回落到统一默认。

### 4. 设置页收敛为三个分页

原独立窗口能力并入 locllmw「设置」页，用 `ttk.Notebook` 分成三页：

1. **目录**：模型目录（单一目录，可改可恢复默认）、工作目录；
2. **引擎**：引擎根目录；已安装引擎列表（TAG / 当前 / 可执行文件）、当前引擎摘要、
   设为默认、`llama-server --version` 体检；在线下载（TAG + cuda / cpu 变体）、
   离线包检查与安装（zip 或已编译目录、深度 CRC）、进度条与输出日志；
3. **界面**：窗口配置文件与已保存的窗口位置 / 大小（只读，关闭时自动写入）。

服务环境变量登记（plain / powershell / bash 三种格式）、`config.json` 原文编辑、
环境信息展示等多余项全部移除；删掉的控件不留空壳。目录修改直接写入权威
`config.json` 并全局生效，模型页的工作目录与设置页共用同一 `work_dir`。

引擎页不是重复实现：所有操作都调用 `llmengine` 的安装 / 列表 / 切换 / 体检函数，
与 `fcmd llmengine <install|list|use|check|inspect>` 同一套逻辑与错误语义，窗口只负责
组织控件、把进度回调转成进度条与日志、并在后台线程执行避免阻塞。

独立环境窗口与独立引擎窗口的工具入口、模块、清单元数据、测试、README 与 ADR
引用一并移除。

## 影响

- 行为变化：下载默认落点从「当前目录/models」改为「模型目录」，与 `llmserve`
  扫描口径一致；引擎 / 服务目录改为全局配置。
- 兼容性：`llmenv` CLI 参数、输出格式与退出码不变；旧配置读取后自动迁移。多值
  模型目录只保留首项，其余目录记录在 `models_dir_extra` 并在设置页提示。
- 界面：设置页为「目录 / 引擎 / 界面」三页；模型页不再在 `locllmw.json` 保存 `dest`；
  引擎管理不再单独开窗。
