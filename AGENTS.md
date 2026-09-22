# PPT Agent 仓库执行指南

本文件是仓库级开发约束与进度检查点，供 Codex、Claude Code 等编码 Agent 在新会话或上下文清理后恢复工作。它不是产品运行时的 Agent 定义，也不替代 [README.md](README.md) 与 `docs/specs/` 下的正式设计规格。

## 1. 产品目标

建设一个面向个人用户、本地优先、可自行配置模型 API Key 的生产级 PPT Agent：

- 输入文本、参考图、Markdown、PDF 等资料。
- 通过确定性 Workflow 规划 Brief、DeckOutline、PageSpec 和 DesignSystem；意图不明确时保存 Checkpoint，并以表单中断/恢复。
- 默认生成 8 页，生成前允许修改每页内容、排版和样式。
- 图片模型负责背景、插图与装饰；交付文字由 HTML/CSS/SVG 等确定性排版后合成为整页图片。
- 支持单页重生成，以及“自然语言定位 + 图片局部编辑”的一期局部修改。
- 输出 PNG/JPG、ZIP、PDF 和图片型 PPTX。
- 重点保证成本可控、可恢复、可观测和稳定，而不是只完成演示链路。

## 2. 源码目录约定

Python 包使用标准 **src layout**：

```text
src/ppt_agent/       # 可安装的 Python 包
tests/               # 单元、集成、契约和端到端测试
migrations/          # Alembic 数据库迁移
docs/specs/          # 已确认的正式技术协议
```

不要把 `src/ppt_agent` 移到仓库根目录。`pyproject.toml` 中的 wheel 包路径和 pytest 导入路径均以此为准。src layout 可以避免测试或开发命令意外导入“尚未安装的仓库根目录代码”，使本地行为更接近构建后的安装包。

不要创建含义模糊的 `agent.md` 作为运行入口。产品 Agent 将由以下部分共同组成：

- `workflows/`：顶层确定性 Workflow 与 Sub-workflow。
- `agents/`：被 Workflow 约束的 ReAct Agent。
- `tools/`：澄清、解析、定位、导出等工具。
- `skills/`：版本化 Prompt、Schema 和能力说明。
- `runtime/`：状态机、TaskSupervisor、中断恢复和启动扫描。

这些目录应在对应模块开始实现时创建，不为展示架构而提前生成空目录。

## 3. 已确认且不得擅自回退的架构决策

- Python 3.12、FastAPI、SQLAlchemy 2、Alembic、SQLite；前端计划使用 React。
- 单进程模块化单体；一个 Workspace 同时只允许一个调度实例。
- 首版不使用 LangGraph。运行时采用显式状态机、SQLite Checkpoint 和 Interrupt/Resume 协议。
- 首版不引入独立 Worker、Redis、Celery、RabbitMQ、租约或 heartbeat。
- 浏览器断开不取消后台任务；FastAPI lifespan 内的 TaskSupervisor 负责进程内任务。
- 顶层使用确定性 Workflow；受限 ReAct 用于局部开放问题；局部编辑使用 Sub-workflow 或 Sub-agent。
- 文本模型首版适配 DeepSeek；图片模型首版适配 Seedream；视觉理解优先使用 Qwen 云 API，并预留豆包适配。
- Provider 必须通过统一能力适配层接入，不能让 Workflow 假设所有 Provider 语义相同。
- 远端调用使用 Operation/Attempt 两层状态，`outcome_unknown` 不得自动重发。
- 所有昂贵调用先进行 SQLite 原子预算预留；未知结果的预算预留不能自动释放。
- Artifact 使用多阶段提交、内容寻址、依赖图和启动对账。
- SSE 只是持久化事件的投影，不能决定 Workflow 状态。
- 一期交付图片型 PPTX，不承诺元素级可编辑 PPTX。
- 画布框选/涂抹、离线评测、本地视觉模型和元素级编辑属于二期。

正式约束以以下文件为准：

1. `docs/specs/01-hybrid-rendering.md`
2. `docs/specs/02-workflow-runtime.md`
3. `docs/specs/03-provider-operations.md`
4. `docs/specs/04-cost-ledger.md`
5. `docs/specs/05-artifact-lifecycle.md`
6. `docs/specs/06-event-stream.md`
7. `docs/specs/07-local-security-and-storage.md`

如 README、规格和实现计划冲突，先指出冲突；正式规格优先于概述性 README，用户最新明确决策优先于旧文档。

## 4. 开发与提交纪律

用户要求在当前分支开发，不新建 worktree。每次只完成一个完整模块：

