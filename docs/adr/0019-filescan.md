# 0019 filescan：目录敏感内容扫描

- 状态：已实施（纯逻辑 / CLI / GUI 均已落地，含后缀白名单与性能基准）
- 日期：2026-11-11

## 背景

仓库里的搜索类能力只有 `filesearch`（按文件名/内容找字符串，命中即列文件），
没有「按规则集判定敏感内容并聚合报告」的工具。实际需求是：给一批目录，
用关键词与正则组成可组合、可分组、可启停的规则集，批量判定身份证号、
手机号、邮箱、银行卡号、API Key、私钥头等敏感内容，输出可按文件查看、
可打码、可导出 CSV / JSON / 文本 / SARIF 的文件级报告。

约束：

- 扫描只读，绝不写被扫文件；报告默认打码，显式开关才输出原文；
- 规则解析/求值、目录遍历、匹配与报告必须可单测，界面层只做展示；
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
| `_report.py` | 文本 / JSON / CSV / SARIF 报告、按规则与路径过滤、打码 |
| `_config.py` | 扫描配置的导入 / 导出、逐字段容错与版本兼容 |
| `filescan.py` | CLI：`scan` / `run` / `rules`（list·export·validate） |
| `filescanw.py` + `_filescanw_qt.py` | Qt 窗口、设置持久化（`~/.fcmd/ui/filescanw.json`） |

`_rules.py` / `_engine.py` / `_report.py` / `_config.py` 只依赖标准库，不导入 Tk，
不注册工具；CLI 与 GUI 共用同一套扫描、报告与配置逻辑。GUI 复用
`fcmd.gui.toolwindow` / `theme` / `config`，不另起窗口骨架。

### D2 规则模型与表达式

单条规则：

- `id`（规则集内唯一，表达式引用它）、`name`、`group`（分组名，默认「内置」）、
  `enabled`、`severity`（严重级，`info` / `low` / `medium` / `high` / `critical`，
  默认 `medium`；旧规则集缺省时按 `medium` 补齐，非法值报中文错误）；
- 匹配器四选一：
  - `keyword`：`text` + `case_sensitive` + `whole_word`（整词按 `\b` 边界，
    仅对首尾为词字符的关键词生效，避免中文关键词被误判）；
  - `regex`：`pattern` + `ignore_case`（不支持跨行匹配，逐行求值）；
  - `time`：`target`（`modified` / `created` / `accessed`）+ `compare`
    （`before` / `after` / `between`）+ `value`（+ `between` 时的 `value2`），见 D16；
  - `size`：`compare`（`lt` / `gt` / `between`）+ `value` / `value2`（``10MB`` 等），
    见 D17。
- 内容类 `target`：`content`（默认，匹配正文）、`filename`（匹配文件名，见 D13）
  或 `path`（匹配完整路径，见 D17）。
- `regex` 可带 `validator`（`luhn` / `cn_id` / `credit_code` / `iban`），命中片段
  通过校验才算命中；`Rule.min_hits` / `max_hits` 控制文件级命中次数阈值（见 D17）。

组合式（`Expression`）是独立的命名条目：`id`、`name`、`expression`、`enabled`。
表达式采用可嵌套的文本 DSL：`rule_id` / `组合式 id`、`and` / `or` / `not`、圆括号，
`and` 优先级高于 `or`，`not` 只能作用于单个规则或括号组。例：

```
(cn_id or bank_card) and not allow_list
```

表达式在**文件级**求值：叶子规则为真表示该规则在文件任意一行命中；文件级
判定使 `and` / `not` 表达「同一文件同时出现 / 不出现某类内容」的语义，避免
逐行求值时 `not` 对所有无命中行都为真。文件命中后，报告只列出参与表达式且
实际命中的正向规则的逐行命中（`not` 分支仅用于判定，不产出命中明细）。

生效规则：

- 全部 `enabled` 的组合式按 `or` 拼接为生效表达式；
- 没有启用的组合式时回落到默认 `or` 全部启用规则；
- `enabled` 只决定「是否默认生效」，**被引用的未启用组合式 / 未启用规则仍参与
  匹配**：可以只把规则或组合式当构件使用（例如两条规则都未启用、只写
  `phone and not test_phone` 的组合式）；
- 组合式可引用其它组合式（嵌套展开），加载 / 保存时做环检测；
- 规则与组合式共用 id 命名空间（不可重名）；`near[k]` 的操作数必须是规则，
  不能是组合式。

引用了不存在的 id、未知记号、括号不配对、空表达式、重复 id、循环引用一律报
中文错误，不静默忽略。引擎按生效表达式引用集合编译规则，未被引用的规则不参与
计算。

规则集 JSON（`*.filescan.json`，同时接受任意 `.json`）：

```json
{
  "version": 6,
  "name": "示例规则集",
  "rules": [
    {"id": "cn_id", "name": "身份证号", "group": "个人信息", "enabled": true,
     "matcher": {"type": "regex", "pattern": "\\d{17}[\\dXx]", "ignore_case": false}},
    {"id": "test_id", "name": "测试身份证", "group": "测试", "enabled": false,
     "matcher": {"type": "keyword", "text": "test"}},
    {"id": "old_files", "name": "历史文件", "group": "时间", "enabled": true,
     "matcher": {"type": "time", "target": "modified", "compare": "before",
                 "value": "2026-01-01T00:00:00"}},
    {"id": "big_card_dump", "name": "批量卡号", "group": "财务", "enabled": true,
     "matcher": {"type": "regex", "pattern": "\d{16}", "validator": "luhn"},
     "min_hits": 5}
  ],
  "expressions": [
    {"id": "pii", "name": "个人信息", "expression": "cn_id and not test_id", "enabled": true},
    {"id": "base", "name": "基础组合", "expression": "pii", "enabled": false}
  ]
}
```

加载时逐字段校验（未知类型、非法正则、重复 id、未知引用、循环引用、非对象条目
都返回带 id 的中文错误）。内置规则按分组覆盖：个人信息（身份证号）、
联系方式（手机号、邮箱）、财务信息（银行卡号）、凭证（API Key / 访问令牌、
AWS 访问密钥、私钥头、数据库连接串、密码赋值、JWT 令牌、密码哈希
bcrypt / argon2、GitHub 令牌、Slack 令牌、阿里云 AccessKey ID）、
企业信息（统一社会信用代码）、社交账号（微信号），每条都带严重级。
内置集可一键清空、全部禁用、按组禁用。

### D3 扫描范围与匹配引擎

`ScanOptions`：

- `recursive`（默认真）、`max_depth`（0 表示不限）、`follow_symlinks`（默认假；
  为真时用 `resolve()` 去重防止目录环）；
- `skip_dirs`（默认 `DEFAULT_SKIP_DIRS`：`.git` / `.svn` / `.hg` / `node_modules` /
  `__pycache__` / `.venv` / `dist` / `build` / `.cargo/registry` 等版本控制、依赖、
  构建产物与系统目录；按任意层级匹配目录名，含斜杠的条目匹配相对根目录的路径
  后缀；自定义列表整体替换默认值，空列表表示不跳过任何目录）；
