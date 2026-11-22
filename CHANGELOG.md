# Changelog

本文件记录 fcmd 的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

- GUI 英文字体统一为 `Cascadia Code`：`apply_theme` 用 `QFont.setFamilies` 组「拉丁字体 +
  CJK 字体」链（英文 / 数字走 Cascadia Code，中文逐字回退到微软雅黑等），等宽控件
  （日志 / 代码 / 十六进制视图）同样优先 Cascadia Code；系统未安装时回退原行为。
- `wslman` 默认工作目录统一为 `${FCMD_HOME:-~/.fcmd}/wsl`，按用途细分 `download/`
  （镜像归档）、`install/`（安装目录根，每个发行版一个子目录）、`backup/`（导出备份）：
  wslmanw 下载 / 安装 / 备份默认落在对应子目录（设置页新增「工作目录」卡片展示这些路径），
  `doctor` 的推荐备份目录随之改为 `~/.fcmd/wsl/backup`。
- `wslmanw` 发行版行在名称右侧注明对应的在线镜像（名称 / 友好名 / 词首前缀三级匹配，
  命中给「镜像 Ubuntu 24.04 LTS」+ 架构 / 来源 tooltip，未命中给「本地导入」，镜像清单
  未加载时留空）；启动时先回显镜像缓存，清单更新后只刷新该说明不重建列表。
- `wslmanw` 任务日志从侧边栏页面移到「帮助 → 任务日志…」（`Ctrl+Shift+L`）：
  侧边栏收敛为发行版 / 镜像 / 设置三页；日志窗口为非模态 `LogDialog`，打开时回放
  最近 1000 行缓冲并实时追加，清空同时清缓冲。
- `llmw` 设置页把画图 / 视频 / 音频三张 sd.cpp 引擎卡合并为一张「引擎（sd.cpp：画图 /
  视频 / 音频）」：三者本来就是同一个引擎目录与同一批已安装引擎，现在共用同一个默认 tag；
  按用途覆盖的 `video_engine` / `audio_engine` 配置键与 `use_sd_engine_for` /
  `usage_engine_tag` API 一并移除（旧配置文件里的残留键不再读取）。
- `wslmanw` 发行版列表改为行卡（对齐 llmw 模型列表）：行内给名称 / 版本 / 占用 /
  状态与默认胶囊 / 启动停止按钮，详情（BasePath、虚拟磁盘、占用量、默认用户、来源）
  通过行内「展开」就地显示，移除下方的表格 + 详情分栏；状态轮询改为就地刷新行内状态。
- `wslmanw` 发行版页完全对齐 llmw 模型列表的视觉：列表自身透明无外框（与
  `ModelResultList` 同口径）；行卡用共享契约 `QFrame[itemRow="true"]`（1px 边框 + 8px
  圆角的卡片、hover / 选中 2px 边框、选中底为半透明的 `accent_soft_strong`），不再按
  `ModelItemRow` / `ImageItemRow` / `DistroItemRow` 分别开 QSS 规则；副标题只留版本，
  磁盘占用（估算值带 `≈`）移到右侧数值位，与 llmw 模型行「左侧标题 / 副标题 + 右侧
  数值与动作」的结构对齐。
- 可展开行卡的骨架抽成共享 `fcmd.gui.qt.widgets.ItemRow`（标题 + 副标题 / 状态胶囊 +
  右侧数值与动作 + 行内展开区，提供 `add_tag` / `add_note_widget` / `set_metrics` /
  `add_action` 插槽）：llmw 模型浏览行、`wslmanw` 发行版行与镜像行都改为继承它，
  不再各写一份骨架（ADR 0038 D1 随之更新，原「不做行卡基类」的重新评估条件已触发）。
- `llmw` 模型行展开后的分页按运行页设计改造：共享 `pill_tabs`（`PillTabs` / `PillTabBar`，
  运行页与行卡详情同一套药丸页签）去掉页签上方的分隔线与 QTabBar 整条基线；行卡详情的
  页签容器（`RowTabs`）及其内部 `QStackedWidget` 置透明，跟上行卡底色（选中时一起变色），
  不再露出一块窗口底色；「文件列表」不再画表框 / 交替行色（`QTableWidget#ModelFiles`），
  与「详细信息」同为行卡底色，表格紧跟页签顶部。
- `llmw` 模型行「已启动」勾选图标在浅色主题下不可见：禁用的主按钮底色会变浅，图标
  按可用性切 `on_accent` / `muted`（已启动与任务进行中都走 muted），不再留下白勾。
- `wslmanw` 发行版行把「镜像 …」说明从名称行挪到副标题行（与版本同排、吃掉剩余宽度、
  超长右侧省略），不再与行尾的「已停止 / 默认」胶囊挤在一起；`ItemRow.add_note_widget`
  新增 `stretch` 参数，可让某块小字吃掉剩余宽度。
