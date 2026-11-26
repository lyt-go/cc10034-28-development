"""保留复核 / 引用续期 / 样本清理 工作流测试（直接调用领域服务，不依赖 httpx）。

每个用例前后都把内存仓库与台账恢复到规范的「一到期、一未到期」场景，避免用例间状态污染。
"""
from __future__ import annotations

import pytest

from app.services.k_anonymity import KAnonymityService
from app.services.pseudonym import PseudonymService
from app.services.retention_flow import flow
from app.store import store


@pytest.fixture(autouse=True)
def reset_state():
    store.reset()
    flow.reset()
    yield
    store.reset()
    flow.reset()


@pytest.fixture()
def ksvc():
    return KAnonymityService()


@pytest.fixture()
def psvc():
    return PseudonymService()


def test_seed_has_one_expired_and_one_active_batch():
    batches = flow.list_batches()
    by_no = {b["批次编号"]: b for b in batches}
    assert set(by_no) == {"BATCH-EXP-01", "BATCH-NEW-01"}
    assert by_no["BATCH-EXP-01"]["到期状态"] == "已到期"
    assert by_no["BATCH-NEW-01"]["到期状态"] == "未到期"


def test_expired_batch_download_blocked_initially():
    allowed, reason = flow.download_gate(4)
    assert allowed is False
    assert "超过保留期限" in reason and "无有效引用" in reason


def test_unexpired_batch_download_allowed():
    allowed, _ = flow.download_gate(5)
    assert allowed is True


def test_review_without_valid_ref_rejects_and_pauses():
    result = flow.review(4)
    assert result["ok"] is True
    receipt = result["receipt"]
    assert receipt["retain"] is False
    assert "样本保留回执" in result["message"]
    assert receipt["回执编号"].startswith("RCPT-")
    assert flow.refs[4]["delivery_state"] == "已暂停"


def test_cleanup_then_renew_conflicts_and_keeps_original_ref():
    review = flow.review(4)
    assert review["receipt"]["retain"] is False
    cleaned = flow.cleanup(4)
    assert cleaned["ok"] is True
    assert store.find("k_anonymity", 4)["status"] == "已清理"

    original_until = flow.refs[4]["valid_until"]
    renew = flow.renew(4)
    assert renew["ok"] is False
    assert renew["conflict"] is True
    assert "冲突结果" in renew["message"]
    assert flow.refs[4]["valid_until"] == original_until
    assert flow.refs[4]["renew_count"] == 0

    resume = flow.resume(4)
    assert resume["ok"] is False
    assert "已清理" in resume["message"]

    allowed, _ = flow.download_gate(4)
    assert allowed is False


def test_renew_then_review_grants_receipt_and_blocks_cleanup():
    renew = flow.renew(4)
    assert renew["ok"] is True
    assert flow.refs[4]["renew_count"] == 1

    review = flow.review(4)
    assert review["ok"] is True
    assert review["receipt"]["retain"] is True
    assert "PREF-EXP-01" in review["receipt"]["有效依据"]
    assert flow.refs[4]["delivery_state"] == "交付中"

    cleanup = flow.cleanup(4)
    assert cleanup["ok"] is False
    assert cleanup["conflict"] is True
    assert "有效依据" in cleanup["message"]

    allowed, _ = flow.download_gate(4)
    assert allowed is True


def test_cleanup_unexpired_batch_rejected():
    result = flow.cleanup(5)
    assert result["ok"] is False
    assert "未到期" in result["message"]
    assert store.find("k_anonymity", 5)["status"] != "已清理"


def test_resume_requires_receipt_basis():
    # 先续期让引用有效，但尚无准予保留回执
    assert flow.renew(4)["ok"] is True
    result = flow.resume(4)
    assert result["ok"] is False
    assert "保留复核" in result["message"]


def test_history_and_receipts_are_append_only():
    flow.review(4)
    flow.cleanup(4)
    flow.renew(4)
    events = flow.list_events()
    kinds = [e["kind"] for e in events]
    assert "review" in kinds and "cleanup" in kinds and "renew_conflict" in kinds
    assert len(flow.list_receipts()) >= 1
    assert all(e["k_id"] == 4 for e in flow.list_events(k_id=4))


def test_generic_action_dispatch_contract(ksvc):
    result = ksvc.dispatch_action(4, "保留复核")
    assert result["ok"] is True
    assert result["receipt"] is not None
    assert result["entry"]["id"] == 4
    assert result["next_actions"]
    # 旧的二元组契约仍可用
    entry, message = ksvc.run_action(5, "保留复核")
    assert entry is not None and "回执" in message


def test_action_synonyms(psvc, ksvc):
    assert psvc.dispatch_action(4, "续期")["ok"] is True
    review = ksvc.dispatch_action(4, "保留复核")
    assert review["receipt"]["retain"] is True
    cleanup = ksvc.dispatch_action(4, "清理")
    assert cleanup["conflict"] is True


def test_download_file_built_for_allowed_batch():
    content, filename = flow.build_sample_file(5)
    assert "BATCH-NEW-01" in filename
    assert "批次编号" in content


def test_created_expired_batch_is_explicit(ksvc):
    entry, missing = ksvc.create_entry({
        "检查编号": "K_AN-T1",
        "数据集名称": "自建批次",
        "准标识符列": "a,b",
        "保留期限": "2020-01-01",
    })
    assert missing == [] and entry is not None
    detail = ksvc.get_entry(int(entry["id"]))
    assert detail["is_sample_batch"] is True
    assert detail["到期状态"] == "已到期"


def test_original_rows_remain_non_batch_until_acted_on():
    row = KAnonymityService().get_entry(1)
    assert row is not None
    assert row["is_sample_batch"] is False
    prow = PseudonymService().get_entry(1)
    assert prow is not None
    assert prow["is_delivery_ref"] is False
