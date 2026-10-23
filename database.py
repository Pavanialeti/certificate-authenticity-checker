"""SQLite persistence for demo records and certificate verification workflows."""

import json
import sqlite3
from pathlib import Path
from typing import Any

DEMO_CERTIFICATES = (
    ("DEMO-2026-001", "Sample Candidate One", "Northstar Learning Institute", "2026-03-12"),
    ("DEMO-2026-002", "Sample Candidate Two", "Example Technical Academy", "2026-04-05"),
    ("DEMO-2026-003", "Sample Candidate Three", "Sample Institute of Design", "2026-05-18"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_certificates (
    certificate_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, issuer TEXT NOT NULL,
    issued_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificate_claims (
    id INTEGER PRIMARY KEY, credential_id TEXT NOT NULL, issuer TEXT NOT NULL,
    candidate_name TEXT NOT NULL, course TEXT NOT NULL, completion_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS consents (
    id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES certificate_claims(id),
    given INTEGER NOT NULL CHECK(given IN (0,1)), recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS verification_runs (
    id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL REFERENCES certificate_claims(id),
    status TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT, error_code TEXT, error_message TEXT
);
CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES verification_runs(id),
    from_state TEXT, to_state TEXT NOT NULL, note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_evidence (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES verification_runs(id),
    source TEXT NOT NULL, evidence_type TEXT NOT NULL, url TEXT, observed_at TEXT,
    content TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS extracted_fields (
    id INTEGER PRIMARY KEY, evidence_id INTEGER NOT NULL REFERENCES source_evidence(id),
    field_name TEXT NOT NULL, field_value TEXT, confidence REAL, extraction_method TEXT
);
CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES verification_runs(id),
    evidence_id INTEGER REFERENCES source_evidence(id), mime_type TEXT NOT NULL,
    image_data BLOB NOT NULL, captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, description TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES verification_runs(id),
    approver TEXT NOT NULL, approved INTEGER NOT NULL CHECK(approved IN (0,1)),
    note TEXT, decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL UNIQUE REFERENCES verification_runs(id),
    outcome TEXT NOT NULL, rationale TEXT NOT NULL, decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS test_outcomes (
    id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES verification_runs(id),
    test_name TEXT NOT NULL, outcome TEXT NOT NULL, details TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT OR IGNORE INTO demo_certificates (certificate_id, recipient, issuer, issued_on) VALUES (?, ?, ?, ?)",
            DEMO_CERTIFICATES,
        )


def get_demo_certificates(db_path: str | Path) -> list[dict[str, str]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT certificate_id, recipient, issuer, issued_on FROM demo_certificates ORDER BY certificate_id"
        ).fetchall()
    return [dict(row) for row in rows]


def save_claim(db_path: str | Path, claim: dict[str, Any], consent: bool) -> tuple[int, int]:
    """Persist a validated claim and its explicit consent, returning claim and run IDs."""
    with connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO certificate_claims (credential_id, issuer, candidate_name, course, completion_date) VALUES (?, ?, ?, ?, ?)",
            (claim["credential_id"], claim["issuer"], claim["candidate_name"], claim["course"], claim["completion_date"]),
        )
        claim_id = cursor.lastrowid
        connection.execute("INSERT INTO consents (claim_id, given) VALUES (?, ?)", (claim_id, int(consent)))
        cursor = connection.execute(
            "INSERT INTO verification_runs (claim_id, status) VALUES (?, ?)",
            (claim_id, "PENDING" if consent else "CONSENT_REQUIRED"),
        )
        run_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO state_transitions (run_id, from_state, to_state, note) VALUES (?, NULL, ?, ?)",
            (run_id, "PENDING" if consent else "CONSENT_REQUIRED", "Claim submitted"),
        )
        if not consent:
            connection.execute(
                "INSERT INTO decisions (run_id, outcome, rationale) VALUES (?, ?, ?)",
                (run_id, "CONSENT_REQUIRED", "Candidate consent was not provided; no source access was attempted."),
            )
    return claim_id, run_id


def record_decision(db_path: str | Path, run_id: int, outcome: str, rationale: str) -> None:
    """Store a final decision and state transition. Errors are surfaced as safe RuntimeErrors."""
    try:
        with connect(db_path) as connection:
            row = connection.execute("SELECT status FROM verification_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Verification run does not exist")
            connection.execute(
                "INSERT INTO decisions (run_id, outcome, rationale) VALUES (?, ?, ?)",
                (run_id, outcome, rationale),
            )
            connection.execute(
                "INSERT INTO state_transitions (run_id, from_state, to_state, note) VALUES (?, ?, ?, ?)",
                (run_id, row["status"], outcome, rationale),
            )
            connection.execute(
                "UPDATE verification_runs SET status = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (outcome, run_id),
            )
    except (sqlite3.Error, ValueError) as exc:
        raise RuntimeError("Could not save verification decision") from exc


def record_evidence(db_path: str | Path, run_id: int, evidence: dict[str, Any]) -> int:
    """Persist source evidence and any extracted values using bound SQL parameters."""
    with connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO source_evidence (run_id, source, evidence_type, url, observed_at, content, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, evidence["source"], evidence.get("evidence_type", "record"), evidence.get("url"), evidence.get("observed_at"), evidence.get("content"), json.dumps(evidence.get("metadata", {}))),
        )
        evidence_id = cursor.lastrowid
        for name, value in evidence.get("fields", {}).items():
            connection.execute(
                "INSERT INTO extracted_fields (evidence_id, field_name, field_value) VALUES (?, ?, ?)",
                (evidence_id, name, None if value is None else str(value)),
            )
    return evidence_id


def record_screenshot(db_path: str | Path, run_id: int, image_data: bytes, mime_type: str,
                      evidence_id: int | None = None, description: str | None = None) -> int:
    with connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO screenshots (run_id, evidence_id, mime_type, image_data, description) VALUES (?, ?, ?, ?, ?)",
            (run_id, evidence_id, mime_type, sqlite3.Binary(image_data), description),
        )
        return cursor.lastrowid


def record_approval(db_path: str | Path, run_id: int, approver: str, approved: bool,
                    note: str | None = None) -> int:
    with connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO approvals (run_id, approver, approved, note) VALUES (?, ?, ?, ?)",
            (run_id, approver, int(approved), note),
        )
        return cursor.lastrowid


def record_test_outcome(db_path: str | Path, test_name: str, outcome: str,
                        run_id: int | None = None, details: str | None = None) -> int:
    with connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO test_outcomes (run_id, test_name, outcome, details) VALUES (?, ?, ?, ?)",
            (run_id, test_name, outcome, details),
        )
        return cursor.lastrowid
