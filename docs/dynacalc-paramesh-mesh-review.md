# dynacalc 侧评审：paramesh 网格交接契约（`paramesh-mesh/1`）

- 评审方：dynacalc（deck 生成 / 求解调度 / 结果门控的负责方）
- 评审对象：`fcmd paramesh contract` 全文、ADR [0022](adr/0022-paramesh.md) D6，以及
  `src/fcmd/apps/paramesh/_kfile.py`、`_package.py` 的实现
- 上游契约：[dynacalc-model-contract.md](dynacalc-model-contract.md)（v1.1）
- 日期：2026-11-13
- 结论版本：v1 意见，只做**加法扩展**，`schema` 字符串仍为 `paramesh-mesh/1`

## 0. 一句话结论

「几何 + 拓扑 + 区域 + 集合命名」这套数据骨架够用，但**按当前清单还不能直接组 deck**：
缺 4 类必填字段（部件角色、截面元数据、集合数值 ID、起爆点 / 轴向范围），另有 2 处
必须澄清的语义（`element_type: "shell"`、`mesh.k` 中 `*PART` 的占位 MID）和 1 处卡格式
问题（`*SET_*_LIST` 的 SID 与成员写在同一行）。单元与截面类型与 dynacalc 现有
3D ALE 模板**不一致**，需要新增「轴对称壳」模板族，而不是改现有模板。

## 1. 字段够不够组 deck

### 1.1 够用（无需改动，可直接引用）

| 字段 | deck 侧用途 |
|---|---|
| `schema` / `mesh_id` / `level` | 包定位、`case.json` 记录、三档配对键 |
| `digest` / `geometry_key` | 追溯与「同几何三档」比较的准入条件 |
| `dimension` | 模板族选择（`axisymmetric-2d` → 壳模板） |
| `units.length` | 坐标与长度的单位约定（mm） |
| `model.id` / `model.digest` | 与工况 `dynacalc-model/1` 的绑定校验 |
| `regions[]`（名称、`part_id`、面积误差） | 部件与区域的对应、质量记录 |
| `parts[]`（`part_id`、`name`） | `*PART` 生成与 PID 映射的锚点 |
| `sets` 的**名称** | 命名稳定，deck 侧按名取 ID（见 §3） |
| `files` | `*INCLUDE` 相对路径 |
| `tbd[]` / `warnings[]` | 写进 `case.json` 与报告，不参与求解 |

### 1.2 不足（阻塞「直接组 deck」）

| 缺什么 | deck 侧卡在哪 | 建议加法 | 级别 |
|---|---|---|---|
| 部件角色 | `*MAT_*` / `*EOS_*` / 接触角色无法从 `name` 可靠映射 | `parts[].role` ∈ `explosive / air / wall / inert` | 必填 |
| 截面元数据 | `*SECTION_SHELL` 的实厚、积分点、ELFORM 无处取；当前 `mesh.k` 全部写 1.0 占位 | `parts[].section{secid, elform, thickness_mm, nip?}` 或顶层 `sections[]` | 必填 |
| 集合数值 ID | `*BOUNDARY_*`、`*DATABASE_HISTORY_NODE`、`*SET_*` 只认 SID，不认 title；清单只有计数 | `sets.nodes[]` / `sets.parts[]` 改为 `{name, id, count/part_ids}`，`members` 可选 | 必填 |
| 起爆点与轴向范围 | `*INITIAL_DETONATION` 需要坐标；`*DATABASE_*` 的时程与选点需要范围 | `extent{z_start_mm, z_end_mm, r_max_mm}` 或 `detonation{x, y, z, t0}` | 必填 |
| 探针节点 | `*DATABASE_HISTORY_NODE` 需要具体节点号，集合计数不够 | `probes{front_nodes[], wall_inner_nodes[], wall_outer_nodes[]}`，或允许 `sets.nodes[].members` | 必填 |
| `element_type` 语义 | `shell` 无法区分普通壳与 ELFORM=14 轴对称 | 改 `axisymmetric-shell`，或加 `elform: 14` | 必填（澄清） |

### 1.3 建议加法（不阻塞，但能少一层猜）

