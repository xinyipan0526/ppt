# 快速开始

PPT Agent 是本地优先的单用户产品。服务、React 工作台、SQLite、任务调度与
Artifact 文件都在同一台机器运行；配置云模型后，资料和生成请求才会发送给对应
Provider。

## Windows 本机安装

需要 Python 3.12。最终用户不需要安装 Node.js。

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\pip.exe' install .
& '.\.venv\Scripts\playwright.exe' install chromium
& '.\.venv\Scripts\ppt-agent.exe' doctor
& '.\.venv\Scripts\ppt-agent.exe' serve
```

服务启动后会自动打开 `http://localhost:8000/`。若不希望自动打开浏览器：

```powershell
& '.\.venv\Scripts\ppt-agent.exe' serve --no-open
```

如果命令提示 `ModuleNotFoundError: No module named 'ppt_agent'`，说明虚拟环境中残留了旧的启动器，或上一次安装被中断。先关闭正在运行的 PPT Agent 窗口，再执行一次强制重装：

```powershell
Get-Process ppt-agent -ErrorAction SilentlyContinue | Stop-Process -Force
& '.\.venv\Scripts\python.exe' -m pip install --no-cache-dir --force-reinstall .
& '.\.venv\Scripts\ppt-agent.exe' doctor
& '.\.venv\Scripts\ppt-agent.exe' serve --no-open
```

始终使用 `.venv\Scripts\ppt-agent.exe`（或 `.venv\Scripts\python.exe -m ppt_agent.cli`），不要混用系统 Python 环境中的同名命令。

首次运行会创建 `workspace/`，其中包含 SQLite 状态、版本化 Artifact 和本地 Secret。
不要把该目录提交到 Git。无 API Key 时系统保持确定性模式，仍可完整验证上传、
澄清、规划、8 页生成、编辑、恢复和导出流程。

## 第一次制作 PPT

1. 打开“模型设置”。试用可保持确定性模式；正式生成按
   [Provider 设置](provider-setup.md)填写一个火山方舟 API Key，并从下拉列表选择一个
   多模态语言模型和一个图片模型。价格、端点与结果域名已有安全默认值，不要求填写。
2. 回到“项目”，创建项目。
3. 上传 Markdown、PDF、PNG、JPEG 或 WebP，也可以直接填写创作意图。
4. 回答必要的澄清问题；在规划复核页修改每一页内容与设计方向。
5. 批准后等待生成。关闭浏览器不会取消后台任务，重新打开会从 SQLite 恢复。
6. 在完整 PPT 画廊中点击页面，分别查看交付文字、设计 Prompt、局部修改 Prompt
   与不可变历史版本。
7. 使用“全局重生成”创建新的整套版本，或下载图片型 PPTX、PDF、图片 ZIP。

## Docker

Docker 构建会在独立 Node 阶段编译 React；运行镜像不包含 Node 开发环境。

```powershell
docker compose up --build
```

打开 `http://localhost:8000/`。数据保存在 `ppt-agent-data` Volume。容器仅映射宿主机
回环地址，不应直接暴露到公网。

## 源码开发

```powershell
& '.\.venv\Scripts\pip.exe' install -e ".[dev]"
Set-Location web
npm install
npm run dev
```

Vite 开发服务代理 `/api` 到 `127.0.0.1:8000`。生产使用前执行 `npm run build`，
并将 `web/dist` 内容同步到 `src/ppt_agent/static`；仓库已提交可直接打包的构建产物。

源码开发时，Vite 页面改动会热更新；Python 后端改动需要重启后端（除非使用显式的
reload 启动方式）。通过 `pip install .` 安装的正式版本不会读取仓库源码：拉取更新后
必须重新构建/安装并重启 `ppt-agent serve`，浏览器刷新后才会使用新的后端与静态资源。
