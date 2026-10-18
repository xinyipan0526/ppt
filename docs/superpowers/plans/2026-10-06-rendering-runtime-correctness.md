# Rendering and Runtime Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现确定性文字自适应、六类布局、容错 OCR、串行页面故障隔离和跨重启 Run 上界。

**Architecture:** Renderer 继续只接受版本化 PageSpec 和受控模板，不执行模型 HTML/JS。页面处理保持串行，但用结构化结果隔离预期失败；Run deadline 持久化并由 Workflow 边界检查。

**Tech Stack:** Python 3.12、Pydantic、Playwright Chromium、Pillow、SQLAlchemy 2、Alembic、pytest。

**Spec:** `docs/superpowers/specs/2026-10-06-analysis-report-remediation-design.md`

## Global Constraints

- 固定 1920×1080、固定 device scale factor 和批准字体集合。
- 暂不引入页面 TaskGroup 或 Provider 并发。
- 不使用省略号隐藏标题、关键数字、引用或完整正文。
- deterministic OCR 仅用于无 Key 流程，不宣称真实视觉校验。
- 规划 Run 默认 30 分钟，生产 Run 默认 2 小时；重启不重置 deadline。
- 若 `.git` 只读，将检查点记入 SDD ledger。

## Review Focus

- 中英文混排、长 URL、无空格文本不得产生非确定性字号；Task 2 覆盖。
- min_font_size 仍溢出必须明确失败而不是裁切；Task 2 覆盖。
- 关键数字 OCR 不允许模糊匹配吞掉数值变化；Task 3 覆盖。
- 页面 A 预期失败后 B/C 必须继续，系统异常必须停止；Task 4 覆盖。
- 进程重启后的 deadline 与接近边界的完成竞态必须确定；Task 5 覆盖。

---

### Task 1: 画布常量与六类布局注册表

**Files:**
- Create: `src/ppt_agent/rendering/constants.py`
- Create: `src/ppt_agent/rendering/layout_registry.py`
- Modify: `src/ppt_agent/rendering/layout.py`
- Modify: `src/ppt_agent/runtime/backend.py`
- Test: `tests/unit/rendering/test_layout.py`
- Test: `tests/unit/domain/test_production_contracts.py`

**Interfaces:**
- Produces: `CANVAS_WIDTH=1920`、`CANVAS_HEIGHT=1080`；`LayoutTemplate`；`LAYOUT_TEMPLATES[token]` 覆盖六个正式 Token。
- Consumes: `ProductionPageSpecV1.layout_token`、page type、内容密度和 visual placement。

- [ ] **Step 1: 写失败测试**：六 Token 都编译为安全区内非重叠基础框；未知 Token 拒绝；backend 坐标换算使用共享常量。
- [ ] **Step 2: 运行 RED**：`pytest tests/unit/rendering/test_layout.py tests/unit/domain/test_production_contracts.py -q`。
- [ ] **Step 3: 实现常量、注册表和选择规则**，保持模型只能选 Token，不接受任意 CSS/坐标。
- [ ] **Step 4: 运行 GREEN**：相关单测与 `compileall`。
- [ ] **Step 5: 记录检查点**：`feat: add constrained six-layout registry`。

### Task 2: 确定性 shrink-then-reflow

**Files:**
- Modify: `src/ppt_agent/domain/production.py`
- Modify: `src/ppt_agent/rendering/templates.py`
- Modify: `src/ppt_agent/rendering/chromium.py`
- Modify: `src/ppt_agent/rendering/validation.py`
- Test: `tests/integration/rendering/test_chromium.py`
- Test: `tests/unit/rendering/test_validation.py`

**Interfaces:**
- Consumes: `TextBlockV1.typography.font_size/min_font_size` 与 `overflow`。
- Produces: `LayoutElementReportV1.actual_font_size`（沿用现有 `font_size` 字段）、最终 line height、overflow；确定性适配算法版本。

- [ ] **Step 1: 写失败测试**：自然换行可容纳；需缩字时字号位于边界；中英文/URL 重复运行一致；min 仍失败产生 `layout_overflow`；禁止省略号。
- [ ] **Step 2: 运行 RED**：Chromium 集成与 validation 单测，确认固定字号路径失败。
- [ ] **Step 3: 在受控 Renderer 内实现整数或固定精度二分搜索字号及模板允许的间距调整**；不得执行输入脚本。
- [ ] **Step 4: 更新 DOM report 和 Renderer/cache 版本**，保证实际字号进入质量证据。
- [ ] **Step 5: 运行 GREEN**：相关测试重复两次验证确定性。
- [ ] **Step 6: 记录检查点**：`feat: implement deterministic shrink and reflow`。

### Task 3: 分块 OCR 一致性

**Files:**
- Create: `src/ppt_agent/rendering/text_verification.py`
- Modify: `src/ppt_agent/runtime/backend.py`
- Modify: `src/ppt_agent/providers/deterministic.py`
- Create: `tests/unit/rendering/test_text_verification.py`
- Modify: `tests/unit/providers/test_deterministic.py`
- Modify: `tests/integration/workflows/test_deck_production.py`

