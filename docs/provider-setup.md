# 模型设置

当前版本只提供火山方舟（`volcengine_ark`）Adapter。进入云模式只需要三项：一个
火山方舟 API Key、一个受支持的多模态语言模型 ID、一个受支持的图片模型 ID。
产品 UI 使用下拉列表，展示友好名称，实际保存并发送的是下表中的精确 ID。

## 在产品 UI 中配置（推荐）

1. 本机启动 `ppt-agent serve`，打开“模型设置”。
2. 填写一个火山方舟 API Key。
3. 选择一个多模态语言模型和一个图片生成模型。
4. 点击“测试并启用”。系统先做不产生 PPT 生成费用的连接与模型可用性检查，再保存。

价格、方舟端点、Seedream 结果 Host 和能力声明来自版本化模型目录，不要求用户填写。
“高级设置”中的价格覆盖只用于账号实际合同价与目录价不一致的场景，完全可留空。
若已有任务运行，新设置显示“待激活”，任务结束后切换；运行中的任务不会更换模型。

API Key 不会返回浏览器，也不会进入 JSON、SQLite、日志或事件；提交成功后密码框立即
清空。Key 保存于 `workspace/.secrets/providers/volcengine_ark.key`，非密钥配置保存于
`workspace/config/provider-settings.json`。

## 支持的模型

| 类别 | 下拉显示名称 | 精确模型 ID | 状态 |
|---|---|---|---|
| 多模态 | Doubao Seed 2.1 Pro | `doubao-seed-2-1-pro-260915` | 稳定、推荐 |
| 多模态 | Doubao Seed 2.1 Lite | `doubao-seed-2-1-lite-260915` | 稳定、低成本 |
| 多模态 | DeepSeek V4.1 Flash | `deepseek-v4-1-flash-260910` | 实验性，默认不在可选列表中 |
| 图片 | Doubao Seedream 5.0 Pro | `doubao-seedream-5-0-pro-260628` | 稳定、推荐 |

Doubao Seed 2.1 同时承担结构化规划、图片理解、区域定位和 OCR，因此不再要求单独的
DeepSeek 与 Qwen Key。Seedream 5.0 Pro 负责图片生成和参考图编辑。工作流只依赖能力
协议；以后增加模型或 Provider 时，通过新增 Adapter 和目录条目接入，无需让业务流程
理解不同厂商的请求语义。

## 环境变量方式

```powershell
$env:PPT_AGENT_PROVIDER_MODE = 'cloud'
$env:ARK_API_KEY = '...'
$env:PPT_AGENT_MULTIMODAL_MODEL_ID = 'doubao-seed-2-1-pro-260915'
$env:PPT_AGENT_IMAGE_MODEL_ID = 'doubao-seedream-5-0-pro-260628'
& '.\.venv\Scripts\ppt-agent.exe' doctor
& '.\.venv\Scripts\ppt-agent.exe' serve
```

Secret Manager 或容器可使用 `ARK_API_KEY_FILE`。不要把真实 Key 写进 TOML、`.env`
模板、截图、Issue 或测试输出。

## 调用与恢复边界

所有可能计费的远端调用仍先持久化 Operation/Attempt，并原子预留预算。明确未计费且
可重试的失败才会自动重试；远端结果不确定时进入 `outcome_unknown`，不会自动重发。

常见错误：

- `model_runtime_invalid`：模型 ID 不在目录内，或模型类别选错。
- `authentication_error`：API Key 无效或无对应方舟资源权限。
- `model_unavailable`：所选模型未在当前账号/区域开通。
- `outcome_unknown`：先到方舟控制台核对请求，再决定是否显式重试。

真实付费 Smoke Test 默认关闭。本次 UI 的“测试并启用”只检查凭据与模型可访问性，
不会生成 PPT 或图片；正式发布前仍应由账号持有人明确授权执行真实调用验收。
