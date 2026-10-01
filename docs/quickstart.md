# 快速开始

## 本机确定性模式

确定性模式不需要任何 API Key，可完整运行素材上传、规划澄清、8 页生成、编辑、质量检查和导出，适合验证安装与自动化测试。

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\pip.exe' install -e ".[dev]"
& '.\.venv\Scripts\playwright.exe' install chromium
& '.\.venv\Scripts\ppt-agent.exe' doctor
& '.\.venv\Scripts\ppt-agent.exe' serve
```

首次启动会在终端显示本地访问令牌，并保存到 `workspace/.secrets/access-token`。HTTP 请求必须同时满足本地 Host/Origin 检查和 `Authorization: Bearer <token>`。

使用配置文件启动：

```powershell
Copy-Item config/ppt-agent.example.toml ppt-agent.toml
& '.\.venv\Scripts\ppt-agent.exe' --config ppt-agent.toml serve
```

数据库迁移通常由启动流程自动执行，也可显式执行：

```powershell
& '.\.venv\Scripts\ppt-agent.exe' --config ppt-agent.toml migrate
```

完整 HTTP 操作见 [API 教程](api-tutorial.md)，云模型配置见 [Provider 配置](provider-setup.md)。

## Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

容器仅映射到宿主机 `127.0.0.1:8000`，状态保存在命名卷 `ppt-agent-data`。访问令牌可通过 `docker compose logs ppt-agent` 查看；正式使用建议在 `.env` 中设置至少 43 字符的 `PPT_AGENT_ACCESS_TOKEN`。
