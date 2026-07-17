# P27: 修复 tox 跨 Python 版本兼容性

## 需求清单

- [x] P27a: 修复 `_unwrap_optional` 不识别 `types.UnionType`（py311+ 失败）
- [x] P27b: 修复 `_unwrap_optional` 在 py38 下字符串注解被误判为 Union（4 个回归）
- [x] P27c: 修复 `test_install_docker_linux` 在 tox 环境下 `getpass.getuser()` 失败
- [x] P27d: 修复 `test_unwrap_optional_typing_union` 在 py314 下 Union 缓存比较失败
- [x] P27e: tox py38-py314 全量验证通过

## 迭代目标

推进 P26 遗留事项：解决 tox 在多 Python 版本（py38-py314）下的测试失败问题，使全部门禁在 7 个版本上一致通过。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fcmd/apis/toolkit.py` | `_unwrap_optional` 新增 `import types` 与 `types.UnionType`（PEP 604）识别；用 `union_type is not None` 守卫避免 py38 下 `None is None` 误判 |
| `tests/test_cli_tools_p17.py` | `test_install_docker_linux` mock `getpass.getuser` 避免 tox 环境缺 `USERNAME` |
| `tests/test_toolkit.py` | `test_unwrap_optional_typing_union` 比较改 `is`→`==` 适配 py314 Union 缓存；新增 `test_unwrap_optional_pep604_union_type`（skipif < 3.10） |

## 关键决策与依据

1. **`types.UnionType` 守卫写法**：Python 3.8/3.9 没有 `types.UnionType`，`getattr(types, "UnionType", None)` 返回 `None`。若直接写 `origin is getattr(types, "UnionType", None)`，当 `origin` 为 `None`（字符串/普通类型的 `typing.get_origin` 返回值）时会退化为 `None is None` → True，导致字符串注解被误判为 Union 并原样返回，破坏 P22 已实现的字符串解析路径。正确写法是先取 `union_type = getattr(...)`，再用 `union_type is not None and origin is union_type` 短路守卫。

2. **mock `getpass.getuser` 而非扩展 `passenv`**：tox `passenv` 默认不透传 `USERNAME`/`LOGNAME`/`USER`，导致 `getpass.getuser()` 在 tox 环境抛 `OSError`。选择在测试中 mock 而非扩展 `passenv`，因为测试本身不验证 `getuser` 的真实返回值，只验证 `usermod` 命令构造；mock 更确定、不依赖宿主环境。

3. **Union 比较 `is` → `==`**：Python 3.10+ 对 `typing.Union` 的缓存策略变化，`Union[int, str, None] is Union[int, str, None]` 不保证成立；`==` 比较值语义更稳妥。

## 代码实现情况

### `_unwrap_optional` 核心修复（toolkit.py L427-439）

```python
union_type = getattr(types, "UnionType", None)
if origin is typing.Union or (union_type is not None and origin is union_type):
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if len(args) == 1:
        return args[0]
    return annotation
```

### `test_install_docker_linux` mock（test_cli_tools_p17.py）

```python
monkeypatch.setattr("fcmd.cli.envdev.getpass.getuser", lambda: "testuser")
```

## 测试验证结果

### 本地门禁（py38，开发主机）

- ruff check / format：0 错误，71 文件已格式化
- pyrefly：0 错误（35 suppressed, 10 warnings）
- pytest：1086 passed, 1 skipped, 2 deselected
- coverage：99.28%（≥95% 基线）

### tox 跨版本（py38-py314）

| 环境 | 结果 |
|------|------|
| py38 | 1086 passed, 1 skipped |
| py39 | 1086 passed, 1 skipped |
| py310 | 1087 passed |
| py311 | 1087 passed |
| py312 | 1087 passed |
| py313 | 1087 passed |
| py314 | 1087 passed |

py38/py39 的 1 skipped 是 `test_unwrap_optional_pep604_union_type`（`@pytest.mark.skipif(sys.version_info < (3, 10))`），按预期跳过。

## 整合优化情况

- 无重复代码引入
- 修复未破坏既有 P22 字符串注解解析路径（`test_unwrap_optional_str_pep604` / `test_unwrap_optional_str_optional_form` / `test_unwrap_optional_non_optional_passthrough` 全绿）

## 遗留事项

无。P26 遗留的 tox 失败已全部解决。

## 下一轮计划

无强制待办。可选方向：
- 检查 `.trae/req/` 是否有未完成需求
- 评估是否需要为 tox 增加 `USERNAME`/`LOGNAME` 到 `passenv`（当前用 mock 规避，若未来有真实依赖 `getuser` 的测试再考虑）
