# 配置说明

进程启动配置的优先级为：命令行参数 > 环境变量 > TOML > 默认值。浏览器工作台保存
的模型非密钥配置位于 `workspace/config/provider-settings.json`；API Key 单独保存在
`workspace/.secrets/providers/volcengine_ark.key`，不会进入 JSON、SQLite、日志、事件
或 API 响应。

## 基础配置

| 配置 | 环境变量 | 默认值 |
|---|---|---|
| Workspace | `PPT_AGENT_WORKSPACE` | `./workspace` |
| 监听地址 | `PPT_AGENT_HOST` | `127.0.0.1` |
| 端口 | `PPT_AGENT_PORT` | `8000` |
| 严格生产探测 | `PPT_AGENT_PRODUCTION` | `false` |
| 全局并发 | `PPT_AGENT_GLOBAL_CONCURRENCY` | `4` |
| 单项目并发 | `PPT_AGENT_PROJECT_CONCURRENCY` | `2` |
| Chromium | `PPT_AGENT_CHROMIUM_EXECUTABLE` | Playwright 管理 |

可复制 `config/ppt-agent.example.toml` 后运行：

```powershell
& '.\.venv\Scripts\ppt-agent.exe' --config ppt-agent.toml serve
```

自定义端口会同步更新 Host、Origin 与同源浏览器安全边界。产品只支持 localhost 访问；
反向代理或远程部署需要另行设计 TLS、身份认证和代理信任边界。

## 模型运行时

当前云运行时只有一个 Adapter：`volcengine_ark`。必填项只有 API Key 和两个模型 ID：

```text
ARK_API_KEY / ARK_API_KEY_FILE
PPT_AGENT_MULTIMODAL_MODEL_ID=doubao-seed-2-1-pro-260915
PPT_AGENT_IMAGE_MODEL_ID=doubao-seedream-5-0-pro-260628
```

也可以在 TOML 的 `[providers]` 中设置 `mode`、`adapter_id`、
`multimodal_model_id` 和 `image_model_id`；TOML 不接受明文 API Key。推荐直接使用本地
UI，它会从模型目录生成下拉列表，避免手工输入错误 ID。

目录内置方舟 Base URL、结果下载 Host、能力和版本化默认价格。用户不需要填写这些
基础设施字段。可选价格覆盖以人民币最小单位（分）记录：多模态模型按百万 Token，
图片模型按输出像素档位及额外参考图计价。覆盖值只应在账号合同价不同于目录价时使用。

旧的 DeepSeek/Seedream/Qwen 配置字段只为识别和回滚旧配置而保留，不会使当前云运行时
通过资格检查。旧版 `provider-settings.json` 会被标记为 legacy，用户需在新界面重新保存。

## 安全模型

- 浏览器通过 HttpOnly、SameSite=Strict Cookie 建立本地会话，脚本无法读取令牌。
- POST/PUT/PATCH/DELETE 必须携带内存中的 CSRF Token。
- 自动化仍可使用 Bearer Token；Bearer 请求不需要 CSRF。
- API Key 表单提交成功后立即清空，不写入 localStorage、sessionStorage、URL 或查询缓存。
- Host 与 Origin 必须匹配当前本地端口；未知来源请求会在认证前拒绝。
- 静态资源与 API 同源，生产不需要 CORS。

## 配置生效与代码更新

在 Web 中保存模型设置时，如果运行时空闲会立即热切换；存在运行任务时保存为 pending，
任务结束后激活。这里的“热切换”只针对模型运行时配置，不代表已安装程序会自动读取
仓库中的新代码。

- `npm run dev`：Vite 管理的前端源码支持热更新。
- 从源码直接运行后端：Python 代码通常需要重启进程才能生效。
- `pip install .` 或 wheel 安装：代码和 Web 静态资源已复制进 `.venv`，修改仓库不会实时
  影响正在运行的产品。必须重新构建/安装并重启 `ppt-agent serve`。
- Docker：必须重新构建镜像并重建容器。

## Workspace 与备份

Workspace 必须位于支持排他锁、原子替换、`fsync` 和可靠 SQLite 锁的本地文件系统。
`production=true` 时，无法分类或不安全的网络文件系统会被拒绝。

停止服务后备份整个 Workspace。启动时系统会自动迁移数据库、检查完整性、对账
Artifact，并只恢复有明确安全证据的任务。`outcome_unknown` 永不自动重发。
## Audit logs and Run traces
The composition root writes structured, one-record-per-line JSON logs to
`<workspace>/logs/ppt-agent.jsonl`. The file is rotated at 10 MiB and keeps
three rotated files (`ppt-agent.jsonl.1` through `.3`). Rotation is bounded;
it does not change the SQLite workflow source of truth. The logger records
bounded lifecycle and provider context such as Run, Step, Operation, provider,
status, error code, and trace ID when available.

Before a record is written, redaction walks nested dictionaries and sequences.
API keys, bearer/authorization values, raw prompts, source contents, and local
secret paths are replaced with `[REDACTED]`; exception text is also filtered.
Do not add request or response bodies to log `extra` fields. Treat the JSONL
files as local operational data and include them in backups only when the
workspace itself is protected.

For a project-authorized, read-only view of one run, use
`GET /api/v1/runs/{run_id}/trace`. It returns the safe Run snapshot, ordered
events, workflow steps, and provider operation/attempt summaries. Defaults are
100 events and 50 operations; `event_limit` and `operation_limit` may request
smaller values but values above the configured caps (100 and 50 by default)
are rejected with HTTP 422. The endpoint requires the same local authentication
and Run ownership checks as the other Run APIs, and never returns raw prompts,
source material, provider request/response bodies, or API keys.
