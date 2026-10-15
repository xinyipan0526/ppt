# 单次完整规划与 Provider 错误闭环设计

## 1. 背景与目标

当前 `deck_planning/v1` 将首次规划拆成 `build_brief`、`build_outline`、
`build_design_system` 和 `build_page_specs` 四次结构化模型调用。实际云运行表明，这会放大时延、
成本和失败概率；只要最后一次调用超时，前三次已经产生的成本也无法形成可审核的完整规划。

本批次交付三个相互关联的产品修正：

1. 输入充分时，首次完整规划只调用一次多模态语言模型，同时得到 Brief、Outline、
   DesignSystem 和全部 PageSpec。
2. Provider 错误从 Adapter、Operation/Attempt、Workflow、Run Snapshot 一直投影到正常产品界面，
   同时保持未知远端结果不自动重发的计费安全边界。
3. 正常任务界面只显示加载状态、当前阶段、必要提示和结果；完整运行轨迹仅在用户主动打开
   “运行详情”标签页后加载和呈现。

该设计以用户最新确认的产品决策为准，替代
`docs/superpowers/specs/2026-09-23-deck-planning-workflow-design.md` 中“首次规划采用四次分阶段
Prompt”的选择，但不改变 `docs/specs/02-workflow-runtime.md`、
`docs/specs/03-provider-operations.md`、`docs/specs/04-cost-ledger.md` 和
`docs/specs/06-event-stream.md` 的安全与持久化约束。

## 2. 非目标

- 不把生产、OCR、视觉定位或图片生成合并进规划模型调用。
- 不承诺一次调用永远完成所有交互；信息不足时允许先返回澄清表单，用户回答后再发起一次完整规划。
- 不自动重发任何 `outcome_unknown` Attempt。
- 不在日志、事件或浏览器中暴露完整 Prompt、原始 Provider 响应、API Key 或不可信素材全文。
- 不取消规划审核、局部页修订、不可变 Artifact、成本预留或恢复协议。
- 不顺带实现元素级 PPTX、画布编辑或其他二期能力。

## 3. 成功标准

- 信息充分的首次规划只有一个 Provider Operation 和一个网络 Attempt。
- 单次成功响应经过严格 Schema 与跨对象校验后，仍拆分保存为现有 Brief、Outline、
  DesignSystem 和逐页 PageSpec Artifact。
- 信息不足时，第一次调用可返回 `clarification_required`；Resume 后只再发起一次完整规划调用。
- 用户每次提交规划修订时最多发起一次语言模型调用；局部页修订不强制重建未受影响内容。
- 任一 Provider 错误在 Run 和当前 Step 上都有安全、结构化、可展示的错误投影。
- 读取超时在主界面明确显示超时阈值、远端结果未知和未自动重试，而不是泛化为“无错误详情”。
- 未打开运行详情时，浏览器不请求 Run Trace API，也不渲染 Steps、Events 或 Operations 列表。

## 4. Workflow 版本与兼容性

新增 `deck_planning/v2`。新建规划 Run 一律使用 v2；不能在原地改变 v1 的步骤语义和恢复键。

v2 的固定顶层步骤为：

1. `build_plan`
2. `review_plan`

已有 v1 Run 的读取、Trace、人工恢复和终态展示继续受支持。已有非终态 v1 Run 必须由保留的 v1
执行入口按原语义恢复、取消或完成；不得把 v1 的 `build_page_specs` Attempt 解释为 v2 的
`build_plan` Attempt。只有当 Workspace 中不再存在非终态 v1 Run 时，后续版本才可以移除 v1
执行器。

Workflow 创建 API 不要求客户端选择版本。Composition Root 为新 Run 注入当前版本，并按持久化的
`workflow_name + workflow_version` 路由恢复执行器。

## 5. 单次完整规划契约

新增严格的判别联合 `InitialPlanDecision`：

