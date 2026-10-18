# Domain Consistency and Maintainability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛生产聚合状态、事件兼容、Provider 重复逻辑和触达模块的静态类型债，同时保持已有数据库与 API 兼容。

**Architecture:** 领域层提供 StrEnum 和纯转换函数，持久化层只通过显式事务方法写状态。Provider 共享无状态 helper 而非继承基类；事件读取兼容历史值，新增写入统一 snake_case。

**Tech Stack:** Python 3.12、SQLAlchemy 2、Alembic、pytest、ruff、mypy。

**Spec:** `docs/superpowers/specs/2026-10-06-analysis-report-remediation-design.md`

## Global Constraints

- 不重写历史数据库 event_type，不重编号 run_seq。
- 不改变公开 API 中既有状态字符串。
- 每个 Provider 保留独立“可证明未计费”白名单。
- 不实现没有官方证据支持的 Ark 幂等请求头。
- ruff/mypy 只覆盖本批次触达模块，禁止全仓格式化。
- 若 `.git` 只读，将检查点记录到 SDD ledger。

## Review Focus

- 旧数据库中的 `step.Completed` 等历史事件必须仍可读取；Task 2 覆盖。
- 非连续 ephemeral 删除后有效 cursor 不应被错误重编号或漏事件；Task 2 覆盖。
- Provider 共用 helper 不得把 429/5xx 误标为确定未计费；Task 3 覆盖。
- 状态迁移必须保持 API 字符串和 Alembic 升级兼容；Task 1 覆盖。
- 静态工具配置不得扫描 workspace、静态 bundle 或 live secret；Task 4 覆盖。

---

### Task 1: 生产聚合 StrEnum 与显式转换

**Files:**
- Create: `src/ppt_agent/domain/production_state.py`
- Create: `tests/unit/domain/test_production_state.py`
- Modify: `src/ppt_agent/storage/models.py`
- Modify: `src/ppt_agent/workflows/production_store.py`
- Modify: `src/ppt_agent/workflows/deck_generations.py`
- Modify: `src/ppt_agent/workflows/page_editing.py`
- Modify: `src/ppt_agent/workflows/exporting.py`
- Modify: `src/ppt_agent/workflows/production_service.py`
- Test: `tests/integration/workflows/test_deck_generations.py`
- Test: `tests/integration/workflows/test_page_editing_workflow.py`
- Test: `tests/integration/workflows/test_exporting.py`

**Interfaces:**
- Produces: `DeckGenerationStatus`、`PageEditStatus`、`ExportStatus` 与 `transition_*` 纯函数。
- Consumes: 现有字符串值 `queued/running/succeeded/failed/cancelled/available/stale`，公开序列化不变。

- [ ] **Step 1: 写失败测试**：枚举值集合、合法转换、非法转换、数据库 round-trip 与 API 字符串兼容。
- [ ] **Step 2: 运行 RED**：domain 与三个 workflow 集成测试。
- [ ] **Step 3: 实现枚举和转换表**，将 touched write path 改为显式转换；查询使用枚举成员比较。
- [ ] **Step 4: 运行 GREEN**：相关测试、迁移兼容检查与 `compileall`。
- [ ] **Step 5: 记录检查点**：`refactor: type production aggregate states`。

### Task 2: Step 事件 ID 与 replay floor

**Files:**
- Modify: `src/ppt_agent/domain/state_machine.py`
- Modify: `src/ppt_agent/runtime/repository.py`
- Modify: `src/ppt_agent/events/store.py`
- Modify: `src/ppt_agent/events/sse.py`
- Modify: `tests/unit/domain/test_state_machine.py`
- Modify: `tests/integration/runtime/test_workflow_repository.py`
- Modify: `tests/integration/events/test_event_store.py`
- Modify: `tests/integration/events/test_sse.py`

**Interfaces:**
- Produces: 明确的 `StepResultKind` 与 snake_case `StepEvent` id；`replay_floor(run_id)` 或等价持久化连续前缀语义。
- Consumes: 历史 PascalCase/snake_case event type，并保持 SSE 客户端兼容。

- [ ] **Step 1: 写失败测试**：新写入全为规范 snake_case；历史值可解析；裁剪中间 ephemeral 不让未过期 cursor 假失效；真正早于 floor 才 cursor_expired。
- [ ] **Step 2: 运行 RED**：state machine、repository、event store、SSE 测试。
- [ ] **Step 3: 分离结果类名和事件 id**，保留只读兼容映射，删除新写入对 `_event_identifier` 正则兜底的依赖。
- [ ] **Step 4: 实现 replay floor/连续前缀裁剪**，保留显式 SQLite `BEGIN` 一致快照。
- [ ] **Step 5: 运行 GREEN**：相关测试及并发 snapshot 用例。
- [ ] **Step 6: 记录检查点**：`fix: normalize step events and replay retention`。

### Task 3: Provider HTTP/Usage helper 与计费契约

