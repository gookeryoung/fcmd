# fcmd 项目结构与优化方向分析

- 分析对象：`F:\Dev\fcmd`，HEAD `4dd709c`（v0.3.12 之后）
- 分析方式：只读。未修改代码 / 配置 / 文档；工作区里并行会话的 5 个 pptmake 改动未触碰
- 标注约定：**【实测】** 本轮在本机（Windows 11 / CPython 3.13 / `.venv`）测得；**【代码】** 静态扫描或阅读可得；**【推测】** 未验证
- 测量说明：所有耗时均为同一台机器、热文件缓存、5 次取中位数；进程级数字含解释器启动 ~32 ms 基线

---

## 0. 结论摘要

1. **先纠正一个前提**：分析线索里的 `dsl/`、`toolcmd`、`fileops`、`synth/`、`commands/*.toml` 在当前仓库**都不存在**。它们在 `95f7c02 refactor(dsl)!: 移除 TOML DSL，统一为 Python 工具并下沉基础件` 中已被删除，ADR 0002 也按索引约定连同文件一并删除。线索描述的是一份 P2 之前的旧快照，不能直接采信。【代码】
2. 分层整体是干净的：`infras` 没有任何向上层 import，`tools` 之间只有 1 处真实跨域依赖，模块级导入环只有 1 个（vendored 的 pyprof HTML 模板）。【实测】
3. 唯一一处**分层规则被破坏**：`core` 在模块顶层硬依赖 `fcmd.console`（进而依赖 `rich`），违反 ADR 0001「core 只依赖标准库」。代价是约 90/115 个候选工具自己完全不使用 `rich`，却每次执行都要多付约 54 ms 的导入。【实测】
4. 最大结构债是 `apps/cmder/app.py`：**8,735 行、29 个类、547 个函数**，其中 16 个对话框/结果面板类约 2,750 行 + 模型/设置约 990 行可以和视图分开。它不是"乱"，而是"不可并行修改"。
5. 性能上真正值得动的只有两处：CLI 的 `rich` 硬依赖、cmderw 的整包 `__init__` 连带导入。大目录列举本身已经很好（4,900 项 17–19 ms），不需要优化。
6. 文档漂移点集中在数字与状态行（README 写"100 个工具"实为 108；ADR 0004 基线是 P2 之前的数据；ADR 0013 写 22 个 GUI 模块/22,510 行，实测 26 个/27,714 行；ADR 0004 索引把"M3/M6 待决策"写成了"M7 暂缓"），改动成本极低。
7. **不建议**做的：重建 DSL、把入口点统一改成 `run_named`、给 Tk 视图补自动化单测、为 `apps/*` 强加与 `tools/*` 同款的镜像守卫。

---

## 1. 当前结构实测

### 1.1 分层与体量【实测】

| 层 | 文件数 | 行数 | 职责 |
|---|---:|---:|---|
| `cli` | 19 | 2,620 | 参数解析、发现、清单快路径、错误边界 |
| `core` | 20 | 5,491 | `@fcmd.tool` 注册、执行器、DAG、结果 |
| `infras` | 18 | 4,723 | 无业务的可复用基础件（fs/text/net/media/hashing/archive…） |
| `tools` | 130 | 37,123 | 108 个 CLI 工具，按 12 个领域分组 |
| `apps` | 40 | 24,026 | 复杂 GUI 应用（cmder / renamer / pptmake / reshack / filesync） |
| `gui` | 4 | 1,725 | Tk 基座与主题 |
| `fcmd` 根 | 5 | 448 | `__init__`、`console`、懒加载/懒日志 |
| **合计** | **236** | **76,156** | — |
| `tests` | 180 | 57,803 | 测试/源码行比 0.76 |

领域体量（行数降序）：`llm` 13,686 / `dev` 5,838 / `pdf` 4,429 / `media` 4,108 / `system` 3,569 / `text` 1,479 / `office` 866 / `calc` 743 / `archive` 730 / `data` 627 / `crypto` 507 / `net` 497。
应用体量：`cmder` 14,445（18 文件）/ `pptmake` 3,745 / `renamer` 2,212 / `reshack` 1,902 / `filesync` 1,709。

