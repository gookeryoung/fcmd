# 0018 peview：PE 文件查看与安全编辑

- 状态：设计中（只读解析 / 编辑 / cmderw 集成分阶段落地）
- 日期：2026-11-11

## 背景

仓库已有 `reshack` / `reshackw`，但它们只覆盖「资源枚举 + 资源数据原位替换」，
且依赖可选依赖 `fcmd[pe]`（pefile）。实际排查 PE 文件时更常见的是另一类需求：
看清 DOS / NT / 可选头、数据目录、节表、导入导出、资源树、重定位与签名状态，
并少量修正入口点、子系统、镜像基址、节属性这类标量字段。

cmderw 的快速查看对 PE 只给出「二进制 + MZ」提示，无法定位结构与字段；因此需要
一个独立应用：既能在 cmderw 中对选中文件一键打开，也能脱离 cmderw 单独使用。

约束：

- 纯逻辑与 Tk 分离，解析 / 校验 / 写回全部可单测；
- 不新增第三方依赖，不引入 `pefile`（避免「没装 extras 就打不开」）；
- 目录深度遵守 ADR 0003（`fcmd/apps/<应用>/<模块>.py`，不建子包）；
- 编辑必须默认安全：备份、写后自校验、拒绝结构性改动、失败不留半成品。

## 决策

### D1 双入口，GUI 为主

应用包 `fcmd/apps/peview/`，按 ADR 0003 逐公开模块注册（不声明 `__tool_name__`）：

- `pevieww`（Tk）：主入口，结构浏览 + 十六进制视图 + 字段编辑；
- `peview`（CLI）：`info` / `headers` / `sections` / `directories` / `imports` /
  `exports` / `resources` / `relocations` / `debug` / `hex` / `check` / `fields` /
  `edit`，用于脚本化检查与无显示环境验证。

CLI 编辑默认只预演，必须显式 `--apply` 才落盘；GUI 编辑对话框同样先展示
「旧值 → 新值」再写入。

### D2 自研只读解析器，不用 pefile

`_pe.py` 用标准库 `struct` 从字节解析，覆盖：

- DOS 头（含 `e_lfanew`）、`PE\0\0` 签名、COFF 文件头；
- 可选头（PE32 / PE32+，含 DataDirectory 16 项）；
- 节表（名称、虚拟/原始大小、RVA/文件偏移、节属性）；
- 导入（含按名称 / 序号）、延迟导入、导出（含转发器）；
- 资源树（类型 / 名称 / 语言，最多 3 层）、基址重定位块、调试目录；
- Authenticode 签名存在性（Security 目录，按文件偏移而非 RVA）与 overlay 大小。

所有读取都经过 `_reader.py` 的边界检查；递归深度、描述符数量、符号数量设上限并
做循环保护，损坏文件返回带位置的中文 `PEError`，不抛裸 `struct.error`。

与 `reshack` 的边界：`reshack` 继续负责资源数据替换；`peview` v1 只读资源、
不编辑资源内容，重点在结构与头/节字段。

### D3 模块划分

| 模块 | 职责 |
|---|---|
| `_reader.py` | 边界安全读取、RVA↔文件偏移、PE 校验和计算、十六进制窗口 |
| `_pe.py` | 数据模型与 `parse_pe()`，纯只读 |
| `_report.py` | 文本/表格行、机器/子系统/节属性等标志位解读 |
| `_edit.py` | 可编辑字段白名单、值校验、备份、原子写、写后重解析校验 |
| `_view.py` | GUI 纯逻辑：分类树节点、详情行、十六进制视图、节点→文件偏移 |
| `peview.py` | CLI 注册与输出 |
| `pevieww.py` | Tk 窗口、设置持久化（`~/.fcmd/ui/pevieww.json`） |

`pevieww.py` 复用 `fcmd.gui.window` / `theme` / `config`，不另起窗口骨架。

### D4 编辑安全边界

可编辑字段（白名单）：

