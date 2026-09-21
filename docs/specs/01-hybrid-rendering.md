# 混合栅格渲染协议

## 目标

在保持图片型 PPT 视觉表现的同时，让标题、正文、数字、引用、表格和页码具有确定性、可校验的交付质量。

默认页面不再由生图模型直接绘制全部文字，而是将生成式视觉层与确定性内容层合成为最终栅格图片。

## 渲染模式

每页显式选择一种 `render_mode`：

- `hybrid`：默认。模型生成背景、插图和装饰，浏览器渲染文字、表格、简单图表及页码。
- `deterministic`：不调用生图模型，适合正文、表格和数据密集页面。
- `full_raster`：整页生图，只适用于少文字、海报式或用户明确选择的页面。

系统不能因为模板实现方便而自动将文字密集页降级为 `full_raster`。

## 页面坐标系

- 设计坐标固定为 `1920 × 1080`，使用逻辑像素。
- 导出时允许等比例缩放，不改变布局计算。
- 所有内容必须位于页面安全区内。
- PageSpec 中的区域使用绝对逻辑坐标，归一化坐标仅用于 Provider 定位结果交换。

## PageSpec 扩展

```yaml
page_id: page_01
render_mode: hybrid
canvas:
  width: 1920
  height: 1080
  safe_area: { top: 64, right: 72, bottom: 56, left: 72 }
visual_regions:
  - id: hero_visual
    box: { x: 960, y: 0, width: 960, height: 1080 }
    kind: generated_image
    prompt_ref: prompt_01
text_blocks:
  - id: title
    role: title
    text: "季度增长概览"
    box: { x: 96, y: 120, width: 760, height: 180 }
    typography:
      font_family_token: heading
      font_size: 68
      min_font_size: 48
      line_height: 1.15
      font_weight: 700
    overflow: shrink_then_reflow
    max_lines: 2
    language: zh-CN
```

`text_blocks` 至少包含：

- 稳定的逻辑 ID 和语义角色。
- 原始文案，不允许 Renderer 再让模型改写。
- 文本框、层级和对齐方式。
- 字体 Token、字号、最小字号、行高和字重。
- 最大行数、断行和溢出策略。
- 语言、方向和强调片段。
- 是否允许截断；标题、数字、引用默认禁止截断。

表格、图表和页码使用独立结构，不通过不可解析的 HTML 字符串传递。

## 生成与合成流程

```text
PageSpec
  → Layout Planner 生成视觉区与内容区
  → Prompt Builder 明确要求视觉区不生成文字并预留安全区
  → Seedream 生成背景/插图/装饰资产
  → HTML/CSS/SVG Renderer 排版文字、表格和简单图表
  → Chromium 以固定 viewport 截图
  → Composite Validator 检查布局和最终图片
  → Artifact Commit
```

如果生成的视觉资产侵入文字安全区，系统优先裁切、遮罩或重新生成视觉资产，不允许通过缩小正文到最小字号以下来掩盖问题。

## 确定性 Renderer

首版使用 HTML/CSS/SVG 与无头 Chromium：

- HTML/CSS 负责文字、表格和常规布局。
- SVG 负责简单图形、图标和确定性数据图表。
- Chromium 使用固定版本、固定 viewport、固定 device scale factor 和固定字体集合。
- 禁止页面模板访问网络、执行任意脚本或读取 Workspace 外文件。
- 截图前等待字体加载完成，并校验 DOM 布局报告。

Renderer 的输入是版本化 Schema，不接受模型直接产生并执行任意 HTML、CSS 或 JavaScript。模型只能选择受支持的布局 Token 和属性。

## 字体策略

- 维护 `FontRegistry`，按中文、拉丁字符和符号分别定义回退链。
- 项目启动时执行字体预检并记录实际字体文件哈希。
- 缓存键必须包含 Renderer 版本、Chromium 版本和字体集合哈希。
- 找不到指定字体时使用已批准回退字体并产生警告，不静默使用系统随机字体。
- 最小字号属于硬约束；无法容纳时返回 `layout_overflow`，进入重新规划或人工处理。

## 溢出策略

按顺序执行：

1. 使用允许的自然断行和语言规则重新排版。
2. 在不低于 `min_font_size` 的范围内缩小字号。
3. 调整同一布局模板允许变化的间距。
4. 请求 Layout Planner 更换模板或拆页。
5. 中断并让用户确认删减文案。

禁止默认使用省略号处理标题、关键数字、引用或用户要求完整展示的正文。

## 修改路由

- “把标题变小”“改成蓝色”“替换数字”等文字和样式修改：直接更新 PageSpec 并重新确定性渲染，不调用 Seedream。
- “把右侧人物换成女性”等视觉修改：只编辑对应视觉资产，再合成原有确定性文字层。
- “把标题移到左边并让图片让出空间”等跨层修改：重新规划布局，按依赖规则使视觉资产和最终页面变为 stale。

这使自然语言局部编辑首先成为结构化页面编辑，只有视觉内容变化时才进入图像定位与编辑 Sub-workflow。

## 校验

渲染前：

- DOM bounding box 不得越过安全区。
- 不允许文字框互相覆盖，除非模板显式许可。
- 字号、对比度、行数和内容完整性必须满足 PageSpec。
- 表格单元格和图表标签必须通过确定性布局检查。

渲染后：

- OCR 用于验证最终合成图片与 PageSpec 文案一致，不用于生成文字。
- OCR 失败时先比较 DOM 渲染报告，区分识别误差和真实渲染错误。
- 视觉差分用于确认重渲染没有修改不应变化的视觉区域。

## Artifact

一页至少产生以下 Artifact：

- `visual_asset`：生成式背景或插图。
- `render_manifest`：Renderer 输入、字体和版本。
- `layout_report`：最终 DOM 坐标、实际字体和溢出结果。
- `page_image`：最终合成图片。
- `thumbnail`：页面预览。

导出只允许引用状态为 `available` 的 `page_image`。

## 验收条件

- 给定相同 Renderer、Chromium、字体和输入，输出布局可重复。
- 标题、数字、引用、页码和表格文字来自结构化字段，不来自生图像素。
- 文字样式修改不产生新的图片模型费用。
- 任何低于最小字号或越过安全区的页面不能进入 `available`。

