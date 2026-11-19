# fcmd

极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。

## 特性

- **极速冷启动**（< 100ms）：三层懒加载，重型依赖（Pillow/pymupdf/cryptography 等）仅在用到时导入。定位慢导入时设置 `FCMD_IMPORT_DEBUG=1`（默认 100ms 阈值）或 `FCMD_IMPORT_DEBUG=50`（毫秒），超阈值的模块与触发来源会输出到 stderr
- **DAG 任务调度**：四种执行策略（sequential/thread/async/dependency）
- **组合 API 简洁**：`@fx.task` 装饰器 + 自动依赖推断 + `@fx.tool` CLI 工具
- **基于 rich 的控制台渲染**：彩色输出、表格与终端宽度自适应
- **便捷脚本模式**：`fcmd pymake b` 一键调用
- **YAML 编排**：GitHub Actions 风格的 `jobs`/`needs`/`matrix`/`if` 任务图
- **零配置工具发现**：`fcmd/tools/` 下的模块自动注册为 CLI 子命令

## 安装

```bash
pip install fcmd          # 或 uv add fcmd
```

可选依赖（按需安装）：

| Extra | 安装命令 | 提供的工具/能力 |
|-------|---------|---------------|
| `img` | - | 已随主包安装（Pillow 为常驻依赖），保留该分组仅为 `ocr` / `office` 组合引用 |
| `pdf` | `pip install fcmd[pdf]` | `pdftool` / `pdfcrypt` / `pdfsplit`（PyMuPDF + pypdf） |
| `ocr` | `pip install fcmd[ocr]` | `pdftool ocr`（pytesseract，需系统 tesseract） |
| `svg` | `pip install fcmd[svg]` | `img2ico` 的 SVG 输入（resvg-py） |
| `crypto` | `pip install fcmd[crypto]` | `cryptool`（cryptography Fernet） |
| `archive` | `pip install fcmd[archive]` | `zipencrypt`（pyzipper AES-256 ZIP） |
| `filecheck` | `pip install fcmd[filecheck]` | `filecheck` 的容器类型识别（puremagic） |
| `color` | `pip install fcmd[color]` | `colortool` 的颜色换算（coloraide） |
| `pe` | `pip install fcmd[pe]` | `reshack` / `reshackw` 的 PE 资源解析与写回（pefile） |
| `ppt` | `pip install fcmd[ppt]` | `pptmake` 的 PPTX 生成与导入（python-pptx） |
| `office` | `pip install fcmd[office]` | pdf + ocr + svg + ppt 全家桶 |
| `win32` | `pip install fcmd[win32]` | `docdiff` / `docdiffw` 的 Word 自动化（pywin32，仅 Windows） |
| `full` | `pip install fcmd[full]` | office + win32 + crypto + archive + filecheck + color + pe |
| `lint` | `pip install fcmd[lint]` | 开发用：ruff + pyrefly |
| `test` | `pip install fcmd[test]` | 开发用：pytest + cov + xdist |
| `dev` | `pip install fcmd[dev]` | lint + test + full + prek + tox |

要求 Python ≥ 3.10（Python 3.10 使用 PySide2，3.11+ 使用 PySide6）。

## 快速上手

### Python API

```python
import fcmd as fx


@fx.task
def extract() -> list[int]:
    return [1, 2, 3]


@fx.task
def double(extract: list[int]) -> list[int]:
    return [x * 2 for x in extract]


graph = fx.graph(extract, double)  # double 自动依赖 extract
report = fx.run(graph)
print(report["double"])  # [2, 4, 6]
```

### CLI

```bash
fcmd                   # 列出所有工具
fcmd pymake b          # 构建
fcmd pymake tc         # 类型检查
fcmd info pymake       # 查看 pymake 子命令
fcmd graph pymake tc   # 输出 DAG（Mermaid）
fcmd env               # 查看运行环境
fcmd doctor            # 诊断环境问题
```

## 内建命令

`fcmd` 自带 7 个内建命令（不通过 `@fx.tool` 注册）：

| 命令 | 说明 |
|------|------|
| `fcmd graph <tool> [subcommand]` | 可视化工具子命令的 DAG（Mermaid/layers/describe） |
| `fcmd info [tool] [subcommand]` | 展示工具或子命令的元信息（不执行） |
| `fcmd completion --shell bash\|zsh\|fish` | 生成 shell 补全脚本 |
| `fcmd yaml <file> [job]` | 从 YAML 加载任务图并执行（支持 `--dry-run`/`--strategy`） |
| `fcmd env [--json]` | 展示运行环境（fcmd 版本、Python、平台、工具数、可选依赖） |
| `fcmd doctor` | 诊断环境问题（Python 版本、核心模块、工具模块、可选依赖、PATH 命令） |
| `fcmd profiler <script.py> [args] [-E html\|text] [-o FILE] [--no-browser]` | 分析含 `fx.run()` 的脚本，生成性能剖面报告（HTML/文本） |

shell 补全安装：

```bash
eval "$(fcmd completion --shell bash)"       # bash
eval "$(fcmd completion --shell zsh)"         # zsh
fcmd completion --shell fish | source         # fish
```

## 工具列表

108 个工具，全部为 Python 模块（`@fcmd.tool` 注册），按用途分组：

工具名（如 `imagetool`）为官方入口并生成同名全局脚本；短名（如 `imaget`）是显式别名，同样保留全局脚本。例外：`taskkill` / `which` 与系统命令同名，不生成同名全局脚本（避免遮蔽系统命令），只保留短名 `taskk` / `wch` 脚本；规范名仍可通过 `fcmd taskkill` / `fcmd which` 使用。仓库不做 `tool`/`cmd` 后缀自动推断，拼错的命令会直接报「未找到工具」。

### GUI 主题

所有 GUI 窗口共用同一份主题注册表（`fcmd.gui.theme` 保存调色板 / 字号 / 持久化，`fcmd.gui.qt.theme` 负责翻译成 QSS），内置 `dark-blue`（深色科技蓝，默认）、`light`、`high-contrast`、`native`（接近原生）。每个窗口菜单栏的「主题」菜单可直接选择，**立即生效**并写入 `${FCMD_HOME:-~/.fcmd}/ui/theme.json`；也可手动编辑该文件（下次启动生效）：

```json
{ "theme": "light" }
```

删除该文件、写入非法主题名或 JSON 损坏时，窗口启动会自动回退到 `dark-blue`，不会影响工具本身运行。

