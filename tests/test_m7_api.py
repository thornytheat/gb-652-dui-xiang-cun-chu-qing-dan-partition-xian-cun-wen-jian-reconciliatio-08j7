def _open(client):
    return client.post("/api/delivery/batches", json={}).json()


def test_delivery_page_renders(client):
    response = client.get("/delivery")
    assert response.status_code == 200
    assert "panel" in response.text


def test_full_delivery_lifecycle_and_trim(client):
    batch = _open(client)
    bid, partition = batch["batch_id"], "dt=2026-11-15/hour=00"
    grant = client.post("/api/delivery/grants",
                        json={"batch_id": bid, "vendor": "vendor-acme", "partition": partition})
    assert grant.status_code == 200 and grant.json()["status"] == "ACTIVE"

    denied = client.post("/api/delivery/download-requests",
                         json={"batch_id": bid, "vendor": "vendor-acme",
                               "partition": "dt=2026-11-15/hour=01",
                               "request_token": "tok-api-deny"})
    assert denied.status_code == 403 and denied.json()["error"] == "GRANT_REQUIRED"

    ticket = client.post("/api/delivery/download-requests",
                         json={"batch_id": bid, "vendor": "vendor-acme",
                               "partition": partition, "request_token": "tok-api-1"})
    assert ticket.status_code == 200
    ticket_id = ticket.json()["ticket_id"]

    payload = client.get(f"/api/delivery/tickets/{ticket_id}")
    assert payload.status_code == 200
    rows = payload.json()["rows"]
    assert [r["key"] for r in rows] == ["dt=2026-11-15/hour=00/b.parquet"]

    csv_resp = client.get(f"/api/delivery/tickets/{ticket_id}/download")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "dt=2026-11-15/hour=00/b.parquet" in csv_resp.text
    assert "orphan" not in csv_resp.text


def test_revoked_ticket_returns_403_on_old_url(client):
    batch = _open(client)
    bid, partition = batch["batch_id"], "dt=2026-11-15/hour=00"
    client.post("/api/delivery/grants",
                json={"batch_id": bid, "vendor": "vendor-acme", "partition": partition})
    ticket_id = client.post("/api/delivery/download-requests",
                            json={"batch_id": bid, "vendor": "vendor-acme",
                                  "partition": partition,
                                  "request_token": "tok-api-rev"}).json()["ticket_id"]
    revoked = client.post("/api/delivery/grants/revoke",
                          json={"batch_id": bid, "vendor": "vendor-acme", "partition": partition})
    assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"
    assert client.get(f"/api/delivery/tickets/{ticket_id}").status_code == 403
    assert client.get(f"/api/delivery/tickets/{ticket_id}/download").status_code == 403


def test_audit_gate_and_full_evidence(client):
    batch = _open(client)
    bid = batch["batch_id"]
    missing_reason = client.post("/api/delivery/audit",
                                 json={"batch_id": bid, "auditor": "a1",
                                       "reason": "", "request_token": "tok-api-a0"})
    assert missing_reason.status_code == 422
    ok = client.post("/api/delivery/audit",
                     json={"batch_id": bid, "auditor": "a1",
                           "reason": "责任核查", "request_token": "tok-api-a1"})
    assert ok.status_code == 200
    body = ok.json()
    # 内部审计可见完整跨供应商差异，包括别家分区键与孤儿/冲突
    assert any(r["status"] == "ORPHAN" for r in body["diff"])
    assert any(r["status"] == "CONFLICT" for r in body["diff"])


def test_idempotent_retry_no_duplicate_access_logs(client):
    batch = _open(client)
    bid, partition = batch["batch_id"], "dt=2026-11-15/hour=00"
    client.post("/api/delivery/grants",
                json={"batch_id": bid, "vendor": "vendor-acme", "partition": partition})
    payload = {"batch_id": bid, "vendor": "vendor-acme", "partition": partition,
               "request_token": "tok-api-idem"}
    first = client.post("/api/delivery/download-requests", json=payload).json()
    second = client.post("/api/delivery/download-requests", json=payload).json()
    assert first["ticket_id"] == second["ticket_id"]
    logs = client.get(f"/api/delivery/access-logs?batch_id={bid}").json()
    assert len([x for x in logs["access"] if x["request_token"] == "tok-api-idem"]) == 1
    assert len(logs["audits"]) == 0 or all(a["batch_id"] == bid for a in logs["audits"])


def test_read_back_partition_diff_and_grant_status(client):
    batch = _open(client)
    bid = batch["batch_id"]
    client.post("/api/delivery/grants",
                json={"batch_id": bid, "vendor": "vendor-acme",
                      "partition": "dt=2026-11-15/hour=00"})
    view = client.get(f"/api/delivery/batches/{bid}")
    assert view.status_code == 200
    body = view.json()
    assert body["digest"] == batch["digest"]
    assert body["partitions"]["dt=2026-11-15/hour=00"]["grants"][0]["vendor"] == "vendor-acme"


def test_unknown_batch_and_ticket_404(client):
    assert client.get("/api/delivery/batches/run-missing").status_code == 404
    assert client.get("/api/delivery/tickets/tk-missing").status_code == 404
