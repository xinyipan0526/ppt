# Product Feedback and Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐取消、可信进度、统一错误、无障碍、首次配置、可恢复异步导出、结构化异常和本地备份运维。

**Architecture:** 继续以 SQLite 为事实源、TaskSupervisor 为单进程任务所有者。前端只消费 snapshot/SSE/查询；导出新增持久化本地调度器，备份复用 Workspace 维护锁与现有 Online Backup 原语。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、SQLite、React 19、TypeScript、TanStack Query、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-06-analysis-report-remediation-design.md`

## Global Constraints

- 当前分支开发，不创建 worktree；保留所有已有未提交改动。
- 暂缓 Provider/页面并发、项目总预算硬顶和运行时价格校验。
- 浏览器不拥有任务生命周期；SQLite 是状态唯一事实源。
- API Key 和原始 Provider payload 不进入浏览器、事件或日志。
- 导出保持纯本地计算，不触发新的 Provider 调用。
- 若 `.git` 仍只读，将提交动作记入 SDD ledger，不尝试绕过权限。

## Review Focus

- 重复取消或终态取消必须幂等且不产生 500；由 Task 1 测试。
- Dialog 在 Shift+Tab、Escape、提交中和卸载时必须恢复焦点；由 Task 2 测试。
- 服务重启后 queued/running 导出必须安全恢复且不重复 Artifact；由 Task 4 测试。
- 备份 manifest 损坏、锁竞争和 restore 失败不得破坏当前数据库；由 Task 5 测试。
- 全局异常响应不得泄漏异常文本、路径或密钥，并可由 correlation id 在日志定位；由 Task 3 测试。

---

### Task 1: Run 取消与可信进度

**Files:**
- Modify: `src/ppt_agent/runtime/supervisor.py`
- Modify: `src/ppt_agent/api/production.py`
- Modify: `web/src/features/runs/RunStatus.tsx`
- Modify: `web/src/api/contracts.ts`
- Test: `tests/integration/runtime/test_supervisor.py`
- Test: `tests/integration/api/test_product_workspace_api.py`
- Test: `web/src/features/runs/RunStatus.test.tsx`

**Interfaces:**
- Consumes: `POST /api/v1/runs/{run_id}/cancel`、`RunSnapshot.pages`。
- Produces: 幂等取消响应；`completed/total/failed` 页进度投影。

- [ ] **Step 1: 写失败测试**：终态重复取消返回已有终态；非终态按钮只提交一次；页面进度严格来自 snapshot。
- [ ] **Step 2: 运行 RED**：`pytest tests/integration/runtime/test_supervisor.py tests/integration/api/test_product_workspace_api.py -q` 与 `npm test -- RunStatus.test.tsx`，确认失败原因是缺失幂等/进度行为。
- [ ] **Step 3: 实现**：让 `TaskSupervisor.cancel(run_id)` 对终态返回 `False`，API 返回实际持久化状态；RunStatus 为可取消状态增加确认按钮及页数汇总，不计算 ETA。
- [ ] **Step 4: 运行 GREEN**：重复上述测试并运行前端 typecheck。
- [ ] **Step 5: 记录检查点**：提交 `feat: add idempotent run cancellation and progress`，或在只读 git 下记录文件与测试证据。

### Task 2: 统一错误与可访问 Dialog

**Files:**
- Create: `web/src/components/ErrorBanner.tsx`
- Create: `web/src/components/Dialog.tsx`
- Create: `web/src/components/Dialog.test.tsx`
- Modify: `web/src/features/projects/ProjectList.tsx`
- Modify: `web/src/features/materials/MaterialPanel.tsx`
- Modify: `web/src/features/deck/PageInspector.tsx`
- Modify: `web/src/features/deck/GlobalRegenerateDialog.tsx`
- Modify: `web/src/features/exports/ExportPanel.tsx`
- Test: `web/src/features/projects/ProjectWorkspace.test.tsx`
- Test: `web/src/features/deck/PageInspector.test.tsx`
- Test: `web/src/features/deck/GlobalRegenerateDialog.test.tsx`

**Interfaces:**
- Consumes: `ApiError.message` 与现有 mutation 状态。
- Produces: `ErrorBanner({error, fallback})`、`Dialog({title, open, onClose, busy, initialFocusRef})`。

- [ ] **Step 1: 写失败测试**：各 mutation 拒绝后存在中文 alert；Dialog 有 `aria-labelledby`、焦点圈定、Escape 和焦点恢复，busy 时不能关闭。
- [ ] **Step 2: 运行 RED**：`npm test -- Dialog.test.tsx ProjectWorkspace.test.tsx PageInspector.test.tsx GlobalRegenerateDialog.test.tsx`。
- [ ] **Step 3: 实现共享组件并替换三个现有 modal 和相关错误文案**，不把 error 对象放进 DOM 属性或 Query key。
- [ ] **Step 4: 运行 GREEN**：相关测试、`npm run typecheck`、`npm run build`；同步 `web/dist` 到 `src/ppt_agent/static`。
- [ ] **Step 5: 记录检查点**：`feat: unify accessible dialogs and mutation errors`。

### Task 3: 首次配置引导、全局异常和维护日志

**Files:**
- Modify: `web/src/features/projects/ProjectWorkspace.tsx`
- Modify: `web/src/features/settings/ProviderSetup.tsx`
- Modify: `src/ppt_agent/api/app.py`
- Modify: `src/ppt_agent/runtime/maintenance.py`
- Modify: `src/ppt_agent/cli.py`
- Test: `web/src/features/projects/ProjectWorkspace.test.tsx`
- Test: `tests/unit/api/test_app.py`
- Test: `tests/unit/runtime/test_event_retention.py`
- Create: `tests/unit/runtime/test_provider_retry_task.py`
- Test: `tests/integration/cli/test_cli.py`

**Interfaces:**
- Consumes: `GET /api/v1/provider-settings` 的 mode/configured 投影、`ppt_agent` JSONL logger。
- Produces: 稳定 `internal_error` + `correlation_id` 响应；节流维护错误日志；cloud 设置 CTA。

- [ ] **Step 1: 写失败测试**：cloud 未配置显示阻断卡、deterministic 不阻断；未知 API 异常响应不含原文但日志含 correlation id；连续维护失败首条及节流摘要可审计。
- [ ] **Step 2: 运行 RED**：执行上述四个测试文件的目标用例。
- [ ] **Step 3: 实现**：加入全局异常 handler 和 correlation id；维护任务用命名 logger 与有界重复计数；CLI 增加 `--debug` 决定 traceback；项目页接配置状态。
- [ ] **Step 4: 运行 GREEN**：相关 pytest、前端测试和 typecheck。
- [ ] **Step 5: 记录检查点**：`feat: add actionable setup and failure observability`。

### Task 4: 持久化异步导出

**Files:**
- Create: `src/ppt_agent/runtime/export_supervisor.py`
- Create: `tests/integration/runtime/test_export_supervisor.py`
- Create: `migrations/versions/0013_async_exports.py`
- Modify: `src/ppt_agent/storage/models.py`
- Modify: `src/ppt_agent/workflows/exporting.py`
- Modify: `src/ppt_agent/runtime/backend.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Modify: `src/ppt_agent/runtime/composition.py`
- Modify: `src/ppt_agent/api/production.py`
- Test: `tests/integration/workflows/test_exporting.py`
- Test: `tests/integration/api/test_product_workspace_api.py`
- Test: `tests/integration/runtime/test_application.py`

