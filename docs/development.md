# 开发指南

本指南面向需要修改项目源码的开发者，重点回答两个问题：修改后端后如何更新，修改前端后如何更新。

## 1. 首次准备

在项目根目录执行：

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\pip.exe' install -e ".[dev]"
& '.\.venv\Scripts\playwright.exe' install chromium
Set-Location web
npm install
Set-Location ..
```

开发前端需要 Node.js；只运行已经构建好的产品不需要 Node.js。

## 2. 日常开发启动方式

打开两个终端。

终端一启动后端：

```powershell
& '.\.venv\Scripts\ppt-agent.exe' serve --reload --no-open
```

终端二启动前端：

```powershell
Set-Location web
npm run dev
```

开发时访问 <http://127.0.0.1:5173>。前端开发服务器会把 API 请求转发给后端。

## 3. 修改后端代码后怎么更新

修改 `src/ppt_agent/` 下的 Python 文件并保存即可：

- `serve --reload` 会自动重启后端。
- 不需要重新执行 `pip install`。
- 浏览器没有刷新数据时，手动刷新页面即可。

如果修改了 `pyproject.toml` 中的依赖，则重新安装依赖，再重启后端：

```powershell
& '.\.venv\Scripts\pip.exe' install -e ".[dev]"
```

自动重载会中断当前进程。正在执行真实付费任务时，不要修改后端代码；等待任务结束后再更新。

## 4. 修改前端代码后怎么更新

使用 `npm run dev` 时，修改 `web/src/` 并保存即可：

- Vite 会自动热更新页面。
- 不需要重启后端。
- 偶尔状态未刷新时，手动刷新浏览器。

如果要让后端的 <http://127.0.0.1:8000> 使用最新前端，需要重新构建并同步静态文件：

```powershell
Set-Location web
npm run build
Set-Location ..
if (Test-Path -LiteralPath '.\src\ppt_agent\static\assets') {
    Remove-Item -LiteralPath '.\src\ppt_agent\static\assets' -Recurse -Force
}
Copy-Item -Path '.\web\dist\*' -Destination '.\src\ppt_agent\static' -Recurse -Force
```

然后重启后端，并刷新浏览器。

## 5. Docker 中更新代码

后端或前端代码变化后，重新构建并启动镜像：

```powershell
docker compose up --build --force-recreate
```

## 6. 提交前检查

后端：

```powershell
& '.\.venv\Scripts\pytest.exe' -q --basetemp='.cache/pytest-final'
& '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations
```

前端：

```powershell
Set-Location web
npm test
npm run build
Set-Location ..
```

最后检查补丁格式：

```powershell
git diff --check
git status --short
```

真实 Provider 测试默认关闭并可能产生费用。日常开发优先使用确定性模式；模型配置和验收
边界见[火山方舟模型配置](provider-setup.md)。
