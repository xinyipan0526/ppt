# 配置说明

配置优先级为：命令行参数 > 环境变量 > TOML > 默认值。浏览器工作台保存的
Provider 非密钥设置位于 `workspace/config/provider-settings.json`；浏览器提交的密钥
单独保存在 `workspace/.secrets/providers/`，不会进入 JSON、SQLite、日志、事件或响应。

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

自定义端口会同步更新 Host、Origin 与同源浏览器安全边界。只支持 localhost 产品
访问；若使用反向代理或远程部署，必须另行设计 TLS、身份认证和代理信任边界。

## Provider 配置渠道

推荐直接在本地 UI 的“模型设置”中填写。也可使用环境变量或 Secret 文件：

```text
DEEPSEEK_API_KEY / DEEPSEEK_API_KEY_FILE
SEEDREAM_API_KEY / SEEDREAM_API_KEY_FILE
QWEN_API_KEY / QWEN_API_KEY_FILE
```

进程配置还支持 `PPT_AGENT_*_BASE_URL`、模型 ID、Seedream 结果 Host 白名单和价格。
完整变量名可参考 `.env.example` 与 `config/ppt-agent.example.toml`。TOML 不接受
明文 API Key。

## 安全模型

- 浏览器通过 HttpOnly、SameSite=Strict Cookie 建立本地会话，脚本无法读取令牌。
- POST/PUT/PATCH/DELETE 必须携带内存中的 CSRF Token。
- 自动化仍可使用 Bearer Token；Bearer 请求不需要 CSRF。
- API Key 表单提交成功后立即清空，不写入 localStorage、sessionStorage、URL 或查询缓存。
- Host 与 Origin 必须匹配当前本地端口；未知来源请求会在认证前拒绝。
- 静态资源与 API 同源，生产不需要 CORS。

## Workspace 与备份

Workspace 必须位于支持排他锁、原子替换、`fsync` 和可靠 SQLite 锁的本地文件系统。
`production=true` 时，无法分类或不安全的网络文件系统会被拒绝。

停止服务后备份整个 Workspace。启动时系统会自动迁移数据库、检查完整性、对账
Artifact，并只恢复有明确安全证据的任务。`outcome_unknown` 永不自动重发。

## 价格单位

所有价格使用币种最小单位，例如 CNY 的“分”。文本/视觉价格按百万 Token，
Seedream 按图片。云模式要求正数价格与非空价格版本，避免真实调用被错误记为免费。
