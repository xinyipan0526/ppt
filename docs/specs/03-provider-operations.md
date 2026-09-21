# Provider 能力与远端调用恢复协议

## 目标

统一描述不同模型服务的能力差异，并消除应用崩溃后盲目重复提交远端生图请求的风险。

## ProviderCapabilities

Provider 注册表不能只返回 `image.generate`、`image.edit` 等粗粒度名称。每个模型版本需要提供版本化能力声明：

```yaml
provider: volcengine
model: seedream-configured-model
operation_mode: async
supports:
  idempotency_key: false
  result_query: true
  cancellation: false
  structured_output: false
image:
  input_formats: [jpeg, png]
  max_reference_images: 10
  max_input_bytes: 15728640
  max_pixels: 16777216
  aspect_ratio_range: [0.333, 3.0]
  controls:
    mask: false
    bounding_box_overlay: true
    scribble_overlay: true
billing:
  unit: generated_image
  region: cn
data_policy_ref: volcengine-cn-current
```

能力维度至少包括：

- 同步或异步操作。
- 幂等键、结果查询和取消支持。
- 最大输入数量、大小、像素、比例和格式。
- Mask、线框、涂鸦等控制方式是原生参数还是参考图约定。
- 流式输出和结构化输出支持，以及可接受的 JSON Schema 子集。
- 内容安全错误和部分成功的返回方式。
- 计价单位、地域、数据保留政策和结果 URL 有效期。

Workflow 在提交前执行能力协商。缺少必要能力时选择已定义的降级路径或触发用户中断，不能假设所有 Provider 语义一致。

## Operation 与 Attempt 两层模型

### provider_operations

表示一次稳定的业务意图，例如“生成第 3 页的 hero visual”：

- `operation_id`：稳定的本地逻辑操作 ID。
- `operation_kind`：文本、视觉理解、图片生成或编辑等能力。
- `request_fingerprint`：Provider、模型、Prompt、输入 Artifact 和参数的规范化哈希。
- `aggregate_status`：由所属 Attempt 推导的汇总状态。
- `selected_attempt_id`：最终被业务采用的 Attempt，可为空。
- `has_unknown_attempts`：是否仍有可能产生费用的未知 Attempt。

Operation 不保存某次网络请求的远端 ID、错误或费用，也不能用新尝试覆盖旧尝试。

### provider_attempts

表示一次真实的远端提交尝试：

- `attempt_id`：每次尝试的稳定 ID。
- `operation_id`：所属业务意图。
- `attempt_no`：Operation 内单调递增序号。
- `status`：本节定义的 Attempt 状态。
- `idempotency_key`：Provider 支持时使用。
- `provider_job_id`：Provider 返回的远端任务 ID。
- `reservation_id`：本次尝试独立的成本预留。
- `request_started_at`、`response_received_at`：提交时间证据。
- `usage_record_id`、`error_code`：本次费用与错误。

同一业务意图的恢复性重试沿用 `operation_id`，但必须创建新的 `attempt_id` 和独立 Reservation。历史 Attempt 永不覆盖或复用。只要存在未解决的非终态 Attempt，就禁止创建并行新 Attempt。

## Attempt 状态语义

