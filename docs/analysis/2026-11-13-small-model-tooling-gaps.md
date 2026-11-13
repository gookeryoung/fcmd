# fcmd 面向小模型调用的工具缺口分析与改进计划

- 分析对象：`f:\Dev\fcmd`，HEAD `fa71899`（2026-11-13）
- 分析方式：§0–§8 为只读静态取证，未修改代码、配置或数据；§9–§11 为 P0 第一批/第二批/第三批落地记录（已改代码，含门禁数字）
- 取证边界：本轮未执行仓库内脚本（该工作区为未受信目录），文中行为结论均由源码与测试断言推导；带“复现命令”的示例为代码推导、未执行
- 分析范围：118 个注册工具、7 个内建命令，以及 `core/toolkit` 的注册/解析/执行/输出链路
- 目标场景：能力较弱的小模型通过调用工具完成原本需要大模型推理的任务，把推理负担下沉到工具里
- 非目标：不接 LLM SDK / MCP、不改 GUI、不做性能优化、不重写人类可读输出

---

## 0. 结论摘要

1. **工具“能力”够，但“接口”不是给机器用的。** 118 个工具里只有 5 个提供显式 JSON/CSV 结构化结果出口（`filescan`、`dynacalc`、`paramesh`、`env`、`tomltool get`，另加 `paramcad digest` 的固定 TSV）；29 个是纯 Tk 窗口入口（占 24.6%），模型调用会弹窗阻塞或在无显示环境下直接报错；其余大量工具输出 Rich 表格或自由中文文本。
2. **最致命的问题不是缺工具，而是失败不可判定。** 绝大多数 CLI 工具在域错误分支 `print_error` / `print("错误: …")` 后**正常返回，退出码 0**；测试甚至把这一行为固化（`tests/tools/text/test_cli_txttool.py:284-288`）。另一端是异常被框架吞掉：单任务异常只写 `LazyLogger.warning`（未导入 logging 时直接丢弃），进程退出 1 但终端通常没有任何错误文本（`src/fcmd/core/toolkit/exec.py:319-334`、`src/fcmd/core/execution/task_runner.py:280-286`、`src/fcmd/_lazylog.py:55-56`）。小模型既无法判断“做没做成”，也无法从错误里恢复。
3. **自描述缺口贯穿全链路。** 清单 schema v3 只记录 `name/kind/target`（`src/fcmd/cli/_manifest.py:54-59`）；`fcmd info <tool> <sub>` 只展示 ToolSpec 字段，不含函数签名（`src/fcmd/cli/_builtins/info_cmd.py:125-160`）；`--help` 使用 argparse 默认 Formatter，不显示默认值（`src/fcmd/core/toolkit/args.py:319`）；没有 JSON 清单、没有参数 schema、没有示例注册。模型拼参数只能读 README 或猜。
4. **正确形态已经在仓库里。** `filescan` 的 `result_to_mapping`（stats / hits / skipped / errors + 0 命中诊断 + `--format`）、`dynacalc` 的 `_fail(code=2)` 与 `gates` 退出码、`doctor` 内部已有的 `checks[{item,ok,detail,fix}]`、`peview edit` 的“默认预演 / 写前备份 / 写后校验”、`csvtool show` 的“共 N 行、显示前 M 行”，都可直接抽象为全仓范式，不需要另起设计。
5. **建议顺序：** P0 只做纯增量（`info --json`、`doctor --json`、`filesearch` 限量与 JSON、`hashfile` 只读查重、`textdiff --json`）；P1 先定“结果/错误/退出码”契约，再批量把“打印错误但退出 0”修掉并铺开 JSON；P2 做计划-执行两阶段、幂等写与断言型工具；P3 做 examples 注册、生成式文档与确定性剧本验收。

---

## 0.1 设计原则（P0 起生效）

前置约束：**必须同时兼顾小模型调用和人调用，不能为了机器可读牺牲人的使用体验。**

1. **默认输出不变。** 维持现有人类可读形态，不改变既有默认行为，不破坏现有脚本与习惯；P0 以现有测试快照作为回归门禁。
2. **机读走显式开关。** `--json` 是增量而非替换；同一份内部结果映射出两种呈现（先构造 `data`，再分别渲染），避免两套逻辑各自漂移。
3. **错误双读。** 既有一句人能看懂的 `message`，也有可解析的结构体（`code/kind/field/expected/hint`）；错误不得只在一种形态下出现。
4. **自描述双读。** 工具清单、参数 schema、示例调用既服务模型拼参数，也要能被人直接当 `--help` 之外的补充说明读（`fcmd info` 文本模式与 `--json` 同源）。
5. **长输出双读。** 默认给人看重点（`--limit`/`--count` 与总量脚注），机读形态给全量；两者数据取自同一处。

落地约定（第一批已固化，后续工具沿用）：

- 参数名 `json_output` 自动获得 `--json` 别名（`--json-output` 保留）；`fcmd info --json` 中记作 `cli: "--json"`、`aliases: ["--json-output"]`。
- 成功结果写 stdout（`fcmd-result/1`），错误写 stderr（`fcmd-error/1`）；`--json` 时框架的进度/汇总行自动静默，保证 stdout 可整体 `json.loads`。
- P0 不改退出码语义（那是 P1 的破坏性变更，见 §5.2）；默认路径与 `--json` 路径都沿用现状。

---

## 1. 判定口径

小模型的典型弱点与本次使用的判定维度一一对应：

| 小模型弱点 | 对应判定维度 | 判定问题 |
|---|---|---|
| 参数幻觉、记不住参数 | **可发现** | 只靠命令行能否发现参数名、类型、默认值、取值枚举？ |
| 不会多步规划、上下文短 | **可构造** | 能否一次调用拼出正确参数，而不必先读源码或拼 5 步？ |
| 长输出读不完 | **可判定** | 结果是否有稳定结构、总量、截断标记、可落盘？ |
| 不会从错误自纠 | **可恢复** | 失败是否有非零退出、错误码、出错的字段与修正提示？ |
| 不会自检结果 | **可验证** | 是否有 `--expect` / `--strict` / 校验工具，能给出机器可判结论？ |

评级图例：

- ✅ **可直接调用**：参数可自解释、结果可机读、失败可判定，三项同时成立
- ◐ **需薄封装**：能力可用，但至少一项不达标；多数只需修退出码或补 `--json`
- ✗ **人用**：GUI、交互式、依赖桌面 Office，headless 场景不应调用

---

## 2. 工具面盘点

### 2.1 总量与形态

| 形态 | 数量 | 说明 |
|---|---:|---|
| 注册工具 | 118 | `src/fcmd/cli/_manifest_data.py` 的 `TOOLS` 条目；schema v3 |
| 内建命令 | 7 | `graph` / `info` / `completion` / `yaml` / `env` / `doctor` / `profiler`（`cli/_builtins/__init__.py:25-33`） |
| 纯 Tk 窗口入口 | 29 | `alarmclock`、`checksum`、`cmderw`、`docdiffw`、`dynacalcw`、`envw`、`filescanw`、`filesyncw`、`img2icow`、`img2pdfw`、`llmchatw`、`llmconvertw`、`llmdownw`、`llmenginew`、`llmenvw`、`llmqntw`、`llmservew`、`mirrortoolw`、`ncmconvertw`、`paramcadw`、`parameshw`、`pdfcryptw`、`pdfsplitw`、`pevieww`、`pptmakew`、`regexvalidatew`、`renamerw`、`reshackw`、`videoconvw` |
| 依赖桌面环境 | +1 | `docdiff` 需要 Windows + Word COM（`fcmd[win32]`），headless 不可用 |
| 提供结构化结果出口 | 5 + 1 | `filescan`（text/json/csv）、`dynacalc`（params/model JSON、report csv/json）、`paramesh report --as-json`、`env --json`、`tomltool get`（JSON 值）；另有 `paramcad digest` 的固定 TSV |
| 默认输出为 Rich 表格/自由文本 | 其余 | 表格无稳定 schema；中文标签与装饰线会占用大量 token |

### 2.2 能力域盘点表

> “主要阻塞”列只列**阻碍小模型直接调用**的第一因，不列全部问题。

