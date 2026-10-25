"""Real Oracle: genuinely concurrent payments against one account."""

from decimal import Decimal

from services.concurrency_service import run_concurrency_test


def test_concurrent_payments_cannot_overspend(make_account, balance):
    sender, receiver = make_account("1000.00"), make_account("0.00")
    result = run_concurrency_test(
        sender_account_id=sender, receiver_account_id=receiver, amount=Decimal("300.00"), num_requests=10
    )
    assert result["all_checks_passed"], result["checks"]
    assert result["succeeded"] == 3
    assert result["rejected"] + result["errors"] == 7
    assert balance(sender) == Decimal("100") and balance(receiver) == Decimal("900")
    # Requests really overlapped in time (not a sequential loop).
    starts = sorted(r["started_ms"] for r in result["requests"])
    finishes = sorted(r["finished_ms"] for r in result["requests"])
    assert starts[-1] < finishes[-1] and starts[1] < finishes[0] + 1000


def test_concurrent_retries_with_one_key_create_one_payment(make_account, balance):
    sender, receiver = make_account("1000.00"), make_account("0.00")
    result = run_concurrency_test(
        sender_account_id=sender, receiver_account_id=receiver, amount=Decimal("50.00"), num_requests=8, mode="same_key"
    )
    assert result["all_checks_passed"], result["checks"]
    assert result["transactions_created"] == 1
    assert balance(sender) == Decimal("950")


def test_opposite_direction_transfers_do_not_deadlock(make_account, balance):
    a, b = make_account("1000.00"), make_account("1000.00")
    from concurrent.futures import ThreadPoolExecutor
    import uuid

    from services.payment_service import PaymentCommand, create_payment

    def pay(i):
        src, dst = (a, b) if i % 2 else (b, a)
        return create_payment(PaymentCommand(src, dst, Decimal("10.00"), f"IT-DL-{uuid.uuid4().hex}")).status

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(pay, range(12)))
    assert statuses.count("SUCCESS") == 12
    assert balance(a) + balance(b) == Decimal("2000")
