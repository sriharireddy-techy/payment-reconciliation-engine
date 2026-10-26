"""Reconciliation matching rules, repeat-run behaviour and the simulated-record generator."""

import random
from decimal import Decimal

from services.reconciliation_service import (
    SIMULATED_PREFIX,
    Finding,
    classify,
    plan_simulated_records,
    split_new,
)

GW = 1


def internal(tid, ref, amount="100.00", status="SUCCESS", gateway_id=GW, has_external=1):
    return {"transaction_id": tid, "transaction_ref": ref, "amount": Decimal(amount), "status": status,
            "gateway_id": gateway_id, "has_external": has_external}


def external(eid, ref, amount="100.00", status="SUCCESS"):
    return {"ext_transaction_id": eid, "external_reference_id": f"SIM-G-{eid}", "internal_transaction_ref": ref,
            "reported_amount": Decimal(amount), "reported_status": status}


def types(result):
    return sorted(f.exception_type for f in result.findings)


def test_exact_match():
    result = classify(GW, [external(10, "r1")], {"r1": internal(1, "r1")}, [internal(1, "r1")])
    assert result.matched_records == 1 and result.findings == [] and result.total_records == 1


def test_amount_mismatch_records_signed_difference():
    result = classify(GW, [external(10, "r1", amount="90.00")], {"r1": internal(1, "r1")}, [])
    (finding,) = result.findings
    assert finding.exception_type == "AMOUNT_MISMATCH"
    assert finding.discrepancy_amount == Decimal("-10.00")
    assert (finding.internal_transaction_id, finding.ext_transaction_id) == (1, 10)


def test_status_mismatch():
    result = classify(GW, [external(10, "r1", status="FAILURE")], {"r1": internal(1, "r1")}, [])
    assert types(result) == ["STATUS_MISMATCH"]
    assert result.matched_records == 0


def test_amount_and_status_mismatch_together():
    result = classify(GW, [external(10, "r1", amount="5.00", status="PENDING")], {"r1": internal(1, "r1")}, [])
    assert types(result) == ["AMOUNT_MISMATCH", "STATUS_MISMATCH"]


def test_status_mapping_for_failed_and_reversed():
    ext = [external(10, "a", status="FAILURE"), external(11, "b", status="REFUNDED")]
    ints = {"a": internal(1, "a", status="FAILED"), "b": internal(2, "b", status="REVERSED")}
    result = classify(GW, ext, ints, [])
    assert result.findings == [] and result.matched_records == 2


def test_missing_internal_for_unknown_and_empty_reference():
    result = classify(GW, [external(10, "nope"), external(11, None)], {}, [])
    assert types(result) == ["MISSING_IN_INTERNAL", "MISSING_IN_INTERNAL"]
    assert result.findings[0].discrepancy_amount == Decimal("100.00")
    assert result.findings[0].internal_transaction_id is None


def test_reference_from_another_gateway_is_missing_internal():
    result = classify(GW, [external(10, "r1")], {"r1": internal(1, "r1", gateway_id=2)}, [])
    assert types(result) == ["MISSING_IN_INTERNAL"]


def test_missing_in_gateway():
    window = [internal(1, "r1", has_external=0), internal(2, "r2", has_external=1)]
    result = classify(GW, [], {}, window)
    assert types(result) == ["MISSING_IN_GATEWAY"]
    assert result.findings[0].internal_transaction_id == 1
    assert result.total_records == 1


def test_duplicate_external_record():
    ext = [external(10, "r1"), external(11, "r1")]
    result = classify(GW, ext, {"r1": internal(1, "r1")}, [])
    assert result.matched_records == 1 and result.findings == []
    (dup,) = result.duplicates
    assert dup.exception_type == "DUPLICATE_EXTERNAL" and dup.ext_transaction_id == 11