| 建议 | 理由 |
|---|---|
| `sets.nodes` 的 SID 段与 `sets.parts` 的 SID 段错开（如节点集 1–99、部件集 101–） | 避免跨集合类型的 ID 歧义，便于人工核对 deck |
| `scale.section_angle_deg` 注明「代表截面方位角，非旋转角」或改名 | ELFORM=14 自带整周回转，方位角被 deck 误读为旋转角会算错质量 |
| `regions[].area_mm2` 注明是**截面面积**还是旋转体体积 | deck 侧做质量核对与 `quality` 交叉验证时要除以 2πr |
| `units` 增加一句说明：只约定长度，密度 `g/cm^3` / 速度 `m/s` 由 deck 换算 | 现状 `units` 只有 `length`，清单读者容易以为全量单位已锁定 |

## 2. 单元与截面类型：与 dynacalc 模板不一致

| 项目 | dynacalc 现有默认模板（`_template.py`） | paramesh 产出 |
|---|---|---|
| 维度 | 3D | `axisymmetric-2d` |
| 单元 | `*SECTION_SOLID`，ELFORM=11（ALE 单点实体） | `*ELEMENT_SHELL` + `*SECTION_SHELL` ELFORM=14 |
| 多物质 | `*ALE_MULTI_MATERIAL_GROUP` | 无（纯 Lagrangian 区域） |
| 流固耦合 | `*CONSTRAINED_LAGRANGE_IN_SOLID` | 无（界面共节点） |
| 部件 | 3 个 `*PART`（charge / tube / air），MID 1/2/3 | v1.1 起 `mesh.k` 不含 `*PART`，由 deck 按 `parts[].role` 生成 |
| 起爆 | `*INITIAL_DETONATION` 写死 0,0,0 占位 | v1.1 起 `detonation{x, y, z, t0}`，x 为轴向 |
| 组装 | 单文件自包含，无 `*INCLUDE` | 网格片段，由 deck `*INCLUDE` 引用 |

结论：**不是改模板，是加模板族**。dynacalc 侧按 `dimension` 选模板：

- `3d-ale`：现有模板，原样保留（无网格交接时走这条）；
- `axisymmetric-2d`：新增，去掉全部 ALE / 流固耦合卡，改为壳 + 轴对称，材料映射沿用现有
  占位（炸药 `*MAT_HIGH_EXPLOSIVE_BURN` + `*EOS_JWL`、间隙 `*MAT_NULL`、管壁
  `*MAT_PLASTIC_KINEMATIC`），`*CONTROL_*` 只保留壳与轴对称适用的子集。

ELFORM=14 的轴对称假定（对称轴位置、`r ≥ 0`、厚度取实厚）属于求解器行为，dynacalc 只负责
按契约写卡，不做物理校验；建议 paramesh 在 `quality.txt` 里明确「已按 x 轴为对称轴生成」，
并择机在目标求解器版本上做一次实跑确认。

## 3. 集合命名与 deck 引用：名字能对上，ID 对不上

命名覆盖度是够的（12 个节点集合 + 3 个部件集合，见 ADR 0022 D4），语义也够 deck 用：

| 集合 | deck 侧典型引用 | 需要的输入 |
|---|---|---|
| `MESH_OUTER` | `*BOUNDARY_PRESCRIBED_MOTION_SET` / 输出 | SID |
| `MESH_END_START`、`MESH_END_END` | 端面约束 / 起爆侧 | SID |
| `MESH_NRB` | `*BOUNDARY_NON_REFLECTING` | SID（语义见 §7 问题 5） |
| `MESH_IF_PROP_GAP`、`MESH_IF_GAP_WALL`、`MESH_IF_PROP_WALL` | 界面输出 / 接触（v1 共节点，不建卡） | SID |
| `MESH_WALL_INNER` | 管壁内表面输出 | SID |
| `MESH_REGION_*` | 分区输出、`*DATABASE_MATS` 对照 | SID |
| `MESH_AXIS` | 仅后处理（ELFORM=14 轴对称，**不要**生成 SPC） | SID |
| `MESH_PART_*` | 接触 / 输出 / 材料组 | SID |

两个具体问题：

1. **清单没有 SID**。`sets.nodes` 只有计数，`sets.parts` 只有 PID 列表；而 LS-DYNA 的
   `title` 只是注释，实际引用全靠 SID。当前 SID 由 `_kfile.py` 按
   `NODE_SET_ORDER.index(name) + 1` 生成，这条规则**没写进契约**，换实现就会漂移。
   → 建议清单直接给出 ID（首选），或把该规则写进契约并由 dynacalc 复算。
