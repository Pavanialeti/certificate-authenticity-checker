import sqlite3

from verification import decide, run_verification, validate_claim
from database import initialize_database


CLAIM = {
    "credential_id": "CERT-1", "issuer": "Example University",
    "candidate_name": "Ada Lovelace", "course": "Mathematics",
    "completion_date": "2026-01-10",
}


def test_validation_requires_all_fields_and_iso_date():
    assert validate_claim(CLAIM) == []
    assert "issuer is required" in validate_claim({**CLAIM, "issuer": "  "})
    assert "completion_date must be a valid ISO date (YYYY-MM-DD)" in validate_claim(
        {**CLAIM, "completion_date": "10/01/2026"}
    )


def test_consent_absent_stops_before_source_and_browser(tmp_path):
    db = tmp_path / "claims.sqlite3"
    initialize_database(db)
    calls = []
    result = run_verification(db, CLAIM, False,
        source_fetcher=lambda _claim: calls.append("source"),
        browser_navigator=lambda _url: calls.append("browser"))
    assert result["status"] == "CONSENT_REQUIRED"
    assert calls == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM verification_runs").fetchone()[0] == "CONSENT_REQUIRED"
        assert conn.execute("SELECT given FROM consents").fetchone()[0] == 0


def test_invalid_claim_never_invokes_source(tmp_path):
    initialize_database(tmp_path / "claims.sqlite3")
    calls = []
    result = run_verification(str(tmp_path / "claims.sqlite3"), {**CLAIM, "course": ""}, True,
                              source_fetcher=lambda _: calls.append(1))
    assert result["status"] == "ERROR"
    assert calls == []


def test_decision_rules_never_treat_missing_evidence_as_match():
    assert decide(CLAIM, [])[0] == "NEEDS_REVIEW"
    assert decide(CLAIM, [], authoritative_search_complete=True)[0] == "NOT_FOUND"
    assert decide(CLAIM, [], source_error=True)[0] == "ERROR"
    assert decide(CLAIM, [{"fields": {"credential_id": CLAIM["credential_id"]}}])[0] == "NEEDS_REVIEW"
    mismatch = {"fields": {**CLAIM, "candidate_name": "Grace Hopper"}}
    assert decide(CLAIM, [mismatch])[0] == "MISMATCH"
    assert decide(CLAIM, [{"fields": {key: f"  {value.upper()}  " for key, value in CLAIM.items()}}])[0] == "VERIFIED"


def test_consented_run_without_source_is_review_not_not_found(tmp_path):
    db = tmp_path / "claims.sqlite3"
    initialize_database(db)
    result = run_verification(str(db), CLAIM, True)
    assert result["status"] == "NEEDS_REVIEW"