| 能力域 | 代表工具 | 评级 | 主要阻塞 | 证据 |
|---|---|---|---|---|
| 文件搜索/哈希 | `filesearch`、`hashfile` | ◐ | 输出无上限、无 JSON；错误与“无匹配”都退出 0 | `tools/system/filesearch.py:29-62`；`tools/crypto/hashfile.py:25-30` |
| 文件校验 | `checksum`、`filecheck` | ✗/◐ | 校验和比较只在 GUI；`filecheck` 为长文本报告 | `tools/system/checksum.py:1,386`；`infras/checksum.py:120,218` |
| 文件写入/改名 | `writefile`、`renamer`、`filerename` | ◐ | 静默覆盖；改名计划为文本；失败退出 0 | `tools/system/writefile.py:20-22`；`apps/renamer/renamer.py:87-98,163` |
| 目录比较/同步 | `filesync` | ◐ | 差异与计划为文本表；错误退出 0 | `apps/filesync/filesync.py:66-72,126` |
| 文本处理（单值） | `txttool`、`mathtool`、`stattool`、`calcdate`、`urltool`、`regextool` 等 | ◐ | 输出本身可读（单值/行），但多数工具的域错误打印后退出 0 | `tools/text/regextool.py`、`tools/data/jsontool.py:34-35` |
| 数据格式 | `jsontool`、`csvtool`、`xmltool`、`yamtool`、`tomltool`、`inifile` | ◐/✅ | 成功输出是数据本身；失败为 `错误: …` + 退出 0 | `tools/data/jsontool.py:48-49,69-70`；`tools/data/csvtool.py:30-39` |
| PDF | `pdftool`、`pdfsplit`、`pdfcrypt`、`img2pdf` | ◐ | `pdftool` 18 个子命令中 10 个是单字母/双字母，选择歧义高 | `tools/pdf/pdftool.py:51,77,127,133,143,153` |
| 办公文档 | `pptmake`、`docdiff` | ◐/✗ | 模板 JSON 参数拼装成本高；`docdiff` 依赖 Word | `apps/pptmake/pptmake.py`；README「办公文档」 |
| 多媒体 | `imagetool`、`img2ico`、`videoconv` | ◐ | 以产出文件为主；失败的呈现方式不统一（打印后退出 0 / 抛异常后静默退出 1） | README「多媒体」；`tools/media/videoconv.py:201-223` |
| 系统/进程 | `sysinfo`、`portcheck`、`peview`、`reshack` | ◐ | `sysinfo` 只有中文标签文本；`peview` 13 个子命令全表格、错误退出 0 | `tools/system/sysinfo.py`；`apps/peview/peview.py:54-59` |
| 项目构建 | `pymake`、`autofmt`、`pyprof`、`piptool`、`packtool`、`gittool` | ✅/◐ | cmd 任务失败可判定（统一退出 1）；但输出是原生命令输出，缺摘要 | `core/toolkit/exec.py:341` |
| LLM 工具链 | `llmenv`、`llmdown`、`llmserve`、`llmqnt`、`llmbench`、`llmchat` | ◐ | 多为长进度/日志；`llmchat` 省略 `--prompt` 会进入 `input()` 交互 | `tools/llm/llmchat.py:404,444-446` |
| 科学计算应用 | `dynacalc`、`paramcad`、`paramesh` | ✅/◐ | `dynacalc` 已有 JSON/退出码范式；`paramcad`/`paramesh` 错误仍退出 0 | `apps/dynacalc/dynacalc.py:104-107,155,205,460`；`apps/paramcad/paramcad.py:40-46`；`apps/paramesh/paramesh.py:112-116,259-273` |
| GUI 应用 | `cmderw`、`filescanw`、`renamerw` 等 29 个 | ✗ | 无 headless 模式；且部分能力只在 GUI 暴露（重复文件、校验文件比对） | `apps/cmder/app.py:8083,8477`；`apps/cmder/_dupes.py:93` |

### 2.3 代表工具证据卡

| 工具 | 现状 | 小模型判定 | 证据 |
|---|---|---|---|
| `filescan` | 唯一同时具备 `--format text/json/csv`、`--output`、stats、0 命中诊断、失败退出 2 的工具 | ✅ 基准范式 | `apps/filescan/filescan.py:78-86,198`；`_report.py:37,72-117,189` |
| `dynacalc` | 参数/模型可 `--json-output`，报告可 csv/json，运行/门控失败退出 1，域错误退出 2 | ✅ | `apps/dynacalc/dynacalc.py:104-107,155,205,276,292,460,452-453,540-541` |
| `pymake` | cmd 任务链，任一命令失败即退出 1 | ✅（输出需摘要） | `core/toolkit/exec.py:341`；README「项目构建」 |
| `peview` | 13 个子命令均为文本表；`check` 只打印结论、始终退出 0 | ◐ | `apps/peview/peview.py:54-59,195-204`；`_report.py:665-691` |
| `renamer` | `preview`/`apply` 两阶段，计划为文本，错误退出 0 | ◐ | `apps/renamer/renamer.py:87-98,163` |
| `filesync` | `compare`/`sync --preview`，差异与计划为文本表，错误退出 0 | ◐ | `apps/filesync/filesync.py:66-72,126` |
| `hashfile` | 只计算不校验；文件缺失打印后退出 0；非法算法抛 `ValueError` 被框架吞掉、退出 1 无文本 | ◐（危险） | `tools/crypto/hashfile.py:25-30`；`core/execution/task_runner.py:280-286`；`core/toolkit/exec.py:319-334` |
| `filesearch` | 无 `--limit`/`--count`/`--json`；`path:line:text` 在 Windows 盘符下存在分隔歧义；错误与无匹配都退出 0 | ◐ | `tools/system/filesearch.py:24-62` |
| `jsontool` | 成功输出是格式化 JSON；失败 `错误: 'key'` 走 stdout、退出 0 | ◐ | `tools/data/jsontool.py:34-35,48-49,69-70` |
| `textdiff` | 输出 unified diff；“相同”与“不同”都退出 0，无统计 | ◐ | `tools/text/textdiff.py:27-42` |
| `checksum` | 工具本身是 Tk GUI；`infras.checksum` 已有 build/parse/verify，但只被 cmderw 调用 | ✗（能力存在但不可 CLI 调用） | `tools/system/checksum.py:1,386`；`infras/checksum.py:120,160,218`；`apps/cmder/app.py:8477` |
| `cmderw` | 重复文件查找只在此 GUI 暴露 | ✗（能力存在但不可 CLI 调用） | `apps/cmder/app.py:8083`；`apps/cmder/_dupes.py:93` |
| `llmchat` | `--prompt` 单次可脚本化；省略时进入 `input()` 循环 | ◐（有交互陷阱） | `tools/llm/llmchat.py:404,444-446` |
| `pdftool` | 18 个子命令中 10 个为 `m/s/c/e/d/xt/xi/w/r/i` | ◐（发现性差） | `tools/pdf/pdftool.py:51,77,127,133,143,153` |
| `paramcad` / `paramesh` | 有 `digest` / `report --as-json` 等结构化出口，但域错误打印后退出 0 | ◐ | `apps/paramcad/paramcad.py:40-46,108-122`；`apps/paramesh/paramesh.py:112-116,259-273` |

### 2.4 仓库内已有的正例（应复用而非另造）

1. **结果映射**：`filescan._report.result_to_mapping()` 把 `stats / hits / skipped / errors` 一次性序列化，并在 0 命中时给诊断（`_report.py:72-117,189`）。
2. **失败退出码**：`filescan._fail()` 打印后 `raise SystemExit(2)`（`filescan.py:78-81`）；`dynacalc._fail()` 同样（`dynacalc.py:104-107`）。
3. **可机读诊断**：`doctor` 内部每个检查项已是 `{item, ok, detail, fix}`（`cli/_doctor_helpers.py:29,46,64,83,95,107`），只差一个 JSON 出口（`:114-140` 只渲染表格）。
4. **限量 + 总量**：`csvtool show` 只显示前 N 行并回报总行数（`tools/data/csvtool.py:37-39`）；`peview hex` 默认只取 256 字节（`apps/peview/peview.py:171-183`）。
5. **计划-执行两阶段**：`renamer preview/apply`、`filesync --preview`、`peview edit`（默认预演、`--apply` 才写、写前备份、写后重解析）。
6. **多步编排**：`fcmd yaml <file>` 支持 job 依赖、`--dry-run`、`--strategy`（`cli/_builtins/yaml_cmd.py:19-67`），但要求模型自己写 YAML。

---

## 3. 缺口清单

每条缺口给出：现象 → 证据 → 影响 → 仓库内正例 → 复现方式。缺口编号供第 5 节计划引用。

### G1 自描述不足：模型无法发现参数（对应弱点：参数幻觉）

**现象**

- 清单只有工具名、类型、模块路径，没有参数信息：

  ```python
  class ToolEntry(NamedTuple):
      name: str
      kind: str
      target: str
  ```

  证据：`src/fcmd/cli/_manifest.py:54-59`，`SCHEMA = 3`（`:51`）。

- `fcmd info <tool> <subcommand>` 展示的是 ToolSpec 字段（help/kind/cmd/needs/strategy/cwd/hidden/…），**不含**函数签名的参数名、类型、默认值、枚举、是否必填：

  ```python
  table.add_column("字段", no_wrap=True)
  table.add_column("值")
  ```

  证据：`cli/_builtins/info_cmd.py:125-160`。

- `--help` 用 argparse 默认 Formatter，`default=` 不会出现在帮助里；`Literal` 的 choices 会显示，但默认值、是否可重复、类型名缺失：

  ```python
  parser = argparse.ArgumentParser(prog=prog, description=description)
  ```

  证据：`core/toolkit/args.py:319-328`。

- 没有 JSON 工具清单、没有 `fcmd schema`、没有示例注册；唯一“示例”散落在各模块 docstring（如 `filescan.py:13-18`）与 README 的使用段落里，机器读不到。

**影响**

- 小模型只能凭工具名猜参数；默认值猜错会静默改变语义（例如 `csvtool show` 的 `--rows` 默认 5）。
- 无法自动生成 function-calling / tool-call schema，接入任何 agent 框架都要手写一份易漂移的映射。
- `pdftool` 单字母子命令、三个相似名字（`checksum` / `hashfile` / `hashtool`）进一步放大选择错误。未知工具虽有 difflib 建议（`cli/_common.py:17-27`），但子命令拼错没有建议。