`native` 贴近系统原生配色（Qt Fusion + 系统调色板），其余主题保持填充式按钮与统一圆角。

高 DPI（125% / 150%）下 Qt 自动按逻辑像素缩放，按钮 / 输入框 / 下拉 / 数值框的高度统一由字体行高推导（`gui/qt/theme.py` 的 `CONTROL_HEIGHT_EXTRA`），避免「字变大、控件不变」。

窗口设置统一以原子方式写入（临时文件 + 替换），并在每次有效写入前保留一份同名 `.bak` 备份。主文件缺失、损坏或顶层不是对象时，读取会自动回退到 `.bak`。

#### 字体

界面字体候选顺序为微软雅黑 → 苹方 → Noto / 思源黑体 → 文泉驿，Qt 取系统第一个可用族；
日志等宽控件用 Cascadia / Consolas / DejaVu Sans Mono。

字体不随 fcmd 打包（避免发行包膨胀），需要时用 `fonttool` 按需安装到当前用户字体目录：

```bash
fcmd fonttool install          # 安装 Cascadia Code + Noto Sans SC（下载并校验 SHA256）
fcmd fonttool status           # 查看安装状态与字体目录
fcmd fonttool remove --all-managed
```

Windows / macOS / Linux 下 Qt 直接使用系统字体；需要额外字体时按上面的 `fonttool` 命令安装。

### 项目构建与发布

| 工具 | 别名 | 说明 |
|------|------|------|
| `pymake` | `pm` | 项目构建/测试/检查/发布全流程（b/t/tc/cov/bump/all 等） |
| `autofmt` | - | 代码格式化与检查（封装 ruff format/check） |
| `bumpversion` | - | 版本号自动管理（patch/minor/major + git tag） |
| `packtool` | - | Python 打包（源码/依赖/wheel/嵌入式 Python/zip/清理） |
| `pyprof` | - | Python 代码性能分析（`run`/`module`/`load`；cProfile 采集 + 单文件 HTML 报告：热点排行、调用关系、火焰图、模块聚合） |
| `piptool` | `pipt` | pip 包管理（安装/卸载/重装/下载/升级/冻结，通配符展开与受保护包过滤） |

### 文件与目录

| 工具 | 别名 | 说明 |
|------|------|------|
| `writefile` | - | 写入文本内容到文件 |
| `filedate` | - | 文件日期前缀处理（添加/清除） |
| `filelevel` | - | 文件等级标记重命名（PUB/NOR/INT/CON/CLA） |
| `filerename` | - | 文件批量重命名（replace/insert/case） |
| `filesearch` | - | 文件名/内容搜索（支持正则与通配符） |
| `folderback` | - | 文件夹备份（自动清理旧备份） |
| `folderzip` | - | 压缩当前目录下所有子文件夹 |
| `hashfile` | - | 文件哈希计算（md5/sha256/sha1，支持目录批量） |
| `checksum` | - | 校验和计算器（Qt：字符串/文件 + MD5/SHA1/SHA256/SHA384/SHA512/Blake2b/Blake2s + 期望值比较，设置持久化到 `~/.fcmd/ui/checksum.json`） |
| `pathtool` | - | 路径处理（show/rel/norm/diff） |
| `archivex` | - | 归档解压（zip/tar/gz/bz2，跨平台） |
| `zipencrypt` | - | ZIP AES-256 加密（pyzipper，批量；需 7-Zip/WinRAR 等支持 AES 的解压器），依赖 `fcmd[archive]` |
| `filecheck` | - | 文件完整性检查（本地磁盘/移动硬盘/NAS；0 字节/尾部零填充/断链/容器签名，视频剧集场景含缺失集数检测），依赖 `fcmd[filecheck]` |

### 文本与编码

| 工具 | 别名 | 说明 |
|------|------|------|
| `txttool` | - | 文本处理（case/count/sort/unique） |
| `asciitool` | - | ASCII 码表（char/code/table） |
| `codetool` | - | 编解码（Base64/URL/HTML/Hex/ROT13） |
| `casetool` | - | 命名风格转换（camel/snake/kebab/pascal） |
| `colortool` | - | 颜色换算（hex2rgb/rgb2hex/rgb2hsl/hsl2rgb），依赖 `fcmd[color]` |
| `convtool` | - | 单位换算（length/weight/temp/datasize） |
| `csvtool` | - | CSV 处理（show/select/to-json/from-json/merge） |
| `hashtool` | - | 字符串哈希（md5/sha1/sha256/sha512） |
| `idtool` | - | ID 生成（uuid/timestamp/random） |
| `jsontool` | `jsont` | JSON 处理（pretty/minify/query/sort） |
| `mathtool` | - | 数学计算（eval/sqrt/pow/factorial） |
| `padtool` | - | 文本对齐（left/right/center/justify） |
| `randtool` | - | 随机生成（password/number/string/bytes） |
| `regextool` | - | 正则表达式（match/find/replace/split） |
| `regexvalidate` | - | 正则校验（匹配/全匹配/findall/分组与正则特征分析，支持 ignorecase/multiline/dotall） |
| `regexvalidatew` | - | 正则校验器窗口版（Qt：实时校验、20 个典型示例、历史记录与语法帮助，设置持久化到 `~/.fcmd/ui/regexvalidate.json`） |
| `stattool` | - | 统计工具（mean/median/stddev/variance/summarize） |
| `textdiff` | - | 文本比较（行级 diff，统一格式输出） |
| `timetool` | - | 时间工具（now/parse/unix/fromunix/convert） |
| `calcdate` | - | 日期计算（add/workdays/diff/compare） |
| `urltool` | - | URL 解析（parse/query/addquery/baseurl） |
| `cryptool` | - | 文本对称加密（genkey/encrypt/decrypt，cryptography Fernet + PBKDF2-HMAC-SHA256），依赖 `fcmd[crypto]` |

### 数据格式

| 工具 | 别名 | 说明 |
|------|------|------|
| `xmltool` | - | XML 处理（pretty/extract/minify/validate） |
| `yamtool` | - | YAML 处理（pretty/get/keys/validate） |
| `inifile` | - | INI 处理（get/set/list/keys，基于 configparser） |
| `tomltool` | - | TOML 处理（get/keys/format/validate，基于标准库 tomllib） |

### PDF（可选依赖）