- `entry_point`、`image_base`、`subsystem`、`dll_characteristics`、
  `timestamp`、`stack_reserve` / `stack_commit` / `heap_reserve` /
  `heap_commit`、`checksum`；
- `section.<序号>.characteristics`（只改属性位，不改名称 / 大小 / 偏移）。

一律拒绝的字段（结构性，改动即可能破坏镜像）：`e_lfanew`、`optional magic`、
`NumberOfSections`、`SizeOfOptionalHeader`、`SizeOfHeaders`、节的
`VirtualAddress` / `VirtualSize` / `SizeOfRawData` / `PointerToRawData`、
全部数据目录的 RVA / Size。错误信息说明「该字段改变文件布局，peview 不支持」。

写入流程（`apply_edits`）：

1. 解析原文件并校验字段名、宽度、取值范围（如 `image_base` 需 64K 对齐、
   `entry_point` 需小于 `SizeOfImage`）；
2. 在内存中打补丁，并按需重算 PE 校验和；
3. 写同目录临时文件（保留原文件属性）；
4. 重新解析临时文件，校验：结构与数据目录不变、未编辑字段逐项相等、
   导入 / 导出 / 资源 / 重定位签名一致、被编辑字段回读等于目标值；
5. 校验通过后创建 `<文件名>.backup[N]` 备份，再 `os.replace` 原子替换；
6. 任一步失败：删除临时文件，原文件保持不变，不产生备份残留。

若检测到 Authenticode 签名，GUI 必须显示「写入将使数字签名失效」并要求勾选
确认；CLI 必须传 `--allow-signature-break`，否则拒绝执行。

### D5 cmderw 集成

- 「文件」菜单与右键菜单新增「PE 查看 / 编辑…」，仅在选中单个普通文件时可用
  （`_MENU_ONE_FILE` / `_MENU_ARCHIVE_READONLY` 同步登记）；
- 以独立进程启动：`sys.executable -m fcmd.cli.main pevieww --file <路径>`
  （Windows 下加 `CREATE_NO_WINDOW`），应用之间不互相 import；
- 不新增快捷键，避免与系统或现有键位冲突。

### D6 测试与门禁

- 测试内用合成 PE32 / PE32+ 夹具覆盖正常解析、截断 / 越界 / 循环资源等损坏输入；
- `_reader` / `_pe` / `_report` / `_edit` / `_view` 覆盖率与现有水平一致（≈100%），
  设置持久化函数同样单测；Tk 窗口类不纳入覆盖率；
- 改动后按 ADR 0003 在干净工作区重生成 `fcmd/cli/_manifest_data.py`；
- `make check-fast` 必过，收尾 `make test` 全绿。

## 验收

- `peview info <exe>` 能列出头、节、目录、导入 / 导出 / 资源 / 重定位 / 签名状态；
- 修改入口点 / 子系统 / 节属性后写入成功，备份存在，重新解析结构不变；
- 传入结构性字段或非法值被拒绝且原文件字节完全不变；
- 写入过程模拟失败（校验不通过 / 替换失败）后无临时文件残留；
- `pevieww` 可打开文件、切换分类、跳转十六进制、编辑字段并重新加载；
- cmderw 中选中 PE 文件可从菜单打开 `pevieww`。

## 落地状态

- 设计：已定稿（本 ADR）。
- 只读解析与展示：已落地。`_reader` / `_pe` / `_report` / `_view` / `peview` /
  `pevieww` 与 `tests/apps/peview` 合成夹具就位，`uv run pytest tests/apps/peview`
  全绿；GUI 为只读浏览（分类树 + 详情 + 十六进制跳转 + 概览）。
- 编辑：已落地。`_edit.apply_edits` 实现白名单、值校验、内存补丁、临时文件
  重解析校验、`<文件名>.backup[N]` 备份与原子替换；CLI `peview edit` 默认预演，
  `--apply` 写入，签名文件需 `--allow-signature-break`；GUI「编辑字段…」对话框
  常驻风险说明并要求勾选确认。
- cmderw 集成与收尾：待落地。