**正例**：`filescan` 在 docstring 中给出 4 条完整示例（`filescan.py:14-18`），但 `info` 不会把它们暴露给机器。

**复现**

```bash
fcmd info filescan scan   # 看不到 format 的 choices / 默认值 / 是否可重复
fcmd filescan scan --help # 能看到 choices，但看不到默认值
```

### G2 结果不可机读：解析失败导致模型编造（对应弱点：上下文短、长输出读不完）

**现象**

- 只有 5 个工具提供结构化结果；其余输出 Rich 表格或中文行。表格含边框、省略号、颜色标记，字段名不稳定。
- `filesearch content` 输出 `path:lineno:line`（`tools/system/filesearch.py:62`）。Windows 盘符本身含 `:`，按 `:` 切分会产生错误列；文件名含 `:` 时同样歧义。
- `hashfile` 输出 `算法  摘要  路径`（`hashfile.py:30`），路径含空格时无法稳定切分，且没有 JSON/CSV 出口。
- `textdiff` 只给 unified diff 文本，不给 `added/removed/changed` 计数（`textdiff.py:38-42`）。

**影响**

- 模型必须“读表格再理解”，既费 token 又易错；一旦解析失败，常见行为是编造结论。
- 同一能力在两处输出格式不同（`checksum` GUI 表格 vs `hashfile` CLI 行），无法统一消费。

**正例**：`filescan --format json` 的字段稳定、可落盘、含 `stats`（`_report.py:72-117`）；`dynacalc report --output-format json`（`dynacalc.py:460`）。

**复现**

```bash
fcmd filesearch content . "TODO"
fcmd hashfile d . --algorithm sha256
```

### G3 失败不可判定：退出码 0 是常态（对应弱点：不会自纠错）

**现象**

- `print_error` 写 stdout，不改变退出码：

  ```python
  def print_error(message: str) -> None:
      get_console().print(f"[red]错误:[/red] {message}")
  ```

  证据：`src/fcmd/console.py:155-157`；`get_console()` 未绑定 `stderr`，按 `console.py:108` 的注释动态解析 `sys.stdout`。

- 域错误分支普遍“打印后正常返回”。已确认的代表：

  | 工具 | 证据 |
  |---|---|
  | `peview` | `apps/peview/peview.py:54-59`（`_run` 捕获 `PEError` 后仅打印） |
  | `paramcad` | `apps/paramcad/paramcad.py:40-46` |
  | `paramesh` | `apps/paramesh/paramesh.py:112-116` |
  | `renamer` | `apps/renamer/renamer.py:87-98,163` |
  | `filesync` | `apps/filesync/filesync.py:66-72,176` |
  | `pptmake` / `reshack` | 同名 `_print_error` 模式，错误后继续返回 |
  | `filecheck` | `tools/media/filecheck.py`（错误分支打印后返回） |
  | `filesearch` / `hashfile` / `textdiff` / `regextool` / `csvtool` / `jsontool` / `xmltool` / `yamtool` / `tomltool` / `inifile` / `txttool` / `mathtool` | 多数以 `print("错误: …")` 结束后正常返回 |

  全仓静态统计（`Get-ChildItem -Recurse` + `Select-String`）：33 个文件共 82 处 `print_error(`；30 个文件共 95 处 `print(f"错误: …")`（含 GUI 文件与定义本身，但 CLI 工具的错误分支基本都不改退出码）。

- 测试固化了“失败也退出 0”：

  ```python
  def test_...(self, ...):
      """文件不存在时打印提示。"""
      ...
      assert code == 0
      assert "文件不存在" in out
  ```

  证据：`tests/tools/text/test_cli_txttool.py:284-288`（同类断言延续到 `:291-309`）。

- 另一端：fn 任务抛异常时，框架只写 `LazyLogger.warning`；未导入 `logging` 时该日志被直接丢弃（`_lazylog.py:55-56`），导入时也只是 `WARNING:…` 前缀的非结构化行。CLI 侧打印“执行失败”和一张**不含错误文本**的汇总表：

  ```python
  raise TaskFailedError(...)          # task_runner.py:280-286，错误只进 logger
  ...
  err_console.print("[red]执行失败[/red]")
  _print_task_summary(e.report, force=True)  # 列：任务/状态/耗时/重试
  ```

  证据：`core/toolkit/exec.py:319-334,347-386`；`core/execution/task_runner.py:280-286`。

- 退出码现状至少四种：`0`（域错误）、`1`（argparse 失败/异常）、`2`（`filescan`、`dynacalc` 域错误）、`130`（中断）。argparse 原本的 `2` 还被压平成 `1`：

  ```python
  return ToolExitCode.SUCCESS.value if e.code == 0 else ToolExitCode.FAILURE.value
  ```

  证据：`core/toolkit/exec.py:273-277`；`core/toolkit/args.py:38-43`。

**影响**

- 模型最常看到的信号是“成功”，于是把失败当结果继续后续步骤，错误被放大。
- 依赖 `if error: retry` 的自动化流程全部失效；只能靠中文关键词匹配。
- 异常场景更糟：退出 1 但终端通常没有任何错误文本，模型连“哪里错了”都拿不到。

**正例**：`filescan` 的 `_fail` 退出 2（`filescan.py:78-81`）；`dynacalc` 的 `_fail` 退出 2、`gates` 失败退出 1（`dynacalc.py:104-107,540-541`）；`doctor` 失败退出 1（`_doctor_helpers.py:114-140`）。

**复现**

```bash
fcmd jsontool query missing.json a.b ; echo $?   # 期望非零，实际 0
fcmd hashfile missing.txt --algorithm sha256 ; echo $?  # 实际 0
fcmd hashfile missing.txt --algorithm nosuch ; echo $?  # 实际 1，终端通常无错误文本
```

### G4 错误没有修正线索（对应弱点：不会从错误自我纠正）

**现象**

- `print_error` 只接受一个字符串，没有错误码、字段名、期望值、可选值、修正命令。
- argparse 的用法错误是 argparse 原生格式（`usage: …` / `error: …`），与工具的中文错误风格不统一；子命令拼错只列子命令、不做 difflib 建议（`core/toolkit/exec.py:256-261`）。
- 参数值非法时，`Literal` 有 choices 校验，但错误信息由 argparse 生成，无法定位到 `field`；非 `Literal` 的语义校验更深（如 `hashfile` 的 `ensure_algorithm` 抛 `ValueError`）。
- 工具内部错误多为 Python 原生异常字符串（如 `jsontool` 的 `错误: 'b'`），缺少“下一步做什么”。

**影响**

- 模型无法做“改参数重试”：不知道该改哪个字段、合法值是什么。
- 多轮重试容易在同一错误上打转，浪费上下文与调用次数。

**正例**：`filescan.no_hit_hint()` 在 0 命中时给出白名单/规则诊断（`_report.py:189`）；`doctor` 每项带 `fix` 文本（如 `pip install fcmd[pdf]`），但仅渲染在表格里。

### G5 长输出无上限、无摘要、不落盘（对应弱点：上下文短、长输出读不完）

**现象**

- `filesearch content` 无命中上限、无 `--count`、无 `--json`，一次可能输出数万行（`filesearch.py:48-62`）。
- `hashfile <dir>` 打印目录下每个文件的哈希（`hashfile.py:47-53`）。
- `peview imports/exports/sections/relocations` 全量表格（`peview.py:107-167`），无 `--limit`/`--out`。
- `csvtool to-json`、`jsontool pretty`、`xmltool pretty`、`yamtool pretty` 会把整个文件倒到 stdout。
- 只有 `filescan` 有 `--output`、`csvtool show` 有行数上限、`peview hex` 有字节上限。

**影响**

- 输出超过上下文窗口后被截断，模型只看到前半段，容易得出错误结论。
- 无法把完整结果落盘后再分段处理。

**正例**：`filescan` 的 `stats` 明确区分 `files_seen / files_scanned / hit_count / elapsed`，且支持 `--output`（`_report.py:77-100`；`filescan.py:198`）；`csvtool show` 打印“共 N 行（显示前 M 行）”（`csvtool.py:37-39`）。

### G6 缺“一次调用完成一件事”的高层封装（对应弱点：不会多步规划）

**现象**

- “查找重复文件”只有 GUI：`cmder/app.py:8083` 调用 `_dupes.find_duplicates`（`apps/cmder/_dupes.py:93`），没有 CLI 工具。
- “生成/校验校验文件”的能力在 `infras/checksum.py`（`build_manifest:120`、`parse_manifest:160`、`verify_manifest:218`），但唯一消费者是 cmderw（`app.py:8477`）；CLI 的 `hashfile` 只算不比，`checksum` 是 GUI。
- “判断两个文件/目录是否一致”只能看 `textdiff`/`filesync` 的人类输出，没有一行结论式入口。
- 多步编排虽有 `fcmd yaml`，但要求模型自己写 YAML，且没有 `--check`（只解析不执行）。

**影响**

- 小模型必须自己完成“列文件 → 算哈希 → 分组 → 比较”四步；任何一步参数错都前功尽弃。
- 能力明明存在，却因为只挂在 GUI 上而不可用，属于“零成本缺口”。

