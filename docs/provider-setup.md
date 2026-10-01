# 云 Provider 配置

将 `PPT_AGENT_PROVIDER_MODE` 设为 `cloud` 后，DeepSeek、Seedream 和 Qwen 三组 Key、HTTPS Endpoint、Seedream 结果域名白名单以及正数价格都必须配置。服务不会在缺项时降级为伪云调用。

1. 在 DeepSeek 控制台创建 Key，配置 `DEEPSEEK_API_KEY` 和实际模型 ID。
2. 在火山方舟启用 Seedream 并配置 `SEEDREAM_API_KEY`、Endpoint/模型 ID。把账号所在区域返回图片 URL 的精确主机名加入 `PPT_AGENT_SEEDREAM_RESULT_HOSTS`，多个主机用逗号分隔；不支持通配符。
3. 在阿里云百炼创建 Key。按账号区域设置兼容 OpenAI 的 Base URL，并配置 Qwen VL 与 OCR 模型 ID。
4. 从各账号当前计费页抄录价格到 `.env`。示例中的 `0` 只是占位，cloud 模式会拒绝启动。
5. 执行 `ppt-agent doctor`，再启动服务并调用 `POST /api/v1/providers/{deepseek|seedream|qwen}/test`。

Seedream 返回 URL 会经过 HTTPS、端口、主机白名单、重定向禁止、解析地址和响应体/图片限制检查。上传图片与生成页面会发送到所配置的 Qwen/Seedream 服务；敏感 Key 不会出现在状态 API 或日志配置快照中。

## 真实契约测试

测试默认全部跳过，只有显式开关才会产生真实、可能计费的请求：

```powershell
$env:LIVE_PROVIDER_TESTS='1'   # DeepSeek
$env:LIVE_SEEDREAM_TESTS='1'  # 生成 + 参考图编辑，两次图片调用
$env:LIVE_QWEN_TESTS='1'      # 视觉理解 + OCR
& '.\.venv\Scripts\pytest.exe' tests/live/providers -m live_provider -v
```

不要在共享 CI 中设置这些开关。Seedream live 测试会产生两次图片费用。
