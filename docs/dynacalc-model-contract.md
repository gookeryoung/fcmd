# dynacalc 模型描述接口契约（paramcad ↔ dynacalc）

- 契约版本：`dynacalc-model/1`（v1.1，仅加法扩展；schema 字符串不变）
- 状态：v1 定稿，供 paramcad 实现与 dynacalc 消费
- 修订：2026-11-13 增补端部疏松、周向壁厚分布、TBD 分类、归一化细则、
  `[0, 1)` 区间与黄金指纹向量
- 相关文档：[ADR 0020](adr/0020-dynacalc.md)

## 1. 目的与分工

| 角色 | 负责 | 不负责 |
|---|---|---|
| paramcad | 参数化建模与预览：管坯 / 装药 / 拉拔工艺参数 → 几何与分布场 → 二维截面、轴向剖面 | 工况矩阵、deck 生成、求解调度、结果汇总 |
| dynacalc | 工况组合（参数矩阵）、导入模型描述、映射到 deck 占位、调度、回收与门控 | 几何建模、网格生成、物理求解、公式推导 |

模型描述是 **纯数据输入**，dynacalc 不执行其中任何内容，也不修改模型文件。
一个模型文件可以携带多个模型，按 `case_scope` 绑定到不同工况；没有匹配模型的工况
回落矩阵内的占位与默认值。

## 2. 文件格式

- 编码 UTF-8，换行 `\n`（CRLF 可读入，指纹按规范化换行计算）。
- 格式 YAML（与 dynacalc 参数表一致；`pyyaml` 已是主依赖，不新增依赖）。
- 顶层必须是映射，`schema: dynacalc-model/1`，`models` 为非空列表。
- 示例见第 11 节。

```
model-file.yaml
├── schema: dynacalc-model/1
├── models[]
│   ├── id                 必填，文件内唯一
│   ├── case_scope         可选，include / exclude
│   ├── units              必填，固定工程单位
│   ├── confidence         可选，按域声明置信等级
│   ├── geometry           必填，外径 / 内径 / 长度 / 壁厚（含周向分布）/ 偏心
│   ├── drawing            可选，拉拔道次与减径 / 壁厚变化
│   ├── density_field      可选，轴向 / 径向 / 周向分布、端部疏松与局部缺陷
│   ├── interface          可选，间隙与脱粘状态
│   └── tbd                可选，文档性 TBD 点路径清单
└── meta                   可选，生成方信息（不参与指纹）
```

## 3. 单位约定

v1 **只接受固定工程单位，不做任何单位换算**。`units` 必须显式写出且逐项相等，
否则视为非法输入并整次生成失败：

| 字段 | 固定值 | 说明 |
|---|---|---|
| `units.length` | `mm` | 全部长度、壁厚、偏心、间隙、缺陷尺寸 |
| `units.density` | `g/cm^3` | 密度场与密度水平 |
| `units.velocity` | `m/s` | 预留给速度类派生量 |

角度一律用 `deg`（字段名自带 `_deg` 后缀），温度为 `K`，时间内部使用 `s`。
写成 `m`、`kg/m^3`、`MPa` 等一律报错，不做猜测性换算。

## 4. 字段语义

类型标记：`num` = 数值或字符串 `TBD`；`int` = 整数；`str` = 文本；`enum` = 限定取值；
`bool` = 布尔。标“必填”的字段缺失即整次失败。

### 4.1 `models[]`

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `id` | `str` | 是 | 模型标识，文件内唯一，建议 `baseline` / `M001` 这类稳定短名 |
| `case_scope` | 映射 | 否 | 绑定到哪些工况；缺省表示全部工况 |
| `case_scope.include` | `[str]` | 否 | 只对这些工况编码生效；缺省不限制 |
| `case_scope.exclude` | `[str]` | 否 | 从生效集合中排除；在 include 之后应用 |
| `units` | 映射 | 是 | 见第 3 节，三项都必须写 |
| `confidence` | 映射 | 否 | 按域声明，缺省 `uncalibrated` |
| `geometry` | 映射 | 是 | 见 4.2 |
| `drawing` | 映射 | 否 | 见 4.3 |
| `density_field` | 映射 | 否 | 见 4.4 |
| `interface` | 映射 | 否 | 见 4.5 |
| `tbd` | `[str]` | 否 | 点路径清单，仅作文档；缺失项由 dynacalc 自行检测 |
| `meta` | 映射 | 否 | 顶层 `meta` 同理；`generated_at`、`generator_version` 等不参与指纹 |

