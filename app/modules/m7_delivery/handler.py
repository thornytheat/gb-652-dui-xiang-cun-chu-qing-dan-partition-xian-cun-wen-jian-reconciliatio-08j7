import csv
import io

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response

from .service import DeliveryService

router = APIRouter(tags=["M7-分区授权交付"])


def _service(request: Request) -> DeliveryService:
    return DeliveryService(request.app.state.store)


@router.get("/delivery")
def page(request: Request):
    data = _service(request).open_batch(None)
    return request.app.state.templates.TemplateResponse(
        request, "m7_delivery.html", {"title": "分区授权交付", "data": data})


@router.post("/api/delivery/batches")
def open_batch(request: Request, payload: dict = Body(default={})):
    return _service(request).open_batch(payload or None)


@router.get("/api/delivery/batches/{batch_id}")
def read_batch(request: Request, batch_id: str):
    return _service(request).read_batch(batch_id)


@router.post("/api/delivery/grants")
def grant(request: Request, payload: dict = Body(...)):
    return _service(request).grant(
        payload.get("batch_id", ""), payload.get("vendor", ""),
        payload.get("partition", ""))


@router.post("/api/delivery/grants/revoke")
def revoke(request: Request, payload: dict = Body(...)):
    return _service(request).revoke(
        payload.get("batch_id", ""), payload.get("vendor", ""),
        payload.get("partition", ""))


@router.post("/api/delivery/download-requests")
def request_download(request: Request, payload: dict = Body(...)):
    return _service(request).request_download(
        payload.get("batch_id", ""), payload.get("vendor", ""),
        payload.get("partition", ""), payload.get("request_token"))


@router.get("/api/delivery/tickets/{ticket_id}")
def redeem_ticket(request: Request, ticket_id: str):
    return _service(request).redeem(ticket_id)


@router.get("/api/delivery/tickets/{ticket_id}/download")
def download_ticket(request: Request, ticket_id: str):
    data = _service(request).redeem(ticket_id)
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=["key", "partition", "status", "evidence", "size", "etag"],
        lineterminator="\n")
    writer.writeheader()
    writer.writerows(data["rows"])
    filename = f"replenish-{data['batch_id']}-{ticket_id}.csv"
    return Response(
        buffer.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.post("/api/delivery/audit")
def audit(request: Request, payload: dict = Body(...)):
    return _service(request).audit_evidence(
        payload.get("batch_id", ""), payload.get("auditor", ""),
        payload.get("reason", ""), payload.get("request_token"))


@router.get("/api/delivery/access-logs")
def access_logs(request: Request, batch_id: str | None = None):
    return {"access": request.app.state.store.list_access(batch_id),
            "audits": request.app.state.store.list_audits(batch_id)}
