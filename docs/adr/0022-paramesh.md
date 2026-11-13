# 0022 paramesh：参数化模型到仿真网格

- 状态：最小原型已落地（v1，二维轴对称）；已按 dynacalc 网格契约评审 `0e91922` 对齐
- 日期：2026-11-13
- 相关文档：[dynacalc 模型契约](../dynacalc-model-contract.md)、[dynacalc 网格评审](../dynacalc-paramesh-mesh-review.md)、[ADR 0020](0020-dynacalc.md)、[ADR 0021](0021-paramcad.md)

## 背景

链路是 `paramcad → paramesh → dynacalc`：上游产出 `dynacalc-model/1` 模型描述，
下游负责工况矩阵、deck 组装与求解调度，中间缺一层把几何离散成可求解网格的实现。
`paramesh` 只做这一层：读取模型里影响网格的几何与分布场，生成网格、边界集与质量
依据，**不求解、不给材料模型与物理参数、不写 dynacalc 的 deck 目录**。

所有示例数值都是虚构占位；不内置任何真实配方、工艺窗口或产品尺寸。

## 决策

### D1 应用边界与入口

新增包 `src/fcmd/apps/paramesh/`，平铺两层，符合 ADR 0003 与 `tests/test_layout.py`：

| 文件 | 职责 |
|---|---|
| `_errors.py` | 统一错误类型 `ParameshError` |
| `_model.py` | 消费 `dynacalc-model/1`：只提取网格需要的几何 / 缺陷 / 端部疏松 / 周向壁厚，并复用契约指纹 |
| `_config.py` | `paramesh/1` 网格配置：三档分母、壁厚层数、加密倍数、反应区尺度、截面角 |
| `_scale.py` | 特征尺度、代表截面、壁厚层数、加密带与未标定项 |
| `_mesh.py` | 二维轴对称结构化四边形网格、区域 / 部件 / 命名集合、加密记录 |
| `_quality.py` | 质量报告：长宽比、尺寸范围、退化 / 负体积、区域面积误差、壁厚层数 |
| `_kfile.py` | LS-DYNA 关键字网格片段（节点 / 壳单元 / 命名集合；部件与截面由 deck 生成） |
| `_package.py` | `paramesh-mesh/1` 网格包、清单、指纹与交接契约草案 |
| `_preview.py` | 纯逻辑线框与视图变换（不依赖 Tk） |
| `paramesh.py` | CLI：`example` / `check` / `generate` / `report` / `contract` |
| `parameshw.py` | Tk 预览窗口，复用 `fcmd.gui` 主题与设置持久化 |

协作约束：本阶段不修改共享工具注册表，也不重生成 `_manifest_data.py`；不触碰
`src/fcmd/apps/dynacalc/` 与 `src/fcmd/apps/paramcad/` 内任何文件。工具发现走动态
路径，入口注册由统一协调后处理。

### D2 网格尺度策略

`L_char = min(反应区尺度, 最小壁厚, 最小几何缺陷特征尺寸)`，三者都从模型或配置读取：

- 反应区尺度只能来自配置 `reaction_zone_mm`（属物理输入，不内置默认值）；
- 最小壁厚取 `wall_thickness.min`，缺失时回落代表截面壁厚；
- 最小缺陷取 `local_defects[].size_mm` 的最小值，TBD 不参与比较。

全局尺寸 `h = L_char / divisor`，三档默认分母 8 / 16 / 32（可配置）。三档共享同一
几何与区域划分，只改 `h`，保证 dynacalc 可在 `geometry_key` 相同的前提下比较相邻
网格误差。`driver` 记录最终由哪一项决定 `L_char`，写进尺度摘要与清单。

局部加密：

| 区域 | 策略 |
|---|---|
| 起爆端成长区 | 轴向首 `initiation_zone_mm` 内按 `refinement_factor × h` 起步、按 `growth` 几何增长 |
| 端部疏松段 | `end_loose.length_fraction × length` 为轴向加密带，与起爆端共用分段增长；不按均匀密度处理 |
| 最薄药层 | 药层径向按界面侧细、轴线侧粗的几何增长分配，界面侧尺寸受 `refinement_factor × h` 限制 |
| 药管界面 | 药层靠间隙侧与管壁内表面均为界面加密起点 |
| 管壁内表面 | 壁厚方向强制分层，层数落在 `[wall_layers.min, wall_layers.max]`（默认 6–10），报告给出实际层数 |
| 偏心近壁侧 | 代表截面自动取 `min(壁厚, 药层厚度)` 最小的角度；也可由配置显式给定 `section_angle_deg` |

### D3 周向壁厚与偏心

二维轴对称无法表达周向变化，因此把周向信息收敛到一个代表截面：

- `circumferential.kind: polyline` 按角度线性插值，跨 360° 回绕；扫描 0–359° 取最坏角度；
- `uniform` 与缺失都按均匀处理；
- `kind: tbd` 或任一样本厚度 TBD 时，按已标定样本的最小值（无有效样本时按名义壁厚
  与 `min` 的较小值）作最坏情形，并在 `tbd[]` 与报告里标注「未标定」，不报错；
