# 0016 llmdown 模型浏览器：ModelScope 搜索 / 详情 / 下载管理

- 状态：已落地（v1）
- 日期：2026-11-11
- 关联：`src/fcmd/tools/llm/llmdown.py`、`src/fcmd/tools/llm/llmdownw.py`、0013（GUI 技术路线）

## 背景

`llmdown` 原先只有 `files` / `mirror` / `download` 三个子命令：用户必须先知道
`组织/模型` 仓库名，再用 `files` 看清单。缺少「发现模型 → 看详情 → 下载」的完整链路，
也不便于清理本地已下载目录。

目标是参考 LM Studio 的浏览体验，在同一个工具里提供：

- ModelScope 模型列表与搜索；
- 选中模型后的详情（README、任务、框架、许可证、下载量等）；
- 带进度与取消的下载；
- 本地模型目录的查看、打开与删除。

## 决策

### 1. ModelScope 只读接口复用现有 JSON 请求层

| 用途 | 请求 | 说明 |
|---|---|---|
| 搜索 | `PUT https://www.modelscope.cn/api/v1/dolphin/models` | JSON 体含 `Name` / `PageNumber` / `PageSize` / `SortBy`（`Default` / `GmtModified`） |
| 详情 | `GET https://www.modelscope.cn/api/v1/models/{repo}` | `Data` 内含 `ReadMeContent`、`Tasks`、`Frameworks`、`Organization` 等 |
| 文件清单 | 沿用 `fetch_files(repo, "modelscope")` | 已有实现；GUI 详情页展示大小与 SHA256 |

`_http_json` 扩展为可指定方法与 JSON 体，仍统一把网络 / 解析错误转换为
`ModelToolError`。搜索与详情都返回不可变 dataclass（`ModelSummary` /
`ModelSearch` / `ModelInfo`），Tk 层只做渲染，不在 UI 里解析 JSON。

### 2. 下载核心增加进度回调与取消，不复制一套下载逻辑

`download_files` 新增两个可选参数：

- `reporter: Callable[[ProgressEvent], None]`：传入时用 `_ReporterProgress`
  替代 rich 进度条，按 `start` / `update` / `done` 事件上报任务级进度；
- `should_stop: Callable[[], bool]`：在每个文件开始、流式分块与哈希校验循环中检查，
  触发后抛 `DownloadCancelled`，**保留 `.part`** 以便下次续传。

CLI 不传这两个参数时行为与原来完全一致。Tk 窗口通过队列把事件送回主线程，
避免跨线程操作控件。

### 3. Tk 窗口布局与交互

`llmdownw` 单窗口，顶部搜索栏，中部左右分栏，底部下载配置与日志：

```
┌ 搜索 [关键字] [搜索] 排序 [默认/最近更新] [上一页] [下一页] 页码 ┐
├ 模型列表（仓库/任务/下载/更新/体积） │ 详情 / 文件列表 / 本地管理 ┤
├ 目标目录 浏览｜下载源｜并发｜校验｜包含/排除掩码｜下载 停止       ┤
├ 总进度 / 当前文件进度                                            ┤
└ 日志 / 状态栏                                                    ┘
```

- 选中列表行即拉取详情；`组织/模型` 直接回车可跳过搜索加载指定仓库；
- 详情请求带世代号，快速切换选择时丢弃过期响应；
- 下载的选源（`modelscope` / `hf-mirror` / `auto` 测速）、并发、SHA256 校验
  与掩码过滤全部复用 CLI 口径；
- 「本地管理」扫描 `目标目录/<组织>/<模型>`，双击可反查 ModelScope 详情；
- 删除展示完整路径、大小与文件数，经二次确认后才执行；目标目录不存在或越界
  由 `delete_local_model` 拒绝。

### 4. CLI 补齐发现与本地查看

- `fcmd llmdown search [--keyword] [--page] [--limit] [--sort]`
- `fcmd llmdown info <repo> [--readme] [--limit]`
- `fcmd llmdown local [--root]`

删除只放在 GUI（带确认），不提供无确认的 CLI 删除入口。

### 5. 配置持久化

窗口位置、目标目录、下载源、并发、校验开关与包含 / 排除掩码保存到
`${FCMD_HOME:-~/.fcmd}/ui/llmdownw.json`；非法值逐项回退默认，与 0013 的 GUI
口径一致。Tk 类标注 `pragma: no cover`，测试只覆盖纯函数与注册。

## 备选方案与取舍

| 方案 | 不采用的原因 |
|---|---|
| 依赖 `modelscope` SDK | 仅为搜索 / 详情引入大依赖，且 SDK 版本与接口变动不可控 |
| 抓取网页 HTML | 页面为前端渲染，结构不稳定；官方 JSON 接口足够 |
| GUI 内重写下载循环 | 会与 CLI 的续传 / 兜底 / 校验口径分叉 |
| 用 rich 进度条嵌 Tk | rich 面向终端，无法嵌入 Tk；用事件回调更直接 |
| 删除直接 `rmtree` 无确认 | 违反不可逆操作必须确认的约定 |

## 落地状态

- 代码：`llmdown.py` 新增搜索 / 详情 / 本地扫描 / 删除 / 进度事件与取消；
  `llmdownw.py` 新增窗口；`pyproject.toml` 新增 `llmdownw` 入口。
- 测试：`tests/tools/llm/test_cli_llmdown.py` 覆盖搜索请求体、详情解析、
  分页校验、本地扫描 / 删除 / 越界拒绝、进度事件与取消保留 `.part`；
  `test_cli_llmdownw.py` 覆盖注册、配置回退、几何恢复、格式化与打开目录。
- 文档：README 工具表新增 `llmdownw` 并更新 `llmdown` 说明；本 ADR 与索引。
- 静态清单已重新生成。

## 交接

- 当前只接入 ModelScope 列表；如需 Hugging Face 搜索，新增 `search_models` 的
  源参数并保持返回结构一致即可。
- README 以纯文本展示，未做 Markdown 渲染；如需渲染，先引入无依赖的轻量转换，
  并保留原始文本兜底。
- 删除为 `shutil.rmtree`（确认后执行），未接入回收站；如需回收站语义，
  复用 cmder 的删除实现或新增平台适配层。
- 下载暂停 / 恢复队列尚未接入；取消只保证保留 `.part`，重新下载时按 Range 续传。
