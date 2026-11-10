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
| `img` | `pip install fcmd[img]` | `imagetool` / `img2pdf` / `img2pdfw`（Pillow） |
| `pdf` | `pip install fcmd[pdf]` | `pdftool` / `pdfcrypt` / `pdfsplit`（PyMuPDF + pypdf） |
| `ocr` | `pip install fcmd[ocr]` | `pdftool ocr`（pytesseract，需系统 tesseract） |
| `svg` | `pip install fcmd[svg]` | `img2ico` 的 SVG 输入（resvg-py） |
| `crypto` | `pip install fcmd[crypto]` | `cryptool`（cryptography Fernet） |
| `archive` | `pip install fcmd[archive]` | `zipencrypt`（pyzipper AES-256 ZIP） |
| `filecheck` | `pip install fcmd[filecheck]` | `filecheck` 的容器类型识别（puremagic） |
| `color` | `pip install fcmd[color]` | `colortool` 的颜色换算（coloraide） |
| `pe` | `pip install fcmd[pe]` | `reshack` / `reshackw` 的 PE 资源解析与写回（pefile） |
| `office` | `pip install fcmd[office]` | img + pdf + ocr + svg 全家桶 |
| `win32` | `pip install fcmd[win32]` | `docdiff` / `docdiffw` 的 Word 自动化（pywin32，仅 Windows） |
| `full` | `pip install fcmd[full]` | office + win32 + crypto + archive + filecheck + color + pe |
| `lint` | `pip install fcmd[lint]` | 开发用：ruff + pyrefly |
| `test` | `pip install fcmd[test]` | 开发用：pytest + cov + xdist |
| `dev` | `pip install fcmd[dev]` | lint + test + full + prek + tox |

要求 Python ≥ 3.13。

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

97 个工具（其中 48 个为 Python 模块工具——`alarmclock`/`archivex`/`bumpversion`/`checksum`/`docdiff`/`docdiffw`/`envdev`/`envw`/`filecheck`/`gittool`/`img2icow`/`img2pdf`/`img2pdfw`/`lscalc`/`llmbench`/`llmchat`/`llmchatw`/`llmconvert`/`llmconvertw`/`llmdoctor`/`llmdown`/`llmengine`/`llmenginew`/`llmenv`/`llmmodel`/`llmqnt`/`llmqntw`/`llmserve`/`llmservew`/`mirrortool`/`ncmconvertw`/`packtool`/`pdfcrypt`/`pdfcryptw`/`pdfsplit`/`pdfsplitw`/`pdftool`/`portcheck`/`pymake`/`pyprof`/`regexvalidate`/`regexvalidatew`/`reshack`/`reshackw`/`screenshot`/`videoconv`/`videoconvw`/`zipencrypt`；其余 49 个为 DSL 声明式工具——纯 exec 与 compute 工具（`clr`/`autofmt`/`dockercmd`/`sshcopyid`/`reseticoncache`/`piptool`）及 action 原语工具（`setenv`/`writefile`/`filedate`/`filerename`/`filelevel`/`folderback`/`taskkill`/`which`/`sysinfo`）由 DSL 声明），按用途分组：

工具名（如 `imagetool`）为官方入口并生成同名全局脚本；短名（如 `imaget`）是显式别名，同样保留全局脚本。例外：`taskkill` / `which` 与系统命令同名，不生成同名全局脚本（避免遮蔽系统命令），只保留短名 `taskk` / `wch` 脚本；规范名仍可通过 `fcmd taskkill` / `fcmd which` 使用。仓库不做 `tool`/`cmd` 后缀自动推断，拼错的命令会直接报「未找到工具」。

### GUI 主题

所有 Tk 窗口共用一套可切换主题，内置 `dark-blue`（深色科技蓝，默认）、`light`、`high-contrast`、`native`（接近原生）。每个窗口菜单栏的「主题」菜单可直接选择，选择会写入 `${FCMD_HOME:-~/.fcmd}/ui/theme.json`，**重新打开窗口后生效**。也可手动编辑该文件：

```json
{ "theme": "light" }
```

删除该文件、写入非法主题名或 JSON 损坏时，窗口启动会自动回退到 `dark-blue`，不会影响工具本身运行。