`confidence` 可用键：`geometry` / `drawing` / `density_field` / `interface`；
取值只能是 `public`（公开通用）、`controlled`（受控试验）、`uncalibrated`（未标定假设）。

### 4.2 `geometry`（必填）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `outer_diameter` | `num` | 是 | 管外径，mm |
| `inner_diameter` | `num` | 是 | 装药内径，mm |
| `length` | `num` | 是 | 有效装药 / 管段长度，mm |
| `wall_thickness.nominal` | `num` | 是 | 名义壁厚，mm |
| `wall_thickness.min` | `num` | 否 | 最小壁厚，mm |
| `wall_thickness.max` | `num` | 否 | 最大壁厚，mm |
| `wall_thickness.circumferential` | 映射 | 否 | 壁厚周向分布；见下 |
| `wall_thickness.circumferential.kind` | `enum` | 给了该节时是 | `uniform` / `polyline` / `tbd` |
| `wall_thickness.circumferential.value` | `num` | `uniform` 时是 | 均匀壁厚，mm；**不允许 TBD** |
| `wall_thickness.circumferential.samples` | `[映射]` | `polyline` 时是 | 至少 2 点，按角度递增 |
| `wall_thickness.circumferential.samples[].angle_deg` | `num` | 是 | 周向角，`0 ≤ angle < 360`，严格递增 |
| `wall_thickness.circumferential.samples[].thickness` | `num` | 是 | 该角度壁厚，mm；可为 TBD |
| `eccentricity.radial_mm` | `num` | 否 | 内孔相对外圆的径向偏移，mm |
| `eccentricity.circumferential_mm` | `num` | 否 | 周向偏移（角度方向的分量），mm |
| `eccentricity.angle_deg` | `num` | 否 | 偏移方向角，deg |

一致性要求：`wall_thickness.min ≤ nominal ≤ max`（三者都非 TBD 时校验），
且 `inner_diameter < outer_diameter`（都非 TBD 时校验）；违反即报错。
TBD 值不参与这些比较，视为未定。周向分布与 `min` / `max` 相互独立：
`min` / `max` 仍表示轴向 / 名义范围，周向统计另见 7.1 的派生键。

### 4.3 `drawing`（可选）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `passes` | `[映射]` | 否 | 拉拔道次，按 `index` 升序 |
| `passes[].index` | `int` | 是 | 道次序号，≥1，文件内唯一；指纹中保持整数 |
| `passes[].diameter_reduction` | `num` | 否 | 该道次相对减径率，`[0, 1)`（1.0 视为几何退化，报错） |
| `passes[].wall_thickness_change` | `num` | 否 | 该道次相对壁厚变化率，`> -1` |
| `passes[].die_angle_deg` | `num` | 否 | 模具半角，deg，`(0, 90)` |
| `cumulative_reduction` | `num` | 否 | 累计相对减径率，`[0, 1)`；缺省由道次推导 |