**Interfaces:**
- Consumes: `ExportWorkflow.create/status/export` 和 `ExportRecord`。
- Produces: `ExportSupervisor.schedule(export_id) -> bool`、启动扫描、快速返回 `{export_id,status:"queued"}`。

- [ ] **Step 1: 写失败测试**：POST 不等待编码；同 fingerprint 去重；queued/running 启动恢复；available 不重跑；失败持久化安全 error；关闭有界。
- [ ] **Step 2: 运行 RED**：运行 exporting、export supervisor、API、application 目标测试。
- [ ] **Step 3: 增加迁移与显式 Export 状态转换**，为本地可重放任务保存 attempt/start/finish 信息；不得引入远端 Operation。
- [ ] **Step 4: 实现 `ExportSupervisor` 并接入 Runtime 生命周期和 API**；`ExportWorkflow.export` 需幂等提交 Artifact，页面变化转 stale/failed。
- [ ] **Step 5: 运行 GREEN**：相关 pytest、迁移升级测试和 `compileall`。
- [ ] **Step 6: 记录检查点**：`feat: make exports durable background tasks`。

### Task 5: 导出 UI 与质量参数

**Files:**
- Modify: `src/ppt_agent/domain/exports.py`
- Modify: `src/ppt_agent/exports/images.py`
- Modify: `src/ppt_agent/exports/pdf.py`
- Modify: `src/ppt_agent/workflows/exporting.py`
- Modify: `src/ppt_agent/api/production.py`
- Modify: `web/src/api/contracts.ts`
- Modify: `web/src/features/exports/ExportPanel.tsx`
- Create: `web/src/features/exports/ExportPanel.test.tsx`
- Test: `tests/unit/exports/test_exporters.py`
- Test: `tests/integration/workflows/test_exporting.py`