- `kind = "ready"`：包含一个完整 `PlanningDraft`。
- `kind = "clarification_required"`：包含原因和 1–4 个现有白名单内的结构化问题，不得同时包含
  部分规划结果。

`PlanningDraft` 包含：

- `brief: PresentationBrief`
- `outline: DeckOutline`
- `design_system: DesignSystem`
- `page_specs: PageSpecs`

所有契约继续使用严格 Pydantic 模型并拒绝未知字段。模型返回后，应用层一次性验证以下跨对象不变量：

- Outline 页面数、PageSpec 数量与 Brief 页数完全一致。
- Outline 和 PageSpec 页码从 1 开始连续、唯一且顺序稳定。
- 每个 PageSpec 的目标与同页 Outline 目标一致。
- `PageSpecs.brief_page_count` 与 Brief 页数一致，内嵌 Outline 与顶层 Outline 一致。
- 所有素材引用只能来自创建 Run 时已经授权、冻结并编译的不可信来源边界。

任何一个不变量失败都视为已收到响应后的内容失败，按真实 Usage 结算，不产生可审核的部分规划。

## 6. Prompt 与调用语义

新增版本化完整规划 Prompt `initial-plan-v1`。它明确要求先判断信息是否足够；足够时在
同一个严格 JSON Schema 响应中生成完整 `PlanningDraft`，不足时只返回澄清问题。

正常首次规划的调用语义为：

1. 编译规范化用户输入、已授权素材摘要和现有约束。
2. 为 `build_plan` 当前 generation 创建一个稳定 Operation ID、幂等键和保守预算预留。
3. 执行唯一一次网络请求。
4. 在选择成功 Attempt 前，原子保存验证前的 Provider 结构化结果与 Usage。
5. 验证 `InitialPlanDecision` 和全部跨对象不变量。
6. 若需要澄清，创建现有版本化 Interrupt；若已就绪，进入 Artifact 物化。

澄清 Resume 会创建新的 `build_plan` generation，因此拥有新的稳定 Operation ID。此前调用已经产生的
Usage 正常结算，但不会再执行独立 Brief、Outline、DesignSystem 或 PageSpec 调用。

规划审核后的修订继续保留确定性 scope。每次用户修订只创建一个新规划 generation，并最多执行一次
语言模型调用：

- Brief、Outline 或 DesignSystem 级修订返回完整、重新验证的 `PlanningDraft`。
- 指定页面修订可返回严格 `PageSpecPatch`，只替换选中页面并保留未选中 Artifact 引用。
- 无法安全局部化的 `auto` 修订升级为完整 `PlanningDraft`，但仍只有一次调用。

## 7. Artifact 物化与崩溃恢复

一次 Provider 响应不意味着合并 Artifact。验证成功后仍按现有 logical ID 物化：

- `planning/brief`
- `planning/outline`
- `planning/design-system`
- `planning/pages/{page_index}`
- 审核批准后的 `planning/bundle`

依赖关系保持 Brief → Outline → DesignSystem → PageSpec 的语义，并补充输入素材依赖。所有规划
Artifact 记录同一个 `build_plan` Operation ID，从而可以追溯到唯一模型调用。

物化过程可能跨多个文件提交，因此必须可幂等恢复：

- Provider 成功结果和 Usage 先进入 `provider_text_results`，再创建 Artifact。
- 每个 Artifact 通过 operation ID、logical ID、generation 和内容指纹识别已提交结果。
- 进程在任意 Artifact 提交后崩溃时，恢复逻辑重放已持久化的结构化结果并补齐缺失 Artifact，
  不创建第二个 Provider Attempt。
- 只有全部必需 Artifact 可用且依赖边完整后，`build_plan` Step 才能完成。
- 已提交的部分 Artifact 在 Step 尚未完成时不得作为可审核 PlanningBundle 暴露。

## 8. Provider 错误分类与安全摘要

Adapter 统一区分：

