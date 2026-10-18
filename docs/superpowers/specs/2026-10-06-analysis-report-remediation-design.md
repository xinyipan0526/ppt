# 项目全维度分析报告整改设计

## 1. 目标与范围

本设计把 `docs/analysis/项目全维度分析报告.md` 中经现状审计确认的问题，收敛为三个可独立验收的发布修复批次。目标是在不改变一期产品边界、不降低远端调用资金安全的前提下，补齐用户可感知反馈、运维入口、渲染正确性和领域一致性。

用户已明确暂缓以下内容：

- Provider 并发限制和页面 TaskGroup 并发。
- 跨 planning、visual、vision、ocr 的项目总预算硬顶及相关预算语义迁移。
- 运行时联网价格校验。

以下内容也不进入整改：元素级可编辑 PPTX、云端多租户、二期画布能力。Ark 未经权威证据确认的幂等键、429/5xx 自动释放预算或自动重发不得实现；`reasoning_tokens` 不得在完整 `output_tokens` 之外重复计费。

## 2. 批次 A：产品反馈与本地运维

### 2.1 长任务控制与可信进度

- `queued`、`running`、`waiting_for_retry`、`waiting_for_user` 的 Run 在前端提供取消入口。
- 取消先调用现有持久化取消 API；按钮在提交期间禁用，`cancel_requested` 明确展示，重复取消按幂等结果处理。
- 进度只展示后端事实：当前步骤、完成页数、总页数和失败页数。不展示没有历史统计支撑的 ETA。
- 同一 Run 的 SSE 和轮询只触发 snapshot 刷新，不在浏览器复制状态机。

### 2.2 统一错误反馈与可访问对话框

- 提供共享 `ErrorBanner`，所有用户触发的 mutation 至少展示安全的 `ApiError.message`；未知异常使用固定中文回退文案。
- Project 创建、素材上传/重试/删除、规划、生产、编辑、恢复和导出都不得静默失败。
- 提供共享 Dialog 基元，统一实现 `aria-labelledby`、初始焦点、Tab/Shift+Tab 焦点圈定、Escape 关闭和关闭后的焦点恢复。
- 对不可关闭或提交中的 Dialog，Escape 行为必须与按钮禁用状态一致。

### 2.3 首次配置引导

- 项目工作台读取现有模型运行时配置状态。
- cloud 模式未配置 Ark 时，在工作台显示阻断式设置卡片和明确的设置页入口；不自动跳转，不隐藏已有项目资料。
- deterministic 模式保持可直接运行，不要求 Ark 配置。

### 2.4 可恢复异步导出

- 导出创建 API 只创建持久化 Export 任务并快速返回，不在请求线程完成全部编码。
- 导出任务由现有单进程 Supervisor 所有，状态至少为 `queued`、`running`、`available`、`failed`、`cancelled`。
- 浏览器通过持久化查询和有界轮询/SSE 投影显示进度；刷新页面后可继续看到任务状态并在 available 后下载。
- 同一导出请求使用确定性指纹去重；进程重启只恢复有安全证据可重放的本地导出，不重复触发 Provider 调用。
- JPG 质量和 PDF 分辨率改为有边界的服务参数；默认仍为 JPG 92、PDF 144 DPI，避免改变现有输出。

### 2.5 日志、异常与备份

- 两个维护循环捕获异常后写脱敏结构化日志，包含 task、error type、attempt 和下一次执行时间；重复错误做节流但不得永久静默。
- FastAPI 全局异常处理器生成 correlation id，JSONL 记录完整本地异常上下文，客户端仅返回稳定错误码和 correlation id。
- CLI 未知异常默认输出简洁错误并落日志；只有显式 debug 模式输出 traceback。
- 增加 `ppt-agent backup`、`ppt-agent restore`，复用 Workspace 锁、manifest 校验和现有 Online Backup/restore；restore 默认先 dry-run 校验，真正替换需要显式确认参数。
- 默认每日自动备份，保留最近 7 份；周期和保留数可配置。自动任务使用共享维护锁，失败必须落日志，不阻塞主服务 readiness。
- Docker 镜像默认 `PPT_AGENT_PRODUCTION=true`，增加针对 `/api/v1/ready` 的 HEALTHCHECK；Compose 保持显式生产配置。

## 3. 批次 B：渲染、页面隔离与运行上界

### 3.1 确定性 `shrink_then_reflow`

- Renderer 先使用自然断行，再在 `[min_font_size, font_size]` 内确定性搜索可容纳字号，然后在模板允许范围调整间距。
- DOM 布局报告返回每个文字元素的实际字号、最终行高和 overflow 状态；这些值进入质量记录和缓存身份所需的 Renderer 版本。
- 低于 `min_font_size` 仍无法容纳时返回 `layout_overflow`，不得使用省略号隐藏标题、关键数字或正文。
- 换模板、拆页或人工删减仍由 Workflow 处理，Renderer 不擅自改变交付文字。

### 3.2 六类布局 Token

- 建立受约束的布局模板注册表，覆盖 `cover`、`title_body`、`two_column`、`hero_right`、`data_focus`、`closing`。
- Layout Planner 只能选择注册表中的 Token；选择依据为 page type、内容密度和 visual placement，不接受模型生成任意坐标或 CSS。
- 1920×1080、字体边界和安全区抽为共享常量，但保持正式规格固定画布，不把它们开放成任意配置。