- `skip_hidden`（默认假；为真时跳过名称以 `.` 开头的文件与目录，Windows 另含
  隐藏属性。默认关闭以免漏扫 `.env` 这类常见敏感配置）；
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
（带原因），不当作错误；`skip_dirs` / 隐藏目录在目录层剪枝，同样记入 `skipped`
（原因「跳过目录」/「隐藏目录」）并累加 `dirs_skipped`，被剪掉的目录不下探、
其中文件也不进入 `files_seen`。读取按行流式进行，单行超长（> 1 MiB）截断后再匹配并标注。

调度分两级（结果与排序完全一致，只是并行单元不同）：

- **线程池**：小目录（文件数 < `PROCESS_MIN_FILES` 且总字节 < `PROCESS_MIN_BYTES`，
  即小规模扫描）走 `ThreadPoolExecutor`，取消在每个文件前检查 `CancelToken`；
- **进程池**：达到阈值且有多个并发时走 `ProcessPoolExecutor`（`spawn`），
  遍历、统计、排序留在主进程，子进程只做「读 + 解码 + 逐行匹配」，按批分发
  （批大小 `len(files) // (workers * 8)`，上限 64）。进程数不超过 CPU 核数。
  取消只在批次边界检查，取消延迟 ≤ 一个批次；已完成批次照常汇总。

进度回调 `(done, total, path)` 两条路径都在主线程按文件调用，GUI 经队列回主
线程；结果按路径排序，保证可复现。

统计 `ScanStats`：文件总数、已扫描、跳过、错误、读取字节数、命中文件数、
命中条数、耗时，用于吞吐口径（`字节 / 秒`、`文件 / 秒`）。跳过原因按
`files_skipped_extension`（后缀未命中）/ `files_skipped_hidden`（隐藏文件）/
`files_skipped_binary`（二进制）/ `files_skipped_other`（符号链接、大小与修改时间
过滤、超限等其他原因）分别计数，另有 `dirs_skipped` 记录目录剪枝数（不计入文件数）。

### D4 结果、打码与报告

- `RuleMatch`：规则 id/名、严重级、行号（1 起）、列区间、打码片段、原文片段（受
  `include_raw` 控制）、上下文（左右各 `context_chars` 字符）；
- `FileHit`：路径、大小、编码、匹配列表；
- `ScanResult`：命中列表、跳过列表、错误列表、统计、是否取消。

打码规则：命中文本长度 ≥ 6 时保留首 2 与末 2 字符，中间以 `*` 替换；
长度 < 6 时整体替换为 `***`。上下文中的命中区间使用同一打码结果拼接；
`include_raw=True` 时才输出原文。默认 `include_raw=False`，CLI 必须显式
`--raw`，GUI 必须显式勾选「导出 / 复制原文」。

报告：

- 文本：文件路径 + 每条「【严重级】规则 / 行号 / 上下文」，末尾汇总统计与跳过原因；
- JSON：结构化结果（含统计与错误、每条命中的 `severity` 英文枚举），默认打码；
- CSV：每行一条命中（文件、规则、严重级、行号、片段、上下文），严重级用中文标签，
  UTF-8 BOM 便于 Excel；
- SARIF 2.1.0：每条命中一条 `result`（`ruleId` / `level` / 行列区域 / 打码片段），
  `tool.driver.rules` 由实际命中的规则去重生成，`invocations` 带统计与错误通知；
  严重级映射 `info|low → note`、`medium → warning`、`high|critical → error`，
  缺省按 `warning`；表达式命中（无正向明细）用伪规则 `expression` 占位；
  绝对路径输出 `file://` URI，相对路径原样输出。

`filter_result` 支持按规则 id 集合、严重级集合与路径子串过滤（规则与严重级取交集），
过滤后重算命中计数。

### D5 CLI

- `filescan scan PATHS...`：`--rules FILE`（默认内置）、`--expr TEXT`、
  `--no-recursive`、`--max-depth`、`--follow-symlinks`、`--skip-dir NAME`（可重复，
  指定后替换内置跳过列表）、`--skip-hidden`、`--ext-whitelist TEXT`
  （留空用内置，`*` 为全部后缀）、`--include-ext`（白名单内收窄）、
  `--force-ext`（强制扫描名单外后缀）、`--exclude-ext`、`--min-size` / `--max-size`、
  `--modified-after` / `--modified-before`（ISO-8601 或 epoch 秒）、
  `--no-skip-binary`、`--max-file-bytes`、`--workers`、`--format text|json|csv|sarif`、
  `--output FILE`、`--raw`、`--rule-id ID`（可重复）、`--path-filter SUBSTR`、
  `--save-config FILE`（把本次生效参数与规则集存成配置）；
- `filescan run --config FILE`：按配置执行扫描，扫描选项 / 根目录 / 规则 / 报告
  口径全部来自配置；命令行只覆盖 `--format` / `--output` / `--raw` / `--rule-id` /
  `--path-filter`，避免「配置与单项参数谁优先」的歧义；
- `filescan rules`：`--action list|export|validate`、`--output FILE`、`--rules FILE`。

`scan` 找不到命中返回 0（扫描完成即有结论），参数或规则错误返回非 0。
输出文件统一 UTF-8；CSV 带 BOM。

### D6 GUI

侧边栏四页（共享 `fcmd.gui.window.QtMainWindow` + `SidebarNav`，与 wslmanw / llmw 同构，
避免表单堆叠、也不再让顶部标签条挤压横向空间）。按「任务 / 结果 / 规则 / 配置」分层：
扫描页按任务管理目录与扫描动作，选项与白名单这类低频配置收进设置页，表单不再和
动作抢纵向空间。

1. **扫描**：扫描任务列表（行卡与 `wslmanw` 实例页同构）。列表首项始终是虚线框
   「添加任务」；每个任务一个固定目录，标题为任务名（默认目录名，同名自动加后缀），
   副标题为目录路径，最近扫描时间与命中情况（「命中 N 文件 / M 条」或「无命中」）
   都以标签形式紧挨目录显示，折叠时即可一眼看到。行内只保留扫描 / 查看结果两个按钮，
   扫描按钮排在第一个并加宽到普通图标按钮的两倍，扫描中切成「停止扫描」，同一时间
   只跑一个任务，其余行禁用；行卡支持展开收起，详情就地显示完整目录、状态、最近扫描
   时间与精简结果摘要。没有任务时在添加框下方显示居中空态说明。卡片标题行保留当前
   选项的带色标签（递归 / 深度 / 并发 / 白名单 / 缓存），移除 / 清空按钮固定在右上角，
   移除只作用于当前选中任务，两者都不弹确认。
   旧设置只有 `paths` 时按「一个目录一个任务」迁移；`tasks` 写回 `filescanw.json`，
   每项记录最近一次扫描时间 / 摘要 / 状态。每个任务的完整扫描结果（命中原文 / 上下文、
   跳过与错误、统计）按任务 id 落盘到 `ui/filescanw-results.json`，与设置文件分开；
   启动时恢复结果树，扫描完成自动进入结果页，行内「查看结果」也能从任务重新进入；
   结果页标题行提供一个任务下拉按钮 + 弹出菜单（只列已有结果的任务），可直接切换当前
   展示的结果，未产生结果时置灰显示「暂无任务结果」；菜单选取后自动关闭，不会残留展开态。移除 / 清空会同时删掉对应结果条目，
   不删除磁盘目录。
