# Changelog

本文件记录 fcmd 的版本变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

- `filescanw` 紧凑模式（默认开启，结果页可关）：任务行走 dense 间距（按钮 26px），
  规则 / 组合式 / 白名单表行高 24px，结果上下文单行省略、悬停看全文；关闭后恢复换行
  与舒适间距，开关随设置持久化。
- `filescanw` 设置页单文件上限改用 MB 输入（0.1 MB 步长，存储仍为字节）；后缀包含 /
  排除 / 强制留空时显示占位提示；结果页收敛为四列（文件 / 规则、严重级、行号、上下文），
  引擎 / 耗时并入文件行摘要，固定列宽只留 300/56/48，剩余宽度全给上下文；筛选与导出
  拆成两行，窗口可缩窄且长路径按中间省略。
- `filescanw` 任务行的增量扫描 / 全量重扫收进扫描按钮旁的「更多扫描方式」菜单：
  增量扫描强制复用缓存，只重扫新增 / 改动过的文件；全量重扫跳过缓存复用、逐个文件
  重新读盘（结果仍回写缓存），用于目录大改后校正结果。引擎 `scan_paths` 同步新增
  `refresh` 参数。
- `filescan` 收录 12 组场景规则模板（改写自 uniscan `examples/rules`，MIT 署名见
  `rules/scenarios/NOTICE`）：CLI 用 `--scenario <slug>` 或
  `rules --action scenarios`，GUI 规则页新增「载入场景」。
- `filescan` 结果可解释性：文本报告给出「扫描完成 / 部分成功（有错误）/ 已取消」状态与
  `通过 N` 计数，命中行和结果树新增「引擎」「耗时」（线程 / 进程 / 缓存），JSON 统计
  同步补 `files_passed` / `status` 与命中的 `engine` / `elapsed`。
- `filescan` 文档解析补齐旧版 Office：`.doc` / `.xls` / `.ppt`（OLE 复合文档）纳入
  内置白名单与 `DOCUMENT_EXTENSIONS`，用标准库解析片段表 / SST / 记录树；加密、
  损坏、无文本分别归入既有跳过原因。
- `filescan` 结果缓存改为长期有效：去掉 7 天 TTL，条目只在文件大小 / 修改时间或
  规则与命中相关选项变化时失效；保留 10 万条容量上限（最旧优先淘汰）与手动清理。
- `filescan` 新增 SARIF 2.1.0 报告格式（`--format sarif` / GUI 导出 `.sarif`）：每条命中
  一条 `result`，带规则元数据、行列区域、打码片段与 invocation 统计；严重级映射
  `info/low → note`、`medium → warning`、`high/critical → error`，可直接被 GitHub 等
  代码扫描平台消费。
- `filescan` 规则再扩展（规则集 `version: 6`）：文档元数据（作者 / 公司 / 标题等）、
  自定义词典、SHA-256 文件哈希、高熵串检测；新增 `file_risk_score` 风险分（报告与
  结果树文件行展示）。
- `filescanw` 结果页右键「加入路径排除白名单」：自动新增禁用的路径规则并追加到
  启用组合式，下次扫描生效；规则类型下拉补齐中文名称（文档元数据 / 自定义词典 /
  文件哈希 / 高熵串）。
- `filescan` 规则新增 `kind=filetype`（魔数识别 pdf/zip/exe 等，发现 rename 文件）与
  `target=archive_path`（zip / tar 成员路径匹配，不解压）；规则集 `version: 5`。
- `filescanw` 规则类型下拉改为中文名称（关键词 / 正则 / 时间 / 文件大小 / 文件类型），
  新增「文件类型」类型与「归档成员路径」匹配对象。
- `filescan` 规则继续扩展（规则集 `version: 4`）：新增 `target=path` 完整路径匹配、
  `kind=size` 文件大小规则（`lt` / `gt` / `between`，支持 10KB / 1.5MB / 裸字节）、
  `regex.validator`（Luhn / 身份证 / 统一社会信用代码 / IBAN）与规则级
  `min_hits` / `max_hits` 命中次数阈值。时间 / 大小命中均为非敏感，报告与复制不打码。
- `filescanw` 规则对话框新增「大小」类型、路径匹配对象、正则校验器下拉与
  最少 / 最多命中次数；类型切换时同步显隐并自适应高度。
- `filescan` 规则新增 `kind=time` 时间匹配：`target=modified/created/accessed` +
  `compare=before/after/between` + `value`（+ `value2`），按文件时间戳求值，不读内容；
  与内容 / 文件名规则可在同一表达式里组合，内容被跳过时时间命中照常报告；时间值
  非敏感，报告 / 结果树 / 复制不打码。规则集 `version` 升到 3。
- `filescanw` 规则编辑对话框新增「时间」类型：切换后显示时间字段、条件、
  起止时间（`介于` 才显示结束时间），关键词 / 正则专属控件与目标 / 大小写选项随之
  隐藏；规则表摘要显示如「修改时间 早于 2026-01-01」。
- `filescanw` 规则编辑对话框按匹配类型切换字段：关键词显示「关键词 + 整词」，
  正则只显示「正则表达式」；类型提示同步说明默认大小写，且关键词 / 正则各自记住
  大小写开关。保存时只写当前类型对应的字段，避免残留另一类型的输入。关键词与正则
  输入框都改为可换行的多行编辑器（按宽度自动折行，高度 64–96px），长内容不再显示
  不全；换行与行首缩进只用于排版，保存时合并为单行。切换类型时对话框高度随可见
  字段自动收缩 / 展开。
- `filescan` 组合表达式改为「组合式列表」：`RuleSet.expressions`（每条含 id / 名称 /
  表达式 / 启用），启用组合式按 OR 生效，没有启用组合式时回落到 OR 全部启用规则；
  组合式可嵌套引用其它组合式，加载时校验重复 id、未知引用与循环。未启用的组合式
  （以及未启用规则）只要被引用就参与匹配，可只作为构件使用。规则集 / 配置文件
  `version` 升到 2（旧 `expression` 字段移除，不兼容旧文件）。