与 ADR 0004 的审计基线（167 文件 / 50,432 行 / 98 工具）相比，源码 **+41% 文件、+51% 行**；`# pragma: no cover` 从 156 增至 228，`# noqa` 从 113 增至 133。【实测】

### 1.2 依赖方向【实测：基于 AST 的模块级导入扫描】

- `infras → core/tools/apps/cli/gui`：**0 条**。基础层没有反向依赖。
- `gui → core/tools/apps/cli/infras`：**0 条**（仅标准库 + `fcmd.console` 的函数内导入）。
- `tools` 跨域依赖：**1 处真实跨域**——`llm → net`，具体是 `apps/locllm/llmserve.py:404` 与 `apps/locllm/llmdoctor.py:65` 函数内导入 `fcmd.tools.net.portcheck.is_port_in_use`。`media/pdf → fcmd.tools._deps` 是共享基础模块，不算跨域。
- `apps → tools`：3 处，全部是 `apps/cmder/app.py` 延迟导入 `fcmd.tools.archive.archivex`（ADR 0001 允许）。
- 模块级导入环：**1 个**，位于 vendored 的 `tools/dev/_pyprof_html` ↔ `tools/dev/pyprof`（自带 HTML 生成器），与本项目主干代码无关。
- **唯一违反 ADR 0001 的边**：`core → console`
  - `core/toolkit/exec.py:32`（顶层 `from fcmd.console import get_console, print_error`）
  - `core/execution/task_command.py:17`（顶层 `from fcmd.console import get_console`）
  - 其余 `core/toolkit/__init__.py:281`、`exec.py:346/390`、`executors.py:67/292`、`gui/window.py:203/344` 都已是函数内导入，做法正确。

### 1.3 工具发现与清单【实测 + 代码】

- 清单候选 115 个模块（`tools` + `apps` 非下划线文件），实际注册 **108** 个工具；别名与命令入口在 `pyproject.toml` 里手写 **113 条**。
- `_manifest_data.py` 为生成物（约 380 行），`tests/cli/test_tools_manifest.py:135` 有「每个工具都必须有 `[project.scripts]` 入口」的守卫，同文件 `:240` `test_digest_matches_sources` 有指纹一致性断言。**当前工作区（含并行未提交改动）DIGEST 与 LAYOUT 仍匹配**，快路径没有静默回退。
- 成本：`tool_layout()` 1.3 ms、`tool_digest()` 7.5 ms（热缓存）、`list_tool_rows()` 13.6 ms、`ensure_tools_discovered()` 约 308 ms。指纹成本可忽略，快路径设计成立。

---

## 2. 结构问题清单（影响面 × 修复成本）

> 排序方式：影响面从大到小，同影响面内修复成本从小到大。

| # | 问题 | 证据 | 影响面 | 修复成本 | 性质 |
|---|---|---|---|---|---|
| S1 | `core` 顶层依赖 `console`/`rich` | `core/toolkit/exec.py:32`、`core/execution/task_command.py:17`；115 个候选中仅 23 个顶层 import console、2 个直接 import rich | 全部 CLI 工具启动 | 低 | **分层回归** + 性能 |
| S2 | `apps/cmder/app.py` 8,735 行单体 | 29 类：`CmderWindow` 2,804 行、`FilePanel` 1,547 行、16 个对话框/面板类共 2,750 行、模型/设置 988 行 | cmder 全部后续改动与评审 | 中高 | 真实结构债 |
| S3 | `llm → net` 跨域 | `llmserve.py:404`、`llmdoctor.py:65` | 2 个工具 | 低 | 结构债（小） |
| S4 | 文件哈希/校验清单三处实现 | `infras/hashing.py`（59 行基础）、`tools/system/checksum.py`、`apps/cmder/_checksum.py`（254 行，自建清单结构） | 校验类功能一致性 | 低 | 重复实现 |
| S5 | 两套重命名引擎 | `apps/renamer/_core.py` 859 行 vs `apps/cmder/_rename.py` 470 行；`split_name`/preview/plan 概念重复 | 重命名语义分叉风险 | 中 | 重复实现（部分合理） |
| S6 | 归档格式探测三份 | `_archwrite._is_zip/_is_tar`、`_arcsearch`、`tools/archive/archivex.detect_format` | 归档边界行为 | 低 | 重复实现 |
| S7 | 113 条手写 `[project.scripts]` | `pyproject.toml`；与 108 工具 + 清单双重维护 | 新增工具流程 | 低 | 可维护性（已有测试兜底） |
| S8 | 布局守卫只覆盖 `tools` | `tests/test_layout.py:19` 只定义 `TOOLS`；apps 目录形状无同款断言 | apps 结构漂移 | 低 | 守卫缺口 |
| S9 | 文档数字/状态漂移 | README「100 个工具」→108；ADR 0004 基线（98 工具/50,432 行）→108/76,156；ADR 0013「22 模块/22,510 行」→26/27,714；`cli/main.py:147` 仍写「DSL 声明式命令」 | 读者判断 | 极低 | 文档漂移 |
| S10 | `infras/dev.py` 覆盖 48% | 116 语句 54 未覆盖（81–123、136–173：SSH/WSL 探测分支） | 环境探测回归 | 中 | 测试薄弱 |
| S11 | GUI 全量 `pragma: no cover` 228 处 | `app.py` 全部 Tk 类标注；CI 无显示环境 | 交互回归只能人工验收 | 高 | 刻意取舍（不建议改） |
| S12 | 各窗口 5 个薄包装样板 | 24 个窗各自 `run_gui/launch_cmd/load_settings/config_path/resolve_geometry` 转发 `gui.window` | 阅读噪音 | 中 | 重复但低价值 |

