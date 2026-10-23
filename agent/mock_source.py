"""Local synthetic records for test workflows. This adapter performs no network or browser access."""

from copy import deepcopy
from typing import Any


SYNTHETIC_RECORDS: tuple[dict[str, Any], ...] = (
    {"record_id": "SIM-TC1", "label": "SIMULATED", "credential_id": "SIM-CERT-001",
     "issuer": "Simulated Northstar Institute", "candidate_name": "Avery Example",
     "course": "Simulated Data Science", "completion_date": "2026-05-14"},
    {"record_id": "SIM-TC2", "label": "SIMULATED", "credential_id": "SIM-CERT-002",
     "issuer": "Simulated Northstar Institute", "candidate_name": "Jordan Example",
     "course": "Simulated Design", "completion_date": "2026-06-20"},
)


class SyntheticMockSourceAdapter:
    """Returns labelled test records only; never treats records as issuer evidence."""

    source_name = "SIMULATED MOCK RECORDS"
    destination_url = "local://simulated-certificate-catalog"

    def __init__(self, records: tuple[dict[str, Any], ...] = SYNTHETIC_RECORDS):
        self.records = tuple(deepcopy(record) for record in records)
        self.lookup_attempts = 0

    def lookup(self, destination_url: str, claim: dict[str, Any], *, run_id: str = "") -> dict[str, Any]:
        if destination_url != self.destination_url:
            raise ValueError("Synthetic adapter only accepts its labelled local destination")
        self.lookup_attempts += 1
        record = next((r for r in self.records if r["credential_id"] == claim["credential_id"]), None)
        if record is None:
            return {"label": "SIMULATED", "evidence": [], "authoritative_search_complete": True,
                    "reason": "No record in the synthetic test catalog."}
        return {
            "label": "SIMULATED",
            "evidence": [{
                "label": "SIMULATED",
                "source": self.source_name,
                "evidence_type": "SIMULATED_TEST_RECORD",
                "record_id": record["record_id"],
                "fields": {key: record[key] for key in
                           ("credential_id", "issuer", "candidate_name", "course", "completion_date")},
            }],
            "authoritative_search_complete": True,
            "reason": "Matched a labelled synthetic test record.",
        }
