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

用户要求在当前分支开发，不新建 worktree。后续开发以“基础设施里程碑”为批次，不再机械执行“一个模块、一次 RED/GREEN、一次审查、一次提交”的细粒度节奏：

1. 读取批次涉及的正式规格、现有接口和最近提交，先锁定跨模块接口与完成标准。
2. 持久化、状态机、恢复协议、HTTP 安全和应用组合等强依赖基础设施允许合并开发；优先按可验证的纵向链路补测试，不要求每个小函数单独停顿或提交。
3. 一个批次内可以集中编写测试、连续实现多个相邻模块并统一调试，但最终必须覆盖错误、并发、恢复、迁移和安全边界。
4. 在合并批次结束时统一运行相关测试与全量测试，进行一次独立代码审查；Critical 和 Important 问题仍必须修复并增加回归测试。
5. 最终运行 `compileall` 与 `git diff --check`。提交按可恢复检查点拆分，不强制每个内部模块单独提交，也不把未验证的半成品提交为完成状态。
6. 不因批次扩大而顺手实现 Renderer、OCR、前端或 Deck 业务 Workflow；它们不属于当前基础设施里程碑。

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
| `8588af7` | Workspace 路径/ZIP 安全、单实例锁、存储能力探测与 SQLite/Artifact 接线 |
| `81862c1` | 本地 HTTP 安全、恢复协议、TaskSupervisor、备份恢复、应用组合与维护循环 |
| `bc6ad55` | 严格规划领域契约、澄清表单与计划复核协议 |
| `8816bd8` | DeepSeek 结构化文本适配、Operation/Attempt 与计费结算闭环 |
| `0d276e0` | PlanningStore、版本化 Prompt 与确定性 Deck Planning Workflow |
| `2c942d0` | 规划 HTTP API、运行时 Workflow 组合与 opt-in Provider live test |
| 本提交 | 独立审查修复：付费结果落盘、原子复核、定向页修订与安全计费边界 |

截至当前基础设施里程碑：

- 全量测试为 `245 passed, 3 skipped`；两项跳过来自当前 Windows 账户无法创建符号链接，一项为默认关闭的真实 DeepSeek 计费测试。
- 事件序号通过 Run 行上的原子 `UPDATE ... RETURNING` 分配。
- 状态变化与核心事件在同一事务提交。
- SSE 支持一致快照、`Last-Event-ID`、游标过期和有界批量读取。
- 事件持久化公共入口强制执行 Payload 大小、敏感字段和 event type 安全策略。
- Workspace 路径统一执行 lexical 与 canonical containment 校验；ZIP 元数据在解压前拒绝路径穿越、驱动器/UNC 和符号链接条目。
- Workspace 单实例锁使用跨进程 OS 文件锁；数据目录启动探测验证本地文件系统、原子替换、排他锁、`fsync`、空间和实际 SQLite journal mode。
- 已知网络文件系统和生产模式下无法分类的文件系统拒绝启动；WAL 失败仅在目录持久化能力已验证时回退到 rollback journal。
- ArtifactStore 已接入 Workspace 路径边界并在写入、重命名后复核 canonical containment。
- 本地访问令牌支持 Keyring 与只读 Secret 存储，API 已实现严格 Host、Origin、Bearer 与资源级 `authorize_run` 双重边界。
- 数据库支持 Alembic 启动迁移、完整性检查、Online Backup、版本化清单以及持锁恢复后的迁移、Artifact 对账和失败回滚。
- 启动顺序已经固定为 Workspace 探测/锁、迁移/完整性、Artifact 对账、状态恢复、Supervisor 与 readiness；失败和关机按反向所有权清理。
- 恢复扫描不会自动重放 `recovery_required` 或远端未知结果；只有持久化明确证据后才能将 Run 标记为 retry-safe。
- TaskSupervisor 已实现 Run 去重、全局/项目/Provider 并发边界、取消专用执行路径和有界关闭；事件保留任务也有独立关闭上限。
- DeepSeek 文本能力通过统一结构化 Provider 接口接入；提交意图先持久化，成功、已知未计费失败、已计费失败和远端结果未知分别结算，`outcome_unknown` 不自动重发。
- Brief → DeckOutline → DesignSystem → PageSpec → 人工复核已形成可恢复闭环；意图不明确时创建版本化表单 Interrupt，Resume 先校验并原子持久化后再调度。
- 规划中间结果使用版本化 Artifact 和依赖边保存；进程在 Artifact 提交后、Step 提交前崩溃时可从 operation_id 对账恢复而不重复调用 Provider。
- 规划创建、查询和 Resume API 已接入 Host、Origin、Bearer、项目/Run 所有权与 `InputLimits`；浏览器连接仅投影状态，不拥有后台任务生命周期。
- ApplicationRuntime 可在 migrations、Artifact 对账和恢复扫描后组合 Planning Workflow，再启动 Supervisor 并开放 readiness。
- 结构化 Provider 成功结果在 Attempt 被选定前与 Usage 原子落入 `provider_text_results`；即使进程在返回后、Artifact 创建前崩溃，也可重放已验证结果而不产生第二次调用。
- 复核批准/取消及修订代际创建已收敛为事务边界；Bundle 和 PageSpec Patch 具备确定性恢复键，定向页修订只替换选中页并保留未选中页引用。
- HTTP 429/5xx 和未映射错误不再仅凭状态码释放预算；保守最大 Token 成本必须由预留覆盖，计费不确定结果进入人工恢复路径。