排序结论：**S1 + S9 属于"低成本、立即可做"**；**S2 是唯一值得立项的大改**；S3/S4/S6/S8 适合顺手清理；S5 要基于实际重合度判断，不建议强行统一。

---

## 3. 性能与启动

### 3.1 实测数字（热缓存，5 次中位数）

| 场景 | 进程级耗时 | 扣除 32.4 ms 解释器基线 |
|---|---:|---:|
| `import fcmd` | 43.1 ms | 10.7 ms |
| `import fcmd.cli.main` | 52.0 ms | 19.6 ms |
| `import rich.console` | 86.3 ms | **53.9 ms** |
| `import fcmd.core.toolkit` | 113.0 ms | 80.6 ms |
| `import fcmd.apps.cmder.app` | 193.5 ms | **161.1 ms** |
| `fcmd --version` | 57.9 ms | 25.5 ms |
| `fcmd`（列表） | 163.0 ms | 130.6 ms |
| `fcmd pymake` | 139.5 ms | 107.1 ms |

单进程内测（排除进程启动抖动）：`tool_layout` 1.3 ms、`tool_digest` 7.5 ms、`list_tool_rows` 13.6 ms、`ensure_tools_discovered` ≈308 ms。
大目录列举：`list_directory(C:\Windows\System32, show_hidden=True)`，4,900 项，**17–19 ms**（两次调用一致）。

> 说明：本轮首测时机器上残留并发测试进程，`fcmd` 列表一度测得 309 ms、`pymake` 253 ms；上表为进程清空后的第二轮。绝对值随机器负载浮动，但**相对关系稳定**（rich ≈ 54 ms，cmder.app ≈ 161 ms）。

### 3.2 解读

- **`fcmd <tool>` 的固定税是 `rich`**。链路是 `cli/main._run_tool → core.toolkit.exec → fcmd.console → rich`，`tools/dev/pymake.py` 自己并没有 import console，却仍然把 rich 拉进 `sys.modules`（实测）。115 个候选中只有 23 个在顶层 import console，另有 `llmdown.py`、`colortool.py` 直接 import rich，**其余约 90 个都在为一个不用的渲染库付钱**。【实测】
- `fcmd`（列表）130 ms 里，rich 约占 54 ms，其余是发现模块导入 + 表格渲染 + argparse。`--json`/纯文本输出能砍掉大部分，但会改变默认观感，属于产品取舍。
- `cmder.app` 的 161 ms 里，`_ops` 本身只有标准库依赖，慢是因为 `apps/cmder/__init__.py` 会连带导入 `app`——**任何 `fcmd.apps.cmder.*` 的导入（包括跑 `_ops` 单测）都会拉起整个 8,735 行模块**。这是包门面与工具注册的既有约定，拆完 S2 后可顺便改为对话框懒加载，预计再省 40–60 ms【推测】。
- 大目录列举无需优化：18 ms / 4,900 项，且已是 `os.scandir` + 后台异步刷新 + Canvas 虚拟化。