- `filescanw` 规则页新增「组合式」表（启用 / ID / 名称 / 表达式）与新增 / 编辑 /
  删除 / 启停按钮；编辑对话框可插入规则或组合式 id、保存前校验引用与循环。原单行
  表达式输入框移除；`--expr` 仍可临时覆盖（新增 `cli_expr` 组合式）。
- `filescan` 组合表达式新增 `near[k]` / `within[k]` 邻近算子（默认 k=1）：
  `password near[3] cn_phone` 表示两条规则在 3 行窗口内同时命中；优先级为
  `not` > `near` > `and` > `or`，`near` 的项必须是规则 id，同一链的窗口行数须一致。
- `filescan` 组合表达式支持「构件规则」：`enabled` 只决定表达式为空时是否进入默认
  OR，显式表达式引用到的规则（含未启用规则）都会参与匹配，因此可以新增规则后保持
  不启用、只在组合式里使用；引擎改为按表达式引用集合编译规则。GUI 表达式旁新增
  「插入选中规则」按钮，并提示启用与显式引用的区别。
- `filescan` 新增文本归一化（默认开启）：匹配前对每行做 NFKC 归一化，全角
  字母数字、`①`、`㈱` 等兼容字符按等价形式命中，`excerpt` / 上下文仍取自原文；
  CLI `--no-normalize-text`、配置 `options.normalize_text` 与 GUI「文本归一化」
  可关闭，该选项参与缓存指纹。
- `filescanw` 规则表在名称前新增 ID 列：直接显示规则 id（如 `cn_phone`），方便
  在组合表达式里引用，悬停提示给出示例（`pii and not email`）。
- `filescan` 规则新增匹配对象 `target`（`content` / `filename`，默认 `content`）：
  文件名规则在读取内容前对文件名求值，命中行号 0、位置记为「文件名」，可与正文
  规则在同一 `and` / `or` / `not` 表达式中组合；即使内容因二进制或文档加密被跳过，
  文件名命中仍照常报告；GUI 规则对话框新增「匹配对象」下拉，文本报告显示
  `@ 文件名：…`。
- `filescan` 内置规则扩充到 16 条，新增数据库连接串、密码赋值、JWT 令牌、
  密码哈希（bcrypt / argon2）、GitHub / Slack 令牌、阿里云 AccessKey ID、
  统一社会信用代码与微信号，按「个人信息 / 联系方式 / 财务信息 / 凭证 /
  企业信息 / 社交账号」分组并各自带严重级；所有规则文案与正则为本项目原创。
- `filescan` 规则新增严重级 `severity`（`info` / `low` / `medium` / `high` / `critical`，
  默认 `medium`，旧规则集自动补齐）：文本报告以「【高】」标注、CSV 增加「严重级」
  列（中文标签）、JSON 输出英文枚举；GUI 结果树新增严重级列并按主题色着色
  （严重 / 高 / 中 / 低），支持按严重级筛选，规则表与编辑对话框均展示 / 可改严重级。
- `filescan` 新增扫描范围剪枝：内置跳过目录列表（`.git` / `node_modules` /
  `__pycache__` / `.venv` / `dist` / `build` 等，含斜杠条目按相对根目录路径后缀
  匹配），默认生效、可在设置页 / 配置 / CLI（`--skip-dir`）整体替换，空列表表示
  不跳过；新增「跳过隐藏文件/目录」开关（`skip_hidden`，默认关闭，避免漏扫
  `.env`）。统计新增 `files_skipped_hidden` 与 `dirs_skipped`，扫描概要与 0 命中
  诊断分别在非零时展示。
- `filescanw` 结果页支持定位文件与复制命中：选中行后可「定位文件」（Windows
  explorer `/select`、macOS Finder `-R`、Linux xdg-open 打开父目录），双击行同样
  定位；「复制命中内容」「复制上下文」「复制扫描说明」（按「【规则：…，匹配项：…】」
  逐条生成），右键行还有「打开文件」「复制文件路径」。
  复制默认打码，与导出一致由「导出 / 复制原文」开关（原「导出原文」）控制。
  结果树去掉重复的「命中内容（打码）」列，命中内容继续在上下文列里用底色标出，
  规则名只在第一列出现一次。
- `filescanw` 结果页上下文列改为换行完整显示：按列宽自动换行、行高随内容增长，
  长上下文不再被列宽省略成「…」；命中片段用主题 warning 底色 + `on_accent` 前景单独
  标出，打码文本与底色在同一格里同时可见（列宽变化时清排版缓存并重算行高）。
- `filesyncw` 改为侧边栏分页（「比较 / 结果」两页）：比较页放文件夹对、比较方式与
  过滤、执行比较；结果页放差异表、同步方式 / 预演 / 执行同步，比较完成后自动切到
  结果页，两页状态栏文字各自保留。文件夹对的「交换左右」移到左右两张卡中间；
  `filesyncw.json` 新增 `sidebar_collapsed` 记录侧边栏折叠状态。
