# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。

## M7 分区授权交付

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `batch_id` | string | 否 | 由登记+现存输入摘要派生的稳定批次编号，重跑复用 |
| `partition` | string | 否 | 对象键分区段拼接的分区身份，无分区段为 `_ROOT` |
| `vendor` | string | 否 | 供应商标识，授权与外发的主体 |
| `grant_id` / `status` | string | 否 | 授权编号与状态：`ACTIVE` / `REVOKED` |
| `request_token` | string | 否 | 客户端幂等令牌，唯一约束保证重试不重复登记/留痕 |
| `ticket_id` | string | 是 | 授权后签发的下载凭据编号，授权收回即失效 |
| `diff.status` | string | 否 | 完整差异裁决：`MATCH` / `MISSING` / `ORPHAN` / `CONFLICT` |
| `audit.reason` | string | 否 | 内部审计查阅理由，为空拒绝且不返回原件 |

外发裁剪只输出授权分区的 `MISSING` 行；`ORPHAN`、`CONFLICT` 与其他分区对象键仅出现在审计完整证据中。