| 工具 | 别名 | 说明 |
|------|------|------|
| `img2pdf` | - | 图片合并为 PDF（文件/目录输入、A3/A4/A5/B5/letter/legal 页面、DPI 与 EXIF 方向校正，Pillow 随主包安装） |
| `img2pdfw` | - | 图片转 PDF 转换器窗口版（Qt，文件/目录批量、页面尺寸与 DPI 可调、进度与日志） |
| `pdfcrypt` | - | PDF 批量加密/解密（扫描目录、按加密状态筛选、并发处理；加密输出 `.enc.pdf`，解密输出 `.dec.pdf`），依赖 `fcmd[pdf]` |
| `pdfcryptw` | - | PDF 加密解密窗口版（Qt，目录与密码输入、递归与并发可调、进度与日志），依赖 `fcmd[pdf]` |
| `pdfsplit` | - | PDF 分割（按份数 / 每份页数 / 页码范围提取，支持 `1,3-5,7-` 语法），依赖 `fcmd[pdf]` |
| `pdfsplitw` | - | PDF 分割窗口版（Qt，三种模式、进度与日志），依赖 `fcmd[pdf]` |
| `pdftool` | - | PDF 处理（merge/split/compress/encrypt/decrypt/extract/ocr/attach/compare/layout 等 18 子命令），依赖 `fcmd[pdf]`，OCR 需 `fcmd[ocr]`；产出文件的子命令支持目录批处理（`--recursive` / `--skip-existing` / `--silent`） |

### 办公文档（可选依赖）

| 工具 | 别名 | 说明 |
|------|------|------|
| `docdiff` | - | 对比两个 doc/docx 文档（调用本机 Word 生成带修订标记的对比文档，标题、original/revised 模式与是否显示修订可设），依赖 Windows + Word + `fcmd[win32]` |
| `docdiffw` | - | 文档对比窗口版（Qt，旧/新文档与输出选择、标题/模式/显示修订设置、后台对比与日志，设置持久化），依赖 Windows + Word + `fcmd[win32]` |
| `pptmake` | - | PPT 模板生成（`presets`/`init`/`inspect`/`export`/`import`；十三种版式 + 四套风格 + 四种装饰，模板 JSON 导入导出，可渲染为 `.pptx` 或从 `.pptx` 反向导入主题与文字），导出依赖 `fcmd[ppt]` |
| `pptmakew` | - | PPT 模板设计器窗口版（Qt，页面列表与布局库、QPainter 实时预览与缩略图、六个语义色自定义、模板 JSON / PPTX 双向导入导出，设置持久化到 `~/.fcmd/ui/pptmakew.json`），导出依赖 `fcmd[ppt]` |

### 多媒体（可选依赖）

| 工具 | 别名 | 说明 |
|------|------|------|
| `img2ico` | - | 图像/SVG 转 ICO/ICNS（多尺寸、质量档位、目录批量），SVG 输入依赖 `fcmd[svg]` |
| `img2icow` | - | 图像转 ICO 转换器窗口版（Qt，单文件/目录批量、尺寸与质量可调），SVG 输入依赖 `fcmd[svg]` |
| `ncmconvertw` | - | NCM 音乐解锁转换器窗口版（Qt，批量添加/目录导入、后台逐文件解锁为 mp3/flac、进度与失败明细），依赖 `fcmd[crypto]` |
| `imagetool` | `imaget` | 图片处理（resize/crop/rotate/flip/convert/watermark/compress/info/exif/histogram/colors，Pillow 随主包安装） |
| `videoconv` | - | 视频格式转换（mp4/mkv/avi/mov/wmv/flv/webm/gif；编码器、比特率、帧率、缩放、CRF、预设与外部音轨可配，支持 `*`/`**` 通配符批量），依赖系统 ffmpeg |
| `videoconvw` | - | 视频转换器窗口版（Qt，文件/输出目录选择、格式与编码参数可视化、进度与日志实时回传、可中断），依赖系统 ffmpeg |

### 系统与进程