- `filescanw` 扫描页改为任务化管理（行卡与 `wslmanw` 实例页同构）：每个任务固定
  一个扫描目录，列表首项始终是虚线框「添加任务」；任务行只保留扫描 / 查看结果
  两个按钮，移除统一走卡片右上角（旁边新增「清空」），扫描中扫描按钮切成
  「停止扫描」，同一时间只跑一个任务，其余行禁用。
  任务行支持展开 / 收起，详情就地显示完整目录、状态、最近扫描时间与结果摘要；
  扫描按钮是行内第一个主动作并加宽到普通图标按钮的两倍，最近扫描时间以标签形式
  紧挨目录显示，命中情况（如「命中 12 文件 / 48 条」/「无命中」）也在折叠面板上
  直接可见；任务行与详情里的结果摘要只保留命中数字，完整口径仍写状态栏与结果页。
  顶部移除只作用于当前选中任务，清空一次移除全部；两者都不弹确认，只清列表与
  结果，不删除磁盘目录；没有任务时用居中的空态
  说明替代原先贴在卡片底部的单行提示。`filescanw.json` 新增 `tasks`（id / 目录 /
  最近扫描时间 / 摘要 / 状态），旧 `paths` 自动迁移成一个目录一个任务，上一版写出的
  `instances` 键也会读取。扫描结果按任务 id 落盘到 `ui/filescanw-results.json`
  （命中原文 / 上下文、跳过 / 错误与统计；与设置文件分开），退出应用后重开仍能恢复
  结果树与任务入口；结果页标题胶囊显示所属任务，扫描完成自动进入，也可从任务行
  「查看结果」重新进入；结果页标题右侧新增任务下拉按钮，可在已有结果的任务之间直接
  切换，不必回扫描页。下拉内容改用 QMenu 弹出菜单而不是 QComboBox，选取后菜单由 Qt
  自动关闭，避免下拉列表卡在展开态或点击回弹。
- 修复 `DiskGrid` 删除目录后列数变少时残留旧列 `stretch` 的问题：扫描页添加多个
  目录再移除回空列表，「添加目录」卡现在重新占满整行，而不是仍按旧列宽只占
  半格 / 三分之一格。
- `filescanw` 扫描页动作卡改为「扫描控制」：左侧当前选项改用带语义色的标签
  （递归 / 深度 / 并发 / 白名单 / 缓存，流式布局窄窗口自动换行），右侧开始 /
  停止按钮靠右对齐；按钮固定最小宽度，不再和卡片标题重复出现「开始扫描」。
  开始按钮改用新增的 Hero 主按钮样式（比常规控件更高、字号更大、左右留白更宽），
  主题切换时高度仍按字体实测值缩放。
- `filescanw` 扫描目录移除不再弹确认框：`StorageCard` 新增 `confirm_remove` 开关
  （默认 `True` 保持 llmw / wslmanw 现有交互），filescanw 置 `False`；目录只是
  列表条目、随时能再加回，不存在删除磁盘文件的风险。
- `filescanw` 扫描进度收进状态栏：扫描中状态栏右侧显示确定进度条 +
  「扫描 42% · 420 / 1000 个文件 · 12 个/s · 剩余 48s」（与 `wslmanw` 下载 / 导出
  同一口径），左侧状态文字保留当前文件路径；扫描页删除页内进度条与独立「取消」
  按钮，改为「开始扫描」按钮在扫描中切换成「停止扫描」（危险色 + 停止图标），
  停止请求下发后按钮显示「停止中…」并禁用，线程池收尾后恢复为「开始扫描」。
- `filescanw` 新增「设置」页（侧边栏第四页），把低频配置从扫描页挪走：**扫描范围**
  （递归 / 符号链接 / 二进制 / 解析文档 + 深度、大小、单文件上限、并发、上下文、
  后缀包含 / 排除 / 强制、修改时间，改用共享 `FormGrid` 两列表单）、**后缀白名单**
  （表格 + 新增 / 编辑 / 删除 / 恢复默认）、**缓存与配置**（使用缓存、清理缓存、
  导入 / 导出配置）。扫描页只留「扫描目录」+「扫描控制」两张卡，摘要
  （递归 / 深度 / 并发 / 白名单后缀数 / 缓存开关）与开始按钮同排，动作不再和
  表单抢高度。
- 存储目录组件通用化：`StorageCard` 支持单目录 / 多目录 / 平权目录三种配方
  （`add_text` 可为 `None`、`max_count` 上限、`labels` / `metas` 覆盖标题与副标题），
  补齐「更改」目录、`swap()` 交换与达到上限隐藏添加入口；新增 `StoragePairCard`
  统一左右成对目录：沿用 filescanw 扫描目录的 `DiskTile` 磁盘卡外观（容量 /
  「更改」选取 / 「打开」），固定左右两槽、未选择显示占位文案，交换按钮垂直居中
  放在两张卡中间，发 `changed` / `swapped`；`filesyncw` 文件夹对改用它。
  `filescanw` 扫描目录也改走 `StorageCard`（平权目录配方），删除旧的列表 + 三按钮区。
- `filescanw`「扫描目录」改用共享 `StorageCard`（与 `wslmanw` 工作目录 / `llmw` 存储
  同一套磁盘卡网格）：目录以卡片列出（路径 + 容量 + 打开 / 移除），末尾虚线卡添加目录，
  标题行保留「清空」；`StorageCard` 新增 `show_primary` 选项，扫描目录没有主目录概念时
  隐藏主目录胶囊与「设为主目录」，宿主不必再自己拼列表 + 增删按钮。
- 分节标题助手 `section_header()` 改为 `SectionHeader` 控件：标题 + 弹性空白 +
  `add_action()`，调用方直接 `addWidget` 到卡片正文；llmw 性能测试与概览页
  同步替换，不再手工拼布局再 `addLayout`。
- 按钮图标的 `set_icon_name()` 支持 `size`，并把尺寸纳入幂等判断；行卡展开 / 复制 /
  `DiskAddTile` / `SidebarActionButton` 改走 mixin 方法，不再直接调 `icons.set_icon`。
- `filescanw` 改成侧边栏分页（与 `wslmanw` / `llmw` 同构）：扫描 / 结果 / 规则三页
  由共享 `QtMainWindow` + `SidebarNav` 承载，支持折叠、`Ctrl+Tab` 切页与几何记忆；
  每页是 `AppPage` 子类，状态栏按页隔离（扫描进度 / 结果筛选 / 规则操作各归各页），
  扫描完成自动切到结果页。扫描页表单套 `PageScroll`（窗口压矮时滚动而不是裁掉
  底部动作行），结果页表格吃满页面高度。侧边栏折叠状态随 `filescanw.json` 落盘
  （新增 `sidebar_collapsed` 字段，旧设置文件不受影响）。
