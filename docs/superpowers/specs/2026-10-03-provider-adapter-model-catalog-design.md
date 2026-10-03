# Provider Adapter 与模型目录重构设计

## 目标

将当前按 DeepSeek、Seedream、Qwen 三个固定 Provider 组合的运行时，重构为按能力选择模型的架构。第一版只支持火山方舟，但边界必须允许以后通过新增 Adapter 接入其他平台，而不修改 Workflow。

面向普通用户的云模型配置只要求：

1. 一个火山方舟 API Key。
2. 一个受支持的多模态模型 ID。
3. 一个受支持的图像模型 ID。

首版推荐组合为：

- 多模态：`doubao-seed-2-1-pro-260915`。
- 多模态经济型：`doubao-seed-2-1-lite-260915`。
- 多模态实验型：`deepseek-v4-1-flash-260910`，只有通过同一能力契约测试后才能启用。
- 图像：`doubao-seedream-5-0-pro-260628`。

上述 ID 进入版本化内置模型目录。目录升级随应用版本发布，不从不可信客户端动态注入。

## 非目标

- 本批次不接入方舟之外的平台。
- 不实现基于负载或成本的多模型自动路由。
- 不允许普通用户输入目录之外的任意模型 ID。
- 不改变图片型 PPTX 边界。
- 不削弱 Operation/Attempt、成本预留、Artifact 或 `outcome_unknown` 恢复语义。

## 架构

### 能力层

Workflow 只依赖能力，不识别厂商或模型名称。现有细粒度协议继续作为内部执行边界：

- `StructuredTextProvider`
- `VisionProvider`
- `OCRProvider`
- `ImageProvider`

配置和运行时资格判断使用两个面向用户的能力组：

- `multimodal`：必须同时提供严格结构化文本、图片理解、OCR 和区域定位。
- `image`：必须同时提供图片生成和参考图编辑。

云模式的最低条件是两个能力组各选择一个合格模型。Qwen 不再是必需 Provider。

### Adapter 层

新增 Adapter 注册表。首版注册 `volcengine_ark`：

- 统一持有方舟 endpoint、鉴权和 HTTP 客户端策略。
- 多模态调用使用方舟 Responses API，并实现结构化文本、视觉、OCR 和定位协议。
- 图片调用使用方舟 Image Generation API，并实现生成与编辑协议。
- 统一归一化 Usage、错误码、是否已知计费和是否可安全重试。
- 网络提交后结果未知时保持 `outcome_unknown`，不得自动重发。

模型差异由模型目录和请求策略处理，不为 Seed 2.1 Pro、Lite 等复制完整 Adapter。

### 模型目录

每个目录项包含：

- 稳定的 `catalog_id`
- `adapter_id`
- 提交给远端的精确 `model_id`
- 展示名称和推荐状态
- `multimodal` 或 `image` 类别
- `stable`、`experimental` 或 `deprecated` 生命周期状态
- 细粒度能力集合
- 上下文、输出、图片数量、像素与格式限制
- 版本化默认价格、币种、来源和生效日期
- 允许的 endpoint 与图片结果 Host

Workflow 不根据名称推断能力。实验模型只有通过契约测试后才可声明生产能力。

## 配置与 Secret

非密钥配置升级为 schema v2，核心字段为：

```json
{
  "schema_version": 2,
  "adapter_id": "volcengine_ark",
  "multimodal_model_id": "doubao-seed-2-1-pro-260915",
  "image_model_id": "doubao-seedream-5-0-pro-260628",
  "pricing_overrides": {}
}
```

API Key 按 Adapter 存入 Secret Store：

```text
workspace/.secrets/adapters/volcengine_ark.key
```

普通配置不要求价格、价格版本、Base URL、Region 或结果 Host。第一版的方舟 endpoint 和 Region 固定为官方北京区域；结果 Host、默认价格及价格版本来自模型目录。高级价格覆盖为可选项，并在所有成本展示中标识为自定义。

一个 Run 启动时冻结 `adapter_id`、两个模型 ID、能力声明、配置版本和价格版本。运行中修改配置只影响新 Run。

## HTTP API

- `GET /api/v1/model-catalog`：返回可选模型、能力、默认价格和推荐状态。
- `GET /api/v1/settings/model-runtime`：返回模型选择与脱敏 Adapter 状态。
- `PUT /api/v1/settings/model-runtime`：原子更新 Key、模型选择和可选覆盖。
- `POST /api/v1/adapters/{adapter_id}/test`：验证鉴权、模型访问和最低协议能力。
- `DELETE /api/v1/adapters/{adapter_id}/credential`：删除 Adapter Key。

密钥成功提交后立即从浏览器表单清空。响应、SQLite、事件、日志、URL、Query Cache、localStorage 和 sessionStorage 都不能包含密钥。

更新配置时先构建候选快照并验证。验证或 Secret 写入失败时保留原活动配置。有昂贵任务运行时，新配置保存为 pending；任务空闲后原子激活。

## 用户体验

模型设置页默认只展示：

