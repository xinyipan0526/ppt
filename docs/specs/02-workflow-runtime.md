# Workflow Runtime、并发与取消协议

## 目标

定义本地单进程 Workflow Runtime 的状态、执行边界、中断恢复、页面并发、取消和关闭行为。

## 事实源

- SQLite 是 Run、Step、Page、Interrupt、Provider Operation、Provider Attempt 和事件的唯一事实源。
- 内存 Task、async generator、SSE 连接和 React 状态都不是事实源。
- Workflow 只从已提交的步骤边界恢复，不序列化或恢复 Python 调用栈。

## 正式状态模型

本节是 Run、Step、Page 和 Provider Attempt 状态名称及转换的唯一规范来源。README 和其他规格只能引用这些状态，不能增加同义状态或独立转换。实现中的数据库约束、枚举和迁移必须从这些表派生。

Provider Operation 的汇总状态不驱动调度，由所属 Attempt 推导，详见 [Provider 能力与远端调用恢复协议](03-provider-operations.md)。

### Step 结果

每个步骤处理器必须返回以下结构化结果之一：

- `Completed(output_refs)`
- `Interrupted(interrupt_id)`
- `RetryScheduled(retry_at, reason)`
- `RecoveryRequired(reason)`
- `Failed(error_code, recoverable)`
- `Cancelled(reason)`

预期的 Provider、校验、预算和页面错误必须转换为结构化结果，不得直接逃逸到页面并发组之外。

### Run 状态转换

Run 状态集合：`queued`、`running`、`waiting_for_user`、`waiting_for_retry`、`cancel_requested`、`recovery_required`、`succeeded`、`failed`、`cancelled`。

| 当前状态 | 触发事件 | 守卫条件 | 下一状态 | 同一事务副作用 | 恢复动作 |
|---|---|---|---|---|---|
| `queued` | `run_dispatched` | Workspace 锁有效且存在可运行 Step | `running` | 记录运行实例和 `run.started` | 无 |
| `running` | `interrupt_created` | Interrupt 与 Payload Schema 已持久化 | `waiting_for_user` | Step 同步转 `waiting_for_user`，写中断事件 | 等待 Resume |
| `waiting_for_user` | `interrupt_resumed` | Interrupt 未解决且 Payload 有效 | `queued` | 解决 Interrupt，将关联 Step 转 `pending`，保存 Resume 输入并写恢复事件 | 重新调度 |
| `running` | `retry_scheduled` | 当前无其他可运行 Step | `waiting_for_retry` | 保存最早 `retry_at` | 到期转 `queued` |
| `waiting_for_retry` | `retry_due` | 当前时间达到 `retry_at` | `queued` | 清除唤醒标记 | 重新调度 |
| `queued`、`running`、`waiting_for_user`、`waiting_for_retry`、`recovery_required` | `cancel_requested` | 尚未终态 | `cancel_requested` | 禁止新 Step 和 Attempt，写取消事件 | 取消本地任务并对账远端 Attempt |
| `cancel_requested` | `cancellation_completed` | 本地任务结束；远端 Attempt 已终态、`outcome_unknown` 或独立对账 | `cancelled` | 结算或保留预算，写终态事件 | 无 |
| `running` | `all_required_steps_succeeded` | 无失败、无未解决 Interrupt、无非终态 Step | `succeeded` | 固化最终输出引用 | 无 |
| `running`、`recovery_required` | `unrecoverable_failure` | 错误明确不可恢复 | `failed` | 保存标准错误和失败事件 | 无 |
| `running` | `process_restarted` | 上次进程未正常完成 | `recovery_required` | 记录恢复原因，禁止直接重放 | 执行 Step 与 Attempt 对账 |
| `cancel_requested` | `process_restarted` | 取消尚未完成 | `cancel_requested` | 保留取消意图并记录恢复事件 | 继续取消和远端对账 |
| `recovery_required` | `recovery_retry_safe` | 能证明重放不会重复副作用 | `queued` | 保存恢复证据 | 重新调度 |
| `recovery_required` | `recovery_user_input_required` | 已创建恢复 Interrupt | `waiting_for_user` | 保存 Interrupt 和恢复事件 | 等待用户 |
| `recovery_required` | `recovery_cancel_required` | 原 Run 已请求取消 | `cancel_requested` | 恢复取消意图 | 继续取消 |

### Step 状态转换