2. **卡格式**：`*SET_NODE_LIST_TITLE` / `*SET_PART_LIST_TITLE` 的第一张数据卡是
   `SID, DA1…DA4`，成员从下一行开始、每行 8 个。当前实现把 SID 与成员排在同一行
   （`_data_lines([sid, *ids])`），会被解析成 `DA1…` 字段并吞掉成员，需修正后再定稿。

另外，`*SET_NODE_LIST` 与 `*SET_PART_LIST` 的 SID 目前都从 1 开始，跨类型重号；建议在
契约层约定分段（§1.3），避免人工核对 deck 时混淆。

## 4. `mesh.k` 与 deck 的职责边界

三处冲突，必须在契约定稿前定死：

1. **`*PART` / `*SECTION_SHELL` 重复**：mesh.k 已含这两类卡，deck 若再写会产生重复
   PID / SECID。契约只说「PID / SECID / MID 为占位，由 deck 侧映射」，没说清是
   「deck 重写片段」还是「deck 避开片段」。
2. **MID 全为 1**：`*PART` 行是 `pid, pid, 1, 0`，三个部件共用 MID=1。deck 直接追加材料卡
   会让炸药、间隙、管壁全部指向材料 1，是可复现的错误，不是占位风格问题。
3. **`*SECTION_SHELL` 厚度全为 1.0**：管壁厚是物理量（来自 `model.wall_thickness`），
   1.0 占位无法被 deck 安全覆盖（deck 不知道真实值来自哪个代表截面）。

建议二选一，由 paramesh 定：

- **方案 A（dynacalc 首选）**：mesh.k 只保留 `*NODE` / `*ELEMENT_SHELL` / `*SET_*`，
  去掉 `*PART` / `*SECTION_SHELL`；`*PART` / `*SECTION_SHELL` 全部由 deck 按
  `parts[].role` + `parts[].section` 生成。职责最清晰，也避免「改网格片段」的争议。
- **方案 B**：保留，但按 `part_id` 写不同占位 MID（1/2/3），`*SECTION_SHELL` 写真实分区
  厚度，deck 仅补材料 / EOS 卡；同时契约要显式允许「deck 可重写 `*PART` 的
  secid/mid/eosid 与 `*SECTION_SHELL` 的厚度」，拓扑仍禁止改动。

无论哪种方案，都建议保留现有「不得重编号节点 / 单元、不得改拓扑、集合名稳定」的约束。

## 5. 需要补进契约的文档项（非字段）

1. **`mesh_id` 的精确定义**：`<model>-<level>-<digest8>`，其中 model 段经过
   `[^0-9A-Za-z_.-] → _` 替换并去除首尾下划线（`_safe_model_name`）。下游不得从
   `model.id` 反推目录名，只能读 `mesh_id`。
2. **`digest` / `geometry_key` 的规范化细则**：当前只写「指纹」，没给序列化规则
   （排序、分隔符、浮点表示、是否含 `schema`）。dynacalc 若要独立复算校验，需要与
   `dynacalc-model-contract.md` §8 同级别的 12 条归一化说明；否则请把契约里的
   「校验」降级为「只校验存在性与三档相等」，不要求复算。
3. **`geometry_key` 的组成**：文档须列出参与项（`dimension`、`model_digest`、
   `section_angle_deg`、区域名顺序、`part_id` 列表、节点 / 部件集合名），否则
   「相邻网格只比同 key 三档」这条规则无法被独立实现。
4. **目录归属**：`write_package` 写到 `<out>/<mesh_id>/`，dynacalc 期望
   `<case>/meshes/<mesh_id>/`。建议契约明确「paramesh 只写自己的输出根，由上层（CLI / GUI）
   放到 meshes 根下」，`files` 里的文件名保持不变，`mesh_id` 必须等于目录名。

## 6. dynacalc 侧的校验与实现计划

### 6.1 读网格包前的校验（失败即报错，不写半成品 deck）