### 4.4 `density_field`（可选）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `axial.kind` | `enum` | 是（给了该节时） | `uniform` / `polyline` / `tbd` |
| `axial.value` | `num` | `uniform` 时是 | 均匀密度，g/cm^3；**不允许 TBD**（未标定请用 `kind: tbd`） |
| `axial.samples` | `[映射]` | `polyline` 时是 | 至少 2 点 |
| `axial.samples[].position` | `num` | 是 | 轴向相对位置，0–1，严格递增，不允许 TBD |
| `axial.samples[].density` | `num` | 是 | 该位置密度，g/cm^3；**可为 TBD**（文件有效，预览方自行标注） |
| `radial` / `circumferential` | 映射 | 否 | 结构同 `axial`；v1 只读取 `kind` |
| `end_loose` | 映射 | 否 | 端部疏松（轴向尾端低密度段），与 `axial` 折线**并列** |
| `end_loose.length_fraction` | `num` | 否 | 疏松段占全长比例，`(0, 1]` |
| `end_loose.density` | `num` | 否 | 疏松段密度，g/cm^3 |
| `local_defects` | `[映射]` | 否 | 局部缺陷列表 |
| `local_defects[].id` | `str` | 是 | 缺陷标识，文件内唯一 |
| `local_defects[].kind` | `enum` | 是 | `void` / `thin_layer` / `crack` / `debond` |
| `local_defects[].location.axial` | `num` | 否 | 轴向相对位置，0–1 |
| `local_defects[].location.radial` | `num` | 否 | 径向相对位置，0–1 |
| `local_defects[].location.angle_deg` | `num` | 否 | 周向角，deg |
| `local_defects[].size_mm` | `num` | 否 | 特征尺寸，mm |
| `local_defects[].severity` | `num` | 否 | 严重度，语义由项目定义（占位可为 TBD） |

### 4.5 `interface`（可选）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `gap_mm` | `num` | 否 | 装药与管壁间隙，mm |
| `debond.state` | `enum` | 否 | `bonded` / `debonded` / `tbd` |
| `debond.extent_deg` | `num` | 否 | 脱粘周向范围，deg |

## 5. TBD 与置信等级

- 未冻结数值统一写字符串 `TBD`（大写），**不要写 `null`、空串、`-1` 或 `0` 代替**。
  `null` / 空值视为必填缺失（必填字段）或非法类型，直接报错。
- 枚举类字段用 `tbd`（小写）表示未定，例如 `density_field.axial.kind: tbd`。
- 置信等级按域声明，缺省 `uncalibrated`；dynacalc 会把各级别数量写进生成摘要。
- `tbd` 清单是给人看的索引，不是权威来源：dynacalc 以实际值为准检测 TBD；
  清单与实际不一致时只给警告，不改变生成结果。

**字段按 TBD 能力分三类**（这是 v1.1 新增的明确分类）：

| 类别 | 规则 | 字段 |
|---|---|---|
| 键必须存在，值可 TBD | 键缺失或写 `null` → 报错；显式写 `TBD` → 合法，且不参与几何一致性比较 | `geometry.outer_diameter` / `inner_diameter` / `length`、`geometry.wall_thickness.nominal` |
| 必须给出具体值 | 缺失或 `TBD` 都报错 | `id`、`units.*`、每个 `*.kind`、`samples[].position`、`samples[].angle_deg`、`local_defects[].id` / `kind`、`passes[].index`、`uniform` 的 `value`（含 `wall_thickness.circumferential.value`） |
| 可留 TBD | 可写 `TBD`；也可整键省略（省略与显式 TBD 等价） | 除上表外的全部 `num`，包括 `wall_thickness.min/max`、`eccentricity.*`、`gap_mm`、`debond.extent_deg`、`passes[].diameter_reduction`、`wall_thickness_change`、`die_angle_deg`、`cumulative_reduction`、`axial.samples[].density`、`circumferential.samples[].thickness`、`end_loose.*`、`local_defects[].size_mm` / `severity` |

**预览最小必填集不属于文件有效性**：paramcad 出二维截面 / 轴向剖面时至少需要
`outer_diameter`、`inner_diameter`、`wall_thickness.nominal` 为具体值；`length` 与
密度值可以是 TBD。解析层必须接受合法但含 TBD 的文件（不得因「画不出来」就报
schema 错误）；预览层把 TBD 显示为「未标定 / 占位」即可。若 paramcad 选择在
导出前强制补齐，那是它自己的业务校验，不是本契约的合法性条件。

## 6. 工况绑定（`case_scope`）

1. 生效集合 = `include`（缺省为全部工况）减去 `exclude`。
2. 一个工况最多匹配一个模型。匹配到多个 → **报错**并列出工况编码与全部命中的
   `model.id`，由调用方收敛 `case_scope`。
