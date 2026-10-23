"""Read-only queries and report shaping for the Streamlit review dashboard."""

import json
from typing import Any

from agent.storage import connect


def list_runs(db_path: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM orchestrator_runs ORDER BY created_at DESC, rowid DESC").fetchall()
    runs = []
    for item in rows:
        run = dict(item)
        run["claim"] = json.loads(run.pop("claim_json"))
        run["result"] = json.loads(run.pop("result_json")) if run.get("result_json") else None
        runs.append(run)
    return runs


def list_live_evidence(db_path: str, run_id: str | None = None, *, include_screenshot: bool = False) -> list[dict[str, Any]]:
    columns = "evidence_id, run_id, source_url, captured_at_utc, extracted_json, evidence_type, screenshot_label"
    if include_screenshot:
        columns += ", screenshot"
    query = f"SELECT {columns} FROM live_source_evidence"
    params: tuple[Any, ...] = ()
    if run_id:
        query += " WHERE run_id=?"
        params = (run_id,)
    query += " ORDER BY created_at DESC"
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for item in rows:
        record = dict(item)
        record["extracted"] = json.loads(record.pop("extracted_json"))
        result.append(record)
    return result


def list_simulated_tests(db_path: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT test_case_id, input_json, expected_result, actual_result, run_id, reason, evidence_reference, label, created_at FROM simulated_test_outcomes ORDER BY created_at DESC, id DESC"
        ).fetchall()
    results = []
    for item in rows:
        row = dict(item)
        row["input"] = json.loads(row.pop("input_json"))
        row["passed"] = row["expected_result"] == row["actual_result"]
        results.append(row)
    return results


def transition_history(db_path: str, run_id: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT from_state, to_state, note, created_at FROM orchestrator_transitions WHERE run_id=? ORDER BY transition_id",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def evidence_report_rows(runs: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_by_id = {run["run_id"]: run for run in runs}
    rows = []
    for item in evidence:
        run = run_by_id.get(item["run_id"], {})
        result = run.get("result") or {}
        rows.append({
            "label": item["evidence_type"],
            "run_id": item["run_id"],
            "status": run.get("state", ""),
            "source_url": item["source_url"],
            "retrieved_at_utc": item["captured_at_utc"],
            "evidence_type": item["evidence_type"],
            "decision_reason": result.get("rationale", ""),
            "extracted_fields": item["extracted"],
            "evidence_reference": item["evidence_id"],
        })
    return rows
