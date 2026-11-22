# Changelog

本文件记录 fcmd 的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

- GUI 共享层目录拉平：`fcmd/gui/qt/*` 全部上移到 `fcmd/gui/`，主题翻译模块改名为
  `gui/style.py`（主题注册表仍为 `gui/theme.py`），分页纯函数改名为 `gui/nav.py`；
  `gui/widgets.py` 拆成 `gui/widgets/` 包（`text` / `tables` / `buttons` / `tabs` /
  `rows` / `cards` / `forms` / `metrics` / `log` / `splitter`），
  `from fcmd.gui.widgets import Card` 等公开导入不变；工具清单 `_manifest_data` 同步重生成。
- 移除 `devman` 与 `devmanw`：删除开发环境管理工具（node / python 多版本安装、切换、
  卸载与 PATH 集成，含其 Qt 窗口）、对应测试与入口注册，ADR 0024 同步删除；`fcmd`
  工具总数与 `_manifest_data` 清单重生成。
- sd.cpp 引擎卡补齐管理入口：与推理引擎共用 `EngineCard` 与 `EngineManageDialog`，
  支持备份（`<引擎根>/backups/sd-<tag>-<时间>.zip`）、删除（默认引擎需先切换）与清理
  下载缓存（zip / .part / 临时解压目录，`backups/` 保留）；sd.cpp 无转换源码，管理
  对话框自动隐藏「清理源码 / 全部清理」，仅保留下载缓存清理。
- llmw「模型转换」的「引擎 tag」改为可编辑下拉框（列出已安装引擎，也可填自定义 tag）；
  新增「查看支持的架构…」读取当前转换脚本的 `conversion/` 注册表，方便判断某个模型
  架构（如 WeMM-Video-9B 的 `WeMMVideoForConditionalGeneration`）是否可转换。不支持时
  可在「转换脚本」指定支持它的脚本 / 源码包，或直接下载官方 GGUF。
- 推理引擎与画图 / 视频 / 音频引擎（sd.cpp）共用同一个 `EngineCard`：标题行、目录行、
  紧凑引擎行、空态与列表伸缩只有一份实现，通过 `EngineCardConfig` 区分标题、目录来源、
  列表 / 设为默认 / 安装回调、类别胶囊与管理入口。
- 推理引擎卡片移除「导入目录」按钮（仍可「下载引擎」「导入压缩包」）；sd.cpp 引擎卡
  改由同一 `EngineCard` 构建，目录行与推理引擎完全一致：可编辑引擎目录（「更改」/
  恢复默认）、「下载引擎」（只拉在线版本）与「导入压缩包」（本地 sd.cpp zip），
  不再提供「打开 / 安装引擎…」。
- 转换架构检查补上「刚下载的源码」：首次转换时先取源码再按真实转换脚本校验架构，
  不再等脚本跑起来才失败。WeMM-Video-9B 这类 `WeMMVideoForConditionalGeneration`
  架构不在 llama.cpp 转换脚本支持列表内，错误信息会提示优先下载官方
  `WeMM-Video-9B-GGUF` 而不是转换。
- llmw「导入压缩包」支持导入 llama.cpp 源码：选到源码包时不再提示「不能作为引擎」，
  而是复制到引擎根 `<引擎根>/llama.cpp*.zip` 并写入「模型转换 → 转换脚本」，转换时
  直接复用；选到引擎 / cudart 包仍按原逻辑安装。混合选择会提示分开导入。
- llmw 推理引擎（llama.cpp）卡片标题行「管理…」打开引擎管理对话框：集中提供备份
  （整目录打包为 `<引擎根>/backups/llama-<tag>-<时间>.zip`）、删除（只允许非默认
  引擎）与清理（下载缓存：引擎 / cudart zip、.part、临时解压目录；各引擎目录下的
  llama.cpp 源码；`backups/` 永不清理）。引擎行只保留「设为默认」，不再堆备份 /
  删除按钮。
- 转换可停止：底部状态行在转换 / 准备阶段显示「停止转换」，取消标记会中断 llama.cpp
  源码下载、解释器探测与转换脚本子进程（无输出长任务也会被及时终止）；关闭窗口时
  同样会先停止转换。