**正例**：`dynacalc demo` 把“示例参数 → 生成 → 假求解 → 汇总 → 对比 → 门控”合成一条命令（README「科学计算应用」）。

### G7 缺断言型 / 可验证工具（对应弱点：不会自检结果）

**现象**

- `hashfile` 没有 `--expect` / `verify` 子命令；无法回答“这个文件的 sha256 是否等于给定值”。
- `peview check` 输出“结论: 未发现结构性问题”或“警告”，但**始终退出 0**（`_report.py:665-691`；`peview.py:195-204`）。
- `textdiff` 有差异也退出 0（`textdiff.py:38-42`）。
- `filesync compare` 有差异也退出 0（`filesync.py:91-125`），无法作为门禁。
- `doctor` 内部有 `ok`/`fix`，但没有 `--json`，自动化只能解析表格（`_doctor_helpers.py:29,114-140`）。

**影响**

- 模型改完文件后无法自证“改对了”；只能把内容再打印一遍靠人看。
- CI/代理流程无法用退出码表达“校验不通过”。

**正例**：`dynacalc gates` 失败退出 1（`dynacalc.py:540-541`）；`filescan` 域错误退出 2；`pymake` 的 cmd 任务失败统一退出 1。

### G8 干跑、幂等与覆盖策略不统一（对应弱点：不会规划、重复调用）

**现象**

- 全局 `--dry-run` 由框架统一占用，语义是“整个 DAG 都不执行，只打印计划”（`args.py:332-340`；`exec.py:316`）。对于 fn 工具，它打印的是任务图，不是工具自己的动作计划。
- 工具各自造预演开关，命名与语义不一：`renamer preview`、`filesync --preview`、`peview edit` 默认预演 + `--apply`、`dynacalc --plan-only`。
- `dynacalc` 源码直接记录了该冲突：

  ```
  全局 ``--dry-run`` 由 fcmd 任务框架占用，故此处用 ``--plan-only``
  ```

  证据：`apps/dynacalc/dynacalc.py:409`。

- `writefile` 无存在性检查、无 `--no-clobber`、无前置哈希，静默覆盖（`tools/system/writefile.py:20-22`）。
- 覆盖策略不一致：`paramcad export/example`、`paramesh` 有 `overwrite` 守卫并报错；`writefile`、`csvtool from-json` 等直接写。
- `llmchat` 省略 `--prompt` 进入交互（`llmchat.py:404,444-446`）；29 个 GUI 工具没有“禁止 headless 启动”的保护，模型误调会阻塞或报错。

**影响**

- 同一操作重试两次可能产生两份副作用或覆盖用户数据。
- 模型无法安全地“先看计划，再执行”；计划与执行之间也没有漂移校验。

**正例**：`renamer apply` 两阶段且支持互换/仅大小写（README）；`peview edit` 写前备份、写后校验；`paramcad`/`paramesh` 的 `overwrite=False` 默认拒绝覆盖。

### 3.9 缺口 → 弱点映射矩阵

| 小模型弱点 | 主要缺口 | 当前后果 | 优先修 |
|---|---|---|---|
| 参数幻觉 | G1、G4 | 猜参数、猜默认值、重试同一错误 | P0 自描述；P1 错误对象 |
| 上下文短 | G2、G5 | 表格/长文本挤爆上下文，解析失败后编造 | P0 限量；P1 JSON |
| 长输出读不完 | G5 | 关键结论落在被截断的后半段 | P0/P1 |
| 不会多步规划 | G6、G8 | 需要 4–5 步的能力直接放弃 | P0 高层封装；P2 plan |
| 不会从错误自纠 | G3、G4 | 失败当成功；退出 1 却无错误文本 | P0 修 silent fail；P1 契约 |
| 不会自检结果 | G7 | 只能打印内容让人看 | P1 断言；P2 strict |
| 重复调用不安全 | G8 | 覆盖用户数据、重复副作用 | P2 幂等 |

---

## 4. 落地前需要先定的契约

P0 的条目是纯增量、不改现有语义，可以直接做。P1 起涉及“行为变更”和“跨工具一致性”，必须先定契约（建议各写一份 ADR，或合并为一份《工具机器可读接口契约》）。

### 4.1 `fcmd-schema/1`：工具与参数自描述

```json
{
  "schema": "fcmd-schema/1",
  "generated_from": "runtime-registry",
  "tools": [
    {
      "name": "filescan",
      "aliases": [],
      "gui": false,
      "description": "目录敏感内容扫描…",
      "subcommands": [
        {
          "name": "scan",
          "help": "扫描目录并输出报告",
          "strategy": "dependency",
          "needs": [],
          "params": [
            {
              "name": "root",
              "cli": "root",
              "type": "path",
              "required": true,
              "default": null,
              "choices": null,
              "repeatable": false,
              "help": "扫描根目录"
            },
            {
              "name": "format",
              "cli": "--format",
              "type": "string",
              "required": false,
              "default": "text",
              "choices": ["text", "json", "csv"],
              "repeatable": false,
              "help": "报告格式"
            }
          ],
          "examples": ["fcmd filescan scan D:/data --format json --output report.json"]
        }
      ]
    }
  ]
}
```

要点：

- 由运行时注册表 + `inspect.signature` + docstring `Parameters` + `param_help` 推导，**不修改** `_manifest_data.py` 与 `pyproject.toml` 的 `[project.scripts]`（项目约束）。
- `gui: true` 必须显式给出，让模型在调用前就能排除 29 个窗口工具。
- `type` 统一为 `string | int | float | bool | path | enum | string[]`，与 argparse 的映射规则一致（`args.py:158-329`）。
- `examples` 需要新增到 `ToolSpec`（P3），P0 可先留空。

### 4.2 `fcmd-result/1`：成功结果信封

```json
{
  "schema": "fcmd-result/1",
  "tool": "filesearch",
  "subcommand": "content",
  "ok": true,
  "data": {
    "matches": [
      {"path": "src/a.py", "line": 12, "text": "TODO: ..."}
    ]
  },
  "stats": {"total": 5, "returned": 2, "truncated": true, "elapsed_ms": 12},
  "warnings": [],
  "error": null
}
```

规则：

- `ok` 只表示“工具完成”，业务上的“无匹配”是 `ok: true` + `data.matches: []`，不是错误。
- `stats.total/returned/truncated` 必须存在，解决 G5；没有分页概念的工具也至少给 `total`。
- 固定字段之外，`data` 由工具自定义；任何新增字段不得改名（语义化版本）。
- 默认输出（不带 `--json`）保持不变，避免破坏人类使用与既有脚本；这是“加一条通道”，不是“换掉旧通道”。

### 4.3 `fcmd-error/1` 与退出码

错误对象写 **stderr**（`--json` 时 stdout 保持为空或纯 JSON），一行一个对象：

```json
{
  "schema": "fcmd-error/1",
  "tool": "hashfile",
  "subcommand": null,
  "ok": false,
  "error": {
    "code": "FILE_NOT_FOUND",
    "kind": "input",
    "message": "文件不存在: a.txt",
    "field": "path",
    "expected": "已存在的文件路径",
    "received": "a.txt",
    "hint": "先运行 fcmd filesearch name . --pattern a.txt 确认路径"
  }
}
```

退出码（沿用 `filescan` / `dynacalc` 已有的 0/1/2 语义，补齐为统一契约；`error.kind` 取 `usage | input | runtime | check`）：

| 退出码 | 含义 | 说明 |
|---:|---|---|
| 0 | 完成 | 包括“无匹配”“两文件相同”等业务结果 |
| 1 | 运行失败 / 校验不通过 | `error.kind = runtime` 或 `check`；IO、权限、内部错误、`--strict` 校验失败 |
| 2 | 用法 / 输入错误 | `error.kind = usage` 或 `input`；缺参、非法枚举、未知子命令、输入文件不存在、模型格式不合法；**argparse 的原生 2 不再压平** |
| 130 | 用户中断 | 沿用现状 |

这样 `filescan scan missing-dir`（2）与 `dynacalc model bad.yaml`（2）的现有行为无需迁移，只需要把“打印后返回 0”的工具对齐到同一张表。

`error.code` 建议固定集合：`FILE_NOT_FOUND`、`PATH_NOT_FOUND`、`PERMISSION_DENIED`、`INVALID_ARGUMENT`、`MISSING_ARGUMENT`、`UNSUPPORTED_FORMAT`、`DEPENDENCY_MISSING`、`CONFLICT`、`CHECK_FAILED`、`INTERNAL`。

迁移要求：

- 修改“打印错误但退出 0”的工具时，必须同步修改仓库内固化该行为的测试（已知 `tests/tools/text/test_cli_txttool.py:284-308`，其余用 `grep "assert code == 0"` 定位）。
- 这是**有意的破坏性变更**，应写入 CHANGELOG，并在一个版本内完成；不建议加 `FCMD_LEGACY_EXIT=1` 之类兼容开关，避免长期双语义。
- `paramcad` / `paramesh` / `dynacalc` 属于独立应用，接口调整需与其维护会话协调，先改框架与工具层，再逐个应用接入。

### 4.4 `fcmd-plan/1`：计划-执行两阶段与幂等