2. **结果**：结果树按文件聚合（父节点路径 + 最高严重级 + 命中数，子节点规则 /
   严重级 / 行号 / 上下文四列；命中内容已在上下文列里用底色标出，不再单占一列），
   严重级按主题色着色（严重 `danger`、高 `warning`、中 `accent`、低 / 提示 `muted`），
   上下文列按列宽换行完整显示
   （行高随内容增长，不再省略成「…」），命中片段用主题 warning 底色 + `on_accent`
   前景单独标出；选中行后可「定位文件」（系统文件管理器选中该文件）、「复制命中
   内容」/「复制上下文」/「复制扫描说明」（逐条生成「【规则：…，匹配项：…】」），
   双击行定位文件，右键行还提供打开文件与复制路径；复制默认打码，勾选
   「导出 / 复制原文」后按原文复制；支持按规则、严重级与路径筛选；
   导出 CSV/JSON/文本（同一开关只影响导出与复制，不改变结果树展示），扫描结束
   后自动切到本页。
3. **规则**：上下两张表 —— **规则表**（启用、分组、严重级、**ID**、名称、匹配器
   摘要；双击行直接编辑）与 **组合式表**（启用、ID、名称、表达式）。规则表支持
   新增/编辑/删除（编辑对话框含严重级与匹配对象下拉）、启用/禁用、载入内置、清空、
   导入/导出/保存规则集；组合式表支持新增/编辑/删除、启用/禁用。规则对话框按
   「类型」切换字段：关键词显示文本与整词选项，正则显示表达式 + 校验器，时间显示
   时间字段 / 条件 / 起止时间（`介于` 才显示结束时间），大小显示条件与起止值；
   另可设置最少 / 最多命中次数阈值；大小写开关按类型
   各记一份（关键词默认不区分、正则默认区分），保存时只带当前类型的字段。关键词
   与正则都用可换行的多行编辑器（按宽度折行、高度 64–96px），换行 / 行首缩进只用于
   排版，保存时合并为单行；切换类型时对话框高度随可见字段自动调整。组合式对话框里可
   直接「插入到表达式」规则 id 或其它组合式 id，保存前校验引用、重复 id 与循环；
   未启用的组合式仍可被引用，卡片下方提示「启用 = 默认生效，禁用只退出默认 OR」。
4. **设置**：三张卡片纵向排布（`PageScroll`）——
   - **扫描范围**：递归 / 跟随符号链接 / 跳过二进制 / 解析文档 / 跳过隐藏文件五个开关 +
     `FormGrid` 两列表单（深度、最小 / 最大字节、单文件上限、并发数、上下文长度、
     后缀包含 / 排除 / 强制、修改时间上下界、跳过目录）；
   - **后缀白名单**：表格「类型 / 后缀」两列，每行一个类型，后缀用英文逗号 `,`
     分隔；行可新增、编辑、删除，支持 Ctrl / Shift 多选后批量删除，编辑时对类型
     重名与后缀逐项校验，非法条目拒绝并提示，删除需二次确认；分组随设置持久化，
     恢复默认重新生成内置分组；
   - **缓存与配置**：「使用缓存」开关（关闭即每次完整读盘）与「清理缓存」/
     「导入配置…」/「导出配置…」（配置见 D10）。
   设置页的控件就是扫描时用的那套值（`_scan_options()` 直接读），不需要额外同步。

设置（目录、选项、规则集、几何位置与侧边栏折叠状态，含未在界面暴露的
`encodings` / `max_hits_per_file` / `use_processes`）持久化到
`~/.fcmd/ui/filescanw.json`；每页是 `AppPage` 子类，状态栏文字按页隔离
（扫描进度 / 结果筛选 / 规则操作 / 白名单与缓存配置各归各页，切页后各自恢复）；
扫描页与设置页都放在 `PageScroll` 里，窗口压矮时滚动而不是裁掉动作行；
结果页表格吃满页面高度，扫描完成自动切到结果页。
扫描在后台线程执行，取消走 `CancelToken`，关窗时置位取消并等待 worker 退出，
不留下悬挂线程。

### D7 安全边界

- 全程只读：仅 `open(..., "rb")` 读取，不创建临时文件、不修改 mtime；
- 报告与复制默认打码，危险动作只有「导出 / 复制原文」一处显式开关；
- 不跟随符号链接默认开启关闭项，避免扫描越出指定目录；
- 单文件读取上限防止误扫超大二进制或设备文件。

### D8 测试与门禁

- `_rules` / `_engine` / `_report` 单测覆盖：表达式解析与优先级、非法输入、
  内置规则、JSON 往返、白名单/收窄/强制/排除、深度/大小/二进制过滤、编码回退、取消、进度、
  打码、三种报告与过滤；
- 进程池路径与线程路径的结果一致性单独成测（`test_scan_paths_process_mode_matches_threads`）；
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

场景：5000×4 KiB 小文件（单规则 / 7 条内置规则）、20×2 MiB 大文件（含
`workers=1/4/8` 的并发维度，用于观察进程池是否绕开 GIL）、8 层深目录
（480 文件）、2000 白名单内 + 3000 白名单外混合目录（分别用白名单与
`ext_whitelist=("*",)` 全后缀跑，输出加速比）。每个场景先校验扫描文件数与
后缀跳过数，防止过滤条件写错导致「虚快」。

本机基线（Windows 11、Python 3.13、`workers=4`、24 核、语料在系统临时目录；
2026-11-12 进程池落地后重测）：

| 场景 | 文件数 | 读取体积 | 耗时 | 文件/秒 | MiB/秒 |
|---|---:|---:|---:|---:|---:|
| small/1rule | 5000 | 19.8 MiB | 0.76 s | 6611 | 26.1 |
| small/builtin（7 规则） | 5000 | 19.8 MiB | 0.62 s | 8031 | 31.7 |
| large/w1（单线程） | 20 | 40.0 MiB | 2.54 s | 7.9 | 15.8 |
| large/builtin（w4） | 20 | 40.0 MiB | 0.89 s | 22.5 | 45.1 |
| large/w8 | 20 | 40.0 MiB | 0.65 s | 30.9 | 61.8 |
| deep/builtin（8 层） | 480 | 971 KiB | 0.12 s | 3966 | 7.8 |
| mixed/whitelist | 2000（另跳过 3000） | 4.0 MiB | 0.53 s | 3811 | 7.5 |
| mixed/all-ext（改前行为） | 5000 | 9.9 MiB | 0.70 s | 7158 | 14.1 |