- 认证或权限错误。
- 请求参数错误。
- 余额不足、限流和服务不可用。
- 连接、连接池、写入和读取超时。
- 连接中断或其他网络错误。
- 非法 JSON、空响应、不完整响应和严格 Schema/业务不变量失败。

每次失败最多持久化以下安全字段：

- `provider`、`model`、`operation_id`、`attempt_id`
- 统一 `error_code`
- `provider_error_type`
- `provider_error_message` 的长度受限、脱敏摘要
- `http_status`
- Provider Request ID；只有确实从响应头或响应体取得时才存在
- `request_phase`：`connect | write | read | parse | validate`
- `timeout_seconds` 和已经过时间；仅适用于超时
- `provider_response_received` 布尔值

读取超时没有 HTTP 响应时，必须明确记录
`provider_response_received = false`，不得伪造火山 Request ID 或声称 Provider 返回了错误正文。
日志文案应说明“在读取响应时超时，未收到可验证的 Provider 响应”。

完整 Prompt、原始响应正文、Authorization、Cookie、API Key、用户素材全文和本地路径不进入错误摘要、
事件或普通日志。成功的完整结构化结果继续只存入受保护的 Provider Result 和 Artifact。

## 9. 错误状态、事务与重试

Provider 错误必须沿以下链路传播：

```text
Adapter error
  -> Attempt status and safe response_summary
  -> Operation aggregate status
  -> current Step.error
  -> Run.error
  -> durable workflow event
  -> snapshot / trace API
  -> user-facing status
```

已知不可恢复失败在同一状态事务中将当前 Step 和 Run 标为 `failed`，并保存同一规范化错误。

无法判断远端是否已经处理或计费的失败进入 `outcome_unknown`：

- Attempt 和 Operation 保持未知结果语义，预算预留继续持有。
- 当前 Step 和 Run 原子进入 `recovery_required`。
- Step.error 与 Run.error 都保存 `unknown_remote_outcome` 及安全 Provider 摘要。
- Attempt/Operation 状态事务写入 `provider.outcome_unknown`；随后 Workflow 状态事务原子更新当前
  Step、Run 并写入 `run.recovery_required`，事件携带 `step_id`、`operation_id` 和同一安全错误摘要。
  若进程在两个事务之间退出，启动恢复扫描必须根据未知 Operation 补齐 Step/Run 的恢复状态，
  不能再次提交远端请求。
- 系统不得自动创建新 Attempt；用户必须先核对 Provider 控制台并显式授权。

只有 Provider 明确证明未计费且允许重试的失败才进入 `waiting_for_retry`，并显示 due time。不能仅凭
429 或 5xx 状态码推断未计费。

本次观察到的方舟读取超时应投影为：

- `error_code = "unknown_remote_outcome"`
- `provider_error_type = "ReadTimeout"`
- `request_phase = "read"`
- `timeout_seconds = 90`
- `provider_response_received = false`
- Run/Step 状态均为 `recovery_required`

## 10. HTTP 与前端信息结构

Run Snapshot 返回 Run 和 Step 的安全结构化错误。Trace API 继续提供完整的安全 Steps、Events、
Operations 和 Attempts 投影，但不会成为正常状态页面的必需请求。

默认任务状态区域只显示：

- 一个 loading 指示器或确定性进度状态。
- 当前产品阶段，例如“正在生成完整规划”。
- 必要的耗时提示和取消入口。
- 澄清表单、规划审核或最终结果。
- 发生异常时的明确错误摘要与下一步操作。

读取超时的主界面文案应表达：等待火山模型响应超过配置阈值、没有收到可验证响应、远端结果未知、
系统为避免重复计费没有自动重试。用户无需先阅读轨迹才能理解发生了什么。

新增用户主动选择的“运行详情”标签页或等价显式入口：