- 新增 `SectionTitle` 标签子类：页面标题、卡片小节、对话框分组统一用它，不再各自
  `QLabel` + `setObjectName("SectionTitle")`；`section_header()` 与窗口页标题行改用，
  llmw 对话配置与概览页的手写标题同步替换。
- 表单路径输入新增 `PathInputMixin`：`PathRow` / `PathField` 共用 `value` /
  `set_value` 与目录 / 文件选择对话框，各自只保留选中后的落地差异（提交信号 /
  路径提示）。
- 新增通用 `ProgressBar`：统一隐藏百分比、忙碌态切换与按比例 / 百分比写入
  （内部按 `maximum` 折算，避免大字节数溢出）；`LoadingBar`、`ProgressRow`、
  `DiskTile` 以及 filescan / ncmconvertw / llmw 模型下载改用，不再各自
  `QProgressBar` + `setTextVisible` + 手写刻度。
- 文本控件新增 `ToneLabelMixin`（`tone` 属性 + 幂等重抛光）：`StatusTag`、`HintLabel`、
  `ElidedLabel`、`MonoLabel`、`ValueLabel` 与选项卡 `ChoiceCard` 复用；`hint_label()` /
  `value_label()` / `mono_label()` 工厂改为返回对应子类，业务侧用 `set_tone()` 替代手写
  `setProperty("tone", ...)` + unpolish / polish。
- 重构 `fcmd.gui.widgets` 按钮：新增 `ButtonStateMixin` 与 `Button` / `LinkButton` /
  `IconButton` 子类，`button()` 等工厂返回子类；文案、图标、变体切换统一走
  `set_text` / `set_icon_name` / `set_kind` / `set_style_name`，业务侧不再手写
  `setObjectName` + unpolish / polish。`ChoiceCard`、`DiskAddTile`、
  `SidebarActionButton` 同样复用该 mixin；模块级函数保留为兼容入口。
- `wslmanw` 镜像页首屏提速：WSLUI 镜像源不再与官方清单同链等待，改由
  `_load_wslui_catalog` 后台单独加载、到货后补一次重绘；超时收紧为
  `wsl --list --online` 12 秒、官方清单 10 秒（WSLUI 侧 5 / 4 / 8 秒），
  慢或不可达都不再拖住列表。测速候选改为前 1 MiB / 1.5 秒、12 路并发，
  下载速度看门狗宽限期 8 秒 → 5 秒、窗口 2 秒 → 1.5 秒，换源更快。
  名称匹配结果按「清单 + 条目」做 `lru_cache`，镜像页重绘不再重复比对。
- `wslmanw`「初始化环境」新增「安装显示依赖库」与「安装中文字体」两个独立项目：
  用 APT 补装 WSLg 下跑 Qt / GTK 窗口所需的 EGL / GL / XCB / Wayland / fontconfig
  系统库；中文字体默认改走轻量的 `fonts-wqy-microhei`（下载约 1.8 MB，原 Noto CJK
  方案约 57 MB），完整字形（`fonts-noto-cjk`）与彩色 Emoji 拆成可选项目、默认不勾。
  字体缓存改为 root 增量 `fc-cache -s`，不再重复 `fc-cache -f` 全量扫描；APT 增加
  `Acquire::Languages=none` / `Dpkg::Use-Pty=0`，未配国内源时日志提示下载可能较慢。
  探测分别用 `ldconfig -p`、`fc-list :lang=zh`、字体族名判定，状态区缺库时列出库名。
- 修复 Qt 窗口入口在缺系统库（如 Kali 缺 `libEGL.so.1`）时误报「未安装 Qt 前端」：
  `fcmd.gui.compat.qt_import_failure` 区分「绑定未安装」与「已安装但加载失败」，
  后者的提示附带底层错误与 Linux `apt install` 命令。
- `wslmanw`「初始化环境」在 uv 之后新增独立项目「安装 auto-uv-env」：走官方安装脚本
  （域名不可达时回退 GitHub），把 shell 集成幂等写进 rc，进入含 `pyproject.toml`
  的目录时自动用 uv 创建 / 激活 `.venv`；探测与状态区同步显示其版本。
- 修复 fspack 打包后的 GUI / 工具入口：窗口骨架显式持有 ``QMenu``（PySide6 6.11
  会回收 ``addMenu(str)`` 的返回值，reshackw / pptmakew / pevieww / envw /
  filesyncw / paramcadw / parameshw / regexvalidatew / renamerw 启动即崩）；
  wslmanw 与 8 个 ``llm*`` CLI 模块补 ``__main__`` 守卫（此前 exe 静默退出 0）；
  cmder 包门面改惰性加载并在发现前别名 ``__main__``，修复 cmderw / llmenv /
  llmmodel / llmserve 的重复注册。新增全窗口菜单归属、入口守卫与 runpy
  子进程回归测试。
- 修复自动下载选到「测速快、下载慢」的源：测速窗口改为最多 1.5 秒 / 1 MiB，
  12 路并发（此前 3 MiB / 2.5 秒、6 路，正式下载前先拉十几份 3 MiB）；超时前
  已读字节仍计入吞吐。下载中持续 5 秒均速低于 512 KB/s 且还有后备源时自动
  换源（丢掉该源 `.part`），不再出现 RLCPlus-9 这类条目整份文件按 KB/s 拉完。