| 工具 | 别名 | 说明 |
|------|------|------|
| `alarmclock` | - | 定时提醒（Qt）（数字时钟 + `HH:MM:SS` 闹钟 + 分钟快捷延时 + 到点置顶闪烁提醒 + 每天重复，设置持久化到 `~/.fcmd/ui/alarmclock.json`） |
| `cmderw` | - | 双面板文件管理器窗口版（Qt，Total Commander 风格：顶部菜单栏按文件/选中/视图/工具/命令/配置/帮助分组、按选择数与面板状态动态启用；左右面板独立历史与多标签页（Ctrl+T/Ctrl+W/Ctrl+Tab，至少保留一个标签；支持锁定、关闭其他、重命名标签与选择记忆）、Ctrl+D 书签、Alt+Down 历史跳转、Alt+Left/Alt+Right 后退/前进，视图→同步浏览（Ctrl+Shift+S，一侧导航对侧按相同相对动作跟随）、视图→分支视图（Ctrl+B，递归平铺文件并显示相对路径，复制保留结构）、文件/右键「打开方式…」（最近程序持久化），F5 复制/F6 移动进入传输队列（逐项进度/按项取消/失败重试，可暂停恢复、任务内并行 1–8），Ctrl+Shift+C 目录比较（仅文件名/按大小/按时间（2 秒容差）/按内容，包含与排除掩码、仅比较文件）与预演式目录同步（左→右/右→左/双向镜像，总是覆盖/仅较新覆盖/跳过冲突，逐项勾选与全选/反选，复制入队、删除走回收站，执行后结果窗口），Ctrl+Q 内嵌快速查看（文本/图片/PE 与二进制，限读并提示截断；编码自动探测与手动切换、十六进制视图、图片缩放与适应窗口）、Ctrl+F8 只读目录树（跟随活动面板）、视图模式（Ctrl+F1 简要 / Ctrl+F2 详细 / Ctrl+Shift+F1 缩略图，每个标签独立记忆模式与列宽，详细列顺序固定、宽度可拖拽并持久化；缩略图异步加载 + Canvas 虚拟化 + 内存 LRU，PNG/GIF/PPM/JPEG/BMP/WebP/TIFF/ICO 统一由 Pillow 解码，非图片、超大或解码失败的文件显示类型徽标）、Ctrl+Enter 命令行栏（上下键历史与草稿恢复、Tab 补全首个路径/命令 token，删除类命令二次确认）、配置→颜色规则（按掩码/大小/类型/时间，持久化），F7 新建/F8 删除到回收站/Shift+F8 永久删除/F2 重命名/F3 查看，Num+/Num- 按掩码选择、Ctrl+S 快速过滤、定义过滤器（包含/排除掩码持久化）、Ctrl+M 多重命名工具（名称/扩展名掩码与日期时间占位符、普通/正则查找替换、大小写、计数器排序与补零、可勾选预览并标出冲突/非法/超长/覆盖、预设保存加载删除并持久化，分支视图可用）、Space 统计大小、「查找重复文件…」（大小预筛 + 哈希确认，可删除或移动），Ctrl+H 显示隐藏文件（菜单勾选同步），Alt+F5 打包/Alt+F9 解压、压缩包浏览与写入（Enter 进入、Backspace 逐级退出，F3/快速查看临时解压，F5/F6 按包内结构导出；zip/tar 可新增/删除/改名并设置压缩级别 0–9 与 zip AES-256 密码，7z/rar 仍只读）、文件分割/合并（按大小或份数、`.sha256` 校验）、Ctrl+F 搜索（区分大小写/整词/按大小与时间范围过滤，结果落入可排序临时面板并可直接复制/移动/删除/加入选择；归档视图内搜索成员名与内容，流式读取并跳过加密/超大成员）、路径栏卷下拉（盘符/挂载点与剩余空间，点击切换）、哈希与属性（批量修改时间戳、Windows 只读/隐藏切换）、用户命令（配置→用户命令…增删改/上下移/试运行，`%P`/`%T`/`%N`/`%S`/`%L` 占位符展开，可挂菜单/工具栏/右键并持久化）、生成与校验校验文件（`.sha256`/`.sha1`/`.md5`/`.sfv`，逐项列 OK/FAIL 与原因）、两个文本文件并排只读比较（增删改着色、行号对齐、同步滚动）、配置→主题（内置调色板写入设置，重开生效，非法值回落默认）、启动参数 `--left`/`--right`/`--select`（相对路径与非法路径容错），复用 archivex / infras.hashing / infras.fs，设置持久化到 `~/.fcmd/ui/cmderw.json`） |
| `envw` | - | 环境变量编辑器窗口版（Qt，参考 Rapid Environment Editor：用户/系统/当前进程作用域、搜索、新建/编辑/删除/重命名、PATH 逐条路径编辑、变量引用展开预览、JSON 备份恢复，设置持久化到 `~/.fcmd/ui/envw.json`） |
| `fonttool` | - | 推荐字体按需安装与管理（list/status/install/remove）：默认 Cascadia Code + Noto Sans SC，下载后校验 SHA256 并保存 OFL 许可证，安装到当前用户字体目录，不内置进发行包 |
| `sysinfo` | - | 系统信息收集（Python/平台/内存/磁盘/CPU，psutil） |
| `portcheck` | - | 端口检查与扫描（socket 探测 + psutil 查询占用进程，跨平台） |
| `taskkill` | `taskk` | 按名称终止进程（psutil terminate → kill）；与系统命令同名，仅 `taskk` 生成全局脚本 |
| `which` | `wch` | 查找可执行命令路径；与系统命令同名，仅 `wch` 生成全局脚本 |
| `clr` | - | 跨平台清屏 |
| `screenshot` | - | 跨平台截图（Windows PowerShell / macOS screencapture / Linux gnome-screenshot） |
| `reseticoncache` | - | 重置 Windows 图标缓存（非 Windows 打印提示） |
| `reshack` | - | PE 资源查看与修改（list/extract/replace/export-all；Icon/Bitmap 导出自动补 ICO/BMP 头，替换前自动备份，依赖 `fcmd[pe]`） |
| `reshackw` | - | PE 资源编辑器窗口版（Qt：资源树 + 文本/十六进制/图像预览（图像支持缩放/适应窗口、Ctrl+滚轮及右键复制/保存）+ 导出（Icon/Bitmap 自动补 ICO/BMP 头）/替换/全部导出，设置持久化到 `~/.fcmd/ui/reshackw.json`，依赖 `fcmd[pe]`） |
| `peview` | - | PE 文件解析与安全编辑（stdlib，无第三方依赖）：info/headers/sections/directories/imports/exports/resources/relocations/debug/hex/check/fields；`edit` 默认预演，`--apply` 才写入并自动备份 `<文件名>.backup[N]`、写后重解析校验，结构性字段一律拒绝 |
| `pevieww` | - | PE 查看/编辑窗口版（Qt：分类树 + 详情 + 十六进制视图，字段编辑对话框常驻风险说明并要求确认，写入前自动备份、写后自校验，设置持久化到 `~/.fcmd/ui/pevieww.json`） |
| `filesync` | - | 文件夹比较与同步（参考 FreeFileSync）：`compare` 按时间与大小/仅大小/内容比较左右目录，`sync` 支持双向/镜像/更新三种模式；包含/排除通配符与跳过隐藏文件，`--preview` 预览，冲突项（时间相同但内容不同）自动跳过 |
| `filesyncw` | - | 文件夹比较与同步窗口版（Qt：左右目录选择、比较/同步模式与过滤、差异表与计划动作着色、后台比较与逐项同步进度、预演不写盘、冲突跳过、JSON 配置载入/保存，设置持久化到 `~/.fcmd/ui/filesyncw.json`） |
| `filescan` | - | 目录敏感内容扫描：关键词/正则规则与 and·or·not 表达式组合，后缀白名单（默认常见文本后缀，`--force-ext` 可强制扫描名单外后缀）、并行扫描与取消、按文件聚合命中并默认打码，支持文本/JSON/CSV 报告、规则集 JSON 导入导出、扫描配置复用（`scan --save-config` / `run --config`）与重复扫描缓存（`--no-cache` 关闭、`--clear-cache` 清理，报告区分实扫/复用）；统计区分「范围外」与「读取后跳过」，0 命中时给出白名单/规则诊断 |
| `filescanw` | - | 敏感内容扫描窗口版（Qt：目录列表与扫描选项、可编辑后缀白名单、扫描配置导入/导出、扫描缓存开关与清理、规则表与表达式编辑、后台进度/取消、命中树与规则/路径筛选、打码导出，设置持久化到 `~/.fcmd/ui/filescanw.json`） |
| `renamer` | - | 批量重命名（规则流水线）：`replace` 正则替换、`reorder` 用 `{0}/{1}/{name}` 重排捕获片段、`move` 把匹配文本搬到开头/结尾/锚点/指定位置，另有 `insert`/`case`/`sequence`；规则用 JSON 或 `@文件` 传入，`preview` 预览、`apply` 两阶段执行（支持互换与仅大小写改名），预览标记未变化/非法/冲突 |
| `renamerw` | - | 批量重命名窗口版（Qt：目标文件/目录列表、规则表增删改与上下移、正则/重排/搬移/插入/大小写/计数器表单、旧名→新名预览与冲突着色、两阶段执行与本次撤销、规则 JSON 导入导出；「帮助 → 使用帮助」内置规则说明与五组典型示例，可一键载入，详见 [src/fcmd/apps/renamer/help/renamerw.md](src/fcmd/apps/renamer/help/renamerw.md)，设置持久化到 `~/.fcmd/ui/renamerw.json`） |
| `dynacalc` | - | 装药密度/尺寸/工艺缺陷 → 爆轰传递的参数化算例（不做物理求解）：YAML 参数矩阵（主矩阵 48 组 + 五项补充组 + 典型工况 01/02/03）与 `dynacalc-model/1` 模型映射（几何/拉拔/密度场/间隙/脱粘），按 LS-DYNA 关键字模板生成可复现 deck，调度求解（`--plan-only`、假求解器、并发/超时/重试/续跑），回收 glstat/matsum/history 输出 CSV/JSON/文本汇总与两工况对比，四项工程门控（阈值可配，属工程建议值）；一键演示 `fcmd dynacalc demo`（示例参数 → 生成 → 假求解 → 汇总 → 对比 → 门控，可接 paramcad 模型与 paramesh 网格，最短命令见 [docs/cli.md](docs/cli.md)）；不含任何真实配方或材料参数，未填项一律 TBD，详见 [docs/adr/0020-dynacalc.md](docs/adr/0020-dynacalc.md) 与 [docs/dynacalc-model-contract.md](docs/dynacalc-model-contract.md) |
| `dynacalcw` | - | 参数化算例窗口版（Qt：参数/模型/输出/模板/求解器选择，工况清单预览，生成 deck、dry-run、执行、作业状态、汇总与门控，设置持久化到 `~/.fcmd/ui/dynacalcw.json`） |
| `iptool` | - | IP 地址工具（parse/subnet/validate） |
| `nettool` | - | HTTP 客户端（GET/POST/HEAD，状态码/响应头） |
| `urlcheck` | - | URL 可访问性与访问速度检测（HEAD/GET 探测 + 并发测速排序，供镜像源选优） |
| `websave` | - | 网页内容保存（主页及同源相关子页面，过滤广告/静态资源） |
| `lscalc` | - | LS-DYNA 计算（run/mpi/status） |
| `dockercmd` | - | Docker 操作（login/push/pull，镜像仓库通用） |