- 默认关闭，且关闭时 Trace Query 使用 `enabled: false` 或不挂载组件。
- 用户首次打开后才请求 `/api/v1/runs/{run_id}/trace`。
- 标签页展示 Steps、Events、Operations、Attempts 和安全错误摘要。
- `outcome_unknown` 的 Provider 控制台核对提示与显式授权操作可以从主错误卡进入，并在详情页展示
  对应 Attempt；安全操作不能因隐藏轨迹而不可达。
- 切换回正常内容不会改变后台任务，也不会丢失已加载的 Query Cache。

SSE 继续驱动 Snapshot 刷新；Trace 未打开时不得因为每个 SSE 事件而触发 Trace 请求。

## 11. 可观测性

每次 Provider 调用至少记录提交和终态两条结构化日志。终态日志包含耗时与安全结果分类。成功日志
只记录 response ID、状态、输出项数量和 Usage 摘要，不记录生成内容；失败日志记录第 8 节的安全字段。

日志配置必须保证同一事件只写入一次。若应用 Logger 与 root Logger 同时持有同一个文件 Handler，
应通过单一所有权或 `propagate` 配置消除重复行；重复日志不能被解释为重复 Provider 调用。

运行详情以数据库中的 Workflow、Operation 和 Attempt 为事实源，日志只用于诊断，不决定恢复行为。

## 12. 测试策略

### 领域与 Workflow

- 完整输入返回一个 `PlanningDraft`，跨对象不变量全部通过。
- 页数、页码、Outline/PageSpec 目标或内嵌 Outline 不一致时拒绝整份结果。
- 信息充分的首次规划断言只有一个 Operation、一个 Attempt 和一次 Provider 调用。
- 澄清路径断言每个 generation 最多一次调用，Resume 后可以得到完整规划。
- 在 Provider Result 以及每个 Artifact 提交边界模拟崩溃，恢复时不产生第二次调用。
- v1 非终态 Run 仍按 v1 执行器恢复，新 Run 使用 v2。
- 完整修订和指定页修订每次用户操作最多调用一次。

### Provider 与错误投影

- 使用 MockTransport 覆盖认证失败、参数错误、429、5xx、连接超时、读取超时、非法 JSON、
  空输出、截断和 Schema 失败。
- 读取超时断言没有伪造的 HTTP 状态、Provider 消息或 Request ID，并保存 phase、阈值和
  `provider_response_received = false`。
- 已知失败断言 Attempt、Step、Run 和事件拥有一致安全错误。
- 未知结果断言预算不释放、不自动重发，Run/Step 均进入 `recovery_required`。
- 日志脱敏测试覆盖 API Key、Authorization、Prompt、响应正文和本地路径。
- 日志集成测试断言单次 Provider 提交只产生一条对应文件日志。

### 前端

- 正常运行默认只显示 loading、阶段和必要操作，不渲染 Run Trace。
- 未打开运行详情时不请求 Trace API；打开后只发起一次可缓存请求。
- HTTP 已知失败显示中文安全摘要和可行下一步。
- `ReadTimeout` 显示 90 秒、远端结果未知和未自动重试。
- `outcome_unknown` 的核对与授权操作始终可达。
- Trace 加载失败不覆盖主任务状态或主错误信息。

### 最终验收

- Python 相关测试与全量测试。
- React 单元测试、类型检查和生产构建。
- Chromium 产品 E2E，覆盖单次规划和按需运行详情。
- `python -m compileall -q src tests migrations`。
- `git diff --check`。

## 13. 发布与迁移说明

发布说明明确指出首次规划从四次模型调用收敛为一次完整规划调用。已有 v1 Run 不做破坏性迁移；
新建 Run 使用 v2。全新 Workspace、含终态 v1 Run 的升级 Workspace，以及包含
`recovery_required` v1 Run 的 Workspace 都必须执行验收。

真实方舟 Smoke Test 只有在用户明确授权费用后运行，并核对所选区域的模型 ID、价格、超时行为和
结果 Host。单元与集成测试不得依赖真实计费调用。