Attempt 的正式状态集合和允许转换以 [Workflow Runtime、并发与取消协议](02-workflow-runtime.md#provider-attempt-状态转换)中的转换表为唯一规范来源。本节只补充远端调用语义，不另行定义转换图。

该状态机属于 `provider_attempts`，不是 `provider_operations`。只有 Provider 明确确认取消及最终费用时，Attempt 才能进入 `cancelled`；否则转为 `outcome_unknown`。

Operation 的 `aggregate_status` 只取 `pending`、`in_progress`、`succeeded`、`failed`、`outcome_unknown` 或 `cancelled`，并由 Attempt 推导：

- 任一被选 Attempt 成功：`succeeded`。
- 存在非终态 Attempt：`in_progress`。
- 没有成功或非终态 Attempt，但存在未知 Attempt：`outcome_unknown`。
- 全部已知 Attempt 明确失败：`failed`。
- 所有 Attempt 均确认取消：`cancelled`。

即使 Operation 已因后续 Attempt 成功，早期未知 Attempt 仍通过 `has_unknown_attempts=true` 保留潜在费用，不能被成功结果覆盖。

### prepared

- 请求已规范化并写入数据库。
- 预算预留已成功创建。
- 尚无证据表明请求已发送。

### submitted

- “准备执行网络提交”的事务已经提交，但网络 I/O 可能尚未开始，也可能已经被 Provider 接收。
- 该状态刻意覆盖不确定发送窗口；如果进程退出，恢复必须进入 `reconciling`，不能回退为 `prepared`。

### provider_running

- 已保存 `provider_job_id`，可以轮询或等待结果。

### reconciling

- 查询 Provider 任务状态或检查幂等请求结果。
- 不允许同时重新提交相同操作。

### cancel_requested

- 本地已记录取消意图，但远端可能仍在执行或计费。
- Provider 明确确认取消后转 `cancelled`；无法确认时转 `outcome_unknown`。

### outcome_unknown

- Provider 不支持查询，且无法证明请求未提交或未计费。
- 保留预算预留并提示用户。
- 时间经过或保留期结束不能证明原请求未计费。
- 只有用户明确确认重复扣费风险后，才能为同一 Operation 创建新的 Attempt；确认时间、用户选择和风险说明必须持久化。

## 提交流程

1. 校验 ProviderCapabilities 和输入限制。
2. **事务 A**：在“没有未解决的非终态 Attempt；若存在未知 Attempt，用户已明确接受重复扣费风险”的守卫下，创建或读取 `provider_operations`，创建新的 `prepared` Attempt，并原子创建该 Attempt 的成本预留。请求指纹变化时必须创建新的 Operation。
3. 提交事务 A。此时崩溃能够确定网络请求尚未发送，恢复后可以安全提交该 Attempt。
4. **事务 B**：以 compare-and-set 将 Attempt 从 `prepared` 更新为 `submitted`，同时记录提交意图时间。
5. 提交事务 B。
6. 实际执行网络 I/O。事务 B 已提交后，无论请求是否真正离开本机，恢复都必须保守对账，不能直接重发。
7. 收到响应后立即在新事务中保存响应摘要、`provider_job_id`、Attempt 状态和可用的 Usage。
8. 异步任务转为 `provider_running`；同步成功或异步完成后，将结果下载到 Artifact staging 并执行 Artifact 提交协议。
9. 结算本 Attempt 的实际费用，将 Attempt 标记为 `succeeded`，再更新 Operation 汇总状态和 `selected_attempt_id`。

明确的崩溃窗口：

- 事务 A 提交后、事务 B 前崩溃：Attempt 仍为 `prepared`，确定未发送，可以安全继续提交。
- 事务 B 提交后、网络发送前崩溃：Attempt 已是 `submitted`，进入 `reconciling`；无法证明未发送时转 `outcome_unknown`。
- 网络发送后、本地保存响应前崩溃：必须查询远端或转 `outcome_unknown`，不得自动创建新 Attempt。

## 恢复矩阵

| Attempt 状态 | Provider 能力 | 恢复动作 |
|---|---|---|
| `prepared` | 任意 | 可以安全提交 |
| `submitted` | 幂等键 | 使用相同幂等键查询或重试 |
| `submitted` | 可查询且有远端 ID | 查询远端状态 |
| `submitted` | 不可查询 | 转 `outcome_unknown` |
| `provider_running` | 可查询 | 轮询并下载结果 |
| `provider_running` | 查询失败 | 有限重试后转 `outcome_unknown` |
| `outcome_unknown` | 任意 | 不自动重新提交 |

从 `outcome_unknown` 创建后续 Attempt 必须满足守卫条件：不存在其他非终态 Attempt，并且已经记录用户对重复扣费风险的明确确认。保留期、进程重启或普通重试策略都不能替代该确认。

## 错误归一化

Provider Adapter 必须归一化：

- `authentication_error`
- `invalid_request`
- `rate_limited`
- `temporary_unavailable`
- `content_rejected`
- `operation_not_found`
- `result_expired`
- `partial_success`
- `unknown_remote_outcome`

每类错误声明是否可重试、是否可能已计费、是否保留预算、是否需要人工确认。

## 契约测试

- 能力上限和输入校验。
- Operation 与多个 Attempt 的历史不会互相覆盖。
- 事务 A 后崩溃可以安全继续；事务 B 后崩溃不会自动重发。
- 幂等键传递。
- 提交成功但本地断开。
- 远端任务 ID 持久化。
- 查询、取消和结果过期。
- 内容安全拒绝和部分成功。
- 错误归一化及预算处理。
- `outcome_unknown` 不会自动重发。
- 保留期结束不会自动授权新 Attempt。
