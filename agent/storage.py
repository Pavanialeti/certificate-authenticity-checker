"""SQLite operations for stateful verification runs and approval actions."""

import json
import sqlite3
from pathlib import Path
from typing import Any


def connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS orchestrator_runs (
            run_id TEXT PRIMARY KEY,
            action_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            claim_json TEXT NOT NULL,
            consent INTEGER NOT NULL CHECK(consent IN (0,1)),
            source TEXT NOT NULL,
            destination_url TEXT NOT NULL,
            proposed_action TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            result_json TEXT,
            error_code TEXT,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS orchestrator_transitions (
            transition_id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES orchestrator_runs(run_id),
            from_state TEXT,
            to_state TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS orchestrator_approvals (
            action_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE REFERENCES orchestrator_runs(run_id),
            status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
            approver TEXT,
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orchestrator_execution_claims (
            action_id TEXT PRIMARY KEY REFERENCES orchestrator_runs(action_id),
            claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS simulated_test_outcomes (
            id INTEGER PRIMARY KEY,
            test_case_id TEXT NOT NULL,
            input_json TEXT NOT NULL,
            expected_result TEXT NOT NULL,
            actual_result TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE,
            reason TEXT NOT NULL,
            evidence_reference TEXT,
            label TEXT NOT NULL CHECK(label='SIMULATED'),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS live_source_evidence (
            evidence_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES orchestrator_runs(run_id),
            source_url TEXT NOT NULL,
            captured_at_utc TEXT NOT NULL,
            extracted_json TEXT NOT NULL,
            evidence_type TEXT NOT NULL CHECK(evidence_type='LIVE SOURCE'),
            screenshot BLOB NOT NULL,
            screenshot_label TEXT NOT NULL CHECK(screenshot_label='LIVE SOURCE'),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)


def create_run(db_path: str | Path, *, run_id: str, action_id: str, idempotency_key: str,
               claim_json: str, consent: bool, source: str, destination_url: str,
               proposed_action: str, state: str, errors: list[str]) -> str:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT run_id FROM orchestrator_runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            return existing["run_id"]
        conn.execute(
            "INSERT INTO orchestrator_runs (run_id, action_id, idempotency_key, claim_json, consent, source, destination_url, proposed_action, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SUBMITTED')",
            (run_id, action_id, idempotency_key, claim_json, int(consent), source, destination_url, proposed_action),
        )
        conn.execute("INSERT INTO orchestrator_transitions (run_id, from_state, to_state, note) VALUES (?, NULL, 'SUBMITTED', 'Claim submitted')", (run_id,))
        if state == "CONSENT_REQUIRED":
            _transition(conn, run_id, "SUBMITTED", state, "Candidate consent was not provided")
        elif errors:
            _transition(conn, run_id, "SUBMITTED", "ERROR", "; ".join(errors))
            conn.execute("UPDATE orchestrator_runs SET error_code='INVALID_INPUT', error_message='Claim validation failed' WHERE run_id=?", (run_id,))
        else:
            _transition(conn, run_id, "SUBMITTED", "VALIDATED", "Required claim fields validated")
            _transition(conn, run_id, "VALIDATED", "AWAITING_APPROVAL", "Action proposal is ready for human review")
            conn.execute("INSERT INTO orchestrator_approvals (action_id, run_id, status) VALUES (?, ?, 'PENDING')", (action_id, run_id))
    return run_id


def _transition(conn: sqlite3.Connection, run_id: str, expected: str, target: str, note: str) -> None:
    cursor = conn.execute("UPDATE orchestrator_runs SET state=?, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND state=?",
                          (target, run_id, expected))
    if cursor.rowcount != 1:
        raise RuntimeError("Verification state changed unexpectedly")
    conn.execute("INSERT INTO orchestrator_transitions (run_id, from_state, to_state, note) VALUES (?, ?, ?, ?)",
                 (run_id, expected, target, note))


def transition(db_path: str | Path, run_id: str, expected: str, target: str, note: str) -> bool:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE orchestrator_runs SET state=?, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND state=?",
                              (target, run_id, expected))
        if cursor.rowcount != 1:
            return False
        conn.execute("INSERT INTO orchestrator_transitions (run_id, from_state, to_state, note) VALUES (?, ?, ?, ?)",
                     (run_id, expected, target, note))
        return True


def claim_execution(db_path: str | Path, run_id: str) -> bool:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT action_id FROM orchestrator_runs WHERE run_id=? AND state='APPROVED'", (run_id,)).fetchone()
        if row is None:
            return False
        cursor = conn.execute("INSERT OR IGNORE INTO orchestrator_execution_claims (action_id) VALUES (?)", (row["action_id"],))
        if cursor.rowcount != 1:
            return False
        _transition(conn, run_id, "APPROVED", "LOOKUP_RUNNING", "Approved action claimed for execution")
        return True


def save_result(db_path: str | Path, run_id: str, result_json: str,
                outcome: str, rationale: str) -> None:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE orchestrator_runs SET result_json=? WHERE run_id=?", (result_json, run_id))
        _transition(conn, run_id, "LOOKUP_RUNNING", "EVIDENCE_CAPTURED", "Source evidence captured")
        _transition(conn, run_id, "EVIDENCE_CAPTURED", outcome, rationale)


def save_failure(db_path: str | Path, run_id: str) -> None:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT state FROM orchestrator_runs WHERE run_id=?", (run_id,)).fetchone()
        if current and current["state"] == "LOOKUP_RUNNING":
            _transition(conn, run_id, "LOOKUP_RUNNING", "ERROR", "Source lookup failed")
            conn.execute("UPDATE orchestrator_runs SET error_code='SOURCE_FAILURE', error_message='Source lookup failed' WHERE run_id=?", (run_id,))


def record_approval(db_path: str | Path, run_id: str, approver: str, approved: bool) -> dict[str, Any]:
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT state FROM orchestrator_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise KeyError("Verification run not found")
        if run["state"] != "AWAITING_APPROVAL":
            return {"state": run["state"], "changed": False}
        state = "APPROVED" if approved else "REJECTED"
        cursor = conn.execute("UPDATE orchestrator_approvals SET status=?, approver=?, decided_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='PENDING'",
                              (state, approver, run_id))
        if cursor.rowcount != 1:
            return {"state": run["state"], "changed": False}
        _transition(conn, run_id, "AWAITING_APPROVAL", state, f"Human decision by {approver}")
        return {"state": state, "changed": True}


def save_simulated_test_outcome(db_path: str | Path, outcome: dict[str, Any]) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO simulated_test_outcomes (test_case_id, input_json, expected_result, actual_result, run_id, reason, evidence_reference, label) VALUES (?, ?, ?, ?, ?, ?, ?, 'SIMULATED')",
            (outcome["test_case_id"], json.dumps(outcome["input"]), outcome["expected_result"],
             outcome["actual_result"], outcome["run_id"], outcome["reason"], outcome.get("evidence_reference")),
        )


def save_live_source_evidence(db_path: str | Path, *, evidence_id: str, run_id: str,
                              source_url: str, captured_at_utc: str,
                              extracted: dict[str, Any], screenshot: bytes) -> None:
    """Persist a real public page capture with explicit LIVE SOURCE labels."""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO live_source_evidence (evidence_id, run_id, source_url, captured_at_utc, extracted_json, evidence_type, screenshot, screenshot_label) VALUES (?, ?, ?, ?, ?, 'LIVE SOURCE', ?, 'LIVE SOURCE')",
            (evidence_id, run_id, source_url, captured_at_utc, json.dumps(extracted), sqlite3.Binary(screenshot)),
        )


def assert_live_lookup_authorized(db_path: str | Path, run_id: str, destination_url: str) -> None:
    """Defense in depth: permit live browser use only for the consented approved run URL."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT consent, source, destination_url, state FROM orchestrator_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if (row is None or row["consent"] != 1 or row["source"] != "public_page"
            or row["destination_url"] != destination_url or row["state"] != "LOOKUP_RUNNING"):
        raise PermissionError("Live source access is not authorized for this verification run")


def transition(conn: sqlite3.Connection, run_id: str, current: str | None,
               target: str, note: str | None = None) -> bool:
    """Compare-and-set run state and append an audit transition in one transaction."""
    if current is None:
        cursor = conn.execute(
            "UPDATE orchestrator_runs SET state=?, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND state=?",
            (target, run_id, target),
        )
        # Initial SUBMITTED state is created together with its first transition.
        return cursor.rowcount == 1
    cursor = conn.execute(
        "UPDATE orchestrator_runs SET state=?, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND state=?",
        (target, run_id, current),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        "INSERT INTO orchestrator_transitions (run_id, from_state, to_state, note) VALUES (?, ?, ?, ?)",
        (run_id, current, target, note),
    )
    return True


def get_run(db_path: str | Path, run_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM orchestrator_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        run = dict(row)
        run["claim"] = json.loads(run.pop("claim_json"))
        run["result"] = json.loads(run.pop("result_json")) if run.get("result_json") else None
        return run


def get_transitions(db_path: str | Path, run_id: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT from_state, to_state, note, created_at FROM orchestrator_transitions WHERE run_id=? ORDER BY transition_id",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]