- 偏心量由 `radial_mm` 与 `circumferential_mm` 合成，方向角缺省时由分量反推，
  给出 `angle_deg` 时以它为准；内孔与外圆按偏心圆求交计算该角度壁厚。

`wall_thickness.min` / `max` 表示轴向 / 名义范围，不当作周向偏心量使用（契约 4.2）。

### D4 区域与边界语义

区域固定顺序与部件号：炸药（1）、间隙 / 空气（2）、管壁（3）；间隙为零时只有
炸药与管壁两区，界面共节点。区域在径向分层、轴向站点三区共享，界面节点严格共用。

命名集合视为稳定接口：

| 集合 | 语义 |
|---|---|
| `MESH_AXIS` | 轴线对称（r = 0） |
| `MESH_OUTER` | 管外表面 |
| `MESH_END_START` / `MESH_END_END` | 起爆端 / 自由端 |
| `MESH_NRB` | 无反射边界预留名；v1 不生成 `*BOUNDARY_NON_REFLECTING`，当前与自由端同集合 |
| `MESH_IF_PROP_GAP` / `MESH_IF_GAP_WALL` / `MESH_IF_PROP_WALL` | 药-间隙、间隙-壁、零间隙时的药-壁界面 |
| `MESH_WALL_INNER` | 管壁内表面 |
| `MESH_REGION_*` | 各区域节点集合 |
| `MESH_PART_ALL` / `MESH_PART_FLUID` / `MESH_PART_WALL` | 部件集合 |

### D5 质量指标

每个网格包给出 `quality.json` / `quality.txt`：

- 长宽比分布（`≤1.5` / `1.5–2` / `2–5` / `5–10` / `>10`）与最小 / 最大长宽比；
- 单元特征尺寸范围（面积等效尺寸）；
- 退化单元与负体积单元计数（必须为 0）；
- 各区域面积与解析值对照及相对误差；
- 壁厚方向实际层数与配置目标区间；
- 尺度、层数、间隙过薄等警告；
- 坐标约定固定写入 `quality.txt`：`x=轴向(z)`、`y=径向(r)`、`z=0`，已按 x 轴为对称轴生成（`ELFORM=14`）。

### D6 交接契约（`paramesh-mesh/1`，已对齐 dynacalc 评审 v1）

定稿前不向 dynacalc 的 deck 目录写入任何文件；`fcmd paramesh contract` 输出全文。

目录结构：

```
<case>/meshes/<mesh_id>/
  manifest.json     权威清单
  mesh.k            LS-DYNA 网格片段
  quality.json      完整质量报告
  quality.txt       文本摘要
```

paramesh 只写自己的输出根，由上层放到 `meshes/` 下；`mesh_id` 必须等于目录名。

坐标与单元：节点写 `x=轴向(z)`、`y=径向(r)`、`z=0`，`r >= 0`，已按 x 轴为对称轴生成；
单元为 `*ELEMENT_SHELL`，截面统一 `ELFORM=14`（轴对称壳），`element_type` 保持
`shell`，用 `parts[].section.elform` 表达轴对称语义。`scale.section_angle_deg` 是代表
截面方位角，不是整周旋转角。`regions[].area_mm2` 是 r–z 截面面积，不是旋转体体积。

`manifest.json` 必填字段：`schema`（`paramesh-mesh/1`）、`mesh_id`、`digest`、
`geometry_key`、`dimension`、`level`、`element_type`、`coordinate_system`、`units`、
`model`（`id` / `digest` / `source` / `end_loose` / `circumferential_wall`）、`scale`、
`regions[]`、`parts[]`、`sets`、`extent`、`detonation`、`probes[]`、`counts`、
`size_range_mm`、`quality`、`refinements[]`、`tbd[]`、`warnings[]`、`files`。

- `parts[]`：`{part_id, name, role, element_type, section{secid, elform, thickness_mm}}`；
  `role` ∈ `explosive / air / wall / inert`；`section.secid = part_id`；`elform = 14`；
  `thickness_mm` 取代表截面处的径向厚度：炸药 `scale.propellant_radius_mm`、间隙
  `scale.gap_mm`、管壁 `scale.section_wall_mm`；`nip` 可选，v1 不给出。
- `sets`：节点 `{集合名: {id, count}}`、部件 `{集合名: {id, part_ids}}`；SID 规则见下。
- `extent`：`{z_start_mm, z_end_mm, r_max_mm}`，来自实际节点。
- `detonation`：`{x, y, z, t0}`，起爆端轴线（`x=z_start_mm`、`y=0`、`z=0`、`t0=0`）。
- `probes[]`：`{name, node_id, r_mm, z_mm}`，锚定端面与固定半径，三档物理坐标一致。

