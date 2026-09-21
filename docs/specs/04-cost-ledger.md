# 原子预算账本协议

## 目标

保证并发页面生成时，所有正在执行的调用共同遵守项目硬预算，避免多个协程基于同一剩余额度同时提交并突破上限。

Run、Step、Page 和 Provider Attempt 的状态名称及转换以 [Workflow Runtime、并发与取消协议](02-workflow-runtime.md#正式状态模型)为唯一规范来源。

## 金额表示

- 数据库内使用最小货币单位的整数或定点十进制，禁止使用二进制浮点数。
- 每条记录包含 `currency` 和 `pricing_version`。
- 不同货币不能在没有明确汇率快照的情况下合并比较。

## 核心对象

### budget_accounts

- 项目总预算。
- 阶段预算。
- 硬上限和软上限。
- 当前结算费用和有效预留汇总。

### cost_reservations

- `reservation_id`
- `project_id`
- `stage`
- `operation_id`
- `amount_reserved`
- `currency`
- `pricing_version`
- `status`
- `expires_at`

### usage_records

- Provider 返回的 Token、图片数量、尺寸和计费单位。
- 估算费用和实际费用。
- 与 Operation、Attempt、Page 和 Reservation 的关联。

## 预留状态

```text
active
  ├── settled
  ├── released
  ├── partially_settled
  └── held_unknown
```

- `settled`：已转为实际费用。
- `released`：能够证明没有发生远端费用。
- `partially_settled`：Provider 返回部分结果或实际费用低于预留。
- `held_unknown`：远端结果或费用未知，不计入可用余额。

## 原子预留

在 SQLite 短写事务中：

1. 读取项目和阶段硬预算。
2. 汇总已结算费用与有效预留。
3. 验证 `settled + active + held_unknown + requested <= hard_limit`。
4. 创建 `active` Reservation。
5. 创建或读取 Provider Operation，并创建关联的 `prepared` Provider Attempt。
6. 提交事务。

任何一步失败都不得发起 Provider 请求。

SQLite 只有一个写者，预算写入通过专用短事务协调器串行化，避免高频 Trace 写入长期占用写锁。

## 预留金额

- Token 模型按最大输入、允许输出和价格表保守估算。
- 图片模型按可能产生的最大计费图片数和尺寸估算。
- Provider 会自动生成多张图片时，默认启用单张输出参数或预留最大可能费用。
- 估算不确定时增加配置化安全系数。

UI 同时展示预计费用、已结算费用、有效预留和未知费用。

## 结算规则

- 成功：使用实际 Usage 结算，释放剩余预留。
- 明确失败且确认未计费：释放全部预留。
- 内容安全拒绝：按 Provider 计价语义决定结算或释放。
- 用户取消：如果远端仍可能执行，转 `held_unknown`。
- `outcome_unknown`：预留转 `held_unknown`，不得立即释放。
- 实际费用超过预留：记录预算偏差事件；不得删除真实费用。后续请求会因余额不足被阻止。

## 预留过期

Reservation 不能仅因时间到期自动释放：

- `prepared` 且能够证明未提交的预留可以过期释放。
- 任何进入 `submitted` 的预留必须先执行 Provider 对账。
- 无法对账时转 `held_unknown` 并要求用户处理。

## 并发验收场景

硬预算 ¥10，同时启动 8 个预计 ¥3 的页面：

- 前 3 个事务成功预留，共 ¥9。
- 后 5 个预留失败，Page 进入正式状态 `waiting_for_budget`；同一事务创建预算 Interrupt，并将对应 Step 与 Run 转为 `waiting_for_user`。
- 任意时刻 `settled + active + held_unknown` 不超过硬预算，除非 Provider 实际费用超出其报价估算；该异常必须单独记录。

## 价格表

- 每个价格配置包含 Provider、模型、地域、生效时间、计费单位和来源。
- 价格配置版本进入 Reservation、Usage 和缓存键。
- 用户可以覆盖价格，但必须在 UI 标识为自定义。
- 无法确定价格时必须要求用户设置单次上限，不能显示虚假的精确成本。

## 测试

- 多协程同时预留不会超卖预算。
- Reservation 与 `prepared` Provider Attempt 在同一事务创建；Operation 不记录单次尝试费用。
- `outcome_unknown` 保留额度。
- 重复回调不会重复结算。
- 实际费用大于、等于和小于预留均有确定行为。
- 不同货币不会错误相加。