- `wslmanw` 行内启动按钮不再停留在禁用态：任务结束后立刻按当前状态恢复（此前要等下一次
  3 秒状态轮询，首屏刷新后「启动」会先以灰色禁用样式显示一段时间）。
- `wslmanw` 下载进度进状态栏：镜像下载在状态栏右侧显示确定进度条 + 短文案
  （百分比 / 速度），左侧状态文字给出完整口径「下载 X · 42% · 1.2 GB / 2.6 GB ·
  3.2 MB/s · 剩余 12s」；其它后台任务（导出 / 注销 / 安装）在任务期间显示加载条，
  结束自动收起。`_source.download` / `download_mirrored` 新增 `progress` 回调
  （CLI 不传不受影响），`LoadingBar.set_progress` / `WindowStatusBar.show_progress` /
  `QtMainWindow.show_progress` 支持确定进度（内部按 0..1000 折算，避免几十 GB
  文件撑爆 QProgressBar 的 int 范围）。
- `wslmanw` 镜像列表把「已下载 / 已安装」的镜像固定置顶（档位：已安装 → 已下载 →
  其它），按名称 / 按友好名 / 已备份优先只在同档位内排序；原「已安装优先」排序项
  随之改为「已备份优先」（已安装置顶已成为固定规则，不再需要单独选项）。
- 修复 `wslmanw` 安装已下载镜像报「系统找不到指定的路径」：默认装到工作目录
  `~/.fcmd/wsl/install/<名称>`，此前父目录不存在时直接跑 `wsl --import`（它只建最后
  一级目录）；现在与 CLI 同口径，导入前 `mkdir(parents=True, exist_ok=True)`，
  建目录失败时给出「无法创建安装目录」的可读提示而不是 wsl 的原始报错。
- 依赖分组整理：Pillow 已是主包常驻依赖，移除冗余的 `img` extra（`ocr` / `office` / `full`
  不再转发该分组；`pip install fcmd[img]` 退化为无作用的多余分组名）。`fcmd env` / `fcmd doctor`
  的可选依赖报告覆盖全部 extra（pdf / ocr / svg / crypto / archive / filecheck / color / pe / ppt），
  不再把 Pillow 当作可选依赖；缺常驻依赖时提示重装主包而不是安装不存在的 extra。
- 应用 `locllm` 更名为 `llm`：包目录改为 `src/fcmd/apps/llm/`，模块 `locllmw.py` 与
  `_locllmw_*` 统一改为 `llmw.py` / `_llmw_*`；命令名统一为 `llmw`（旧命令 `locllmw`
  已移除）；窗口设置 `~/.fcmd/ui/locllmw.json` 在首次访问时
  一次性搬迁为 `ui/llmw.json`（主文件与 `.bak` 一起移动并删除旧文件），模型列表磁盘缓存改名为
  `~/.fcmd/cache/llmw-model-list.json`（缓存可重建，不做迁移）。
- Python 版本要求放宽到 ≥3.10：3.11+ 继续使用 PySide6，3.10 使用 PySide2；新增运行时
  绑定映射与 3.10 标准库兼容垫片（`tomllib` / `StrEnum` / `datetime.UTC` / `itertools.batched` /
  `typing.override` / `Path.is_junction`），CI 与 tox 矩阵加入 3.10。
- GUI 全面迁移到 Qt（PySide6），移除全部 Tk 工具窗口：
  - `filescanw`：扫描 / 结果 / 规则三页改为 Qt，白名单与规则编辑、后台扫描进度与取消保持原有口径；
  - `wslmanw`：发行版表 + 详情 / 下载 / 日志分页，3 秒运行状态轮询、下载源选择与安装本地镜像对话框迁到 Qt；
  - `llmw`：模型页补齐 ModelScope 浏览 / 下载、已安装仓库、转换与量化向导，移除 `--ui tk` 与全部 Tk 模块；
  - `alarmclock` / `checksum`：数字时钟提醒与校验和计算器改为 Qt 窗口；
  - `cmderw`：双面板文件管理器改为 Qt（面板 / 标签 / 传输队列 / 快速查看 / 缩略图 / 命令行 /
    目录树 / 搜索 / 比较同步 / 多重命名 / 用户命令 / 颜色规则 / 打包解压 / 分割合并 / 查重等），
    `app.py` 只保留纯逻辑；
  - 删除 Tk 共享层（`fcmd.gui.window` / `fcmd.gui.sidebar` 与 `theme.py` 的 ttk 渲染），
    新增 `fcmd.gui.pages`，主题注册表保留为纯数据模块；
  - 断言 Tk 内部实现的测试改写为 Qt 等价断言，纯逻辑与设置测试保持原样。