大文件并发维度：同一 20×2 MiB 语料，单线程 2.54 s → 4 进程 0.89 s（2.9×）
→ 8 进程 0.65 s（3.9×）。小文件 5000 个：4 进程 0.62 s，相对线程路径（1.9 s）
约 3.1×。

白名单加速比：同一混合目录，白名单跳过 3000/5000 个文件后耗时 0.53 s，改为
全后缀逐文件嗅探需 0.70 s，**约 1.3 倍**；扫描文件数本身就是 2.5×，差额来自
被跳过文件仍要 stat，且该场景耗时已接近「首次读取 2000 个新写文件」的冷
缓存 IO 地板。

### D9.1 性能分析结论（2026-11-12）

分阶段计时（4000×3.6 KiB 语料）与 cProfile 结论：

- **文件枚举** `walk_files` 0.025 s（<1%），`os.scandir` 足够快；
- **解码/二进制嗅探** `_looks_binary` 0.004 s、`decode_text` 0.006 s，均非瓶颈；
- **文件读取**曾是最主要热点：`handle.read(max_file_bytes + 1)` 会按 8 MiB
  申请/清零缓冲区，小文件每个都付一次成本，读取 4000 个小文件需 6–8 s；
  改为按 `stat` 实际大小申请（`min(size, max_file_bytes) + 1`）后降到 0.16 s；
- **逐行正则**为次要热点：每行每条规则都创建 `finditer` 迭代器；先
  `regex.search(line)` 判有无、命中后再 `finditer`，匹配集合与顺序不变，
  匹配阶段耗时下降约 25%；
- **线程调度**：GIL 下正则匹配无法跨线程并行，`workers=4` 相对 1 线程仅约
  1.1×，主要收益来自 I/O 重叠（详见 D9.2）。

未采纳的方案（收益不足或改变语义）：文件级 `(?m:)` 联合预筛实测约 10%、
逐行联合预筛约 17%，需处理 `\A`/`\Z`、内联 flag、CRLF 等边界，风险高于收益；
关键词字面量预筛实测无收益；把逐行匹配改为整段 `finditer` 会改变 `\s`、`\A`、
`\Z` 与 CRLF 语义。

`--check` 以 `BASELINES` 为参照，文件/秒或 MiB/秒低于基线的 `--min-ratio`
（默认 50%）即判回归并返回非零；阈值刻意留宽，用于挡「数量级」退化而非
抖动。基线绑定本机与 `workers=4`，换机器/CI 需按实测回填。

### D9.2 绕开 GIL 的评估与结论（2026-11-12）

**先量化瓶颈归属。** 同机 4000×3.6 KiB / 7 条内置规则、页缓存已热：

| 场景 | workers=1 | 2 | 4 | 8 | CPU/墙钟（w4） |
|---|---:|---:|---:|---:|---:|
| 5000×4 KiB | 2.27 s | 1.97 s | 1.95 s | 1.93 s | 1.33 |
| 20×2 MiB | 2.61 s | 3.01 s | 3.03 s | 2.82 s | 1.03 |
| 4×16 MiB | 4.12 s | 4.66 s | 4.49 s | 4.50 s | 1.02 |

单线程 cProfile：`re.Pattern.search` 1.27 s（56%）、`_collect_matches` 自身
0.33 s、读写与 `stat` 约 0.25 s，解码 0.012 s、`splitlines` 0.028 s。也就是说：
**枚举与解码都不是瓶颈，真正的 CPU 段是「逐行 × 逐规则」的正则扫描**；线程
路径只在大文件上略微变差（CPU/墙钟 ≈ 1.0，全是 CPU，没有可重叠的 I/O）。

**各方案实测（同语料，取 3 次最优）：**

| 方案 | 5000 小文件 | 20×2 MiB | 结论 |
|---|---:|---:|---|
| 线程池 w4（现状，改前） | 1.92 s | 2.73 s | 基准 |
| 进程池 w4（按批） | 0.61 s（3.1×） | 0.85 s（3.2×） | **采纳** |
| 进程池 w8 | 0.57 s（3.3×） | 0.65 s（4.3×） | 同上，随核数继续涨 |
| 合并规则为单次扫描 | — | — | 反而慢：1759 ms vs 1132 ms（交替式正则每次都要试全部分支） |
| 字节级匹配（不解码） | — | — | 只省解码 12 ms（0.5%），且破坏编码回退语义 |
| 自由线程 3.14t 正则并行 | — | — | 4 线程 3.22×、8 线程 5.44×（纯正则段），单线程比 3.13 慢 17% |

**进程池的具体成本（本机实测）**：4 进程 spawn + 子进程导入 `fcmd` 约
0.10 s；协议序列化 5000 个结果约 645 KiB；按批（16–64）相对按文件只差
13%；池复用时第二次扫描 0.42 s。因此用「文件数 ≥ 512 或总字节 ≥ 2 MiB」做
门槛：小目录继续走线程池，不付 spawn 固定成本；取消延迟被批大小限制在
一个批次内。扫描结果与线程路径逐条一致（`test_scan_paths_process_mode_matches_threads`
+ 600 文件语料对拍快照）。

**没做的部分（明确不做，不是遗漏）：**

- **单独拆「读+正则」到子进程、遍历与统计留主进程**：这正是已实现的分工，
  没有第二种拆法能进一步降本；
- **自由线程作为可选路径**：现有线程池在自由线程解释器上会自然获得 3–5×，
  无需改代码；但项目 venv 是常规 GIL 构建，且 3.13t 生态（C 扩展）尚不普及，
  故只作为文档结论，不切默认解释器；
- **提高默认 `workers`**：GIL 下无效（1→8 线程只有 1.1×），进程池路径已按
  CPU 核数封顶，改默认值收益为零；
- **mmap**：读取仅占单线程耗时约 10%（读 0.105 s / 开 0.099 s），且已按实际
  大小申请缓冲，mmap 最多省一次拷贝，低于测量噪声。

### D10 扫描配置导入 / 导出（2026-11-12）

CLI 与 GUI 需要「一套配置反复用」，且团队内可直接传一个文件。配置由
`_config.py` 统一读写，CLI / GUI 共用同一份字段与容错口径。

配置文件（JSON，`version` 当前为 2，省略时按旧版本处理）：

```json
{
  "version": 2,
  "roots": ["D:/data"],
  "rules": { "version": 2, "name": "内置敏感规则", "rules": [], "expressions": [] },
  "options": {
    "recursive": true, "max_depth": 0, "follow_symlinks": false,
    "ext_whitelist": [".txt", ".log"],
    "include_exts": [], "force_exts": [], "exclude_exts": [],
    "min_size": 0, "max_size": null,
    "modified_after": "2026-01-01T00:00:00", "modified_before": null,
    "skip_binary": true, "scan_documents": true,
    "max_file_bytes": 8388608, "encodings": ["utf-8", "gb18030", "latin-1"],
    "workers": 4, "context_chars": 40, "max_hits_per_file": 200, "use_processes": true
  },
  "report": { "mask": true, "format": "text" }
}
```

