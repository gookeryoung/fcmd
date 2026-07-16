# P16：executors.py 覆盖率提升至 99%

## 需求清单

- [x] executors.py 覆盖率从 93% 提升至 ≥99%
- [x] 识别并处理防御性/不可达代码
- [x] 全套门禁通过（ruff/pyrefly/pytest --cov）

## 迭代目标

提升 `src/fcmd/executors.py` 分支覆盖率，识别防御性代码并通过 `# pragma: no cover` 标注或补测处理未覆盖分支。

## 改动文件清单

- `src/fcmd/executors.py`：4 处防御性代码添加 `# pragma: no cover` 标注
- `tests/test_executors.py`：新增 10 个测试用例

## 关键决策与依据

### 防御性代码识别与处理

通过分析源码调用链与不变量保证，识别以下 6 处不可达/防御性代码，添加 `# pragma: no cover`：

| 行号 | 代码 | 不可达原因 |
|------|------|-----------|
| 205 | `if dep not in report.results: continue` | `_validate_references` 保证硬依赖在图中，`_store_result` 保证所有任务结果（含 SKIPPED/FAILED）已存储 |
| 541 | `if not to_run: return`（ThreadedLayerRunner） | `Graph.layers()` 拓扑排序不产生空层 |
| 573 | `if not to_run: return`（AsyncLayerRunner） | 同上 |
| 609 | `if d not in dependents: dependents[d] = []` | `dependents` 已用 `all_specs` 全部名称预初始化（line 600） |
| 668 | `if not in_flight:`（已有 pragma） | 图已校验无环，前一轮 `ready` 非空时 `in_flight` 必非空 |

### 剩余 4 处不可测分支（保留为 partial）

以下分支为线程竞态或结构不变量保证的不可达分支，无法通过测试覆盖且 `# pragma: no cover` 会丢失可达分支覆盖，故保留为 partial：

| 分支 | 说明 |
|------|------|
| 74->76 | 双检锁内层 `if _thread_pool is None`，线程竞态 |
| 182->181 | `if dep in global_context` False 分支，`_store_result` 保证依赖结果已注入 |
| 675->exit | `next(n for n, t in in_flight.items() if t is task)` 迭代器耗尽，task 必在 `in_flight` 中 |
| 682->681 | `if not t.done()` False 分支，`asyncio.wait` 返回后其他任务可能已完成（竞态） |

### 新增测试（10 个）

| 测试 | 覆盖目标 |
|------|---------|
| `test_run_retry_with_wait` | 同步重试 `time.sleep(wait)`（line 400） |
| `test_run_async_retry_with_wait` | 异步重试 `asyncio.sleep(wait)`（lines 434-435） |
| `test_run_async_retry_no_wait` | 异步重试 delay=0 跳过 sleep（434->425 分支） |
| `test_run_async_conditions_skip` | async 策略条件跳过（line 415） |
| `test_run_async_sync_fn_with_env` | async 策略同步 fn 带 env（lines 467-468） |
| `test_run_dependency_fail_cancels_others` | dependency 策略失败取消其他任务（lines 679-682） |
| `test_run_verbose_with_on_event` | verbose + on_event 回调（line 707） |
| `test_run_subgraph_filter_no_match` | tags 不匹配返回空 report（line 731） |
| `test_run_multi_dep_context_injection` | 多依赖 `_build_context` 循环 |
| `test_verbose_callback_pending_event` | PENDING 事件不匹配任何 elif 分支（705->708） |

### pragma 位置调整

将 `if not in_flight:` 的 pragma 从内层 `if remaining:` 移至外层，覆盖整个防御性代码块（含不可达 `break`）。

## 代码实现情况

- `executors.py`：4 处添加 `# pragma: no cover` + 注释说明不可达原因
- `test_executors.py`：+10 测试用例，修复 `test_run_subgraph_filter_no_match`（`only=["nonexistent"]` KeyError → `tags=["nonexistent_tag"]`），修复 `tags=["alpha"]` 类型错误（→ `tags=("alpha",)`）

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| ruff check | All checks passed |
| ruff format --check | 59 files already formatted |
| pyrefly check | 0 errors |
| pytest | 861 passed（+10） |
| executors.py 覆盖率 | 93% → 99%（0 miss, 4 BrPart） |
| 总覆盖率 | 98.18% → 98.96%（+0.78%） |

## 遗留事项

- 4 处不可测分支（线程竞态/结构不变量）保留为 partial，无法进一步覆盖
- `.coveragerc` 不支持 `pragma: no branch`，无法单独排除分支

## 下一轮计划

- 评估其他低覆盖模块（`command.py` 94%、`console.py` 92%、`dag.py` 96%）是否值得补测
- 或继续功能开发
