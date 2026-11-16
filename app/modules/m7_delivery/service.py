"""M7 分区授权交付 · 服务编排。

职责边界：
- 批次：由实际登记与现存对象形成，摘要不变；同输入重跑复用同一批次。
- 授权：按 (批次, 供应商, 分区) 发放与收回，收回后旧下载地址立即失效。
- 外发：按授权裁剪，仅含本分区缺失对象键；越权读取一律拒绝。
- 审计：必须给理由，先留痕成功才返回完整证据；重试凭请求令牌不重复留痕/登记。
"""
from __future__ import annotations

from uuid import uuid4

from app.errors import AuthError, DomainError, NotFoundError
from . import core


def _ticket_id() -> str:
    return "tk-" + uuid4().hex


def _grant_id() -> str:
    return "gr-" + uuid4().hex


def _require(value, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DomainError("EMPTY_VALUE", f"必要值为空: {name}")
    return text


class DeliveryService:
    def __init__(self, store):
        self.store = store

    # ---- 批次 -----------------------------------------------------------
    def open_batch(self, payload: dict | None = None) -> dict:
        data = dict(core.SAMPLE)
        if payload:
            data.update({k: v for k, v in payload.items() if k in core.SAMPLE})
        manifest, objects = data["manifest"], data["objects"]
        batch = core.build_batch(manifest, objects)
        saved = self.store.save_batch(batch, manifest, objects)
        return {**batch, "digest": saved["digest"], "created_at": saved["created_at"]}

    def read_batch(self, batch_id: str) -> dict:
        """回读分区差异与授权状态；只投影状态，不改写摘要、不触发补数领取。"""
        record = self.store.get_batch(batch_id)
        if not record:
            raise NotFoundError("BATCH_NOT_FOUND", "对账批次不存在或尚未登记")
        partitions = core.group_partitions(record["diff"])
        grants = self.store.list_grants(batch_id)
        for partition in partitions.values():
            partition["grants"] = sorted(
                {"vendor": g["vendor"], "status": g["status"],
                 "revoked_at": g["revoked_at"]}
                for g in grants if g["partition"] == partition["partition"]
            )
        return {"batch_id": record["batch_id"], "digest": record["digest"],
                "created_at": record["created_at"],
                "input_count": len(record["manifest"]),
                "object_count": len(record["objects"]),
                "diff_count": len(record["diff"]),
                "missing_count": sum(r["status"] == "MISSING" for r in record["diff"]),
                "conflict_count": sum(r["status"] == "CONFLICT" for r in record["diff"]),
                "orphan_count": sum(r["status"] == "ORPHAN" for r in record["diff"]),
                "matched_count": sum(r["status"] == "MATCH" for r in record["diff"]),
                "partitions": partitions,
                "grants": grants}

    # ---- 授权与收回 -----------------------------------------------------
    def grant(self, batch_id: str, vendor: str, partition: str) -> dict:
        _require(batch_id, "batch_id")
        vendor = _require(vendor, "vendor")
        partition = _require(partition, "partition")
        if not self.store.get_batch(batch_id):
            raise NotFoundError("BATCH_NOT_FOUND", "对账批次不存在或尚未登记")
        return self.store.upsert_active_grant(
            {"grant_id": _grant_id(), "batch_id": batch_id,
             "vendor": vendor, "partition": partition})

    def revoke(self, batch_id: str, vendor: str, partition: str) -> dict:
        _require(batch_id, "batch_id")
        vendor = _require(vendor, "vendor")
        partition = _require(partition, "partition")
        grant = self.store.revoke_grant(batch_id, vendor, partition)
        if not grant:
            raise NotFoundError("GRANT_NOT_FOUND", "未找到可收回的有效授权")
        return grant

    # ---- 外发申请与下载 -------------------------------------------------
    def request_download(self, batch_id: str, vendor: str, partition: str,
                         request_token: str | None) -> dict:
        batch_id = _require(batch_id, "batch_id")
        vendor = _require(vendor, "vendor")
        partition = _require(partition, "partition")
        token = _require(request_token, "request_token")
        result = self.store.request_delivery(
            batch_id=batch_id, vendor=vendor, partition=partition,
            request_token=token, make_ticket_id=_ticket_id)
        if result.get("batch_missing"):
            raise NotFoundError("BATCH_NOT_FOUND", "对账批次不存在或尚未登记")
        entry = result["entry"]
        if not entry["allowed"]:
            # 越权读取：拒绝并留痕，不返回任何对象键。
            raise AuthError("GRANT_REQUIRED", "该分区未授权或授权已收回")
        return {"request_token": token, "batch_id": batch_id, "vendor": vendor,
                "partition": partition, "allowed": True,
                "replayed": result["replayed"],
                "ticket_id": entry["ticket_id"],
                "download_url": f"/api/delivery/tickets/{entry['ticket_id']}/download"}

    def redeem(self, ticket_id: str) -> dict:
        ticket_id = _require(ticket_id, "ticket_id")
        ticket = self.store.redeem_ticket(ticket_id)
        if not ticket:
            raise NotFoundError("TICKET_NOT_FOUND", "下载地址不存在")
        if not ticket["active"]:
            # 收回优先：授权收回后旧地址立即失效，不返回原件。
            raise AuthError("GRANT_REVOKED", "授权已收回，下载地址失效")
        record = self.store.get_batch(ticket["batch_id"])
        rows = core.project_partition(record["diff"], ticket["partition"])
        return {"ticket_id": ticket_id, "batch_id": ticket["batch_id"],
                "vendor": ticket["vendor"], "partition": ticket["partition"],
                "rows": rows, "row_count": len(rows)}

    # ---- 内部审计 -------------------------------------------------------
    def audit_evidence(self, batch_id: str, auditor: str, reason: str,
                       request_token: str | None) -> dict:
        batch_id = _require(batch_id, "batch_id")
        auditor = _require(auditor, "auditor")
        reason = _require(reason, "reason")
        token = _require(request_token, "request_token")
        record = self.store.get_batch(batch_id)
        if not record:
            raise NotFoundError("BATCH_NOT_FOUND", "对账批次不存在或尚未登记")
        existing = self.store.get_audit(token)
        if existing:  # 重试回放，不重复留痕。
            log = existing
        else:
            # 先落审计留痕；该事务失败会抛错，调用方拿不到下面的完整原件。
            log = self.store.write_audit(
                {"request_token": token, "batch_id": batch_id,
                 "auditor": auditor, "reason": reason})
        partitions = core.group_partitions(record["diff"])
        return {"request_token": token, "audit": log,
                "batch_id": batch_id, "digest": record["digest"],
                "partitions": partitions, "diff": record["diff"]}