---

## 4. 可维护性

### 4.1 超大文件【实测】

全仓 236 个源文件中，**8 个超过 1,000 行，2 个超过 2,000 行**：

| 文件 | 行数 | 说明 |
|---|---:|---|
| `apps/cmder/app.py` | 8,735 | 视图 + 模型 + 设置混在一起，唯一真正需要拆的文件 |
| `apps/cmder/_ops.py` | 2,565 | 纯逻辑，97 个函数，符合"逻辑与 UI 分离"的预期 |
| `tools/system/envw.py` | 1,331 | 环境变量 GUI，工具内单体 |
| `apps/locllm/llmdownw.py` / `llmdown.py` | 1,262 / 1,252 | 模型浏览 / 下载，已在 0016 记录 |
| `gui/theme.py` | 1,239 | ttk 样式表驱动，ADR 0004 M6 待决策 |
| `apps/renamer/renamerw.py` | 1,021 | 批量重命名 GUI |
| `apps/pptmake/pptmakew.py` | 987 | 接近阈值，可暂不处理 |

判断：除 `app.py` 外，其余大文件都是"单工具单窗口"的自然结果，拆分收益低于回归成本。

### 4.2 测试与覆盖率【实测】

- `pytest -n 8 --cov`：**5,936 passed / 9 skipped / 66.65 s**，总覆盖率 **96%**（24,155 语句，819 未覆盖，322 个分支未走全）。
- 覆盖薄弱点（按缺口绝对值）：
  - `infras/dev.py` **48%**（54/116 未覆盖，集中在 SSH/WSL 探测）
  - `tools/system/alarmclock.py` **48%**（138/266 未覆盖，GUI + 定时逻辑）
  - `img2ico.py` 79%、`padtool.py` 79%、`tomltool.py` 83%、`websave.py` 84%
- 大多数工具 ≥94%，141 个文件 100% 覆盖；总覆盖率数字好看，**但"覆盖 96%" 与"GUI 行为有数"是两件事**：228 处 `pragma: no cover` 让 Tk 视图完全不进统计。
- 测试耦合：全仓 `monkeypatch` 引用 2,396 处，`tests/cli/test_cli.py` 仍是直捣 `_discovery` 私有表的模式（ADR 0004 M7 已记录并暂缓）。

### 4.3 重复实现（已核实的具体项）

1. **校验和**：`infras/hashing.py` 只有单文件摘要；`apps/cmder/_checksum.py`（254 行）自建 `parse_manifest/verify_manifest`；`tools/system/checksum.py` 又是一套。三者的文本格式与失败项表述存在分叉风险。
2. **重命名**：`apps/renamer/_core.py` 与 `apps/cmder/_rename.py` 都有 `split_name`/`RenamePreviewItem`/`RenamePreview`/`preview_to_plan`，但前者是规则流水线、后者是 TC 掩码。**重合的是数据结构与命名，不是算法**——收敛应只抽公共基元，不应合并引擎。
3. **归档探测**：`_archwrite`/`_arcsearch` 各自的 `_is_zip/_is_tar` 与 `archivex.detect_format` 三份判断。
4. **窗口样板**：24–26 个窗各自 `run_gui`/`launch_cmd`/`load_settings`/`config_path`/`resolve_geometry` 转发 `gui.window`，均为一行委托，属可接受噪音。

### 4.4 生成物与指纹机制的风险

- 设计是合理的：内容指纹（CRLF 归一化后哈希）+ 布局集合，任何候选文件内容变化都会让快路径失效并**静默回退**到 308 ms 的完整发现。回退不报错，只慢。
- 真实风险有三点：
  1. **本地开发感知不到**：只有 CI（`test_tools_manifest`）会红灯；本地若忘了重生成，功能正确但每次启动慢 200 ms 以上。
  2. **`apps/*` 也在候选内**，所以 S2 的拆分即使只搬动对话框，也必须重生成清单。
  3. 指纹只覆盖候选模块内容，**不覆盖 `pyproject.toml` 的 scripts**；入口点漂移由另一条测试守住，两者是分开的防线（可接受，但需要知道）。

---

## 5. 优化方向建议