| 校验项 | 失败语义 |
|---|---|
| `schema == paramesh-mesh/1` | 不支持的模式版本 |
| `dimension == axisymmetric-2d` | 未支持维度，显式报错（不静默回落 3D 模板） |
| `manifest.mesh_id == 目录名` | 包被改名或复制错位 |
| `model.digest == ` 工况模型 digest | 网格与工况不是同一模型 |
| 三档 `geometry_key` 相同 | 才允许做相邻网格误差门控 |
| 被 deck 引用的集合存在且非空 | 缺失即报错，并指出集合名 |
| `parts[].role` 齐全、`*PART` MID 与材料卡一致 | 见 §4，按最终方案校验 |

### 6.2 写入与引用方式

- `case.json` 增加：`mesh_id` / `mesh_digest` / `geometry_key` / `level` / `template_family`；
- deck 主文件沿用 `<case>/<编码>-<指纹前8位>/case.k`（与求解调度、断点续跑的既有约定一致，
  不另起 `deck.k`），网格用 `*INCLUDE` 相对路径 `../meshes/<mesh_id>/mesh.k` 引用
  （不复制、不改片段）；
- `*DATABASE_HISTORY_NODE` 用 `probes[].node_id`（按节点号升序、去重）；
- `*BOUNDARY_*` 一律按 SID 引用，不按 title 猜测；v1 不生成 `*BOUNDARY_NON_REFLECTING`。

### 6.3 排期（明确排在 GUI / 测试之后）

| 顺序 | 工作 | 状态 |
|---|---|---|
| 1 | CLI + Tk GUI + 单元测试（现有闭环） | 已完成 |
| 2 | 模板族拆分：`3d-ale` 保持 + `axisymmetric-2d` 新增，纯函数渲染测试 | 已完成 |
| 3 | 网格包读取与校验模块（§6.1）+ 单元测试 | 已完成 |
| 4 | deck 组装：`*PART` / `*SECTION_SHELL` 映射、`*INITIAL_DETONATION`、`*DATABASE_HISTORY_NODE`、`*INCLUDE` | 已完成 |
| 5 | 门控：同 `geometry_key` 三档的网格误差比较 | 已完成（纯函数 + 测试；结果侧门控后接） |
| 6 | CLI `--mesh-root` / `--dimension` / `--mesh-level` 与 GUI 挂接 | CLI 已完成；GUI 待挂接 |

落地位置：`_template.py`（`TEMPLATE_FAMILIES` / `template_for`）、`_mesh.py`（读取、校验、
推断与档间比较）、`dynacalc.py generate`（`--mesh-root` / `--dimension` / `--mesh-level`）。
现有 3D ALE 路径的模板文本与指纹保持不变；网格派生值只在给定网格根时参与指纹。

## 7. 需要 paramesh 回复的 6 个问题

1. `*PART` / `*SECTION_SHELL` 走方案 A（从 mesh.k 移除、deck 生成）还是方案 B（保留但
   写不同 MID 与真实厚度）？
2. `sets` 是补数值 ID（推荐），还是把 `index + 1` 的生成规则写进契约？
3. `*SECTION_SHELL` 的厚度取哪个代表值？管壁厚度沿轴向 / 周向有变化（`wall_thickness`
   为区间、`circumferential` 为折线），v1 用 `scale.section_wall_mm` 是否就是它？
4. `probes` 由谁选点？若由 paramesh 选，选点规则能否保证三档落在同一物理位置（跨档稳定，
   否则爆速提取与到达时程无法比较）？
5. `MESH_NRB` 与 `MESH_END_END` 同集合在物理上是冲突的（自由端需要反射，无反射边界会吸收）。
   v1 是否先不生成 `*BOUNDARY_NON_REFLECTING`？
6. `element_type` 最终取值？建议 `axisymmetric-shell` + `elform: 14`，避免与普通壳混淆。

以上 6 条答复后，dynacalc 侧按 §1.2 的加法清单一次性对齐，`schema` 字符串不变。

## 8. v1.1 复核（paramesh `cd7e194`，2026-11-13）

paramesh 已按 §1.2 / §7 落地，`schema` 仍为 `paramesh-mesh/1`。逐条对照：