- `wslmanw` 镜像列表首次获取提速：官方清单（`wsl --list --online` +
  `DistributionInfo.json`）与本地状态串成两段，WSLUI 镜像源不再进这条链路（它只影响
  行卡上的源数量与下载候选，改为单独后台加载、到货后补一次重绘）；超时同时收紧：
  `wsl --list --online` 30 → 12 秒（该命令冷启动实测 15–38 s，超时后清单 / 缓存照样
  出列表）、官方清单 30 → 10 秒（文件只有约 19 KB）、WSLUI 直取 / helper / 清单
  改为 5 / 4 / 8 秒。断网或 GitHub 不可达时首屏从「等满 30 s 以上」降到 12 秒以内；
  WSLUI 清单仍是一次请求直取 `online-distros`（与 WSL Dashboard 一致），失败才回到
  install helper 要地址。行卡重绘时 WSLUI 匹配结果按 `(清单, 条目)` 缓存，不再每次
  重绘都对每个条目全量比对。
- 新增可用镜像源：Arch Linux 的 WSL 镜像（`fastly.mirror.pkgbuild.com/wsl`）在中科大 /
  南京大学有字节一致副本（实测 431 KiB/s vs 官方 170 KiB/s）；GitHub Releases 上的发行版
  镜像（openSUSE / SUSE / AlmaLinux / RLC+）在部分网络直连不可用，改用 ghfast.top /
  gh-proxy.com / ghproxy.net 三个加速前缀（仅当条目有官方 sha256 时参与，下载后仍然
  校验摘要）。AlmaLinux / SUSE 这类条目从「1 个官方源」变为 4 个候选。
- `wslmanw` 实例页在「迁移」后新增「复制」：弹框询问新实例名称与目标目录，默认
  `<源>-copy`（占用时递增）与源实例所属工作目录的 `install/<新名称>`；复制会导出
  源实例并以新名称导入，源实例保持不变，失败只回收新建实例与目标目录。
- `wslmanw` 镜像页行卡折叠时只显示镜像源数量（`镜像源：N 个`），具体源名称与 URL
  收进展开区的「镜像源」字段（tooltip 同一份名单）：展开后能看到 WSLUI 清单给出的
  全部源（如 Kali 的清华 / 腾讯云 / 南京大学 / 北外四个）加可校验国内镜像与官方源。
  原来的「国内镜像」字段只统计中科大 / 阿里云 / 华为云三条可校验改写，Kali 这类没有
  国内副本的条目会显示「无（官方源）」，与实际可用源数量不符。
- `wslmanw` 镜像源按本机架构过滤，不再拿装不了的制品充数：官方清单按 `arch` 字段
  比对（x86 机器上不再列 aarch64 的 `.wsl`），WSLUI 源按 URL 里的架构（`/amd64/`、
  `*-arm64`）判断，认得出且不匹配就丢掉；认不出时保留。因此 Kali / Arch 是
  4 个 WSLUI 镜像 + 1 个官方源。
- 修复 `wslmanw`「已备份」始终为空：镜像页原来读 CLI 配置的 `backups.dir`，而「备份」
  按钮写到 GUI 工作目录的 `backup` 子目录，两边不是同一份配置。现在按全部已配置工作
  目录的 `backup` 子目录判定；实例页行卡新增「已备份」胶囊，备份成功后与镜像页同步
  生效。
- 修复「开发时 WSL 实例经常自己启动」：`llmw` 的 vLLM 平台探测会对每个发行版执行
  `wsl -d <名称>`（探测等于启动），而运行页构造即调用它——打开 llmw 或跑测试都会把
  Ubuntu / kali-linux 一个个拉起来。现在默认**只探测运行中的实例**，其余实例名放进
  `VllmPlatform.stopped` 交给界面提示；只有安装 vLLM（确认对话框）时才带
  `allow_start=True` 连已停止实例一起探。平台探测拆到 `_llmvllm_platform.py`。
  测试侧新增全局守卫 `forbid_wsl_distro_start()`：任何测试里出现
  `-d` / `--exec` / `--terminate` / `--import` 等会启动 / 改动实例的调用直接失败；
  gui 与 llm 测试默认把平台探测替换成「不支持」，不再碰真实宿主机。
- 推进 ADR 0046：wslman 六个对话框迁至 `apps/wslman/qt/dialogs.py`，
  `_wslmanw_qt.py` 保留同名 re-export；布局守卫支持 `apps/<app>/qt/` 三层。
- 推进 ADR 0046 P2/P3：`WslmanQtWindow` 方法拆为 10 个 `qt/*.py` mixin，行卡/详情、
  初始化环境、SSH 迁移段落独立成模块；`_wslmanw_qt.py` 约 5.3k → 480 行。
- `wslmanw`「初始化环境」加 `uv`、Node.js 改为 nvm + Bun，并统一国内加速口径：
  APT 已装包跳过 + 索引新鲜不重复 `update`；pip / uv 索引由卡片里独立的
  「软件包源」选择（自动跟随系统镜像，或指定中科大 / 阿里 / 腾讯 / 华为 / 清华 /
  官方 PyPI），与 APT 系统源解耦；`uv` 用 `pip --user` 装、Python 下载走 npmmirror，
  nvm 源码走 gitee + node 二进制走 npmmirror，Bun 走 `npm i -g bun`（二进制随 npm
  包分发），不再依赖 GitHub / astral.sh；状态区新增 `uv` / `Bun` / `Node.js（nvm）`
  与合并后的 `pip&uv` / `npm&bun` 镜像行。
- 修改实例的软件包源后，即使 Python / uv 已初始化，点「开始初始化」也会先执行独立的
  pip / uv 配置步骤（只改 `pip config` 与 `uv.toml`，不重装 uv、不碰系统源）；
  环境检测的初始化项目标签同时覆盖两种状态：绿色「已初始化」/ 红色「未初始化」。
- `wslmanw` 镜像页接入 WSL Dashboard 的公开镜像源清单：每行显示该镜像的全部
  可用源（WSLUI 多源 + 可校验的国内镜像 + 官方兜底），`自动` 下载会并发测速
  择优并依次回退；每个源带自己的落盘名与摘要口径，不同格式不会写进同一个文件。
  名称匹配补充无版本条目的同发行版降级（Debian / Kali / Arch 等），带版本号的
  条目不会被指向别的版本。下载状态栏显示聚合百分比、已下载/总量、速度和剩余时间，
  行内进度与取消保持原样。