```json
{
  "schema": "fcmd-plan/1",
  "tool": "filesync",
  "subcommand": "sync",
  "fingerprint": "sha256:…",
  "actions": [
    {"op": "copy", "source": "D:/data/a.txt", "target": "E:/backup/a.txt", "reason": "only-left", "size": 12, "mtime": 1760000000}
  ],
  "summary": {"copy": 1, "delete": 0, "skip": 2}
}
```

规则：

- `--plan <file>` 执行时必须重新校验 `fingerprint`（源与目标的 size/mtime/hash）；漂移则拒绝执行并给出 `CHECK_FAILED`，不静默跳过。
- `--dry-run` 保持框架现状（不执行任何任务）；工具级预演统一叫 `--plan` / `--plan-only` / `--preview` 中的一种，并在 schema 中声明。
- 删除类动作必须要求显式勾选并二次确认；CLI 默认只读（与项目既有不可逆操作约束一致）。

### 4.5 校验语义

- 新增 `--strict` 的工具：校验不通过退出 1，并在 `data.checks[]` 中给出 `{name, ok, detail, fix}`。
- 已有 `doctor` 的 `checks` 结构直接复用；`peview check --strict`、`filecheck --strict`、`filesync compare --strict` 采用同一形状。
- `hashfile verify --expect <hex>` 或 `hashfile manifest/verify` 采用同一形状。

---

## 5. 实施计划

排序原则：先让模型“看得见参数、拿得到结论”，再修“失败信号”，最后做高层封装与自检。每项都给出解决的具体弱点、风险、契约依赖与验收方式。

### 5.1 P0：纯增量、低风险、可直接做（不改现有 stdout 与退出码）

| 编号 | 改动 | 解决的弱点 | 契约 | 风险 | 验收 |
|---|---|---|---|---|---|
| P0-1 | `fcmd info --json`、`fcmd info <tool> [sub] --json`：导出工具清单、子命令、参数（name/cli/type/required/default/choices/repeatable/help）、`gui` 标记、aliases、needs/strategy | 参数幻觉 | 无（字段集后续固化为 `fcmd-schema/1`） | 低；纯新增选项。`gui` 由 `_schema.py` 中的显式清单给出（覆盖 29 个窗口工具，含 `alarmclock`、`checksum`），不逐个改注册点 | `fcmd info filescan scan --json` 中 `format.choices == ["text","json","csv"]`；`fcmd info checksum --json` 的 `gui == true`；文本输出逐字节不变 |
| P0-2 | `fcmd doctor --json`：复用现有 `checks[{item,ok,detail,fix}]` | 参数幻觉、自检 | 无 | 低 | 人为制造失败项时 `ok=false` 且 `fix` 非空；退出码与现在一致 |
| P0-3 | `fcmd filesearch name/content --json --limit N --count`：返回 `{matches,total,truncated}`，默认无匹配时 `ok=true,total=0` | 长输出、解析失败 | 无（先按 `fcmd-result/1` 的形状设计） | 低；默认行为不变 | 构造 3 文件 5 命中：`--limit 2` 返回 2 条、`total=5`、`truncated=true`；无命中返回空数组且退出 0 |
| P0-4 | `fcmd hashfile dupes <dir>`：只读复用重复文件逻辑，输出分组与可回收字节，`--json`、`--min-size`、`--limit` | 多步规划 | 无 | **中低**：需先把 `find_duplicates` 从 `apps/cmder/_dupes.py` 下沉到 `infras/dupes.py`（tools 不得 import apps，见 ADR 0001 规则 4/5），cmderw 改为引用同一实现；只读，不删除 | 构造已知重复文件，组数/路径/字节数正确；不修改任何文件；`apps/cmder` 既有重复文件测试迁移后仍全绿 |
| P0-5 | `fcmd textdiff files --json`：输出 `{equal, added, removed, hunks}`；暂不改退出码 | 解析失败、自检 | 无 | 低 | 相同文件 `equal=true` 且计数为 0；不同文件计数与 diff 一致；默认文本输出不变 |

说明：P0-4 选择挂到 `hashfile` 而不是新建顶层 `dupes`，是为了避免新增工具带来的清单重生成与 `[project.scripts]` 改动（项目当前禁止改这两处）。若后续确定要顶层 `dupes`，需先协调清单生成与入口注册。

状态（2026-11-13）：P0-1、P0-2、P0-3、P0-4、P0-5 均已完成并作为范式模板（见 §9、§10）。

### 5.2 P1：先定契约，再统一失败语义与 JSON 通道

前置：完成 4.2 / 4.3 的契约文档与评审。

| 编号 | 改动 | 解决的弱点 | 风险 | 验收 |
|---|---|---|---|---|
| P1-1 | 框架级 `--json` 上下文 + `ok/fail/emit` helper：成功走 `fcmd-result/1`，失败走 `fcmd-error/1` 到 stderr；argparse 失败保留退出码 2 | 全部 | 中；触及 `console.py` / `core/toolkit`，需回归 | 退出码矩阵测试：缺参=2、非法枚举=2、运行错误=1、成功=0、中断=130；`--json` 时 stdout 必须可被 `json.loads` 解析 |
| P1-2 | 修复高价值工具的“silent success”：`peview`、`paramcad`、`paramesh`、`renamer`、`filesync`、`pptmake`、`reshack`、`filecheck` 及文本/数据类工具的错误分支，改为非零退出并带 `error` 对象 | 自纠错 | 中高；行为变更，需改测试 | 每个工具至少 1 个失败用例断言非零；`grep -r "assert code == 0" tests` 中“错误场景断言 0”的用例全部改掉并列出清单 |
| P1-3 | JSON 输出铺开（按价值排序）：`peview summary/sections/imports/check`、`filesync compare/sync --preview`、`renamer preview`、`sysinfo`、`hashfile`、`filecheck`、`pdf info` | 解析失败、长输出 | 中；输出契约需评审 | 每个工具 golden JSON；`stats.total/returned/truncated` 齐全；默认文本输出快照不变 |
| P1-4 | `fcmd hashfile verify` 与 `fcmd hashfile manifest`：复用 `infras.checksum.build_manifest/parse_manifest/verify_manifest`，不匹配退出 1 并列出 `{path, expected, actual, ok}` | 自检 | 低（只读）；新子命令 | 正确清单通过；篡改 1 字节后失败并精确指出文件；`--json` 可解析 |
| P1-5 | 错误文案补 `hint` 与 `field`：建立各工具错误码到提示的映射；子命令拼错加 difflib 建议 | 自纠错 | 低 | 缺文件/非法枚举/未知子命令三类错误均含 `code/field/hint/expected` |

### 5.3 P2：计划-执行、幂等与断言型工具

前置：完成 4.4 / 4.5 契约。

| 编号 | 改动 | 解决的弱点 | 风险 | 验收 |
|---|---|---|---|---|
| P2-1 | `filesync compare --json` + `filesync sync --plan plan.json`：执行前校验计划指纹，漂移拒绝并报 `CHECK_FAILED` | 多步规划、重复调用 | 中；涉及写操作 | 预演后修改源文件 → 执行被拒绝；未修改 → 精确执行计划内动作 |
| P2-2 | `renamer preview --json`（冲突原因结构化）+ `renamer apply --plan plan.json`（校验预览后文件未变） | 多步规划、幂等 | 中 | 预览后改名 1 个文件 → `apply` 拒绝并提示漂移；正常流程成功且可重跑无副作用 |
| P2-3 | `writefile --from-file --if-match <sha256> --no-clobber`，原子写；统一“已存在”语义 | 重复调用不安全 | 中；触及写文件 | 哈希不匹配/文件已存在时拒绝；成功后 `--if-match` 可用于第二次幂等调用 |
| P2-4 | `peview check --strict`、`filecheck --strict`、`filesync compare --strict`：校验不通过退出 1，`data.checks[]` 结构化 | 自检 | 低 | 构造坏 PE / 0 字节文件 / 有差异目录，`--strict` 均非零且 `checks` 指出问题 |
| P2-5 | `fcmd yaml --check --json`：只解析与校验 job 图并输出计划，不执行 | 多步规划 | 低 | 合法 YAML 返回 job 列表与依赖；非法 YAML 退出 2 并定位到行 |

CLI 的 `hashfile dupes` 保持只读，不提供删除动作；删除仍只在 cmderw 中执行并二次确认（遵循“不可逆操作先确认”的项目约束）。

### 5.4 P3：examples、生成式文档与剧本验收

前置：`fcmd-schema/1` 定稿。

| 编号 | 改动 | 解决的弱点 | 风险 | 验收 |
|---|---|---|---|---|
| P3-1 | `ToolSpec` 增加 `examples: list[str]` 与 `gui/deprecated` 元数据；`fcmd info`、schema 输出 examples | 参数幻觉 | 低 | 新增工具必须带 examples（布局/清单守卫）；examples 可被逐条解析为合法 argv |
| P3-2 | 从 schema 生成 `docs/agent-cli.md`，并在现有门禁中增加“生成物不漂移”校验 | 上下文短、接入成本 | 低 | 生成幂等；CI 中手工改文档会失败 |
| P3-3 | `tests/eval/` 确定性剧本：给定命令 + `jq` 断言，不接 LLM，验证“工具面足以表达任务” | 全部 | 低 | 12 条剧本全绿（见 6.2） |
| P3-4 | 可选：`fcmd schema --all` 导出离线 `schema.json`，避免每次解析都 import 全部工具 | 启动成本 | 低 | 与运行时结果逐字节一致 |