- 设置页「推理引擎（llama.cpp）」卡片标题行显示当前引擎 tag 的 llama.cpp 源码状态：
  已包含源码 / 未下载（路径与提示在 tooltip），切换转换引擎 tag 后即时刷新；
  「模型转换」卡片不再重复该状态。
- 修复 llmw 转换「卡死在准备阶段」：准备阶段（探测 torch 解释器、检查架构、首次
  获取 llama.cpp 源码）现在逐步回传状态行并用忙碌进度条表示进行中；解释器探测单次
  超时 60s，源码包改走与引擎下载相同的 GitHub 镜像候选（`FCMD_GITHUB_MIRROR` 可覆盖、
  直连兜底），镜像候选多时每源重试次数收敛，避免国内网络下首次转换长时间无反馈。
- 内部整理：Qt 绑定兼容层 `_pyside_compat.py` 改名为 `compat.py`（现位于 `gui/compat.py`），导入路径与测试同步更新；行为不变。
- 修复 llmw 模型列表搜不到 Qwen 等官方仓库：默认「适合本机」此前按 ModelScope 的
  **整仓体积**过滤，而 GGUF 官方仓库（如 `Qwen/Qwen3-8B-GGUF`）的总大小是多份量化
  之和（约 32 GB），在 16–32 GB 机器上会被整体误杀。现在 GGUF 仓库按名字里的参数量
  估算单份 Q4 体积（`8B` / `30B-A3B` / `8x7B`），非 GGUF 仓库仍按整仓体积；名称
  搜索时跳过「适合本机」过滤，输入 `Qwen` 即可看到官方命中结果。
- 修复行卡选中后动作分隔线看不清：分隔线常态改用 `border_strong`，选中行把
  `selected` 同步到分隔线并换成强调色；llmw 模型行与 wslmanw 实例 / 镜像行同时生效。
- 修复 wslmanw 实例列表上下行「标签 / 启动按钮前的分隔线」不对齐：右侧数值列改为按
  列表共用样本测量后固定宽度，磁盘占用文案长短不同不再挤动左侧标签与右侧动作区；
  镜像行把「仅 wsl --install」纳入样本，模型 / 镜像 / 发行版三类行卡共用同一口径。
- 模型 / 镜像列表行卡的标签与功能键布局统一并收紧：行内胶囊（状态 / 量化 / 发布方 /
  下载进度）共享同一套高度、圆角、内边距与字号，边框改同色系浅描边；右侧数值摘要
  （体积 / 状态）独立为 `RowMetrics` 小号加粗样式；动作区前加 1px 细分隔线，
  与标题 / 标签分成两区；行内按钮左右内边距收紧，转换 / 量化改用 Ghost 次要样式，
  下载 / 启动服务 / 安装保持主按钮。llmw 模型行与 wslmanw 镜像 / 发行版行同时生效。
- `wslmanw`「打开终端」进入 Linux 用户主目录：启动参数固定带 `--cd ~`，不再继承
  Windows 侧当前目录（之前会落到 `/mnt/c/...` 这类映射路径）；CLI `wslman open`
  的 `--workdir` 口径不变。
- GUI 共享层整合：`QtMainWindow` / `QtToolWindow` 的设置 / 几何 / 主题 / 关闭逻辑拆成
  `gui/shell.py` 的 mixin 并由 `WindowShellMixin` 组合，两个窗口骨架不再各存一份；
  应用页面统一继承 `gui/page.py` 的 `AppPage`（页面 key + 按页状态通道 + 卡片辅助），
  页面登记改用 `PageSpec` / `QtMainWindow.add_pages`。llmw 五个页面与 wslmanw 三个页面
  已迁移；页面状态改为写入自己的分页，后台回调切页后不再串页。
- 主题可配置性：`Theme` 新增 `Metrics`（圆角 / 控件高度补偿 / 按钮内边距），QSS 只从
  主题度量生成；`ui/theme.json` 支持 `overrides` 与 `apps.<窗口名>` 覆盖调色板 token、
  字号与度量（非法值逐项忽略），切主题时保留手写覆盖段；同一主题 id 的不同覆盖会重新
  应用样式表。
