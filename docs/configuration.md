# 配置参考

优先级为：命令行参数 > 环境变量 > TOML > 默认值。API Key 只从环境变量或 Secret 文件读取，不接受 TOML 明文配置。`ppt-agent doctor` 输出经过脱敏的最终配置并检查 Chromium。

Provider 密钥既可直接使用 `DEEPSEEK_API_KEY`、`SEEDREAM_API_KEY`、`QWEN_API_KEY`，也可分别使用对应的 `*_API_KEY_FILE` 指向 Docker/Kubernetes Secret 文件。直接环境变量优先于文件；密钥不会进入有效配置输出或日志。

## 核心设置

| 配置 | 环境变量 | 默认值 |
|---|---|---|
| Workspace | `PPT_AGENT_WORKSPACE` | `./workspace` |
| 监听地址 | `PPT_AGENT_HOST` / `PPT_AGENT_PORT` | `127.0.0.1:8000` |
| Provider 模式 | `PPT_AGENT_PROVIDER_MODE` | `deterministic` |
| 固定访问令牌 | `PPT_AGENT_ACCESS_TOKEN` | 自动生成并持久化 |
| 全局/项目并发 | `PPT_AGENT_GLOBAL_CONCURRENCY` / `PPT_AGENT_PROJECT_CONCURRENCY` | `4` / `2` |

`production=true` 时 Workspace 必须位于通过能力探测的本地文件系统。SQLite、Artifact 和 Secret 必须放在同一持久化 Workspace；不要放在不支持可靠锁、原子替换或 `fsync` 的网络盘。

## 成本单位

所有价格是所选货币的最小单位，例如 CNY 的“分”。文本/视觉价格按每百万 token 填写，Seedream 按每张图片填写。cloud 模式拒绝零价格或缺少价格版本的配置，避免真实调用被错误记为免费。改变账号套餐或厂商价格后应更新 `PPT_AGENT_PRICING_VERSION`，历史 Usage 会保留旧版本。

服务在远端调用前原子预留保守上限，成功后按 Provider 返回 Usage 结算；结果未知的预留不会自动释放。项目创建请求中的 `budget_minor_units` 是每个阶段账户的硬上限。

## 备份与恢复

停止写入后备份整个 Workspace。应用启动时会迁移数据库、执行完整性检查、对账 Artifact，并只恢复明确可安全重放的工作；`outcome_unknown` 必须通过操作 API 查看并由用户显式授权重试。
