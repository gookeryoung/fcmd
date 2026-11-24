# filescan 敏感内容扫描

`filescan` 扫描一个或多个目录，按关键词 / 正则 / 文件名 / 路径 / 时间 / 大小 / 元数据 / 词典 / 哈希 / 高熵规则判定命中，再按文件聚合输出命中行与上下文。默认打码，只读不修改文件；需要原文时显式加 `--raw`。

GUI 版 `filescanw` 复用同一套扫描引擎、规则集与配置格式，本文示例中的参数与规则可直接在窗口里对应操作。设计与边界见 [ADR 0019](../../../docs/adr/0019-filescan.md)。

## 快速开始

```powershell
# 扫描当前目录，输出文本报告（默认打码）
filescan scan .

# 只扫描日志与文本，结果写 JSON
filescan scan D:/data --include-ext ".txt;.log" --format json --output report.json

# 用场景模板扫描（先看有哪些模板）
filescan rules --action scenarios
filescan scan D:/data --scenario privacy-contact --format text
```

只看不改：扫描不会创建临时文件，也不会改被扫文件；报告默认把命中片段打码。

## 三个子命令

| 子命令 | 作用 | 典型入口 |
|---|---|---|
| `scan` | 扫描目录；规则、过滤、报告参数都在本次命令行给出 | `filescan scan D:/data --rules my-rules.json` |
| `run` | 按 `scan --save-config` 生成的配置执行，命令行只覆盖报告输出 | `filescan run --config team.json` |
| `rules` | 列出 / 校验 / 导出规则集，或列出场景模板 | `filescan rules --action export` |

### scan：扫描目录

```powershell
# 基本扫描
filescan scan D:/data

# 指定规则集，并用表达式组合（文件级：同一文件同时满足）
filescan scan D:/data --rules my-rules.json --expr "cn_id and not allow_list"

# 只保留指定规则 id / 路径包含关键字的命中
filescan scan D:/data --rule-id cn_id --rule-id bank_card --path-filter "customer"

# 按时间与大小过滤（ISO-8601 或 epoch 秒）
filescan scan D:/data --modified-after 2026-01-01 --min-size 1024 --max-size 10485760

# 输出 SARIF，接入静态分析工具
filescan scan D:/data --format sarif --output filescan.sarif
```

常用扫描范围参数：

| 参数 | 说明 |
|---|---|
| `--no-recursive` / `--max-depth N` | 只扫根目录 / 限制下探层数（0 表示不限） |
| `--include-ext ".txt;.log"` | 在白名单内收窄后缀 |
| `--force-ext ".pdf"` | 强制扫描白名单外的后缀（逃生口） |
| `--exclude-ext ".min.js"` | 排除后缀，优先级最高 |
| `--skip-dir node_modules` | 追加跳过目录，可重复；默认已跳过 `.git` / `node_modules` / `__pycache__` 等 |
| `--skip-hidden` | 跳过隐藏文件与目录（默认不跳过，避免漏掉 `.env`） |
| `--no-skip-binary` | 不按二进制跳过（一般不需要） |
| `--workers 8` | 并行线程数 |

### run：复用配置

```powershell
# 把本次参数、根目录与规则集存成配置
filescan scan D:/data --include-ext ".log" --expr "cn_id" --save-config team.json

# 之后按配置执行；--format / --output 可临时覆盖
filescan run --config team.json
filescan run --config team.json --format csv --output hits.csv
```

配置里的 `roots`、扫描选项、规则集与 `report.mask` 都以文件为准；命令行非法字段会逐条中文提示并回落默认值。

### rules：规则集与场景

```powershell
# 列出当前规则集与生效表达式
filescan rules

# 校验规则集（引用、环、正则都检查）
filescan rules --action validate --rules my-rules.json

# 导出内置 / 指定规则集，供 GUI 或团队复用
filescan rules --action export --output filescan-rules.json
filescan rules --action export --scenario privacy-contact --output privacy.json
```

## 场景模板示例

场景模板随包发布，适合先跑通再按团队口径改规则：

```powershell
filescan rules --action scenarios
filescan scan D:/repo --scenario code-security
filescan scan D:/repo --scenario dependency-audit --format json
filescan scan D:/logs --scenario log-analysis --include-ext ".log"
filescan scan D:/data --scenario privacy-contact --expr "phone or email"
```

`--scenario` 与 `--rules` 互斥；表达式里的 id 必须能在该规则集中找到，否则扫描前直接报错。

## 规则集与表达式

规则集是 JSON（`*.filescan.json` 或任意 `.json`），最小示例：

```json
{
  "version": 6,
  "name": "团队规则",
  "rules": [
    {
      "id": "cn_id",
      "name": "身份证号",
      "group": "个人信息",
      "enabled": true,
      "matcher": {"type": "regex", "pattern": "\\d{17}[\\dXx]", "ignore_case": false}
    },
    {
      "id": "allow_list",
      "name": "测试白名单",
      "group": "测试",
      "enabled": true,
      "matcher": {"type": "keyword", "text": "test"}
    }
  ],
  "expressions": [
    {"id": "pii", "name": "个人信息", "expression": "cn_id and not allow_list", "enabled": true}
  ]
}
```

表达式规则：

- 叶子是规则 id 或组合式 id；支持 `and` / `or` / `not` 与括号，`and` 优先于 `or`；
- 在**文件级**求值：`cn_id and not allow_list` 表示同一文件命中身份证且不含测试词；
- 未启用的规则 / 组合式仍可作为构件被引用；只有 `enabled` 决定是否默认生效；
- 报告只列参与表达式且实际命中的正向规则，`not` 分支不产出命中明细。

## 报告与缓存

| 格式 | 参数 | 适合 |
|---|---|---|
| `text` | `--format text`（默认） | 终端快速查看 |
| `json` | `--format json` | 二次处理 / 交接 |
| `csv` | `--format csv` | Excel / 表格筛选 |
| `sarif` | `--format sarif` | GitHub Code Scanning 等静态分析工具 |

- 默认打码；需要原文加 `--raw`（导出 / 复制原文前确认合规）。
- 缓存默认开启：未变化文件直接复用上次命中；`--no-cache` 关闭，`--clear-cache` 先清空。
- 0 命中时报告会给出白名单排除数、文档无文本层等诊断，避免「全是跳过」却不知道原因。

## GUI（filescanw）

- 「帮助 → 使用帮助」（`F1`）打开本文对应说明；
- 扫描页按任务管理目录，行内「开始 / 停止扫描」，`更多扫描方式` 可增量或全量重扫；
- 结果页按文件聚合命中，可筛选、打码导出，上下文默认完整换行显示；
- 规则页维护规则表与组合式，支持规则集导入 / 导出；
- 设置页维护扫描范围、后缀白名单、缓存与配置导入 / 导出，等价于 `scan --save-config` / `run --config`。

## 常见问题

- **扫不到 `.env` / 无后缀文件**：默认白名单只含常见文本后缀；用 `--force-ext ".env"` 或把白名单设为 `*`。
- **文档 / PDF 没命中**：默认按文本读取；扫描 PDF / Office 需要可选依赖与可提取文本层，扫描统计会把无文本层的文件记入跳过。
- **结果全是「范围外」**：检查 `--include-ext` / `--exclude-ext` / `--ext-whitelist` 的优先级，排除后缀最高。
- **表达式报未知 id**：先用 `filescan rules --rules <file>` 确认规则 id；组合式与规则共用 id 命名空间。
- **想固定团队口径**：用 `scan --save-config` 固化配置，或用 `rules --action export` 导出规则集进版本库。