3. 没有匹配模型 → 不是错误：该工况使用参数矩阵的值；缺失键写 `TBD`。
4. `include` / `exclude` 中出现生成清单里不存在的工况编码 → **报错**（防止拼写
   错误导致模型静默不生效）。
5. 同一文件内 `model.id` 重复 → 报错。
6. **校验分层**：第 1–2、5 条与类型 / 区间检查可脱离工况清单完成（paramcad 可自行
   做结构校验）；第 3–4 条（未知编码、互斥）**必须知道完整工况编码集合**。paramcad
   若要在导出前预检，需由调用方传入清单；否则把这两项留给 dynacalc 在
   `dynacalc model --file <model> --params <params>` / `generate` 时判定。
   两种方式结果必须一致，rule 归 dynacalc 所有，paramcad 不重复实现第二套语义。

## 7. 映射到 deck 占位符

### 7.1 派生键

dynacalc 把模型解析成下列扁平键，模板可写成 `${键}`（必填）或 `${键?}`（可选，
缺失写 TBD）：

| 派生键 | 来源 | 缺失 / TBD 时 |
|---|---|---|
| `model_id` | `models[].id` | 无模型时为 `""` |
| `model_digest` | 模型指纹（第 8 节） | 无模型时为 `""` |
| `outer_diameter` | `geometry.outer_diameter` | 回落矩阵同名因子，再回落 TBD |
| `inner_diameter` | `geometry.inner_diameter` | 同上 |
| `length` | `geometry.length` | 同上 |
| `wall_thickness` | `geometry.wall_thickness.nominal` | 同上 |
| `wall_thickness_min` | `geometry.wall_thickness.min` | 缺失 → TBD |
| `wall_thickness_max` | `geometry.wall_thickness.max` | 缺失 → TBD |
| `wall_thickness_circumferential_kind` | `wall_thickness.circumferential.kind` | 缺失 → `tbd` |
| `wall_thickness_circumferential_min` / `max` / `mean` | 周向壁厚样本统计 | 缺失或任一样本 TBD → TBD |
| `wall_thickness_circumferential_curve` | `角度:壁厚` 以 `;` 连接（`0:14.8;180:15.2`） | 缺失或任一样本 TBD → TBD |
| `eccentricity_radial` | `geometry.eccentricity.radial_mm` | 缺失 → TBD |
| `eccentricity_circumferential` | `geometry.eccentricity.circumferential_mm` | 缺失 → TBD |
| `eccentricity_angle_deg` | `geometry.eccentricity.angle_deg` | 缺失 → TBD |
| `gap_mm` | `interface.gap_mm` | 回落矩阵同名因子 `gap_mm`，再 TBD |
| `debond_state` | `interface.debond.state` | 缺失 → TBD |
| `debond_extent_deg` | `interface.debond.extent_deg` | 缺失 → TBD |
| `drawing_passes` | `drawing.passes` 数量 | 缺失 → TBD |
| `drawing_reduction_total` | `cumulative_reduction`，否则按道次推导 | 缺失 → TBD |
| `drawing_wall_change_total` | 各道次 `wall_thickness_change` 之和 | 缺失 → TBD |
| `density_axial_kind` | `density_field.axial.kind` | 缺失 → `tbd` |
| `density_axial_min` / `max` / `mean` | 轴向密度样本统计 | 任一样本 TBD → TBD |
| `density_end_loose_length_fraction` | `density_field.end_loose.length_fraction` | 缺失 → TBD |
| `density_end_loose_density` | `density_field.end_loose.density` | 缺失 → TBD |
| `density_radial_kind` / `density_circumferential_kind` | 对应 `kind` | 缺失 → `tbd` |
| `defect_count` | `local_defects` 数量 | 缺失 → `0` |
| `defect_ids` | 缺陷 `id` 逗号连接 | 缺失 → `""` |

推导规则（全部按 IEEE 双精度，输出用最短往返表示）：

- `drawing_reduction_total`：优先用 `cumulative_reduction`；否则
  `1 - Π(1 - diameter_reduction_i)`；任一道次该项为 TBD / 缺失 → TBD。