## 6. 下一步

持久化、状态机、DeepSeek 文本适配和确定性规划闭环已经完成。下一开发批次应进入 **输入解析与渲染/图片能力**：

- 接入 Markdown、PDF 和图片解析器，实际执行字节数、页数、像素、压缩比、外部资源和超时限制，并将解析内容始终视为不可信数据。
- 实现 Seedream 图片 Provider 能力与 Qwen 视觉理解适配，沿用现有 Operation/Attempt、预算预留和未知结果恢复协议。
- 实现确定性 HTML/CSS/SVG 文字排版、整页合成、单页重生成，以及 PNG/JPG、ZIP、PDF 和图片型 PPTX 导出链路。
- 在渲染链路稳定后再接入 React 规划复核界面和局部编辑 Sub-workflow。

下一批仍不应提前扩展 React 前端或元素级可编辑 PPTX；先保持 Provider 与 Workflow 通过已完成的持久化协议闭环运行。

## 7. 已知待办与审查结论

- Artifact 维护操作需要由 Workspace 维护锁保护；跨 Store 并发文件移动仍应继续强化测试。
- `artifact_references.project_id` 与 Artifact 项目一致性目前由服务层保证，数据库尚无复合外键。
- Run 所有权规则仍由应用层注入的 `authorize_run` 实现；本地单用户令牌只提供应用级认证，不能替代资源级校验。
- Workspace 路径审查裁决：并发换链 TOCTOU 需要已取得同一 OS 用户权限的恶意进程，超出一期威胁模型；静态链接在 I/O 前拒绝，写入/重命名后仍复核。
- 当前 Windows 测试环境不能创建符号链接；在具备开发者模式或 CI 权限的 Windows 环境中仍需运行 link/junction 用例。
- Seedream、视觉理解、Renderer、输入解析/OCR、导出和前端尚未进入实现阶段；当前 live Provider 测试只有在显式设置开关及全部 DeepSeek 配置时才会产生真实请求。
- 当前默认导出的 `app` 仍只提供未配置的应用骨架；实际规划路由通过 `create_app` 显式注入 Runtime、Workflow、Supervisor、认证和资源授权。面向最终用户的环境变量/配置文件启动器应与下一阶段应用配置一起实现。
- Provider 的 `retryable` 目前只用于能力映射，不自动定时重试；这是为了避免在尚无持久化计时调度器时产生隐式重复费用。已知失败由用户显式重试，计费不确定失败必须先完成恢复或风险确认。
- 意图是否需要澄清仍由受 Schema 约束的 Brief 决策模型判断；不可信输入边界已改为 JSON 字符串编码并转义闭合标记，但尚未增加领域专用的确定性“信息充分度”规则。

这些待办不能因上下文清理而丢失，也不应在无关模块中顺手实现。

## 8. 会话恢复检查清单

新会话开始时：

1. 读取本文件、README 和即将实现模块对应的规格。
2. 执行 `git status --short`，不得覆盖未提交的用户改动。
3. 执行 `git log --oneline -12`，确认进度表与实际 HEAD。
4. 运行相关模块测试，确认基线可复现。
5. 按“基础设施里程碑合并开发、统一审查与全量验证”的节奏继续，不恢复细粒度逐模块停顿。