### 5.5 依赖关系

```text
P0-1 schema ──► P3-1 examples ──► P3-2 生成式文档
   │
   └──► P1-1 结果/错误契约 ──► P1-2 退出码修复 ──► P1-3 JSON 铺开
                                     │
                                     └──► P2-4 strict 校验

P0-5 textdiff --json ──► P1-2/1-3
P2-1 plan ──► P2-2 renamer plan ──► P2-3 幂等写
```

---

## 6. 验收方式

### 6.1 分层验收

| 层级 | 方法 | 通过标准 |
|---|---|---|
| 契约 | JSON Schema 校验 + golden 文件 | `fcmd-result/1`、`fcmd-error/1`、`fcmd-plan/1` 字段齐全；新增字段不破坏旧字段 |
| 单元 | `pytest tests/cli tests/core` | schema 推导、退出码映射、错误对象、计划指纹校验均有纯逻辑单测 |
| 集成 | `run_tool()` 级测试 | 每个改动工具至少 1 成功 + 1 失败用例；失败用例断言退出码与 `error.code` |
| 人类输出回归 | 文本快照 | 不带 `--json` 时输出与改动前逐字节一致（除已声明修复的错误行） |
| 门禁 | `make check-fast`、收尾 `make test` | 全绿；不放宽断言、不加 skip、不使用 `--no-verify` |

### 6.2 确定性剧本（P3 落地，用于证明“工具面够用”）

| # | 任务 | 调用 | 断言 |
|---|---|---|---|
| 1 | 找出目录内所有 TODO | `filesearch content . TODO --json --limit 50` | `ok=true`；`total` 为真实命中数；`truncated` 正确 |
| 2 | 判断两个文件是否相同 | `textdiff files a.txt b.txt --json` | `equal` 正确；`added/removed` 与 diff 一致 |
| 3 | 校验下载文件 | `hashfile verify sums.sha256 --root dist` | 正确时 0；篡改后 1 且列出文件 |
| 4 | 找重复文件 | `hashfile dupes D:/data --json` | 分组正确，未修改磁盘 |
| 5 | 查看 PE 结构是否正常 | `peview check app.exe --strict --json` | 正常 0；坏文件 1 且 `checks` 指出原因 |
| 6 | 比较两目录并给出计划 | `filesync compare D:/a E:/b --json` | 差异项与 `stats` 正确；`--strict` 时有差异退出 1 |
| 7 | 预览批量改名 | `renamer preview ... --json` | 冲突/非法/覆盖原因结构化 |
| 8 | 扫描敏感内容 | `filescan scan D:/data --format json` | `stats` 与 `hits` 可解析 |
| 9 | 跑项目测试并判断 | `pymake t` | 失败时退出 1（不区分下游具体码） |
| 10 | 环境自检 | `doctor --json` | 每项 `ok/detail/fix` 齐全 |
| 11 | 非法枚举自恢复 | 任意工具传非法 `Literal` | 退出 2，`error.expected` 给出合法值 |
| 12 | 缺文件自恢复 | `hashfile missing.txt` | 退出 2，`error.code=FILE_NOT_FOUND` 且 `hint` 可执行 |

---

## 7. 风险、约束与不做的事

**风险**

1. **退出码变更会破坏既有脚本与测试**。仓库内已知固化用例（`tests/tools/text/test_cli_txttool.py:284-308`）必须一起改；外部脚本无法枚举，应在 CHANGELOG 明确标注为破坏性变更，并在一个版本内完成迁移。
2. **JSON 与人类输出双通道的维护成本**。要求“默认输出不变”，JSON 由同一份数据映射生成（参考 `filescan.result_to_mapping`），禁止两套逻辑各写一遍。
3. **`--json` 与既有 `--format` / `--json-output` 冲突**。需在契约中规定优先级：`--json` 是 `--format json` 的语法糖；同时出现时以显式 `--format` 为准。
4. **跨会话协调**。`paramcad` / `paramesh` / `dynacalc` 由并行会话维护，P1-2、P1-3 涉及它们时必须先对齐，不在本计划内单方面改动。

**约束（必须遵守）**

- 不修改 `src/fcmd/cli/_manifest_data.py` 与 `pyproject.toml` 的 `[project.scripts]`；schema 由运行时注册表推导。若确需静态清单，另立 ADR 并协调生成器。
- 不改变 `--dry-run` 的既有语义；工具级计划使用 `--plan` / `--plan-only` / `--preview`。
- 删除类操作必须显式确认，不实现静默删除；P0/P2 的只读工具（dupes、verify、compare）不得写盘。
- 每批功能独立可提交、可回退；提交前跑 `make check-fast`，收尾 `make test`。

**不做的事**

- 不接 LLM SDK / MCP / function-calling 运行时；本计划只改善 CLI 契约。
- 不重写 Rich 表格输出，不给全部 118 个工具一次性补 JSON；按价值排序。
- 不改 GUI，不为 29 个窗口工具补 headless 模式（只加 `gui` 标记供筛选）。
- 不做性能优化；不引入新的第三方依赖。
- 不把 `fcmd yaml` 扩成通用脚本语言；只加 `--check` 与 JSON 出口。

---

## 8. 附录 A：证据索引

| 结论 | 证据位置 |
|---|---|
| 清单 schema v3 仅有 name/kind/target | `src/fcmd/cli/_manifest.py:49-59` |
| `info` 不展示函数签名 | `src/fcmd/cli/_builtins/info_cmd.py:125-160` |
| `--help` 不显示默认值 | `src/fcmd/core/toolkit/args.py:319-328` |
| 全局 `--dry-run` 语义 | `src/fcmd/core/toolkit/args.py:332-340`；`src/fcmd/core/toolkit/exec.py:316` |
| argparse 失败被压平为 1 | `src/fcmd/core/toolkit/exec.py:273-277` |
| 退出码仅 0/1/130 | `src/fcmd/core/toolkit/args.py:38-43` |
| 单任务异常通常无终端错误文本 | `src/fcmd/core/toolkit/exec.py:319-334,347-386`；`src/fcmd/core/execution/task_runner.py:280-286` |
| `print_error` 写 stdout | `src/fcmd/console.py:155-157`（动态流说明见 `:108`） |
| 测试固化“错误也退出 0” | `tests/tools/text/test_cli_txttool.py:284-308` |
| `filescan` 的失败退出 2 | `src/fcmd/apps/filescan/filescan.py:78-86` |
| `filescan` 结果映射与 0 命中诊断 | `src/fcmd/apps/filescan/_report.py:37,72-117,189` |
| `dynacalc` 的 `_fail` 与 JSON 出口 | `src/fcmd/apps/dynacalc/dynacalc.py:104-107,155,205,276,292,460` |
| `dynacalc --plan-only` 与全局 dry-run 冲突说明 | `src/fcmd/apps/dynacalc/dynacalc.py:409` |
| `peview` 错误仅打印 | `src/fcmd/apps/peview/peview.py:54-59` |
| `peview check` 无退出码语义 | `src/fcmd/apps/peview/peview.py:195-204`；`src/fcmd/apps/peview/_report.py:665-691` |
| `peview hex` 默认 256 字节 | `src/fcmd/apps/peview/peview.py:171-183` |
| `paramcad` / `paramesh` 错误仅打印 | `src/fcmd/apps/paramcad/paramcad.py:40-46`；`src/fcmd/apps/paramesh/paramesh.py:112-116` |
| `renamer` 预览失败返回 None | `src/fcmd/apps/renamer/renamer.py:87-98,163` |
| `filesync` 错误仅打印、`--preview` | `src/fcmd/apps/filesync/filesync.py:66-72,91-176` |
| `hashfile` 只算不比、错误退出 0 | `src/fcmd/tools/crypto/hashfile.py:25-30,47-53` |
| `filesearch` 无上限、`:` 分隔 | `src/fcmd/tools/system/filesearch.py:24-62` |
| `jsontool` 错误文本 | `src/fcmd/tools/data/jsontool.py:34-35,48-49,69-70` |
| `csvtool show` 限量与总量 | `src/fcmd/tools/data/csvtool.py:30-39` |
| `textdiff` 相同/不同都退出 0 | `src/fcmd/tools/text/textdiff.py:27-42` |
| `writefile` 静默覆盖 | `src/fcmd/tools/system/writefile.py:20-22` |
| `checksum` 为 GUI | `src/fcmd/tools/system/checksum.py:1,386` |
| 校验文件逻辑只在 infras/cmderw | `src/fcmd/infras/checksum.py:120,160,218`；`src/fcmd/apps/cmder/app.py:8477` |
| 重复文件查找只在 cmderw | `src/fcmd/apps/cmder/app.py:8083`；`src/fcmd/apps/cmder/_dupes.py:93` |
| `pdftool` 单字母子命令 | `src/fcmd/tools/pdf/pdftool.py:51,77,127,133,143,153,198` |
| `llmchat` 省略 prompt 进入交互 | `src/fcmd/tools/llm/llmchat.py:404,444-446` |
| `doctor` 内部已有 ok/detail/fix | `src/fcmd/cli/_doctor_helpers.py:29,46,64,83,95,107,114-140` |
| `env --json` 是少数结构化出口 | `src/fcmd/cli/_builtins/env_cmd.py:31` |
| 未知工具仅有 difflib 建议 | `src/fcmd/cli/_common.py:17-27` |
| 无子命令时打印列表并退出 0 | `src/fcmd/core/toolkit/exec.py:240-249` |
| `yaml` 的 job 依赖与 dry-run | `src/fcmd/cli/_builtins/yaml_cmd.py:19-67` |

