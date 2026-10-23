"""Human approval decision rules."""

from agent.storage import record_approval


def apply_approval(db_path: str, run_id: str, approver: str, approved: bool) -> dict:
    """Require an approver identity and persist a single explicit decision."""
    if not approver.strip():
        raise ValueError("Approver identity is required")
    return record_approval(db_path, run_id, approver.strip(), approved)