| 分组 | 字段 | 说明 |
|---|---|---|
| 顶层 | `version` | 配置版本号，当前 2 |
| | `roots` | 根目录数组（CLI `run` 必填） |
| | `rules` | 内嵌规则集（与 `filescan rules --action export` 同构） |
| | `rules_path` | 外部规则集引用（相对配置文件目录，`rules` 优先） |
| | `report.mask` | 真=报告打码，假=输出原文（CLI `--raw` / GUI「导出 / 复制原文」的反面） |
| | `report.format` | `text` / `json` / `csv` / `sarif` |
| `options` | `recursive` / `max_depth` / `follow_symlinks` | 遍历范围 |
| | `skip_dirs` / `skip_hidden` | 跳过目录名列表（空数组表示不跳过，缺失用内置）/ 是否跳过隐藏文件与目录 |
| | `ext_whitelist` / `include_exts` / `force_exts` / `exclude_exts` | 后缀白名单与收窄 / 强制 / 排除（接受数组，也接受分号串） |
| | `min_size` / `max_size` | 大小过滤；`max_size` 为 `null` 或 `0` 表示不限 |
| | `modified_after` / `modified_before` | 修改时间过滤，ISO-8601 文本或 epoch 秒，边界含端点 |
| | `skip_binary` / `scan_documents` | 跳过二进制 / 是否解析 docx·xlsx·pptx·odt·ods·pdf |
| | `max_file_bytes` | 单文件读取上限 |
| | `encodings` | 解码候选（未知编码逐项丢弃） |
| | `workers` / `use_processes` | 并发数 / 是否允许进程池调度 |
| | `context_chars` / `max_hits_per_file` | 上下文长度 / 单文件命中上限 |

**规则是否内嵌：内嵌。** 导出时把规则集整份写进 `rules`，配置文件单文件即可
复用，不会因为换机器、移动目录或相对路径失效而丢掉规则；GUI 导入后规则页
直接回填。代价是配置文件体积随规则数增长（内置 7 条约 3 KiB，可忽略），且
规则与扫描选项绑在一起、不能「同一规则集配多套范围」——需要时仍可手工用
`rules_path` 引用外部规则集，两种写法都接受，`rules` 优先。

**兼容策略（逐字段容错，不整份作废）：**

- 只有「文件读不到 / 不是合法 JSON / 顶层不是对象」才整份失败，给出中文错误；
- 缺字段一律回落默认值；未知字段忽略；`version` 缺失按 1，高于当前版本只提示
  「未知字段忽略、缺失字段用默认」，仍尽力加载；
- 每个字段独立校验：类型不符、越界、未知编码、非法时间、空白名单、非法
  `report.format` 等只把该字段回落默认值，并在 `warnings` 里记一条带字段路径
  与原始值的中文提示（如 `options.workers 不能小于 1，实际为 -5，已使用默认值 4`），
  其余字段照常生效；时间上下界颠倒时同时忽略两端；
- 内嵌规则集或 `rules_path` 解析失败视为「规则」这一个字段失败：提示后回落
  内置规则，扫描选项不受影响；
- CLI `run` 把提示逐条打到 stderr 后继续执行；GUI 导入后弹窗列出全部提示；
- 导出永远写当前 `CONFIG_VERSION`，并内嵌规则集，保证「导出 → 再导入」往返
  稳定（`test_save_and_load_config_roundtrip`、`test_roundtrip_preserves_options_and_rules`）。

**入口：**

- CLI：`filescan scan … --save-config FILE` 存当前生效参数；`filescan run
  --config FILE` 加载执行（命令行只覆盖输出类参数，避免优先级歧义）；
- GUI：扫描页「导入配置…」按字段回填各控件（目录、开关、白名单表格、规则与组合式、
  规则页），「导出配置…」把当前界面状态（含未暴露的 `encodings` /
  `max_hits_per_file` / `use_processes`，由设置持久化承载）写出。

### D11 扫描结果持久化缓存（2026-11-12）

重复扫描同一批目录时，绝大多数文件在上次扫描后并未变化，却仍要完整读盘 +
逐行匹配。`_cache.py` 为此提供持久化缓存：命中的文件直接复用上次结果，
未命中的文件也不再重读。

**存储选型：SQLite。** 单文件 `${FCMD_HOME:-~/.fcmd}/cache/filescan-cache.db`：

```sql
CREATE TABLE meta   (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE entries (
    cache_key TEXT NOT NULL, path TEXT NOT NULL,
    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
    stored_at INTEGER NOT NULL,
    encoding TEXT NOT NULL, has_hit INTEGER NOT NULL, matches TEXT NOT NULL,
    PRIMARY KEY (cache_key, path)
);
CREATE INDEX entries_stored_at ON entries(stored_at);
```

选它的理由：并发写有成熟的事务与锁语义（索引文件需要自己实现原子替换与合并）；
单条 `INSERT OR REPLACE` 即可增量更新，不必整份重写；增量删除 / 清理（`DELETE`）
天然支持；损坏检测（`DatabaseError`）与「删文件重建」都很直接。代价是需要 SQLite
（Python 标准库自带，无额外依赖）与多次扫描会增长的单文件体积，`clear` 与
``--clear-cache`` 都已提供。

**复用条件（防止陈旧结果）：**

- 同一路径的 `size` 与 `mtime_ns` 都与缓存一致，且 `cache_key` 一致才复用；
  `size` / `mtime_ns` 任一变化即重扫。内容变化必然改变二者之一（写入即更新
  mtime，即使长度相同），因此不会返回陈旧结果；
- 不做全量内容哈希：哈希需要完整读盘，等于把「省一次读」变成「再读一次」；
- `has_hit` 区分「实扫过但无命中」（也缓存，避免每次重读）与「命中但无明细」
  （表达式级命中、无正向规则明细），后者不会被误算进 `hit_files`。

**有效期与容量（防止无限增长）：**

- `stored_at` 记录写入时间（epoch 秒）；查询只认 `stored_at >= now - CACHE_TTL_SECONDS`
  （默认 7 天）的条目，过期视同未命中，下次扫描重扫并刷新；
- 写入时顺带删除过期行；条目总数超过 `CACHE_MAX_ENTRIES`（默认 10 万）时按
  `stored_at` 最旧优先裁剪，单文件体积随之有界；
- 表结构版本升到 2：打开时按 `meta.schema` 判定，版本不符则删表按当前结构重建
  （旧条目一次性失效，不尝试逐列迁移）。

**指纹口径（`cache_fingerprint`）：**

| 参与（改变即失效） | 不参与（只影响扫哪些文件或怎么输出） |
|---|---|
| 完整规则集映射：规则增删改、启用状态、匹配器、表达式 | 递归 / 深度 / 符号链接 |
| `skip_binary` / `scan_documents` / `max_file_bytes` | 后缀白名单、包含 / 强制 / 排除后缀 |
| `encodings` / `context_chars` / `max_hits_per_file` | 大小与修改时间过滤 |
| `mask`（打码 / 原文口径决定 `matches` 内容） | `workers` / `use_processes` / 报告格式 |

