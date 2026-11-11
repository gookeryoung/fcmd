# 0019 filescan：目录敏感内容扫描

- 状态：已实施（纯逻辑 / CLI / GUI 均已落地，含后缀白名单与性能基准）
- 日期：2026-11-11

## 背景

仓库里的搜索类能力只有 `filesearch`（按文件名/内容找字符串，命中即列文件），
没有「按规则集判定敏感内容并聚合报告」的工具。实际需求是：给一批目录，
用关键词与正则组成可组合、可分组、可启停的规则集，批量判定身份证号、
手机号、邮箱、银行卡号、API Key、私钥头等敏感内容，输出可按文件查看、
可打码、可导出 CSV / JSON / 文本的文件级报告。

约束：

- 扫描只读，绝不写被扫文件；报告默认打码，显式开关才输出原文；
- 规则解析/求值、目录遍历、匹配与报告必须可单测，Tk 层只做展示；
- 目录深度遵守 ADR 0003（`fcmd/apps/filescan/<模块>.py`，不建子包）；
- 大目录要能中断，单文件读取有上限与编码容错；
- 不新增第三方依赖。

## 决策

### D1 应用包与双入口

`fcmd/apps/filescan/`：`filescan`（CLI）与 `filescanw`（Tk）两个公开模块，
模块名即命令名，遵循 ADR 0003 的逐公开模块注册，不声明 `__tool_name__`。

| 模块 | 职责 |
|---|---|
| `_rules.py` | 规则/规则集模型、内置敏感规则、表达式语法解析与求值、JSON 读写 |
| `_engine.py` | 目录遍历、文件类型/大小过滤、编码探测、逐行匹配、聚合与统计、并行与取消 |
| `_report.py` | 文本 / JSON / CSV 报告、按规则与路径过滤、打码 |
| `filescan.py` | CLI：`scan` / `rules`（list·export·validate） |
| `filescanw.py` | Tk 窗口、设置持久化（`~/.fcmd/ui/filescanw.json`） |

`_rules.py` / `_engine.py` / `_report.py` 只依赖标准库，不导入 Tk，不注册工具；
CLI 与 GUI 共用同一套扫描与报告逻辑。GUI 复用 `fcmd.gui.window` / `theme` /
`config`，不另起窗口骨架。

### D2 规则模型与表达式

单条规则：

- `id`（规则集内唯一，表达式引用它）、`name`、`group`（分组名，默认「内置」）、
  `enabled`；
- 匹配器二选一：
  - `keyword`：`text` + `case_sensitive` + `whole_word`（整词按 `\b` 边界，
    仅对首尾为词字符的关键词生效，避免中文关键词被误判）；
  - `regex`：`pattern` + `ignore_case`（不支持跨行匹配，逐行求值）。

表达式采用可嵌套的文本 DSL：`rule_id`、`and` / `or` / `not`、圆括号，
`and` 优先级高于 `or`，`not` 只能作用于单个规则或括号组。例：

```
(cn_id or bank_card) and not allow_list
```

表达式在**文件级**求值：叶子规则为真表示该规则在文件任意一行命中；文件级
判定使 `and` / `not` 表达「同一文件同时出现 / 不出现某类内容」的语义，避免
逐行求值时 `not` 对所有无命中行都为真。文件命中后，报告只列出参与表达式且
实际命中的正向规则的逐行命中（`not` 分支仅用于判定，不产出命中明细）。

未写表达式时默认 `or` 全部启用规则；引用了不存在的规则、未知记号、
括号不配对、空表达式一律报中文错误，不静默忽略。禁用规则在表达式中恒为
假（`not 禁用规则` 恒为真）。

规则集 JSON（`*.filescan.json`，同时接受任意 `.json`）：

```json
{
  "version": 1,
  "name": "内置敏感规则",
  "rules": [
    {"id": "cn_id", "name": "身份证号", "group": "个人信息", "enabled": true,
     "matcher": {"type": "regex", "pattern": "\\d{17}[\\dXx]", "ignore_case": false}}
  ],
  "expression": "(cn_id or bank_card) and not allow_list"
}
```

加载时逐字段校验（未知类型、非法正则、重复 id、未知规则引用、非对象条目
都返回带规则名的中文错误）。内置规则覆盖：身份证号、手机号、邮箱、银行卡号、
API Key / 访问令牌、私钥头。内置集可一键清空、全部禁用、按组禁用。

### D3 扫描范围与匹配引擎

`ScanOptions`：

- `recursive`（默认真）、`max_depth`（0 表示不限）、`follow_symlinks`（默认假；
  为真时用 `resolve()` 去重防止目录环）；
- `ext_whitelist`（后缀白名单，默认 `DEFAULT_TEXT_EXTENSIONS` 共 102 项常见
  文本后缀：txt/md/log/csv/json/yaml/yml/ini/toml/cfg/conf/env/py/js/ts/java/c/go/rs/sql/sh/html/css/xml 等；
  按「文件名结尾」匹配，`.env` 同时命中 `.env` 与 `prod.env`；`*` 表示全部后缀）；