**Interfaces:**
- Produces: `verify_ocr_text(expected_blocks, report) -> TextVerificationResult`，区分标题/关键数字严格匹配和正文有界相似度。
- Consumes: `OCRReportV1.full_text/spans` 与 DOM 交付文字块。

- [ ] **Step 1: 写失败测试**：空白/标点/OCR 常见字符误差可容忍；缺段、错数字、标题变化失败；阈值固定且顺序无关。
- [ ] **Step 2: 运行 RED**：`pytest tests/unit/rendering/test_text_verification.py tests/integration/workflows/test_deck_production.py -q`。
- [ ] **Step 3: 实现 block/span 归一化和相似度**；关键数字与标题不得走宽松分支。
- [ ] **Step 4: deterministic provider 在审计元数据中标记 `visual_verification=false`**，不改变无 Key 闭环可运行性。
- [ ] **Step 5: 运行 GREEN**：相关单元/集成测试。
- [ ] **Step 6: 记录检查点**：`fix: harden OCR delivery-text verification`。

### Task 4: 串行页面故障隔离

**Files:**
- Modify: `src/ppt_agent/workflows/deck_production.py`
- Modify: `src/ppt_agent/workflows/production_store.py`
- Modify: `src/ppt_agent/domain/state_machine.py`
- Test: `tests/integration/workflows/test_deck_production.py`
- Test: `tests/integration/runtime/test_workflow_repository.py`

**Interfaces:**
- Produces: `run_page_safely(page_id) -> PageTaskResult`；预期页错误持久化后返回，不逃逸。
- Consumes: 既有 Page/Step/Run 正式状态和事件事务。

- [ ] **Step 1: 写失败测试**：A 发生 Provider/validation/layout 预期错误时 B/C 继续；数据库/状态不变量异常停止；CancelledError 重新抛出。
- [ ] **Step 2: 运行 RED**：deck production 与 repository 目标测试。
- [ ] **Step 3: 定义预期页错误边界和结构化结果**，在现有串行循环逐页调用；不得新增 TaskGroup。
- [ ] **Step 4: 聚合 Run 结果**：部分页失败时保存可诊断终态和全部已完成页，禁止把失败当成功。
- [ ] **Step 5: 运行 GREEN**：相关测试及生产 API 回归。
- [ ] **Step 6: 记录检查点**：`fix: isolate expected page production failures`。

### Task 5: 持久化 Run deadline

**Files:**
- Create: `migrations/versions/0014_run_deadlines.py`
- Modify: `src/ppt_agent/storage/models.py`
- Modify: `src/ppt_agent/config/settings.py`
- Modify: `src/ppt_agent/runtime/repository.py`
- Modify: `src/ppt_agent/runtime/recovery.py`
- Modify: `src/ppt_agent/workflows/deck_planning.py`
- Modify: `src/ppt_agent/workflows/deck_production.py`
- Test: `tests/unit/config/test_settings.py`
- Test: `tests/integration/runtime/test_recovery.py`
- Test: `tests/integration/workflows/test_deck_planning.py`
- Test: `tests/integration/workflows/test_deck_production.py`

**Interfaces:**
- Produces: `WorkflowRunRecord.deadline_at`；配置 `planning_run_timeout_seconds=1800`、`production_run_timeout_seconds=7200`；`ensure_within_deadline(run_id, now)`。
- Consumes: Run 创建时间、workflow_name 和正式 recovery/failed 状态。

- [ ] **Step 1: 写失败测试**：创建时 deadline 正确；接近边界完成成功；越界不发新 Provider；重启不延长；revision 新 Run 获得新 deadline 且无次数硬顶。
- [ ] **Step 2: 运行 RED**：settings/recovery/planning/production 目标测试。
- [ ] **Step 3: 增加迁移、设置与 repository 守卫**，deadline 使用 UTC 持久化。
- [ ] **Step 4: 在每个昂贵步骤和恢复调度前检查 deadline**，持久化安全错误与恢复信息，不删除 checkpoint。
- [ ] **Step 5: 运行 GREEN**：相关测试、迁移升级和 `compileall`。
- [ ] **Step 6: 记录检查点**：`feat: enforce durable workflow deadlines`。

### Task 6: 批次 B 集成验收

**Files:**
- Modify: `tests/e2e/test_deterministic_deck.py`
- Modify: `docs/ui-guide.md`

**Interfaces:**
- Consumes: Tasks 1-5。
- Produces: 长文本、六布局、故障隔离和 deadline 的发布证据。

- [ ] **Step 1: 扩展 deterministic E2E**：8 页包含长中英文、数据页和 closing，验证所有布局可渲染且无静默裁切。
- [ ] **Step 2: 运行 rendering/workflow/runtime 相关套件、后端全量、Chromium E2E、`compileall` 和 `git diff --check`。
- [ ] **Step 3: 独立 whole-plan review**，Critical/Important 清零。
- [ ] **Step 4: 记录检查点**：`test: verify rendering and runtime correctness milestone`。
