# 0051 envdev 迁入 apps 与镜像源公用件

- 状态：已落地
- 日期：2026-11-24
- 关联：[0001](0001-layering.md)（分层）、[0003](0003-package-layout.md)（目录不变量）、
  [0046](0046-large-qt-app-split.md)（应用拆分）、[0023](0023-wslman.md)（WSL 下载源）

## 背景

envdev 工具族原本散在 `fcmd.tools.dev`：`envdev.py` / `envdev_core.py` /
`_envdev_langs.py` / `_envdev_linux.py` / `_envdev_mirrors.py` 以及
`mirrortool` / `mirrortoolw`。它已经是多入口应用（CLI + Qt），却仍按 tools 域
模块组织；镜像探测与配置逻辑同时被 envdev、mirrortool、mirrortoolw 与 wslman
复用，存在两份相似设计：

- envdev：服务候选表、测速选优、`MirrorSpec`（环境变量 + 配置文件）；
- wslman：镜像站 / GitHub 加速前缀、URL 改写、下载候选与测速排序。

目标：把 envdev 整体迁入 `apps/envdev`，同时把「镜像站 / 加速前缀 / 候选排序 /
配置模板」抽成 `infras` 公用件，业务数据仍留在各应用。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | 新建 `apps/envdev/`，公开模块 `envdev.py` / `mirrortool.py` / `mirrortoolw.py` 分别保留原命令名；私有实现用 `_` 前缀 | 整包 `__tool_name__` 单工具（会失去 mirrortool / mirrortoolw 独立一级入口） | `src/fcmd/apps/envdev/` |
| D2 | CLI / GUI 行为冻结：命令名、参数、输出、`ui/mirrortoolw.json` 路径不变 | 借迁移重命名命令（外部脚本与测试面过大） | 同上 |
| D3 | 新增 `fcmd/infras/net/mirrors.py`：`Mirror` / `Proxy` / `Candidate`、`build_candidates`、`rank_candidates`、`MirrorSpec` / `apply_mirror_config` | 把 envdev 与 wslman 两份镜像逻辑互相 import（apps 之间反向依赖） | `infras/net/mirrors.py` |
| D4 | 数据留在应用：WSL 镜像清单 / GitHub 加速前缀在 `apps/wslman/_mirrors.py`；envdev 服务候选表、MirrorZ 抓取与 apply 映射在 `apps/envdev/_mirror_services.py` | 公共件内置全部站点 / 服务表（infra 认识业务数据，难以独立测试） | 各应用 |
| D5 | 不保留 `tools/dev/envdev*` / `mirrortool*` shim；直接更新测试与 manifest | 兼容 shim（发现阶段会多扫一遍、长期留两套路径） | 删除旧文件 |
| D6 | 覆盖模块映射：`envdev_core.py` → `_core.py`，`_envdev_langs.py` → `_langs.py`，`_envdev_linux.py` → `_linux.py`，`_envdev_mirrors.py` → `_mirror_services.py` | 保留旧名（`_envdev_` 前缀已无域含义） | 同上 |

## 最终目录

```text
src/fcmd/apps/envdev/
  __init__.py
  envdev.py
  mirrortool.py
  mirrortoolw.py
  _mirrortoolw_qt.py
  _core.py
  _langs.py
  _linux.py
  _mirror_services.py
  envdev_go.py
  envdev_java.py
  envdev_node.py
```

`infras/net/mirrors.py` 只依赖标准库与 `infras.system`；wslman / envdev 通过参数
传入各自的镜像清单与服务候选表。

## 验证

- `tests/tools/dev/test_cli_envdev.py` 更新到 `fcmd.apps.envdev.*` 后全量通过；
- `tests/apps/wslman/test_wslman_gaps_lowlevel.py` / `test_wslman_gaps_source.py`
  验证 wslman 候选生成与测速排序委托共享件后行为不变；
- 新增 `tests/infras/test_net_mirrors.py`：URL 改写、官方源兜底、可校验策略、
  测速 / 延迟排序、配置写入；
- `make check-fast`、`make test` 全绿；重新生成 `cli/_manifest_data.py`，确认
  `envdev` / `mirrortool` / `mirrortoolw` 注册路径指向 `fcmd.apps.envdev.*`。

## 后果

- envdev 与其他 apps 一致，成为可独立演进的桌面应用；CLI 命令保持不变；
- 镜像机制只有一份实现，wslman 不再需要 `_mirrors → _wslui` 的反向探测依赖
  （排序探测函数仍由 app 注入）；
- 旧 `fcmd.tools.dev.envdev*` 导入路径不再存在，仓库内测试与文档已同步；
- 后续 LLM 的 GitHub 加速前缀可继续复用 `Proxy` / `build_candidates`，无需再复制。
