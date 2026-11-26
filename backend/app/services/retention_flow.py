"""样本保留复核与假名化交付引用的跨页工作流。

业务主线（题目约定）：
1. 管理员在 K 匿名检查页对「将到期/已到期样本批次」提交 **保留复核**；
2. 服务端校验与批次关联的 **假名化交付引用** 是否有效，出具 **样本保留回执**；
3. 假名化页依据回执 **继续 / 暂停** 未完成的交付；
4. 超过保留期限且无有效引用的样本禁止下载；已清理样本即便引用仍在也不得继续交付；
5. 「样本清理」与「引用续期」按提交顺序形成状态争用，失败的一方返回冲突结果；
6. 所有结论、回执、冲突与本次继续处理所依据的 **有效依据** 均追加留痕，可回查。

状态只保存在内存仓库之外的独立台账中，避免给 store 增加模块名而改变 /api/health
的模块计数；进程重启或调用 reset 后恢复种子场景。
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any

from app.seed import RETENTION_DATES
from app.store import store

REVIEW_ACTIONS = ("保留复核", "提交保留复核", "提交复核", "retention-review", "review")
CLEANUP_ACTIONS = ("样本清理", "清理样本", "清理批次", "批次清理", "删除样本", "清理", "cleanup")
RENEW_ACTIONS = ("引用续期", "续期引用", "办理续期", "续期", "renew")
RESUME_ACTIONS = ("继续交付", "恢复交付", "继续", "resume")
PAUSE_ACTIONS = ("暂停交付", "暂停", "pause")
DOWNLOAD_ACTION = "下载样本"
DOWNLOAD_ACTIONS = ("下载样本", "样本下载", "下载", "download")

K_FLOW_ACTIONS = set(REVIEW_ACTIONS) | set(CLEANUP_ACTIONS) | set(DOWNLOAD_ACTIONS)
P_FLOW_ACTIONS = set(RENEW_ACTIONS) | set(RESUME_ACTIONS) | set(PAUSE_ACTIONS)

# 两个规范批次：id 与 k_anonymity 种子行一致，任意时间启动都满足到期/未到期各一批。
_BATCH_SEED: dict[int, dict[str, str]] = {
    4: {"batch_no": "BATCH-EXP-01", "retention_until": RETENTION_DATES["batch_expired_until"]},
    5: {"batch_no": "BATCH-NEW-01", "retention_until": RETENTION_DATES["batch_active_until"]},
}
_REF_SEED: dict[int, dict[str, Any]] = {
    4: {
        "ref_no": "PREF-EXP-01", "k_id": 4,
        "valid_until": RETENTION_DATES["ref_expired_until"],
        "delivery_state": "已暂停",
    },
    5: {
        "ref_no": "PREF-NEW-01", "k_id": 5,
        "valid_until": RETENTION_DATES["ref_active_until"],
        "delivery_state": "交付中",
    },
}

OPERATOR = "管理员（数据安全组）"


def _today() -> date:
    return date.today()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_date(value: Any, default: date | None = None) -> date:
    text = str(value or "").strip()
    if len(text) >= 10:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    return default or (_today() + timedelta(days=30))


class RetentionFlow:
    """保存批次状态、引用状态、回执与只增事件台账的单例服务。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.batches: dict[int, dict[str, Any]] = {}
        self.refs: dict[int, dict[str, Any]] = {}
        self.receipts: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._event_seq = 0
        self._receipt_seq = 0

    # ————————————————————————————————————————————
    # 初始化 / 重置
    # ————————————————————————————————————————————
    def reset(self) -> None:
        with self._lock:
            self.batches.clear()
            self.refs.clear()
            self.receipts.clear()
            self.events.clear()
            self._event_seq = 0
            self._receipt_seq = 0

    def ensure_batch(self, k_id: int, row: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = self.batches.get(k_id)
        if existing is not None:
            return existing
        spec = _BATCH_SEED.get(k_id)
        if spec is None:
            retention_until = _parse_date(
                (row or {}).get("保留期限") or (row or {}).get("retention_until"),
                _today() + timedelta(days=30),
            ).isoformat()
            batch_no = f"BATCH-K{k_id:02d}"
        else:
            batch_no = spec["batch_no"]
            retention_until = spec["retention_until"]
        state: dict[str, Any] = {
            "k_id": k_id,
            "batch_no": batch_no,
            "retention_until": retention_until,
            "cleaned": False,
            "cleaned_at": None,
            "cleanup_basis": "",
            "last_receipt_no": None,
            "last_decision": "",
            "basis": "",
            # 只有规范的两批种子和被显式办理过的行才算「样本批次」，
            # 避免把原本的匿名检查记录误展示成到期批次。
            "explicit": k_id in _BATCH_SEED,
        }
        self.batches[k_id] = state
        return state

    def ensure_ref(self, p_id: int, row: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = self.refs.get(p_id)
        if existing is not None:
            return existing
        seed = _REF_SEED.get(p_id)
        if seed is not None:
            state = {
                "p_id": p_id,
                "ref_no": seed["ref_no"],
                "k_id": seed["k_id"],
                "valid_until": seed["valid_until"],
                "original_valid_until": seed["valid_until"],
                "delivery_state": seed["delivery_state"],
                "renew_count": 0,
                "basis": "",
                "receipt_no": None,
                "explicit": True,
            }
        else:
            valid_until = _parse_date((row or {}).get("失效时间"), _today() + timedelta(days=180))
            status = str((row or {}).get("status") or "")
            if status == "已失效":
                delivery_state = "已暂停"
            elif status == "已轮换":
                delivery_state = "已完成"
            else:
                delivery_state = "交付中"
            state = {
                "p_id": p_id,
                "ref_no": f"PREF-M{p_id:02d}",
                "k_id": p_id,  # 同名 id 的检查行即其样本批次，保证任意种子行可走完整流程
                "valid_until": valid_until.isoformat(),
                "original_valid_until": valid_until.isoformat(),
                "delivery_state": delivery_state,
                "renew_count": 0,
                "basis": "",
                "receipt_no": None,
                "explicit": False,
            }
        self.refs[p_id] = state
        return state

    # ————————————————————————————————————————————
    # 查询 / 派生判定
    # ————————————————————————————————————————————
    def ref_is_valid(self, ref: dict[str, Any]) -> bool:
        if ref.get("delivery_state") == "已终止":
            return False
        return _parse_date(ref.get("valid_until")) >= _today()

    def refs_for_batch(self, k_id: int, *, valid_only: bool = False) -> list[dict[str, Any]]:
        result = [ref for ref in self.refs.values() if ref.get("k_id") == k_id]
        # 尚未装饰过的行也要纳入（惰性初始化全部假名映射行）
        for row in store.rows("pseudonym"):
            p_id = int(row.get("id", 0))
            ref = self.ensure_ref(p_id, row)
            if ref.get("k_id") == k_id and ref not in result:
                result.append(ref)
        if valid_only:
            result = [ref for ref in result if self.ref_is_valid(ref)]
        return sorted(result, key=lambda item: int(item.get("p_id", 0)))

    def is_expired(self, batch: dict[str, Any]) -> bool:
        return _parse_date(batch.get("retention_until")) < _today()

    def download_gate(self, k_id: int) -> tuple[bool, str]:
        row = store.find("k_anonymity", k_id)
        if row is None:
            return False, f"匿名检查 {k_id} 不存在或已归档"
        batch = self.ensure_batch(k_id, row)
        if batch["cleaned"]:
            return False, (
                f"下载被拒绝：样本批次 {batch['batch_no']} 已于 {batch['cleaned_at']} 清理，"
                "已清理的样本不能继续提供下载；有资格继续的交付也不能指向已清理的样本。"
            )
        valid_refs = self.refs_for_batch(k_id, valid_only=True)
        if self.is_expired(batch) and not valid_refs:
            return False, (
                f"下载被拒绝：样本批次 {batch['batch_no']} 的保留期限至 {batch['retention_until']}，"
                "复核日 " + _today().isoformat() + " 已超过保留期限且无有效引用，"
                "不能继续提供下载。请先在假名化页办理引用续期并提交保留复核，或执行样本清理。"
            )
        return True, ""

    def batch_next_actions(
        self, batch: dict[str, Any], valid_refs: list[dict[str, Any]]
    ) -> list[str]:
        if batch["cleaned"]:
            return ["既往结论回查"]
        actions = ["保留复核"]
        if not self.is_expired(batch) or valid_refs:
            actions.append("下载样本")
        if self.is_expired(batch) and not valid_refs:
            actions.append("样本清理")
        return actions

    def ref_next_actions(
        self, ref: dict[str, Any], batch: dict[str, Any] | None
    ) -> list[str]:
        if batch is not None and batch["cleaned"]:
            return ["既往结论回查"]
        actions = ["引用续期"]
        if ref.get("delivery_state") == "交付中":
            actions.append("暂停交付")
        elif self.can_resume(ref, batch)[0]:
            actions.append("继续交付")
        elif ref.get("delivery_state") == "已暂停":
            # 即使当前缺少依据，也把「继续交付」作为可提交动作，由服务端给出阻断说明，
            # 方便页面验证“无依据时不能继续”。
            actions.append("继续交付")
        return actions

    def can_resume(
        self, ref: dict[str, Any], batch: dict[str, Any] | None
    ) -> tuple[bool, str]:
        if batch is None:
            return False, "引用未关联有效样本批次"
        if batch["cleaned"]:
            return False, (
                f"目标样本批次 {batch['batch_no']} 已清理，"
                "有资格继续的交付也不能指向已清理的样本"
            )
        if not self.ref_is_valid(ref):
            return False, "假名化交付引用已过期或无效，请先办理引用续期"
        receipt = self.latest_retain_receipt(batch["k_id"])
        if receipt is None:
            return False, "缺少结论为「准予保留」的《样本保留回执》，请先在 K 匿名检查页提交保留复核"
        return True, str(receipt.get("有效依据") or receipt.get("basis") or "")

    def latest_retain_receipt(self, k_id: int) -> dict[str, Any] | None:
        for receipt in reversed(self.receipts):
            if int(receipt.get("k_id", -1)) == k_id and receipt.get("retain"):
                return receipt
        return None

    # ————————————————————————————————————————————
    # 留痕
    # ————————————————————————————————————————————
    def _add_event(
        self,
        *,
        kind: str,
        type_name: str,
        ok: bool,
        message: str,
        conflict: bool = False,
        k_id: int | None = None,
        batch_no: str = "",
        p_id: int | None = None,
        ref_no: str = "",
        basis: str = "",
        receipt_no: str = "",
        next_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        self._event_seq += 1
        event = {
            "id": self._event_seq,
            "时间": _now_text(),
            "at": _now_text(),
            "类型": type_name,
            "kind": kind,
            "ok": ok,
            "conflict": conflict,
            "是否冲突": conflict,
            "k_id": k_id,
            "批次编号": batch_no,
            "p_id": p_id,
            "引用编号": ref_no,
            "结论": message,
            "说明": message,
            "有效依据": basis,
            "receipt_no": receipt_no,
            "回执编号": receipt_no,
            "下一次可执行的操作": next_actions or [],
        }
        self.events.append(event)
        return event

    def _next_receipt_no(self) -> str:
        self._receipt_seq += 1
        return f"RCPT-{self._receipt_seq:04d}"

    def _add_receipt(
        self,
        *,
        receipt_no: str,
        k_id: int,
        batch_no: str,
        retain: bool,
        conclusion: str,
        basis: str,
        valid_refs: list[str],
        retention_until: str,
    ) -> dict[str, Any]:
        receipt = {
            "回执编号": receipt_no,
            "receipt_no": receipt_no,
            "出具时间": _now_text(),
            "at": _now_text(),
            "经办人": OPERATOR,
            "k_id": k_id,
            "批次编号": batch_no,
            "是否准予保留": retain,
            "retain": retain,
            "结论": conclusion,
            "有效依据": basis,
            "basis": basis,
            "有效引用": valid_refs,
            "retention_until": retention_until,
        }
        self.receipts.append(receipt)
        return receipt

    # ————————————————————————————————————————————
    # 行装饰：把工作流状态合并回通用资源行
    # ————————————————————————————————————————————
    def decorate_k(self, row: dict[str, Any]) -> dict[str, Any]:
        k_id = int(row["id"])
        batch = self.ensure_batch(k_id, row)
        all_refs = self.refs_for_batch(k_id)
        valid_refs = [ref for ref in all_refs if self.ref_is_valid(ref)]
        allowed, block_reason = self.download_gate(k_id)
        if batch["cleaned"]:
            expiry_state = "已清理"
        elif self.is_expired(batch):
            expiry_state = "已到期"
        else:
            expiry_state = "未到期"
        next_actions = self.batch_next_actions(batch, valid_refs)
        row["is_sample_batch"] = bool(batch.get("explicit"))
        row["批次编号"] = batch["batch_no"]
        row["保留期限"] = batch["retention_until"]
        row["到期状态"] = expiry_state
        row["样本清理状态"] = "已清理" if batch["cleaned"] else "未清理"
        row["清理时间"] = batch["cleaned_at"] or ""
        row["有效引用数"] = len(valid_refs)
        row["有效引用"] = "、".join(
            f"{ref['ref_no']}(至{ref['valid_until']})" for ref in valid_refs
        )
        row["关联引用"] = "、".join(ref["ref_no"] for ref in all_refs)
        row["最近回执编号"] = batch["last_receipt_no"] or ""
        row["复核结论"] = batch["last_decision"] or ""
        row["有效依据"] = batch["basis"] or batch["cleanup_basis"] or ""
        row["download_allowed"] = allowed
        row["block_reason"] = block_reason
        row["next_actions"] = next_actions
        row["allowed_actions"] = next_actions
        row["next_action"] = next_actions[0] if next_actions else ""
        row["下一次可执行的操作"] = "、".join(next_actions)
        row["下一步"] = next_actions[0] if next_actions else ""
        return row

    def decorate_p(self, row: dict[str, Any]) -> dict[str, Any]:
        p_id = int(row["id"])
        ref = self.ensure_ref(p_id, row)
        batch = self.batches.get(ref["k_id"]) if ref.get("k_id") is not None else None
        if batch is None and ref.get("k_id") is not None:
            k_row = store.find("k_anonymity", int(ref["k_id"]))
            if k_row is not None:
                batch = self.ensure_batch(int(ref["k_id"]), k_row)
        valid = self.ref_is_valid(ref)
        can_continue, reason = self.can_resume(ref, batch)
        next_actions = self.ref_next_actions(ref, batch)
        batch_no = batch["batch_no"] if batch else ""
        row["is_delivery_ref"] = bool(ref.get("explicit"))
        row["引用编号"] = ref["ref_no"]
        row["关联批次编号"] = batch_no
        row["关联K检查ID"] = ref.get("k_id")
        row["引用有效期至"] = ref["valid_until"]
        row["原引用有效期"] = ref["original_valid_until"]
        row["引用状态"] = "有效" if valid else "已过期"
        row["交付状态"] = ref["delivery_state"]
        row["续期次数"] = ref["renew_count"]
        row["最近回执编号"] = ref.get("receipt_no") or ""
        row["有效依据"] = ref.get("basis") or ""
        row["can_continue"] = can_continue
        row["continue_block_reason"] = "" if can_continue else reason
        row["batch_cleaned"] = bool(batch and batch["cleaned"])
        row["next_actions"] = next_actions
        row["allowed_actions"] = next_actions
        row["next_action"] = next_actions[0] if next_actions else ""
        row["下一次可执行的操作"] = "、".join(next_actions)
        row["下一步"] = next_actions[0] if next_actions else ""
        return row

    # ————————————————————————————————————————————
    # K 页：保留复核
    # ————————————————————————————————————————————
    def review(self, k_id: int, remark: str | None = None) -> dict[str, Any]:
        with self._lock:
            row = store.find("k_anonymity", k_id)
            if row is None:
                return {"ok": False, "message": f"匿名检查 {k_id} 不存在或已归档", "entry": None}
            batch = self.ensure_batch(k_id, row)
            batch["explicit"] = True
            ts = _now_text()
            receipt_no = self._next_receipt_no()
            linked_refs = self.refs_for_batch(k_id)
            valid_refs = [ref for ref in linked_refs if self.ref_is_valid(ref)]

            if batch["cleaned"]:
                conclusion = "不予保留·样本已清理"
                basis = (
                    f"样本批次 {batch['batch_no']} 已于 {batch['cleaned_at']} 完成样本清理，"
                    f"清理依据：{batch['cleanup_basis']}"
                )
                receipt = self._add_receipt(
                    receipt_no=receipt_no, k_id=k_id, batch_no=batch["batch_no"],
                    retain=False, conclusion=conclusion, basis=basis, valid_refs=[],
                    retention_until=batch["retention_until"],
                )
                batch["last_receipt_no"] = receipt_no
                batch["last_decision"] = conclusion
                row["status"] = "已清理"
                for ref in linked_refs:
                    self._pause_ref(ref, row_note="样本已清理，交付保持暂停")
                message = (
                    f"保留复核已提交并出具《样本保留回执》{receipt_no}：批次 {batch['batch_no']} "
                    f"样本已清理，结论「{conclusion}」，清理不可撤销、样本不能恢复或继续下载。"
                    "冲突结果与既往结论可在下方「既往结论回查」中查询。"
                )
                event = self._add_event(
                    kind="review", type_name="保留复核", ok=True, message=message,
                    k_id=k_id, batch_no=batch["batch_no"], basis=basis,
                    receipt_no=receipt_no,
                )
                return {
                    "ok": True, "message": message, "receipt": receipt, "event": event,
                    "conflict": False, "next_actions": ["既往结论回查"],
                    "entry": self.decorate_k(row),
                }

            expired = self.is_expired(batch)
            if valid_refs:
                ref_summaries = [
                    f"{ref['ref_no']}（有效期至 {ref['valid_until']}，续期 {ref['renew_count']} 次）"
                    for ref in valid_refs
                ]
                basis = (
                    f"《样本保留回执》{receipt_no} 结论为准予保留；"
                    f"有效假名化交付引用：{'、'.join(ref_summaries)}；复核时间 {ts}"
                )
                conclusion = "准予保留·存在有效引用"
                receipt = self._add_receipt(
                    receipt_no=receipt_no, k_id=k_id, batch_no=batch["batch_no"],
                    retain=True, conclusion=conclusion, basis=basis,
                    valid_refs=[ref["ref_no"] for ref in valid_refs],
                    retention_until=batch["retention_until"],
                )
                batch["last_receipt_no"] = receipt_no
                batch["last_decision"] = conclusion
                batch["basis"] = basis
                row["status"] = "保留中"
                row["pending"] = True
                row["abnormal"] = False
                continued: list[str] = []
                for ref in valid_refs:
                    if ref.get("delivery_state") != "已完成":
                        ref["delivery_state"] = "交付中"
                        ref["basis"] = basis
                        ref["receipt_no"] = receipt_no
                        pseudo_row = store.find("pseudonym", int(ref["p_id"]))
                        if pseudo_row is not None:
                            pseudo_row["status"] = "交付中"
                        continued.append(ref["ref_no"])
                        self._add_event(
                            kind="resume", type_name="继续交付", ok=True,
                            message=(
                                f"假名化依据《样本保留回执》{receipt_no} 继续未完成交付 "
                                f"{ref['ref_no']}，当前状态：交付中。"
                            ),
                            k_id=k_id, batch_no=batch["batch_no"],
                            p_id=int(ref["p_id"]), ref_no=ref["ref_no"], basis=basis,
                            receipt_no=receipt_no, next_actions=["暂停交付", "引用续期"],
                        )
                next_actions = self.batch_next_actions(batch, valid_refs)
                message = (
                    f"保留复核完成，已出具《样本保留回执》{receipt_no}：批次 {batch['batch_no']} "
                    f"验证到 {len(valid_refs)} 条有效假名化交付引用，结论「准予保留」。"
                    f"本次继续处理的有效依据：{basis}。"
                    + (f"假名化页已据此继续交付：{'、'.join(continued)}。" if continued else "")
                    + f"下一次可执行的操作：{'、'.join(next_actions)}。"
                )
                event = self._add_event(
                    kind="review", type_name="保留复核", ok=True, message=message,
                    k_id=k_id, batch_no=batch["batch_no"], basis=basis,
                    receipt_no=receipt_no, next_actions=next_actions,
                )
                return {
                    "ok": True, "message": message, "receipt": receipt, "event": event,
                    "conflict": False, "next_actions": next_actions,
                    "entry": self.decorate_k(row),
                }

            # 无有效引用
            if expired:
                conclusion = "不予保留·到期且无有效引用"
                basis = (
                    f"保留期限至 {batch['retention_until']}，复核日 {_today().isoformat()} "
                    "已超过保留期限，且无有效假名化交付引用"
                )
                row["status"] = "待清理"
                row["pending"] = True
                row["abnormal"] = True
                next_actions = ["样本清理"]
                pause_note = "保留复核结论为不予保留，未完成交付暂停"
            else:
                conclusion = "正常保留·未到期"
                basis = (
                    f"保留期限至 {batch['retention_until']}，复核日 {_today().isoformat()} "
                    "尚未超过保留期限"
                )
                row["status"] = "保留中"
                row["pending"] = True
                row["abnormal"] = False
                next_actions = ["下载样本", "保留复核"]
                pause_note = "缺少有效假名化交付引用，交付暂停，待引用续期后复核"
            receipt = self._add_receipt(
                receipt_no=receipt_no, k_id=k_id, batch_no=batch["batch_no"],
                retain=not expired, conclusion=conclusion, basis=basis, valid_refs=[],
                retention_until=batch["retention_until"],
            )
            batch["last_receipt_no"] = receipt_no
            batch["last_decision"] = conclusion
            batch["basis"] = basis
            for ref in linked_refs:
                self._pause_ref(ref, row_note=pause_note, basis=basis, receipt_no=receipt_no)
            if expired:
                message = (
                    f"保留复核完成，已出具《样本保留回执》{receipt_no}：结论「{conclusion}」。"
                    f"{basis}——超过保留期限且无有效引用的样本不能继续提供下载，"
                    "关联的未完成交付已在假名化页暂停。"
                    f"本次处理的有效依据：{basis}。下一次可执行的操作：样本清理。"
                )
            else:
                message = (
                    f"保留复核完成，已出具《样本保留回执》{receipt_no}：结论「{conclusion}」。"
                    f"{basis}，样本仍可下载；但当前无有效假名化交付引用，交付保持暂停，"
                    "可先办理引用续期后再次提交保留复核。"
                    f"有效依据：{basis}。"
                    f"下一次可执行的操作：{'、'.join(next_actions)}。"
                )
            event = self._add_event(
                kind="review", type_name="保留复核", ok=True, message=message,
                k_id=k_id, batch_no=batch["batch_no"], basis=basis,
                receipt_no=receipt_no, next_actions=next_actions,
            )
            return {
                "ok": True, "message": message, "receipt": receipt, "event": event,
                "conflict": False, "next_actions": next_actions,
                "entry": self.decorate_k(row),
            }

    def _pause_ref(
        self,
        ref: dict[str, Any],
        *,
        row_note: str,
        basis: str = "",
        receipt_no: str | None = None,
    ) -> None:
        if ref.get("delivery_state") == "已完成":
            return
        ref["delivery_state"] = "已暂停"
        if basis:
            ref["basis"] = basis
        if receipt_no:
            ref["receipt_no"] = receipt_no
        pseudo_row = store.find("pseudonym", int(ref["p_id"]))
        if pseudo_row is not None:
            pseudo_row["status"] = "已暂停"
        self._add_event(
            kind="pause", type_name="暂停交付", ok=True, message=row_note,
            k_id=ref.get("k_id"), batch_no=self.batches.get(
                ref.get("k_id", -1), {}
            ).get("batch_no", "") if ref.get("k_id") is not None else "",
            p_id=int(ref["p_id"]), ref_no=ref["ref_no"], basis=basis,
            receipt_no=receipt_no or "", next_actions=["引用续期", "继续交付"],
        )

    # ————————————————————————————————————————————
    # K 页：样本清理（与引用续期争用）
    # ————————————————————————————————————————————
    def cleanup(self, k_id: int, remark: str | None = None) -> dict[str, Any]:
        with self._lock:
            row = store.find("k_anonymity", k_id)
            if row is None:
                return {"ok": False, "message": f"匿名检查 {k_id} 不存在或已归档", "entry": None}
            batch = self.ensure_batch(k_id, row)
            batch["explicit"] = True
            if batch["cleaned"]:
                message = (
                    f"样本批次 {batch['batch_no']} 已于 {batch['cleaned_at']} 清理，"
                    "请勿重复提交样本清理；清理结论与既往保留复核回执均可在"
                    "「既往结论回查」中查询。"
                )
                self._add_event(
                    kind="cleanup", type_name="样本清理", ok=False, message=message,
                    k_id=k_id, batch_no=batch["batch_no"], conflict=False,
                    basis=batch["cleanup_basis"],
                    next_actions=["既往结论回查"],
                )
                return {
                    "ok": False, "message": message, "conflict": False,
                    "next_actions": ["既往结论回查"], "entry": self.decorate_k(row),
                }

            if not self.is_expired(batch):
                message = (
                    f"样本清理未执行：批次 {batch['batch_no']} 的保留期限至 "
                    f"{batch['retention_until']}，复核日 {_today().isoformat()} 仍为「未到期」，"
                    "不能清理未超过保留期限的样本；样本可继续下载，"
                    "可按需要办理引用续期或保留复核。"
                )
                self._add_event(
                    kind="cleanup_rejected", type_name="样本清理", ok=False, message=message,
                    k_id=k_id, batch_no=batch["batch_no"],
                    next_actions=["保留复核", "下载样本", "引用续期"],
                )
                return {
                    "ok": False, "message": message, "conflict": False,
                    "next_actions": ["保留复核", "下载样本"],
                    "entry": self.decorate_k(row),
                }

            valid_refs = self.refs_for_batch(k_id, valid_only=True)
            if valid_refs:
                # 引用续期先提交：有效引用构成继续处理的有效依据，清理在争用中失败。
                receipt_no = self._next_receipt_no()
                ref_summaries = [
                    f"{ref['ref_no']}（有效期至 {ref['valid_until']}）" for ref in valid_refs
                ]
                basis = (
                    f"有效假名化交付引用：{'、'.join(ref_summaries)}；"
                    f"冲突裁定回执 {receipt_no} 结论：准予保留；裁定时间 {_now_text()}"
                )
                receipt = self._add_receipt(
                    receipt_no=receipt_no, k_id=k_id, batch_no=batch["batch_no"],
                    retain=True, conclusion="准予保留·清理争用有效引用", basis=basis,
                    valid_refs=[ref["ref_no"] for ref in valid_refs],
                    retention_until=batch["retention_until"],
                )
                batch["last_receipt_no"] = receipt_no
                batch["last_decision"] = "准予保留·清理争用有效引用"
                batch["basis"] = basis
                row["status"] = "保留中"
                for ref in valid_refs:
                    if ref.get("delivery_state") != "已完成":
                        ref["delivery_state"] = "交付中"
                        ref["basis"] = basis
                        ref["receipt_no"] = receipt_no
                        pseudo_row = store.find("pseudonym", int(ref["p_id"]))
                        if pseudo_row is not None:
                            pseudo_row["status"] = "交付中"
                message = (
                    f"冲突结果：样本清理与引用续期争用同一批次 {batch['batch_no']}，"
                    "本次以「引用续期形成的有效引用」为有效依据，样本清理被拒绝、样本保留。"
                    f"服务端验证到有效假名化交付引用 {'、'.join(ref_summaries)}，"
                    f"并补出《样本保留回执》{receipt_no}。"
                    f"本次继续处理的有效依据：{basis}；关联交付继续为「交付中」，下载保持可用。"
                    "该冲突结论已留痕，可在「既往结论回查」中回查。"
                )
                self._add_event(
                    kind="cleanup_conflict", type_name="样本清理冲突", ok=False,
                    conflict=True, message=message, k_id=k_id,
                    batch_no=batch["batch_no"], basis=basis, receipt_no=receipt_no,
                    next_actions=["保留复核", "下载样本", "继续交付"],
                )
                return {
                    "ok": False, "message": message, "receipt": receipt, "conflict": True,
                    "next_actions": ["保留复核", "下载样本"],
                    "entry": self.decorate_k(row),
                }

            # 到期且无有效引用：清理生效。
            basis = (
                f"保留期限至 {batch['retention_until']}，清理日 {_today().isoformat()} "
                "已超过保留期限且无有效引用"
            )
            ts = _now_text()
            batch["cleaned"] = True
            batch["cleaned_at"] = ts
            batch["cleanup_basis"] = basis
            batch["basis"] = basis
            row["status"] = "已清理"
            row["pending"] = False
            row["abnormal"] = False
            for ref in self.refs_for_batch(k_id):
                self._pause_ref(ref, row_note="样本批次已清理，交付必须暂停，不得再指向该样本")
            message = (
                f"样本清理完成：批次 {batch['batch_no']} 已清理（{ts}）。"
                f"有效依据：{basis}。自即时起该样本不能继续提供下载，"
                "所有指向该样本的假名化交付引用均置为「已暂停」；"
                "此后提交的引用续期将按冲突拒绝，且不能恢复交付。清理结论已留痕可回查。"
            )
            self._add_event(
                kind="cleanup", type_name="样本清理", ok=True, message=message,
                k_id=k_id, batch_no=batch["batch_no"], basis=basis,
                next_actions=["既往结论回查"],
            )
            return {
                "ok": True, "message": message, "conflict": False,
                "next_actions": ["既往结论回查"], "entry": self.decorate_k(row),
            }

    # ————————————————————————————————————————————
    # 假名化页：引用续期
    # ————————————————————————————————————————————
    def renew(self, p_id: int, days: int | None = None) -> dict[str, Any]:
        with self._lock:
            row = store.find("pseudonym", p_id)
            if row is None:
                return {"ok": False, "message": f"假名映射 {p_id} 不存在或已归档", "entry": None}
            ref = self.ensure_ref(p_id, row)
            ref["explicit"] = True
            k_id = ref.get("k_id")
            k_row = store.find("k_anonymity", int(k_id)) if k_id is not None else None
            batch = self.ensure_batch(int(k_id), k_row) if k_id is not None and k_row is not None else None
            extension = days if isinstance(days, int) and days > 0 else 180

            if batch is not None and batch["cleaned"]:
                message = (
                    f"冲突结果：引用续期与样本清理争用同一批次 {batch['batch_no']}，"
                    "清理已先生效，本次引用续期不予生效。"
                    f"样本已于 {batch['cleaned_at']} 清理——有资格继续的交付也不能指向已清理的样本。"
                    f"原引用 {ref['ref_no']}（假名标识 {row.get('假名标识', '')}）保留可查，"
                    f"其原有效期 {ref['valid_until']} 与续期次数 {ref['renew_count']} 均不变，"
                    "交付保持「已暂停」，继续交付与样本下载均被禁止。"
                    f"清理有效依据：{batch['cleanup_basis']}。冲突结论已留痕可回查。"
                )
                self._add_event(
                    kind="renew_conflict", type_name="引用续期冲突", ok=False,
                    conflict=True, message=message, k_id=k_id,
                    batch_no=batch["batch_no"] if batch else "", p_id=p_id,
                    ref_no=ref["ref_no"], basis=batch["cleanup_basis"] if batch else "",
                    next_actions=["既往结论回查"],
                )
                return {
                    "ok": False, "message": message, "conflict": True,
                    "next_actions": ["既往结论回查"], "entry": self.decorate_p(row),
                }

            base = max(_today(), _parse_date(ref["valid_until"]))
            new_until = base + timedelta(days=extension)
            ref["valid_until"] = new_until.isoformat()
            ref["renew_count"] += 1
            if str(row.get("status")) == "已失效":
                row["status"] = ref["delivery_state"]
            batch_no = batch["batch_no"] if batch else ""
            message = (
                f"引用续期完成：原引用 {ref['ref_no']}（假名标识 {row.get('假名标识', '')}）"
                f"编号与假名标识不变，有效期延长至 {ref['valid_until']}，"
                f"累计续期 {ref['renew_count']} 次。"
            )
            if batch is not None:
                message += (
                    f"关联样本批次 {batch_no} 当前「"
                    + ("已清理" if batch["cleaned"] else ("已到期" if self.is_expired(batch) else "未到期"))
                    + "」。可到 K 匿名检查页提交保留复核，凭《样本保留回执》继续交付；"
                    "若批次已到期且无其他有效引用，样本清理将因本有效引用而被冲突拦截。"
                )
            message += "下一次可执行的操作：保留复核、继续交付（需准予保留回执）。"
            self._add_event(
                kind="renew", type_name="引用续期", ok=True, message=message,
                k_id=k_id, batch_no=batch_no, p_id=p_id, ref_no=ref["ref_no"],
                next_actions=["保留复核", "继续交付"],
            )
            return {
                "ok": True, "message": message, "conflict": False,
                "next_actions": ["保留复核", "继续交付"], "entry": self.decorate_p(row),
            }

    # ————————————————————————————————————————————
    # 假名化页：继续 / 暂停交付
    # ————————————————————————————————————————————
    def resume(self, p_id: int) -> dict[str, Any]:
        with self._lock:
            row = store.find("pseudonym", p_id)
            if row is None:
                return {"ok": False, "message": f"假名映射 {p_id} 不存在或已归档", "entry": None}
            ref = self.ensure_ref(p_id, row)
            ref["explicit"] = True
            k_id = ref.get("k_id")
            k_row = store.find("k_anonymity", int(k_id)) if k_id is not None else None
            batch = self.ensure_batch(int(k_id), k_row) if k_id is not None and k_row is not None else None
            allowed, reason = self.can_resume(ref, batch)
            if not allowed:
                message = f"继续交付被拒绝：{reason}。引用 {ref['ref_no']} 维持「{ref['delivery_state']}」。"
                self._add_event(
                    kind="resume_blocked", type_name="继续交付", ok=False,
                    conflict=bool(batch and batch["cleaned"]), message=message,
                    k_id=k_id, batch_no=batch["batch_no"] if batch else "",
                    p_id=p_id, ref_no=ref["ref_no"],
                    basis=batch["cleanup_basis"] if batch and batch["cleaned"] else "",
                    next_actions=["引用续期", "保留复核"],
                )
                return {
                    "ok": False, "message": message,
                    "conflict": bool(batch and batch["cleaned"]),
                    "next_actions": ["引用续期", "保留复核"],
                    "entry": self.decorate_p(row),
                }
            receipt = self.latest_retain_receipt(int(k_id))  # type: ignore[arg-type]
            basis = str(receipt.get("有效依据")) if receipt else reason
            ref["delivery_state"] = "交付中"
            ref["basis"] = basis
            ref["receipt_no"] = receipt.get("回执编号") if receipt else None
            row["status"] = "交付中"
            message = (
                f"交付继续：引用 {ref['ref_no']}（假名标识 {row.get('假名标识', '')}）"
                f"恢复「交付中」。本次继续处理的有效依据：{basis}。"
                "下一次可执行的操作：暂停交付、引用续期。"
            )
            self._add_event(
                kind="resume", type_name="继续交付", ok=True, message=message,
                k_id=k_id, batch_no=batch["batch_no"] if batch else "",
                p_id=p_id, ref_no=ref["ref_no"], basis=basis,
                receipt_no=str(ref.get("receipt_no") or ""),
                next_actions=["暂停交付", "引用续期"],
            )
            return {
                "ok": True, "message": message, "conflict": False,
                "next_actions": ["暂停交付", "引用续期"], "entry": self.decorate_p(row),
            }

    def pause(self, p_id: int) -> dict[str, Any]:
        with self._lock:
            row = store.find("pseudonym", p_id)
            if row is None:
                return {"ok": False, "message": f"假名映射 {p_id} 不存在或已归档", "entry": None}
            ref = self.ensure_ref(p_id, row)
            ref["explicit"] = True
            if ref.get("delivery_state") == "已暂停":
                message = f"引用 {ref['ref_no']} 当前已经是「已暂停」，无需重复暂停。"
                return {
                    "ok": False, "message": message, "conflict": False,
                    "next_actions": ["引用续期", "继续交付"], "entry": self.decorate_p(row),
                }
            k_id = ref.get("k_id")
            batch = self.batches.get(int(k_id)) if k_id is not None else None
            self._pause_ref(ref, row_note="管理员手动暂停未完成交付",
                            basis=ref.get("basis", ""), receipt_no=ref.get("receipt_no"))
            row["status"] = "已暂停"
            message = (
                f"交付已暂停：引用 {ref['ref_no']} 置为「已暂停」；"
                "取得有效引用与准予保留回执后可再次继续交付。"
            )
            return {
                "ok": True, "message": message, "conflict": False,
                "next_actions": ["引用续期", "继续交付"], "entry": self.decorate_p(row),
            }

    # ————————————————————————————————————————————
    # 下载
    # ————————————————————————————————————————————
    def build_sample_file(self, k_id: int) -> tuple[str, str]:
        row = store.find("k_anonymity", k_id)
        batch = self.ensure_batch(k_id, row)  # type: ignore[arg-type]
        filename = f"{batch['batch_no']}-sample.csv"
        lines = [
            "批次编号,检查编号,数据集名称,保留期限,导出时间,说明",
            ",".join([
                batch["batch_no"], str(row.get("检查编号", "")),  # type: ignore[union-attr]
                f"\"{row.get('数据集名称', '')}\"",  # type: ignore[union-attr]
                batch["retention_until"], _now_text(), "K匿名化样本，仅含假名化标识",
            ]),
            "",
            "假名化交付引用,假名标识,行键,度量值",
        ]
        for idx, ref in enumerate(self.refs_for_batch(k_id), start=1):
            pseudo_row = store.find("pseudonym", int(ref["p_id"])) or {}
            lines.append(",".join([
                ref["ref_no"], str(pseudo_row.get("假名标识", "")),
                f"row-{idx:04d}", f"{1000 + idx * 37}",
            ]))
        lines += [
            "",
            "样本行ID,年龄段,邮编前缀,性别,聚合度量",
            "S-0001,30-39,100**,*,128",
            "S-0002,40-49,200**,*,256",
            "S-0003,50-59,300**,*,384",
        ]
        return "﻿" + "\n".join(lines) + "\n", filename

    def log_download(self, k_id: int, allowed: bool, reason: str = "") -> None:
        row = store.find("k_anonymity", k_id)
        batch = self.ensure_batch(k_id, row) if row is not None else None
        self._add_event(
            kind="download", type_name="样本下载", ok=allowed,
            message=(
                f"样本批次 {batch['batch_no'] if batch else k_id} 下载成功。"
                if allowed else reason
            ),
            k_id=k_id, batch_no=batch["batch_no"] if batch else "",
            basis=batch["basis"] if batch and allowed else "",
        )

    # ————————————————————————————————————————————
    # 汇总查询
    # ————————————————————————————————————————————
    def list_batches(self, *, explicit_only: bool = True) -> list[dict[str, Any]]:
        result = []
        for row in store.rows("k_anonymity"):
            decorated = self.decorate_k(row)
            if not explicit_only or decorated.get("is_sample_batch"):
                result.append(decorated)
        return result

    def list_references(self, *, explicit_only: bool = True) -> list[dict[str, Any]]:
        result = []
        for row in store.rows("pseudonym"):
            decorated = self.decorate_p(row)
            if not explicit_only or decorated.get("is_delivery_ref"):
                result.append(decorated)
        return result

    def list_events(self, k_id: int | None = None, p_id: int | None = None) -> list[dict[str, Any]]:
        events = list(self.events)
        if k_id is not None:
            events = [e for e in events if e.get("k_id") == k_id]
        if p_id is not None:
            events = [e for e in events if e.get("p_id") == p_id]
        return events

    def list_receipts(self, k_id: int | None = None) -> list[dict[str, Any]]:
        if k_id is None:
            return list(self.receipts)
        return [r for r in self.receipts if int(r.get("k_id", -1)) == k_id]


flow = RetentionFlow()