`native` 沿用系统 ttk 主题（Windows 为 `vista`），系统原生按钮不接受自定义背景色，因此「强调 / 危险」按钮改用系统按钮底色 + 蓝色 / 红色文字区分语义，避免出现浅底白字；其余主题仍是填充式按钮。

窗口设置统一以原子方式写入（临时文件 + 替换），并在每次有效写入前保留一份同名 `.bak` 备份。主文件缺失、损坏或顶层不是对象时，读取会自动回退到 `.bak`。

#### WSL / Linux 中文乱码

窗口里的中文显示为方块（乱码）时，说明当前 Python 自带的 Tk 看不到可用中文字体：uv 安装的独立构建 Python（python-build-standalone）自带 Tk 未启用 Xft，只能枚举 X11 核心字体，fontconfig 中的 Noto CJK 等 TrueType 字体对它不可见。为这种 Python 补一套 X11 核心中文字体即可：

```bash
sudo apt install xfonts-wqy   # X11 核心中文字体（文泉驿位图）
xset fp rehash                # 让当前 X 会话重新加载字体，然后重开窗口
```

若用发行版自带的 Python（其 `python3-tk` 启用 Xft），安装 `fonts-noto-cjk` 即可正常显示中文，无需位图字体。

窗口启动时会自检主题正文字体能否渲染中文，缺字时在终端输出上述修复提示。字体候选顺序为微软雅黑 → 苹方 → Noto / 思源黑体 → 文泉驿，日志等宽控件在无 Xft 时回退到文泉驿位图字体，保证中文可读。

### 项目构建与发布

| 工具 | 别名 | 说明 |
|------|------|------|
| `pymake` | `pm` | 项目构建/测试/检查/发布全流程（b/t/tc/cov/bump/all 等） |
| `autofmt` | - | 代码格式化与检查（封装 ruff format/check） |
| `bumpversion` | - | 版本号自动管理（patch/minor/major + git tag） |
| `packtool` | - | Python 打包（源码/依赖/wheel/嵌入式 Python/zip/清理） |
| `pyprof` | - | Python 代码性能分析（`run`/`module`/`load`；cProfile 采集 + 单文件 HTML 报告：热点排行、调用关系、火焰图、模块聚合） |
| `piptool` | `pipt` | pip 包管理（安装/卸载/重装/下载/升级/冻结，DSL 声明 + compute 通配符展开） |

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
| `checksum` | - | 校验和计算器（Tk GUI：字符串/文件 + MD5/SHA1/SHA256/SHA384/SHA512/Blake2b/Blake2s + 期望值比较，设置持久化到 `~/.fcmd/ui/checksum.json`） |
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
| `regexvalidatew` | - | 正则校验器窗口版（Tk：实时校验、20 个典型示例、历史记录与语法帮助，设置持久化到 `~/.fcmd/ui/regexvalidate.json`） |
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
| `img2pdf` | - | 图片合并为 PDF（文件/目录输入、A3/A4/A5/B5/letter/legal 页面、DPI 与 EXIF 方向校正），依赖 `fcmd[img]` |
| `img2pdfw` | - | 图片转 PDF 转换器窗口版（Tk，文件/目录批量、页面尺寸与 DPI 可调、进度与日志），依赖 `fcmd[img]` |
| `pdfcrypt` | - | PDF 批量加密/解密（扫描目录、按加密状态筛选、并发处理；加密输出 `.enc.pdf`，解密输出 `.dec.pdf`），依赖 `fcmd[pdf]` |
| `pdfcryptw` | - | PDF 加密解密窗口版（Tk，目录与密码输入、递归与并发可调、进度与日志），依赖 `fcmd[pdf]` |
| `pdfsplit` | - | PDF 分割（按份数 / 每份页数 / 页码范围提取，支持 `1,3-5,7-` 语法），依赖 `fcmd[pdf]` |
| `pdfsplitw` | - | PDF 分割窗口版（Tk，三种模式、进度与日志），依赖 `fcmd[pdf]` |
| `pdftool` | - | PDF 处理（merge/split/compress/encrypt/decrypt/extract/ocr/attach/compare/layout 等 18 子命令），依赖 `fcmd[pdf]`，OCR 需 `fcmd[ocr]`；产出文件的子命令支持目录批处理（`--recursive` / `--skip-existing` / `--silent`） |

