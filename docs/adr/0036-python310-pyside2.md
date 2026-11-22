# 0036 Python 3.10 支持与 PySide2 兼容

- 状态：已落地
- 日期：2026-11-18
- 关联：0013（GUI 技术路线）、0033 / 0034（Qt 迁移）

## 背景

项目原先要求 Python ≥3.13，Qt 绑定固定为 PySide6。需求改为最低支持 Python 3.10，
并明确 **3.10 使用 PySide2**（PySide2 5.15.2.1 仍提供 cp310 wheel）。这同时影响：

- `pyproject.toml` 版本下限、依赖标记与分类器；
- 3.11/3.12 才有的标准库 API；
- PySide6 与 PySide2 的 Qt5 / Qt6 API 差异；
- CI、tox、release 文档与类型检查目标版本。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | `requires-python = ">=3.10"`，分类器覆盖 3.10–3.14 | `pyproject.toml` |
| D2 | 按解释器选择绑定：3.11+ 安装 `PySide6-Essentials>=6.9`，3.10 安装 `PySide2>=5.15.2.1` | `pyproject.toml` 的 `python_version` 标记 |
| D3 | 业务代码继续导入 `PySide6`；3.10 上由轻量 meta path finder 映射到 PySide2，不预加载 Qt | `fcmd.gui.compat.install()`，在 `fcmd/__init__.py` 注册 |
| D4 | Qt5/Qt6 差异在兼容层补齐：`QAction` 模块位置、`exec` / `QAction.menu()` 生命周期、`QTimer.singleShot` 调用形态、`QFontDatabase.families`、`QWheelEvent.position` | `compat` + 少量调用点改写成两版通用写法 |
| D5 | 3.11/3.12 标准库 API 用项目内垫片或条件导入：`tomllib`→`tomli`、`StrEnum`→`str+Enum`、`datetime.UTC`→`timezone.utc`、`itertools.batched`、`typing.override`、`Path.is_junction` | `fcmd._compat`、`fcmd.infras._toml` |
| D6 | 门禁目标版本同步到 3.10：ruff / pyrefly / tox | `ruff.toml`、`pyrefly.toml`、`tox.ini` |
| D7 | CI 测试矩阵为 3.10 / 3.13 / 3.14；3.10 job 实际安装 PySide2 | `.github/workflows/ci.yml` |

## 兼容层边界

- 兼容层只在 Python <3.11 安装 finder，其它版本完全零开销；CLI 冷启动不导入 Qt。
- 业务模块继续写 PySide6 风格 scoped enum（`Qt.AlignmentFlag.AlignLeft` 等），
  PySide2 5.15 已提供同类枚举命名空间，无需改写全部调用点。
- PySide2 的 `QMenu` / `QAction` Python 包装存在回收时序差异，兼容层保留菜单与
  action 强引用，避免「C++ object already deleted」。
- 打包仍使用 Python 3.13 + PySide6；3.10 + PySide2 是运行支持路径，不改变便携包构建版本。

## 验证

- Python 3.10.20 + PySide2 5.15.2.1：全量非 slow 测试 8329 passed / 9 skipped（离屏 Qt）。
- Python 3.14（当前开发环境）：全量非 slow 测试 8329 passed / 9 skipped。
- `make check-fast`：ruff（target py310）与 pyrefly（python-version 3.10）通过。

## 风险与退出条件

- PySide2 已停止维护，Qt5 与 Qt6 的长期行为会继续分化；应锁定 `5.15.2.1` 并保持
  Qt 兼容层集中，不在业务代码里写版本分支。
- Python 3.10 于 2026-10 结束维护；到期后删除 PySide2 依赖与 `compat` 映射，
  `requires-python` 回到 ≥3.11，并同步移除 3.10 CI / tox 环境。