Step 状态集合：`pending`、`running`、`waiting_for_user`、`waiting_for_retry`、`recovery_required`、`cancel_requested`、`succeeded`、`failed`、`cancelled`、`skipped`。

| 当前状态 | 触发事件 | 守卫条件 | 下一状态 | 同一事务副作用 | 恢复动作 |
|---|---|---|---|---|---|
| `pending` | `step_started` | 前置依赖成功且 Run 为 `running` | `running` | 记录输入快照和开始事件 | 无 |
| `running` | `Completed` | 输出 Schema 与 Artifact 引用有效 | `succeeded` | 保存输出、依赖边和完成事件 | 无 |
| `running` | `Interrupted` | Interrupt 已创建 | `waiting_for_user` | Run 同步转 `waiting_for_user` | 等待 Resume 后重新排队该 Step |
| `waiting_for_user` | `interrupt_resumed` | 关联 Interrupt 已解决且 Payload 有效 | `pending` | 保存 Resume 输入并增加执行代次 | 随 Run 重新调度 |
| `running` | `RetryScheduled` | 错误可重试且未超上限 | `waiting_for_retry` | 保存 `retry_at`、错误和尝试次数 | 到期创建下一次执行 |
| `waiting_for_retry` | `retry_due` | Run 未取消且时间到期 | `pending` | 清除唤醒标记 | 重新执行 |
| `running` | `RecoveryRequired` | 结果或远端副作用不确定 | `recovery_required` | 保存对账目标 | 按后续 `step_recovery_*` 事件转换 |
| `recovery_required` | `step_recovery_retry_safe` | 能证明重放不会重复副作用 | `pending` | 保存恢复证据 | 重新执行 |
| `recovery_required` | `step_recovery_user_input_required` | 已创建恢复 Interrupt | `waiting_for_user` | 保存 Interrupt | 等待 Resume |
| `recovery_required` | `step_recovery_failed` | 无安全恢复路径 | `failed` | 保存标准错误 | 由 Workflow 判断 Run 状态 |
| `running` | `Failed` | 不可重试或已超上限 | `failed` | 保存标准错误 | 由 Workflow 判断 Run 是否失败 |
| `pending`、`waiting_for_user`、`waiting_for_retry`、`recovery_required` | `cancel_requested` | Step 没有正在执行的本地任务 | `cancelled` | 保存取消原因 | 无 |
| `running` | `cancel_requested` | Run 已请求取消 | `cancel_requested` | 通知内存 Task，禁止新副作用 | 清理本地任务并对账远端 Attempt |
| `cancel_requested` | `step_cancellation_completed` | 本地清理完成且远端副作用已隔离 | `cancelled` | 保存取消结果 | 无 |
| `pending` | `dependency_not_selected` | 分支决策确认无需执行 | `skipped` | 保存跳过原因 | 无 |
| `running` | `process_restarted` | 无已提交完成记录 | `recovery_required` | 禁止直接重放 | 检查 Provider Attempt 与 Artifact |

### Page 状态转换

Page 状态集合：`planned`、`queued`、`running`、`waiting_for_budget`、`waiting_for_user`、`recovery_required`、`cancel_requested`、`succeeded`、`failed`、`cancelled`、`stale`。具体处理阶段使用独立 `page_stage` 字段表示 `visual_generation`、`rendering` 或 `validation`，不得扩展为临时状态名。