- 新增 `fcmd.gui.geometry` 纯函数模块，各窗口与设置统一复用 `WxH+X+Y` 解析、夹紧与居中算法。
- `llmw` 设置页移除存储 / 下载 / 转换 / 引擎卡片下方的静态小字说明；目录来源与旧配置迁移提示保留，引擎操作反馈按需显示。
- `llmw` 设置页将存储与引擎拆为独立卡片，引擎卡片紧随存储下方；
  引擎路径名称同步为「引擎目录」，与「模型目录」对齐。
- `llmw` 设置页引擎列表压缩为单行行式：标签 + 可执行文件 +
  右侧「默认」或「设为默认」+ CUDA / CPU 类别胶囊，移除来源角标与重复的当前默认按钮；
  列表高度随内容伸缩，超过 6 个引擎才出现滚动条。标签使用常规标签参与布局，
  避免与状态胶囊 / 可执行文件路径重叠。引擎卡片移除状态标签与「体检」入口，
  「刷新」与「引擎」标题同行靠右；「导入目录」「导入压缩包」两个独立按钮放在
  引擎目录行的浏览按钮右侧。
- `llmengine` 新增 `releases` 子命令列出可下载的 llama.cpp 版本（含变体与大小，
  `--assets` 展开资产名），`install --asset` 可按选中资产下载安装；
  `llmw` 设置页引擎卡片新增「下载引擎」对话框：版本 / 变体列表、实时下载进度，
  安装后刷新引擎列表，尚无默认引擎时自动设为默认；版本 / 变体下拉附用途说明
  （CUDA / Vulkan / ROCm / HIP / SYCL / OpenVINO / Metal / OpenCL / CPU），
  已安装引擎的类别胶囊 tooltip 同样带说明，`llmengine releases` 输出变体说明。
- `llmengine` 下载默认优先国内镜像（`gh-proxy.com` / `gh-proxy.net`），失败按顺序回退
  下一镜像与 GitHub 直连；可用 `FCMD_GITHUB_MIRROR` 覆盖（`direct` 表示只用直连）。
  版本列表 / 下载请求都带连接与读取超时，全部失败时一次回报完整地址列表；
  「下载引擎」对话框可随时关闭，不再因后台请求无法退出。
  「下载引擎」后台请求改用守护线程执行，下载卡住时关闭窗口 / 退出应用
  不会被 ``QThreadPool`` 等待，可直接结束进程。
- `llmw` 设置页「导入目录」改为把所选 llama.cpp 目录复制到引擎根目录下，
  不再只登记源路径；「导入压缩包」仍解压到同一引擎目录。存储 / 引擎路径行的
  选择按钮文案由「浏览…」改为「更改」。

### 修复

- 修复 `filescanw` / `pptmake` / `wslmanw` / `envw` 启动时把 `app_name` 按位置传给
  仅关键字参数的 `run_window()` 而抛 `TypeError` 的问题；新增 Qt 窗口入口签名回归测试，
  逐项调用各 `run_qt()` 并以共享入口的真实签名校验。
- `llmw` / `llmserve` 启动兼容性：
  - `--flash-attn` 按引擎 `--help` 探测语法（旧版布尔开关 / 新版 `on|off|auto`），
    按可执行文件路径与修改时间缓存，升级引擎后自动失效，不再因参数解析失败让引擎直接退出；
  - 引擎日志出现 `unknown model architecture: 'xxx'` 时，启动失败提示直接给出升级命令与日志路径，
    而不是只贴一段日志尾部。

### 重构

- 清理迁移后遗留的死代码与常量（cmder / llmw / alarmclock / paramcad / envdev 等）。
- 合并重复实现：Qt 后台事件桥统一为 `EventBridge`、等宽字体统一为 `mono_font()`、
  终端显示宽度填充统一到 `infras.text`、窗口默认尺寸统一走 `geometry.size_from_geometry`、
  压缩包格式探测统一为 `archivex.try_detect_format`、llmw 转换 / 量化共用一次
  `scan_model_env()`；cmder 的大小格式化复用卷容量格式化。

## [0.3.12] - 2026-11-11

### 新增

- `filesync` / `filesyncw`：文件夹比较与同步，支持 compare / sync / mirror / update 四种模式，
  可预览变更、按规则过滤、跳过冲突项；`filesyncw` 为 Tk 窗口版。
- CI 发布流水线接入 fspack 多平台打包：推 `v*.*.*` tag 时产出 Windows / Linux 便携 zip，
  与 PyPI 产物一并发到 GitHub Release；支持 `workflow_dispatch` dry-run 与 `release_tag` 预检。

### 变更

- `cmder`：文件面板刷新改为后台扫描，补齐高 DPI 观感与主题热切换。

### 修复

- 修正 Windows 上回收站、文本预览与排序相关测试的平台假设。
- 修正 Linux 下 pyrefly 对 `ctypes.windll` 的类型推断，发布流水线可在 Linux runner 上通过类型检查。