- `drawing_wall_change_total`：`Σ wall_thickness_change_i`；任一为 TBD / 缺失 → TBD。
- 轴向密度统计：`uniform` → 三者都等于 `value`；`polyline` → 对 `samples[].density`
  取 min / max / 算术平均；`tbd` 或任一点 TBD → 三者都是 TBD。
- 周向壁厚统计：`uniform` → min / max / mean 都等于 `value`；`polyline` → 对
  `samples[].thickness` 取 min / max / 算术平均；`tbd` 或任一点 TBD → 三者都是 TBD。
  曲线文本按样本原顺序输出，数值用最短往返浮点表示（`repr(float(...))`），
  分隔符固定为 `;` 与 `:`，不写角度单位。

### 7.2 覆盖优先级

对同名键（如 `inner_diameter`、`gap_mm`）：

1. 模型的**非 TBD** 值优先；
2. 模型值为 TBD / 字段缺失 → 回落参数矩阵中的同名因子；
3. 矩阵也没有 → 写 `TBD`。

这样 paramcad 给出的单工况几何会覆盖矩阵里的几何水平，但模型未标定的项仍由矩阵
（或 TBD）承担，不会出现「模型缺失就变成默认材料参数」。

### 7.3 指纹

- 模型指纹 `model_digest`：对第 8 节的规范化载荷做 SHA-256，输出 64 位十六进制。
  不含 `meta`、`tbd`、`case_scope`（这三项不影响 deck 内容）。
- 工况指纹 `case_fingerprint`：在原有载荷（schema、编码、分组、水平、解析值、模板
  摘要）上追加 `model_digest` 后取 SHA-256。模型内容不同 → deck 目录不同。

## 8. 参数指纹算法

`model_digest = SHA-256(canonical_json(payload_normalized))`，其中：

- `payload_normalized`：模型的功能字段（`id`、`units`、`confidence`、`geometry`、
  可选的 `drawing`、`density_field`、`interface`），**按 8.1 的归一化规则**处理；
  丢弃 `meta`、`tbd`、`case_scope` 与未知字段。
- `canonical_json`：`json.dumps(payload, ensure_ascii=False, sort_keys=True,
  separators=(",", ":"))`，所有数值先转 Python `float`（整数 `60` 写成 `60.0`），
  字符串按 UTF-8。
- 十六进制小写，保留全部 64 位；展示与目录名可截前 8 / 12 位，但比较必须用全值。
- 空白、注释、键顺序、YAML 引号风格不影响指纹。

### 8.1 归一化细则（逐条对拍用）

1. **始终存在**：`id`、`units`（三项固定值）、`confidence`（四个域全部展开，缺省
   `uncalibrated`）、`geometry`。`confidence` 的域固定为 `geometry` / `drawing` /
   `density_field` / `interface`，即使某域没有内容也写默认值。
2. **可选段省略**：`drawing`、`density_field`、`interface` 在其内容为空时**不写键**。
   「空」的判定：
   - `drawing`：没有 `passes` 且 `cumulative_reduction` 为 TBD / 缺失；
   - `density_field`：没有任何轴、没有 `end_loose`、没有 `local_defects`；
   - `interface`：`gap_mm` 为 TBD、`debond` 为空。
3. **可选字段为 TBD 时不写键**（等价于缺失），包括 `wall_thickness.min/max`、
   `eccentricity` 的三个分量、`gap_mm`、`debond.extent_deg`、每个道次的可选参数、
   `local_defects[].size_mm/severity`、`end_loose` 的两个字段。
   若 `eccentricity` 三个分量全为 TBD，则整个 `eccentricity` 键省略。
4. **枚举 `tbd` 等于省略**：`density_field.<axis>.kind: tbd` 归一化后该轴不存在；
   `debond.state: tbd` 时 `debond.state` 键不写（若 `extent_deg` 也为 TBD，整个
   `debond` 省略）。
5. **`uniform` 的 `value` 必须是具体数值**，写进载荷为 `float`；`value: TBD` 是
   非法输入（见第 5 节），不使用「省略成 tbd」的兜底。