### 办公文档（可选依赖）

| 工具 | 别名 | 说明 |
|------|------|------|
| `docdiff` | - | 对比两个 doc/docx 文档（调用本机 Word 生成带修订标记的对比文档，标题、original/revised 模式与是否显示修订可设），依赖 Windows + Word + `fcmd[win32]` |
| `docdiffw` | - | 文档对比窗口版（Tk，旧/新文档与输出选择、标题/模式/显示修订设置、后台对比与日志，设置持久化），依赖 Windows + Word + `fcmd[win32]` |

### 多媒体（可选依赖）

| 工具 | 别名 | 说明 |
|------|------|------|
| `img2ico` | - | 图像/SVG 转 ICO/ICNS（多尺寸、质量档位、目录批量），依赖 `fcmd[img,svg]` |
| `img2icow` | - | 图像转 ICO 转换器窗口版（Tk，单文件/目录批量、尺寸与质量可调），依赖 `fcmd[img,svg]` |
| `ncmconvertw` | - | NCM 音乐解锁转换器窗口版（Tk，批量添加/目录导入、后台逐文件解锁为 mp3/flac、进度与失败明细），依赖 `fcmd[crypto]` |
| `imagetool` | `imaget` | 图片处理（resize/crop/rotate/flip/convert/watermark/compress/info/exif/histogram/colors），依赖 `fcmd[img]` |
| `videoconv` | - | 视频格式转换（mp4/mkv/avi/mov/wmv/flv/webm/gif；编码器、比特率、帧率、缩放、CRF、预设与外部音轨可配，支持 `*`/`**` 通配符批量），依赖系统 ffmpeg |
| `videoconvw` | - | 视频转换器窗口版（Tk，文件/输出目录选择、格式与编码参数可视化、进度与日志实时回传、可中断），依赖系统 ffmpeg |

### 系统与进程

