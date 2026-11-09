# 0001 分层与依赖方向

- 状态：已落地（P1 完成，P2 继续沿用）
- 日期：2026-11-10

## 背景

改造前存在反向依赖（`core` 菜单式地引用 `cli` 的实现）与职责混杂（业务实现散落在 `dsl/actions`，`cli` 承担了本应属于 `core` 的持久化逻辑），导致循环导入只能用函数内懒导入掩盖，测试也难以按层定位。

## 决策

分层自下而上：

```
core            基础能力（toolkit / execution / orchestration / models / context / dag / task / profiling / report / env_persist / filefilter）
core/shared     中立共享层：dsl 与 tools 都向下依赖
tools/<域>      业务实现（每域一份实现，含 CLI / GUI 入口）
dsl             声明解析、exec / compute 原语、动作注册表门面
cli             组合根：参数解析、工具发现、内建命令
```

依赖规则：

1. `core` 只依赖标准库与自身，不得 import `tools` / `dsl` / `cli`。
2. `core/shared` 只依赖 `core` 与标准库；`dsl` 与 `tools` 均可依赖它。
3. `tools` 与 `dsl` 之间不互相 import：业务实现放 `tools`，`dsl/actions` 只做声明与转接。
4. `cli` 是唯一组合根，可依赖以上全部层。

依赖反转（D4 配套）：

- `core/hooks.py` 提供 `register_discovery_hook` / `register_action_resolver`。
- 组合根 `fcmd/__init__.py` 把工具发现实现（`fcmd.cli._discovery.ensure_tools_discovered`）注册给 `core`；`fcmd/dsl/__init__.py` 把动作解析器（`get_action`）注册给 `core`。
- `core` 侧只调用 `ensure_discovered()` / `resolve_action()`，不感知上层实现。

## 落地状态

- P1-1：`cli/_env_persist.py` → `core/env_persist.py`，消除 `core → cli` 反向依赖。
- P1-2：新增 `core/hooks.py`，discovery 与 action 解析改为注册式依赖反转。
- P1-3：`core/models/pdf.py` → `tools/pdf/models.py`，模型归属业务域。
- P1-4：新增 `core/shared/net.py`，网络探测能力从 `dsl/actions/net.py` 下沉，`tools/dev`、`tools/llm` 与 `dsl` 共用同一实现。
- P2：图片域业务实现迁入 `tools/media/mediabase.py`（见 ADR 0002）。

## 交接

- 后续域迁移沿用 `tools/<域>` 承载实现的模式，不得在 `dsl/actions` 保留第二份实现。
- 新增跨层能力时先判断归属：中立能力进 `core/shared`，业务能力进 `tools/<域>`，`core` 新增对外依赖必须走 hooks 反转。
- 测试镜像目录（`tests/{core,cli,dsl,tools/<domain>}`）已随 P2-3 落地，新增测试按被测实现所在层放置。
