"""假名化领域服务。

在原有假名映射流转之外，承载「假名化交付引用」的续期、继续交付与暂停交付，
并依据 K 匿名检查页出具的《样本保留回执》决定未完成交付的去留。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.retention_flow import (
    PAUSE_ACTIONS,
    P_FLOW_ACTIONS,
    RENEW_ACTIONS,
    RESUME_ACTIONS,
    flow,
)


class PseudonymService(BaseModuleService):
    MODULE = "pseudonym"
    ENTITY = "假名映射"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["映射编号", "原始标识", "假名标识", "映射算法", "可逆标记", "盐值哈希", "创建时间", "失效时间"]
    REQUIRED_FIELDS = ["映射编号", "原始标识", "假名标识"]
    STATUS_ORDER = ["有效", "已失效", "已轮换", "交付中", "已暂停", "已完成"]
    ACTION_RULES = {"标记失效": "已失效", "轮换映射": "已轮换"}
    NEGATIVE_ACTIONS = []

    def list_entries(self, **kwargs: Any):  # type: ignore[override]
        rows, total = super().list_entries(**kwargs)
        for row in rows:
            flow.decorate_p(row)
        return rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = super().get_entry(entry_id)
        if entry is not None:
            flow.decorate_p(entry)
        return entry

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action in P_FLOW_ACTIONS:
            result = self.dispatch_action(entry_id, action)
            if result["ok"]:
                return result.get("entry"), result["message"]
            return None, result["message"]
        return super().run_action(entry_id, action)

    def dispatch_action(
        self, entry_id: int, action: str, payload: Any | None = None
    ) -> dict[str, Any]:
        if action not in P_FLOW_ACTIONS:
            return super().dispatch_action(entry_id, action, payload)
        if action in RENEW_ACTIONS:
            days = getattr(payload, "days", None)
            return flow.renew(entry_id, days)
        if action in RESUME_ACTIONS:
            return flow.resume(entry_id)
        if action in PAUSE_ACTIONS:
            return flow.pause(entry_id)
        return super().dispatch_action(entry_id, action, payload)