| 工具 | 别名 | 说明 |
|------|------|------|
| `alarmclock` | - | Tk 定时提醒（数字时钟 + `HH:MM:SS` 闹钟 + 分钟快捷延时 + 到点置顶闪烁提醒 + 每天重复，设置持久化到 `~/.fcmd/ui/alarmclock.json`） |
| `envw` | - | 环境变量编辑器窗口版（Tk，参考 Rapid Environment Editor：用户/系统/当前进程作用域、搜索、新建/编辑/删除/重命名、PATH 逐条路径编辑、变量引用展开预览、JSON 备份恢复，设置持久化到 `~/.fcmd/ui/envw.json`） |
| `sysinfo` | - | 系统信息收集（Python/平台/内存/磁盘/CPU，psutil） |
| `portcheck` | - | 端口检查与扫描（socket 探测 + psutil 查询占用进程，跨平台） |
| `taskkill` | `taskk` | 按名称终止进程（psutil terminate → kill）；与系统命令同名，仅 `taskk` 生成全局脚本 |
| `which` | `wch` | 查找可执行命令路径；与系统命令同名，仅 `wch` 生成全局脚本 |
| `clr` | - | 跨平台清屏 |
| `screenshot` | - | 跨平台截图（Windows PowerShell / macOS screencapture / Linux gnome-screenshot） |
| `reseticoncache` | - | 重置 Windows 图标缓存（非 Windows 打印提示） |
| `reshack` | - | PE 资源查看与修改（list/extract/replace/export-all；Icon/Bitmap 导出自动补 ICO/BMP 头，替换前自动备份，依赖 `fcmd[pe]`） |
| `reshackw` | - | PE 资源编辑器窗口版（Tk：资源树 + 文本/十六进制/图像预览（图像支持缩放/适应窗口、Ctrl+滚轮及右键复制/保存）+ 导出（Icon/Bitmap 自动补 ICO/BMP 头）/替换/全部导出，设置持久化到 `~/.fcmd/ui/reshackw.json`，依赖 `fcmd[pe]`） |
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
| `llmchatw` | - | `llmchat` 的 Tk 窗口版（服务下拉框读取 `llmenv` 登记服务并回填地址 / 模型 / API Key，默认选中第一个进程存活的可用服务，无可用服务时状态栏显示“不可用”；也可填任意 OpenAI 兼容地址；流式回复在后台线程实时追加到聊天区，思考型模型的思考过程以灰色单独展示，支持停止生成、清空历史、系统提示词与 max_tokens / temperature / top_p 参数，max_tokens 默认 2048） |
| `llmconvert` | - | HF safetensors 转 GGUF 独立命令（复用引擎目录内 `llama.cpp*.zip` 或在线源码，可选 `--quant` 再量化、`--tag` 指定源码版本） |
| `llmconvertw` | - | `llmconvert` 的 Tk 窗口版（界面按「模型列表 / 转换配置 / 输出日志」分标签页，先选模型再配参数，避免表单堆叠；启动时后台扫描 `llmenv` 已配置的模型目录，识别并列出可转换的 HF safetensors 目录，选中即填入源目录；配置转换精度/解释器/源码脚本/引擎 tag；以勾选方式选择原始 GGUF 及多种量化格式，已生成的格式自动标注“已存在”并置为只读，除非勾选覆盖；可预览转换计划与命令，后台先转换一次基础 GGUF 再批量量化，转换脚本与量化器输出逐行实时回显，并解析 tqdm 百分比（含 `\r` 覆盖刷新）驱动整体进度条） |
| `llmdoctor` | - | 运行环境体检独立命令（GPU / CUDA / 引擎 / 模型 / 端口 / 磁盘 / VC++ 运行库） |
| `llmdown` | - | 大模型仓库下载（`files`/`mirror`/`download`；ModelScope 优先 + hf-mirror 兜底，两源同名分片实测吞吐自动选优，rich 展示进度/速度/剩余时间，支持 Range 断点续传、已存在跳过与 sha256 校验） |
| `llmengine` | - | llama.cpp 引擎管理独立命令（`list`/`install`/`use`/`check`/`inspect`；安装或注册引擎、切换默认引擎、验证可运行、检查 zip 离线包类型/内容/CRC 完整性；`install --zips` 可一次解压引擎包 + cudart 运行库包） |
| `llmenginew` | - | `llmengine` 的 Tk 窗口版（在线下载官方预编译包并显示实时下载进度；检查/安装本地 zip 离线包或已编译引擎目录，支持深度 CRC 校验；列出已安装引擎、切换默认引擎、后台执行 `llama-server --version` 体检） |
| `llmenv` | - | 路径与配置独立命令（`show`/`add`/`remove`/`reset`/`list`/`env`；打印 `FCMD_HOME` 目录布局、增删模型目录、列出服务状态、输出 `OPENAI_*` 环境变量） |
| `llmmodel` | - | 模型扫描独立命令（`list`/`find`/`vram`；列出 GGUF、按量化定位文件、估算显存占用） |
| `llmqnt` | - | GGUF 量化（调用 `llama-quantize` 批量生成 Q2_K…Q8_0 共 12 种量化版本；默认 Q4_K_M/Q5_K_M，自动去掉输入文件 `-F16` 后缀，已存在输出默认跳过，`--overwrite` 强制重跑，`--out-dir` 指定输出目录，`--quantize-exe` / `FCMD_LLAMA_QUANTIZE` / 已装引擎 / PATH 依次解析量化器） |
| `llmqntw` | - | `llmqnt` 的 Tk 窗口版（启动时扫描 `llmenv` 已配置的模型目录，仅把未量化的 F16/BF16 原始 GGUF 列入下拉框，已量化文件选择时告警；显示当前引擎 tag/路径、量化器路径与 llama.cpp 版本；量化类型勾选、进度条与实时日志，后台线程执行不阻塞界面） |
| `llmserve` | - | 服务生命周期独立命令（`plan`/`start`/`stop`/`status`/`logs`；打印启动计划与显存估算、拉起引擎等待 `/health`、停止进程树、查看状态与日志；OpenAI 模型别名默认取 GGUF 文件名，可用 `--alias` 覆盖） |
| `llmservew` | - | `llmserve` 的 Tk 窗口版（启动时后台扫描 `llmenv` 已配置的模型目录并列出可选 GGUF，按文件名内置能力标签（代码/推理/对话/视觉/数学/重排/嵌入，主流系列兜底为通用）并给出推荐档位与最佳实践提示；按整卡与当前可用显存双重评估标注模型适配情况：整卡放不下的标红加删除线为“硬件不足”，仅当前余量不足的标橙为“需释放显存”，运行中的模型标为“运行中”，选择后给出针对性建议（优先停止占用显存的服务 / 减小上下文 / KV 切 q8_0 / 更小量化），切换档位或显存释放后自动重算；显示引擎 tag/路径、版本与 GPU；表单配置档位/端口/上下文/KV/ubatch 等参数（服务名为可编辑下拉框，自动列出已登记服务并默认选中唯一运行中的服务；选择模型时服务名自动按模型文件名去掉量化后缀同步，选中服务名时自动定位其模型，运行中服务的模型始终并入模型列表并自动选中），可预览启动计划、后台启动与一键停止全部已登记服务（无需先选择服务名）、查看状态与日志、清空日志输出、一键在默认浏览器打开服务地址（优先使用运行中服务的实际地址，否则用表单配置的监听地址）；服务运行中每秒采样 `/metrics`，生成/提示 token/s 按 10s 滑动窗口平滑（参考 DeepSeek Harness 的累计平均口径），并展示处理中/排队请求与 KV 使用率；系统负载面板补充 CPU 频率、内存、交换、服务进程 CPU/内存/线程/运行时长，GPU 面板展示利用率、显存、服务显存、温度、功耗、风扇与核心频率；系统负载在窗口打开后即持续采样，服务未启动时同样可见） |

