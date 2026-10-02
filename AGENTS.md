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

- Python 3.12、FastAPI、SQLAlchemy 2、Alembic、SQLite；前端使用 React 19、TypeScript、Vite、TanStack Query。
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
6. 后端里程碑已包含 Renderer、OCR 与 Deck Workflow；下一阶段只实现 React 前端，不扩展元素级可编辑 PPTX、云端多租户或二期画布能力。

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
| `cbdde09` | 独立审查修复：付费结果落盘、原子复核、定向页修订与安全计费边界 |
| `4b72261` | Source Material 领域契约、迁移、状态持久化与原子项目配额 |
| `569d2d0` | Markdown、PDF、图片解析器与有界子进程隔离 |
| `7e7f171` | Artifact-first 素材服务、调度器、恢复、HTTP API 与 Runtime 组合 |
| `7374213` | 素材解析结果可信边界编译、规划 Run 事务内复核与来源接线 |
| `bdba6e8` | 独立审查修复：恢复引用、上传清理、解析资源上限、子进程取消、规划竞态与来源依赖 |
| `28834a3` | 生产合同与持久化 Schema |
| `5871ec7` | 图片/视觉 Provider 协议与确定性实现 |
| `70ad1d6` | Seedream、Qwen 与持久化 Operation Runner |
| `83ab8b7` | 确定性布局、Chromium 渲染和质量验证 |
| `4eb4938` | 可恢复的生产 Page Pipeline |
| `fe8f965` | 完整后端服务、编辑/导出/API/CLI/配置/文档与端到端验证 |
| `107ac5c` | 完整后端交付检查点与使用文档 |
| `34ca108` | React 产品工作台设计与实施计划 |
| `9b25d73` | HttpOnly 浏览器会话、CSRF 与安全 Provider 配置 |
| `bfdb0f6` | 不可变 Deck/Page generation 工作台投影与恢复 API |

截至当前产品里程碑：

- Python 全量测试为 `388 passed, 6 skipped`；三项跳过来自当前 Windows 账户无法创建符号链接/目录链接，三项为默认关闭的 DeepSeek、Seedream、Qwen 真实计费测试。React 为 `9 passed`，Chromium 产品 E2E 为 `2 passed`。
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
- Source Material 以 `uploaded → parsing → ready/failed → deleted` 状态持久化；原文件和解析结果均为版本化 Artifact，启动时可对中断解析与失效引用进行对账。
- Markdown、PDF、PNG/JPEG/WebP 使用内容优先识别和严格限制解析；Pillow 与 pypdf 运行在 spawn 子进程中，父进程执行超时、IPC 大小和结果 Schema 复核。Windows 当前只具备进程隔离与超时终止，无法提供 POSIX `rlimit` 的同等级资源约束。
- 素材上传、查询、重试和删除 API 已接入认证、项目授权、流式字节上限与全局/项目解析并发边界；项目累计上传配额在 SQLite `BEGIN IMMEDIATE` 事务内复核。
- 规划创建 API 只接受用户文本和 `material_ids`，不接受客户端伪造的 Artifact 引用；解析块以规范 JSON 的 `untrusted_source_content` 边界编译，并在创建 Run 的同一写事务中复核 parsed Artifact 的项目、类型和可用状态。
- 独立审查后，素材恢复会原子替换过期 parsed 引用；上传执行项目剩余额度预检和竞态失败清理；二进制签名可跨任意 HTTP 分片识别；图片增加宽高、解码字节与逐帧解码上限；关机取消会终止并 join 解析子进程。
- 规划 Context 设有聚合 UTF-8 字节上限，并在 Run 创建事务中同时复核 Material `ready` 状态与 parsed Artifact 绑定；Brief Artifact 通过依赖边追溯并保护输入素材。
- Seedream 图片生成/参考图编辑、Qwen 视觉理解/OCR 已通过统一能力层接入；成功响应先持久化，再下载/验证 Artifact，URL 下载执行 HTTPS、精确 Host、DNS/IP、重定向、媒体、像素和字节限制。
- HTML/CSS/SVG 模板与 Playwright Chromium 完成确定性文字排版、页面合成和 DOM/图像质量检查；字体清单及内容哈希参与缓存身份。
- 生产、单页重生成、自然语言内容/样式/视觉编辑均保存不可变代际与依赖；内容/样式编辑不调用图片 Provider，视觉编辑使用 Qwen 定位、Seedream 编辑后重新合成确定性文字。
- PNG/JPG、ZIP、PDF 和图片型 PPTX 导出已实现，导出前复核页代际/质量/未知远端结果，下载支持鉴权与单段 HTTP Range。
- 原生 `ppt-agent serve|doctor|migrate`、确定性无 Key 模式、cloud 模式、TOML/环境变量/Secret 文件配置、Docker/Compose 和完整 HTTP API 已组合到同一 Runtime。
- 仅“明确未计费且 retryable”的 Provider 失败会保存 due time 并由单进程调度器恢复；未知结果继续持有预算并要求人工授权。
- 确定性 API 端到端测试覆盖项目、规划复核、8 页生产、单页编辑、PPTX 导出、下载与 Range；真实 Provider 测试必须显式打开独立开关。
- React 工作台已同源打包进 Python wheel，覆盖项目创建、素材上传、类型化澄清、逐页规划复核、任务恢复、完整 PPT 画廊、单页重生成、局部编辑、历史恢复、全局重生成和 PPTX/PDF/PNG ZIP/JPG ZIP 导出。
- 浏览器认证使用 HttpOnly/SameSite=Strict 会话 Cookie 与内存 CSRF Token；API Key 成功提交后清空，禁止进入 localStorage、sessionStorage、URL、Query Cache、DOM、日志和响应。Bearer Token 接口继续保留给自动化客户端。
- Provider 配置由本地 UI 写入 Workspace Secret 文件和非密钥 JSON；运行时空闲时热切换，忙碌时标记 pending。CLI 不再输出访问令牌，`ppt-agent serve` 默认打开同源 UI。
- SSE 命名事件驱动工作台刷新，断线后由持久化 snapshot 与有界轮询兜底；浏览器状态不复制后端 Workflow 状态机。
- 正式构建使用 Node 阶段生成静态资源，运行时不需要 Node.js；wheel 已验证包含 `static/index.html` 与带内容哈希的 CSS/JS。

