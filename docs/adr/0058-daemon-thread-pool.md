# 0058 进程退出不再等待后台任务：守护线程池

- 状态：已落地
- 日期：2026-11-20
- 关联：[0033](0033-qt-gui-migration.md)（Qt 后台任务 `workers.submit`）、
  [0001](0001-layering.md)（分层，本方案的池放在 `shared`）、[0046](0046-large-qt-app-split.md)（窗口拆分）

## 背景

用户报告：**`wslmanw` 关闭窗口后长时间无法结束进程**（任务管理器里残留 python / exe，
数十秒到几分钟才消失）。定位后确认这不是窗口销毁慢（关窗本身 2–6 ms），而是
「事件循环退出到进程真正结束」这一段被后台线程 join 住：

| 来源 | 线程属性 | 退出现象 |
|---|---|---|
| `gui.workers.submit` → `QThreadPool` | 非守护 | `QCoreApplication` 析构时 `waitForDone()`，再者 Qt 线程池线程要 join |
| `gui.workers.submit_daemon` | **守护** | 立即退出（原本就是对的） |
| `_images.collect_images` → `ThreadPoolExecutor(2)` | 非守护 | 镜像清单 / `wsl --list --online` 卡住时，进程等网络超时 |
| `infras/net`（`rank_candidates` / `probe_mirrors` / `check_urls`） | 非守护 | 测速 / 探测读超时最坏 30 s+ |
| `_wslui.rank_items` | 非守护 | 同上（吞吐探测 2.5 s × 轮次） |
| `infras/dupes` 并行哈希、`cmder/_queue` 并行拷贝、`filescan/_engine` 线程扫描 | 非守护 | 大目录 / 大文件拷到一半关窗即卡住 |

`ThreadPoolExecutor` 的 worker 由 `threading` 注册进 `_threads_queues`，`threading._shutdown`
（`atexit` 阶段）会 join 它们；守护线程池则不参与 join。复现脚本（离屏 Qt，任务里
`time.sleep(20)`）对比：

| 场景 | 事件循环退出 | `atexit` | 进程总耗时 |
|---|---:|---:|---:|
| 提交前（稳定版本） | 0.45 s | 20.15 s | **20.3 s** |
| 本方案 | 0.45 s | 0.45 s | **0.5 s** |

同一现象在 `llmw`、`filescanw`（关窗时 `waitForDone(3000)` 直接卡 3 s）、`cmderw`
（队列拷贝）上都存在，只是 wslmanw 的镜像页最容易踩到。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | 新增 `shared/pool.py`：`DaemonPool`（守护线程 + `SimpleQueue`）提供与 `ThreadPoolExecutor` 等价的 `submit` / `map` / `shutdown`，返回标准 `concurrent.futures.Future` | 继续用 `ThreadPoolExecutor` + `shutdown(wait=False)`（worker 仍被 `threading._shutdown` join）；裸 `threading.Thread` 每任务一线程（无法限并发、无返回值语义、调用点要重写） | `shared/pool.py` |
| D2 | 池放在 `shared` 层，不进 `infras` | 放 `infras`（`gui` 不能 import `infras`，会破 ADR 0001 的分层守卫）；放 `gui`（`infras` 网络探测反过来 import GUI，更糟） | `shared/pool.py`、`tests/test_architecture.py` |
| D3 | `gui.workers.submit` 改走进程内共享守护池 `default_pool()`；`_Task` 不再是 `QRunnable`，改为持有 `future` 的普通对象（`_PENDING` 语义不变） | 保留 `QThreadPool` 只把慢任务改成 `submit_daemon`（漏一个就是「关不掉」，且新代码随时会再踩）；`QThreadPool` 换自建 `QThreadPool`（仍是非守护、仍要 join） | `gui/workers.py` |
| D4 | `submit_daemon` 保留：每任务独占守护线程，不受共享池并发上限约束（下载 / 拉清单这类「一次一个、可能长时间阻塞」的任务） | 全部并到共享池（下载会占满槽位，状态轮询被饿死） | `gui/workers.py` |
| D5 | 全部可能长时间阻塞的 `ThreadPoolExecutor` 调用点改用 `DaemonPool`：镜像清单、镜像 / 加速源测速、URL 探测、并行哈希、拷贝队列、filescan 线程扫描 | 只改 GUI 侧（`filescan` / `cmder` / CLI 的网络与扫描路径同样会卡退出） | 见「落点」表 |
| D6 | `filescanw._on_close` 去掉 `waitForDone(3000)`：只置取消标记，扫描在守护线程里自然收尾 | 保留等待（取消标记在批次边界即可生效，等待只会让关窗固定卡 3 s） | `apps/filescan/_filescanw_qt.py` |

## 口径（C1..Cn → 落点）

| # | 约束 | 落点 |
|---|---|---|
| C1 | 池内 worker 必须是守护线程；进程退出不等未完成任务 | `shared/pool.py`、`tests/infras/test_pool.py::test_workers_are_daemon_threads` / `test_process_exits_without_waiting_for_daemon_pool` |
| C2 | 语义与 `ThreadPoolExecutor` 对齐：`submit` 返回 Future、`map` 保序、异常原样抛出、`shutdown(wait=..., cancel_futures=...)` 签名兼容 | 同上（`test_submit_*` / `test_map_*` / `test_shutdown_rejects_new_tasks`） |
| C3 | 并发上限不变，空闲 worker 复用 | `test_pool_reuses_workers_within_limit` |
| C4 | `submit` / `submit_daemon` 的回调顺序仍是 `done → failed → finished`，回调仍在 GUI 线程 | `tests/gui/test_qt_workers.py` |
| C5 | 不改业务口径：镜像清单合并、测速排序、扫描顺序、拷贝并行度全部不变 | 既有测试（`tests/apps/wslman/test_wslman_images.py`、`tests/infras/test_net_mirrors.py`、`tests/apps/filescan/`、`tests/apps/cmder/`） |

## 边界

- 未完成的任务在进程退出时**不会**写回结果（本来就只用于「中途结果不值得等」的
  路径）；需要落盘的任务（设置、备份、导入导出）仍是同步调用或进程内完成。
- `DaemonPool.shutdown()` 不 join 正在跑的任务，这是与 `ThreadPoolExecutor` 的唯一
  语义差异；调用点若依赖「shutdown 返回即完成」，必须改调用方而不是改池。
- 共享池 `default_pool()` 不注册 `atexit` 关闭：进程退出即结束，注册关闭反而会引入
  一次「投递哨兵 + 等待」的伪同步。
- 测试侧：任务不再由 `QThreadPool` 持有，`tests/apps/llm/conftest.py` 改为轮询
  `workers._PENDING` 并转事件循环排空（守护线程不会自动帮测试「等完」，拖尾污染
  下一个用例的风险要自己兜住）。

## 落地状态

- P0（已落地）：`shared/pool.py` + `gui/workers.submit` 切换 + wslman 镜像 / 测速 /
  WSLUI 探测 + `filescanw` 关窗不再 `waitForDone`。
- P1（已落地）：`infras/dupes`、`cmder/_queue`、`filescan/_engine` 线程扫描。
- 交接：新增后台任务一律 `fcmd.gui.workers.submit` / `submit_daemon`；新增并行工具函数
  用 `fcmd.shared.pool.DaemonPool` / `parallel_map`，不要新引 `ThreadPoolExecutor`
  （`tests/infras/test_pool.py` 用子进程守护「进程能立刻退出」这条口径）。
