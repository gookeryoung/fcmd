# P24: 增强 README 文档

## 需求清单

- [x] P24a: 扩展 README 从 36 行到完整文档（安装/工具列表/内建命令/API/YAML/策略/开发）
- [x] P24b: 列出全部 24 个工具模块，按用途分组并标注可选依赖
- [x] P24c: 列出全部 6 个内建命令及用法
- [x] P24d: 补全 @fx.task / @fx.tool / cmd / aggregate 四种任务定义方式
- [x] P24e: 补全 YAML 编排示例与 fcmd yaml 命令用法
- [x] P24f: 补全执行策略说明
- [x] P24g: 补全开发指南与新增工具的零配置说明

## 迭代目标

原 README 仅 36 行，仅含 5 条特性 + 2 段示例 + MIT。作为一个已具备 24 工具 + 6 内建命令 + DAG 调度 + YAML 编排的项目，文档严重不足。本轮在不增加代码改动的前提下，仅增强 README.md，让首次使用者能快速了解：

- 怎么装（含可选依赖 extras）
- 有什么工具（按用途分组的完整清单）
- 有哪些内建命令（graph/info/completion/yaml/env/doctor）
- 怎么用 Python API 定义任务（4 种方式）
- 怎么用 YAML 编排
- 有哪些执行策略
- 怎么开发与贡献

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `README.md` | 从 36 行扩展到 297 行，新增 9 节：安装、内建命令、工具列表、任务定义 API、YAML 编排、执行策略、开发 |

## 关键决策与依据

### 1. 工具列表按用途分组而非字母序

24 个工具按字母序排列难以让用户快速定位需求场景。改用 5 个语义分组：项目构建与发布 / 文件与目录 / 多媒体（可选依赖）/ 系统与进程 / 开发环境。每组工具数量适中（2-7 个），用户可按场景定位。

### 2. 可选依赖用独立表格展示

`pyproject.toml` 的 `[project.optional-dependencies]` 有 7 个 extras（img/pdf/ocr/office/lint/test/dev），用户不易从 toml 文件直接看出"装哪个 extra 能用哪个工具"。README 用表格明确 extra → 工具/能力映射，并在工具列表的对应行重复标注（如 `imagetool` 依赖 `fcmd[img]`），冗余但便于检索。

### 3. CLI 参数映射规则独立成节

P22 刚完成 `_unwrap_optional` 与 `bool=True → --no-name store_false` 的框架增强，规则较隐蔽。在 `@fx.tool` 示例后用 bullet 列出 5 条映射规则，便于工具开发者避免踩坑（如忘记 `dest=pname` 导致 `--no-keep-ratio` 存到 `no_keep_ratio` 而非 `keep_ratio`）。

### 4. YAML 示例复用 yaml_loader.py docstring

`src/fcmd/yaml_loader.py` 模块 docstring 已有完整的 schema 示例（strategy/defaults/jobs/needs/cmd/run/env/matrix/if），README 直接复用此示例并补充 `fcmd yaml` 命令行用法与 Python API 等价形式，避免文档与代码示例脱节。

### 5. 不新增 CHANGELOG / CONTRIBUTING 等独立文档

rule-01 明确"不主动新建 *.md 文档"。本轮仅扩展已有 README，开发指南作为 README 一节而非独立 CONTRIBUTING.md。

## 代码实现情况

无代码改动。仅文档。

## 整合优化情况

- 工具列表的"别名"列与 `_TOOL_ALIASES` 注册表一致（如 pymake → pm）
- 内建命令表与 `_BUILTIN_COMMANDS` 元组一致（graph/info/completion/yaml/env/doctor）
- 执行策略表与 `_run_builtin` 的 `--strategy` choices 一致（sequential/thread/async/dependency）
- YAML 字段表与 `yaml_loader.py` 的 schema 一致

## 测试验证结果

文档改动无需运行测试。ruff/pyrefly/pytest 未触及代码路径，基线维持 99.21%。

## 遗留事项

1. **main.py 预先存在的未覆盖分支**（P23 遗留）：L79-80、L875、L898-900，可后续补测试
2. **config 命令**（P23 遗留）：.fcmd.toml 配置 schema 设计，风险较高
3. **README 中的 `<repo-url>` 占位符**：克隆示例未填实际仓库地址，需用户按实际仓库替换

## 下一轮计划

- 检查 `.trae/req/` 是否有未完成需求
- 补 main.py 预先存在的未覆盖分支测试
- 或实现 config 命令
- 或评估是否需要 user manual / dev guide 等独立文档（当前 README 已覆盖核心内容）
