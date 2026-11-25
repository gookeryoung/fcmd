# 0057 llmw / wslmanw 冷启动：后台探测 + 导入瘦身 + 渲染延后

- 状态：已落地
- 日期：2026-11-15
- 需求卡片：[0057](../requirements/0057-llmw-wslmanw-startup-fast.md)
- 关联：[ADR 0032](0032-llmw-startup-latency.md)（0046 页面拆分、0031 配置单一来源的后续）

## 背景

两个 Qt 窗口的冷启动都超出「按下命令到能操作」的心理阈值：`llmw` 约 5.7 s、
`wslmanw` 约 1.1 s。分阶段计时（本机 Windows + offscreen + `.venv`，Python 3.13）
与构造期 `cProfile`（`print_stats('fcmd')`）归因：

| 阶段 | llmw | wslmanw |
|---|---|---|
| 门面导入 | 388 ms | 583 ms |
| `create_application`（+ 主题） | 86–147 ms（+24–27 ms） | 约 170 ms（含主题） |
| 窗口模块导入 | 482 ms（PySide6 已载入时 320 ms） | 165 ms |
| 窗口构造 | **4761 ms** | **175 ms** |

llmw 构造期热点：`_llmvllm.detect_platform()` **3.35 s**（`RunPage.__init__` 在 GUI
线程同步调用；`_probe_wsl` 对每个运行中发行版串行跑 4 次 `wsl.exe ... sh -lc`）、
`build_plan_from` ×2 **0.80 s**（默认路径先构造 base 再构造带 custom 的 plan）、
`scan_models` ×4 **0.65 s**（`find_model` 逐目录重扫，无缓存）、
`refresh_services`（同步 psutil）0.2 s、图标 / paint 约 0.15 s。

wslmanw 热点：门面里 `_install` 62 ms（→ `archivex` 53 ms → `fcmd.core.toolkit` + rich）、
`_wslui` 46 ms（→ `urllib.request` / `ssl` / `email`）、`_mirrors` 26 ms；构造里
`_restore_images_cache` 约 100 ms（26 行镜像 × 109 次 `set_icon` SVG 渲染、
`local_arch()` 26 次 `platform.machine()`）。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | `RunPage` 不再在构造期同步探测平台：先用占位平台（`supported=False`，默认引擎回落 llama.cpp）建页，`submit` 后台探测后由 `_apply_platform` 回填 vLLM 选项 / 引擎 / 模型列表 / 预览；失败只写提示不弹窗。`_probe_wsl` 的 4 次 `sh -lc` 合并为 1 次带前缀标记的输出，解析抽成纯函数 `_parse_probe`；平台回填方法与引擎族选项拆到 `qt/run_platform.py`（`_llmw_qt_run.py` 回到 800 行守卫内） | `_llmw_qt_run.py`、`_llmvllm_platform.py`、`qt/run_platform.py` |
| D2 | `scan_models` 加进程内缓存（键 = 各根目录绝对路径 + `st_mtime_ns`，TTL 5 s 兜底，返回副本，`clear_scan_cache()` 手动失效）；`find_model` 改为一次扫描全部根。`build_plan` 在 `custom` 为空时单次 `build_plan_from` 返回；`strip_form_owned_args` 随 `build_plan` 一起落在 `qt/run_params.py`；预览刷新用 `QTimer.singleShot(0, …)` 合并同一轮重复请求 | `llmmodel.py`、`qt/run_params.py`、`_llmw_qt_run.py` |
| D3 | 「可安装后缀表」下沉到轻量 `_images.py`，`_install` 反向导入并保持 `__all__` re-export；`wslmanw` / `_wslmanw_qt` 顶层不再导入 `_install`、`_wslui`，改到安装 / 下载动作的函数内导入（`_REEXPORTS` 同步删除两项） | `_images.py`、`_install.py`、`wslmanw.py`、`_wslmanw_qt.py`、`qt/images_manage.py`、`qt/images_download.py` |
| D4 | 镜像缓存回显只写 `image_entries` 与状态提示，行卡渲染与实例页标注推到首帧之后（`QTimer.singleShot(0, _fill_cached_images)`，`_render_images` 清空重建、幂等）；`local_arch()` 加 `lru_cache(maxsize=1)`；`gui/icons.pixmap()` 按 `(name, size, color, ratio)` 缓存 `QPixmap`（上限 256、FIFO 驱逐，只在 GUI 线程创建） | `qt/images_download.py`、`wslmanw.py`、`gui/icons.py` |
| D5 | 服务状态刷新后台化：`LlmWindow` 构造与切页用 `submit(state.refresh_services)` / `ChatPage.refresh_services_async()`，同步入口 `refresh_services()` 保留给按钮与服务信号（槽保持无参，避免 `clicked(bool)` 把布尔当状态列表） | `_llmw_qt_window.py`、`_llmw_qt_chat.py` |
| D6 | 不做的项：平台探测磁盘缓存（后台化已消除阻塞，缓存只会引入过期口径）；`_mirrors` 延迟导入（设置读取与镜像页构造都在首屏路径，收益趋零）；`fcmd.console`（rich）延迟导入（0057 目标已达成，留 0032 记录的遗留项） | — |