### A 档：低风险、可独立提交、能立刻量化收益

| 项 | 动机 | 改动面 | 风险 | 重生成清单 | ADR |
|---|---|---|---|---|---|
| **A1 切断 `core → console` 顶层依赖** | 恢复 ADR 0001 分层；约 90 个工具每次省 ~54 ms | `core/toolkit/exec.py:32`、`core/execution/task_command.py:17` 改为函数内导入（与 `executors.py` 现有注释口径一致） | 低：仅导入时机变化，rich 缺失时的错误路径需保持 | 否（`core` 不在候选） | 更新 0001 落地状态，无需新 ADR |
| **A2 收敛 `llm → net`** | 消除唯一跨域依赖 | 把 `is_port_in_use` 移到 `infras/net.py`；`tools/net/portcheck.py` 保留薄转发或直接引用 | 低 | **是**（`portcheck.py` 内容变） | 更新 0001 |
| **A3 文档/注释对齐** | 读者不被旧数字误导 | README「100 个工具」→108；ADR 0004 基线加"截至 2026-11-10"或刷新；ADR 0013 旧行数标注测量日期；`cli/main.py:147` 删掉 DSL 表述；ADR 0004 索引行改为「生效（M3/M6 待决策，M7 暂缓）」 | 极低 | 否 | 0004 自身 |
| **A4 校验清单下沉** | 三份实现合成一份 | 新增 `infras/manifest.py`（或扩 `hashing.py`），`tools/system/checksum.py` 与 `apps/cmder/_checksum.py` 改为调用 | 低-中：需保持两种 UI 的文案与排序 | **是**（`checksum.py` 变） | 不需要；若新增 infras 模块，按 0003 单文件模块约定即可 |
| **A5 归档探测复用** | 消除 3 份格式判断 | cmder 的 `_is_zip/_is_tar` 改为调用 `archivex.detect_format` | 低 | 否（`_archwrite.py`/`_arcsearch.py` 为下划线模块，不参与候选） | 不需要 |
| **A6 补 `infras/dev.py` 覆盖** | 当前最大逻辑缺口 48% | 注入 `subprocess`/`shutil.which` 假实现，覆盖 SSH client 探测各分支 | 低 | 否 | 不需要 |

### B 档：值得立项，但需要设计与分批

| 项 | 动机 | 改动面 | 风险 | 重生成清单 | ADR |
|---|---|---|---|---|---|
| **B1 拆 `apps/cmder/app.py`** | 8,735 行 → 约 4,300 行，恢复可并行修改 | ① 16 个对话框/面板类（3,182–5,931，约 2,750 行）拆成平铺的 `_dlg_*.py`；② `FileEntry/PanelState/CmderSettings/PanelScan*/ArchiveMember`（453–1,440，约 990 行）→ `_models.py`/`_settings.py`；③ 保留 `FilePanel` + `CmderWindow`。**不能建 `dialogs/` 子包**——`tests/test_layout.py` 限制 3 段路径，平铺 + `_` 前缀即可（下划线不参与工具发现） | 中高：Tk 类互相引用多，需一次机械搬迁 + 保持对外导入路径 | **是**（`app.py` 属候选，内容变） | 记入 ADR 0004（M2 的延续），不需要新 ADR |
| **B2 重命名公共基元** | 两套引擎共享语义，避免规则漂移 | 只抽 `split_name`、唯一名规划、非法字符检查到 `apps/cmder/_rename_common.py` 或 `infras/rename.py`；**不合并引擎** | 中：两边测试都要跟着迁移 | 否（下划线） | 更新 0009/0014 交叉说明 |
| **B3 `core` 输出职责归位** | `exec.py` 现在直接渲染表格/错误，是 presentation 混进 core | 让 core 返回结构化结果，`cli` 负责渲染 | 中高：触及所有工具的输出路径 | 否 | 更新 0001 |
| **B4 cmderw 启动懒加载** | 拆完 B1 后才划算 | 对话框模块按需 import；`__init__` 不连带 `app` | 中：需保证注册时机不变 | 否 | 不需要 |

### C 档：可选，收益依赖产品取舍