**Interfaces:**
- Consumes: Task 4 的 Export 查询 API。
- Produces: 有界 `jpeg_quality`（默认 92）与 `pdf_dpi`（默认 144）；刷新可恢复的导出状态 UI。

- [ ] **Step 1: 写失败测试**：参数越界 422；默认输出身份不变；页面刷新后 queued/running 继续轮询；available 才显示下载；失败显示安全 detail。
- [ ] **Step 2: 运行 RED**：exporter/workflow pytest 与 `npm test -- ExportPanel.test.tsx`。
- [ ] **Step 3: 实现参数 Schema、manifest 指纹和 exporter 透传**；前端创建后轮询同一 export id，避免重复创建。
- [ ] **Step 4: 运行 GREEN**：相关测试、typecheck/build，并同步静态资源。
- [ ] **Step 5: 记录检查点**：`feat: add recoverable configurable exports`。

### Task 6: 备份恢复、自动备份与容器探针

**Files:**
- Create: `src/ppt_agent/runtime/backup_task.py`
- Create: `tests/unit/runtime/test_backup_task.py`
- Modify: `src/ppt_agent/config/settings.py`
- Modify: `src/ppt_agent/cli.py`
- Modify: `src/ppt_agent/runtime/application.py`
- Modify: `src/ppt_agent/storage/maintenance.py`
- Modify: `tests/integration/storage/test_maintenance.py`
- Modify: `tests/integration/cli/test_cli.py`
- Modify: `tests/unit/config/test_settings.py`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `docs/quickstart.md`
- Modify: `docs/configuration.md`

**Interfaces:**
- Consumes: `create_online_backup`、`restore_backup`、`retain_recent_backups`、Workspace maintenance lock。
- Produces: `ppt-agent backup`、`ppt-agent restore --dry-run/--confirm`；每日备份默认 86400 秒、保留 7 份。

- [ ] **Step 1: 写失败测试**：CLI 参数、dry-run 无写入、manifest 损坏拒绝、锁竞争、失败回滚、自动任务节流与保留 7 份、配置校验。
- [ ] **Step 2: 运行 RED**：storage/CLI/settings/backup task 测试。
- [ ] **Step 3: 实现 CLI 和 `BackupTask`**，只在应用 ready 后启动，关机有界；restore 真正替换必须 `--confirm` 且服务未持锁运行。
- [ ] **Step 4: 增加镜像默认 production 和 `/api/v1/ready` HEALTHCHECK**，测试探针不需要泄漏认证凭据。
- [ ] **Step 5: 更新 Quickstart/configuration 并运行 GREEN**：相关 pytest、Dockerfile 静态断言、`compileall`。
- [ ] **Step 6: 记录检查点**：`feat: add local backup operations and health probe`。

### Task 7: 批次 A 集成验收

**Files:**
- Modify: `web/e2e/product-flow.spec.ts`
- Modify: `tests/e2e/test_deterministic_deck.py`
- Modify: `docs/ui-guide.md`

**Interfaces:**
- Consumes: Tasks 1-6 的公开 API/UI。
- Produces: 刷新恢复的取消、导出、错误、备份文档证据。

- [ ] **Step 1: 增加 E2E 场景**：配置阻断、取消、异步导出刷新恢复、错误 alert 与键盘 Dialog。
- [ ] **Step 2: 运行前后端相关套件、React 全量、Chromium E2E、生产构建、`compileall`、`git diff --check`。
- [ ] **Step 3: 独立 whole-plan review**，Critical/Important 清零后完成批次 A。
- [ ] **Step 4: 记录检查点**：`test: verify product feedback and operations milestone`。