6. **折线样本**：保留原始顺序（`samples[].position` 已保证严格递增）；`position`
   与 `density` 都转 `float`；`density` 允许 `"TBD"` 字符串，原样参与指纹。
7. **道次序**：按 `index` 升序写入；`index` **保持整数**（不转 `float`），其余
   数值转 `float`。
8. **`cumulative_reduction`**：存在具体值时原样写入；否则按
   `1 - Π(1 - reduction_i)` 推导并以 `float` 写入；推不出时**省略该键**（不写
   `TBD` 字符串）。
9. **局部缺陷**：`id`、`kind` 必写；`location` 仅在该对象至少一个分量为具体值
   时写入，且对象内只写具体分量；`size_mm` / `severity` 仅在具体值时写入。
10. **`end_loose`**：只写具体字段，两个字段都为 TBD 时整节省略。
11. **周向壁厚**：`circumferential.kind` 为 `tbd` 时整节省略；`uniform` 写
    `{kind, value}`；`polyline` 写 `{kind, samples}`，样本只含 `angle_deg` 与
    `thickness`（`thickness` 可为 `"TBD"` 字符串）。
12. **浮点表示**：`json.dumps` 使用 Python 的最短往返表示，不做四舍五入或格式化；
    因此 `0.08` 推导出的累计减径率写作 `0.07999999999999996`，两边必须一致。

同一模型内容在任何机器、任何 YAML 排版下都应得到同一指纹；两侧用第 12 节黄金
向量逐字节对拍。

## 9. paramcad 提供什么 / dynacalc 回写什么

paramcad 提供：

- 一个或多个 `dynacalc-model/1` YAML 文件（可含多个模型与 `case_scope`）；
- 模型预览所需的几何与场数据（dynacalc 不使用预览图，也不读取网格）；
- 每个模型的置信等级与 TBD 清单。

dynacalc 回写：

- **不修改、不回写模型文件**；
- 在输出根目录 `manifest.json` 的 `cases[]` 中记录 `model_id` 与 `model_digest`；
- 在每个工况目录 `case.json` 的 `model` 块记录 `id` / `digest` / `source`；
- 在生成摘要中统计「绑定模型的工况数 / 回落矩阵的工况数 / 模型 TBD 字段数」。

paramcad 若需要知道绑定结果，读取 `manifest.json` 即可，不需要 dynacalc 反向写模型。

## 10. 失败语义与版本演进

### 10.1 报错（整次生成失败，不写半成品）

| 场景 | 行为 |
|---|---|
| YAML 语法错误 / 顶层不是映射 | 报错，指明文件 |
| `schema` 缺失或不是 `dynacalc-model/1` | 报错，指明期望版本 |
| `models` 缺失 / 空 / 含非映射项 | 报错 |
| `model.id` 缺失、非文本、文件内重复 | 报错 |
| `units` 缺失或任一项不等于固定值 | 报错，列出字段与期望值 |
| 必填字段缺失、`null`、类型不符（如把数写成列表） | 报错，带字段路径 |
| `TBD` 出现在枚举 / 文本字段 | 报错 |
| 枚举取值未知 | 报错，列出允许值 |
| 壁厚范围与名义值矛盾、内径 ≥ 外径 | 报错 |
| 轴向样本位置非 0–1、非严格递增、少于 2 点 | 报错 |
| 减径率 / 累计减径率不在 `[0, 1)`、道次 `die_angle_deg` 不在 `(0, 90)` | 报错 |
| 周向壁厚样本角度不在 `[0, 360)`、非严格递增、少于 2 点 | 报错 |
| `end_loose.length_fraction` 不在 `(0, 1]` | 报错 |
| `uniform` 的 `value` 写成 `TBD` | 报错（改用 `kind: tbd`） |
| 同一工况匹配多个模型 | 报错，列出工况与模型 id |
| `include` / `exclude` 引用不存在的工况编码 | 报错 |
| 未知顶层 / 模型字段 | **忽略并警告**（向前兼容） |

### 10.2 降级（不报错）