- **C1 `fcmd` 列表去掉 rich 表格**（纯文本/`--json`）：省 ~54 ms，但改变默认观感，需产品确认。
- **C2 指纹缓存**（按 mtime 缓存摘要）：当前 7.5 ms，收益可忽略；只有把仓库放到网络盘才有意义。
- **C3 给 `apps/*` 补布局守卫**：低风险，但当前 apps 只有 5 个目录，收益有限。

---

## 6. 明确不建议做的事

1. **重建 DSL / 合并 `dsl` 与 `commands`**：DSL 已在 `95f7c02` 整体移除，ADR 0002 已按索引规则删除。这条线索基于旧快照，做任何"优化"都会把刚删掉的复杂度加回来。
2. **把 `fileops` 搜索下沉到 `core/shared`**：仓库里没有 `fileops` 和 `core/shared`；搜索能力已经在 `infras/fs.py`（`search_by_name` / `search_by_content`），位置正确（基础件、无业务）。
3. **把 SSH 探测从 `synth` 下沉**：`synth` 不存在；SSH 探测已在 `infras/dev.py`。它的问题不是位置，而是 48% 覆盖率。
4. **拆 `toolcmd` 的 codec/conv/crypto/compute**：`toolcmd` 已不存在；对应能力分别在 `infras/codec|crypto|data` 与 `tools/calc|crypto`，分层已到位。
5. **把 113 条入口点统一改成 `run_named`**：会破坏 `fcmd-xxx` 直连入口的启动优势，且已有测试守护入口完整性，收益为负。
6. **给 Tk 视图补自动化单测以拉高覆盖率**：CI 无显示环境，代价高、稳定性差。维持 `pragma: no cover` + 纯逻辑单测 + 人工验收清单是正确取舍（ADR 0013 已有结论）。
7. **为凑"去重"而统一两套重命名引擎**：两者语义不同（规则流水线与 TC 掩码），重合度主要在数据结构。强行合并会制造最难维护的"万能引擎"。
8. **为 `apps/*` 建与 `tools/*` 完全对称的镜像测试守卫**：可做，但当前 apps 规模小，先解决 S1/S2 更有价值。

---

## 7. 已验证 vs 推测

**已验证**
- 全部行数/文件数、最大文件、类行区间、`pragma`/`noqa` 计数、导入方向、跨域依赖、导入环、清单指纹匹配状态
- 全部启动/导入/列举耗时（含两次采样与负载说明）
- 覆盖率 96%、5,936 passed / 9 skipped、低覆盖文件清单
- `dsl/toolcmd/fileops/synth/commands` 不存在，ADR 0002 已删除

**推测（未验证）**
- 拆完 B1 后 cmderw 启动还能再省 40–60 ms（依赖懒加载的实现方式）
- A4 合并校验清单后两种 UI 的文案可以无损统一（需逐条对文案）
- `apps/*` 布局守卫的实际收益（当前目录数太少）

---

## 8. 复现命令

```powershell
# 结构与体量
.venv\Scripts\python.exe -c "import pathlib; root=pathlib.Path('src/fcmd'); [print(l, len(list((root/l).rglob('*.py')))) for l in ('cli','core','infras','tools','apps','gui')]"

# 分层边 / 跨域 / 环：使用 AST 扫描 src/fcmd，过滤 ImportFrom/Import 的 fcmd.* 前缀（脚本见本次分析过程）

# 启动与导入（5 次中位数，进程级）
.venv\Scripts\python.exe -m fcmd --version
.venv\Scripts\python.exe -m fcmd
.venv\Scripts\python.exe -m fcmd pymake

# 大目录列举
.venv\Scripts\python.exe -c "from pathlib import Path; from fcmd.apps.cmder.app import list_directory; import time; t=time.perf_counter(); e=list_directory(Path(r'C:\Windows\System32'), show_hidden=True); print((time.perf_counter()-t)*1000, len(e))"

# 覆盖率
$env:COVERAGE_FILE="$env:TEMP\fcmd_cov"; .venv\Scripts\python.exe -m pytest tests -q -n 8 --cov --cov-report=term-missing:skip-covered --cov-fail-under=0

# 清单新鲜度
.venv\Scripts\python.exe -c "from fcmd.cli import _manifest, _manifest_data as d; print(_manifest.tool_digest()==d.DIGEST, frozenset(d.LAYOUT)==_manifest.tool_layout())"
```