### 开发环境

| 工具 | 别名 | 说明 |
|------|------|------|
| `gittool` | `gitt` | Git 操作（add+commit/init/init-submodules/clean/push/pull） |
| `envdev` | - | 开发环境一键配置（分组入口 lang/app/mirror/check/all，镜像按服务（pip/conda/rustup/cargo/go/maven/npm）独立自动选优：以真实构件实测下载吞吐排序并可识别限速/门户页，避免误选「延迟低但被限速」的镜像；另含教育网镜像探测（动态拉取 MirrorZ 列表）与 `mirror --service <服务>` 测速、`--apply` 自动应用，以及工具链安装，Linux 含系统依赖） |
| `mirrortool` | - | 镜像源独立入口（薄壳，实现与 `envdev mirror` 共用）：教育网镜像站点探测；`--service <服务>` 对 pip/conda/rustup/cargo/go/maven/npm 实测下载吞吐排序并推荐最快镜像，`--apply` 立即写入对应服务配置 |
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
from fcmd.core.models import run_command


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

## 命令定义 DSL

exec 型简单命令无需编写 Python 模块，用 TOML 声明即可注册为工具。两种形态：**单命令**（`[commands.<name>]`，如内置的 `clr`）与**多子命令**（`[commands.<tool>.<sub>]`，如内置的 `pymake`/`gittool`/`autofmt` 纯 exec 子命令）。两级配置：包内 `fcmd/commands/*.toml`（出厂命令，按工具拆分为多个 TOML 文件）与 `${FCMD_HOME:-~/.fcmd}/commands.toml`（用户自定义，同名可覆盖内置单命令；与 Python 模块工具重名时 Python 优先，多子命令形态逐子命令合并注册——同名子命令先注册者优先，用户仅可新增子命令）。

