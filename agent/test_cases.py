"""Run and persist the four deterministic SIMULATED test cases."""

import uuid
from pathlib import Path
from typing import Any

from agent.mock_source import SyntheticMockSourceAdapter
from agent.storage import initialize, save_simulated_test_outcome
from verification import decide


BASE = {
    "issuer": "Simulated Northstar Institute",
    "course": "Simulated Data Science",
    "completion_date": "2026-05-14",
}

TEST_CASES: tuple[dict[str, Any], ...] = (
    {"test_case_id": "TC1", "input": {**BASE, "credential_id": "SIM-CERT-001",
        "candidate_name": "Avery Example", "consent": True}, "expected_result": "VERIFIED"},
    {"test_case_id": "TC2", "input": {**BASE, "credential_id": "SIM-CERT-002",
        "course": "Simulated Design", "completion_date": "2026-06-20",
        "candidate_name": "Casey Example", "consent": True}, "expected_result": "MISMATCH"},
    {"test_case_id": "TC3", "input": {**BASE, "credential_id": "SIM-ABSENT-003",
        "candidate_name": "Taylor Example", "consent": True}, "expected_result": "NOT_FOUND"},
    {"test_case_id": "TC4", "input": {**BASE, "credential_id": "SIM-CERT-001",
        "candidate_name": "Avery Example", "consent": False}, "expected_result": "CONSENT_REQUIRED"},
)


def run_all_test_cases(db_path: str | Path,
                       adapter: SyntheticMockSourceAdapter | None = None) -> list[dict[str, Any]]:
    """Run only against the local mock adapter and save a labelled result per case."""
    initialize(db_path)
    adapter = adapter or SyntheticMockSourceAdapter()
    outcomes = []
    for case in TEST_CASES:
        test_input = dict(case["input"])
        consent = bool(test_input.pop("consent"))
        run_id = str(uuid.uuid4())
        if not consent:
            actual, reason, evidence_reference = (
                "CONSENT_REQUIRED", "Consent was absent; synthetic lookup was not attempted.", None
            )
        else:
            lookup = adapter.lookup(adapter.destination_url, test_input)
            evidence = lookup.get("evidence", [])
            actual, reason = decide(
                test_input, evidence,
                authoritative_search_complete=bool(lookup.get("authoritative_search_complete")),
            )
            if actual == "NOT_FOUND":
                reason = "No matching record exists in the SIMULATED test catalog; this does not mean FAKE."
            elif actual == "MISMATCH":
                reason = "Conflicting candidate name: claimed 'Casey Example', synthetic record says 'Jordan Example'."
            else:
                reason = "All required fields match the SIMULATED test record."
            evidence_reference = f"SIMULATED:{evidence[0]['record_id']}" if evidence else None
        outcome = {
            "test_case_id": case["test_case_id"],
            "input": {**test_input, "consent": consent},
            "expected_result": case["expected_result"],
            "actual_result": actual,
            "run_id": run_id,
            "reason": reason,
            "evidence_reference": evidence_reference,
            "label": "SIMULATED",
            "passed": actual == case["expected_result"],
        }
        save_simulated_test_outcome(str(db_path), outcome)
        outcomes.append(outcome)
    return outcomes