## 口径（C1..Cn → 落点）

| # | 约束 | 落点 |
|---|---|---|
| C1 | 分阶段基线与复测对账 | 本文「背景」与「验收」两节 |
| C2 | llmw ≤ 2.0 s | D1 / D2 / D5 |
| C3 | wslmanw ≤ 0.85 s | D3 / D4 |
| C4 | 不改功能口径 | 见「边界」 |

## 验收

复测口径与基线一致（offscreen、`os._exit(0)` 收尾、3 次取样）：

| 指标 | 基线 | 复测 | 目标 |
|---|---|---|---|
| llmw 冷启动合计 | 5.7 s | **0.57–0.60 s** | ≤ 2.0 s |
| llmw 构造 | 4.76 s | **0.16 s** | ≤ 1.2 s |
| wslmanw 冷启动合计 | 1.1 s | **0.48 s** | ≤ 0.85 s |
| wslmanw 构造 | 175 ms | **90–99 ms** | ≤ 0.1 s |

- `detect_platform(use_cache=False)` 本机两次 3571 ms / 285 ms（首次含 WSL 服务自启；
  发行版已停止时不探子进程），已不占首屏。
- 测试：`_parse_probe` 多行解析与「合并后只调一次 runner」、`scan_models` 缓存
  （复用 / mtime 失效 / `clear_scan_cache`）、占位平台下的无阻塞构造与 `_apply_platform`
  回填、`local_arch` 缓存、延后渲染时序（offscreen + `QTest.qWait`）、
  `_wslmanw_qt` 导入链不含 `_install` / `_wslui` / `archivex`（子进程断言）。

## 边界

- 后台探测期间 vLLM 选项短暂禁用、默认 llama.cpp，检测完成后按持久化选择恢复；
  仍然只探运行中实例（`allow_start=False`），不隐式启动已停止发行版。
- 扫描缓存以目录 mtime 为准，TTL 5 s + `clear_scan_cache()` 覆盖边缘；`find_model`
  的 `ServeError` 文案与「exact 优先、取最大」的优先级不变。
- 镜像渲染只把约 100 ms 移到首帧之后；`_wslui_catalog` 到货重绘、筛选 / 排序、
  下载状态刷新路径不变，空列表期间状态行提示「正在载入本地缓存…」。
- `QPixmap` 缓存键含设备像素比，主题切换天然换键；`QIcon` 持有已显示位图的引用，
  缓存驱逐不影响界面上已渲染的图标。

## 交接

- 新增「首屏不该做的 IO」优先按 D1 / D5 的模式后台化，而不是加磁盘缓存。
- 新应用入口若要复用镜像后缀表，直接 `from fcmd.apps.wslman._images import INSTALL_SUFFIXES`，
  不要再从 `_install` 取（会把 `archivex` 拉回首屏）。
- 图标缓存与 `local_arch` 都是进程级缓存：测试里 monkeypatch 依赖项后需 `cache_clear()`
  （`icons` 侧改主题或换分辨率即换键，无需手动清）。