| 场景 | 行为 |
|---|---|
| 工况没有匹配模型 | 回落矩阵值；缺失键写 TBD |
| 模型字段为 TBD | 回落矩阵同名因子，仍无则 TBD |
| `tbd` 清单与实际不符 | 警告，以实际值为准 |
| 径向 / 周向场给了完整样本但 v1 只读 `kind` | 只读 `kind`，不报错 |

### 10.3 版本演进

- 同一 `dynacalc-model/1` 内只做**加法**：新增可选字段；消费方忽略未知字段。
  已定义字段不改名、不改语义、不改单位。
- **v1.1（2026-11-13）** 属于发布前的加法修订：新增 `density_field.end_loose`、
  `geometry.wall_thickness.circumferential` 两组可选字段，补全 TBD 分类与归一化
  细则。旧文件不需要改动，指纹只在用到新字段时变化。
- **区间澄清**：`diameter_reduction` / `cumulative_reduction` 的合法区间由
  「0–1」明确为 `[0, 1)`。`1.0`（100% 减径）是几何退化输入，此前双方都未真正支持
  （paramcad 已按 `[0, 1)` 拒绝），因此按发布前澄清处理，schema 字符串不升版；
  若发现历史文件写了 `1.0`，两侧都会报错，需要把值改为 `< 1` 或补全真实道次。
- 任何破坏性变更（改单位、改必填、改字段含义、删除字段）→ 新版本
  `dynacalc-model/2`；dynacalc 遇到不认识的 schema 直接报错，不做猜测性兼容。
- 弃用字段保留至少一个版本，并在消费侧给出警告后再移除。
- 指纹算法属于契约的一部分：改算法必须同步升主版本，否则跨版本指纹不可比。

## 11. 示例

最小可用模型（全部几何已冻结，无拉拔 / 场信息）：

```yaml
schema: dynacalc-model/1
models:
  - id: baseline
    units: {length: mm, density: g/cm^3, velocity: m/s}
    geometry:
      outer_diameter: 60.0
      inner_diameter: 30.0
      length: 300.0
      wall_thickness: {nominal: 15.0}
```

按工况绑定 + TBD + 密度场：

```yaml
schema: dynacalc-model/1
meta: {generator: paramcad, generator_version: "0.1.0"}
models:
  - id: M001-geometry
    case_scope: {include: [M001]}
    units: {length: mm, density: g/cm^3, velocity: m/s}
    confidence: {geometry: controlled, density_field: uncalibrated}
    geometry:
      outer_diameter: 60.0
      inner_diameter: 30.0
      length: 300.0
      wall_thickness: {nominal: 15.0, min: 14.2, max: 15.8}
      eccentricity: {radial_mm: 0.15, circumferential_mm: 0.0, angle_deg: 0.0}
    drawing:
      passes:
        - {index: 1, diameter_reduction: 0.08, wall_thickness_change: -0.03, die_angle_deg: 12.0}
    density_field:
      axial:
        kind: polyline
        samples:
          - {position: 0.0, density: 1.62}
          - {position: 0.5, density: 1.65}
          - {position: 1.0, density: 1.60}
      local_defects:
        - id: D1
          kind: void
          location: {axial: 0.4, radial: 0.9, angle_deg: 30}
          size_mm: 2.0
          severity: TBD
    interface:
      gap_mm: 0.05
      debond: {state: bonded, extent_deg: 0.0}
    tbd: ["density_field.local_defects[0].severity"]
```

v1.1 新增可选字段（端部疏松 + 周向壁厚）：

```yaml
schema: dynacalc-model/1
models:
  - id: additive
    units: {length: mm, density: g/cm^3, velocity: m/s}
    geometry:
      outer_diameter: 60.0
      inner_diameter: 30.0
      length: 300.0
      wall_thickness:
        nominal: 15.0
        min: 14.0
        max: 16.0
        circumferential:
          kind: polyline
          samples:
            - {angle_deg: 0, thickness: 14.8}
            - {angle_deg: 180, thickness: 15.2}
    density_field:
      end_loose:
        length_fraction: 0.1
        density: 1.45
    interface:
      gap_mm: 0.05
      debond: {state: tbd}
```

## 12. 黄金指纹向量