摘要为 `sha256`（键排序的 JSON），不同配置在库里共存，切回旧配置仍能命中。
规则集的 `name` 也在映射内，改名同样会失效——这是有意的保守取舍。

**并发安全：**

- 只有扫描的**主进程**打开连接并读写；线程池与进程池的 worker 只返回结果，
  不接触数据库。进程池调度下不会出现多进程竞争写；
- 连接使用 WAL + `synchronous=NORMAL` + `busy_timeout=5000`，多个 fcmd 进程同时
  扫描时写锁等待，超时即降级为「本次不写缓存」，扫描结果不受影响；
- 读路径 `lookup_many` 分批查询（每批 400 条路径），避开 SQLite 变量上限。

**损坏降级：**

- 打开或建表失败（文件损坏 / 不是 SQLite）：关闭句柄、删掉 `.db` / `-wal` / `-shm`
  后重建一次；
- 读写中途报 `sqlite3.Error`：同样删库重建并重试一次，仍失败则本次禁用缓存；
- 目录不可写、磁盘满等 `OSError`：直接禁用缓存，`ScanCache.error` 记中文原因，
  CLI 打到 stderr、GUI 显示在状态栏，**扫描继续**；
- 引擎侧 `_take_cached` / `_store_fresh` 再包一层异常兜底：任何缓存异常都不会
  中断扫描。

**入口与可见性：**

- CLI：`filescan scan` / `filescan run` 默认启用，`--no-cache` 关闭，
  `--clear-cache` 扫描前清空并打印条数；
- GUI：扫描页「使用缓存」勾选框（随设置持久化）与「清理缓存」按钮；
- 文本报告概要与 JSON `stats` 都给出 `files_reused`，摘要行为
  `文件 N（实扫 X / 缓存复用 Y）`，GUI 状态栏同口径。

**实测（Windows 11 / Python 3.13 / workers=4，5000 个 4 KiB 文件，共 19.8 MiB）：**

| 场景 | 实扫 | 复用 | 耗时 | 相对首次 |
|---|---|---|---|---|
| 首次扫描 | 5000 | 0 | 1.151 s | 1.0x |
| 二次扫描（全命中） | 0 | 5000 | 0.062 s | **18.6x** |
| 改一个文件 | 1 | 4999 | 0.069 s | 16.7x |
| 改规则（新增一条） | 5000 | 0 | 0.684 s | 1.7x |
| 改上下文长度 40→120 | 5000 | 0 | 0.727 s | 1.6x |
| 仅改并发数 4→8 | 0 | 5000 | 0.078 s | 14.8x（如预期仍复用） |
| 切换打码口径 | 5000 | 0 | 0.655 s | 1.8x |

三种失效行为均符合预期：改文件只重扫该文件，改规则 / 改上下文长度 / 切换打码
全部实扫；而只改并发数不影响命中，继续全部复用。

### D12 结果聚合口径：范围外 vs 读取后跳过（2026-11-13）

**问题反馈：** 扫描结果「全是跳过」或「文档无文本层」，看不到正常命中。

**真实复现（用户本地设置 + 真实目录）：** 读取
`~/.fcmd/ui/filescanw.json`（白名单只勾「文档 + 数据/结构化」两组，共 20 个后缀；
规则只启用 `cn_id`），扫描 `D:/devspace`：

| 指标 | 值 |
|---|---|
| 文件（`files_seen`） | 4909 |
| 实扫 / 缓存复用 | 24 / 0 |
| 命中文件 / 命中条数 | 0 / 0 |
| 旧口径「跳过」 | 4885（后缀 4881 / 二进制 0 / 其他 0），另有 4 个文档无文本层 |

用同一批 13951 条缓存记录逐个提取文本、套 `cn_id` 正则复核，确认这 4 个 PDF
确实是图片型无文本层，其余 4881 个 `.py/.js/.json/.md/.txt` 等**根本不在白名单**，
从未被读取。即：0 命中不是漏报，问题出在**统计聚合与展示口径**。

**根因：**

- `_engine._classify_entry` 对白名单外文件累加 `walk.skipped_extension`，同时计入
  `files_seen`；`_finalize_stats` 把这些「从未读取」的文件与大小 / 时间过滤
  （原 `walk.skipped_size`）一起塞进单一 `files_skipped`，再与「二进制 / 其他」
  并列展示；
- `_report._summary_line` 与 GUI `_finish_scan` 都把它统称为「跳过」，且 0 命中时
  不给任何诊断。于是用户看到的是「文件 4909，命中 0，跳过 4885」——绝大多数是
  **范围外**、根本没参与扫描的文件，却被读成「扫了但全跳过」；
- GUI 状态栏还缺少 `no_text / encrypted / corrupt / unsupported` 分类，
  与 CLI 口径不一致。

**修复：**

- `ScanStats` 增加 `files_skipped_size_time`，并由派生属性拆分：
  `files_out_of_scope = 后缀白名单外 + 大小/时间过滤 + 隐藏文件`（从未读取），
  `files_skipped_after_read = files_skipped - files_out_of_scope`
  （二进制 / 文档四类 / 其他）；
- 新增 `_report.scan_summary(stats, compact=...)` 与 `no_hit_hint(stats)`，
  CLI 文本 / JSON 与 GUI 状态栏**共用**同一实现：
  `范围外 N（后缀白名单 X / 大小或时间 Y[ / 隐藏 Z]）；读取后跳过 M（二进制 … / 其他 … / 无文本 …）[；跳过目录 D 个]`
  （隐藏文件与目录剪枝只在非零时出现，默认概要不被拉长）；
- 0 命中且未取消时追加可操作提示：已检查文件数、范围外数量、读取后跳过与无文本层
  数量，并提示检查后缀白名单与规则启用状态；
- JSON `stats` 增加 `files_skipped_size_time`、`files_skipped_hidden`、`dirs_skipped`、
  `files_out_of_scope`、`files_skipped_after_read`，既有字段语义保持不变
  （`files_skipped` 仍为文件总数，不含目录剪枝）。

**口径约定：** 大小 / 修改时间过滤属于「范围外」（从未读取），不再计入「其他」；
`files_skipped_other` 只统计读盘（或解析）之后才发生的其它跳过。

### D13 文件名规则（2026-11-24）

规则匹配器增加 `target` 字段（`content` / `filename`，默认 `content`，JSON 中与默认
一致时省略）。`filename` 规则由引擎在读取内容前对 `path.name` 求值：命中行号固定
`0`、位置记为「文件名」，上下文是被截取的完整文件名（左右各取 `context_chars`）。

- 文件名规则与正文规则共用表达式与文件级求值：两者的命中 id 合并后统一判定
  `and` / `or` / `not`，`(body and not marker)` 这类跨对象组合天然成立；
