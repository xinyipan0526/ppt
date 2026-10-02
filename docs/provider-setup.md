# 云 Provider 设置

正式云模式需要同时配置 DeepSeek、Seedream 和 Qwen。缺少任一 Key、Seedream 结果
Host 白名单或正数价格时，系统不会进入云模式。

## 在产品 UI 中配置（推荐）

1. 本机启动 `ppt-agent serve`，打开“模型设置”。
2. 填写 DeepSeek、Seedream、Qwen API Key。
3. 填写 Seedream 返回图片 URL 的精确 Host。不要写协议、路径或通配符；多个 Host
   用逗号分隔。
4. 按 Provider 官方计费页填写当前价格和你自己的价格版本标识。
5. 点击“保存并启用”。若有任务运行，界面显示“待激活”，任务结束后再热切换；
   已运行任务不会中途更换 Provider。

响应只返回 `configured`、存储后端和末四位掩码，不返回 Key。Key 文件位于
`workspace/.secrets/providers/*.key`，非密钥配置位于
`workspace/config/provider-settings.json`。

## Provider 职责

| Provider | 用途 | 默认模型配置 |
|---|---|---|
| DeepSeek | Brief、Outline、DesignSystem、PageSpec 结构化规划 | `deepseek-chat` |
| Seedream | 页面背景、插图、装饰与参考图编辑 | Seedream 4.0 配置项 |
| Qwen | 输入图片理解、区域定位、OCR 与页面视觉复核 | `qwen-vl-max` / OCR 配置项 |

工作流不会假设三个接口语义相同。远程调用都先持久化 Operation/Attempt 并原子预留
预算；远端结果未知时停止自动重试，等待用户检查并显式授权。

## 环境变量方式

```powershell
$env:PPT_AGENT_PROVIDER_MODE = 'cloud'
$env:DEEPSEEK_API_KEY = '...'
$env:SEEDREAM_API_KEY = '...'
$env:QWEN_API_KEY = '...'
$env:PPT_AGENT_SEEDREAM_RESULT_HOSTS = '精确的结果域名'
$env:PPT_AGENT_PRICING_VERSION = 'official-2026-10'
# 同时设置各 Provider 的正数价格变量
& '.\.venv\Scripts\ppt-agent.exe' doctor
& '.\.venv\Scripts\ppt-agent.exe' serve
```

生产环境更推荐 `*_API_KEY_FILE` 指向 Docker/Kubernetes Secret。不要把 Key 写进 TOML、
`.env` 模板、截图、Issue 或测试输出。

## 连接检查与常见问题

保存后可在每个 Provider 旁点击“测试连接”，也可通过
`POST /api/v1/providers/{deepseek|seedream|qwen}/test` 显式检查。检查会产生真实网络请求，
但不执行完整 PPT 生成。

- `provider_configuration_invalid`：检查三组 Key、结果 Host 和价格是否完整。
- Seedream 下载失败：确认返回 URL 的精确 Host 已加入白名单，且为 HTTPS。
- 设置显示“待激活”：等待当前生成/解析任务结束后刷新。
- `outcome_unknown`：先到 Provider 控制台确认请求是否成功，不要盲目重新生成。

真实 Provider 测试默认跳过；只有显式设置 `LIVE_PROVIDER_TESTS`、
`LIVE_SEEDREAM_TESTS` 或 `LIVE_QWEN_TESTS` 才可能产生费用。
