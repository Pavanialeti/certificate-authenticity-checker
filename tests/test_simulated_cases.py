import json
import sqlite3

from agent.mock_source import SyntheticMockSourceAdapter
from agent.test_cases import run_all_test_cases


def test_tc1_matching_record_is_verified(tmp_path):
    adapter = SyntheticMockSourceAdapter()
    results = run_all_test_cases(tmp_path / "cases.sqlite3", adapter)
    result = next(row for row in results if row["test_case_id"] == "TC1")
    assert result["actual_result"] == "VERIFIED"
    assert result["label"] == "SIMULATED"
    assert result["evidence_reference"].startswith("SIMULATED:")


def test_tc2_candidate_name_conflict_is_explained(tmp_path):
    results = run_all_test_cases(tmp_path / "cases.sqlite3")
    result = next(row for row in results if row["test_case_id"] == "TC2")
    assert result["actual_result"] == "MISMATCH"
    assert "candidate name" in result["reason"]
    assert "Casey Example" in result["reason"]
    assert "Jordan Example" in result["reason"]


def test_tc3_no_record_is_not_found_never_fake(tmp_path):
    results = run_all_test_cases(tmp_path / "cases.sqlite3")
    result = next(row for row in results if row["test_case_id"] == "TC3")
    assert result["actual_result"] == "NOT_FOUND"
    assert result["actual_result"] != "FAKE"
    assert "does not mean FAKE" in result["reason"]


def test_tc4_missing_consent_never_calls_source_adapter(tmp_path):
    adapter = SyntheticMockSourceAdapter()
    results = run_all_test_cases(tmp_path / "cases.sqlite3", adapter)
    result = next(row for row in results if row["test_case_id"] == "TC4")
    assert result["actual_result"] == "CONSENT_REQUIRED"
    assert adapter.lookup_attempts == 3
    assert "not attempted" in result["reason"]


def test_all_cases_are_persisted_with_required_fields_and_simulated_label(tmp_path):
    db_path = tmp_path / "cases.sqlite3"
    results = run_all_test_cases(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT test_case_id, input_json, expected_result, actual_result, run_id, reason, evidence_reference, label FROM simulated_test_outcomes ORDER BY test_case_id"
        ).fetchall()
    assert len(rows) == 4
    assert len({row[4] for row in rows}) == 4
    for row, result in zip(rows, results):
        assert row[0] == result["test_case_id"]
        assert json.loads(row[1]) == result["input"]
        assert row[2] == result["expected_result"]
        assert row[3] == result["actual_result"]
        assert row[4] == result["run_id"]
        assert row[5] == result["reason"]
        assert row[6] == result["evidence_reference"]
        assert row[7] == "SIMULATED"


def test_synthetic_records_are_labelled_and_mock_does_not_capture_screenshots(tmp_path):
    adapter = SyntheticMockSourceAdapter()
    assert all(row["label"] == "SIMULATED" for row in adapter.records)
    assert not hasattr(adapter, "capture_screenshot")
    results = run_all_test_cases(tmp_path / "cases.sqlite3", adapter)
    assert all("screenshot" not in (result["evidence_reference"] or "").casefold() for result in results)