- `wslmanw` 实例页在「导出」左侧新增「安装」：支持 `.tar` / `.tar.gz` / `.wsl` 直接导入，
  `.vhdx` 复制到实例目录后 `--import-in-place`，以及 zip / 7z / rar / gz / bz2 / xz /
  tar.bz2 / tar.xz 等压缩包；解压后自动找 `ext4.vhdx`、`rootfs.tar` / `install.tar`，
  只有 Linux 文件树时临时打包成 tar 再导入。
- 修复 `wslmanw`「初始化环境」把 Kali 等自带 python3 的实例误判为未初始化：`uv` / `Bun`
  拆成独立初始化项目，每个项目只看自己那个工具（Python 只看 `python3`，`uv` 看 `uv`，
  `Bun` 看 `bun`，Node.js 看 `node` + nvm）；Bun 项自带 nvm 引导，只勾 Bun 也能装成。
- wslman 测试改为**默认离线**：`tests/apps/wslman/conftest.py` 的 autouse 替身把宿主机
  探测全换成本地假数据（`shutil.which` 恒为 `None`、`LOCALAPPDATA` / `APPDATA` /
  `ProgramFiles` 指向临时目录、`_build_environment` 返回假 runner + 空注册表、
  `FCMD_HOME` 指向临时目录），并拦截 `subprocess.run` / `Popen` / `os.startfile` /
  `QDesktopServices.openUrl`，跑测试不再碰真实 WSL、终端或 IDE；新增
  `tests/apps/wslman/test_wslman_hermetic.py` 自测这套替身。
- `wslmanw` 镜像页筛选精简为四个互不重叠的口径（全部 / 可下载 / 已安装 / 已备份），
  去掉只是「可下载」取反的「仅安装」；每个胶囊补 tooltip，状态行增加「已备份 N」，
  行内「已安装 / 已备份 / 已下载 / sha256」胶囊与详情区都写清含义（已备份 = 备份目录里
  有同名导出包）。
- `wslmanw` 实例页新增「备份」（`archive` 图标）：一键导出到
  `${工作目录}/backup/<实例>.tar`，覆盖前确认；完成后镜像行的「已备份」胶囊立即更新。
- `wslmanw`「迁移 SSH 文件」的状态持久化到 `wslmanw.json` 的 `distro_ssh_status`：
  再次打开配置对话框直接回显上次扫描的标签 / 摘要 / 实例侧现状，不再为看一眼状态去
  启动实例或重扫来源（文件清单仍要求重新扫描，迁移按钮在回显态下禁用）；
  同时修掉设置保存用白名单式构造导致 `distro_env_status` / `distro_opener_paths` 等
  缓存字段被静默丢弃的问题（改为 `replace(self.settings, …)`）。
- 推进 ADR 0046 P4：llmw 模型页 `_BrowsePanel` 方法拆到 `apps/llm/qt/models_*.py`，
  `_llmw_qt_center.py` 约 2.3k → 499 行；P4 续拆 `RunPage` 到 `apps/llm/qt/run_*.py`，
  `_llmw_qt_run.py` 约 2.2k → 771 行；清理已失效的行数白名单项。
- 补完 P4/P5 收尾：`gui/style.py` 拆为 `gui/style/{qss,tokens,state,apply}.py`，`gui/theme.py` 拆为
  `gui/theme/{palette,fonts,builtin,registry}.py`；
  `filescanw` / `filesyncw` / `pevieww` / `renamerw` / `reshackw` / `wslmanw` /
  `pptmakew` / `paramcadw` / `parameshw` / `cmderw` / `llmw` / `mirrortoolw`
  的设置读写迁移到 `gui.settings.SettingsStore`；
  `cli._discovery` 新增 `install_fake_registry` / `reset_discovery_state` 公共测试接缝。
- 结构优化 P0：新增 `fcmd/shared/`（`paths.fcmd_home` / `formatting.format_bytes`），
  `gui.config` 与 `gui.widgets.storage` 改依赖 shared，移除 gui → infras 的反向依赖；
  新增 `tests/test_architecture.py`（import 方向 + 行数预算 + tools 域隔离）。
- 结构优化 P1：`core/toolkit` 拆为 `spec` / `parser` / `schema` / `plan` / `routing` /
  `execute` / `present`，`args.py` / `exec.py` 保留兼容 facade；`core/profiling.py`
  拆为 `profiling/{model,render}.py`，`fcmd.core.profiling` 导入路径不变。
- 结构优化 P2：MCP 从 `cli` 迁出为 `fcmd/mcp/{server,security,whitelist}.py`，
  `cli/mcp_adapter.py` / `agent_*.py` 保留模块别名 shim；新增 `fcmd mcp serve`
  内建命令（缺 `mcp` 依赖时给出安装提示）。
- 结构优化 P3：`infras/net`、`infras/text`、`infras/data`、`infras/fs`、`infras/media` 按主题拆为
  子包，`__init__.py` re-export 旧 API；`media/{deps,icon,image}` 用 deps 集中
  PIL/resvg 可变状态，`ensure_pil()` 注入后对 icon/image 同时可见。
- 修复 `tests/cli/bench_startup.py` 在 Windows 非 UTF-8 终端下读取子进程输出
  抛 `UnicodeDecodeError` 的问题（显式 UTF-8 + replace），启动基准重新可用。
- 结构优化 P4（试点）：新增 `gui.settings.SettingsStore`，`dynacalcw` 的设置读写
  改用统一存储（原子写入 + `.bak` 回退）；其余应用后续按同一接口迁移。
