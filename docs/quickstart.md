# 快速开始

本指南只做一件事：用最少步骤启动 PPT Agent，并生成第一份 PPT。

## 1. 准备环境

你需要：

- Windows 10/11
- Python 3.12
- 本项目源码

以下命令都在项目根目录执行。正常使用不需要安装 Node.js。

## 2. 安装

首次使用时执行：

```powershell
py -3.12 -m venv .venv
& '.\.venv\Scripts\pip.exe' install .
& '.\.venv\Scripts\playwright.exe' install chromium
& '.\.venv\Scripts\ppt-agent.exe' doctor
```

`doctor` 全部通过后即可启动。

## 3. 启动

```powershell
& '.\.venv\Scripts\ppt-agent.exe' serve
```

浏览器通常会自动打开。没有自动打开时，访问：

<http://127.0.0.1:8000>

停止服务时，在终端按 `Ctrl+C`。

## 4. 配置火山方舟（可选）

不配置 API Key 也能使用内置的确定性模式体验完整流程，但不会调用真实云模型。

需要真实生成图片时：

1. 打开右上角“模型设置”。
2. 填写一个火山方舟 API Key。
3. 选择一个多模态模型和一个图片模型。
4. 点击“测试并启用”。

模型 ID、区域和常见错误见 [火山方舟模型配置](provider-setup.md)。真实模型调用可能产生费用。

## 5. 生成第一份 PPT

1. 创建项目。
2. 输入主题；也可以上传 Markdown、PDF 或图片资料。
3. 回答必要的澄清问题。
4. 检查并批准逐页规划。
5. 等待生成完成，然后导出 PPTX、PDF、PNG ZIP 或 JPG ZIP。

浏览器关闭不会取消后台任务；重新打开页面后可以继续查看进度。

## Docker 启动（可选）

已经安装 Docker Desktop 时，也可以直接运行：

```powershell
docker compose up --build
```

然后访问 <http://127.0.0.1:8000>。

## 遇到问题

- 启动失败：先运行 `& '.\.venv\Scripts\ppt-agent.exe' doctor`。
- 需要修改源码：见 [开发指南](development.md)。
- 需要调整端口、Workspace、日志或备份：见 [配置与运维](configuration.md)。
- 不清楚页面如何操作：见 [界面使用指南](ui-guide.md)。
- 仍无法定位：查看 `workspace/logs/ppt-agent.jsonl`。
