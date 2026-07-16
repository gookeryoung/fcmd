# P19: 测试状态隔离修复 + command.py 覆盖率补全

## 需求清单

- [x] P19a: 修复 `test_toolkit.py` 的 `_clean_registry` autouse fixture 清空整个注册表导致其他测试文件模块级 import 注册的工具（如 pymake）丢失
- [x] P19b: 补全 `command.py` 第 43 / 84-87 行未覆盖分支，覆盖率从 90% 提升到 100%

## 迭代目标

1. 修复测试状态泄漏，使 `pytest tests/` 整体运行时全部通过（先前 57 failed）
2. 通过 `monkeypatch.setattr` mock `subprocess.run` 覆盖 TimeoutExpired / OSError 等罕见分支，避免 `@pytest.mark.slow` 真实超时
3. 补全 callable + verbose + cwd 分支，使 command.py 行覆盖率达 100%
4. 总覆盖率从 98.86% 提升至 99.07%

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `tests/test_toolkit.py` | `_clean_registry` 改为 save/restore 模式：快照 + 清空 + 恢复 |
| `tests/test_command.py` | 新增 3 个测试：`test_run_command_callable_verbose_with_cwd` / `test_run_command_timeout_mock` / `test_run_command_os_error_generic` |

## 关键决策与依据

### 1. save/restore 模式替代简单 clear

**问题**：原 fixture 直接 `clear_tool_registry()` + yield + `clear_tool_registry()`，会清空其他测试文件通过模块级 `import fcmd.cli.pymake` 注册的工具，导致 `test_pymake_tool.py` 中 `_TOOL_REGISTRY` 查不到 pymake 而 57 failed。

**方案**：进入测试前 `copy.deepcopy(_TOOL_REGISTRY)` 保存快照，清空让单测从干净状态开始；测试结束后先 clear 再 `update(saved)` 恢复快照，保留其他文件模块级 import 注册的工具。

**依据**：autouse fixture 仅影响定义文件内的测试，但模块级 import 是全局一次性的。snapshot/restore 模式可保证测试隔离同时不污染跨文件共享状态。

### 2. mock subprocess.run 覆盖罕见错误分支

**问题**：`command.py` 第 84-87 行（TimeoutExpired / OSError 分支）原先靠 `@pytest.mark.slow` 真实超时测试，被 `pytest -m "not slow"` 跳过导致覆盖率不足。

**方案**：用 `monkeypatch.setattr("fcmd.command.subprocess.run", fake_run)` 直接 raise `subprocess.TimeoutExpired` 或 `OSError`，无需真实超时即可触发分支。

**依据**：rule-11 推荐 Mock 优先级 `monkeypatch > 内联 stub > unittest.mock`；rule-01 禁止放宽断言或绕过覆盖率检查。mock 方案既符合规则又能让默认测试套件覆盖该分支。

### 3. callable + verbose + cwd 分支

**问题**：第 43 行 `console.print(f"  [dim]工作目录: {cwd}[/dim]")` 仅在 callable + verbose + cwd 三条件同时成立时执行，原测试未覆盖。

**方案**：新增 `test_run_command_callable_verbose_with_cwd`，构造 `cmd=lambda: 1, verbose=True, cwd=tmp_path` 触发该分支。

## 代码实现情况

### `_clean_registry` save/restore

```python
@pytest.fixture(autouse=True)
def _clean_registry():
    saved = copy.deepcopy(_TOOL_REGISTRY)
    clear_tool_registry()
    yield
    clear_tool_registry()
    _TOOL_REGISTRY.update(saved)
```

### mock 测试

```python
def test_run_command_timeout_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    def fake_run(*_args: object, **_kwargs: object) -> None:
        raise sp.TimeoutExpired(cmd="test", timeout=0.5)

    monkeypatch.setattr("fcmd.command.subprocess.run", fake_run)
    spec = TaskSpec(name="x", cmd=["echo", "hi"], timeout=0.5)
    with pytest.raises(RuntimeError, match="超时"):
        spec.effective_fn()
```

## 整合优化情况

- 无新重复代码
- 测试新增 3 个，全部为公共接口测试，符合 rule-11 "公共 API 优先通过公共接口测试"
- mock 用 monkeypatch，符合 Mock 优先级

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 65 files already formatted |
| `uv run pyrefly check` | 0 errors |
| `uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95` | 920 passed, 2 deselected |
| command.py 覆盖率 | 90% → 100% |
| 总覆盖率 | 98.86% → 99.07% |
| test_toolkit.py + test_pymake_tool.py 联跑 | 188 passed（先前 57 failed） |

## 遗留事项

无。

## 下一轮计划

待用户确认下一方向。可选方向：
- 探索 fcmd CLI 文档/README 是否需更新（用户使用手册）
- 检查是否有未完成的 req 项