```toml
# ~/.fcmd/commands.toml
[commands.hello]
help = "向某人问好"
cmd = "echo 你好 {name} x{count}"
cwd = "/tmp"                     # 可选：工作目录（支持 {参数名} 插值）
timeout = 30                     # 可选：超时秒数
env = { LANG = "zh" }            # 可选：环境变量（值支持插值）
message = "同步完成: {name}"       # 可选：执行成功后打印的完成消息（支持插值，dry-run 不打印；目标被 when 守卫跳过时不打印）
fail_message = "同步失败"          # 可选：执行失败后打印的失败消息（支持插值，如修复提示）

[commands.hello.args.name]       # 无 default → positional 参数
help = "目标名字"

[commands.hello.args.count]
type = "int"                     # str(默认)/int/float/bool/path/choices/list
default = 1                      # 有 default → --count 选项（list 无 default → positional 多值）
help = "次数"

[commands.publish]               # {参数名:content} 文件内容插值：读取文件内容（展开 ~，utf-8，去首尾空白）替换进 cmd
help = "发布文件备注"
cmd = ["notes-cli", "publish", "--text", "{note:content}"]
[commands.publish.args.note]
type = "path"                    # content 插值仅支持 type=str/path 参数
default = "~/note.txt"

[commands.pkg.install]           # list 参数：独占占位符按元素展开（无 shell 每元素一个 token）
help = "批量安装"
cmd = ["pip", "install", "{packages}"]
[commands.pkg.install.args.packages]
type = "list"
help = "包名列表"

[commands.fmttool.lint]          # bool 参数 on：值为真时向 cmd 尾部追加固定 token
help = "检查并可选自动修复"
cmd = ["ruff", "check", "{target}"]
[commands.fmttool.lint.args.target]
type = "str"
default = "."
[commands.fmttool.lint.args.fix]
type = "bool"
default = false
on = ["--fix", "--unsafe-fixes"]

[commands.deploy.build]           # when 守卫：探针不满足 → SKIPPED（命令退出码 0）
help = "有代码变更才构建"
cmd = ["make", "build"]
when = {cmd = "git status --porcelain", expect = "nonempty"}  # 探针 cmd/path 二选一，支持 {参数名} 插值
allow_upstream_skip = true        # 上游被跳过时豁免执行（默认连坐跳过）

[commands.backup.files]           # 探针 expect：cmd 用 nonempty/empty/success/failure，path 用 exists/missing
help = "目录已备份则跳过"
cmd = ["robocopy", "{source}", "{dest}", "/mir"]
when = {path = "{dest}", expect = "missing"}  # path 探针支持插值与 ~ 展开

[commands.deploy.all]             # 聚合可声明 args：值经共享变量流入子任务插值
help = "构建并部署"
needs = ["build"]
[commands.deploy.all.args.tag]
type = "str"
default = "latest"

[commands.dockercmd.login]        # tty 透传：stdout/stderr 不捕获（交互式命令）
help = "登录 Docker 镜像仓库"
cmd = ["docker", "login", "--username", "{username}", "{registry}"]
tty = true                        # 密码提示可见可输入
message = "已登录镜像仓库: {registry} (用户: {username})"
[commands.dockercmd.login.args.username]
default = ""
default_env = ["USERNAME", "LOGNAME", "USER", "LNAME"]  # 值等于 default 时按链取第一个非空环境变量

[commands.setenv]                 # action 原语：进程内动作（fn 任务，非 subprocess）
help = "设置环境变量"
action = "setenv"                 # 内建动作名；参数 schema 来自动作实现签名（禁声明 args）
message = "环境变量 {name} 已设置"

[commands.pkg.u]                  # compute 计算原语：采集→计算→注入 exec 参数（输出管道）
help = "卸载包（支持通配符）"
cmd = ["pip", "uninstall", "-y", "{expanded}"]
[commands.pkg.u.args.packages]
type = "list"
[commands.pkg.u.compute]
action = "pip_expand"             # 计算动作：输入按实现签名取 CLI 变量，返回值注入 {expanded}
as = "expanded"                   # 空结果（空 list/空串）→ SKIPPED；同链同名只计算一次

[commands.sync]
help = "同步（跨平台示例）"
win.cmd = "robocopy src dst /mir"   # 字符串 → shell 执行
unix.cmd = ["rsync", "-a", "src/", "dst"]  # 数组 → 无 shell（注入面最小）
aliases = ["sy"]

# 多子命令形态：与 Python 模块同名的工具逐子命令合并注册
[commands.mytool.go]
help = "执行构建"
cmd = ["make", "build"]

[commands.mytool.all]
help = "聚合（needs 编排，无 cmd）"
needs = ["go"]
strategy = "thread"              # sequential/thread/async/dependency
```

```bash
fcmd hello world --count 3        # echo 你好 world x3
fcmd sy                           # 别名调用
fcmd mytool all                   # 聚合：先 go 后 all（thread 并行）
```

能力边界：单命令与多子命令 exec 形态（平台分支 / 参数（含 list 多值、bool on 固定 token 与 default_env 环境回退链） / 插值（含 `{参数名:content}` 文件内容插值） / cwd / env / timeout / when 守卫探针（支持参数插值与返回码判定）/ needs 聚合（含 allow_upstream_skip 豁免连坐）/ 聚合 args 共享插值 / tty 透传 / message 与 fail_message post-run 消息 / strategy / action 内建动作原语（进程内 fn 任务，参数 schema 来自动作实现签名）/ compute 计算原语（进程内计算动作返回值注入 exec 参数，输出管道形态））；matrix/if 条件编排属 YAML 编排的领地。

