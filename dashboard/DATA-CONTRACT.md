# 本地数据契约 v1

本文只定义实时输入和模型输出的规范性字段、约束及兼容性。后端接入步骤见 [DEVELOPMENT.md](DEVELOPMENT.md)，页面操作见 [README.md](README.md)。机器校验以 `api/schemas.py` 和运行中的 `/openapi.json` 为准。

## 通用规则

- 顶层必须包含 `schema_version: 1` 和 `kind`。
- schema 为严格模式：未知字段被拒绝，字符串去除首尾空白，NaN 与无穷值被拒绝。
- ID 长度为 1–80，只能使用字母、数字、下划线和连字符。
- 时间必须是带时区 ISO 8601，例如 `2026-09-10T10:30:00+08:00` 或 `Z`；存储时标准化为 UTC。
- 一个 JSON 批次最多 10,000 条观测或预测。input 批次还最多包含 1,000 位患者。
- 同一 `record_id + revision` 必须始终对应相同内容；更正时递增 revision。
- 缺少来源 `available_at` 时，后端使用本机首次接收时间，并记录 `availability_basis=local_received`。

## InputBatch

顶层结构：

```json
{
  "schema_version": 1,
  "kind": "input",
  "patients": [],
  "observations": [],
  "actor": "local-user"
}
```

`actor` 用于本地审计，写入交换归档前会移除。

### Patient

| 字段 | 类型 | 规则 |
|---|---|---|
| `patient_id` | ID | 当前数据库患者主键 |
| `encounter_id` | ID | 住院实例；必须与该患者后续记录一致 |
| `name` | 文本 | 1–120 字符 |
| `icu_admitted_at` | 带时区时间 | 不得晚于本机接收时间 |
| `bed` | 文本 | 可选，最多 40 字符 |
| `note` | 文本 | 可选，最多 500 字符 |

已有 patient_id 若对应不同资料，整个批次被拒绝。当前一个 patient_id 只对应一个 encounter_id。

### Observation

| 字段 | 类型 | 规则 |
|---|---|---|
| `record_id` | ID | 来源系统中的稳定记录 ID |
| `revision` | 整数 | 从 1 开始 |
| `patient_id` | ID | 必须已登记 |
| `encounter_id` | ID | 必须与患者记录一致 |
| `metric` | 文本 | 稳定变量代码 |
| `label` | 文本 | 页面显示名 |
| `unit` | 文本 | 最多 40 字符，可为空 |
| `value` | 数值 | 必须有限 |
| `measured_at` | 带时区时间 | 实际测量时间 |
| `available_at` | 带时区时间或 null | 来源能够证明的实际可用时间 |

时间必须满足：

```text
icu_admitted_at ≤ measured_at ≤ available_at ≤ 本机接收时刻
```

缺少 available_at 时，后端用接收时刻补充。不要把录入时间写成 measured_at。变量临床范围、单位换算、采样间隔、插值和缺失处理尚未统一，必须由采集程序和模型预处理共同约定。

合成格式示例：

```json
{
  "schema_version": 1,
  "kind": "input",
  "patients": [
    {
      "patient_id": "example_001",
      "encounter_id": "example_stay",
      "name": "示例患者",
      "icu_admitted_at": "2020-01-01T00:00:00Z"
    }
  ],
  "observations": [
    {
      "record_id": "example_obs_1",
      "revision": 1,
      "patient_id": "example_001",
      "encounter_id": "example_stay",
      "metric": "example_measure",
      "label": "示例观测",
      "unit": "example-unit",
      "value": 1.2,
      "measured_at": "2020-01-01T01:00:00Z",
      "available_at": "2020-01-01T01:05:00Z"
    }
  ]
}
```

## PredictionBatch

顶层结构：

```json
{
  "schema_version": 1,
  "kind": "prediction",
  "predictions": []
}
```

### Prediction 必需字段

