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
| 搜索 | `PUT https://www.modelscope.cn/api/v1/dolphin/models` | JSON 体含 `Name` / `PageNumber` / `PageSize` / `SortBy`（`Default` / `GmtModified` / `GmtCreated` / `DownloadsCount` / `StarsCount`） |
| 任务类别 | `GET https://www.modelscope.cn/api/v1/tasks` | `Data.Domains[].Tasks[]` 提供英文任务名与中文名，用于类别下拉框 |
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

`llmdownw` 与 `llmservew` 一致，采用「底部状态栏 + `ttk.Notebook` 分页」结构，
避免搜索、详情、下载配置与日志挤在单页：

```
┌ 模型浏览 │ 下载 │ 本地管理                                  ┐
├ 搜索 [关键字] [搜索] 类别 [全部类别] 公司 [全部公司] 排序 […] ┤
├ 模型列表（仓库/公司/任务/下载/体积，点列头排序） │ 模型详情 / 文件列表 ┤
└ 状态栏                                                      ┘
下载页：当前模型｜下载设置（目录/源/并发/校验/掩码）｜下载 停止｜进度｜日志
本地管理页：刷新 / 打开目录 / 删除模型 + 本地模型列表
```

- 模型浏览页左右分栏首次布局即固定分隔条，列表四列完整可见，详情页提供
  「前往下载」按钮跳转到下载页；
- **类别筛选**：启动时后台拉取 `/api/v1/tasks` 填充中文任务名下拉框；
  选类别且关键字为空时用任务英文名作为 `Name` 搜索（ModelScope 的名称检索
  会命中任务），再按返回项的中文任务名做本页过滤，避免模糊命中混入；
  有关键字时按关键字搜索，仅做本页任务过滤；
- **排序**：工具栏「排序」是服务端全局排序（默认 / 下载最多 / 星标最多 /
  最新发布 / 最近更新）；点击列头（模型 / 任务 / 下载 / 体积）对当前页做
  升降序切换并显示箭头，不触发重新请求；
- 选中列表行即拉取详情；`组织/模型` 直接回车可跳过搜索加载指定仓库；
- 详情请求带世代号，快速切换选择时丢弃过期响应；
- 下载的选源（`modelscope` / `hf-mirror` / `auto` 测速）、并发、SHA256 校验
  与掩码过滤全部复用 CLI 口径；
- 「本地管理」扫描 `目标目录/<组织>/<模型>`，双击可切回模型浏览页反查详情；
- 删除展示完整路径、大小与文件数，经二次确认后才执行；目标目录不存在或越界
  由 `delete_local_model` 拒绝。

### 4. CLI 补齐发现与本地查看

- `fcmd llmdown search [--keyword] [--page] [--limit] [--sort] [--company]`
- `fcmd llmdown info <repo> [--readme] [--limit]`
- `fcmd llmdown local [--root]`

删除只放在 GUI（带确认），不提供无确认的 CLI 删除入口。

### 6. 公司筛选与归一

公司信息不新增请求，直接从列表项的元数据归集：优先 `owner`（`Path`），
缺失时依次回落到 `Owner` / `Author` / `Publisher`，再回落到 `组织/模型`
的组织段；都没有则归入「其他」，不会因筛选消失。

归一规则（`model_company`）：

- 归一键 = 去空白、折叠 `-` / `_` / 空格 / 点号并转小写；
- 命中小写别名表时使用统一显示名：`Qwen` / `qwen` / `Alibaba` / `Aliyun`
  都显示「阿里巴巴（Alibaba）」，`BAAI` 显示「智源（BAAI）」等；
- 未命中别名时保留原写法，全大写 / 全小写名称统一为首字母大写，避免
  `acme` / `ACME` 分裂成两行。

界面：工具栏新增可编辑下拉框（含「全部公司」），输入文本按别名与子串过滤
候选项，回车应用、Esc 清空；公司选项随当前页结果归集刷新，「其他」置末。
公司筛选只重渲染本地当前页，不重新请求，并与关键字、类别、列排序叠加；
已选公司在结果里消失时回落「全部公司」。

列表渲染改为按 iid 增量同步（`sync_tree_rows`）：只增删 / 移动变化行，
不再整表删除重建，从而保留选中模型与滚动位置，数据量大时也不逐行重排。

### 7. 配置持久化

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

- 代码：`llmdown.py` 新增搜索 / 任务类别 / 详情 / 本地扫描 / 删除 / 进度事件与取消 /
  公司归集与筛选（`model_company` / `company_options` / `filter_by_company`）；
  `llmdownw.py` 新增窗口与公司下拉、增量列表同步（`sync_tree_rows`）；
  `pyproject.toml` 新增 `llmdownw` 入口。
- 测试：`tests/tools/llm/test_cli_llmdown.py` 覆盖搜索请求体、排序取值、
  任务类别解析、详情解析、分页校验、本地扫描 / 删除 / 越界拒绝、进度事件与
  取消保留 `.part`、公司归一 / 选项排序 / 别名筛选 / `--company` 过滤；
  `test_cli_llmdownw.py` 覆盖注册、配置回退、几何恢复、格式化、类别过滤、
  列排序、打开目录与增量列表同步。
- 文档：README 工具表新增 `llmdownw` 并更新 `llmdown` 说明；本 ADR 与索引。
- 静态清单已重新生成。

## 交接

- ModelScope 的 `SingleCriterion` / `Target` 任务过滤在匿名接口上不生效（已探测
  多种字段形态均被忽略），因此类别筛选用「任务英文名作 `Name` 搜索 + 本页中文名
  过滤」实现；若官方后续开放服务端任务过滤，替换 `_start_search` 的查询构造即可。
- 类别筛选与列排序都只作用于当前页（30 条）；公司筛选同样只作用于当前页，
  下拉选项也按当前页归集。如需跨页筛选，需改成多页抓取后在本地分页，注意
  请求量与取消逻辑。
- 当前只接入 ModelScope 列表；如需 Hugging Face 搜索，新增 `search_models` 的
  源参数并保持返回结构一致即可。
- README 以纯文本展示，未做 Markdown 渲染；如需渲染，先引入无依赖的轻量转换，
  并保留原始文本兜底。
- 删除为 `shutil.rmtree`（确认后执行），未接入回收站；如需回收站语义，
  复用 cmder 的删除实现或新增平台适配层。
- 下载暂停 / 恢复队列尚未接入；取消只保证保留 `.part`，重新下载时按 Range 续传。
