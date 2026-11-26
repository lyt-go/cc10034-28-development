"""保留复核 / 引用续期 / 样本清理 / 下载 的专用 API。

通用动作接口 POST /api/{module}/{id}/actions 已覆盖同一套业务能力；
本路由额外提供：
- 台账查询：批次、交付引用、样本保留回执、事件轨迹（既往结论回查）；
- 样本文件下载与下载阻断；
- 语义化别名路径，方便不同调用方接入；
- /api/reset 测试/演示重置钩子。
必须先于通用 /api/{module}/{entry_id} 资源路由注册。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app.schemas import ActionPayload
from app.services.retention_flow import flow
from app.store import store

router = APIRouter(prefix="/api", tags=["保留复核与交付引用"])


def _blocked(message: str) -> JSONResponse:
    # 403：保留策略禁止下载；响应体同时给出 detail/message，便于各类客户端读取。
    return JSONResponse(
        status_code=403,
        content={
            "ok": False,
            "code": "RETENTION_DOWNLOAD_BLOCKED",
            "detail": message,
            "message": message,
        },
    )


# ————————————————————————————————————————————
# 台账查询
# ————————————————————————————————————————————
@router.get("/retention/batches")
def retention_batches() -> dict[str, Any]:
    items = flow.list_batches()
    return {"items": items, "total": len(items)}


@router.get("/retention/batches/{batch_id}")
def retention_batch_detail(batch_id: int) -> dict[str, Any]:
    row = store.find("k_anonymity", batch_id)
    if row is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": f"样本批次 {batch_id} 不存在"})
    batch = flow.decorate_k(row)
    return {
        "batch": batch,
        "references": flow.refs_for_batch(batch_id),
        "receipts": flow.list_receipts(batch_id),
        "events": flow.list_events(k_id=batch_id),
    }


@router.get("/retention/references")
def retention_references() -> dict[str, Any]:
    items = flow.list_references()
    return {"items": items, "total": len(items)}


@router.get("/retention/receipts")
def retention_receipts(k_id: int | None = None) -> dict[str, Any]:
    items = flow.list_receipts(k_id)
    return {"items": items, "total": len(items)}


@router.get("/retention/receipts/{receipt_no}")
def retention_receipt_detail(receipt_no: str) -> dict[str, Any]:
    for receipt in flow.receipts:
        if receipt.get("回执编号") == receipt_no or receipt.get("receipt_no") == receipt_no:
            return receipt
    return JSONResponse(status_code=404, content={"ok": False, "message": f"回执 {receipt_no} 不存在"})


@router.get("/retention/events")
def retention_events(k_id: int | None = None, p_id: int | None = None) -> dict[str, Any]:
    items = flow.list_events(k_id=k_id, p_id=p_id)
    return {"items": items, "total": len(items)}


@router.post("/retention/reset")
def retention_reset() -> dict[str, Any]:
    store.reset()
    flow.reset()
    return {"ok": True, "message": "数据已恢复为包含一个已到期批次和一个未到期批次的初始场景"}


@router.post("/reset")
def global_reset() -> dict[str, Any]:
    store.reset()
    flow.reset()
    return {"ok": True, "message": "平台演示数据与保留复核台账均已重置"}


# 模块语义化别名：回执 / 轨迹
@router.get("/k_anonymity/receipts")
def k_receipts() -> dict[str, Any]:
    return retention_receipts()


@router.get("/k_anonymity/events")
def k_events() -> dict[str, Any]:
    return retention_events()


@router.get("/pseudonym/receipts")
def p_receipts() -> dict[str, Any]:
    return retention_receipts()


@router.get("/pseudonym/events")
def p_events() -> dict[str, Any]:
    return retention_events()


# ————————————————————————————————————————————
# 样本下载
# ————————————————————————————————————————————
def _download_sample(entry_id: int):
    allowed, reason = flow.download_gate(entry_id)
    if not allowed:
        flow.log_download(entry_id, False, reason)
        return _blocked(reason)
    content, filename = flow.build_sample_file(entry_id)
    flow.log_download(entry_id, True)
    encoded = filename  # 文件名主体为 ASCII 批次号，兼容 filename 直接回传
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{encoded}\"; filename*=UTF-8''{encoded}"
            )
        },
    )


@router.get("/k_anonymity/{entry_id}/download-sample")
def download_k_sample(entry_id: int):
    return _download_sample(entry_id)


@router.get("/k_anonymity/{entry_id}/download")
def download_k_sample_alias(entry_id: int):
    return _download_sample(entry_id)


@router.get("/retention/batches/{batch_id}/download")
def download_batch(batch_id: int):
    return _download_sample(batch_id)


# ————————————————————————————————————————————
# 语义化动作别名（与通用 actions 接口等价）
# ————————————————————————————————————————————
def _action_result(result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=200 if result.get("ok") else 200, content=result)


@router.post("/k_anonymity/{entry_id}/review")
def review_alias(entry_id: int, payload: ActionPayload | None = None):
    result = flow.review(entry_id, getattr(payload, "remark", None))
    return _action_result(result)


@router.post("/k_anonymity/{entry_id}/cleanup")
def cleanup_alias(entry_id: int, payload: ActionPayload | None = None):
    result = flow.cleanup(entry_id, getattr(payload, "remark", None))
    return _action_result(result)


@router.post("/retention/batches/{batch_id}/review")
def review_batch(batch_id: int, payload: ActionPayload | None = None):
    result = flow.review(batch_id, getattr(payload, "remark", None))
    return _action_result(result)


@router.post("/retention/batches/{batch_id}/cleanup")
def cleanup_batch(batch_id: int, payload: ActionPayload | None = None):
    result = flow.cleanup(batch_id, getattr(payload, "remark", None))
    return _action_result(result)


@router.post("/pseudonym/{entry_id}/renew")
def renew_alias(entry_id: int, payload: ActionPayload | None = None):
    days = getattr(payload, "days", None)
    return _action_result(flow.renew(entry_id, days))


@router.post("/pseudonym/{entry_id}/resume")
def resume_alias(entry_id: int):
    return _action_result(flow.resume(entry_id))


@router.post("/pseudonym/{entry_id}/pause")
def pause_alias(entry_id: int):
    return _action_result(flow.pause(entry_id))


@router.post("/retention/references/{ref_id}/renew")
def renew_ref(ref_id: int, payload: ActionPayload | None = None):
    return _action_result(flow.renew(ref_id, getattr(payload, "days", None)))


@router.post("/retention/references/{ref_id}/resume")
def resume_ref(ref_id: int):
    return _action_result(flow.resume(ref_id))


@router.post("/retention/references/{ref_id}/pause")
def pause_ref(ref_id: int):
    return _action_result(flow.pause(ref_id))