- 状态栏页内补充（如「Ubuntu-24.04 · 已停止 · v2 · ≈12.0 GiB」）改为**右对齐**：
  主状态文字靠左、选中项摘要靠右并紧挨右侧备注 / 加载条；原来两段各占一半宽度且都左对齐，
  摘要看起来悬在状态栏中间。
- 修 `wslmanw` 状态栏串页：3 秒状态轮询会把「Ubuntu · 已停止」这类发行版摘要写进
  当前页，切到镜像 / 设置页后底部还挂着它；摘要现在固定写到「发行版」页的页内补充，
  其它页只显示自己那一页的内容。
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
- 可展开行卡的骨架抽成共享 `fcmd.gui.widgets.ItemRow`（标题 + 副标题 / 状态胶囊 +
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
- 修复行卡列表「点名字没反应、要再点旁边」：共享 `ItemRow` 里的标题 / 副标题 / 状态胶囊
  原本是 `TextSelectableByMouse`（`ElidedLabel` / `StatusTag` 的默认值），落在文字上的
  点击被标签吃掉，`QListWidget` 收不到、选不中这一行。现在行内文字统一
  `NoTextInteraction`（标题、副标题、数值、胶囊、备注小字），点标题即可选中整行，
  wslmanw 发行版 / 镜像列表与 llmw 模型列表同时生效。
- `wslmanw` 发行版行的启停按钮按状态换主题：运行中显示**红色危险按钮**「停止」，
  已停止 / 未知时仍是主按钮「启动」；状态轮询原地切换（不重建行），任务进行中禁用时
  图标色调退回 `muted`，避免禁用底色变浅后图标看不清。共享层新增
  `widgets.set_button_kind`，动态动作按钮可在 `#Primary` / `#Danger` / 无变体间切换。
- `wslmanw` 展示层把「发行版」统一改称**实例**（页面 / 标题 / 对话框 / 状态栏 / CLI 帮助文案），
  内部模型与 CLI 输出仍是发行版 / `distro`，指同一对象。
- `wslmanw` 启动实例改用后台看护循环（`wsl.exe -d <名称> --exec /bin/sh -c
  "while :; do sleep 3600; done"`，`CREATE_NO_WINDOW` + `SW_HIDE` + 断开标准流）：关闭 wslmanw
  不再结束实例（此前一次性命令跑完就被 WSL 收回 Stopped），只有用户点「停止」/
  「关闭全部」或 `wsl --shutdown` 才会停。
- 修复 `wslmanw` 启动实例立刻弹「启动失败」：启动是异步的（实测冷启动约 0.7–2.6 秒才
  从 Stopped 迁移到 Running），此前 `spawn` 完立刻用 `wsl -l -v` 断言状态，几乎必然
  误报。新增 `wslman.wait_until_running`（默认最长 20 秒、每 0.15 秒轮询一次），只有
  等够时间仍不是 Running 才按失败提示（文案注明已等待时长并提示稍后刷新）。
- 修复启动实例后弹出一个一直黑屏的控制台：看护进程原来用 `DETACHED_PROCESS |
  CREATE_NO_WINDOW`，两个标志互斥，Windows 反而给 `wsl.exe` 建了可见的空白控制台。
  改为与 `_runner` 同口径的 `CREATE_NO_WINDOW` + `STARTF_USESHOWWINDOW/SW_HIDE`
  （真机枚举顶层窗口验证：启动前后无新增可见窗口，实例仍 Running）。
- 修复输入法组合时占位文字与组合串重影：`QPlainTextEdit`（llmw 对话输入框等）在 preedit
  显示期间仍绘制 placeholder。新增应用级事件过滤器 `gui/ime.py`
  （`PlaceholderImeGuard`，`create_application` 里装一次）：组合期间临时清空占位文字，
  组合结束 / 失焦后还原；覆盖全部窗口与后续新增输入框，不需要各自处理。