### LLM 与模型

| 工具 | 别名 | 说明 |
|------|------|------|
| `llmbench` | - | OpenAI 兼容服务吞吐实测独立命令（`--name 服务名` 读本机服务状态，或 `--base-url` 直连任意兼容服务；`--prompt-tokens N` 模拟长上下文） |
| `llmchat` | - | OpenAI 兼容服务多轮流式对话独立命令（走 `/v1/chat/completions` SSE 并维护 `messages` 历史，兼容思考型模型的 `reasoning_content` 流；`--name` 读本机服务状态或 `--base-url` 直连；`--prompt` 单次提问，省略时进入交互模式，`/clear` 清空历史、`/exit` 退出） |
| `llmconvert` | - | HF safetensors 转 GGUF 独立命令（复用引擎目录内 `llama.cpp*.zip` 或在线源码，可选 `--quant` 再量化、`--tag` 指定源码版本） |
| `llmdoctor` | - | 运行环境体检独立命令（GPU / CUDA / 引擎 / 模型 / 端口 / 磁盘 / VC++ 运行库） |
| `llmdown` | - | 大模型仓库下载（`search`/`info`/`local`/`files`/`mirror`/`download`；ModelScope 搜索与详情、本地模型扫描，ModelScope 优先 + hf-mirror 兜底，两源同名分片实测吞吐自动选优，rich 展示进度/速度/剩余时间，支持 Range 断点续传、已存在跳过与 sha256 校验；已量化仓库可按 `--quant UD-Q4_K_XL` / `--file` 精确下载分片与 mmproj，`--all-quants` 保留整仓下载，默认多量化仓库要求显式选择） |
| `llmengine` | - | llama.cpp 引擎管理独立命令（`list`/`install`/`use`/`check`/`inspect`；安装或注册引擎、切换默认引擎、验证可运行、检查 zip 离线包类型/内容/CRC 完整性；`install --zips` 可一次解压引擎包 + cudart 运行库包；窗口内入口在 `llmw` 设置页的「引擎」分页） |
| `llmenv` | - | 路径与配置独立命令（`show`/`add`/`remove`/`reset`/`list`/`env`；打印 `FCMD_HOME` 目录布局、设置单一模型目录、列出服务状态、输出 `OPENAI_*` 环境变量；`FCMD_SERVE_DIR` / `FCMD_CONFIG` / `FCMD_ENGINES` 可分别覆盖服务目录、配置文件与引擎根目录；模型 / 引擎 / 服务目录统一由 `config.json` 落盘，转换产物默认写入模型目录下的 `converted` 子目录，旧多值配置与旧工作目录配置在读取时迁移或提示） |
| `llmmodel` | - | 模型扫描独立命令（`list`/`find`/`vram`；列出 GGUF、按量化定位文件、估算显存占用） |
| `llmqnt` | - | GGUF 量化（调用 `llama-quantize` 批量生成 Q2_K…Q8_0 与 IQ1_S…IQ4_XS 共 25 种量化版本；默认 Q4_K_M/Q5_K_M，输出到模型目录下的 `converted` 子目录，自动去掉输入文件 `-F16` 后缀，已存在输出默认跳过，`--overwrite` 强制重跑，`--out-dir` 指定输出目录，`--quantize-exe` / `FCMD_LLAMA_QUANTIZE` / 已装引擎 / PATH 依次解析量化器） |
| `llmserve` | - | 服务生命周期独立命令（`plan`/`start`/`stop`/`status`/`logs`；打印启动计划与显存估算、拉起引擎等待 `/health`、停止进程树、查看状态与日志；OpenAI 模型别名默认取 GGUF 文件名，可用 `--alias` 覆盖；`--tune auto/default/throughput/long-context/low-vram` 按实测预设调整 batch、并发槽、HTTP 线程与 KV 类型；`llmdown` 下载已量化模型时可用 `--quant UD-Q4_K_XL` / `--file` 精确选择，`--all-quants` 保留整仓下载） |
| `llmw` | `locllmw` | LLM（原 LocLLM）：本地 LLM 服务管理窗口（Qt；3.11+ PySide6 / 3.10 PySide2，侧边栏分页：概览 / 模型 / 运行 / 对话 / 实验室 / 设置；与 CLI 共用同一份配置与窗口设置）：概览页展示 CPU / GPU / 内存（首次检测写入配置，标题旁可重新检测），系统信息右侧实时显示生成 tok/s 与服务请求指标，下方用 GPU / CPU / 内存三块分段格图（格图 + 占用率，按余量分档配色：充足绿 / 偏紧琥珀 / 告急红）实时显示系统占用，三块标题后以小字提示显存 / 功耗、频率 / 服务 CPU、已用 / 交换信息，主格图 + 占用率保持紧凑，按硬件推荐模型与启动配置（性能来源用预测 / 实测胶囊标签，性能测试后优先用实测值；每条按显存余量 / 上下文 / KV / 解码速度打分，左侧圆形评分框与行边框按档位配色；推荐列表在卡片内独立滚动，页面高度不足时不叠加整页滚动条，启动服务按钮固定在列表卡片外）；运行页用模型下拉框列出 GGUF，识别精度并在选中后给出标签、接入地址、调优与显存适配建议；运行页顶部用模型下拉框选择 GGUF（下拉项仅对已启动模型显示绿色「已启动」，默认恢复上次运行模型，每个模型的启动参数分别持久化），右侧按钮随所选模型显示启动服务或停止服务；档位与调优整合为带说明和主题色的「运行模式」方块选项（速度红 / 均衡蓝 / 长上下文绿 / 超长上下文琥珀 / 省显存灰），KV 也提供带说明选项，切换后同步高级参数；服务名自动按模型 / 运行中服务推导，无需手填；配置监听地址 / 端口 / 线程与高级参数（上下文 / KV / ubatch / 别名 / 并发槽 / HTTP 线程 / API Key / 等待就绪）；启动参数卡片提供只读启动命令预览、自定义启动参数输入与「保存参数」/「填入当前参数」（填入内容与预览命令一致，省略可执行文件 / 模型 / mmproj，不会重复追加）；启停与重启操作集中在顶部模型卡片（重启按钮仅在服务运行时可用，按当前参数停止并重新启动），不再保留底部操作栏；对话页左侧为对话历史栏（顶部新对话、下方可切换历史，底部支持删除当前与清空全部），右侧顶部为服务选择、运行状态胶囊标签与状态说明，刷新按钮右侧用与刷新按钮等高的 🔗 / ⚙ 图标按钮展开连接参数 / 生成参数（折叠分区标题隐藏），左侧「＋ 新对话」与运行页「启动服务」同尺寸，支持流式回复、思考过程单独展示、Ctrl+Enter 发送与导出 Markdown；服务日志与性能测试都从顶部「帮助」菜单以非模态窗口打开：服务日志回放本次会话最近 1000 行，支持复制 / 清空 / 导出；性能测试支持短对话 / 长输出 / 代码补全 / 长上下文 8K / 32K / 并发 4 路，可选择被测服务、调重复次数 / 超时 / 输出上限并导出 CSV；设置页统一模型目录与引擎根目录（单一来源）、显示已安装引擎并可切换默认引擎；实验室页集中说明代理 / 画图 / 视频 / 音频规划。窗口位置与大小（客户区，不含窗口装饰）记忆到 `~/.fcmd/ui/llmw.json`，避免每次启动因标题栏 / 边框叠加而漂移；切到模型页的本地扫描 / 在线列表加载与概览页硬件检测都在状态栏右侧显示带已用时的进度条（多任务聚合、不遮挡左侧状态文字）；模型页含 ModelScope 在线浏览（顶部按适合本机 / 全部 / 对话 / 编程 / 看图 / 推理 / 视频 / 音频筛选，另有来源（在线 / 已下载 / 全部来源）、发布方过滤与同名折叠（选项写入 llmw.json，下次启动恢复）；浏览结果为自定义列表行（标题显示模型名与发布方胶囊，副标题显示系列 / 任务，副标题行显示系列 / 任务，发布时间紧接其后；已安装 / 部分下载 / 仅本地 / UD / 不可转换 / 不可量化 / 已转换与量化精度胶囊同排展示（量化标签最多 4 个，其余折叠为 +N）；上一页左侧提供加宽的 250ms 防抖名称过滤框，后接按下载量 / 发布时间 / 更新时间 / 星标 / 综合评分排序下拉，默认按下载量倒排；在线列表带 10 分钟内存 + 磁盘缓存（陈旧先回显再后台刷新），筛选条右侧「更新模型」可绕过缓存强制刷新并重扫本地，状态栏常驻显示最近更新时间，其右侧「导入模型」支持导入 GGUF 文件或模型文件夹（文件夹会识别 tensors / GGUF / diffusers / unknown 类型并给出导入或导入并转换选项）；未下载完的模型按钮显示「继续下载」并标出已下载进度；已安装模型的下载按钮保留绿色样式并改为「下载其他量化」，可继续选择 UD 等其它量化下载（本地一级目录无法确定远程仓库时名称行显示「仅本地」胶囊），已安装行提供删除按钮，「模型管理」对话框列出全部本地模型（完整 / 未完成 .part），支持多选移除、清理未完成下载、空目录与模型列表缓存，已安装且匹配用途分类的置顶并按评分排序，下载前可按量化选择（含 `UD-Q4_K_XL` 等动态量化）只拉取对应分片与 mmproj，可转换模型显示「转换」按钮，已转换模型在名称行显示「已转换」胶囊，可量化模型提供「量化」并按名称下方标签显示已有精度（量化菜单对已量化精度打勾置灰并标注「已量化」，不可重复选用），有可用量化 GGUF 的仓库额外提供「启动服务」快捷按钮（默认选上次启动过的量化精度并在精度标签注明「· 上次启动」，多精度可弹出菜单切换后直接启动）），展开行就地查看详情元数据，行内「下载」/「转换」/「量化」按钮在底部右侧显示进度；下载源 / 并发 / 校验 / 掩码与转换精度 / 解释器 / 脚本 / 覆盖在设置页维护并自动保存）、行内转换与量化（也可用 `fcmd llmdown` / `fcmd llmconvert`） |

