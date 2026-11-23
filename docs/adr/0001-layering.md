# 0001 分层与依赖方向

- 状态：已落地（P1 / P2 完成）
- 日期：2026-11-10（2026-11-11 更新：移除 DSL，新增 infras；core/shared、core/filefilter、core/models 与 core/env_persist 并入 infras）

## 背景

改造前存在反向依赖（`core` 菜单式地引用 `cli` 的实现）与职责混杂（业务实现散落在 `dsl/actions`，`cli` 承担了本应属于 `core` 的持久化逻辑），导致循环导入只能用函数内懒导入掩盖，测试也难以按层定位。

## 决策

分层自下而上：

```
shared          叶子层：纯标准库路径 / 格式化（core/gui/infras/tools/apps 均可依赖）
core            基础能力（toolkit / execution / orchestration / context / dag / task / profiling / report）
gui             展示基座（Qt 窗口骨架 / 主题 / UI 设置持久化 / 控件）
infras          基础件：编解码、换算、哈希、加解密、文本/数据/文件/系统/网络/文件过滤等纯函数
tools/<域>      业务实现（每域一份实现，含 CLI / GUI 入口）
apps            独立桌面应用（Qt GUI，与 tools 同为工具发现根，可复用 gui / tools / infras）
cli             组合根：参数解析、工具发现、内建命令（展示适配在 presentation）
mcp             可选集成层：MCP server / 安全策略 / 白名单
```

依赖规则：

1. `shared` 只依赖标准库，不得 import 任何其他 `fcmd` 包。
2. `core` 只依赖标准库、`shared` 与自身，不得 import `gui` / `infras` / `tools` / `apps` / `cli` / `mcp`；展示层只能在函数体内懒导入 `fcmd.console`。
3. `gui` 只依赖标准库、`shared` 与 Qt，不得 import `core` / `infras` / `tools` / `apps` / `cli` / `mcp`；`tools` 的窗口模块依赖 `gui`。
4. `infras` 只依赖标准库与 `shared`，不得 import `core` / `gui` / `tools` / `apps` / `cli` / `mcp`；工具命令只做参数适配与输出，计算与基础操作下沉到 `infras`。
5. `tools` 依赖 `infras` / `gui` / `core` / `shared`；不同工具域之间不互相 import 实现。
6. `apps` 是独立应用层，可依赖 `gui` / `tools` / `infras` / `shared`；不得反向依赖 `cli` / `mcp`。
7. `mcp` 是可选的集成层，可依赖 `cli` / `core` / `shared`，不得 import `tools` / `apps`。
8. `cli` 是唯一组合根，可依赖以上全部层；`tools` / `apps` 不得反向依赖 `cli`。
9. 上述方向由 `tests/test_architecture.py` 的 AST 扫描守卫（懒导入单独校验）。

依赖反转（D4 配套）：

- `core/hooks.py` 提供 `register_discovery_hook`。
- 组合根 `fcmd/__init__.py` 把工具发现实现（`fcmd.cli._discovery.ensure_tools_discovered`）注册给 `core`。
- `core` 侧只调用 `ensure_discovered()`，不感知上层实现。

## 落地状态

- P1-1：环境变量持久化下沉至 `infras/system.py`，消除 `core → cli` 反向依赖。
- P1-2：新增 `core/hooks.py`，discovery 改为注册式依赖反转。
- P1-3：`core/models/pdf.py` → `tools/pdf/models.py`，模型归属业务域。
- P1-4：网络探测能力下沉至 `infras/net.py`，`tools/dev`、`apps/llm` 共用同一实现。
- P2：移除 TOML DSL 与 `dsl` 包；全部命令改为 `tools/<域>` 下的 `@fcmd.tool` 模块，纯计算迁入 `fcmd/infras/`（见 ADR 0003）。
- P2-1：`core/filefilter.py` → `infras/filefilter.py`；`core/shared/textio.py` → `infras/textio.py`；`core/shared/net.py` 并入 `infras/net.py`，`core/shared` 包移除。
- P2-2：`core/models/command.py` → `infras/command.py`；`core/version.py` → `infras/version.py`；`core/env_persist.py` 并入 `infras/system.py`，`core/models` 包移除。
- P3：新增 `fcmd/shared/`（`paths.fcmd_home` / `formatting.format_bytes`）；`gui.config` 与 `gui.widgets.storage`
  改依赖 shared，移除 gui → infras 的反向依赖；`tests/test_architecture.py` 把 import 方向守卫变成红灯。

## 交接

- 新增工具沿用 `tools/<域>/<工具>.py` + `@fcmd.tool`；不得在工具模块内堆叠可复用的纯计算，抽到 `infras/<主题>.py`。
- 新增基础件：放 `infras/<主题>.py`（主题继续长大后拆为 `infras/<主题>/` 子包，`__init__.py` 保持旧导入路径），
  只写纯函数/基础操作，不打印、不依赖 argparse 与工具注册表。
- 新增跨层纯函数（路径 / 格式化等）：放 `fcmd/shared/`，只依赖标准库；不要让 gui 直接依赖 infras。
- 新增跨层能力时先判断归属：纯计算/基础操作进 `infras`，业务编排进 `tools/<域>`；`core` 不再承载通用基础件，新增对外依赖必须走 hooks 反转。
- 测试镜像目录（`tests/{core,cli,infras,tools/<domain>,apps/<app>}`）随实现所在层放置。
