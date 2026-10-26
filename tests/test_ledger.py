"""Double-entry ledger rules (pure checks used by the integrity report)."""

from decimal import Decimal

from services.ledger_service import summarize_entries, transaction_problems


def row(status="SUCCESS", amount="100", dr="100", cr="100", count=2, sender_debits=1, receiver_credits=1):
    return {
        "status": status, "amount": Decimal(amount), "dr_total": Decimal(dr), "cr_total": Decimal(cr),
        "entry_count": count, "sender_debits": sender_debits, "receiver_credits": receiver_credits,
    }


def test_balanced_successful_payment_has_no_problems():
    assert transaction_problems(row()) == []


def test_failed_payment_without_entries_is_fine():
    assert transaction_problems(row("FAILED", dr="0", cr="0", count=0, sender_debits=0, receiver_credits=0)) == []


def test_unbalanced_entries_are_reported():
    problems = transaction_problems(row(dr="100", cr="90"))
    assert any("do not equal" in p for p in problems)


def test_missing_entry_is_reported():
    assert transaction_problems(row(cr="0", dr="100", count=1))


def test_failed_payment_with_entries_is_reported():
    assert transaction_problems(row("FAILED"))


def test_ledger_amount_must_match_payment_amount():
    problems = transaction_problems(row(amount="120"))
    assert any("differs" in p for p in problems)


def test_entries_on_wrong_accounts_are_reported():
    assert transaction_problems(row(sender_debits=0, receiver_credits=0))


def test_summary_marks_balanced_entries():
    entries = [
        {"entry_type": "DR", "amount": Decimal("75.5"), "entry_id": 1},
        {"entry_type": "CR", "amount": Decimal("75.50"), "entry_id": 2},
    ]
    summary = summarize_entries(entries)
    assert summary["balanced"] is True
    assert summary["dr_total"] == "75.50" and summary["cr_total"] == "75.50"
    assert summary["entries"][0]["amount"] == "75.50"
