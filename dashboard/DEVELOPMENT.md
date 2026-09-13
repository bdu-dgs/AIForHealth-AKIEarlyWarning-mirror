# 网站开发与后端接入指南

本文只负责网站架构、后端接入流程和修改规范。安装与日常操作见 [README.md](README.md)；JSON 字段的规范性定义见 [DATA-CONTRACT.md](DATA-CONTRACT.md)；测试结果见 [VALIDATION.md](VALIDATION.md)。

当前网站由一个本地 FastAPI 进程提供 React 页面、HTTP API、SSE 更新和 SQLite 数据层。当前没有 AKI 推理实现。

## 文档维护规则

同一事实只在一份主文档中完整维护，其他位置使用链接和一句必要摘要：

| 信息 | 唯一主文档 |
|---|---|
| 研究目标、候选模型方案、notebook 和研究产物 | 仓库根 `README.md` |
| 用户如何安装、启动和操作网站 | `dashboard/README.md` |
| 模块职责、接入顺序、API 目录和开发流程 | 本文 |
| JSON 字段、类型、时间等式、修订和指纹算法 | `DATA-CONTRACT.md` |
| 某项检查何时运行、结果及不能证明什么 | `VALIDATION.md` |
| 接手时旧总结与仓库的差异 | `HANDOFF-CHECK.md` |

更新文档时遵循：

- 字段改变时修改 schema 和 DATA-CONTRACT；本文只更新接入流程。
- 新测试结果只追加到 VALIDATION；README 不维护通过数量。
- 页面操作变化只修改网站 README；本文只记录实现约定。
- 研究候选方案变化只修改根 README；不能把候选方案写成网站硬限制。
- 已完成事项从待办中移除；历史差异保留在 HANDOFF-CHECK。
- “计划”“已实现”“已测试”“已交付”必须使用与证据一致的措辞。

## 必须保持的系统边界

- React 负责录入、查询和显示；模型计算通过版本化契约接入。
- 所有观测按追加和修订保存，历史回放只读。
- 测量时间、来源可用时间、本机接收时间、预测起点和生成时间不可混用。
- 没有模型输出时保持空状态；不得生成演示、规则或随机风险。
- 只有带完整 validation 元数据且 `locked=true` 的阈值才能产生 AKI 警告。
- 本地实时工作流与 MIMIC CSV 预览相互隔离。
- 不提交本地数据库、MIMIC 文件、照片、模型、患者级产物或凭据。
- Windows 运行包只在用户明确要求后构建。

## 模块分工

| 位置 | 责任 |
|---|---|
| `api/schemas.py` | 输入、观测、预测、阈值、贡献和轨迹的机器校验 |
| `api/storage.py` | SQLite 事务、修订、时间可见性、指纹和 outbox |
| `api/watcher.py` | 监听 JSON、稳定文件检查、重试和错误摘要 |
| `api/exchange.py` | JSON/ZIP 导入导出和分卷 |
| `api/model_adapter.py` | 模型进程的接口边界；当前仅定义 Protocol |
| `api/dataset_preview.py` | 只读 MIMIC 子集预览 |
| `api/main.py` | HTTP、SSE、本地请求保护和编译后页面 |
| `web/lib/api.ts` | 前端共享类型、请求、历史分页和告警判定 |
| `web/components/clinical/` | 患者卡片、详情、录入、预览和曲线 |
| `web/app/page.tsx` | 页面路由、总览状态和实时更新 |

新增功能应进入对应模块。模型预处理不能写进 React，模型进程不能直接修改 SQLite。

## 后端输入通道

### 网页与 HTTP

网页向 `POST /api/import` 提交 `input` 或 `prediction` 批次。`POST /api/import-file` 接受本站 JSON/ZIP。一个批次在同一 SQLite 事务中校验，失败时不会部分写入。

主要查询接口：

