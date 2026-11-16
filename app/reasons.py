REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    "GRANT_REQUIRED": "分区未授权或授权已收回",
    "GRANT_REVOKED": "授权已收回，旧下载地址失效",
    "AUDIT_REASON_REQUIRED": "内部审计必须登记查阅理由",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")
