# 0052 帮助文档通用组件

- 状态：已落地（P0 / P1 / P2）
- 日期：2026-11-15
- 需求卡片：[0052](../requirements/0052-help-doc-components.md)

## 背景

`--help`、`fcmd info`、GUI 使用帮助三处各自维护文案：CLI 只有 `examples`
硬编码成「示例:」epilog（`core/toolkit/parser.py:186`），`ToolSpec` 没有
环境变量 / 退出码 / 相关文档等分段；GUI 侧 renamerw 用 `QPlainTextEdit`
展示 `_examples.HELP_TEXT`，regexvalidatew 另写 `_HelpDialog`
（`tools/text/_regexvalidatew_qt.py:65`），帮助菜单项也各自手插到「关于」之前。
同一工具的完整说明还分散在 `apps/renamer/help/`，与包内其他文档没有统一入口。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 新增 `shared/helpdoc.py` 作为唯一帮助数据层：`HelpSection` / `HelpDoc` / `parse_markdown` / `load_help_doc`；只依赖标准库，CLI 冷启动不导入 rich / Qt | `src/fcmd/shared/helpdoc.py` |
| D2 | `ToolSpec` / `@fx.tool` 增加 `help_sections`；`--help` 的 epilog 由 `HelpDoc.with_examples(...).epilog()` 生成，未声明分段时与旧输出逐字一致 | `core/toolkit/spec.py`、`parser.py`、`__init__.py` |
| D3 | `fcmd info --json` 的子命令 schema 增加 `help_sections`，文本模式追加非示例分段；与 `--help` 共用同一份数据 | `cli/_schema.py`、`cli/_builtins/info_cmd.py` |
| D4 | 新增 `gui/help.py`：非模态 `HelpDialog`（Markdown 正文 + 目录 + 搜索 + 可选示例表 + 外链）与 `HelpMenuMixin`（F1「使用帮助」插到「任务日志 → 关于」之前） | `src/fcmd/gui/help.py` |
| D5 | 包内 Markdown 是完整说明的唯一来源：`src/fcmd/help/<tool>.md`；renamerw 文档从 `apps/renamer/help/` 迁入，示例 JSON 仍留在原处 | `src/fcmd/help/`、`docs/help/README.md` |
| D6 | renamerw / regexvalidatew 改用公共帮助窗口；示例表作为 `HelpDialog` 的可选插槽，由调用方提供回调 | `apps/renamer/_renamerw_qt.py`、`tools/text/_regexvalidatew_qt.py` |
| D7 | 分层披露：`--help` 短、`fcmd info` 中、包内 Markdown 长；文档索引与链接、epilog 快照进测试，防文案漂移 | `tests/shared/test_helpdoc.py`、`tests/gui/test_help.py` |

## 口径（C1..Cn → 落点）

| # | 约束 | 落点 |
|---|---|---|
| C1 | 跨 CLI / GUI 纯数据层 | `shared/helpdoc.py` |
| C2 | `ToolSpec.help_sections` + 同源渲染 | `core/toolkit/`、`cli/_schema.py`、`cli/_builtins/info_cmd.py` |
| C3 | GUI 通用帮助窗口 | `gui/help.py` |
| C4 | 帮助菜单装配统一 + 两个窗口迁移 | `gui/help.py`、renamerw、regexvalidatew |
| C5 | 包内 Markdown + `docs/help` 索引 | `src/fcmd/help/`、`docs/help/README.md` |
| C6 | 最佳实践（单一事实源 / 分层披露 / 防漂移） | 上述落点 + 测试 |

## 落地状态

- P0：`shared/helpdoc.py`、`ToolSpec.help_sections`、`--help` epilog、`fcmd info` 文本 / JSON 接入。
- P1：`gui/help.py`（`HelpDialog` / `HelpMenuMixin`）；renamerw、regexvalidatew 迁移。
- P2：`src/fcmd/help/renamerw.md`、`regexvalidatew.md`、`docs/help/README.md`；
  `tests/shared/test_helpdoc.py` 校验索引 / 链接 / 分段顺序，`tests/gui/test_help.py`
  覆盖目录 / 搜索 / 示例 / 关闭。

## 交接

- 新增工具帮助：优先在包内放 `src/fcmd/help/<tool>.md`，再用
  `load_help_doc("<tool>")` 装配 GUI；CLI 用 `help_sections=[HelpSection(...)]`
  声明环境变量 / 退出码 / 相关命令，不要再手写 argparse `epilog`。
- 修改帮助文案：只改包内 Markdown；CLI 分段改动后需重新生成
  `fcmd/cli/_manifest_data.py` 的帮助快照（`uv run python -m fcmd.cli._manifest_data`）。
- 新增帮助窗口：调用 `install_help_menu(doc, examples=..., on_example=...)`，
  不要自行往菜单插项或另写 QDialog。
- 已知边界：`HelpDialog` 用 `QTextBrowser.setMarkdown`，不渲染 Markdown 的
  脚注 / 数学公式；复杂表格建议收窄列数，或把细节留在 Markdown 由系统浏览器打开。