- `@fx.tool` 新增三个编排原语（ADR 0045）：
  - `preconditions=[...]`：目标命令的前置断言，按签名注入 CLI 变量，失败即中止
    （`PRECONDITION_FAILED`），不进 DAG / 不计步数，`--dry-run` 跳过；
  - `steps=[...]`：有序流水线，自动生成链式依赖；`step("task", key=value)` 的
    参数只注入该步骤，不进入 CLI / schema；
  - `aggregate=True`：显式声明编排壳，不再依赖函数体 AST 推断。
  `gitt a` / `gitt i` 改用它：`a` 只做 add + commit 并用 `_require_repo` 断言仓库存在，
  `i` 的 `steps` 才给 `_init` 注入 `create=True`；`--init` / `--no-init` 不再出现。
- `gitt a`（gittool 添加并提交）不再隐式初始化仓库：`needs` 只表达
  「项目必须已在 Git 仓库内」的前置校验，非仓库目录直接报错并提示先运行
  `fcmd gitt i`，不再静默创建 `.git`；需要先初始化时用 `gitt i`。`_init` 改用
  `git rev-parse --show-toplevel` 判定，在仓库子目录执行 `gitt a` 会提交到外层仓库，
  不会创建嵌套仓库；`gitt isub` 跳过已有 `.git` 的子目录并检查每一步返回码，
  失败时报告具体目录而不是打印「已初始化」。
- 按钮 / 开关 / 图标键的禁用态改用主题独立的 `disabled_bg` / `disabled_fg` /
  `disabled_border` 三色：原来禁用态直接复用 `muted` + `surface`，浅色主题里
  禁用按钮比可用按钮还亮、深色主题里文字仍偏亮，两种状态都不容易分辨；现在
  禁用态底色贴近窗口底、文字与边框各退一档，主 / 危险 / Ghost / 侧边栏 / 胶囊
  开关 / 图标按钮与输入框统一生效，四套主题均拉开对比（可在 `theme.json` 的
  `overrides` 里单独覆盖这三个 token）。

- 修复「新版 llama.cpp 引擎跑不了小模型 / 启动即崩溃且日志为空」：官方 Windows 包
  依赖新版 MSVC 运行库，但只导入引擎 zip（不带 cudart 包）时目录里没有
  `msvcp140.dll` / `vcruntime140*.dll`，b12627 这类新构建会在加载阶段 `c0000005`
  崩溃（连 `--version` 都跑不出），同模型在 b12470 上却正常。现在解压安装、
  `resolve_engine` 解析与 `llmdoctor` 体检都会从本机 Visual Studio / VC Redist
  把同名 DLL 补进引擎目录（与 sd.cpp 共用 `llmbase.ensure_vc_runtime`），旧安装
  无需重装即可自愈。只有引擎包、没装 cudart 时不再静默退化：`probe_version` /
  `check_engine` 标出缺 `cudart64_*` / `cublas64_*`，引导用「导入压缩包」把
  cudart 包解到同一目录；启动崩溃（`c0000005` 且日志为空）的错误附加 MSVC
  运行库排查提示。
- llmw 模型页：已转换但未量化的仓库（只有 `converted/<源目录名>/<源目录名>.gguf`）
  现在也有「启动服务」，直接启动 f16 转换产物；扫描本地 GGUF 时一并扫
  `converted/<源目录名>/`，转换产物的量化变体进入精度标签 / 启动菜单，不再出现
  「已转换却不可启动 / 不可量化」。
- llmw 新增服务看护（`LlmState.watch_services`，每 3s）：外部 CLI 启停 / 进程意外
  退出后，概览「运行中的模型」、运行页下拉的「✓ 已启动」、模型页「已启动」按钮都
  自动跟上，不再等应用内的启停操作；快照没变不发信号，不会每秒重建列表。
- llmw 运行页模型下拉修正「已启动」配色：弹层选中项原来写死 `on_accent`，在深色 /
native 主题下是深色字压选中蓝底（几乎看不清），切到已启动模型时绿色标记也会
丢失；现选中项统一用主题 `select_fg`，运行中的项无论是否选中都画绿框并保留
「✓ 已启动」文案。当前项文字色改由项角色直接取语义色并用运行中主题
（`active_theme`），切换已启动 / 未启动模型后不再依赖 QSS 重新 polish 的调色板。
- 对话页「新对话」改用公共 `SidebarActionButton`：与历史条目同宽、同圆角 / 高度，
  底色更实、边框更深；历史列表保持无底色，仅显示条目文字。
- 分页窗口移除页标题下的描述行（`PageSpec.hint` 只保留在侧边栏 tooltip），正文
  更紧凑；对话历史列表不再画列表 / 选中行底色，只显示等宽的历史条目，选中态用
  强调色文字与字重表达。
- 对话历史列表改用公共 `SidebarList`（参考 DeepSeek 侧边栏）：圆角选中底、
  悬停浅底、标题右侧省略、无左侧强调条；组件同时支持两行标题 / 副标题、右侧
  摘要与日期分组标题，后续其它窗口可直接复用。
- 多存储目录设置抽成公共组件 `gui/widgets.StorageCard`：`StorageRoots` 统一
  去重 / 添加 / 移除 / 设为主目录（至少保留一个目录，移除不删磁盘文件），
  变更只发 `changed`，宿主负责落盘；llmw 设置页改为直接复用并删除
  `_llmw_qt_storage.py` 适配层。
