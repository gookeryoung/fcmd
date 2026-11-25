# 需求卡片：llmw / wslmanw 冷启动提速

- 编号：0057（卡片存 `docs/requirements/0057-llmw-wslmanw-startup-fast.md`；与 ADR 同号）
- 来源 / 提出人：用户（会话内提出）
- 日期：2026-11-15
- 关联 ADR：[0057](../adr/0057-llmw-wslmanw-startup-fast.md)、[0032](../adr/0032-llmw-startup-latency.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> llmw、wslmanw启动感觉偏慢，请分析优化方案

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 「启动感觉偏慢」 | 给出可复现的分阶段基线（门面导入 / `QApplication` / 主题 / 窗口模块导入 / 窗口构造），并复测对账 | `_llmw_qt_window.py`、`_wslmanw_qt.py` | 分阶段脚本 + `cProfile` 归因 |
| C2 | 「llmw」 | llmw 冷启动 ≤ 2.0 s：平台探测移出 GUI 线程、模型扫描加缓存、预览单次构造、服务刷新后台化 | `_llmw_qt_run.py`、`_llmvllm_platform.py`、`llmmodel.py`、`qt/run_params.py`、`_llmw_qt_window.py`、`_llmw_qt_chat.py` | `tests/apps/llm/test_llmvllm.py`、`test_llmw_qt_smoke.py`、`test_cli_llm_tools.py` |
| C3 | 「wslmanw」 | wslmanw 冷启动 ≤ 0.85 s：入口导入瘦身（`_install` / `_wslui` 延迟）、镜像行卡渲染推到首帧之后、`local_arch` / 图标位图缓存 | `wslmanw.py`、`_wslmanw_qt.py`、`qt/images_*.py`、`gui/icons.py` | `tests/apps/wslman/test_wslmanw_startup.py`、`test_wslmanw_qt.py` |
| C4 | 「分析优化方案」 | 不改功能口径：WSL 探测不隐式启动已停止实例、镜像页「陈旧回显 + 后台刷新」、下载源排序与校验、服务看护周期均保持 | 同上 | 既有 llm / wslman 全量用例 |

## 现状影响面

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | WSL / vLLM 平台探测 | `_llmvllm_platform.py`、`RunPage.__init__` | GUI 线程同步 4 次 `wsl.exe` 子进程 |
| 2 | 模型目录扫描 | `llmmodel.scan_models`、`RunParamsMixin.build_plan` | 每个模型目录重复扫盘、预览重建两次 `make_plan` |
| 3 | 服务状态刷新 | `LlmState.refresh_services`、`ChatPage.refresh_services` | psutil 进程检查占 GUI 线程 |
| 4 | wslmanw 入口导入 | `wslmanw.py`、`_wslmanw_qt.py` | `_install`（→ `archivex`）、`_wslui`（→ `urllib`/`ssl`） |
| 5 | 镜像页首屏 | `qt/images_download.py`、`gui/icons.py` | 26 行镜像 × SVG 图标位图 |

## 未决问题

- [x] 平台探测是否做磁盘缓存：不做——后台化已消除阻塞，缓存会引入过期口径。
- [x] 镜像记录「已下载」是否需要实时后缀表：需要，后缀表下沉到轻量 `_images.py` 保持口径不变。

## 卡点

- [x] 每条需求句都有 C 编号约束（无遗漏、无「待定」）
- [x] 未决问题全部有结论，需求方确认