### 3.3 OCR 与页面故障隔离

- cloud OCR 从整段子串判断改为 block/span 级规范化匹配与有界相似度；关键数字、标题和用户要求完整展示的文本保持严格校验。
- deterministic OCR 明确标记为非视觉验证；真实渲染错误由 Chromium DOM/图像 E2E 覆盖。
- 在保持页面串行执行的本批次中，预期页面错误必须被 `run_page_safely` 捕获并持久化为 Page failed，后续页面继续执行；系统级异常仍终止 Run。
- 页面 TaskGroup 并发暂缓，不影响先完成故障隔离。

### 3.4 Run 上界

- 单个 Provider 调用继续使用已有 timeout。
- 规划 Run 默认最长活跃 30 分钟，生产 Run 默认最长活跃 2 小时；超时进入可诊断的失败或 recovery 状态，不直接删除持久化进度。
- 用户主动 revision 不设硬次数，只展示代次和累计费用/风险软提示。
- 超时从持久化运行时间计算，进程重启不得重置整个 Run 的时间预算。

## 4. 批次 C：领域一致性与维护性

### 4.1 强类型状态与转换

- 为 DeckGeneration、PageEdit、Export 等聚合定义 `StrEnum` 和显式转换函数；API 和数据库中的字符串值保持兼容。
- 非恢复专用途径不得直接赋 Run/Step/Page 终态；需要原子复核的路径增加正式事件或专用事务方法。
- `production_service` 等调用方比较枚举成员，不比较 `.value` 字面量。
- 取消终态 Run 返回幂等结果，不因重复点击抛非法转换。

### 4.2 事件兼容

- 将领域 Step 结果类型与持久化 event id 分离；新增事件统一使用 snake_case。
- 已有数据库中的历史 event type 不原地改名，读取层继续兼容旧值。
- ephemeral 事件裁剪使用明确 replay floor 或连续前缀策略，不能重编号 `run_seq`；内部空洞不得被误判为数据损坏。
- SQLite 一致读所需的显式 `BEGIN` 保留并由并发测试约束。

### 4.3 Provider 与通用助手

- 抽取无状态 HTTP 错误分类、Usage 字段解析和安全摘要助手，不引入要求所有 Provider 继承的重型基类。
- 每个 Adapter 继续拥有“可证明未计费”的独立白名单和 Provider 特有响应语义。
- Ark/Seedream 只有在官方协议或契约测试证明支持时才发送幂等键；没有证据时保持本地两阶段提交与人工恢复。
- 增加契约测试证明 `reasoning_tokens` 是 output 明细而不是额外重复计费项。

### 4.4 工具与代码清理

- 渐进引入 ruff 和 mypy，只对本批次触达模块启用严格检查；不得以一次性全仓格式化制造巨大无关 diff。
- 抽画布常量、移除 `computed-by-text-runner` 等 placeholder，并用明确 Session Protocol/类型替换恢复代码中的宽泛 `object` 和不必要 `type: ignore`。
- 文本编辑继续保持稳定 block id/role 粒度；元素级 PPTX 编辑明确不在一期范围。

## 5. 数据、安全与兼容性

- 新增数据库字段必须通过 Alembic 迁移；迁移可重复运行，并覆盖旧 Workspace 升级测试。
- 浏览器响应、SSE、日志和异常 detail 继续执行现有敏感字段白名单与递归脱敏策略。
- API Key 不进入 localStorage、sessionStorage、URL、Query Cache、DOM、数据库事件或日志。
- 所有外部副作用仍受 Workspace 所有权、Host/Origin、Session/CSRF/Bearer 和资源级授权保护。
- 现有图片型 PPTX、确定性无 Key 模式、同源静态交付和 Windows 本地运行必须保持兼容。

## 6. 验收策略

每个子批次按 TDD 实施，并由独立 reviewer 检查规格符合性和代码质量。至少覆盖：

- 前端 mutation 失败、snapshot/SSE 断线、重复取消、刷新恢复、键盘焦点和读屏告警。
- 异步导出创建、去重、进程恢复、失败、下载和旧 Workspace 迁移。
- 日志脱敏、维护循环异常节流、备份锁竞争、manifest 损坏、restore dry-run 和失败回滚。
- 中英文长文本缩字边界、六种布局、关键数字 OCR、低于最小字号的 overflow 和后页故障隔离。
- Run 超时跨重启、状态转换非法路径、旧事件兼容和 replay floor。
- 相关测试、Python 全量测试、React 全量测试、Chromium E2E、`compileall`、前端生产构建和 `git diff --check`。

## 7. 实施顺序与停止条件

实施顺序固定为：批次 A → 批次 B → 批次 C。每个批次必须独立通过测试和审查，Critical/Important 问题清零后才进入下一批。

若实现过程中发现必须依赖暂缓的并发、项目总预算或运行时价格校验，停止该具体子项并记录依赖，不通过隐式扩大范围绕过用户决策。429/5xx 资金安全、未知远端结果和 API Key 存储边界不可作为折中项。
