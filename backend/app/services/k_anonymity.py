"""K匿名检查领域服务。

除原有的检查状态流转外，承载「样本保留复核 / 样本清理 / 样本下载」工作流：
到期样本批次与假名化交付引用的跨页一致性由 retention_flow 统一裁定。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.retention_flow import (
    CLEANUP_ACTIONS,
    DOWNLOAD_ACTIONS,
    K_FLOW_ACTIONS,
    REVIEW_ACTIONS,
    flow,
)
from app.store import store


class KAnonymityService(BaseModuleService):
    MODULE = "k_anonymity"
    ENTITY = "匿名检查"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["检查编号", "数据集名称", "准标识符列", "K值要求", "等价类数量", "最小等价类大小", "匿名化措施", "检查结论"]
    REQUIRED_FIELDS = ["检查编号", "数据集名称", "准标识符列"]
    STATUS_ORDER = ["待检查", "已通过", "未通过", "已处理", "已到期", "保留中", "待清理", "已清理"]
    ACTION_RULES = {"发起检查": "已通过", "标记通过": "未通过", "标记未通过": "已处理"}
    NEGATIVE_ACTIONS = []

    def list_entries(self, **kwargs: Any):  # type: ignore[override]
        rows, total = super().list_entries(**kwargs)
        for row in rows:
            flow.decorate_k(row)
        return rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = super().get_entry(entry_id)
        if entry is not None:
            flow.decorate_k(entry)
        return entry

    def create_entry(self, values: dict[str, Any]):
        entry, missing = super().create_entry(values)
        if entry is not None:
            # 允许调用方直接用「保留期限」指定到期日，驱动到期/未到期判定。
            retention_until = values.get("保留期限") or values.get("retention_until")
            batch = flow.ensure_batch(int(entry["id"]), entry)
            # 从 K 匿名检查页新登记的行直接按样本批次纳管。
            batch["explicit"] = True
            if retention_until:
                from app.services.retention_flow import _parse_date
                batch["retention_until"] = _parse_date(retention_until).isoformat()
            flow.decorate_k(entry)
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action in K_FLOW_ACTIONS:
            result = self.dispatch_action(entry_id, action)
            if result["ok"]:
                return result.get("entry"), result["message"]
            # 与基类契约保持一致：失败返回 (None, message)。
            return None, result["message"]
        return super().run_action(entry_id, action)

    def dispatch_action(
        self, entry_id: int, action: str, payload: Any | None = None
    ) -> dict[str, Any]:
        if action not in K_FLOW_ACTIONS:
            return super().dispatch_action(entry_id, action, payload)
        remark = getattr(payload, "remark", None)
        if action in REVIEW_ACTIONS:
            return flow.review(entry_id, remark)
        if action in CLEANUP_ACTIONS:
            return flow.cleanup(entry_id, remark)
        if action in DOWNLOAD_ACTIONS:
            allowed, reason = flow.download_gate(entry_id)
            if not allowed:
                flow.log_download(entry_id, False, reason)
                row = store.find(self.MODULE, entry_id)
                return {
                    "ok": False, "message": reason, "conflict": False,
                    "entry": flow.decorate_k(row) if row else None,
                    "next_actions": ["保留复核", "样本清理"],
                }
            flow.log_download(entry_id, True)
            return {
                "ok": True,
                "message": f"样本批次 {entry_id} 允许下载，已生成样本文件。",
                "download_url": f"/api/k_anonymity/{entry_id}/download-sample",
                "entry": self.get_entry(entry_id),
                "next_actions": ["保留复核"],
            }
        return super().dispatch_action(entry_id, action, payload)