| 接口 | 用途 |
|---|---|
| `GET /api/health` | 服务、修订号、监听错误、outbox 状态 |
| `GET /api/settings` | 数据和交换目录、模型状态 |
| `GET /api/patients` | 总览 |
| `GET /api/patients/{id}/history` | 历史与回放 |
| `GET /api/patients/{id}/snapshot` | 指定时刻和截止点的输入指纹 |
| `GET /api/patients/{id}/current-predictions` | 当前模型结果 |
| `GET /api/patients/{id}/quality` | 新鲜度和数据密度统计 |
| `GET /api/events` | SSE 修订通知 |
| `GET /api/datasets/icu-preview` | 只读 CSV 预览 |
| `GET /api/patients/{id}/export-plan/{kind}` | 导出分卷计划 |
| `GET /api/patients/{id}/export/{kind}` | 下载输入或预测分卷 |

运行中的 `/openapi.json` 是 HTTP 请求结构的机器可读入口。服务只监听回环地址并限制 Host，浏览器写请求还会检查 Origin。若未来允许其他电脑访问，必须先设计认证、权限、TLS、审计和部署方式，不能只改成监听 `0.0.0.0`。

### 外部程序写文件

外部程序可将完整 JSON 原子写入：

```text
<data-root>/inbox/input/*.json
<data-root>/inbox/prediction/*.json
```

生产者必须：

1. 先写同目录临时文件，完成后原子重命名为 `.json`。
2. 使用稳定 `record_id`；更正时递增 `revision`。
3. 允许同一批次幂等重试。
4. 保证顶层 `kind` 与目录一致。
5. 单文件不超过 16 MB。
6. 不通过删除数据库、`accepted` 或 outbox 来清理错误。

监听器由文件事件唤醒，并约每秒补扫；只处理稳定文件。失败文件不会替换有效记录，错误摘要从 health 接口读取。

## 实时观测接入

字段和完整 JSON 示例只在 [DATA-CONTRACT.md](DATA-CONTRACT.md) 维护。接入程序还需遵守以下流程规则：

- 患者必须先登记，观测的患者与 `encounter_id` 必须一致。
- 当前一个 `patient_id` 只对应一个住院实例；再次住院需新的唯一条目标识，正式一人多次住院尚未实现。
- 实时时间使用带时区 ISO 8601，并在存储层标准化为 UTC。
- 缺少来源 `available_at` 时，后端使用首次接收时刻，确保补录数据不会出现在更早的回放中。
- 变量代码、单位、临床范围、换算、插值和缺失处理由采集与模型预处理共同定义；当前后端不替代这些规则。

成功输入后，事务写入 SQLite 和 durable outbox。outbox 再生成 `accepted/input` 归档及最新的患者模型请求。文件写入失败时 outbox 保留并重试。

## 模型工作进程

输入变化后，后端更新：

```text
<data-root>/requests/{patient_id}.json
```

请求文件表示“等待处理”，包含患者、住院、输入版本、最新指纹，以及历史和输入导出接口位置。模型工作进程按以下流程接入：

1. 监视或轮询 requests。
2. 按患者合并任务，只保留最新输入版本。
3. 读取完整可见历史并选择 `data_cutoff`、`origin_time` 和 horizon。
4. 调用 snapshot 接口取得该截止点的 `input_fingerprint`。
5. 执行预处理、推理、校准和解释。
6. 写出前再次检查请求版本，丢弃被新输入取代的任务。
7. 原子写入 prediction inbox，或调用 import API。
8. 分别记录排队、读取、预处理、推理、解释、写入和网页刷新耗时。

不要使用请求文件中的最新指纹代表较早的截止点。历史截止点必须调用：

```text
GET /api/patients/{id}/snapshot?as_of=<生成时已可用的时刻>&cutoff=<数据截止时刻>
```

后端会重新计算指纹并拒绝不匹配的预测。新观测到达后，网页会把仍引用旧输入的结果标为待更新。

