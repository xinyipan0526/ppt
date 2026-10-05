# HTTP API 教程

以下 PowerShell 示例假设服务运行在 `http://localhost:8000`。先读取启动时显示的 Token：

```powershell
$base = 'http://localhost:8000'
$token = Get-Content 'workspace/.secrets/access-token' -Raw
$headers = @{ Authorization = "Bearer $($token.Trim())"; Origin = $base }
```

## 1. 创建项目并上传素材

```powershell
$project = Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects" -Headers $headers `
  -ContentType 'application/json' -Body '{"name":"季度复盘","budget_minor_units":100000,"currency":"CNY"}'

Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects/$($project.id)/materials?filename=notes.md" `
  -Headers $headers -ContentType 'text/markdown' -InFile './notes.md'
```

等待素材状态为 `ready`。`GET .../materials/{id}/attempts` 可查看完整解析尝试历史。

## 2. 规划、澄清与复核

```powershell
$body = @{ normalized_text='为管理层制作 8 页季度复盘'; material_ids=@() } | ConvertTo-Json
$planning = Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects/$($project.id)/planning-runs" `
  -Headers $headers -ContentType 'application/json' -Body $body
$snapshot = Invoke-RestMethod -Uri "$base/api/v1/runs/$($planning.run_id)/snapshot" -Headers $headers
```

Run 进入 `waiting_for_user` 时，从 snapshot 的 `interrupts` 读取表单和值，提交：

```powershell
$interrupt = $snapshot.interrupts | Where-Object status -eq 'pending' | Select-Object -First 1
$resume = @{ expected_version=$interrupt.values.version; payload=@{ action='approve' } } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "$base/api/v1/runs/$($planning.run_id)/interrupts/$($interrupt.id)/resume" `
  -Headers $headers -ContentType 'application/json' -Body $resume
```

## 3. 生成、编辑与导出

规划成功后创建 production run：

```powershell
$production = Invoke-RestMethod -Method Post -Uri "$base/api/v1/projects/$($project.id)/production-runs" `
  -Headers $headers -ContentType 'application/json' `
  -Body (@{ planning_run_id=$planning.run_id } | ConvertTo-Json)
```

查询 `GET /api/v1/runs/{run_id}/production`。可调用 `/pages/{page_id}/regenerate` 或 `/pages/{page_id}/edits`；模糊编辑会返回可恢复的澄清状态。取消运行使用 `POST /api/v1/runs/{run_id}/cancel`。

```powershell
$export = Invoke-RestMethod -Method Post -Uri "$base/api/v1/runs/$($production.run_id)/exports" `
  -Headers $headers -ContentType 'application/json' -Body '{"format":"pptx"}'
Invoke-WebRequest -Uri "$base/api/v1/exports/$($export.export_id)/download" -Headers $headers -OutFile deck.pptx
```

导出格式支持 `png`、`jpg`、`png_zip`、`jpg_zip`、`pdf`、`pptx`，下载支持单段 HTTP Range 续传。SSE 使用 `Last-Event-ID` 从持久化事件游标恢复，不拥有后台任务生命周期。

## 4. 未知远端结果

查询 `GET /api/v1/operations/{operation_id}`。若 Attempt 为 `outcome_unknown`，系统不会自动重发；确认远端确无可回收结果并接受重复计费风险后，调用 `/operations/{operation_id}/authorize-retry`，提交 `expected_attempt_id` 和说明。成本汇总见 `GET /api/v1/projects/{project_id}/costs`。

只有 Provider 明确确认“未计费”且错误标记为 `retryable` 时，服务才会保存到期时间并自动重新入队。普通本地失败进入 `recovery_required` 后，可在确认失败点不含不确定远端调用时调用 `POST /api/v1/runs/{run_id}/authorize-local-retry`；接口会再次复核 Attempt 状态，存在不确定结果时拒绝授权。
## 5. Inspecting a Run trace

Use the same authenticated `$headers` from the examples above:

```powershell
$trace = Invoke-RestMethod -Uri "$base/api/v1/runs/$($planning.run_id)/trace?event_limit=100&operation_limit=50" -Headers $headers
$trace.run | Select-Object id, project_id, workflow_name, status, error
$trace.events | Select-Object run_seq, event_type, occurred_at, step_id, operation_id, trace_id
```

The response is a bounded read projection containing `run`, `steps`, `events`,
and `operations`. Each operation contains safe metadata and bounded attempt
summaries. The endpoint enforces Run/project authorization; a Run from another
project is denied. `event_limit` defaults to 100 and `operation_limit` to 50,
and values above the configured caps return HTTP 422. Raw input text, source
material, provider request/response bodies, authorization values, and API keys
are intentionally absent.

For a local emergency audit while the service is stopped, query SQLite in
read-only mode and select only safe trace columns (never `payload`):

```powershell
& '.\\.venv\\Scripts\\python.exe' -c "import sqlite3; db=sqlite3.connect('workspace/state.db'); db.execute('PRAGMA query_only=ON'); print(*db.execute(\"SELECT run_seq,event_type,occurred_at,step_id,operation_id,trace_id FROM workflow_events WHERE run_id=? ORDER BY run_seq LIMIT 100\", ('<run-id>',)), sep='\\n'); db.close()"
```