`mesh.k`（方案 A）只含 `*NODE` / `*ELEMENT_SHELL` / `*SET_NODE_LIST_TITLE` /
`*SET_PART_LIST_TITLE`；**不含** `*PART` / `*SECTION_SHELL` / `*MAT_*` / `*KEYWORD` /
`*END` / `*BOUNDARY_NON_REFLECTING`。deck 按 `parts[].role` + `parts[].section` 生成
`*PART` 与 `*SECTION_SHELL`，职责边界清晰。`*SET_*_LIST` 的首张数据卡为
`SID, DA1, DA2, DA3, DA4`，成员从下一行起每行 8 个。

可复算细则：

- `_safe_model_name`：`re.sub(r"[^0-9A-Za-z_.-]+", "_", model.id).strip("_") or "mesh"`；
  `mesh_id = <safe_model>-<level>-<digest 前 8 位>`。
- 集合 SID：节点集合按 `NODE_SET_ORDER` 固定顺序从 1 开始（1–99），未生成的集合保留
  空号；部件集合按 `PART_SET_ORDER` 从 101 开始（101–199）。`MESH_NRB` 当前与
  `MESH_END_END` 同集合但为预留名，v1 不对其生成无反射边界。
- `digest`：`json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`
  的 UTF-8 字节取 SHA-256；`payload = {schema, dimension, model_digest, level,
  nodes: [[r_mm, z_mm] 按 node_id 升序], elements: [[part_id, n1..n4] 按 element_id 升序]}`。
- `geometry_key`：同一规范化；`payload = {dimension, model_digest, section_angle_deg,
  regions: [区域名按生成顺序], parts: [part_id 按生成顺序], node_sets: [实际集合名升序],
  part_sets: [部件集合名升序]}`；不含 `level`、坐标、单元与 `mesh_id`，故三档一致。

deck 侧约束：

1. 导入前校验 `schema` / `dimension` / `mesh_id == 目录名` / `manifest.model.digest`
   与工况模型 digest 一致；
2. 相邻网格误差判定只允许比较 `geometry_key` 相同的三档；
3. 不得重编号节点 / 单元；如需重编号必须同步重写全部集合；
4. 集合名视为稳定接口，边界条件按 SID 引用，不按 title 猜测；
5. 禁止修改 `mesh.k` 的单元拓扑；`*PART` / `*SECTION_SHELL` / 材料 / 接触 / 载荷由
   deck 侧生成；
6. 契约定稿前 paramesh 不写 dynacalc 的 deck 目录。

### D7 不变量

- 三档共享 `model.digest`、`geometry_key`、区域顺序、集合名与 SID、探针物理坐标，
  仅尺寸等级不同；
- 同一模型 + 配置 + 档位 → 逐字节一致的 `manifest.json` / `mesh.k`（无时间戳、稳定排序）；
- 所有单元面积 > 0，退化与负体积计数为 0；
- 壁厚方向实际层数落在配置区间内，并在质量报告中给出实际值；
- 可留 TBD 的字段一律不报错，降级为最坏情形并记入 `tbd[]`；
- 区域面积与解析值对照，相对误差写入 `regions[].area_rel_error`。

### D8 非目标与三维接口

v1 不做三维、不做自适应重划分、不做网格收敛自动批跑（只生成三档，由 dynacalc 去跑），
不做材料 / 载荷 / 接触 / 初始条件。二维轴对称的维度字符串统一为
`axisymmetric-2d`；三维只在配置层预留 `dimension` 字段，当前给其他值直接拒绝，
后续以新的生成器实现，不改变既有清单字段语义。

### D9 评审答复（dynacalc `0e91922` §7）

| 问题 | v1 答复 |
|---|---|
| `*PART` / `*SECTION_SHELL` 归属 | 方案 A：从 `mesh.k` 移除，deck 按 `parts[].role` + `parts[].section` 生成 |
| 集合 ID | 清单直接给数值 SID；节点 1–99、部件 101–199，生成规则写进契约 |
| `*SECTION_SHELL` 厚度 | 取代表截面处的径向厚度：炸药 `propellant_radius_mm`、间隙 `gap_mm`、管壁 `section_wall_mm` |
| 探针选点 | paramesh 选点，锚定端面与固定半径；三档物理坐标一致，仅 `node_id` 随编号不同 |
| `MESH_NRB` 与自由端冲突 | v1 不生成 `*BOUNDARY_NON_REFLECTING`；`MESH_NRB` 为预留名，deck 只用 `MESH_END_END` |
| `element_type` | 保持 `shell` + `dimension=axisymmetric-2d`，用 `parts[].section.elform=14` 澄清轴对称 |

## 后果

- 网格与 deck 解耦：paramesh 只对清单与集合名负责，dynacalc 只对映射与求解负责；
- 周向信息在二维中只能通过代表截面体现，误差来源明确写进报告，避免静默近似；
- 端部疏松与周向壁厚折线都进入尺度决策，TBD 以最坏情形处理，保证「合法但未标定」的
  模型仍能生成可比较的三档网格；
- 交接契约已按 dynacalc 评审 v1 对齐（`schema` 不变，只做加法扩展）；`digest` /
  `geometry_key` / 集合 SID / probes 是后续独立复算与门控的主要接口面。