def test_repeat_run_does_not_store_duplicates():
    findings = [Finding("AMOUNT_MISMATCH", 1, 10, Decimal("5"), "x"), Finding("MISSING_IN_GATEWAY", 2, None, Decimal("9"), "y")]
    existing = {("AMOUNT_MISMATCH", 1, 10): {"batch_id": 4, "resolution_status": "UNRESOLVED"}}
    new, previous = split_new(findings, existing)
    assert [f.exception_type for f in new] == ["MISSING_IN_GATEWAY"]
    assert previous[0]["batch_id"] == 4
    # A second identical run finds everything already recorded.
    existing.update({f.key: {"batch_id": 5, "resolution_status": "UNRESOLVED"} for f in new})
    assert split_new(findings, existing)[0] == []


CANDIDATES = [
    {"transaction_id": i, "transaction_ref": f"ref-{i}", "amount": Decimal("100.00"), "fee_amount": Decimal("1.50"),
     "status": "SUCCESS" if i % 4 else "FAILED"}
    for i in range(1, 13)
]


def plan(seed=7, **counts):
    defaults = dict(amount_mismatches=1, status_mismatches=1, missing_in_gateway=1, missing_in_internal=1, duplicates=1)
    return plan_simulated_records(CANDIDATES, rng=random.Random(seed), **{**defaults, **counts})


def test_generator_injects_requested_anomalies():
    records, omitted = plan()
    injected = [r["injected"] for r in records]
    assert injected.count("AMOUNT_MISMATCH") == 1
    assert injected.count("STATUS_MISMATCH") == 1
    assert injected.count("DUPLICATE_EXTERNAL") == 1
    assert injected.count("MISSING_IN_INTERNAL") == 1
    assert len(omitted) == 1 and omitted[0]["status"] == "SUCCESS"


def test_generated_records_are_labelled_and_valid():
    records, omitted = plan()
    refs = [r["external_reference_id"] for r in records]
    assert all(ref.startswith(SIMULATED_PREFIX) for ref in refs)
    assert len(set(refs)) == len(refs)
    assert all(r["reported_amount"] > 0 for r in records)
    omitted_refs = {o["transaction_ref"] for o in omitted}
    assert not omitted_refs & {r["internal_transaction_ref"] for r in records}
    known = {c["transaction_ref"] for c in CANDIDATES}
    unknown = [r for r in records if r["injected"] == "MISSING_IN_INTERNAL"]
    assert unknown and all(r["internal_transaction_ref"] not in known for r in unknown)


def test_generated_file_reconciles_to_the_injected_anomalies():
    """Feed the generator's output into the matcher: it must find exactly what was injected."""
    records, omitted = plan(seed=3)
    ext = [{**r, "ext_transaction_id": i + 100} for i, r in enumerate(records)]
    ints = {c["transaction_ref"]: {**c, "gateway_id": GW} for c in CANDIDATES}
    in_file = {r["internal_transaction_ref"] for r in records}
    window = [{**c, "has_external": int(c["transaction_ref"] in in_file)} for c in CANDIDATES if c["status"] == "SUCCESS"]
    result = classify(GW, ext, ints, window)
    assert types(result) == ["AMOUNT_MISMATCH", "MISSING_IN_GATEWAY", "MISSING_IN_INTERNAL", "STATUS_MISMATCH"]
    assert len(result.duplicates) == 1


def test_generator_selection_is_repeatable_with_a_seed():
    a, _ = plan(seed=11)
    b, _ = plan(seed=11)
    assert [(r["transaction_id"], r["injected"]) for r in a] == [(r["transaction_id"], r["injected"]) for r in b]


def test_reconciliation_request_validation(client):
    assert client.post("/api/reconciliation/batches", json={"gateway_id": 1, "batch_date": "25-10-2026"}).status_code == 422
    assert client.post("/api/reconciliation/simulate-external",
                       json={"gateway_id": 1, "settlement_date": "2026-10-25", "duplicates": 99}).status_code == 422
    assert client.post("/api/reconciliation/external-records",
                       json={"gateway_id": 1, "settlement_date": "2026-10-25", "reported_amount": "5.00",
                             "reported_status": "SETTLED"}).status_code == 422
    assert client.patch("/api/reconciliation/exceptions/1", json={"resolution_status": "AUTO_RESOLVED"}).status_code == 422
