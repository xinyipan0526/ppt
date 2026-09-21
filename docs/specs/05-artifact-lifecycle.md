# Artifact 提交、依赖与失效协议

## 目标

解决 SQLite 元数据与文件系统无法跨资源原子提交的问题，并定义局部重规划、缓存、撤销和垃圾回收所依赖的 Artifact 图谱。

## 身份

每个 Artifact 同时具有：

- `logical_id`：业务逻辑身份，例如“第 3 页当前背景视觉”。
- `artifact_id`：不可变版本身份。
- `content_hash`：文件内容哈希。
- `spec_fingerprint`：产生该版本的规范、Provider、Prompt、Skill、Renderer 和配置哈希。

相同 `logical_id` 可以有多个不可变版本。当前采用哪个版本由显式引用决定，不覆盖历史文件。

## 生命周期

```text
staging
  → file_committed
  → metadata_committed
  → available
      ├── stale
      ├── superseded
      ├── invalid
      └── deleted
```

- `staging`：正在写临时文件，不可引用。
- `file_committed`：最终文件已原子落盘，但业务元数据可能尚未提交。
- `metadata_committed`：元数据和依赖边已写入，但尚未完成最终可用检查。
- `available`：允许下游引用和导出。
- `stale`：上游变化，内容可能仍可查看但不能作为新 Run 的默认输入。
- `superseded`：同一逻辑身份已有明确替代版本。
- `invalid`：校验失败、文件损坏或违反 Schema。
- `deleted`：逻辑删除，等待或已经完成物理回收。

## 文件提交协议

1. 在 SQLite 中创建 `staging` Artifact 记录和临时相对路径。
2. 在最终目录所在的同一文件系统创建 staging 文件。
3. 流式写入并计算哈希。
4. flush；对要求耐久性的文件执行 `fsync`。
5. 校验格式、尺寸、解码能力和期望哈希。
6. 原子重命名到内容寻址的最终路径。
7. 将 Artifact 状态更新为 `file_committed`。
8. 在 SQLite 事务中写入完整元数据、依赖边和 `metadata_committed` 状态。
9. 完成最终一致性检查后转为 `available`。

步骤之间任何位置都可能崩溃，因此协议依赖启动对账，而不是宣称数据库与文件系统具有跨资源事务。

## 启动对账

启动时或维护任务中执行：

- 清理超过保留期且没有活跃 Operation 的 staging 文件。
- 为 `file_committed` 且文件存在的记录补写或恢复元数据事务。
- 将元数据存在但文件缺失的 Artifact 标记为 `invalid`。
- 将最终目录中没有元数据引用的文件登记为 orphan。
- 校验 `available` 文件的存在性；按配置抽样复算哈希。
- 只有完成对账后才允许依赖异常 Artifact 的 Run 自动恢复。

## 依赖边

至少支持：

- `derived_from`：输出由输入计算产生。
- `references`：运行时引用但不决定内容身份。
- `supersedes`：新版本取代旧版本。
- `composed_from`：最终页面由视觉资产、Render Manifest 等组合产生。
- `validates`：Quality Report 验证某个 Artifact。

依赖图必须无非法环。`supersedes` 可以形成版本链，但不能参与内容派生拓扑。

## 失效规则

| 上游变化 | 默认失效范围 |
|---|---|
| Brief 的受众、语言、主题 | DeckOutline 及全部下游 |
| Brief 页数 | DeckOutline、受影响页面顺序、页码和导出 |
| DesignSystem 字体或颜色 | 全部 Render Manifest、Page Image、Thumbnail 和导出 |
| 单页正文 | 该页 Render Manifest、Page Image、Thumbnail 和导出 |
| 单页视觉 Prompt | 该页 Visual Asset、Page Image、Thumbnail 和导出 |
| 页面顺序 | 页码、目录页、跨页引用和导出；不必重生成独立视觉资产 |
| Provider 或模型版本 | 只使依赖该 Provider 输出且需要重算的缓存失效，不主动删除历史 Artifact |
| Renderer 或字体哈希 | 全部依赖该渲染环境的 Page Image 和下游导出 |

字段级影响规则必须版本化并由测试覆盖。不能只比较对象更新时间。

## 回滚

- 回滚 Brief 或 PageSpec 创建新的当前引用，不删除后续历史版本。
- 如果历史 Artifact 的完整 `spec_fingerprint` 仍匹配，可以直接复用。
- 如果 Provider、Prompt、Skill、Renderer、字体或关键配置不同，历史 Artifact 只能供用户预览，不能自动视为缓存命中。
- 回滚行为创建新 Run，以便成本、上下文和用户决策可追踪。

## 缓存命中

至少比较：

- 输入 Artifact 内容哈希。
- 结构化 PageSpec/DesignSystem 规范化哈希。
- Provider、模型和地域。
- Prompt 与 Skill 版本。
- Renderer、Chromium 和字体集合哈希。
- 图像尺寸、采样和安全策略配置。
- 影响输出的 Feature Flag。

图片生成具有随机性，命中缓存时 UI 必须明确区分“复用已有版本”和“重新采样”。

## 引用计数与垃圾回收

- 引用计数来源于数据库依赖边和当前项目引用，不以目录扫描猜测。
- 活跃 Run、未解决 Interrupt、非终态或 `outcome_unknown` Provider Attempt 和导出包引用的 Artifact 不得回收。
- superseded/stale Artifact 进入保留期，默认允许用户撤销。
- orphan 文件先隔离到回收区，再按保留期物理删除。
- 删除项目需要生成可审计的回收计划。

## 导出规则

- ZIP、PDF 和 PPTX 只能依赖 `available` 的最终 `page_image`。
- 任何必需 Page Artifact 处于 `stale` 或 `invalid`，或者其必需 Provider Attempt 处于 `outcome_unknown` 且无法证明页面完整时，默认禁止静默导出。
- 用户强制导出时必须在 UI 明示风险，并将该决定写入 Run 事件。

## 测试

- 在每个提交阶段模拟进程退出并执行对账。
- 孤儿文件和缺失文件均能发现。
- 字段变化只传播到规则指定的下游。
- 回滚能复用完全匹配的历史版本。
- 字体或 Renderer 变化不会错误命中页面缓存。
- 活跃引用不会被垃圾回收。
