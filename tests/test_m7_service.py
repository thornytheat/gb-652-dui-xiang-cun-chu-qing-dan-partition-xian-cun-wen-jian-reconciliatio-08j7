import sqlite3

import pytest

from app.errors import AuthError, DomainError, NotFoundError
from app.modules.m7_delivery.core import ROOT_PARTITION, SAMPLE
from app.modules.m7_delivery.service import DeliveryService
from app.store import Store


@pytest.fixture
def service(tmp_path):
    return DeliveryService(Store(str(tmp_path / "m7.db")))


@pytest.fixture
def batch(service):
    return service.open_batch(SAMPLE)


def grant_h00(service, batch):
    return service.grant(batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00")


def test_open_batch_is_idempotent_and_persists(service, batch):
    again = service.open_batch(SAMPLE)
    assert again["batch_id"] == batch["batch_id"]
    assert service.store.get_batch(batch["batch_id"])["digest"] == batch["digest"]


def test_read_batch_returns_partition_diff_and_grant_status(service, batch):
    grant_h00(service, batch)
    view = service.read_batch(batch["batch_id"])
    assert "dt=2026-11-15/hour=00" in view["partitions"]
    assert view["partitions"]["dt=2026-11-15/hour=00"]["grants"][0]["vendor"] == "vendor-acme"
    assert view["partitions"]["dt=2026-11-15/hour=00"]["grants"][0]["status"] == "ACTIVE"
    # 回读不改摘要
    assert view["digest"] == batch["digest"]


def test_read_unknown_batch_404(service):
    with pytest.raises(NotFoundError):
        service.read_batch("run-nope")


def test_grant_unknown_batch_404(service):
    with pytest.raises(NotFoundError):
        service.grant("run-nope", "v", "p")


def test_grant_requires_values(service, batch):
    with pytest.raises(DomainError):
        service.grant(batch["batch_id"], "", "p")


def test_repeated_grant_keeps_single_active_row(service, batch):
    first = grant_h00(service, batch)
    second = grant_h00(service, batch)
    assert first["grant_id"] == second["grant_id"]
    grants = service.store.list_grants(batch["batch_id"])
    assert len([g for g in grants if g["status"] == "ACTIVE"]) == 1


def test_download_without_grant_is_denied_and_logged(service, batch):
    with pytest.raises(AuthError):
        service.request_download(batch["batch_id"], "vendor-acme",
                                 "dt=2026-11-15/hour=00", "tok-deny-1")
    logs = service.store.list_access(batch["batch_id"])
    assert len(logs) == 1 and logs[0]["allowed"] is False
    assert logs[0]["detail"]["reason"] == "GRANT_REQUIRED"


def test_cross_partition_read_denied_even_with_other_grant(service, batch):
    grant_h00(service, batch)  # 只授权 hour=00
    with pytest.raises(AuthError):
        service.request_download(batch["batch_id"], "vendor-acme",
                                 ROOT_PARTITION, "tok-cross-1")
    with pytest.raises(AuthError):
        service.request_download(batch["batch_id"], "vendor-other",
                                 "dt=2026-11-15/hour=00", "tok-cross-2")


def test_authorized_download_is_trimmed_to_partition_missing(service, batch):
    grant_h00(service, batch)
    ticket = service.request_download(
        batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00", "tok-ok-1")
    assert ticket["download_url"].endswith("/download")
    redeemed = service.redeem(ticket["ticket_id"])
    assert [r["key"] for r in redeemed["rows"]] == [
        "dt=2026-11-15/hour=00/b.parquet"]
    # 裁剪结果不含冲突/孤儿，也不含别的分区键
    all_keys = [r["key"] for r in redeemed["rows"]]
    assert "dt=2026-11-15/hour=01/c.parquet" not in all_keys
    assert "dt=2026-11-15/hour=03/orphan.parquet" not in all_keys
    assert "loose/root.parquet" not in all_keys


def test_retry_same_token_does_not_duplicate_access_or_ticket(service, batch):
    grant_h00(service, batch)
    first = service.request_download(
        batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00", "tok-retry")
    second = service.request_download(
        batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00", "tok-retry")
    assert second["replayed"] is True
    assert first["ticket_id"] == second["ticket_id"]
    logs = service.store.list_access(batch["batch_id"])
    assert len(logs) == 1
    tickets = service.store.list_access(batch["batch_id"])
    assert len([t for t in tickets if t["ticket_id"]]) == 1


def test_denied_retry_same_token_stays_single_log(service, batch):
    for _ in range(3):
        with pytest.raises(AuthError):
            service.request_download(batch["batch_id"], "vendor-acme",
                                     "dt=2026-11-15/hour=00", "tok-deny-retry")
    assert len(service.store.list_access(batch["batch_id"])) == 1


def test_revocation_invalidates_old_download_url(service, batch):
    grant_h00(service, batch)
    ticket = service.request_download(
        batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00", "tok-rev-1")
    service.revoke(batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00")
    with pytest.raises(AuthError):
        service.redeem(ticket["ticket_id"])
    # 收回后再次申请同样被拒
    with pytest.raises(AuthError):
        service.request_download(batch["batch_id"], "vendor-acme",
                                 "dt=2026-11-15/hour=00", "tok-rev-2")


def test_revocation_and_read_concurrent_revoke_wins(service, batch):
    """并发交错：读取开始等待写锁时，收回事务提交；读取按收回后的状态裁决。"""
    import threading
    import time

    grant_h00(service, batch)
    ticket = service.request_download(
        batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00", "tok-cc-1")
    lock_held = threading.Event()

    def revoke_in_flight():
        db = service.store._tx()
        db.execute(
            "UPDATE delivery_grants SET status='REVOKED', revoked_at=?"
            " WHERE batch_id=? AND vendor=? AND partition=? AND status='ACTIVE'",
            ("2026-11-16T00:00:00+00:00", batch["batch_id"],
             "vendor-acme", "dt=2026-11-15/hour=00"))
        lock_held.set()
        time.sleep(0.5)  # 让并发读取先进入等锁，再提交收回
        db.commit()

    worker = threading.Thread(target=revoke_in_flight)
    worker.start()
    lock_held.wait(2)
    with pytest.raises(AuthError):  # 等锁 → 收回已提交 → 旧地址失效
        service.redeem(ticket["ticket_id"])
    worker.join()
    with pytest.raises(AuthError):
        service.redeem(ticket["ticket_id"])


def test_revoke_unknown_grant_404(service, batch):
    with pytest.raises(NotFoundError):
        service.revoke(batch["batch_id"], "vendor-acme", "dt=1999-01-01/hour=00")


def test_regrant_after_revoke_issues_new_grant_and_old_ticket_stays_dead(service, batch):
    grant_h00(service, batch)
    old = service.request_download(
        batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00", "tok-rg-1")
    service.revoke(batch["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00")
    new_grant = grant_h00(service, batch)
    assert new_grant["status"] == "ACTIVE"
    with pytest.raises(AuthError):
        service.redeem(old["ticket_id"])  # 旧地址不复活


def test_audit_requires_reason(service, batch):
    with pytest.raises(DomainError):
        service.audit_evidence(batch["batch_id"], "auditor-1", "  ", "tok-a-1")


def test_audit_returns_full_evidence_after_logging(service, batch):
    out = service.audit_evidence(
        batch["batch_id"], "auditor-1", "责任核查", "tok-a-1")
    statuses = {r["key"]: r["status"] for r in out["diff"]}
    assert "dt=2026-11-15/hour=03/orphan.parquet" in statuses  # 完整跨供应商差异
    assert len(service.store.list_audits(batch["batch_id"])) == 1


def test_audit_retry_token_not_duplicated(service, batch):
    first = service.audit_evidence(batch["batch_id"], "auditor-1", "责任核查", "tok-a-2")
    second = service.audit_evidence(batch["batch_id"], "auditor-1", "责任核查", "tok-a-2")
    assert first["request_token"] == second["request_token"]
    assert len(service.store.list_audits(batch["batch_id"])) == 1


def test_audit_failure_returns_no_evidence(service, batch, monkeypatch):
    def boom(entry):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(service.store, "write_audit", boom)
    with pytest.raises(sqlite3.OperationalError):
        service.audit_evidence(batch["batch_id"], "auditor-1", "责任核查", "tok-a-fail")
    assert service.store.list_audits(batch["batch_id"]) == []


def test_restart_recovers_batches_grants_tickets_and_logs(tmp_path, batch):
    db_path = str(tmp_path / "m7-restart.db")
    service = DeliveryService(Store(db_path))
    opened = service.open_batch(SAMPLE)
    service.grant(opened["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00")
    ticket = service.request_download(
        opened["batch_id"], "vendor-acme", "dt=2026-11-15/hour=00", "tok-persist")
    service.audit_evidence(opened["batch_id"], "auditor-1", "责任核查", "tok-audit-persist")
    # 模拟中断重启：新 Store 指向同一 SQLite 文件
    restarted = DeliveryService(Store(db_path))
    view = restarted.read_batch(opened["batch_id"])
    assert view["digest"] == opened["digest"]
    assert view["partitions"]["dt=2026-11-15/hour=00"]["grants"][0]["status"] == "ACTIVE"
    redeemed = restarted.redeem(ticket["ticket_id"])
    assert redeemed["row_count"] == 1
    assert len(restarted.store.list_audits(opened["batch_id"])) == 1
