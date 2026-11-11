# 0014 批量重命名：规则流水线 + 正则重排 / 搬移

- 状态：已落地（v1）
- 日期：2026-11-11
- 关联：需求卡片 [0014](../requirements/0014-batch-renamer.md)、0009（cmder 多重命名）

## 背景

现有改名能力分散且各有边界：

- `tools/system/filerename.py` 只有字面替换、插入与大小写，没有预览与冲突检测。
- `apps/cmder/_rename.py` 的多重命名依赖 cmder 面板与选择状态，无法独立批处理，
  且位置调整只能靠正则替换模板间接完成。
- 用户需要在 `apps/` 下有独立工具，规则可组合、可预览、可撤销，并能明确表达
  「正则匹配后把内容搬到另一个位置」。

## 决策

### 1. 独立 `fcmd.apps.renamer` 包，CLI 与 Tk 共用纯逻辑

```
src/fcmd/apps/renamer/
  _core.py      # 规则求值 / 预览 / 冲突 / 两阶段执行 / 撤销（无 Tk、无 IO 终端）
  _examples.py  # 内置帮助文本与典型规则示例（纯数据，窗口与测试共用）
  renamer.py    # CLI：preview / apply
  renamerw.py   # Tk：目标选择、规则表、预览、执行、撤销、规则 JSON 导入导出、使用帮助
```

`_core` 不依赖 `fcmd.gui`，CLI 可在无显示环境使用；窗口只做表单与渲染，所有
求值与执行都调用 `_core`。Tk 类标注 `pragma: no cover`，与 0013 的 GUI 口径一致。

### 2. 规则是有序流水线，作用于名称主干或扩展名

| kind | 语义 | 关键字段 |
|---|---|---|
| `replace` | 正则替换，模板用 `\1` / `\g<name>` | pattern / replacement / occurrence / ignore_case |
| `reorder` | 捕获组按 `{0}`、`{1}`、`{name}` 重排 | pattern / replacement |
| `move` | 把匹配文本整体搬到锚点 | pattern / anchor / anchor_text / position / occurrence |
| `insert` | 在锚点插入字面文本 | text / anchor |
| `case` | 大小写转换 | mode |
| `sequence` | 按批内排序插入计数器 | start / step / digits / text / anchor |

`field=stem` 为默认，`field=ext` 可单独处理扩展名；目录忽略扩展名规则。
`occurrence=0` 表示全部匹配，`N` 表示第 N 个（越界视为未匹配，不报错）。

**位置调整有两种正交口径**：

1. `reorder` 解决「片段顺序」：`^(\d{4})-(.+)$` + `{2}-{1}` 把日期从前面换到后面。
2. `move` 解决「整段位置」：`^\[(.+?)\]` + `anchor=end` 把方括号内容搬到结尾。

`move` 只搬运匹配到的原始文本，不自动吞并分隔符；需要处理 `2024_title` 这类
前缀时，把 `_` 写进正则（如 `^\d{4}_`）即可，避免隐式规则造成意外。

### 3. 预览与执行口径

- 预览状态：`ok` / `unchanged` / `invalid` / `conflict`。非法名称（空、路径分隔符、
  Windows 保留字符、超过 255 字符）与批内重名、目标已存在都只标记，不中断整批。
- 规则级错误（未知类型、非法正则、缺字段）直接返回 `RenamePreview.error`，不产生半套结果。
- 执行：先校验目标占用，再把所有源改到同目录唯一临时名，最后改到目标名。
  两阶段可正确处理 `a↔b` 互换与仅大小写改名；任一阶段失败时尽力回滚。
- 撤销：`RenameReport.completed` 记录 (源, 目标)，按相反顺序再走一次两阶段。
  只保证本次运行内的撤销，不持久化历史。

### 4. 规则输入与落地

- CLI：`--rules` 接受内联 JSON 或 `@rules.json`；`preview` 只打印计划，`apply`
  先打印计划再执行。
- GUI：规则表支持增删改与上下移，规则导出 / 导入 JSON；目标列表、规则、递归与
  排序选项、窗口位置持久化到 `~/.fcmd/ui/renamerw.json`。预览默认隐藏「旧名」列，
  仅显示新名与状态；非法 / 冲突的具体原因不单列，刷新后按次数归并到状态栏。
- 帮助：「帮助 → 使用帮助」打开非模态窗口，左侧为典型示例（双击或按钮写入规则表，
  已有规则时确认覆盖），正文为规则速查；完整说明见 `help/renamerw.md`，
  示例规则同时落在 `help/examples/*.json`，测试校验两者与 `_examples.py` 一致。
- 新增工具后重新生成 `fcmd/cli/_manifest_data.py`，保持列表快路径有效。

## 备选方案与取舍

| 方案 | 不采用的原因 |
|---|---|
| 扩展 `tools/system/filerename.py` | 现有口径是单条规则、无预览/冲突/撤销；改成流水线会破坏其简单 CLI 契约 |
| 直接复用 cmder 的 `_rename` / `_ops` | 依赖 cmder 包初始化（会拉起 Tk）与面板选择状态，且不适配独立批处理 |
| 只做 `replace` 正则替换 | 无法显式表达「把匹配段落搬到开头/结尾」；用户不得不写复杂的前后向断言 |
| 每条规则一个 dataclass 子类 | 规则需 JSON 往返与 GUI 表单统一读写，单 dataclass + `kind` 更简单，校验集中在 `validate_rules` |
| 单阶段顺序改名 | 无法处理互换与 Windows 仅大小写改名，容易误覆盖 |

## 落地状态

- 代码：`_core.py`、`_examples.py`、`renamer.py`、`renamerw.py`；`pyproject.toml` 新增
  `renamer` / `renamerw` 入口；静态工具清单已重新生成。
- 测试：`tests/apps/renamer/` 覆盖规则求值、模板错误、JSON、排序、冲突、
  目标收集、互换、回滚与撤销；内置示例逐条跑通演示改名，并校验
  `help/examples/*.json` 与 `_examples.py` 不漂移；GUI 只测纯函数与注册。
- 文档：README 工具表新增两行；`help/renamerw.md` 提供规则说明与典型示例；
  本 ADR 与需求卡片 0014。

## 交接

- 下一期可加：规则预设管理、按文件名通配过滤、执行前逐项勾选、撤销历史持久化。
- 已知边界：`move` 的 `before` / `after` 只匹配锚点文本首次出现；多个匹配搬到
  同一锚点时按匹配顺序拼接；回滚为尽力而为，磁盘级故障仍可能留下临时名。
- 扩展规则时先改 `_core.RenameRule` / `validate_rules` / `apply_rule` 与 JSON
  字段表，再同步 GUI 表单与 README，最后补 ADR 口径行与本表。
- 修改帮助或示例时同步 `_examples.py`、`help/renamerw.md` 与
  `help/examples/*.json`；示例一致性由测试拦截，勿只改其中一处。