**启动排错**：若模型启动失败且引擎日志出现 `unknown model architecture: 'xxx'`，说明当前 llama.cpp
引擎版本过旧、不认识该模型架构（例如 Qwen3-MoE 的 `qwen3moe`），换启动参数无效；请用
`fcmd llmengine install <新 tag>` 安装新版引擎后 `fcmd llmengine use <tag>`。引擎日志位于
`${FCMD_HOME:-~/.fcmd}/serve/logs/<服务名>.log`；llmw 的启动失败弹窗会直接给出升级命令与日志路径，
而不是只贴一段日志尾部。

### 开发环境

| 工具 | 别名 | 说明 |
|------|------|------|
| `gittool` | `gitt` | Git 操作（add+commit/init/init-submodules/clean/push/pull） |
| `devman` | - | Windows 开发环境管理（首批 node / python）：`list`/`versions`/`install`/`uninstall`/`use`/`upgrade`/`init`/`uninit`/`env`/`exec`/`doctor`；装进独立根目录、多版本共存、切换只改活动版本文件；`install`/`init` 默认把 shim 目录写入 HKCU 用户 PATH（`--no-path` 关闭，遮蔽已有 node/python 需 `--allow-shadow`），`uninit` / 卸掉最后一个版本按记录精确回滚 PATH 条目，不碰既有 node/python/conda/venv，改动均支持 `--dry-run` 与 `--yes`，详见 [docs/adr/0024-devman.md](docs/adr/0024-devman.md) |
| `envdev` | - | 开发环境一键配置（分组入口 lang/app/mirror/check/all，镜像按服务（pip/conda/rustup/cargo/go/maven/npm）独立自动选优：以真实构件实测下载吞吐排序并可识别限速/门户页，避免误选「延迟低但被限速」的镜像；另含教育网镜像探测（动态拉取 MirrorZ 列表）与 `mirror --service <服务>` 测速、`--apply` 自动应用，以及工具链安装，Linux 含系统依赖） |
| `mirrortool` | - | 镜像源独立入口（薄壳，实现与 `envdev mirror` 共用）：教育网镜像站点探测；`--service <服务>` 对 pip/conda/rustup/cargo/go/maven/npm 实测下载吞吐排序并推荐最快镜像，`--apply` 立即写入对应服务配置 |
| `mirrortoolw` | - | 镜像搜索与配置窗口版（Qt）：服务镜像测速排序与一键应用、MirrorZ 教育网镜像站点探测二合一；结果按镜像名/地址实时过滤，展示延迟与实测吞吐并高亮推荐项，配置位置随服务切换提示，设置持久化到 `~/.fcmd/ui/mirrortoolw.json` |
| `setenv` | - | 设置当前进程环境变量（支持 `--default` 仅在未设置时写入） |
| `sshcopyid` | - | SSH 公钥部署到远程服务器（依赖 sshpass） |

