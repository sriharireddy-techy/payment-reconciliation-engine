"""Concurrency lab: request validation and the consistency checks applied to a run.

The checks are fed hand-built results here, including deliberately corrupted
ones, to prove they catch overspending and double debits. Genuine concurrent
execution against Oracle is tested in tests/integration/test_oracle_concurrency.py.
"""

from decimal import Decimal

import pytest

from services import concurrency_service
from services.concurrency_service import evaluate_consistency
from services.errors import ValidationFailedError


def run(n_success, n_failed, amount="300"):
    results = [{"outcome": "SUCCESS"}] * n_success + [{"outcome": "FAILED"}] * n_failed
    txns = [{"status": "SUCCESS", "entry_count": 2, "dr_total": Decimal(amount), "cr_total": Decimal(amount)}] * n_success
    txns += [{"status": "FAILED", "entry_count": 0, "dr_total": 0, "cr_total": 0}] * n_failed
    return results, txns


def failed_names(checks):
    return {c["name"] for c in checks if not c["passed"]}


def test_correct_run_passes_every_check():
    results, txns = run(3, 7)
    checks = evaluate_consistency(
        amount=Decimal("300"), mode="unique_keys", sender_before="1000", sender_after="100",
        receiver_before="0", receiver_after="900", results=results, transactions=txns,
    )
    assert failed_names(checks) == set()


def test_overspending_is_detected():
    results, txns = run(4, 6)
    checks = evaluate_consistency(
        amount=Decimal("300"), mode="unique_keys", sender_before="1000", sender_after="-200",
        receiver_before="0", receiver_after="1200", results=results, transactions=txns,
    )
    assert {"No overspending", "Sender balance never negative"} <= failed_names(checks)


def test_double_debit_is_detected():
    results, txns = run(3, 7)
    checks = evaluate_consistency(
        amount=Decimal("300"), mode="unique_keys", sender_before="1000", sender_after="-200",
        receiver_before="0", receiver_after="900", results=results, transactions=txns,
    )
    assert "Sender debited exactly once per success" in failed_names(checks)


def test_lost_update_is_detected():
    # Two payments succeeded but the balance only dropped once (classic lost update).
    results, txns = run(2, 0)
    checks = evaluate_consistency(
        amount=Decimal("300"), mode="unique_keys", sender_before="1000", sender_after="700",
        receiver_before="0", receiver_after="600", results=results, transactions=txns,
    )
    assert "Sender debited exactly once per success" in failed_names(checks)


def test_missing_ledger_entries_are_detected():
    results, txns = run(1, 0)
    txns = [{**txns[0], "entry_count": 1, "cr_total": Decimal("0")}]
    checks = evaluate_consistency(
        amount=Decimal("300"), mode="unique_keys", sender_before="1000", sender_after="700",
        receiver_before="0", receiver_after="300", results=results, transactions=txns,
    )
    assert "Ledger balanced for every successful payment" in failed_names(checks)


def test_api_and_database_disagreement_is_detected():
    results, txns = run(3, 0)
    results = results + [{"outcome": "SUCCESS"}]  # API claims a 4th success the DB does not have
    checks = evaluate_consistency(
        amount=Decimal("300"), mode="unique_keys", sender_before="1000", sender_after="100",
        receiver_before="0", receiver_after="900", results=results, transactions=txns,
    )
    assert "API responses match the database" in failed_names(checks)


def test_same_key_mode_requires_a_single_payment():
    results = [{"outcome": "SUCCESS"}] + [{"outcome": "REPLAYED"}] * 4
    one = [{"status": "SUCCESS", "entry_count": 2, "dr_total": Decimal("50"), "cr_total": Decimal("50")}]
    ok = evaluate_consistency(amount=Decimal("50"), mode="same_key", sender_before="1000", sender_after="950",
                              receiver_before="0", receiver_after="50", results=results, transactions=one)
    assert failed_names(ok) == set()
    bad = evaluate_consistency(amount=Decimal("50"), mode="same_key", sender_before="1000", sender_after="900",
                               receiver_before="0", receiver_after="100", results=results, transactions=one * 2)
    assert "Same idempotency key created one payment" in failed_names(bad)


@pytest.mark.parametrize("change", [{"num_requests": 1}, {"num_requests": 101}, {"receiver_account_id": 1}, {"mode": "chaos"}, {"amount": "0"}])
def test_invalid_concurrency_requests(client, change):
    body = {"sender_account_id": 1, "receiver_account_id": 2, "amount": "300.00", "num_requests": 10, **change}
    assert client.post("/api/concurrency/run", json=body).status_code == 422


def test_configured_request_limit_is_enforced(monkeypatch):
    import config

    monkeypatch.setenv("CONCURRENCY_MAX_REQUESTS", "5")
    config.get_settings.cache_clear()
    with pytest.raises(ValidationFailedError):
        concurrency_service.run_concurrency_test(
            sender_account_id=1, receiver_account_id=2, amount=Decimal("1"), num_requests=6
        )


def test_no_python_lock_guards_payments():
    """Correctness must come from Oracle, not from an in-process lock around payments."""
    import inspect

    from services import payment_service

    for module in (payment_service, concurrency_service):
        source = inspect.getsource(module)
        assert "threading.Lock" not in source and "RLock" not in source