1. 读取相关规格、现有接口和最近提交。
2. 先写能够因缺失行为而失败的测试，并确认失败原因正确。
3. 实现最小完整模块，处理错误、并发、恢复和迁移边界。
4. 运行模块测试，再运行全量测试。
5. 进行独立代码审查；Critical 和 Important 问题必须在提交前修复并增加回归测试。
6. 运行 `compileall` 与 `git diff --check`。
7. 每个模块只做一次功能提交，不把下一个模块混入同一提交。

开发环境已经位于 `.venv`。PowerShell 常用验证命令：

```powershell
& '.\.venv\Scripts\pytest.exe' -q --basetemp='.cache/pytest-final'
& '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations
git diff --check
git status --short
```

编辑文件使用补丁方式；保留用户已有改动。不要使用 `git reset --hard`、`git checkout --` 或其他可能丢失工作区内容的命令。

## 5. 当前实现进度

当前分支：`feat/ppt`。

已完成并提交的基础模块：

| Commit | 模块 |
|---|---|
| `793ce91` | FastAPI 应用骨架与健康检查 |
| `7c2204d` | Run、Step、Page、Provider Attempt 正式状态机 |
| `c2509b9` | 版本化 Workflow 领域契约 |
| `ad14a63` | SQLite 事务与可靠性配置基础 |
| `eda961b` | Workflow 持久化 Schema |
| `86f2046` | 状态转换与事件原子持久化 |
| `ce08bab` | 可恢复 Interrupt/Resume 协议 |
| `8a191ba` | 原子成本预留与结算账本 |
| `17ed984` | Provider Operation/Attempt 与远端恢复协议 |
| `09196e9` | Artifact 生命周期、提交、依赖、失效、GC 和对账 |
| `adb5364` | Workflow 事件快照、游标续传和 SSE 投影 |

截至 `adb5364`：

- 全量测试为 `121 passed`。
- 事件序号通过 Run 行上的原子 `UPDATE ... RETURNING` 分配。
- 状态变化与核心事件在同一事务提交。
- SSE 支持一致快照、`Last-Event-ID`、游标过期和有界批量读取。
- 事件持久化公共入口强制执行 Payload 大小、敏感字段和 event type 安全策略。
- ArtifactStore 已实现 `reconcile()`，但尚未接入 FastAPI 启动流程。
- SSE 路由已经要求注入 `authorize_run`，但本地令牌、Host/Origin 和会话安全尚未实现。

## 6. 下一步

下一个完整模块是 **本地安全与 Workspace 存储能力**，以 `docs/specs/07-local-security-and-storage.md` 为准。建议先明确并测试以下边界，再编码：

- Workspace 根路径规范化、路径穿越和符号链接/Windows junction 逃逸防护。
- 数据目录能力探测：本地文件系统、原子重命名、文件锁、WAL 支持和回退策略。
- 单实例 Workspace 锁。
- 首次启动本地访问令牌及安全存储抽象；API 的 Host、Origin 和认证依赖。
- 上传大小、像素数、页数、压缩比和解析超时的配置模型。
- 安全 ZIP 路径校验。

该规格较大。如果实现时单个模块边界过宽，应按“Workspace/路径安全基础”和“本地 HTTP 认证边界”拆成两个连续提交，但不得留下默认开放的生产 API。

随后需要实现 **应用组合与 TaskSupervisor/启动恢复**：

- 获取 Workspace 排他锁。
- 执行迁移和完整性检查。
- 在 readiness 和任务恢复前调用 `ArtifactStore.reconcile()`。
- 对 Provider Attempt 先进入 reconciling，而不是统一重发 running 任务。
- 恢复 queued/recovery_required 工作，并处理优雅关闭。

## 7. 已知待办与审查结论

- Artifact 启动对账尚未接线，必须在 TaskSupervisor 启动前完成。
- Artifact 维护操作需要由 Workspace 维护锁保护；跨 Store 并发文件移动仍应继续强化测试。
- `artifact_references.project_id` 与 Artifact 项目一致性目前由服务层保证，数据库尚无复合外键。
- SSE 具体的令牌和 Run 所有权校验由本地安全模块实现；当前只有强制注入的授权回调边界。
- SSE 保留任务的调度与配置尚未实现；清理按服务端 `recorded_at` 判断。
- Provider、Renderer、OCR、前端和实际 Deck Workflow 均尚未进入实现阶段。

这些待办不能因上下文清理而丢失，也不应在无关模块中顺手实现。

## 8. 会话恢复检查清单

新会话开始时：

1. 读取本文件、README 和即将实现模块对应的规格。
2. 执行 `git status --short`，不得覆盖未提交的用户改动。
3. 执行 `git log --oneline -12`，确认进度表与实际 HEAD。
4. 运行相关模块测试，确认基线可复现。
5. 继续“一个模块、测试先行、审查、全量验证、一次提交”的节奏。

