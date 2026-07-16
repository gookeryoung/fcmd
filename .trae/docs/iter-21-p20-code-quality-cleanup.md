# P20: 代码质量整合 —— 删除预留扩展点 + 修正过时文档

## 需求清单

- [x] P20a: 删除 `executors.py` 中未实现的 `run_iter` 存根（rule-01 禁止预留扩展点）
- [x] P20b: 修正 `dag.py` 的 `from_yaml` docstring（实际支持 matrix/if，文档过时）

## 迭代目标

响应 `req-01-功能需求.md` 第 2 项"继续完善整合代码，确保代码质量"，聚焦在两项明确的可执行改进：

1. 删除违反 rule-01 "不为未来预留扩展点" 的 `run_iter` 存根函数及其专属 `Iterator` 导入
2. 修正 `dag.py` 的 `from_yaml` docstring 与实际行为不一致问题（实际已支持 matrix/if，文档却写"不支持"）

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fcmd/executors.py` | 删除 `run_iter` 函数（13 行）+ 删除未使用的 `Iterator` 导入 |
| `src/fcmd/dag.py` | 修正 `from_yaml` docstring，反映 matrix/if 实际支持 |

## 关键决策与依据

### 1. 删除 `run_iter` 存根

**问题**：`executors.py` 末尾存在 `run_iter` 函数存根，注释 "P0 不实现"，抛 `NotImplementedError("run_iter 将在 P1 阶段实现。")`。

**违反规则**：
- rule-01「核心原则」："不写未被要求的功能、不为未来预留扩展点"
- rule-11「不留死分支」：`# pragma: no cover` 应激活或删除

**删除安全性**：
- `run_iter` 未在 `__init__.py` 的 `__all__` 中导出
- 没有任何测试引用 `run_iter`
- 仅 `.trae/ref/pyflowx/` 参考代码中存在（不属于本项目）
- `Iterator` 类型仅 `run_iter` 使用，可同步删除

**决策**：直接删除。如未来确有流式执行需求，按 rule-01 流程重新设计实现。

### 2. 修正 `from_yaml` docstring

**问题**：`dag.py` 第 230-252 行 `from_yaml` docstring 写"不支持 ``strategy.matrix`` 矩阵扇出与 ``if`` 条件"，但实际 `yaml_loader.py` 已通过 `expand_matrix` + `parse_if` 实现这两项功能（见模块 docstring 与测试）。

**依据**：rule-11「公共 API 必须有中文 docstring」隐含文档应准确反映行为；rule-01「闭环执行」要求文档同步实际实现。

**修正方式**：将"不支持"改为"支持 ``if`` 条件判断与 ``matrix`` 矩阵扇出（由 :mod:`fcmd.conditions` 与 :mod:`fcmd.yaml_loader` 提供）"，并引导至 `yaml_loader` 模块查看 schema 细节。

## 代码实现情况

### 删除 run_iter

```python
# 删除前（executors.py 末尾）：
# 流式执行迭代器（保留接口，P0 不实现）
def run_iter(  # noqa: PLR0913
    graph: Graph,
    strategy: Strategy = "dependency",
    *,
    max_workers: int | None = None,
    verbose: bool = False,
    on_event: EventCallback | None = None,
    only: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> Iterator[tuple[str, TaskResult[Any]]]:
    """流式执行图（P1 阶段实现）。"""
    raise NotImplementedError("run_iter 将在 P1 阶段实现。")

# 删除后：executors.py 以 _async_drive 函数结尾
```

### 同步删除 Iterator 导入

```python
# 删除前：
from collections.abc import Iterable, Iterator, Mapping
# 删除后：
from collections.abc import Iterable, Mapping
```

### 修正 from_yaml docstring

```python
# 修正前：
"""...不支持 ``strategy.matrix`` 矩阵扇出与 ``if`` 条件。..."""

# 修正后：
"""...以及 ``if`` 条件判断与 ``matrix`` 矩阵扇出
（由 :mod:`fcmd.conditions` 与 :mod:`fcmd.yaml_loader` 提供）。
schema 细节见 :mod:`fcmd.yaml_loader`。..."""
```

## 整合优化情况

- 无新重复代码
- 删除死代码 13 行 + 未使用导入 1 处
- 文档与实际行为一致

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 65 files already formatted |
| `uv run pyrefly check` | 0 errors |
| `uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95` | 920 passed, 2 deselected |
| 总覆盖率 | 99.07%（与上轮持平） |
| executors.py 覆盖率 | 99%（无回归） |

## 遗留事项

- `req-01-功能需求.md` 第 1 项（imgtool/pdftool 移植 + 可选依赖）待用户授权后推进
- 其他核心模块（task.py / conditions.py / context.py / dag.py / errors.py / report.py / console.py）经审查质量已高，docstring 完整、类型注解完整、无明确可改进点
- `executors.py` / `task.py` / `toolkit.py` 中 `except Exception` 共 6 处均为任务/条件执行边界的合理捕获（用户代码可能抛任意异常），非业务逻辑过度捕获，不修改

## 下一轮计划

待用户指示。可选方向：
- 授权引入 Pillow/PyMuPDF 等可选依赖并移植 imgtool/pdftool
- 其他质量改进点或新功能需求