结果的字段、时间约束和可选解释项以数据契约为准。前端按模型、版本、目标和实际 horizon 分组，不限制半小时、一小时或 8/12/24 小时。模型进程应取消过期任务，避免旧队列拖延当前结果。

## 历史回放

history 接口使用：

- `start/end`：事件时间范围。
- `as_of`：当时已经可用的信息边界。
- `kind`：observation 或 prediction。
- `offset/limit`：分页。

存储层先选择 `as_of` 时已经可见的最高修订，再按事件时间过滤，避免修订后旧版本重新出现。查询和滑动回放不触发模型计算。

前端每类历史最多加载 24,000 条，缓存有界；更大范围应分页、缩短窗口或导出。图表降采样只用于显示，不可作为模型输入或导出数据。

## 前端约定

前端统一通过 `web/lib/api.ts` 请求 `/api`。后端 schema 改动时同步 TypeScript 类型。SSE 只传全局 revision，不传患者数据；页面收到新版本后重新查询。

患者总览保持一位患者一张横向卡片：

- 左侧：图片或姓名替代图、患者信息、提醒。
- 中间：观测曲线。
- 右侧：预测风险或轨迹。
- 点击卡片：进入详情，按需加载更长历史。

风险图上红下绿；显眼程度可参考模型提供的 `data_confidence`，概率数值保持原值。缺失阈值、阈值未锁定、输入变化和窗口结束必须显示不同状态。

## CSV 预览的开发边界

操作方法和当前字段映射见 [README.md](README.md)。实现上必须保持：

- 三个 ID 联合匹配，同患者不同住院不合并。
- 无时区源时间原样显示，只在同一 ICU stay 内计算相对小时。
- 不写 SQLite，不生成患者登记、风险、阈值或可信度。
- 每次请求重新读取小型子集；完整 MIMIC 数据应走独立离线提取和分页产物。
- 错误响应不回显源行、原始值或本地完整路径。

## 修改数据契约

修改契约时依次完成：

1. 明确字段临床含义、时间、单位、主键、修订和兼容性。
2. 修改 `api/schemas.py`。
3. 修改 storage、HTTP、交换和模型适配层。
4. 更新 `web/lib/api.ts` 和使用字段的组件。
5. 使用纯合成数据增加契约测试。
6. 更新 [DATA-CONTRACT.md](DATA-CONTRACT.md)。
7. 构建页面并完成 HTTP 与浏览器检查。
8. 把实际结果追加到 [VALIDATION.md](VALIDATION.md)。

当前格式为 `schema_version: 1`，未知字段会被拒绝。删除、重命名、改变单位或时间含义通常需要新 schema 版本和迁移方案。不要放宽 strict schema 来掩盖生产者与消费者不一致。

## 开发验证

后端：

```powershell
.\.venv\Scripts\python.exe -m pytest dashboard/tests -q
```

前端：

```powershell
cd dashboard\web
pnpm test
pnpm build
```

涉及相应功能时还要检查事务回滚、幂等修订、文件事件、SSE、`as_of`、旧预测状态、空模型状态、实际路由和浏览器交互。测试只能证明覆盖的代码路径，验证结论统一记录在 VALIDATION，不在本文维护通过数量。

## 后续开发顺序

1. 核对获准建模数据、变量字典、队列和结局定义。
2. 建立独立的 MIMIC 离线提取与训练数据流程。
3. 比较不同数据截止时间的预测稳定性。
4. 在 validation set 选择并锁定阈值，关联稳定性结果。
5. 实现遵守现有契约的模型工作进程。
6. 根据实际数据密度定义并验证综合可信度。
7. 测量持续负载、模型延迟、故障恢复和医生使用流程。
8. 用户明确要求后再评估 Windows 运行包。

若 GitHub 与本地冲突，应列出具体文件和差异，保留本地成果，再决定合并方式。不得用旧总结覆盖当前源码，也不得把计划或接口占位写成已实现。