| 本文件要求 | v1.1 实现 | 结论 |
|---|---|---|
| `parts[].role` | `explosive / air / wall / inert` | 接受 |
| `parts[].section{secid, elform, thickness_mm}` | `secid = part_id`、`elform = 14`、厚度取代表截面径向厚度 | 接受（厚度语义见 §8.2） |
| 集合数值 ID | 节点 1–99、部件 101–199，规则写入契约 §5.1 | 接受 |
| `extent` / `detonation` | `extent{z_start_mm, z_end_mm, r_max_mm}`、`detonation{x, y, z, t0}` | 接受 |
| `probes` 具体节点 | `probes[]{name, node_id, r_mm, z_mm}`，三档物理坐标一致 | 接受（站点密度见 §8.2） |
| `*PART` / `*SECTION_SHELL` 归属 | 方案 A：mesh.k 移除，deck 生成 | 接受 |
| `*SET_*_LIST` 卡格式 | 首卡 `SID, DA1..DA4`，成员另起每行 8 个 | 已修正 |
| `element_type` 语义 | 保持 `shell`，靠 `section.elform = 14` 澄清；新增 `coordinate_system` | 接受 |
| `MESH_NRB` 语义 | v1 不生成 `*BOUNDARY_NON_REFLECTING`，deck 只用 `MESH_END_END` | 接受 |
| `mesh_id` / `digest` / `geometry_key` 可复算细则 | 契约 §5 给出规范化与 payload | 接受 |

### 8.1 坐标约定变更对模板族的影响

v1.1 把坐标定为 **x = 轴向（模型 z）、y = 径向（模型 r）、z = 0**，与 `*SECTION_SHELL`
ELFORM=14 以 x 轴为对称轴一致。dynacalc 侧据此确认：

- `3d-ale` 模板**不改**：它没有轴对称假定，x/y/z 由三维工况自身决定；
- `axisymmetric-2d` 模板**独立新建**，不复用 3D 模板的轴序，也不把 `detonation` 写死为
  `0,0,0`，而是绑定 `${detonation.x}` / `${detonation.y}` / `${detonation.z}`；
- `*DATABASE_HISTORY_NODE` 用 `probes[].node_id`，探针按 `probes[].name` 映射到结果列
  （`axis_*` → 波面到达，`wall_*` → 管壁速度 / 应变）；
- 校验时读 `coordinate_system`，要求 `symmetry_axis == "x"`、`x == "axial"`、`y == "radial"`，
  不匹配即报错，不静默按 3D 轴序组卡。

### 8.2 两项待确认 → 已由 paramesh `0a6d966` 确认

1. **`section.thickness_mm` 的物理语义**：固定为 `1.0`，表示轴对称连续体的**周向单位厚度**，
   另带 `thickness_semantics = "unit_out_of_plane"`；区域总厚度只出现在
   `section.region_thickness_mm`，**不得**写入 `*SECTION_SHELL`。paramesh 同时补
   `quality.mass_check`：单位密度下按求解器厚度折算的旋转体体积 / 解析体积，基准模型
   两者都是 25132.741 mm³、比值 1.0；若误用区域总厚度约 9.855 倍。dynacalc 在网格包校验
   （`_mesh.load_package`）里断言 `|ratio - 1| <= 1e-2`（`MASS_RATIO_TOLERANCE`），并断言
   每个部件 `section.thickness_mm == 1.0`、`thickness_semantics == "unit_out_of_plane"`；
   任一项不满足即拒绝出卡，不落半成品 deck。
2. **探针轴向站位**：从 8 个增到 11 个，新增 `axis_station_025 / 050 / 075`；三档网格在
   0.25L / 0.5L / 0.75L 都有精确节点，稳态段爆速用这三点拟合。dynacalc 把
   `axis_start / axis_station_025 / axis_station_050 / axis_station_075 / axis_end`
   设为必检探针（`_mesh.REQUIRED_PROBES`），缺失即报错。

### 8.3 排期与落地状态

§6.3 的顺序与内容不变，当前进度见其状态列：模板族拆分、网格包校验、deck 组装与档间误差
比较已完成并有测试覆盖；CLI 已接 `--mesh-root` / `--dimension` / `--mesh-level`，Tk GUI
挂接留到最后一步。

另：`extent` 用 `z_start_mm` / `z_end_mm`、`detonation` 用 `x`，两套命名并存（模型坐标 vs
LS-DYNA 坐标）。v1 接受该写法，dynacalc 只按契约读，不做推断；后续若改名按加法扩展处理。