- `include_exts`（在白名单内收窄）、`force_exts`（强制扫描名单外后缀的逃生口）、
  `exclude_exts`（最高优先级）；四者均支持含点/不含点与大小写不敏感，
  判定顺序为 排除 → 强制 → 白名单 → 收窄；
- `min_size` / `max_size`；
- `skip_binary`（默认真，按前 8 KiB 是否含 `\x00` 判定；白名单已跳过的文件不做
  二进制嗅探）；
- `max_file_bytes`（单文件读取上限，默认 8 MiB，超限记入跳过原因）；
- `encodings`（默认 BOM 检测 → `utf-8` → `gb18030` → `latin-1` 兜底）；
- `workers`（默认 4）、`context_chars`（默认 40，命中片段左右各取）；
- `max_hits_per_file`（默认 200，防单文件刷屏）。

后缀白名单是默认路径：名单外文件在遍历阶段直接跳过、不打开、不解码，因此
混合目录的扫描耗时主要由「白名单内文件数」决定（见 D9 实测）。需要扫描名单外
格式时用 `force_exts`（CLI `--force-ext`、GUI「强制后缀」）或把白名单改成 `*`。

遍历用 `os.scandir` 迭代实现，不跟随符号链接（除非显式开启），权限错误、
读取失败记入 `errors` 并继续。二进制、超限、大小过滤、符号链接记入 `skipped`
（带原因），不当作错误。读取按行流式进行，单行超长（> 1 MiB）截断后再匹配并标注。

并行使用 `ThreadPoolExecutor`，每个文件处理前检查 `CancelToken`（`is_set()`）；
取消后返回已完成部分并标记 `cancelled`。进度回调 `(done, total, path)` 在
worker 线程调用，GUI 经队列回主线程。结果按路径排序，保证可复现。

统计 `ScanStats`：文件总数、已扫描、跳过、错误、读取字节数、命中文件数、
命中条数、耗时，用于吞吐口径（`字节 / 秒`、`文件 / 秒`）。跳过原因按
`files_skipped_extension`（后缀未命中）/ `files_skipped_binary`（二进制）/
`files_skipped_other`（符号链接、大小过滤、超限等其他原因）分别计数。

### D4 结果、打码与报告

- `RuleMatch`：规则 id/名、行号（1 起）、列区间、打码片段、原文片段（受
  `include_raw` 控制）、上下文（左右各 `context_chars` 字符）；
- `FileHit`：路径、大小、编码、匹配列表；
- `ScanResult`：命中列表、跳过列表、错误列表、统计、是否取消。

打码规则：命中文本长度 ≥ 6 时保留首 2 与末 2 字符，中间以 `*` 替换；
长度 < 6 时整体替换为 `***`。上下文中的命中区间使用同一打码结果拼接；
`include_raw=True` 时才输出原文。默认 `include_raw=False`，CLI 必须显式
`--raw`，GUI 必须显式勾选「导出原文」。

报告：

- 文本：文件路径 + 每条「规则 / 行号 / 上下文」，末尾汇总统计与跳过原因；
- JSON：结构化结果（含统计与错误），默认打码；
- CSV：每行一条命中（文件、规则、行号、片段、上下文），UTF-8 BOM 便于 Excel。

`filter_result` 支持按规则 id 集合与路径子串过滤，过滤后重算命中计数。

### D5 CLI

- `filescan scan PATHS...`：`--rules FILE`（默认内置）、`--expr TEXT`、
  `--no-recursive`、`--max-depth`、`--follow-symlinks`、`--ext-whitelist TEXT`
  （留空用内置，`*` 为全部后缀）、`--include-ext`（白名单内收窄）、
  `--force-ext`（强制扫描名单外后缀）、`--exclude-ext`、`--min-size` / `--max-size`、
  `--no-skip-binary`、`--max-file-bytes`、`--workers`、`--format text|json|csv`、
  `--output FILE`、`--raw`、`--rule ID`（可重复）、`--path SUBSTR`；
- `filescan rules`：`--action list|export|validate`、`--output FILE`、`--rules FILE`。

`scan` 找不到命中返回 0（扫描完成即有结论），参数或规则错误返回非 0。
输出文件统一 UTF-8；CSV 带 BOM。

### D6 GUI

`ttk.Notebook` 两页（与 llmdownw 的分页口径一致，避免表单堆叠）：

1. **扫描**：多目录列表（添加/移除）、递归与深度、符号链接、可编辑后缀白名单
   （默认填充内置 102 项，「恢复默认白名单」一键还原）、后缀包含/排除、
   强制后缀、大小上下限、跳过二进制、单文件上限、并发数、规则集下拉
   （内置/载入）；开始/取消、进度条与状态栏（含后缀/二进制跳过分类）；
   结果树按文件聚合（父节点路径 + 命中数，子节点规则/行号/上下文），
   支持按规则与路径筛选；导出 CSV/JSON/文本。
2. **规则**：规则表（启用、分组、名称、类型、表达式摘要）、新增/编辑/删除、
   启用/禁用、载入内置、清空、导入/导出/保存规则集、表达式输入框与校验。

