# Workflow 事件与 SSE 续传协议

## 目标

让前端在刷新、断网和浏览器重开后恢复一致的 Agent 轨迹，同时确保 SSE 只是持久化状态的投影，不参与 Workflow 决策。

## 事件身份

每个持久化事件包含：

- `event_id`：全局稳定 ID。
- `run_id`：所属 Run。
- `run_seq`：Run 内严格单调递增序号。
- `event_type`：版本化事件类型。
- `occurred_at`：事件发生时间。
- `recorded_at`：数据库提交时间。
- `step_id`、`operation_id`、`page_id`：可选关联。
- `payload`：版本化 JSON 对象。
- `trace_id`：观测关联 ID。

数据库唯一约束为 `(run_id, run_seq)`。`run_seq` 在写事务中分配，不能由多个协程在内存中自行递增。

## 事务要求

影响 Workflow 状态的事件必须与对应状态变化在同一 SQLite 事务中提交。例如创建 Interrupt 时，同时提交 Interrupt、Run 状态和 `workflow.interrupted` 事件。

纯观测事件可以异步批量写入，但不得改变 Workflow 语义，也不能阻塞关键状态事务。

## 快照加增量协议

前端连接流程：

1. 请求 Run 状态快照。
2. 服务端在一致读取中返回实体状态和 `snapshot_seq`。
3. 前端订阅 `run_seq > snapshot_seq` 的 SSE。
4. 前端按 `run_seq` 顺序应用事件，并忽略重复事件。
5. 发现序号缺口时停止增量投影并重新获取快照。

这避免在“先读状态、后订阅事件”的窗口中丢失更新。

## SSE 映射

```text
id: <run_seq>
event: <event_type>
retry: 3000
data: <JSON payload>
```

浏览器重连时发送 `Last-Event-ID`。服务端将其解释为最后确认的 `run_seq`，从下一条可用事件继续发送。

参考：[FastAPI SSE 与 Last-Event-ID](https://fastapi.tiangolo.com/tutorial/server-sent-events/#resuming-with-last-event-id)

## 游标过期

- `workflow_events` 具有配置化保留期。
- 影响审计、成本和状态恢复的核心事件不能仅因 SSE 保留期被删除；可以归档到压缩审计记录。
- 如果 `Last-Event-ID` 早于最早可回放事件，服务端返回 `cursor_expired` 事件并关闭连接。
- 前端收到后重新拉取全量快照，不猜测缺失状态。

## 慢消费者

- SSE 使用有界内存缓冲。
- 慢客户端不会阻塞 Workflow 或数据库提交。
- 缓冲溢出时关闭该连接，客户端通过 Last-Event-ID 重连。
- 大型 Prompt、Response 和 Artifact 不直接放入事件，只发送摘要和资源引用。

## 事件类型示例

- `run.started`
- `step.started`
- `step.completed`
- `step.failed`
- `workflow.interrupted`
- `workflow.resumed`
- `provider.submitted`
- `provider.reconciling`
- `budget.reserved`
- `budget.settled`
- `artifact.available`
- `page.progress`
- `run.cancel_requested`
- `run.cancelled`

事件类型和 Payload Schema 必须版本化。前端对未知的非关键事件应安全忽略。

## 安全与隐私

- SSE 端点必须要求本地访问令牌。
- Run 必须属于当前本地用户上下文。
- Payload 默认不包含完整 Prompt、模型响应、API Key 或用户文件内容。
- 事件中的文件引用只能是服务端资源 ID，不暴露任意本地路径。

## 测试

- 快照与订阅之间发生事件时不丢失。
- 重复事件不产生重复 UI 状态。
- Last-Event-ID 可以续传。
- 游标过期触发全量刷新。
- 慢消费者不会阻塞 Workflow。
- Run 状态和语义事件不会出现一方提交、一方缺失。