- 修复本地已有同名目录时看不到 ModelScope 官方模型（如磁盘上有 `Qwen3.8-27B`，官方
  `Qwen/Qwen3.8-27B` 却只有一个「同名 2」的本地行）：「同名折叠」不再把本地目录条目
  （`repo` 里没有 `/`）和远程仓库折进一组，本地目录各自独立成行，官方仓库正常显示；
  远程仓库之间的同名折叠保持不变。
- `llmw` 模型详情的「文件列表」增加下载状态与「下载全部」：文件表第三列显示
  「已下载 / 部分下载 / 未下载」（按本地完整文件与 `.part` 判断，状态行给「已下载 N /
  未下载 M」），「下载所选」右侧新增「下载全部」，只补尚未下载完的文件（已下载跳过、
  `.part` 续传，全部下完后按钮禁用）；重新展开同一仓库或下载结束时只刷新状态列，
  不重拉在线清单。
- 修复「适合本机」把已安装模型的官方条目藏掉（如本地有 `Qwen3.8-27B` 时看不到官方
  `Qwen/Qwen3.8-27B`）：容量估算按显存 / 内存口径，官方原始仓库整仓体积（约 52 GB）
  必然超容，于是整条被过滤。现在**已安装模型的同名条目始终保留**（匹配模型名 / 目录名），
  其它无关的超容模型仍按「适合本机」过滤。
- 行卡（wslmanw 实例 / 镜像、llmw 模型）右侧的数值 / 操作按钮 / 展开箭头改为与
  「标签行（副标题）」居中对齐：两行结构下按钮中线与状态胶囊重合，不再介于两行之间；
  空副标题不再占一条空白行，单行行卡整行垂直居中（行高也随之收窄）。
- `llmw` 运行中数量徽标：有运行中的模型时，侧边栏「运行」按钮显示运行总数，
  运行页对应类别页签（对话 / 编程 / 画图 / 视频 / 音频）显示各类别数量，样式为
  半透明圆形数字（跟随主题强调色）；画图（sd.cpp）服务单独计数。共享层新增
  `PillTabBar`（`pill_tabs` 默认装好，`set_badge`）与 `SidebarNav.set_badge`。
- llmw 模型行标题右侧新增小号复制按钮：一键复制完整仓库名（标题只显示模型名，长名会省略），
  复制后图标换成对勾 1.2 秒再复原；该能力作为共享 `ItemRow` 的可配置项
  （`copy_text` / `set_copy_text`），不传就不显示、也不占标题行。
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
    新增 `fcmd.gui.nav`，主题注册表保留为纯数据模块；
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

- 任务日志统一为公共组件：所有 Qt 窗口的日志入口统一挂在「帮助 → 任务日志…」
  （`Ctrl+Shift+L`），由 `gui/logdialog.py` 的 `LogChannelMixin` 提供缓冲 / 回放 / 实时追加 /
  清空 / 关闭联动；`dynacalcw`、`filesyncw`、`mirrortoolw`、`img2icow`、`ncmconvertw`、
  `videoconvw`、`docdiffw`、`img2pdfw`、`pdfcryptw`、`pdfsplitw` 移除页面内日志页签 /
  日志卡片 / 日志分栏，只保留业务内容；`QtMainWindow` / `QtToolWindow` 创建即带日志菜单。
- GUI 页签统一走共享 `tab_widget(...)`（内部 `pill_tabs`）：`reshackw` 行卡展开预览、
  `dynacalcw` / `filescanw` / `paramcadw` / `pevieww` / `llmw` 运行与模型页不再创建
  默认 `QTabWidget`，消除页签基线 / 边框与卡片圆角叠加产生的错位横线；新增源码扫描
  测试，禁止在 `fcmd.gui.widgets.tabs` 之外构造 `QTabWidget`。
- `reshack` / `reshackw` 读取大体积 PE 文件时不再整体解析导入 / 重定位 / 调试等
  目录：改为 `pefile` 的 `fast_load` + 只解析资源目录，`ntoskrnl.exe`（13 MB）
  加载耗时从约 1.2s 降到毫秒级，资源列表与预览行为不变。
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