查看任意工具的子命令与字段详情：

```bash
fcmd info <tool>                 # 列出工具所有子命令
fcmd info <tool> <subcommand>    # 展示 ToolSpec 完整字段
```

## 任务定义 API

### `@fx.task` —— Python 函数即任务

函数参数名即依赖名，fcmd 据此自动构建 DAG：

```python
import fcmd as fx


@fx.task
def fetch() -> str:
    return "raw data"


@fx.task
def parse(fetch: str) -> list[str]:
    return fetch.split()


@fx.task
def save(parse: list[str]) -> None:
    # parse 自动依赖 fetch，save 自动依赖 parse
    ...


report = fx.run(fx.graph(fetch, parse, save))
```

### `@fx.tool` —— 函数即 CLI 子命令

```python
import fcmd
from fcmd.infras.command import run_command


@fcmd.tool("gittool", subcommand="a", help="git add + commit")
def add_commit(message: str, amend: bool = False) -> None:
    """添加并提交。"""
    cmd = ["git", "add", "-A"]
    run_command(cmd, check=True)
    cmd = ["git", "commit", "-m", message]
    if amend:
        cmd.append("--amend")
    run_command(cmd, check=True)
```

CLI 参数映射规则：

- 无默认值参数 → 位置参数（`message` → `message`）
- 有默认值参数 → `--name` 选项（`amend: bool = False` → `--amend`）
- `bool = False` → `--name`（store_true）
- `bool = True` → `--no-name`（store_false，保留原参数名）
- `X | None` / `Optional[X]` → 自动解包为 `X`

### `cmd` / `aggregate` 任务

无需 Python 函数，直接用命令或聚合依赖：

```python
@fcmd.tool("mytool", subcommand="build", cmd=["uv", "build"], help="构建")
def _build() -> None: ...  # 函数体可空，cmd 优先执行


@fcmd.tool("mytool", subcommand="all", needs=["build", "test"], strategy="dependency")
def _all() -> None: ...  # aggregate：仅声明依赖
```

## YAML 编排

GitHub Actions 风格的 `jobs`/`needs`/`matrix`/`if`：

```yaml
# deploy.yaml
strategy: thread              # 图级默认策略
defaults:
  retry: {max_attempts: 3}
  timeout: 300
  env: {CI: "true"}

jobs:
  setup:
    cmd: ["git", "clone"]

  build:
    needs: [setup]
    cmd: ["uv", "build"]
    timeout: 120

  deploy:
    needs: [build]
    run: "twine upload dist/*"
    env: {TWINE_TOKEN: "..."}
    continue-on-error: true

  notify:
    needs: [build]
    if: "failure()"           # 仅 build 失败时执行
    cmd: ["python", "-m", "notify"]

  test:
    matrix:                    # 笛卡尔积展开为 4 个任务
      py: ["3.9", "3.10"]
      os: ["linux", "windows"]
    cmd: ["pytest"]
```

执行：

```bash
fcmd yaml deploy.yaml                  # 执行全部 jobs
fcmd yaml deploy.yaml build            # 仅执行 build 及其依赖
fcmd yaml deploy.yaml --dry-run        # 打印执行计划不执行
fcmd yaml deploy.yaml --strategy thread  # 覆盖执行策略
```