| 当前状态 | 触发事件 | 守卫条件 | 下一状态 | 同一事务副作用 | 恢复动作 |
|---|---|---|---|---|---|
| `planned` | `page_approved` | PageSpec 已通过 Schema 校验 | `queued` | 固化本次 PageSpec 版本 | 等待调度 |
| `queued` | `page_started` | Run 为 `running` | `running` | 设置 `page_stage` 并写事件 | 无 |
| `running` | `budget_reservation_denied` | 原子预留失败 | `waiting_for_budget` | 创建预算 Interrupt；Step 和 Run 转 `waiting_for_user` | 等待 `budget_interrupt_resumed` 或 `cancel_requested` |
| `waiting_for_budget` | `budget_interrupt_resumed` | 用户已增额、选择降级或释放了足够预算 | `queued` | 解决 Interrupt 并保存用户选择 | 重新调度 |
| `running` | `page_interrupt_created` | 需要页面级人工输入 | `waiting_for_user` | 创建 Interrupt；Step 和 Run 同步等待 | Resume 后转 `queued` |
| `waiting_for_user` | `page_interrupt_resumed` | 关联 Interrupt 已解决且 Payload 有效 | `queued` | 保存 Resume 输入 | 重新调度 |
| `running` | `page_completed` | 最终 Page Artifact 为 `available` 且质量门通过 | `succeeded` | 保存当前页面版本 | 无 |
| `succeeded` | `upstream_invalidated` | 依赖规则判定受影响 | `stale` | 保存失效原因和传播边 | 用户执行时转 `queued` |
| `stale` | `regeneration_requested` | 用户或 Workflow 确认重新计算 | `queued` | 固化新的输入依赖 | 重新调度 |
| `queued`、`running`、`waiting_for_budget`、`waiting_for_user`、`recovery_required`、`stale` | `cancel_requested` | Run 或页面收到取消 | `cancel_requested` | 禁止新 Provider Attempt | 清理后转 `cancelled` |
| `cancel_requested` | `page_cancellation_completed` | 本地任务结束且远端副作用已隔离 | `cancelled` | 保存取消结果 | 无 |
| `running` | `page_failed` | 页面错误不可恢复 | `failed` | 保存错误，不自动影响其他页 | 用户可重试并转 `queued` |
| `failed` | `manual_retry_requested` | 用户确认重试且预算、输入有效 | `queued` | 创建新的页面执行代次 | 重新调度 |
| `running` | `process_restarted` | 上次状态没有完成提交 | `recovery_required` | 保存恢复原因 | 执行 Page、Attempt 与 Artifact 对账 |
| `cancel_requested` | `process_restarted` | 页面取消尚未完成 | `cancel_requested` | 保留取消意图 | 继续取消和对账 |
| `recovery_required` | `page_recovery_retry_safe` | 能证明可以安全重放 | `queued` | 保存恢复证据 | 重新调度 |
| `recovery_required` | `page_recovery_user_input_required` | 已创建恢复 Interrupt | `waiting_for_user` | 保存 Interrupt | 等待用户 |
| `recovery_required` | `page_recovery_failed` | 无安全恢复路径 | `failed` | 保存标准错误 | 等待用户手动重试或放弃 |

### Provider Attempt 状态转换

Attempt 状态集合：`prepared`、`submitted`、`provider_running`、`reconciling`、`cancel_requested`、`succeeded`、`failed`、`outcome_unknown`、`cancelled`。

| 当前状态 | 触发事件 | 守卫条件 | 下一状态 | 同一事务副作用 | 恢复动作 |
|---|---|---|---|---|---|
| 不存在 | `attempt_prepared` | Operation 可创建新 Attempt 且预算预留成功 | `prepared` | 创建 Attempt 与 Reservation | 可安全继续提交 |
| `prepared` | `submission_intent_committed` | compare-and-set 成功 | `submitted` | 记录提交意图时间 | 事务提交后才执行网络 I/O |
| `prepared` | `submission_aborted` | 能证明网络 I/O 尚未开始且请求无法提交 | `failed` | 保存错误并释放或结算 Reservation | 按策略决定是否新建 Attempt |
| `submitted` | `provider_job_accepted` | 已获得远端任务 ID | `provider_running` | 保存 `provider_job_id` | 轮询或等待回调 |
| `submitted`、`provider_running` | `result_received` | 响应与输入身份匹配 | `succeeded` | 保存 Usage，提交 Artifact，结算预算 | 无 |
| `submitted`、`provider_running` | `known_failure` | 能确定失败语义和计费结果 | `failed` | 保存错误，结算或释放预留 | 按策略决定是否新建 Attempt |
| `submitted`、`provider_running` | `recovery_started` | 进程重启、断连或结果未落库 | `reconciling` | 记录对账开始事件 | 查询幂等结果或远端任务 |
| `reconciling` | `reconcile_success_found` | 找到明确成功结果 | `succeeded` | 保存结果、Artifact 与费用 | 无 |
| `reconciling` | `reconcile_failure_found` | 找到明确失败结果和计费语义 | `failed` | 保存错误，结算或释放预留 | 按策略决定是否新建 Attempt |
| `reconciling` | `outcome_cannot_be_proven` | 无法证明未提交或未计费 | `outcome_unknown` | Reservation 转 `held_unknown` | 禁止自动新建 Attempt |
| `submitted`、`provider_running`、`reconciling` | `cancel_requested` | Provider 支持取消或需要停止本地等待 | `cancel_requested` | 记录取消请求 | 调用取消或继续对账 |
| `cancel_requested` | `provider_cancel_confirmed` | Provider 明确确认取消及费用 | `cancelled` | 结算或释放预留 | 无 |
| `cancel_requested` | `cancel_outcome_unknown` | 无法确认取消或费用 | `outcome_unknown` | Reservation 转 `held_unknown` | 等待人工决策 |

