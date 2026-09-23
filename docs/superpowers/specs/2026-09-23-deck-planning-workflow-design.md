# 可运行 Deck Planning Workflow 设计

## 1. 目标与范围

本阶段交付一个可以真实调用 DeepSeek、可以中断澄清、可以恢复、可以审核并持久化全部规划版本的确定性规划闭环：

```text
normalized source text
  -> PresentationBrief
  -> DeckOutline
  -> DesignSystem
  -> PageSpec[]
  -> plan review
  -> approved planning result
```

成功标准：

- 用户提交已规范化的文本或 Markdown 后，可以启动 `deck_planning/v1` Run。
- 信息不足时，系统生成结构化澄清表单，持久化 Interrupt，并在用户回答后从已提交边界恢复。
- 信息充分时，系统生成经过严格 Schema 校验的 Brief、Outline、DesignSystem 和与页数一致的 PageSpec 集合。
- 每个规划输出都是不可变、内容寻址且有依赖关系的 JSON Artifact。
- 规划完成后必须由用户审核；批准后 Run 才成功，修改意见会从确定性失效点重新规划。
- Provider 调用复用现有 Operation/Attempt、预算、事件和恢复协议；未知远端结果不得自动重发。

本阶段不包含 PDF/图片解析、视觉理解、Seedream、页面渲染、OCR、导出、React UI 或开放式 ReAct Agent。

## 2. 架构选择

采用分阶段强类型 Workflow，不采用单次大 Prompt，也不让 ReAct Agent 控制顶层流程。

原因：

- 每个阶段都有独立 Schema、成本、幂等键和恢复边界。
- 用户修改设计风格时不需要重新构建 Brief；修改 Brief 时则能确定性失效所有下游结果。
- 模型输出只能提供候选内容或澄清决策，不能直接改变 Run 状态、写文件或发起昂贵调用。
- SQLite、Artifact 和 Workflow 状态继续作为唯一事实源。

新增代码按职责分为：

- `providers/text.py`：Provider 无关的结构化文本请求、结果和错误协议。
- `providers/deepseek.py`：DeepSeek Responses API 适配、认证、HTTP、Usage 和错误归一化。
- `operations/text_runner.py`：预算、Operation/Attempt 与一次真实网络调用的编排。
- `tools/clarification.py`：澄清问题校验、表单生成和 Resume Payload 校验。
- `workflows/deck_planning.py`：固定步骤、依赖、失效和恢复入口。
- `skills/deck_planning/`：版本化 Prompt 模板、JSON Schema 身份和能力说明。

## 3. 领域契约

扩展 `domain/contracts.py`，新增以下严格 Pydantic 契约，全部 `extra="forbid"`：

- `PresentationBrief`：保留现有字段并补齐来源摘要、约束和已确认字段。
- `DeckOutline`：叙事目标、章节和按顺序排列的页面意图。
- `DesignSystem`：配色、字体角色、间距、视觉风格、图片风格、页面类型和禁用项。
- `PageSpec`：页码、页面目标、页面类型、结构化文案块、布局意图、视觉需求、来源引用和验收条件。
- `ClarificationDecision`：判别联合 `ready | clarification_required`。
- `PlanningBundle`：已批准 Brief、Outline、DesignSystem 和 PageSpec Artifact 引用。

核心不变量：

- Brief 页数为 `1..50`，默认 8。
- Outline 页面意图数和 PageSpec 数量必须等于 Brief 页数。
- PageSpec 页码从 1 开始连续且不重复。
- 每个 PageSpec 必须对应同页 Outline 意图。
- 不可信素材仅作为数据进入 Prompt，不得改变工具白名单、预算、Provider 配置或文件权限。

## 4. Provider 协议与 DeepSeek 适配

首版使用 DeepSeek Responses API 的 `json_schema` 输出。官方当前接口声明支持 JSON Schema 结构化输出以及输入、输出和推理 Token Usage：

- <https://api-docs.deepseek.com/api/create-response/>

`StructuredTextProvider` 接收：

- 模型与能力标识。
- system/user 消息和最小充分上下文。
- Schema name、version 和 JSON Schema。
- `max_output_tokens`、超时和稳定的非隐私 `user_id`。
- Operation/Attempt 提供的调用关联信息。

返回：

- 原始 response ID 和安全响应摘要。
- 已验证前的 JSON 值。
- input/output/cached/reasoning Token Usage。
- finish/incomplete 信息。