## 附录 B：复现命令清单

> 以下命令用于人工验证现状，本轮未执行（工作区为未受信目录）。退出码为按源码推导的预期值。

```bash
# G3：错误退出 0
fcmd jsontool query missing.json a.b ; echo $?      # 预期 0
fcmd hashfile missing.txt --algorithm sha256 ; echo $?  # 预期 0
fcmd textdiff files a.txt b.txt ; echo $?           # 有差异预期仍为 0

# G3/G4：异常被吞，退出 1 且终端通常无文本
fcmd hashfile existing.txt --algorithm nosuch ; echo $?

# G3：同一工具族的相反信号（正确范式）
fcmd filescan scan missing-dir ; echo $?            # 预期 2
fcmd dynacalc model missing.yaml ; echo $?          # 域错误预期 2

# G1：自描述缺失
fcmd info filescan scan
fcmd filescan scan --help

# G5：长输出
fcmd filesearch content . TODO
fcmd hashfile d . --algorithm sha256

# G6：能力只在 GUI
fcmd checksum                                       # 打开 Tk 窗口
fcmd cmderw                                         # 打开文件管理器
```

---

## 9. P0 第一批落地记录（2026-11-13）

### 9.1 选型与理由

第一批只挑“高频、输出最容易被误读、错误最容易被吞”的入口，先把人机双读范式跑通，再复制到其余工具。

| 工具 | 子命令 | 为什么是它 | 小模型的典型调用 |
|---|---|---|---|
| `filesearch` | `name` / `content` | 高频；`path:line:text` 在 Windows 盘符与含 `:` 文件名下存在切分歧义；输出无上限；错误与无匹配都退出 0 | 找文件、找 TODO/调用点 |
| `hashfile` | `f` / `d` | 高频；输出是“算法/摘要/路径”三元组，路径含空格无法稳定切分；非法算法抛 `ValueError` 被框架吞掉 | 校验下载产物、比对构建结果 |
| `textdiff` | `file` / `dir` | 结论只有“同/不同”，但两种结果都退出 0、无增删计数，模型无法自检改动是否生效 | 判断改动是否落盘 |
| `info`（内建） | — | 自描述是参数幻觉的根因；文本表格与 JSON 共用一份 schema 才能不漂移 | 调用前先查参数与示例 |

未选 `pdftool` / `peview`：子命令多、表格重、改造面大，放到第二批。

### 9.2 范式模板（后续工具照抄）

```python
@fx.tool(
    "<tool>",
    subcommand="<sub>",
    help="<一句话>",
    param_help={...},          # 进入 --help 与 info 的参数表
    examples=["fcmd <tool> <sub> ...", "fcmd <tool> <sub> ... --json"],
)
def sub(..., json_output: bool = False) -> None:  # json_output 自动获得 --json 别名
    result = ...                                   # 1. 先算结构化数据
    if 输入非法:
        if json_output:
            emit_error("<tool>", "<sub>", code="...", message="<人话>",
                        field="<参数名>", expected="<期望形态>", hint="<修正建议>")
            return
        print("<保持原有错误行>")                    # 2. 默认路径逐字不变
        return
    if json_output:
        emit_result("<tool>", "<sub>", data)        # 3. 同一份 data 的机器呈现
        return
    ...                                             # 4. 同一份 data 的人类呈现
```

配套约定：

- `core/result.py`：`fcmd-result/1`（stdout）、`fcmd-error/1`（stderr）、`fcmd-schema/1`（`info --json`）。
- `core/toolkit/args.py`：`ToolSpec.examples` 进入 `--help` 末尾；`param_schema()` 从签名推导参数表；`json_output` → `--json` 别名。
- `cli/_schema.py`：工具/子命令/参数/示例的统一投影，文本 `fcmd info` 与 `--json` 同源；`gui` 标记覆盖 29 个窗口工具。
- `--json` 时框架自动静默进度/汇总行（`exec.py` 的 `verbose` 条件），保证 stdout 可整体 `json.loads`。

### 9.3 默认输出是否变化

**没有变化。** 既有人类可读路径逐字保留，新增开关才产生新行为：

- `filesearch name/content`：逐行路径 / `path:line:text` 与“（无匹配）”不变；只有传 `--limit` / `--count` 才出现截断脚注或纯计数。
- `hashfile f/d`：`算法  摘要  路径` 不变；非法算法在默认模式下仍抛 `ValueError`（框架按失败处理），只有 `--json` 才转成结构化错误。
- `textdiff file/dir`：diff 原文与“文件内容相同”不变；`--json` 才输出计数与字段。
- `fcmd info` 文本输出：原表格保留，**新增**“参数 / 示例”两段（纯增量）。
- 既有测试全部保留通过（含“错误场景退出 0”的历史断言），证明默认行为未变。

### 9.4 门禁数字

| 门禁 | 结果 |
|---|---|
| `ruff check src tests` | 通过 |
| `ruff format --check` | 通过 |
| `pyrefly check` | 通过 |
| `pytest -m "not slow"` | **6898 passed, 9 skipped, 11 deselected** |
| 覆盖率 | **94.4%**（低于 95% 阈值；该既有缺口已在第二批按门禁要求补齐并给出基线量化对比，见 §10.4） |
| 静态清单 | 已按项目要求重生成 `_manifest_data.py`，**仅 `DIGEST` 一行变化**（`git diff` 可核对） |

新增/改动文件覆盖率：`core/result.py` 100%、`cli/_schema.py` 100%、`cli/_builtins/info_cmd.py` 100%、`tools/system/filesearch.py` 100%、`tools/crypto/hashfile.py` 100%、`tools/text/textdiff.py` 100%。

### 9.5 与计划的偏差

- P0-5 的实际子命令名是 `textdiff file`（计划表中写作 `files`）。
- P0-2（`doctor --json`）与 P0-4（`hashfile dupes`）不在第一批，已在第二批补齐（见 §10）。
- 退出码未动（按 §5.1 约束留到 P1）；`--json` 下的输入错误目前仍是 `ok=false` + 退出 0，模型需读 `ok` 而不是只看 `$?`（过渡说明见 §10.3）。
- 改动直接落在 `F:\Dev\fcmd`：该目录已是唯一主开发位置，不再维护 WSL 副本，也不需要手工移植。

### 9.6 第二批（按序推进，不再等待确认；1、2 已完成，见 §10）

1. `fcmd doctor --json`：复用既有 `checks[{item,ok,detail,fix}]`，`ok=false` 时退出码保持现状。
2. `fcmd hashfile dupes <dir>`：先把 `find_duplicates` 从 `apps/cmder/_dupes.py` 下沉到 `infras/dupes.py`（满足 ADR 0001），再挂只读子命令，输出 `{groups,total_groups,reclaimable_bytes}`。
3. 继续按范式铺 JSON：`peview summary/sections/imports/check`、`filesync compare`、`renamer preview`、`sysinfo`、`csvtool show`（已完成，见 §11）。
4. 每批沿用同一验收：默认输出快照不变、`--json` 可 `json.loads`、错误含 `code/field/hint`、新增代码 100% 覆盖。

---

## 10. P0 第二批落地记录（2026-11-13）

### 10.1 本批内容

| 项 | 状态 | 落地方式 |
|---|---|---|
| P0-2 `fcmd doctor --json` | 完成 | 检查项仍是既有 `checks[{item,ok,detail,fix}]`；新增 `doctor_result()` 把同一份检查项汇总为 `{ok,passed,failed,total,checks}`，表格与 JSON 共用该汇总，避免两套计数逻辑漂移 |
| P0-4 `fcmd hashfile dupes <dir>` | 完成 | 只读查重（不删除、不移动）。先按 ADR 0001 把纯逻辑从 `apps/cmder/_dupes.py` 下沉到 `infras/dupes.py`（`find_duplicates` / `DuplicateGroup` / `DuplicateReport` / `duplicate_paths` / `report_to_mapping`）；GUI 侧只保留 `move_duplicates` 并重新导出查找 API，既有调用方与测试无需改动 |

`hashfile dupes` 的人类输出（新增命令，无历史输出需兼容）：

```
重复文件: 2 组，可回收 101.0 B（扫描 4 个文件，算法 sha256）

[1] 100.0 B  sha256:2c26b46b68ff  保留: D:\a\big1.bin
    重复: D:\a\big2.bin

（共 2 组，已显示前 1 组；--limit 0 查看全部）   # 仅 --limit 截断时出现
```

JSON（`--json`）字段：`algorithm` / `scanned` / `total_groups` / `reclaimable_bytes` / `returned` / `truncated` / `groups[{size,digest,wasted,paths}]`。
错误码：`DIR_NOT_FOUND`（`field=directory`）、`INVALID_ALGORITHM`（`expected` 列出合法算法）、`INVALID_MIN_SIZE`（`field=min_size`，`expected=">= 0 的整数字节数"`）。
默认路径沿用既有行为：非法算法/负数阈值抛 `ValueError`（框架按失败处理），目录不存在打印一行后返回。