- 火山方舟 API Key。
- 多模态模型下拉框，默认推荐 Doubao Seed 2.1 Pro。
- 图像模型下拉框，默认推荐 Seedream 5.0 Pro。
- “测试并启用”按钮。

界面不展示或要求用户理解 Token 单价、图片单价、价格版本、结果 Host、Base URL、Region 或 Qwen Key。高级设置默认折叠，全部可留空。

保存流程：

1. 前端提交 Key 和两个目录模型 ID。
2. 后端校验模型类别、生命周期和能力集合。
3. 后端执行轻量鉴权、模型可访问性和协议兼容检查。
4. 后端原子保存 Secret 与非密钥配置。
5. 空闲时立即激活；繁忙时标记 pending。

错误只暴露安全分类：鉴权失败、模型无权限、模型不可用、能力不匹配或远端结果未知。

## 成本

支持目录中的生产模型必须带有默认价格快照，因此普通用户不需要填写价格。价格采用最小货币单位的整数或足够精度的定点表示，不能因 UI 整数约束丢失精度。

Seedream 5.0 Pro 按输出像素档位计价，且多参考图可能产生输入图费用；预留逻辑必须依据请求的最大输出像素和参考图数量保守计算，不能继续使用单一固定“每张价格”假设。

若未来目录模型无法获得可靠价格，必须使用目录定义的保守单次调用上限，不能要求普通用户填写价格，也不能显示虚假的精确金额。

## 迁移

- 保留旧 provider 配置和三份旧 Secret，不静默删除。
- 新版本首次启动时读取旧配置并生成 v2 候选配置。
- 可迁移旧 Seedream 的方舟 endpoint 与非密钥安全设置。
- 不能假设旧 `DEEPSEEK_API_KEY` 是方舟 Key，因此不自动复制为 Ark 凭据。
- 旧 Qwen 配置不再参与云模式资格判断，但保留以支持版本回退。
- 未完成 v2 配置时进入确定性模式，并提示选择两个模型。
- 旧 API 在一个兼容窗口内返回明确弃用信息，不静默解释为新配置。

## 失败与恢复

- 鉴权、无权限和参数错误是明确失败，不自动重试。
- 只有 Provider 明确证明未计费且可重试的临时失败才能有界重试。
- 请求可能已经到达方舟但响应未知时进入 `outcome_unknown`。
- Schema 不合格的成功响应仍按返回 Usage 结算，再记录内容失败。
- 配置阶段发现能力不匹配时拒绝启用，不能留到 Workflow 中途失败。
- 图片 URL 仍执行 HTTPS、精确 Host、DNS/IP、重定向、媒体、像素和字节限制。

## Web 构建与本地交付

开发模式使用 editable Python 安装和 Vite：React 源码通过 HMR 实时刷新，后端源码修改需要重启服务或显式启用 reload。

正式 `ppt-agent serve` 只提供打包在 `ppt_agent/static` 中的构建产物。修改 `web/src` 后必须重新构建并同步静态文件；普通安装的 Python 包还必须重新安装并重启。最终交付应重新构建 Web、安装当前工作区版本，并提醒用户重启正在运行的服务。

## 测试与验收

单元与契约测试覆盖：

- 模型目录唯一性、能力完整性、生命周期和价格精度。
- 方舟 Responses 请求、图片输入、严格 JSON Schema 和 Usage 解析。
- OCR 与归一化区域坐标 Schema。
- Seedream 5.0 Pro 单图生成、多参考图编辑、像素档位预留和结果解析。
- 所有错误到统一错误语义的映射。
- Secret 原子替换、回滚、脱敏和旧配置迁移。

集成测试覆盖：

- 仅一个 Ark Key 加两个模型 ID 即可启用云模式。
- 缺少任一必填项时保持确定性模式。
- 配置 pending/activation 和 Run 快照冻结。
- 规划、素材视觉分析、生产、局部视觉编辑和 OCR 质检均使用同一多模态模型选择。
- `outcome_unknown`、预算预留和恢复行为不回退。

前端测试覆盖三字段主流程、下拉目录、错误展示、密钥清空和浏览器存储泄漏。Chromium E2E 覆盖从配置到创建任务的闭环。真实方舟测试继续使用显式付费开关，默认跳过。

最终验收运行 Python 全量测试、React 单元测试、生产构建、Chromium E2E、`compileall` 和 `git diff --check`。

## 官方依据

- 方舟 Seed 2.1 多模态与发布信息：<https://docs.volcengine.com/docs/ark/model-release-announcement?lang=zh>
- 方舟 Responses API 结构化输出：<https://docs.volcengine.com/docs/ark/list-model-responses-api?lang=en>
- Seedream 版本与模型 ID：<https://docs.volcengine.com/docs/ark/seedream-4-0-5-0?lang=zh>
- Seedream 图片生成 API：<https://docs.volcengine.com/docs/ark/image-generation-api?lang=en>
- 方舟模型价格：<https://docs.volcengine.com/docs/ark/model-pricing?lang=zh>