以下向量供两侧逐字节对拍。`canonical` 即 `canonical_json(payload_normalized)` 的
完整输出（一行、键排序、无空格），`digest` 为其 UTF-8 字节的 SHA-256。

### 12.1 示例模型（第 11 节第二段）

- digest：`ce29bedba34f6bc6fcc3916ba9912c46da3f008b7cd4e8c23b6ae8098f5cf5b1`
- canonical：

```text
{"confidence":{"density_field":"uncalibrated","drawing":"uncalibrated","geometry":"controlled","interface":"uncalibrated"},"density_field":{"axial":{"kind":"polyline","samples":[{"density":1.62,"position":0.0},{"density":1.65,"position":0.5},{"density":1.6,"position":1.0}]},"local_defects":[{"id":"D1","kind":"void","location":{"angle_deg":30.0,"axial":0.4,"radial":0.9},"size_mm":2.0}]},"drawing":{"cumulative_reduction":0.07999999999999996,"passes":[{"diameter_reduction":0.08,"die_angle_deg":12.0,"index":1,"wall_thickness_change":-0.03}]},"geometry":{"eccentricity":{"angle_deg":0.0,"circumferential_mm":0.0,"radial_mm":0.15},"inner_diameter":30.0,"length":300.0,"outer_diameter":60.0,"wall_thickness":{"max":15.8,"min":14.2,"nominal":15.0}},"id":"M001-geometry","interface":{"debond":{"extent_deg":0.0,"state":"bonded"},"gap_mm":0.05},"units":{"density":"g/cm^3","length":"mm","velocity":"m/s"}}
```

### 12.2 最小模型（第 11 节第一段）

- digest：`d583539119054ec21c73c78fc0d7a911b015dc9b59b1d8e2c0870b02abe08d5d`
- canonical：

```text
{"confidence":{"density_field":"uncalibrated","drawing":"uncalibrated","geometry":"uncalibrated","interface":"uncalibrated"},"geometry":{"inner_diameter":30.0,"length":300.0,"outer_diameter":60.0,"wall_thickness":{"nominal":15.0}},"id":"baseline","units":{"density":"g/cm^3","length":"mm","velocity":"m/s"}}
```

### 12.3 v1.1 新增字段（第 11 节第三段，暂定）

- digest：`6ced6d37a78466e473bc451af32a1e42a4d74aa313d043e586a938169908fc72`
- canonical：

```text
{"confidence":{"density_field":"uncalibrated","drawing":"uncalibrated","geometry":"uncalibrated","interface":"uncalibrated"},"density_field":{"end_loose":{"density":1.45,"length_fraction":0.1}},"geometry":{"inner_diameter":30.0,"length":300.0,"outer_diameter":60.0,"wall_thickness":{"circumferential":{"kind":"polyline","samples":[{"angle_deg":0.0,"thickness":14.8},{"angle_deg":180.0,"thickness":15.2}]},"max":16.0,"min":14.0,"nominal":15.0}},"id":"additive","interface":{"gap_mm":0.05},"units":{"density":"g/cm^3","length":"mm","velocity":"m/s"}}
```

说明：12.1 与 12.2 已于 2026-11-13 在 dynacalc / paramcad 两侧交叉验证逐字节一致；
12.3 因 paramcad 尚未实现新增字段，先由 dynacalc 给出，待两侧实现后补做对拍。

## 13. paramcad 侧验收清单

- [ ] 生成的 YAML 能被 `dynacalc model --file <file>` 校验通过；
- [ ] `model --file` 输出的模型指纹与 paramcad 自算结果逐字符一致（至少覆盖 12.1、12.2）；
- [ ] 缺 `units`、写错单位、必填缺失、`null`、枚举未知四类输入都能得到明确报错；
- [ ] `case_scope` 覆盖、互斥、未知编码三种情况行为符合第 6 节；
- [ ] 同一模型内容改排版（键序、注释、引号）后指纹不变；
- [ ] 含 TBD 的模型生成 deck 时，TBD 原样保留且被计入统计；
- [ ] 可读取并保留 `end_loose` 与 `circumferential`，导出后 dynacalc 能映射到派生键。