- 只对进入扫描范围（通过遍历与后缀 / 大小 / 时间过滤）的文件求值，范围外文件
  不参与；
- 内容因二进制、单文件超限、文档加密 / 损坏 / 无文本层而跳过时，只要表达式由
  文件名命中成立，仍记为命中（`encoding` 为空、大小取遍历阶段的值），不再计入
  跳过；这使「按文件名找敏感资料」不受内容可读性影响；
- 文本报告对 `line_no == 0` 的命中显示 `@ 文件名：…` 而不是「第 0 行」；CSV / JSON
  沿用 `line=0`、`location=文件名`；GUI 结果树的位置列显示「文件名」；
- GUI 规则对话框新增「匹配对象」下拉，规则表摘要以「文件名 · …」区分。

### D14 文本归一化（2026-11-24）

扫描选项新增 `normalize_text`（默认开启，CLI `--no-normalize-text` / 配置
`options.normalize_text` / GUI「文本归一化」可关）。匹配前对每行做 NFKC 归一化，
使全角字母数字、`①`、`㈱` 等兼容字符按等价形式参与匹配。

- 纯 ASCII 行直接跳过归一化（快速路径），不产生额外分配；
- 命中区间按「基字符 + 组合记号」簇建立「归一化边界 → 原文偏移」映射，
  `excerpt` / `context` 仍从原文切片，报告与 GUI 展示的是用户看到的字符；
- 极少数跨簇组合（如韩文初声 / 中声分离）与整行归一化不一致时退化为近似映射，
  只影响极端片段的截取范围，不影响命中判定；
- 该选项参与缓存指纹（开启 / 关闭会产生不同命中，不能复用旧缓存）；
- 文件名规则不参与归一化（保持文件名原样匹配，见 D13）。

### D16 时间规则（2026-11-25）

规则新增 `kind=time`：按文件时间戳判定，不读取内容，也不做正则匹配。

- `target`：`modified`（`st_mtime`）/ `created` / `accessed`（`st_atime`）；
- `compare` + `value` / `value2`：ISO-8601 或 epoch 秒，口径为半开区间
  `before: t < value`、`after: t >= value`、`between: value <= t < value2`；
- 创建时间优先 `st_birthtime`，Windows 用 `st_ctime`（创建时间）；Linux 无
  birthtime 时退化为 `st_ctime`（元数据变更时间），GUI / 文档明确提示；
- 引擎在读内容前 `stat` 一次求值，命中 `line_no=0`、`location` 为时间字段
  （修改时间 / 创建时间 / 访问时间），`RuleMatch.sensitive=False`，报告 / 结果树 /
  复制都不打码；
- 内容因二进制 / 加密被跳过时时间命中照常报告；与内容 / 文件名规则在同一表达式
  里组合（`and` / `or` / `not`）；
- `near[k]` 的操作数必须是内容 / 文件名规则，引用时间规则在加载 / 保存时直接报错；
- 全局 `modified_after` / `modified_before` 仍是遍历期剪枝，范围外的文件不会被
  时间规则命中。

### D17 路径 / 大小 / 校验器 / 命中阈值（2026-11-25）

- **路径规则**：`target=path`，对完整路径文本做关键词 / 正则匹配，命中
  `line_no=0`、`location=路径`；用于按目录 / 项目限定范围，也可进组合式做 `not`。
- **大小规则**：`kind=size`，`compare=lt/gt/between` + `value` / `value2`；解析
  `10KB` / `1.5MB` / 裸字节数（1024 进制），区间 `[start, end)`。命中
  `location=文件大小`、`sensitive=False`。
- **校验器**：`regex` 的 `validator` 作用于每个命中片段（Luhn、身份证、统一社会
  信用代码、IBAN），不通过的不计入命中次数与明细，用于显著降低误报。
- **命中阈值**：`Rule.min_hits`（默认 1）与 `Rule.max_hits`（0 表示不限）在文件级
  聚合后过滤规则 id；阈值不满足时该规则视为未命中，命中明细同步过滤。计数按
  实际有效片段数（不受 `max_hits_per_file` 截断影响）。
- 规则集 `version` 升到 4；`min_hits` / `max_hits` 与默认一致时不写入 JSON。

### D18 文件类型与归档成员（2026-11-25）

- **文件类型规则** `kind=filetype`：按魔数识别真实类型（`text` / `pdf` / `zip` /
  `gzip` / `7z` / `rar` / `exe` / `ole` / `image` / `other`），`value` 为逗号分隔的
  类型集合；命中 `location=文件类型`、`sensitive=False`，可发现扩展名与内容不符的文件。
- **归档成员规则** `target=archive_path`：对 zip / tar 的成员路径做关键词 / 正则匹配
  （只读成员名，不解压内容，单归档上限 5000 项），命中 `location=归档成员`。
- 两者都属于元数据阶段：内容被跳过 / 二进制时仍照常报告；`near[k]` 不参与。
- 规则集 `version` 升到 5；D19 再升到 6。GUI 规则类型下拉改用中文名称（关键词 / 正则 / 时间 /
  文件大小 / 文件类型），内部仍存稳定 id。

### D19 元数据 / 词典 / 哈希 / 高熵 / 风险与加白（2026-11-25）

- **文档元数据** `kind=metadata`：读取 docx/xlsx/pptx 的 `docProps`（作者、最后修改者、
  标题、主题、公司），`value` 为字段、`text` 为包含匹配（忽略大小写）；位置
  `元数据:字段`。
- **自定义词典** `kind=dictionary`：`value` 为换行 / 逗号分隔词条，编译为正则交替
  （忽略大小写），适合客户名单、项目代号等大词表。
- **文件哈希** `kind=hash`：`value` 为 SHA-256 列表，按文件内容摘要精确匹配；位置
  `文件哈希`，`sensitive=False`。
- **高熵串** `kind=entropy`：`value` 为 Shannon 熵阈值（0–8]，`value2` 为最小长度
  （默认 24），扫描随机 / 加密风格字符串。
- **风险分**：`_report.file_risk_score` 按命中严重级（提示 1 / 低 2 / 中 4 / 高 8 /
  严重 12）汇总并封顶 100，文本报告与结果树文件行展示。
- **一键加白**：结果页右键「加入路径排除白名单」→ `add_path_allowlist` 新增禁用的
  路径规则并把它追加到启用组合式（`(...) and not allow_x`），下次扫描生效；不改写
  历史结果。
- 规则集 `version` 升到 6；GUI 类型下拉新增中文名称：文档元数据 / 自定义词典 /
  文件哈希 / 高熵串。

### D15 邻近表达式 near[k]（2026-11-24）

表达式新增二元（可链式）邻近算子：``a near[k] b`` / ``a within[k] b``，表示
若干规则命中出现在跨度不超过 ``k`` 行的窗口内，``k`` 省略时默认 1（同行或相邻
行）。优先级从高到低：``not`` > ``near`` > ``and`` > ``or``。