设置（目录、选项、规则集、几何位置）持久化到
`~/.fcmd/ui/filescanw.json`；扫描在后台线程执行，取消走 `CancelToken`，
关窗时置位取消并等待 worker 退出，不留下悬挂线程。

### D7 安全边界

- 全程只读：仅 `open(..., "rb")` 读取，不创建临时文件、不修改 mtime；
- 报告默认打码，危险动作只有「导出原文」一处显式开关；
- 不跟随符号链接默认开启关闭项，避免扫描越出指定目录；
- 单文件读取上限防止误扫超大二进制或设备文件。

### D8 测试与门禁

- `_rules` / `_engine` / `_report` 单测覆盖：表达式解析与优先级、非法输入、
  内置规则、JSON 往返、白名单/收窄/强制/排除、深度/大小/二进制过滤、编码回退、取消、进度、
  打码、三种报告与过滤；
- CLI 测试走 `run_tool` 全链路（含 `--format json` / `--format csv` / `--raw` /
  `--ext-whitelist` / `--force-ext`）；
- GUI 只测设置映射、配置路径与几何回退，窗口类不纳入覆盖率；
- 改动后按 ADR 0003 在干净工作区重生成 `fcmd/cli/_manifest_data.py`；
- `make check-fast` 必过，收尾 `make test` 全绿。

### D9 性能基准与防回归

基准脚本 [bench_filescan.py](../../tests/apps/filescan/bench_filescan.py) 独立运行，
不进默认 `make test`：

```
uv run python tests/apps/filescan/bench_filescan.py          # 打印实测
uv run python tests/apps/filescan/bench_filescan.py --check  # 与基线比对
make bench-filescan                                          # 同上（独立 target）
```

场景：5000×4 KiB 小文件（单规则 / 7 条内置规则）、20×2 MiB 大文件、8 层深目录
（480 文件）、2000 白名单内 + 3000 白名单外混合目录（分别用白名单与
`ext_whitelist=("*",)` 全后缀跑，输出加速比）。每个场景先校验扫描文件数与
后缀跳过数，防止过滤条件写错导致「虚快」。

本机基线（Windows 11、Python 3.13、`workers=4`、语料在系统临时目录）：

| 场景 | 文件数 | 读取体积 | 耗时 | 文件/秒 | MiB/秒 |
|---|---:|---:|---:|---:|---:|
| small/1rule | 5000 | 19.8 MiB | 4.81 s | 1040 | 4.11 |
| small/builtin（7 规则） | 5000 | 19.8 MiB | 6.59 s | 758 | 3.00 |
| large/builtin（2 MiB/个） | 20 | 40.0 MiB | 3.75 s | 5.3 | 10.67 |
| deep/builtin（8 层） | 480 | 971 KiB | 0.53 s | 910 | 1.80 |
| mixed/whitelist | 2000（另跳过 3000） | 4.0 MiB | 2.22 s | 900 | 1.78 |
| mixed/all-ext（改前行为） | 5000 | 9.9 MiB | 5.57 s | 898 | 1.77 |

白名单加速比：同一混合目录，白名单跳过 3000/5000 个文件后耗时 2.22 s，改为
全后缀逐文件嗅探需 5.57 s，**约 2.4–2.5 倍**；扫描文件数本身就是 2.5×，说明
后缀跳过的成本接近纯 `stat`，白名单确实把「读+判二进制」的成本省掉了。

`--check` 以 `BASELINES` 为参照，文件/秒或 MiB/秒低于基线的 `--min-ratio`
（默认 50%）即判回归并返回非零；阈值刻意留宽，用于挡「数量级」退化而非
抖动。基线绑定本机与 `workers=4`，换机器/CI 需按实测回填。

## 验收

- 对含身份证号/手机号/邮箱/API Key 的样例目录扫描，命中文件、规则、行号与
  上下文正确，默认打码；
- `(a or b) and not c` 等嵌套表达式按文件级语义判定，非法表达式给出中文错误；
- 递归深度、后缀白名单/收窄/强制/排除、大小上下限、跳过二进制、单文件上限均生效；
  统计能区分「后缀未命中跳过」与「二进制跳过」；
- 取消后返回已完成部分且不写任何文件；
- CSV / JSON / 文本三种报告可导出，`--raw` 才含原文；
- GUI 可添加多目录、编辑白名单、载入内置规则、编辑/启停规则、查看聚合结果、筛选与导出；
- 数千文件目录有进度且可取消，给出本机实测文件数、体积、耗时与吞吐（见 D9）。

## 落地状态

- 设计：已定稿（本 ADR）。
- 纯逻辑引擎：已落地（`_rules` / `_engine` / `_report`，含后缀白名单与跳过分类统计）。
- CLI：已落地（`scan` / `rules`，含 `--ext-whitelist` / `--force-ext`）。
- GUI 与收尾：已落地（`filescanw.py`，两页布局 + 设置持久化）。
- 性能基准：已落地（`bench_filescan.py` + `make bench-filescan`，基线见 D9）。