### DSL 逻辑边界

判断一段命令逻辑能否迁入 DSL：

| 类别 | 判定 | 例子 |
|------|------|------|
| 纯 exec（含 when 守卫与聚合编排） | 可迁 DSL | `gittool a/i`（守卫链 `_init`/`_add`/`_commit`）、`piptool d/f`、`dockercmd login`（tty 透传 + default_env 环境回退）、`sshcopyid`（when 探针 + env 插值传 SSHPASS + `{参数名:content}` 公钥内容）、`reseticoncache`（win shell 链 + unix 提示分支） |
| 输出管道（采集→计算→注入 exec 参数） | 可迁 DSL（compute 计算原语） | `piptool u/r`（`pip list` 采集 + 通配符展开/受保护包过滤注入 uninstall/install 参数；r 经 `_uninstall`/`_install` 隐藏链编排） |
| 输出管道（计算主体为进程内逻辑，输出解析仅子步骤） | 保留 Python | `portcheck`（socket 探测主体 + psutil 占用查询回显） |
| 动态遍历（运行时枚举文件系统） | 保留 Python | `gittool isub`、`envdev` 系列 |
| 进程内副作用（无法映射为子进程） | 副作用型动作可迁 DSL（action 原语） | `setenv`/`writefile`/`filedate`/`filerename`/`filelevel`/`folderback`/`taskkill`/`which`/`sysinfo`（已迁入 action 原语；taskkill 逐进程 terminate→kill 回显，which 逐条查找回显，sysinfo 诊断信息打印，均无返回值消费）；`pathtool`/`filesearch`/`nettool`/`iptool` 产出计算结果（路径解析计算/搜索结果/HTTP 响应/IP 解析），非副作用型动作，保留 Python |
| 多步有状态流程 | 保留 Python | `bumpversion`、`packtool` |

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
  core/          # 运行时内核：DAG、报告、执行引擎、工具注册、编排、忽略规则
    shared/      #   中立共享层：dsl 与 tools 共同依赖（网络探测、文本 IO）
    toolkit/     #   函数签名 → argparse 的 CLI 工具基座
    execution/   #   sequential/thread/async/dependency 四种执行器
    orchestration/  # YAML 编排与 if 条件求值
    models/      #   通用数据模型
  cli/           # 仅入口、工具发现、内建命令与诊断辅助
    _builtins/   #   graph/info/completion/yaml/env/doctor/profiler
    _manifest.py #   静态工具清单读取（快路径：只加载目标工具）
    _manifest_data.py  # 生成文件，勿手改
  tools/         # 业务实现（自动发现）
    archive/ calc/ data/ dev/ hash/ llm/ media/ net/ pdf/ system/ text/
  dsl/           # TOML 命令定义与 exec/compute 原语
    actions/     #   @action 声明与参数转接（业务实现位于 tools/<域>）
  commands/      # 出厂 DSL 命令定义（*.toml）
  console.py     # rich 控制台薄封装
```

新增 Python 工具：在 `src/fcmd/tools/<领域>/` 下新建模块（非 `_` 前缀、非 `main`），用 `@fcmd.tool` 装饰器注册即可，无需修改 `main.py`。可选在模块内定义 `__tool_aliases__: list[str]` 声明别名。目录按功能域划分；图形界面版命名为 `<工具名>w.py` 并放在其核心逻辑所在域（如 `llm/llmconvertw.py`、`media/img2icow.py`）。

新增 / 删除 / 改名工具或调整别名后，重新生成静态工具清单（`fcmd <tool>` 快路径据此只加载目标工具；清单缺失或过期会自动回退全量发现，功能不受影响，只影响启动速度）：

```bash
uv run python -m fcmd.cli._manifest_data
```

`tests/cli/test_tools_manifest.py` 会校验清单与实跑全量发现严格一致。

公共 API（`import fcmd as fx`）名称保持稳定；原 `fcmd.apis.*` / `fcmd.engine.*` / `fcmd.models.*` / `fcmd.orchestration.*` 深路径已统一收敛到 `fcmd.core.*`，不保留兼容 shim。

## 许可证

MIT