**Files:**
- Create: `src/ppt_agent/providers/http_common.py`
- Create: `src/ppt_agent/providers/usage.py`
- Modify: `src/ppt_agent/providers/deepseek.py`
- Modify: `src/ppt_agent/providers/ark.py`
- Modify: `src/ppt_agent/providers/seedream.py`
- Modify: `src/ppt_agent/providers/qwen.py`
- Modify: `tests/contract/providers/test_deepseek.py`
- Modify: `tests/contract/providers/test_ark_multimodal.py`
- Modify: `tests/contract/providers/test_seedream.py`
- Modify: `tests/contract/providers/test_qwen.py`
- Modify: `tests/integration/providers/test_text_runner.py`

**Interfaces:**
- Produces: `classify_http_failure(status, provider_policy) -> FailureClassification`、共用 usage 数值读取 helper。
- Consumes: Provider 特有 `provably_uncharged_statuses` 与响应字段映射。

- [ ] **Step 1: 写失败测试**：四 Adapter 的既有错误码输出不变；429/5xx unknown 不释放；malformed usage 拒绝；reasoning_tokens 不重复加到 output 费用。
- [ ] **Step 2: 运行 RED**：四个 contract 与 text runner 测试。
- [ ] **Step 3: 实现无状态 helper 并迁移 Adapter**，禁止共享 helper 自行猜测 Provider 计费语义。
- [ ] **Step 4: 验证 Ark 请求仍不发送未证明支持的幂等头**。
- [ ] **Step 5: 运行 GREEN**：contract/provider runner 套件。
- [ ] **Step 6: 记录检查点**：`refactor: share provider HTTP and usage parsing`。

### Task 4: 原子规划状态写入与 placeholder 清理

**Files:**
- Modify: `src/ppt_agent/workflows/planning_store.py`
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Modify: `src/ppt_agent/runtime/repository.py`
- Modify: `tests/integration/workflows/test_deck_planning.py`
- Modify: `tests/integration/runtime/test_workflow_repository.py`

**Interfaces:**
- Produces: PlanningStore 专用原子 `complete_*` 方法，经正式事件/守卫完成 Run/Step；真实 request fingerprint 来源。
- Consumes: 现有 planning Artifact、Operation 和事件事务边界。

- [ ] **Step 1: 写失败测试**：非法直接终态被拒绝；完成状态与事件同事务；崩溃回滚不留半状态；fingerprint 非 placeholder 且重放稳定。
- [ ] **Step 2: 运行 RED**：planning workflow 与 repository 测试。
- [ ] **Step 3: 将直接赋值收敛到显式事务方法/正式事件**；从实际结构化请求生成 fingerprint，移除 `computed-by-text-runner`。
- [ ] **Step 4: 运行 GREEN**：相关集成测试。
- [ ] **Step 5: 记录检查点**：`refactor: enforce planning transition boundaries`。

### Task 5: 类型协议与渐进静态检查

**Files:**
- Create: `src/ppt_agent/storage/protocols.py`
- Modify: `src/ppt_agent/runtime/recovery.py`
- Modify: `src/ppt_agent/rendering/constants.py`
- Modify: `src/ppt_agent/runtime/backend.py`
- Modify: `pyproject.toml`
- Modify: `docs/quickstart.md`
- Modify: `tests/integration/runtime/test_recovery.py`

**Interfaces:**
- Produces: recovery 所需最小 Session Protocol；ruff/mypy targeted 配置与命令。
- Consumes: 批次 B 的画布常量；现有 SQLAlchemy Session 行为。

- [ ] **Step 1: 写/调整测试**：recovery fake session 满足 Protocol；画布换算结果不变。
- [ ] **Step 2: 配置 dev 依赖 `ruff`、`mypy` 和 SQLAlchemy/Pydantic 所需设置并更新 Quickstart 命令**，只包含本计划触达模块，排除 static/workspace/migrations 生成内容。
- [ ] **Step 3: 替换 recovery 中宽泛 `object` 和本任务触达的无必要 ignore**，不进行全仓清理。
- [ ] **Step 4: 运行 GREEN**：目标 pytest、ruff、mypy、`compileall`。
- [ ] **Step 5: 记录检查点**：`chore: add targeted static analysis`。

### Task 6: 批次 C 集成验收

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/provider-setup.md`

**Interfaces:**
- Consumes: Tasks 1-5。
- Produces: 状态/事件/Provider 兼容性文档和发布证据。

- [ ] **Step 1: 运行领域、workflow、events、Provider 相关套件和 targeted ruff/mypy**。
- [ ] **Step 2: 运行 Python 全量、React 全量、`compileall`、生产前端构建和 `git diff --check`。
- [ ] **Step 3: 独立 whole-plan review**，检查历史数据库、资金安全和未扩大范围；Critical/Important 清零。
- [ ] **Step 4: 记录检查点**：`test: verify domain consistency milestone`。