### 10.2 默认输出是否变化

- `fcmd doctor`：标题、`OK/FAIL` 表格、`修复:` 建议、`诊断结果: x/y` 汇总逐字不变；只有显式 `--json` 才有新形态。
- `fcmd hashfile f/d`：不变。
- `fcmd hashfile dupes`：新增子命令，默认给人类摘要（组数、可回收量、保留项/重复项），不改变任何既有命令输出。
- `apps/cmder/_dupes.py` 的 GUI 行为不变（`move_duplicates` 逻辑与调用方未动，查找 API 由 `infras.dupes` 提供）。

### 10.3 过渡说明：`--json` 下的输入错误仍退出 0

P0 阶段**不改退出码语义**（统一语义留到 P1，见 §5.2）。因此在 `--json` 下遇到输入错误（目录不存在、非法算法、负数阈值等）时：

- stderr 输出 `fcmd-error/1`：`ok=false`，含 `code` / `field` / `expected` / `hint` 与一句人话 `message`；
- stdout 为空字符串（不会混入半截 JSON）；
- **进程退出码仍为 0**。

调用方不能只看 `$?`，必须读 `ok` 字段判定成败；这条约束持续到 P1 统一错误退出码（改为 2/1）为止，届时按破坏性变更在文档与 CHANGELOG 标注。

### 10.4 门禁数字（含覆盖率基线对比）

| 门禁 | 结果 |
|---|---|
| `ruff check src tests` | 通过 |
| `ruff format --check src tests` | 通过（517 files already formatted） |
| `pyrefly check` | 0 errors |
| `pytest -m "not slow"` | **6981 passed, 9 skipped, 11 deselected** |
| 覆盖率 | **95.15%**（34123 语句 / 1230 未覆盖；10672 分支 / 837 部分覆盖） |
| 静态清单 | 已重生成 `_manifest_data.py`，仅 `DIGEST` 一行变化 |

覆盖率改动前后对比（同一台 Windows 机器、同一命令 `pytest -m "not slow" --cov=fcmd`；基线用 `git worktree` 检出 HEAD `fa71899` 实测）：

| 口径 | 语句（覆盖/总数） | 分支（覆盖/总数） | 覆盖率 |
|---|---|---|---|
| 改动前基线（HEAD 代码 + 原测试集） | 32307 / 33814 | 9555 / 10540 | **94.38%** |
| 只加本批代码（+309 语句、+132 分支，新增代码 100% 覆盖） | 32616 / 34123 | 9687 / 10672 | 94.44% |
| 终值（本批代码 + 同步补的既有缺口测试） | 32893 / 34123 | 9731 / 10672 | **95.15%** |

结论：**本批代码本身只把覆盖率从 94.38% 抬到 94.44%（+0.06pp）**，因为新增 441 个计量单元全部被新测试覆盖，分子分母同增。离 95% 差的 0.56pp 全部来自既有缺口，不能靠新增功能代码补齐；为了让门禁真正变绿，本批另补了既有模块的测试，新增覆盖既有 **277 语句 + 44 分支 = 321 个单元**，才从 94.44% 推到 95.15%（+0.71pp）。

既有缺口分布（改动前 1507 未覆盖语句 + 845 部分分支，按未覆盖语句降序）：

| 模块 | 未覆盖语句 | 性质 |
|---|---:|---|
| `apps/cmder/_ops.py` | 114 | Tk 文件操作菜单分支，依赖真实窗口与交互 |
| `apps/dynacalc/_params.py` / `_model.py` / `_mesh.py` | 68 / 65 / 51 | 参数矩阵与模型边界分支，需构造大量非法/边界输入 |
| `apps/paramesh/_model.py` | 61 | 网格模型边界校验 |
| `apps/dynacalc/dynacalc.py` | 53 | `demo` 正常路径只被 `slow` 用例覆盖（门禁 `-m "not slow"` 排除） |
| `apps/filescan/_engine.py` / `_config.py` | 47 / 30 | 扫描引擎与配置加载的异常分支 |
| `apps/paramcad/_model.py` / `_export.py` | 40 / 36 | 参数化模型与导出边界 |
| `apps/cmder/app.py` | 40 | 主窗口菜单与状态机 |

这些模块都属于 GUI/桌面应用交互层，与本批「工具接口双读」的改动面无关；因此**不能**用本次新增的代码路径去覆盖它们，也不应通过修改 `.coveragerc`、CI 或加 `pragma: no cover` 来绕开。本批的处理方式：只加测试（未改 `.coveragerc` / `pytest.ini` / CI / 任何 `src/fcmd` 业务代码，未加 `pragma: no cover/branch`），定向补齐 `alarmclock` 窗口、`dynacalc/_fakesolver`、`filescan` 异常分支与 dynacalc CLI 失败路径。

剩余未覆盖项（1230 语句 + 837 部分分支）中，已确认不可达/不应补的典型：

- `apps/dynacalc/dynacalc.py` 的 `demo` 正常路径：仅 `slow` 用例覆盖，门禁口径排除；
- `apps/filescan/filescan.py:391-392,438-439`：加载阶段已用 `parse_expression` 校验，`resolve_expression` 失败分支不可达；
- `tools/system/alarmclock.py:438→441,461→464`：`_blink_timer` / `_tick_timer` 为 `None` 的防御分支，正常流程中成对存在；
- 其余为各 GUI 窗口的布局/交互分支，属既有技术债，留待 P1 契约改造时按模块补。

---

## 11. P0 第三批落地记录（2026-11-13）

### 11.1 本批内容

| 子命令 | `--json` 数据 | 备注 |
|---|---|---|
| `sysinfo` | `os/platform/arch/python/python_path/cpu/cpu_cores/cwd/boot_time`，分组 `memory{total,used,available}` / `swap{total,used}` / `disk{total,used,free}` | 平台取不到的项直接省略，不补空值、不做有损换算 |
| `csvtool show` | `file/rows/total_rows/returned/truncated/columns/data` | 空文件与 `--no-header` 仍返回结构（`total_rows=0`、`columns=[]`），不制造只存在于机读形态的错误 |
| `peview info` | `path/name/size/is_dll/is_64bit/machine/machine_name/timestamp/image_base/entry_point/subsystem/subsystems/section_count/import_dlls/import_symbols/export_count/resource_count/relocation_blocks/relocation_count/debug_count/checksum/signed/overlay_offset/overlay_size` | 计划表里的 `summary` 在仓库中实际叫 `info`，按实名落地 |
| `peview sections` | `sections[{name,virtual_address,virtual_size,raw_offset,raw_size,characteristics}], total` | |
| `peview imports` | `dlls[{name,functions}], total_dlls, total_functions` | hint、描述符偏移等细节仍只出现在人类输出；JSON 精简但同源解析 |
| `peview check` | `ok,checks[{item,ok,detail}],passed,failed,total` | 与 `doctor` 同一形状 |
| `filesync compare` | `left,right,equal,only_left,only_right,different,errors[{path,error}]` | |
| `renamer preview` | `total,items[{source,target,changed}],conflicts[{source,target,reason}]` | |

结构处理：`apps/peview/_report.py` 抽 `summary_data/sections_data/imports_data/check_data` 与 `_checksum_values/_alignment_problem/_entry_point_problem/_section_problems/_signature_problem`，`format_*` 改为从同一份数据渲染；`apps/filesync/_core.py` 加 `compare_result_data`；`apps/renamer/_core.py` 加 `preview_data`。没有第二套解析。

### 11.2 默认输出是否变化

不变。默认分支文案逐字保留（peview/filesync/renamer 既有 421 个用例与 csvtool/sysinfo 用例原样通过）。错误双读：`--json` 走 `fcmd-error/1`（`FILE_NOT_FOUND` / `DIR_NOT_FOUND` / `NOT_PE_FILE` / `SAME_DIRECTORY` / `INVALID_RULES`，带 `field` 与 `hint`），默认路径仍是原 `print_error` 与原退出码。

### 11.3 门禁数字

| 门禁 | 结果 |
|---|---|
| `ruff check src tests` / `ruff format --check` | 通过 / 517 files already formatted |
| `pyrefly check` | 0 errors |
| `pytest -m "not slow"` | **7013 passed, 9 skipped, 11 deselected** |
| 覆盖率 | **95.18%**（34252 语句 / 1228 未覆盖；10724 分支 / 836 部分覆盖） |
| 静态清单 | 重新生成 `_manifest_data.py`，仅 `DIGEST` 一行变化 |

### 11.4 已知偏差

- `peview` 没有 `summary` 子命令，实际为 `info`；
- `csvtool show` 的 `--no-header` 是合法模式，不能当成“缺表头”报错（否则错误只在机读形态出现）；
- 退出码语义仍未动，`--json` 下输入错误仍是 `ok=false` + 退出 0（过渡说明见 §10.3）。

---

## 12. 一句话总结


fcmd 的工具“能做很多事”，但当前更像一套给人用的命令集合：参数靠读文档、结果靠肉眼看、失败靠猜。把小模型当作第一调用者，不需要重写工具，只需要补上四层薄契约——**自描述 schema、结果信封、错误对象与退出码、计划/断言**——就能把现有 118 个工具中的大部分从“需要大模型解释”变成“小模型可直接调用”。