任何规格需要新增状态时，必须先修改本节转换表、数据库枚举和迁移，再更新派生状态图与 prose。

## Interrupt/Resume

创建 Interrupt 时，在同一 SQLite 事务中：

1. 写入 `workflow_interrupts`。
2. 写入对应事件。
3. 将 Step 和 Run 更新为 `waiting_for_user`。

Resume 时：

1. 校验 Interrupt 尚未解决且 Resume Payload 符合 Schema。
2. 写入新的领域对象版本，例如 `PresentationBriefVersion`。
3. 标记 Interrupt 已解决。
4. 将关联 Step 从 `waiting_for_user` 转为 `pending`，增加执行代次并保存 Resume 输入。
5. 将 Run 改为 `queued` 并交给 TaskSupervisor。

重复提交同一个 Resume 请求必须幂等。

## 页面并发

首版可使用 `asyncio.TaskGroup`，但页面边界必须捕获所有预期异常：

```python
async def run_page_safely(page_id: str) -> PageTaskResult:
    try:
        return await run_page(page_id)
    except asyncio.CancelledError:
        raise
    except ExpectedPageError as exc:
        await persist_page_failure(page_id, exc)
        return PageTaskResult.failed(page_id, exc.code)
```

只有数据库不可用、进程关闭、状态不变量破坏等系统级异常允许逃逸并取消 TaskGroup 中的其他任务。Python 3.12 的 `TaskGroup` 会在任一子任务抛出非 `CancelledError` 异常时取消其他子任务，因此该异常边界属于强制要求。

参考：[Python 3.12 asyncio TaskGroup](https://docs.python.org/3.12/library/asyncio-task.html#task-groups)

## 取消协议

- 用户取消先持久化 `cancel_requested`，再通知内存任务。
- 新页面和新 Provider 调用不得在 `cancel_requested` 后提交。
- 本地可取消任务执行清理后重新抛出 `CancelledError`。
- 不支持取消的远端请求保留 Provider Attempt 和预算预留，进入 `reconciling`。
- 远端最终成功时可以保存 Artifact，但默认不自动将已取消 Run 变回成功；由用户选择是否采用结果。
- 已提交但结果未知的费用不能因为本地取消而立即释放。

## 优雅关闭

默认关闭过程：

1. 停止接受新的昂贵操作。
2. 持久化所有 Run 的关闭原因。
3. 给本地步骤有限的清理时间。
4. 对可查询的远端调用保存 Provider Attempt 和远端任务 ID。
5. 超时后取消本地 Task，并把未完成步骤标记为 `recovery_required`。

关闭等待时间必须配置化并有上限。不得为了等待远端生图无限阻塞进程退出。

## 启动恢复

启动时按状态处理：

- `queued`：重新提交给 TaskSupervisor。
- `running`：改为 `recovery_required`，检查最后 Step 和 Provider Attempt，不直接重放。
- `waiting_for_user`：保持等待。
- `waiting_for_retry`：到期后转为 `queued` 并重新调度。
- `cancel_requested`：完成取消或远端对账。
- `succeeded/failed/cancelled`：不执行。

任何包含 `submitted`、`provider_running`、`reconciling` 或 `outcome_unknown` Provider Attempt 的 Step 必须先对账。

## TaskSupervisor

TaskSupervisor 负责：

- 跟踪本进程创建的 Run Task。
- 全局、项目和 Provider Semaphore。
- 取消通知和关闭等待。
- 将结构化执行事件写入 SQLite。

TaskSupervisor 不负责持久化队列、租约或多实例抢占。一个 Workspace 同时只允许一个调度实例，并通过文件锁保护。

## 测试

- 页面 A 失败时，页面 B/C 继续执行。
- `CancelledError` 不被吞掉。
- Interrupt 创建与 Run 状态在同一事务中提交。
- 重复 Resume 不产生重复步骤。
- Resume 后 Run 进入 `queued`，不会跳过调度直接进入 `running`。
- 进程在每个状态边界退出后均可恢复。
- 远端请求未完成时取消不会释放未知费用。
- 优雅关闭超时后生成明确的 `recovery_required` 状态。
