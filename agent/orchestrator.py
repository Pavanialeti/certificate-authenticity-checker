"""Stateful approval-gated certificate verification workflow."""

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent.approval import apply_approval
from agent.sources import SourceRegistry
from agent.storage import (claim_execution, create_run, get_run, get_transitions,
                           initialize, save_failure, save_result)
from verification import decide, validate_claim

FINAL_STATES = {"VERIFIED", "MISMATCH", "NOT_FOUND", "NEEDS_REVIEW", "ERROR", "REJECTED", "CONSENT_REQUIRED"}


class VerificationOrchestrator:
    def __init__(self, db_path: str | Path, sources: SourceRegistry | None = None):
        self.db_path = str(db_path)
        self.sources = sources or SourceRegistry()
        initialize(self.db_path)

    def submit(self, claim: dict[str, Any], *, consent: bool, source: str,
               destination_url: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Create a persisted proposed action. Does not call a source adapter."""
        key = idempotency_key or str(uuid.uuid4())
        parsed = urlparse(destination_url)
        if not source.strip() or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors = ["A source and valid HTTP(S) destination URL are required"]
        else:
            errors = validate_claim(claim)
        proposed = f"Look up credential {claim.get('credential_id', '')!r} against {source} at {destination_url}"
        run_id, action_id = str(uuid.uuid4()), str(uuid.uuid4())
        if not consent:
            initial = "CONSENT_REQUIRED"
        elif errors:
            initial = "ERROR"
        else:
            initial = "AWAITING_APPROVAL"
        run_id = create_run(self.db_path, run_id=run_id, action_id=action_id,
                            idempotency_key=key, claim_json=json.dumps(claim), consent=consent,
                            source=source, destination_url=destination_url, proposed_action=proposed,
                            state=initial, errors=errors if consent else [])
        return self.get_run(run_id)

    def approve(self, run_id: str, *, approver: str, approved: bool) -> dict[str, Any]:
        """Persist approval/rejection and execute approved action at most once."""
        run = self.get_run(run_id)
        if run is None:
            raise KeyError("Verification run not found")
        if run["state"] == "AWAITING_APPROVAL":
            decision = apply_approval(self.db_path, run_id, approver, approved)
            if not decision["changed"]:
                return self.get_run(run_id)
            if decision["state"] == "REJECTED":
                return self.get_run(run_id)
        run = self.get_run(run_id)
        if run["state"] != "APPROVED":
            return run

        # Atomic insert serializes repeated clicks and concurrent retries.
        if not claim_execution(self.db_path, run_id):
            return self.get_run(run_id)

        try:
            # This is the sole external source call, reached only after durable human approval.
            result = self.sources.lookup(run["source"], run["destination_url"], run["claim"], run_id=run_id)
            evidence = result.get("evidence", [])
            outcome, rationale = decide(run["claim"], evidence,
                source_error=bool(result.get("error")),
                authoritative_search_complete=bool(result.get("authoritative_search_complete")))
            save_result(self.db_path, run_id, json.dumps({"evidence": evidence, "rationale": rationale,
                                                        "source_reason": result.get("reason"),
                                                        "label": result.get("label")}),
                        outcome, rationale)
            return self.get_run(run_id)
        except Exception:
            # Keep exception details out of user-facing state and database messages.
            save_failure(self.db_path, run_id)
            return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return get_run(self.db_path, run_id)

    def history(self, run_id: str) -> list[dict[str, Any]]:
        return get_transitions(self.db_path, run_id)

