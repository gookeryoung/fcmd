# 0030 locllmw 模型页：整合 llmdownw

- 状态：已落地
- 日期：2026-11-14
- 相关文档：[ADR 0016](0016-llmdown-model-browser.md)、[ADR 0025](0025-sidebar-page-navigation.md)、[ADR 0027](0027-locllmw-rename.md)

## 背景

`llmdownw` 是独立的 ModelScope 模型浏览器 / 下载器，已有完整的搜索、类别 /
系列筛选、详情、文件清单、下载队列与本地模型管理。但 `locllmw` 的「模型」页
只能列出本地 GGUF 供启动，用户仍需切到另一个窗口去搜索、下载与管理模型目录。
两边都围绕 `llmenv` 的模型目录工作，却维护两套窗口设置与两套服务状态展示。

## 决策

### 1. 三个子页取代独立窗口

模型页改为嵌套 Notebook（`ttk.Notebook`）承载三个子页：

| 子页 | 内容 |
|---|---|
| **浏览** | ModelScope 搜索、类别 / 系列筛选、服务端与列头排序、模型详情、README、文件清单 |
| **已安装** | 扫描下载目录下 `<组织>/<模型>`，打开目录、二次确认后删除 |
| **下载** | 当前模型、目标目录 / 下载源 / 并发 / 校验、进度与日志、开始 / 停止 |

嵌套 Notebook 只用于页面内部的子视图，主页面导航仍是 [ADR 0025](0025-sidebar-page-navigation.md)
的 `SidebarNav`；这与「嵌套子页保留 Notebook」的边界一致。

### 2. GGUF 启动列表移到「运行」页

模型页的职责收敛为「获取与管理模型文件」，原先占用模型页的本地 GGUF 列表
（能力标签、显存适配、服务名联动）移到「运行」页顶部，与启动参数、操作栏
组成完整的「选模型 → 调参 → 启动」链路。这样模型页三个子页与用户要求一一对应，
不再把「可启动模型」和「已下载目录」混在同一棵树里。

### 3. 页面类独立，避免与宿主状态冲突

- 新增 `_locllmw_models.py` 的 `_ModelBrowserPage`，持有自己的控件、队列、
  后台任务与轮询；`locllmw` 只负责创建它并调用 `settings_dict()` / `close()`。
- 下载设置从 `~/.fcmd/ui/llmdownw.json` 迁入 `locllmw.json` 的 `download` 字段
  （`dest / source / workers / verify / include / exclude`），非法值逐项回退默认。
- 关窗时若仍在下载，先弹二次确认；确认后置停止事件，已下载部分保留 `.part`。

### 4. 移除独立工具 `llmdownw`

- 删除 `src/fcmd/apps/locllm/llmdownw.py`；
- 删除 `pyproject.toml` 入口、`_schema` GUI 工具集合与静态清单条目；
- 测试文件改为 `test_locllmw_models.py`，保留纯逻辑（格式化 / 过滤 / 排序 /
  增量同步 / 打开路径）并覆盖设置字典与「工具已移除」断言；
- README 工具表移除 `llmdownw`，说明并入 `locllmw`。

## 验证

- 真实 Tk 冒烟：`locllmw` 打开后模型页显示三个子页，浏览页加载 ModelScope
  列表、已安装页扫描本地目录、下载页可保存设置，切页状态保留。
- 全量测试与 `make check-fast` 通过。

## 交接

- 「浏览」与「已安装」的联动（双击已安装模型回到浏览详情）已保留；如需从
  「已安装」直接启动 GGUF，应跳到「运行」页并复用模型选择逻辑，不在模型页
  复制启动按钮。
- 下载设置当前在关窗时统一写回；若需实时保存，应在 `_ModelBrowserPage` 的
  参数变更处调用宿主回调，而不是恢复独立设置文件。