| 类别 | 字段 |
|---|---|
| 记录 | `record_id`、`revision` |
| 关联 | `patient_id`、`encounter_id`、`input_revision`、`input_fingerprint` |
| 模型 | `model_id`、`model_version`、`target` |
| 时间 | `origin_time`、`data_cutoff`、`horizon_end`、`generated_at` |
| 输出 | `risk` |

`available_at` 可选。约束：

- `input_revision ≥ 0`。
- `input_fingerprint` 是 64 位小写十六进制 SHA-256。
- `0 ≤ risk ≤ 1`。
- `data_cutoff ≤ origin_time < horizon_end`。
- `generated_at ≥ origin_time`。
- 存储时还要求 `generated_at ≤ available_at ≤ 本机接收时刻`；缺少 available_at 时以接收时刻为准。
- 患者与 encounter 必须匹配。
- 指纹必须等于 generated_at 时已可用、且 measured_at 不晚于 data_cutoff 的完整输入快照。

`input_revision` 用于任务合并和追踪；跨电脑或历史截止点的一致性以 input_fingerprint 为准。指纹由后端 snapshot 接口生成，接入方不应复制规范化算法。

### 可选字段

#### data_confidence

`data_confidence` 范围 0–1。提供时必须同时提供非空 `confidence_definition`。它表示模型或质量模块定义的数据可信度，不是预测正确率，也不改变 risk。

#### drivers

最多 100 项。每项包含：

- `feature`：稳定特征代码。
- `label`：显示名称。
- `contribution`：模型原始贡献数值。
- `value`、`unit`：可选的当时特征值。

不同模型的 contribution 当前不做统一尺度转换。

#### trajectories

最多 30 个系列，每个系列最多 10,000 点：

- 系列：`metric`、`label`、`unit`。
- 点：`time`、`value`，可选 `lower`、`upper`。
- 点必须按时间有序，并位于 `origin_time` 到 `horizon_end` 内。
- 若有区间，必须满足 `lower ≤ value ≤ upper`。

前端当前显示轨迹线，区间字段已保留但尚未绘制区间带。

#### stability_assessment_id

关联后续不同数据截止时间稳定性评估的版本化产物。当前只保留字段，不生成稳定性结论。

#### threshold

| 字段 | 规则 |
|---|---|
| `value` | 0–1 |
| `comparison` | `>=` 或 `>` |
| `validation_run_id` | validation 实验标识 |
| `policy_version` | 预警策略版本 |
| `monitoring_window` | 适用监测窗口 |
| `calibration_version` | 校准版本 |
| `locked` | 是否已经锁定 |
| `selection_basis` | 阈值选择依据，最多 2,000 字符 |
| `stability_assessment_id` | 可选的稳定性评估关联 |

threshold 随具体模型、版本、目标、horizon 和校准策略提供，不是全局常数。缺失 threshold 或 `locked=false` 时，前端不得派生 AKI 警告。结构校验不能证明 validation 结论真实有效。

## 输入快照指纹

指纹内容为：

```json
{"patient_id":"...","observations":[...]}
```

观测取 generated/as_of 时已可用的每个 record_id 的最高修订，并限制 measured_at 不晚于 cutoff；按 record_id 排序，移除内部 `availability_basis`，使用 UTF-8、键排序和紧凑 JSON 后计算 SHA-256。

规范化实现属于后端。调用：

```text
GET /api/patients/{id}/snapshot?as_of=<生成时已可用时刻>&cutoff=<数据截止时刻>
```

导入预测时会重新计算并验证。当前输入变化后，旧预测仍可进入历史，但会在当前结果中标为待更新。

## 版本兼容性

- v1 的未知字段被拒绝。
- 新增可选字段时必须验证现有 v1 文件仍可读取。
- 删除、重命名、改变单位或时间含义通常需要新的 schema_version 和迁移方案。
- JSON/ZIP 文件通道、模型任务调度、HTTP 分页及 UI 行为属于开发指南，不在本文重复。
