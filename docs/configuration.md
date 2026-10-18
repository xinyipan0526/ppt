# 配置说明

进程启动配置的优先级为：命令行参数 > 环境变量 > TOML > 默认值。浏览器工作台保存
的模型非密钥配置位于 `workspace/config/provider-settings.json`；API Key 单独保存在
`workspace/.secrets/adapters/volcengine_ark.key`，不会进入 JSON、SQLite、日志、事件
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
| 自动备份 | `PPT_AGENT_BACKUP_ENABLED` | `true` |
| 备份周期（秒） | `PPT_AGENT_BACKUP_INTERVAL_SECONDS` | `86400` |
| 保留份数 | `PPT_AGENT_BACKUP_RETENTION` | `7` |
| 备份关闭上限（秒） | `PPT_AGENT_BACKUP_SHUTDOWN_TIMEOUT_SECONDS` | `5` |

可复制 `config/ppt-agent.example.toml` 后运行：

```powershell
& '.\.venv\Scripts\ppt-agent.exe' --config ppt-agent.toml serve
```

停止服务时在运行终端按 `Ctrl+C`，然后重新执行启动命令。使用自定义配置重启时，仍应在
`serve` 前保留 `--config ppt-agent.toml`。源码开发与自动重载见[开发指南](development.md)。

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

## 配置生效

在 Web 中保存模型设置时，如果运行时空闲会立即热切换；存在运行任务时保存为 pending，
任务结束后激活。端口、Workspace、备份周期等进程级配置需要重启服务。

这里的“热切换”只针对模型运行时配置。修改后端或前端代码后的更新方法见
[开发指南](development.md)。

## Workspace 与备份

Workspace 必须位于支持排他锁、原子替换、`fsync` 和可靠 SQLite 锁的本地文件系统。
`production=true` 时，无法分类或不安全的网络文件系统会被拒绝。

自动备份在应用完成迁移、完整性检查、Artifact 对账、任务恢复并进入 ready 后启动。
默认每 86400 秒使用 SQLite Online Backup 生成数据库与 manifest，保留最近 7 份。它与
Artifact 对账和 GC 复用同一个 Workspace maintenance lock；失败按 1、2、4、8… 次节流
记录，下一周期继续尝试，不阻塞 readiness。TOML 配置示例：

```toml
[backup]
enabled = true
interval_seconds = 86400
retention = 7
shutdown_timeout_seconds = 5
```

周期和保留数必须为正数，关闭上限不得为负数。更改这些进程级设置只需重启服务，不需要
重新安装依赖。环境变量优先级仍高于 TOML。

`ppt-agent backup` 默认写入 `<workspace>/backups`；可用 `--output-directory` 指定其他
位置。手动备份先取得 Workspace 独占锁，运行中的服务会使其拒绝；在线运行期间由 ready
后的自动任务使用 ArtifactStore maintenance lock。`ppt-agent restore BACKUP` 与显式
`--dry-run` 都只做只读验证，实际替换必须使用 `--confirm`，且运行中的服务不能持有
Workspace 锁。恢复继续执行 manifest/checksum、
SQLite 完整性/外键、迁移和 Artifact 对账；替换后检查失败会回滚原数据库。

数据库备份的 manifest 记录 Artifact 清单，但不重复复制内容寻址大文件。灾难恢复还应
保护完整 Workspace 或 Docker Volume。启动时系统会自动迁移数据库、检查完整性、对账
Artifact，并只恢复有明确安全证据的任务。`outcome_unknown` 永不自动重发。

容器默认设置 `PPT_AGENT_PRODUCTION=true`。Dockerfile 与 Compose 的 healthcheck 请求
`http://127.0.0.1:8000/api/v1/ready`；该只读探针不需要也不会携带浏览器 Cookie、Bearer
Token 或 CSRF Token，且不会返回任何 Secret。
## 日志与运行轨迹

结构化日志位于 `<workspace>/logs/ppt-agent.jsonl`，每行一条 JSON。单个文件达到 10 MiB
后轮转，保留 `ppt-agent.jsonl.1` 到 `.3`。日志包含 Run、Step、Operation、Provider、状态、
安全错误码和 trace ID 等排障信息。Provider 返回的安全错误摘要会被记录，但 API Key、
Authorization、原始 Prompt、素材正文和本地 Secret 路径会替换为 `[REDACTED]`。

不要在日志字段中主动加入完整请求体或响应体。JSONL 属于本地运维数据，只有 Workspace
本身受到保护时才应随备份保留。

如需查看单个 Run 的安全轨迹，可调用 `GET /api/v1/runs/{run_id}/trace`。它返回 Run 快照、
有序事件、Workflow Step 和 Provider Operation/Attempt 摘要，不返回原始 Prompt、素材、
Provider 完整请求/响应或 API Key。默认最多返回 100 条事件和 50 个 Operation；更大的请求
会返回 HTTP 422。接口使用与其他 Run API 相同的本地认证和项目所有权检查。
