# 帮助文档索引

帮助内容分三层，按「越往下越完整」组织：

| 层 | 入口 | 内容 |
|---|---|---|
| 短 | `fcmd <tool> --help` | 参数、示例、环境变量、退出码、相关命令（`ToolSpec.help_sections`） |
| 中 | `fcmd info <tool> [subcommand]` | 同上 + 必填 / 默认值 / 可选值 + 编排字段 |
| 长 | 本文索引的包内 Markdown | 完整说明、界面分区、常见问题与设计链接 |

包内 Markdown 是完整说明的**唯一来源**，随 wheel 一起发布（`src/fcmd/help/`）；
GUI 的「帮助 → 使用帮助」（`F1`）与本文索引都指向同一份文件。新增 / 改名文档后
同步更新本页，`tests/shared/test_helpdoc.py` 会校验索引与相对链接。

## 工具帮助

| 工具 | 文档 | 说明 |
|---|---|---|
| `renamer` / `renamerw` | [renamerw.md](../../src/fcmd/help/renamerw.md) | 批量重命名：规则流水线、预览状态、执行与撤销、典型示例 |
| `regexvalidate` / `regexvalidatew` | [regexvalidatew.md](../../src/fcmd/help/regexvalidatew.md) | 正则校验：元字符、分组、标志、结果区读数与常见问题 |

## 命令行帮助格式

`help_sections` 支持的 `kind` 与默认标题（渲染顺序固定）：

| kind | 标题 | 用途 |
|---|---|---|
| `usage` | 用法 | 最短命令序列 / 前置条件 |
| `examples` | 示例 | 可直接复制的完整命令（与 `ToolSpec.examples` 合并） |
| `env` | 环境变量 | `FCMD_*` 等开关 |
| `exit_codes` | 退出码 | 0 / 1 / 2 的语义 |
| `files` | 文件 | 配置、缓存、输出路径 |
| `see_also` | 相关 | 同类命令、GUI 版、ADR / 文档链接 |
| `notes` | 说明 | 注意事项、已知边界 |

示例：

```python
from fcmd.shared.helpdoc import HelpSection


@fcmd.tool(
    "demo",
    examples=["fcmd demo . --json"],
    help_sections=[
        HelpSection("env", lines=["FCMD_HOME  配置目录（默认 ~/.fcmd）"]),
        HelpSection("see_also", lines=["GUI：fcmd demow"]),
    ],
)
def demo() -> None:
    """演示。"""
```

未声明 `help_sections` 时，`--help` 的输出与旧版完全一致（只多一个「示例」段）；
改完分段后需要重新生成帮助快照：

```powershell
uv run python -m fcmd.cli._manifest_data
```

## 相关文档

- [CLI 最短命令序列](../cli.md)
- [ADR 0052：帮助文档通用组件](../adr/0052-help-doc-components.md)
- [AI 自动驱动开发流](../AI_DRIVEN_DEV_FLOW.md)
