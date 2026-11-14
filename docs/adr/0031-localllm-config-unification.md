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

原独立窗口 `llmenv` 的窗口版还把目录覆盖写入 `ui/llmenvw.json` 并注入当前进程环境
变量，形成第二份配置与第二优先级：窗口内生效、其他终端不生效。另有多处写入语义
不一致：增删模型目录只回写 `models_roots` 会丢其他键，概览页直接覆盖整个配置文件，
恢复默认直接删除配置文件。

## 决策

### 1. 唯一权威定义

新增 `_llmconfig`，由它统一解析与落盘；`llmenv` 保留为命令层与兼容门面（re-export），
现有导入路径、CLI 子命令、输出与退出码不变。任何模块不再自带私有默认值。

`config.json`（`${FCMD_HOME:-~/.fcmd}/serve/config.json`，`FCMD_CONFIG` 可覆盖）权威键：

| 键 | 含义 | 默认 |
| --- | --- | --- |
| `models_roots` | 模型目录列表 | `D:/models`（存在时）否则 `${home}/models` |
| `work_dir` | 下载 / 转换等工作目录 | 主模型目录（`models_roots` 第一项） |
| `engines_root` | llama.cpp 引擎根目录 | `${home}/engines/llama.cpp` |
| `serve_dir` | 服务状态与日志目录 | `${home}/serve` |

优先级统一为「显式参数 > 环境变量 > `config.json` > 内置默认」：

- 模型目录：`--models-root` → `FCMD_MODELS` → `models_roots` → 默认；
- 工作目录：显式 dest → `FCMD_WORK` → `work_dir` → 主模型目录；
- 引擎目录：`FCMD_ENGINES` → `engines_root` → 默认；
- 服务目录：`FCMD_SERVE_DIR` → `serve_dir` → 默认；
- 配置文件路径只由 `FCMD_CONFIG` / `FCMD_HOME` 决定，避免自引用。

所有写入改为合并式：保留其他键；恢复默认只删除 `models_roots` 键，不删除配置文件。

### 2. 旧配置一次性迁移

`load_config` 读入时迁移并写回一次，之后不再读取旧键：

- `models_root` / `model_roots` / `models` → `models_roots`；
- `download_dir` / `workdir` / `work_root` → `work_dir`；
- `engines` / `engine_dir` / `llama_cpp_dir` → `engines_root`；
- `serve` → `serve_dir`；
- `ui/llmenvw.json` 的 `serve_dir` / `engines_root` 并入配置，旧文件改名为
  `.migrated`（保留数据，不再读取）；
- `ui/locllmw.json` 的 `download.dest` 仅当为绝对路径时迁移为 `work_dir`，随后移除
  该键；旧的相对默认值 `models` 不迁移，回落到统一默认。

### 3. 迁移原环境窗口

原独立窗口能力并入 locllmw「设置」页，以四个子页提供：目录布局、模型目录、
服务与环境变量、配置文件原文编辑。目录修改直接写入权威 `config.json` 并全局生效，
不再只影响窗口进程；模型页的工作目录与设置页共用同一 `work_dir`。

独立窗口工具及其入口、模块、清单元数据、测试、README 与 ADR 引用一并移除。

## 影响

- 行为变化：下载默认落点从「当前目录/models」改为「主模型目录」，与 `llmserve`
  扫描口径一致；引擎 / 服务目录改为全局配置。
- 兼容性：`llmenv` CLI 参数、输出格式与退出码不变；旧配置读取后自动迁移。
- 界面：设置页新增四个子页；模型页不再在 `locllmw.json` 保存 `dest`。
