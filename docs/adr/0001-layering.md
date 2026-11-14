# 0001 分层与依赖方向

- 状态：已落地（P1 / P2 完成）
- 日期：2026-11-10（2026-11-11 更新：移除 DSL，新增 infras；core/shared、core/filefilter、core/models 与 core/env_persist 并入 infras）

## 背景

改造前存在反向依赖（`core` 菜单式地引用 `cli` 的实现）与职责混杂（业务实现散落在 `dsl/actions`，`cli` 承担了本应属于 `core` 的持久化逻辑），导致循环导入只能用函数内懒导入掩盖，测试也难以按层定位。

## 决策

分层自下而上：

```
core            基础能力（toolkit / execution / orchestration / context / dag / task / profiling / report）
gui             展示基座（tkinter 窗口骨架 / 主题 / UI 设置持久化），只依赖标准库
infras          基础件：编解码、换算、哈希、加解密、文本/数据/文件/系统/网络/文件过滤等纯函数
tools/<域>      业务实现（每域一份实现，含 CLI / GUI 入口）
apps            独立桌面应用（Tk GUI，与 tools 同为工具发现根，可复用 gui / tools / infras）
cli             组合根：参数解析、工具发现、内建命令
```

依赖规则：

1. `core` 只依赖标准库与自身，不得 import `gui` / `infras` / `tools` / `apps` / `cli`。
2. `gui` 只依赖标准库（tkinter），不得 import `core` / `infras` / `tools` / `cli`；`tools` 的窗口模块依赖 `gui`。
3. `infras` 只依赖标准库，不得 import `core` / `gui` / `tools` / `apps` / `cli`；工具命令只做参数适配与输出，计算与基础操作下沉到 `infras`。
4. `tools` 依赖 `infras` / `gui` / `core`；不同工具域之间不互相 import 实现。
5. `apps` 是独立应用层，可依赖 `gui` / `tools` / `infras`；`core` / `infras` / `tools` 不得反向依赖 `apps`。
6. `cli` 是唯一组合根，可依赖以上全部层。
7. `tools` / `apps` 不得反向依赖 `cli`；工具发现通过 hooks 反转（见下）。

依赖反转（D4 配套）：

- `core/hooks.py` 提供 `register_discovery_hook`。
- 组合根 `fcmd/__init__.py` 把工具发现实现（`fcmd.cli._discovery.ensure_tools_discovered`）注册给 `core`。
- `core` 侧只调用 `ensure_discovered()`，不感知上层实现。

## 落地状态

- P1-1：环境变量持久化下沉至 `infras/system.py`，消除 `core → cli` 反向依赖。
- P1-2：新增 `core/hooks.py`，discovery 改为注册式依赖反转。
- P1-3：`core/models/pdf.py` → `tools/pdf/models.py`，模型归属业务域。
- P1-4：网络探测能力下沉至 `infras/net.py`，`tools/dev`、`apps/locllm` 共用同一实现。
- P2：移除 TOML DSL 与 `dsl` 包；全部命令改为 `tools/<域>` 下的 `@fcmd.tool` 模块，纯计算迁入 `fcmd/infras/`（见 ADR 0003）。
- P2-1：`core/filefilter.py` → `infras/filefilter.py`；`core/shared/textio.py` → `infras/textio.py`；`core/shared/net.py` 并入 `infras/net.py`，`core/shared` 包移除。
- P2-2：`core/models/command.py` → `infras/command.py`；`core/version.py` → `infras/version.py`；`core/env_persist.py` 并入 `infras/system.py`，`core/models` 包移除。

## 交接

- 新增工具沿用 `tools/<域>/<工具>.py` + `@fcmd.tool`；不得在工具模块内堆叠可复用的纯计算，抽到 `infras/<主题>.py`。
- 新增跨层能力时先判断归属：纯计算/基础操作进 `infras`，业务编排进 `tools/<域>`；`core` 不再承载通用基础件，新增对外依赖必须走 hooks 反转。
- 测试镜像目录（`tests/{core,cli,infras,tools/<domain>,apps/<app>}`）随实现所在层放置。
