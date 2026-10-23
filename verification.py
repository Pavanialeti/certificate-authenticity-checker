"""Consent-gated, deterministic certificate claim evaluation."""

from datetime import date
from typing import Any, Callable

from database import record_decision, record_evidence, save_claim

OUTCOMES = {"VERIFIED", "MISMATCH", "NOT_FOUND", "NEEDS_REVIEW", "ERROR", "CONSENT_REQUIRED"}
REQUIRED_FIELDS = ("credential_id", "issuer", "candidate_name", "course", "completion_date")
COMPARE_FIELDS = REQUIRED_FIELDS


def validate_claim(claim: dict[str, Any]) -> list[str]:
    errors = [f"{field} is required" for field in REQUIRED_FIELDS if not str(claim.get(field, "")).strip()]
    completion_date = str(claim.get("completion_date", "")).strip()
    if completion_date:
        try:
            date.fromisoformat(completion_date)
        except ValueError:
            errors.append("completion_date must be a valid ISO date (YYYY-MM-DD)")
    return errors


def _normalized(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return " ".join(str(value).split()).casefold()


def decide(claim: dict[str, Any], evidence: list[dict[str, Any]], *, source_error: bool = False,
           authoritative_search_complete: bool = False) -> tuple[str, str]:
    """Compare only present fields. Missing values are inconclusive, never matches."""
    if source_error:
        return "ERROR", "The source could not be checked safely."
    if not evidence:
        if authoritative_search_complete:
            return "NOT_FOUND", "The completed authoritative search returned no record."
        return "NEEDS_REVIEW", "No usable source evidence is available."
    saw_missing = False
    for item in evidence:
        fields = item.get("fields") or {}
        for field in COMPARE_FIELDS:
            actual = _normalized(fields.get(field))
            expected = _normalized(claim.get(field))
            if actual is None:
                saw_missing = True
            elif expected is not None and actual != expected:
                display_field = {"candidate_name": "candidate name", "credential_id": "credential ID",
                                 "completion_date": "completion date"}.get(field, field.replace("_", " "))
                return "MISMATCH", f"Conflicting {display_field}: the claimed value does not match the source evidence."
    if saw_missing:
        return "NEEDS_REVIEW", "Evidence is missing one or more required comparison fields."
    return "VERIFIED", "All required fields exactly match source evidence after whitespace and case normalization."


def run_verification(db_path: str, claim: dict[str, Any], consent: bool,
                     source_fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                     browser_navigator: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Validate, persist, and evaluate. Source and browser callbacks are never touched absent consent."""
    errors = validate_claim(claim)
    if errors:
        return {"status": "ERROR", "errors": errors, "claim_id": None, "run_id": None}
    claim_id, run_id = save_claim(db_path, claim, consent)
    if not consent:
        return {"status": "CONSENT_REQUIRED", "claim_id": claim_id, "run_id": run_id,
                "message": "Candidate consent is required before checking any source."}

    evidence_items: list[dict[str, Any]] = []
    source_error = False
    authoritative_search_complete = False
    try:
        if source_fetcher is not None:
            result = source_fetcher(claim)
            evidence_items = result.get("evidence", [])
            authoritative_search_complete = bool(result.get("authoritative_search_complete", False))
            for evidence in evidence_items:
                record_evidence(db_path, run_id, evidence)
                url = evidence.get("url")
                if url and browser_navigator is not None:
                    browser_navigator(url)
        else:
            # No issuer source is configured; absence of a connector is not proof of absence.
            evidence_items = []
    except Exception:
        source_error = True
    outcome, rationale = decide(claim, evidence_items, source_error=source_error,
                                authoritative_search_complete=authoritative_search_complete)
    record_decision(db_path, run_id, outcome, rationale)
    return {"status": outcome, "claim_id": claim_id, "run_id": run_id, "message": rationale}