或 Python API：

```python
from fcmd.core.orchestration.yaml_loader import load_yaml
from fcmd.core.execution.executors import run

graph = load_yaml("deploy.yaml")
report = run(graph, strategy="dependency")
```

## 工具定义（Python）

工具统一用普通 Python 模块 + `@fcmd.tool` 装饰器定义：函数签名即 CLI 参数
（有默认值 → 选项，无默认值 → 位置参数；`list[str]` → 多值；`pathlib.Path` →
路径；`Literal[...]` → choices），函数体即逻辑。纯计算与基础操作放
`fcmd/infras/`，工具模块只做参数适配与输出。

```python
import fcmd as fx
from pathlib import Path


@fx.tool("hello", subcommand=None, help="向某人问好", param_help={"name": "目标名字", "count": "次数"})
def hello(name: str, count: int = 1) -> None:
    print(f"你好 {name} x{count}")


@fx.tool("deploy", subcommand="build", help="构建", cmd=["make", "build"], cwd=Path("."))
def build() -> None:
    """cmd 任务：函数体不执行，签名仅驱动 CLI。"""
```

`@fcmd.tool` 支持 `cmd`（str/list，`{参数名}` 插值）/ `needs`（DAG 依赖）/
`strategy` / `cwd` / `env` / `timeout` / `passthrough` / `hidden` /
`allow_upstream_skip` / `param_help`；模块级 `__tool_aliases__` 声明别名。
平台差异在模块导入期按 `sys.platform` 选择命令；需要特殊语义（环境变量回退、
输出管道、交互式透传）时直接写成普通函数。

## 执行策略

四种策略通过 `fx.run(graph, strategy=...)` 或 `fcmd yaml --strategy ...` 指定：

| 策略 | 说明 |
|------|------|
| `sequential` | 拓扑序串行执行，简单可预测 |
| `thread` | 同层任务用线程池并行（适合 I/O 密集） |
| `async` | 同层任务用 asyncio 并发（适合异步 I/O） |
| `dependency`（默认） | 仅按依赖关系调度，同层并行，兼顾效率与正确性 |

## 开发

```bash
git clone <repo-url>
cd fcmd
pip install -e ".[dev]"         # 安装开发依赖（lint + test + full + prek + tox）

fcmd pymake tc                # 类型检查（pyrefly + ruff）
fcmd pymake t                 # 运行测试
fcmd pymake cov               # 测试 + 覆盖率
fcmd pymake chk               # 类型检查聚合（清理 + pyrefly + lint + fmt + test_fast）
```

工具链独立配置文件：`ruff.toml` / `pyrefly.toml` / `pytest.ini` / `.coveragerc` / `.bumpversion.toml` / `uv.toml` / `.pre-commit-config.yaml`。`pyproject.toml` 仅含项目元数据。

### 目录结构

```
src/fcmd/
  core/          # 运行时内核：DAG、报告、执行引擎、工具注册、编排
    toolkit/     #   函数签名 → argparse 的 CLI 工具基座
    execution/   #   sequential/thread/async/dependency 四种执行器
    orchestration/  # YAML 编排与 if 条件求值
  infras/        # 基础件：编解码/换算/哈希/加解密/文本/数据/文件/系统/网络/文件过滤/命令/版本等纯函数
  cli/           # 仅入口、工具发现、内建命令与诊断辅助
    _builtins/   #   graph/info/completion/yaml/env/doctor/profiler
    _entry.py    #   console script 通用入口（按 argv[0] 推断工具名）
    _manifest.py #   静态工具清单读取（快路径：只加载目标工具）
    _manifest_data.py  # 生成文件，勿手改
  gui/           # 展示基座（不参与工具发现）：Qt 窗口骨架 / 主题 / UI 设置持久化 / 几何纯函数
    qt/          #   Qt（PySide6 / 3.10 PySide2）基座：主题 QSS / 侧边栏 / 窗口骨架 / 后台任务 / 控件
  apps/          # 独立桌面应用（自动发现；如 cmder、filesync、llm）
  tools/         # 业务实现（自动发现）
    archive/ calc/ crypto/ data/ dev/ media/ net/ office/ pdf/ system/ text/
  console.py     # rich 控制台薄封装
```

新增 Python 工具：在 `src/fcmd/tools/<领域>/` 下新建模块（非 `_` 前缀、非 `main`），用 `@fcmd.tool` 装饰器注册即可，无需修改 `main.py`。可选在模块内定义 `__tool_aliases__: list[str]` 声明别名。目录按功能域划分；图形界面版命名为 `<工具名>w.py` 并放在其核心逻辑所在域（如 `media/img2icow.py`）。相互依赖、共享模型与运行时的本地 LLM 工具族统一放 `src/fcmd/apps/llm/`（如 `llm/llmconvert.py`）。独立桌面应用（如 `cmder`，命令 `cmderw`）放 `src/fcmd/apps/<应用>/`（一包一应用，入口 `app.py`），与 `tools` 共用工具发现与静态清单。

新增 / 删除 / 改名工具、调整别名或修改工具描述后，重新生成静态工具清单（`fcmd <tool>` 快路径据此只加载目标工具，`fcmd` 列表快路径据此直接取名字 / 别名 / 描述而不导入工具模块；清单缺失或过期会自动回退全量发现，功能不受影响，只影响启动速度）：

```bash
uv run python -m fcmd.cli._manifest_data
```

`tests/cli/test_tools_manifest.py` 会校验清单与实跑全量发现严格一致（含列表描述与源码内容指纹）。清单的内容指纹必须在**干净工作区**生成：若生成时存在其他未提交的工具源码改动，提交后指纹会与仓库内容不符，列表会回退全量发现。

公共 API（`import fcmd as fx`）名称保持稳定；原 `fcmd.apis.*` / `fcmd.engine.*` / `fcmd.models.*` / `fcmd.orchestration.*` 深路径已收敛：运行时内核在 `fcmd.core.*`，通用基础件在 `fcmd.infras.*`，不保留兼容 shim。

`fcmd.infras.*` 是**半公开**层：模块 `__all__` 列出的名字可被外部导入复用，但**不承诺跨版本稳定**，可能随版本调整、改名或迁移；稳定承诺仅限上段 `import fcmd as fx` 暴露的公共 API。

## 许可证

MIT