## 6. 下一步

一期完整产品已经交付。下一开发批次应进入 **发布与真实环境验收**：

- 在用户明确授权产生费用后，按账号区域分别运行 DeepSeek、Seedream、Qwen live smoke tests，并记录实际模型 ID、价格单位与结果 Host。
- 在 Windows 开发者模式和 Linux CI 各跑一次全量套件，补齐当前被权限跳过的链接边界测试。
- 制作版本化安装包/镜像并执行全新 Workspace 安装、升级、备份恢复和浏览器兼容性验收。
- 继续保持图片型 PPTX 边界；元素级编辑、画布框选/涂抹、云端多租户仍属于二期，不应混入发布修复。

## 7. 已知待办与审查结论

- Artifact 对账与 GC 已由共享 Workspace 维护锁串行化，并有并发回归测试；未来新增维护任务必须使用同一锁。
- `artifact_references.project_id` 与 Artifact 项目一致性目前由服务层保证，数据库尚无复合外键。
- Run 所有权规则仍由应用层注入的 `authorize_run` 实现；本地单用户令牌只提供应用级认证，不能替代资源级校验。
- Workspace 路径审查裁决：并发换链 TOCTOU 需要已取得同一 OS 用户权限的恶意进程，超出一期威胁模型；静态链接在 I/O 前拒绝，写入/重命名后仍复核。
- 当前 Windows 测试环境不能创建符号链接；在具备开发者模式或 CI 权限的 Windows 环境中仍需运行 link/junction 用例。
- React 前端已实现并通过真实 Chromium 确定性闭环；尚未在 Safari/Firefox 与移动端做正式兼容性认证，当前产品定位为桌面浏览器工作台。
- `src/ppt_agent/api/app.py` 中的模块级 `app` 仍是无配置骨架，正式运行必须使用 `ppt-agent serve`，它会加载配置并调用完整 composition root。
- 真实云 smoke test 默认关闭且会产生费用；只有显式设置对应 `LIVE_*` 开关与完整凭据时才运行。正式使用前还应按账号所在区域核对模型 ID、价格和 Seedream 结果 Host allowlist。
- Windows 子进程解析无法提供 POSIX `rlimit` 同等级资源约束；生产环境优先使用容器/Linux。当前 Windows 账户无符号链接权限，因此 link/junction 用例仍需在 CI 或开发者模式下运行。
- FastAPI/Starlette 测试依赖目前产生 `httpx`/AnyIO 弃用警告，不影响运行，但升级依赖时应迁移到其建议的新 TestClient/BlockingPortal 接口。

这些待办不能因上下文清理而丢失，也不应在无关模块中顺手实现。

## 8. 会话恢复检查清单

新会话开始时：

1. 读取本文件、README 和即将实现模块对应的规格。
2. 执行 `git status --short`，不得覆盖未提交的用户改动。
3. 执行 `git log --oneline -12`，确认进度表与实际 HEAD。
4. 运行相关模块测试，确认基线可复现。
5. 按“基础设施里程碑合并开发、统一审查与全量验证”的节奏继续，不恢复细粒度逐模块停顿。