- llmw 设置页的磁盘卡片抽成共享 UI 组件 `gui/widgets/storage.py`：
  `DiskGrid` / `DiskTile` / `DiskAddTile` / `DiskLocation` 只负责展示与
  打开 / 移除 / 设为主目录信号，任意应用的目录列表可复用；llmw 侧保留
  `StorageLocation → DiskLocation` 适配与「下载目标」文案。
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
- llmw 转换改用本应用解释器执行 `convert_hf_to_gguf.py`：解释器顺序固定为
  「显式参数（设置页 / `--python`）> `FCMD_CONVERT_PYTHON` > 本应用解释器」，本应用
  解释器可用时不再扫描 PATH 挑别的 python，避免 torch / CUDA 版本不一致导致转换
  失败；只有它缺 `torch` / `safetensors` / `numpy` 时才回退 PATH 候选，并在状态行
  写明改用了哪个解释器。设置页填写的解释器改为在准备阶段先探测，路径写错会立刻给出
  「指定的转换解释器不可用」而不再拖到启动子进程才报 `WinError`；全部候选都不可用时
  错误信息直接给出本应用解释器路径与 `-m pip install …` 命令。
- llmw 转换不再因为模型自带代码的额外依赖失败：转换脚本报缺 `tiktoken` /
  `transformers` 等纯 Python 包时，自动装到当前转换解释器并重跑一次（白名单见
  `llmconvert.CONVERT_SAFE_DEPS`；`torch` 等 GPU 依赖不自动装，避免覆盖用户的 CUDA
  构建，只给出带解释器路径的安装命令）。GLM-4 这类只有 `padded_vocab_size` 的
  `config.json` 会临时补上等值 `vocab_size` 再重跑（llama.cpp chatglm 脚本的默认参数
  早期求值 bug），无论成败都会恢复原始 config。tokenizer 只能靠 transformers
  `trust_remote_code` 加载的模型（如 GLM-4-9B 的 `tokenization_chatglm.py`）在准备
  阶段直接提示无法转换并建议下载官方 GGUF，不再等到脚本跑起来才报 `ValueError`。
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

- 提升画图生成稳定性：sd-server 的 `txt2img` 在进程内串行执行，聊天 / 工作流 /
  看护不会同时向同一服务发多个生成请求；画图启动 / 出图共用操作锁，避免自动重启
  与生成互相踩踏。生成途中服务退出时自动重启并重试一次；连接重置（如
  `WinError 10054`）会附带画图服务状态与日志尾部，便于定位 OOM / 崩溃原因。
- 修复 llmw 退出后画图模型（sd-server）随机被结束：`_in_job` 的 ctypes 调用未声明
  64 位 HANDLE 签名，`GetCurrentProcess()` 伪句柄被截断，导致明明在 Windows Job
  Object 里却判定为「不在 Job」，漏加 `CREATE_BREAKAWAY_FROM_JOB`；从 IDE / 终端
  启动时 Job 关闭会连带结束服务。现按 64 位句柄显式声明参数 / 返回类型，Job 内
  启动的 sd-server 能正确脱离，退出应用后继续运行。
- 提升画图服务稳定性：Windows 下从 IDE / 终端（父进程在 Job Object 中）启动时，
  sd-server 增加 `CREATE_BREAKAWAY_FROM_JOB`，避免 llmw 退出后被 Job 连带结束；
  Job 不允许 breakaway 时自动退回普通创建方式。llmw 运行期间每 5 秒看护一次
  画图服务，意外退出自动重启（连续 3 次失败后停止看护），并在任务日志记录原因。
- 修复多模型目录下「模型管理」只扫描主目录：当主目录是空目录 / 新目录时，
  即使其它模型目录里有模型也会显示「没有本地模型」。现在模型管理汇总全部
  `models_dirs`，空目录跳过、同仓库按首个目录去重，移除 / 清理未完成下载按
  模型实际所在目录执行；新增 `scan_managed_models` 纯函数。
- 修复 FLUX.2（如 FLUX.2-klein-9B）无法启动画图服务：
  识别独立 `flux2` 家族并要求 `llm` + `vae` 组件；自动配对不再把分片
  `model-0000X-of-0000Y.safetensors` 当成单文件文本编码器，跨家族 VAE 也不兜底；
  启动参数为 FLUX.2 自动追加 `--vae-format flux2`；启动失败提示优先列出日志里的
  `[E]` 行，不再只贴单张量校验噪声。
- `llmw` 侧边栏「运行」徽标重启后丢失：画图服务探针在 `RunPage` 构造期就完成，
  早于窗口连接 `runningChanged`，只跑着 sd-server 时徽标停在 0；窗口连好信号后
  补一次同步，重启应用即可恢复运行中模型数量。
- `llmw` / `llmpaint` 画图服务异常结束：sd-server 启动时显式 `stdin=DEVNULL`、
  不再继承父进程控制台句柄，关闭 / 重启窗口或父进程控制台不会因 stdin EOF 带走服务；
  画图服务健康检查默认等待从 300s 放宽到 900s，大模型冷加载不再被超时终止。
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

- llmw 新增「工作流」页：参考 ComfyUI 的节点 / 端口 / DAG 思路，提供类型化节点
  （文本输入、LLM 对话、sd.cpp 文生图、保存图片 / 文本）、拓扑执行与节点缓存、
  取消、JSON 持久化到 `${FCMD_HOME}/flows`，并内置「文生图」「提示词润色 → 出图」
  「LLM 问答 → 保存」模板；MVP 用线性节点链编辑，画布与 ComfyUI 子集导入留待后续。
- `filesync` / `filesyncw`：文件夹比较与同步，支持 compare / sync / mirror / update 四种模式，
  可预览变更、按规则过滤、跳过冲突项；`filesyncw` 为 Tk 窗口版。
- CI 发布流水线接入 fspack 多平台打包：推 `v*.*.*` tag 时产出 Windows / Linux 便携 zip，
  与 PyPI 产物一并发到 GitHub Release；支持 `workflow_dispatch` dry-run 与 `release_tag` 预检。

### 变更

- `cmder`：文件面板刷新改为后台扫描，补齐高 DPI 观感与主题热切换。

### 修复

- 修正 Windows 上回收站、文本预览与排序相关测试的平台假设。
- 修正 Linux 下 pyrefly 对 `ctypes.windll` 的类型推断，发布流水线可在 Linux runner 上通过类型检查。