DeepSeek Adapter 使用可注入的 `httpx.AsyncClient` 或 Transport，便于契约测试。API Key 仅从进程安全配置取得，不进入数据库、事件、Artifact、异常文本或请求指纹。

首版 capability 声明为同步调用。若提交后发生断连、读取超时或无法确定服务端是否处理，请求进入 `unknown_remote_outcome`；不能因文本调用成本较低而绕过现有未知结果规则。

## 5. Provider 调用、预算与结算

`TextOperationRunner` 严格执行：

1. 校验 capability、输入大小、Schema 和 pricing 配置。
2. 规范化请求并生成指纹。
3. 通过现有 `ProviderOperationService.prepare_attempt()` 原子创建 Attempt 和预算预留。
4. 通过 `commit_submission_intent()` 将 Attempt CAS 为 `submitted`。
5. 执行唯一一次网络 I/O。
6. 保存响应摘要、Usage、错误语义和结构化输出。
7. 成功时结算 Usage 并选择 Attempt；明确未计费失败时释放预留；未知结果转为 `held_unknown`。

需要扩展现有 Provider Operation 服务以支持“已计费的已知失败”：例如 HTTP 200 且有 Usage，但内容为空、被截断或未通过本地 Schema。该路径结算真实 Usage，将 Attempt 置为 `failed`，但不产生成功输出。

价格使用版本化 `TextPricing` 配置，不在 Workflow 中硬编码。配置至少包含 Provider、模型、输入/输出/缓存 Token 单价、币种、生效时间、来源和安全预留系数。无法获得可信价格时，要求用户配置单次硬上限。

## 6. 错误归一化与重试

DeepSeek 官方错误码映射参考：

- <https://api-docs.deepseek.com/quick_start/error_codes/>

映射规则：

| 远端结果 | 统一错误 | 默认行为 |
|---|---|---|
| 400 / 422 | `invalid_request` | 不自动重试 |
| 401 | `authentication_error` | 中断并要求修复配置 |
| 402 | `insufficient_balance` | 中断并要求处理 Provider 余额 |
| 429 | `rate_limited` | 仅在确认未计费时有限退避重试 |
| 500 / 503 | `temporary_unavailable` | 仅在确认未计费时有限退避重试 |
| 明确内容安全拒绝 | `content_rejected` | 按返回 Usage/计费证据结算 |
| 提交后的断连或超时 | `unknown_remote_outcome` | 保留预算，不自动重发 |

HTTP 200 不等于业务成功。空内容、`incomplete`、Token 截断、无输出消息、无 Usage 或本地 Schema 校验失败均需单独处理。DeepSeek 官方也提示 JSON 输出可能出现空内容：

- <https://api-docs.deepseek.com/guides/json_mode/>

自动重试只适用于能够证明未发生远端计费的失败，并受 Step 最大尝试次数和总预算约束。

## 7. 澄清工具协议

模型在 Brief 阶段只能返回：

- `ready`：完整 `PresentationBrief`。
- `clarification_required`：原因和 1–4 个结构化问题。

允许澄清的 Brief 字段：

- `topic`
- `purpose`
- `audience`
- `language`
- `tone`
- `page_count`
- `budget_minor_units`
- `quality`

问题输入类型仅允许 `single_select | multi_select | text | number`。`RequestClarificationTool` 拒绝未知字段、重复字段、空选项、类型不一致的推荐值和越界值，并生成 `clarification_form/v1`。

工具而非模型调用 `WorkflowRepository.create_interrupt()`。Interrupt 保存：

- 问题及原因。
- 表单 Schema name/version。
- 输入 Artifact 引用。
- Prompt/Skill/输出 Schema 版本。
- 当前 Step generation。

Resume 必须校验 Interrupt 仍待解决、版本一致、必填字段、未知答案、答案类型和领域范围。相同 Payload 重复提交保持幂等；不同 Payload 冲突。回答合并为新的规划输入 Artifact，不覆盖原始素材。

## 8. 确定性 Workflow

`deck_planning/v1` 固定包含：

1. `build_brief`
2. `build_outline`
3. `build_design_system`
4. `build_page_specs`
5. `review_plan`

每个生成步骤：

- 从已提交 Artifact 编译最小上下文。
- 创建稳定 Operation ID 和 Step generation 对应的幂等键。
- 调用 `TextOperationRunner`。
- 用对应 Pydantic 契约再次验证 Provider 输出和跨对象不变量。
- 将 `SchemaEnvelope` JSON 写入 Artifact staging，提交文件、元数据、依赖边和 availability。
- 在同一逻辑步骤完成 Step output refs 和核心事件持久化。

