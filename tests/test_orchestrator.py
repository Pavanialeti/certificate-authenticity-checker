from agent.orchestrator import VerificationOrchestrator
from agent.sources import SourceRegistry


CLAIM = {
    "credential_id": "CERT-77", "issuer": "Northstar University",
    "candidate_name": "Morgan Lee", "course": "Data Science",
    "completion_date": "2026-04-10",
}
URL = "https://issuer.example/verify"


class CountingAdapter:
    def __init__(self, result=None, fail=False):
        self.calls = 0
        self.result = result or {"evidence": [{"fields": dict(CLAIM)}]}
        self.fail = fail

    def lookup(self, destination_url, claim, *, run_id):
        self.calls += 1
        assert destination_url == URL
        assert claim == CLAIM
        if self.fail:
            raise RuntimeError("private adapter error")
        return self.result


def make(tmp_path, adapter=None):
    adapter = adapter or CountingAdapter()
    orchestrator = VerificationOrchestrator(
        tmp_path / "orchestration.sqlite3", SourceRegistry({"issuer_registry": adapter})
    )
    return orchestrator, adapter


def submit(orchestrator, **kwargs):
    return orchestrator.submit(CLAIM, consent=True, source="issuer_registry",
                               destination_url=URL, **kwargs)


def test_missing_consent_never_looks_up(tmp_path):
    orchestrator, adapter = make(tmp_path)
    run = orchestrator.submit(CLAIM, consent=False, source="issuer_registry", destination_url=URL)
    assert run["state"] == "CONSENT_REQUIRED"
    assert adapter.calls == 0
    assert orchestrator.history(run["run_id"])[-1]["to_state"] == "CONSENT_REQUIRED"


def test_pending_approval_never_navigates_and_shows_proposal(tmp_path):
    orchestrator, adapter = make(tmp_path)
    run = submit(orchestrator)
    assert run["state"] == "AWAITING_APPROVAL"
    assert "issuer_registry" in run["proposed_action"]
    assert run["destination_url"] == URL
    assert adapter.calls == 0


def test_rejection_never_looks_up(tmp_path):
    orchestrator, adapter = make(tmp_path)
    run = submit(orchestrator)
    rejected = orchestrator.approve(run["run_id"], approver="reviewer-1", approved=False)
    assert rejected["state"] == "REJECTED"
    assert adapter.calls == 0


def test_approval_captures_evidence_then_decides(tmp_path):
    orchestrator, adapter = make(tmp_path)
    run = submit(orchestrator)
    result = orchestrator.approve(run["run_id"], approver="reviewer-1", approved=True)
    assert result["state"] == "VERIFIED"
    assert adapter.calls == 1
    states = [entry["to_state"] for entry in orchestrator.history(run["run_id"])]
    assert states == ["SUBMITTED", "VALIDATED", "AWAITING_APPROVAL", "APPROVED",
                      "LOOKUP_RUNNING", "EVIDENCE_CAPTURED", "VERIFIED"]


def test_duplicate_submission_and_execution_are_idempotent(tmp_path):
    orchestrator, adapter = make(tmp_path)
    first = submit(orchestrator, idempotency_key="same-request")
    second = submit(orchestrator, idempotency_key="same-request")
    assert second["run_id"] == first["run_id"]
    orchestrator.approve(first["run_id"], approver="reviewer-1", approved=True)
    repeated = orchestrator.approve(first["run_id"], approver="reviewer-1", approved=True)
    assert repeated["state"] == "VERIFIED"
    assert adapter.calls == 1
    assert [x["to_state"] for x in orchestrator.history(first["run_id"])].count("LOOKUP_RUNNING") == 1


def test_adapter_failure_is_safe_and_persisted(tmp_path):
    orchestrator, adapter = make(tmp_path, CountingAdapter(fail=True))
    run = submit(orchestrator)
    result = orchestrator.approve(run["run_id"], approver="reviewer-1", approved=True)
    assert result["state"] == "ERROR"
    assert result["error_code"] == "SOURCE_FAILURE"
    assert result["error_message"] == "Source lookup failed"
    assert "private adapter error" not in str(result)
    assert adapter.calls == 1