- 例：``password near[3] cn_phone`` 表示密码与手机号出现在 3 行窗口内；
- 为避免歧义，``near`` 的每一项必须是规则 id（不支持括号嵌套 ``and`` / ``or`` /
  ``not``）；同一链上的 ``k`` 必须一致，否则报错；
- 求值依赖行号：引擎只对 ``near`` 中引用的规则额外记录命中行号（其余规则仍只记
  id 与明细，避免大文件保存无用的行号集合）；``evaluate_expression`` 兼容旧的
  「id 集合」入参，含 ``near`` 而传入集合时明确报错而不是静默给出错误结论；
- 文件名规则命中记为第 0 行，参与 ``near`` 时按 0 行计算（与正文行距离较远时
  通常不成立，符合直觉）；
- GUI 表达式标题与占位提示同步说明 ``near[k]``。

## 验收

- 对含身份证号/手机号/邮箱/API Key 的样例目录扫描，命中文件、规则、行号与
  上下文正确，默认打码；
- `(a or b) and not c` 等嵌套表达式按文件级语义判定，非法表达式给出中文错误；
- 递归深度、后缀白名单/收窄/强制/排除、大小上下限、修改时间上下界、跳过二进制、
  跳过目录（内置列表按名匹配，含斜杠条目按相对路径后缀）、跳过隐藏文件（默认关闭）、
  单文件上限均生效；统计区分「范围外（后缀白名单 / 大小或时间 / 隐藏）」与「读取后跳过
  （二进制 / 文档无文本层等）」，并单列目录剪枝数，0 命中时给出可操作诊断（见 D12）；
- 取消后返回已完成部分且不写任何文件；
- CSV / JSON / 文本 / SARIF 四种报告可导出，`--raw` 才含原文；严重级在文本 / CSV 用中文标签、
  JSON 用英文枚举、SARIF 用 `note|warning|error`，旧规则集缺省按 `medium` 处理；
- GUI 可添加多目录、编辑白名单、载入内置规则、编辑/启停规则、查看聚合结果、
  按规则 / 严重级 / 路径筛选与导出；结果行按严重级着色，可定位文件并复制命中内容 /
  上下文（默认打码）；
- `filescan scan --save-config` 导出的配置可被 `filescan run --config` 原样复用；
  GUI 导入后各控件回填、导出再导入不漂移；旧配置缺字段回落默认，非法值逐字段
  提示而不作废整份配置（见 D10）；
- 重复扫描同一批文件时未变化文件直接复用（文件大小 / 修改时间 / 规则与命中相关
  选项任一变化即重扫）；CLI 与 GUI 都能开关与清理缓存，报告区分「实扫 / 缓存复用」；
  缓存损坏时自动重建且不中断扫描（见 D11）；
- 文件名规则（`target=filename`）与正文规则可在同一表达式中组合，文件名命中即使
  内容不可读（二进制 / 加密文档）也照常报告（见 D13）；
- 数千文件目录有进度且可取消，给出本机实测文件数、体积、耗时与吞吐（见 D9）。

## 落地状态

- 设计：已定稿（本 ADR）。
- 纯逻辑引擎：已落地（`_rules` / `_engine` / `_report`，含后缀白名单与跳过分类统计）。
- CLI：已落地（`scan` / `run` / `rules`，含 `--ext-whitelist` / `--force-ext` /
  `--save-config`）。
- GUI 与收尾：已落地（`filescanw.py`，扫描 / 结果 / 规则 / 设置四页布局 + 设置
  持久化 + 配置导入 / 导出；动作与配置分层见 D6）。
- 性能基准：已落地（`bench_filescan.py` + `make bench-filescan`，基线见 D9）。
- 性能优化：已落地（读缓冲按文件实际大小申请、逐行先 `search` 后 `finditer`，
  优化前后结果快照逐字节一致，见 D9.1）。
- 进程池调度：已落地（达到工作量阈值时用 `ProcessPoolExecutor` 绕开 GIL，
  结果与线程路径逐条一致，见 D9.2）。
- 扫描配置：已落地（`_config.py` + CLI `run`/`--save-config` + GUI 导入导出，
  字段与兼容策略见 D10）。
- 修改时间过滤：已落地（`ScanOptions.modified_after/before`，遍历阶段计入
  `files_skipped_size_time`，属「范围外」）。
- 扫描缓存：已落地（`_cache.py` + CLI `--no-cache`/`--clear-cache` + GUI 开关与
  清理，选型 / 指纹 / 并发与损坏降级 / 实测见 D11）。
- 结果页上下文展示：已落地（`_report.context_segments` 把上下文切成「命中前 /
  命中 / 命中后」三段，与 `render_context` 共用打码口径；`_ContextDelegate` 按列宽
  换行完整显示并给命中段加 warning 底色，行高随内容增长，列宽变化时重排版；命中
  内容不再单占一列，结果树为「文件 / 规则、行号、上下文」三列）。
- 结果行操作：已落地（`_PATH_ROLE` / `_MATCH_ROLE` 把路径与命中绑到行上，双击 /
  按钮 / 右键菜单定位文件（`reveal_command`：explorer `/select` / Finder `-R` /
  xdg-open），复制命中内容 / 上下文 / 文件路径走剪贴板，默认打码、勾选
  「导出 / 复制原文」后取原文；`_report.match_note` 生成
  「【规则：…，匹配项：…】」扫描说明，与其它复制动作共用选中行与打码开关）。
- 结果聚合口径：已落地（`ScanStats.files_out_of_scope` / `files_skipped_after_read`
  + `_report.scan_summary` / `no_hit_hint` + GUI `scan_status_text` 共用，
  0 命中诊断与真实复现见 D12）。
- 文件名规则：已落地（`Matcher.target` / `CompiledRule.matches_filename` +
  引擎 `_match_filename`，报告与 GUI 规则对话框同步，见 D13）。
- 元数据 / 词典 / 哈希 / 高熵 / 风险与加白：已落地（`_metadata`、`_validators`、
  `_report.file_risk_score`、`add_path_allowlist`，见 D19）。
- 文件类型 / 归档成员：已落地（`_filetype` + `_engine._match_filetype` /
  `_match_archive`，GUI「文件类型」类型与「归档成员路径」目标，见 D18）。
- 路径 / 大小 / 校验器 / 命中阈值：已落地（`_matcher` 路径与大小类型、
  `_validators` 校验器、`_engine._match_size` / `_apply_thresholds`，GUI 类型切换
  与阈值输入同步，见 D17）。
- 时间规则：已落地（`Matcher.kind=time` + `_timeutil` 解析 / 格式化 +
  `CompiledRule.time_start/time_end` + 引擎 `_match_times`，GUI 类型切换与
  规则表摘要同步，见 D16）。
- 文本归一化：已落地（`ScanOptions.normalize_text` + 引擎 `_normalize_line`
  边界映射，CLI / 配置 / GUI 均可关闭并参与缓存指纹，见 D14）。
- 邻近表达式：已落地（`NearExpr` + `near_rule_ids` + 引擎行号收集与滑动窗口
  求值，见 D15）。