任何自由文本都不能直接成为后续控制条件。

Brief 信息不足时，`build_brief` 返回 `Interrupted`。Resume 后 Step generation 增加并重新执行 Brief。其余生成步骤若发生可恢复的明确未计费错误，可返回 `RetryScheduled`；未知结果返回 `RecoveryRequired`。

## 9. Artifact 与版本关系

规划文档统一保存为 JSON Artifact，不增加 Brief、Outline、DesignSystem 和 PageSpec 的重复专用版本表。

建议 logical ID：

- `planning/brief`
- `planning/outline`
- `planning/design-system`
- `planning/pages/{page_index}`
- `planning/bundle`
- `planning/input/{generation}`

依赖关系：

- Brief 依赖规划输入。
- Outline 依赖 Brief。
- DesignSystem 依赖 Brief 和 Outline。
- PageSpec 依赖 Brief、Outline、DesignSystem 和相关素材引用。
- PlanningBundle 依赖全部被批准的规划 Artifact。

`workflow_runs.brief_version_id` 与 `pages.page_spec_version_id` 保存对应 Artifact ID。Run 的 `result_refs` 保存最终 PlanningBundle 和主要规划 Artifact。

## 10. 规划审核与修改

完成全部 PageSpec 后必须创建 `plan_review/v1` Interrupt：

- `approve`：固化当前 Artifact 集合，生成 PlanningBundle，完成 Run。
- `revise`：要求非空 `feedback`，并接受 `scope = brief | outline | design | pages | auto`。
- `cancel`：先持久化取消意图，再走现有取消协议。

确定性失效规则：

- `brief`：失效 Brief 及全部下游。
- `outline`：保留 Brief，失效 Outline、DesignSystem、PageSpec 和 Bundle。
- `design`：保留 Brief/Outline，失效 DesignSystem、PageSpec 和 Bundle。
- `pages`：失效指定 PageSpec 和 Bundle；未指定页时失效全部 PageSpec。
- `auto`：根据结构化修改目标匹配以上规则；无法可靠分类时从 Outline 开始。

修改意见作为新的 PlanningInput Artifact 保存。旧版本保留，可审计和比较。

## 11. 恢复、取消与并发

- Workflow 只从已提交 Step/Artifact 边界恢复，不恢复 Python 调用栈。
- `prepared` Attempt 可以安全继续提交。
- `submitted` 且没有可证明的结果时进入 `reconciling`，DeepSeek 首版无可靠远端查询时最终进入 `outcome_unknown`。
- `cancel_requested` Run 不得创建新的 Step、Attempt 或 Artifact；只允许专用取消/对账路径运行。
- 同一 Run 同一 Step generation 只允许一个有效执行任务。
- Planning 阶段按 Run 串行；跨 Run 并发由现有 Supervisor 与 Provider semaphore 限制。

## 12. 测试策略

### 契约测试

- 所有领域模型拒绝未知字段和越界值。
- Outline/PageSpec 数量、页码和引用不变量。
- ClarificationDecision 判别联合及表单白名单。

### Provider 测试

- 使用 `httpx.MockTransport` 验证 URL、认证、请求 Schema 和超时。
- JSON Schema 输出、Usage 和 incomplete 解析。
- 400/401/402/422/429/500/503 映射。
- 断连、读取超时、空内容和非法结构。
- 日志、错误和持久化摘要不包含 API Key 或完整敏感素材。

### 账本与 Operation 集成测试

- 成功调用正确结算。
- 已计费 Schema 失败仍结算 Usage。
- 明确未计费失败释放预留。
- 未知结果进入 `held_unknown` 且不能自动新建 Attempt。

### Workflow 集成测试

- 输入不足 -> Interrupt -> Resume -> Brief。
- 完整输入直接生成全部规划 Artifact。
- 审核批准生成 PlanningBundle 并完成 Run。
- revise 按 scope 失效正确范围并保留旧版本。
- 在每个 Step、Attempt 和 Artifact 提交边界模拟重启，验证无重复远端提交。
- 取消后不再产生 Provider 或 Artifact 副作用。

### 可选真实调用

真实 DeepSeek 测试必须显式设置 API Key 和 pytest marker，默认跳过，不进入日常全量测试，也不得依赖固定自然语言措辞。

## 13. 完成边界

本阶段完成时，仓库应能通过程序入口创建规划 Run，使用真实或测试 Provider 执行，处理中断与恢复，并取得已批准 PlanningBundle。尚未批准的规划不能进入图片生成阶段；本阶段也不实现图片生成阶段本身。
