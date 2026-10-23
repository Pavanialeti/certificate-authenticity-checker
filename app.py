"""Streamlit review console for certificate claims and source evidence."""

import csv
import hashlib
import io
import json
import os
import uuid

import streamlit as st

from agent.dashboard import (evidence_report_rows, list_live_evidence, list_runs,
                             list_simulated_tests, transition_history)
from agent.mock_source import SyntheticMockSourceAdapter
from agent.orchestrator import VerificationOrchestrator
from agent.sources import SourceRegistry
from agent.test_cases import run_all_test_cases
from database import get_demo_certificates, initialize_database
from sources.public_page import PublicPageAdapter


DATABASE_PATH = os.environ.get("CERTIFICATE_CHECKER_DB_PATH", "data/demo.sqlite3")
def configured_public_hosts():
    configured = os.environ.get("PUBLIC_PAGE_ALLOWED_HOSTS", "")
    if not configured:
        try:
            configured = st.secrets.get("PUBLIC_PAGE_ALLOWED_HOSTS", "")
        except Exception:
            # Streamlit secrets are optional; the app starts and runs simulated tests without them.
            configured = ""
    return tuple(host.strip() for host in configured.split(",") if host.strip())


PUBLIC_PAGE_ALLOWED_HOSTS = configured_public_hosts()
STATUS_HELP = {
    "VERIFIED": "All required claim fields matched visible source evidence. This is a result of the configured checks, not a guarantee beyond that evidence.",
    "MISMATCH": "At least one observed field conflicts with the submitted claim. Review the field comparison and source evidence.",
    "NOT_FOUND": "A completed authoritative search returned no record. NOT_FOUND does not mean FAKE.",
    "NEEDS_REVIEW": "Evidence was incomplete or inconclusive. Missing fields were not treated as matches.",
    "ERROR": "The permitted source could not be checked or the run failed. No authenticity conclusion was reached.",
    "CONSENT_REQUIRED": "Candidate consent is absent. No source request or browser navigation was attempted.",
    "REJECTED": "A reviewer rejected the proposed action. No lookup was performed.",
    "AWAITING_APPROVAL": "The exact source and destination are awaiting human review. No lookup has been performed.",
}


def get_orchestrator():
    adapters = {}
    if PUBLIC_PAGE_ALLOWED_HOSTS:
        adapters["public_page"] = PublicPageAdapter(
            str(DATABASE_PATH), allowed_hosts=PUBLIC_PAGE_ALLOWED_HOSTS
        )
    return VerificationOrchestrator(DATABASE_PATH, SourceRegistry(adapters))


def _normalized(value):
    if value is None or not str(value).strip():
        return None
    return " ".join(str(value).split()).casefold()


def comparison_rows(run):
    evidence = (run.get("result") or {}).get("evidence", [])
    source_fields = evidence[0].get("fields", {}) if evidence else {}
    rows = []
    for key, label in (("credential_id", "Credential ID"), ("issuer", "Issuer"),
                       ("candidate_name", "Candidate name"), ("course", "Course"),
                       ("completion_date", "Completion date")):
        claimed = run["claim"].get(key)
        observed = source_fields.get(key)
        if _normalized(observed) is None:
            status = "MISSING"
        elif _normalized(claimed) == _normalized(observed):
            status = "MATCH"
        else:
            status = "CONFLICT"
        rows.append({"Field": label, "Claimed value": claimed or "—",
                     "Observed value": observed or "No evidence", "Comparison": status})
    return rows


def run_table_rows(runs):
    return [{
        "Status": run["state"], "Candidate": run["claim"].get("candidate_name", ""),
        "Credential ID": run["claim"].get("credential_id", ""),
        "Issuer": run["claim"].get("issuer", ""), "Course": run["claim"].get("course", ""),
        "Completion date": run["claim"].get("completion_date", ""),
        "Source": run["source"], "Created (UTC)": run["created_at"],
        "Run ID": run["run_id"], "Destination URL": run["destination_url"],
    } for run in runs]


def filter_runs(runs, *, statuses=None, source=None, search=""):
    filtered = runs
    if statuses:
        filtered = [run for run in filtered if run["state"] in statuses]
    if source and source != "All sources":
        filtered = [run for run in filtered if run["source"] == source]
    term = search.strip().casefold()
    if term:
        filtered = [run for run in filtered if term in json.dumps(run["claim"]).casefold()
                    or term in run["run_id"].casefold()]
    return filtered


def filtered_evidence(evidence, label_filter):
    if label_filter == "All evidence":
        return evidence
    return [row for row in evidence if row["evidence_type"] == label_filter]


def render_evidence_report(rows, key):
    if not rows:
        st.caption("No evidence rows match the current filters.")
        return
    st.download_button("Download evidence JSON", json.dumps(rows, indent=2),
                       file_name="certificate-evidence-report.json", mime="application/json", key=f"json_{key}")
    csv_buffer = io.StringIO()
    columns = list(rows[0])
    writer = csv.DictWriter(csv_buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()})
    st.download_button("Download evidence CSV", csv_buffer.getvalue(),
                       file_name="certificate-evidence-report.csv", mime="text/csv", key=f"csv_{key}")


st.set_page_config(page_title="Certificate Authenticity Checker", page_icon="🔎",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
.block-container { max-width: 1280px; padding-top: 1.6rem; }
[data-testid="stMetric"] { background:#f6f8fb; border:1px solid #e8edf4; padding:.8rem; border-radius:12px; }
.source-live { color:#075985; background:#e0f2fe; border-radius:99px; padding:.2rem .55rem; font-weight:700; }
.source-sim { color:#754b00; background:#fff2d7; border-radius:99px; padding:.2rem .55rem; font-weight:700; }
</style>
""", unsafe_allow_html=True)

initialize_database(DATABASE_PATH)
get_orchestrator()  # Initialize workflow tables even when the first page opened is the dashboard.
runs_all = list_runs(str(DATABASE_PATH))
live_evidence_all = list_live_evidence(str(DATABASE_PATH))
simulated_tests_all = list_simulated_tests(str(DATABASE_PATH))

with st.sidebar:
    st.markdown("## Certificate review")
    st.caption("Local review workspace · human approval required for live source access")
    section = st.radio("Workspace", ("Dashboard", "New Verification", "Approval Review",
                                      "Verification History", "Evidence", "Test Case Dashboard",
                                      "Synthetic Demo Records"), label_visibility="collapsed")
    st.divider()
    st.markdown('<span class="source-live">LIVE SOURCE</span> page captures', unsafe_allow_html=True)
    st.markdown('<span class="source-sim">SIMULATED</span> test records', unsafe_allow_html=True)

st.title("Certificate Authenticity Checker")
st.caption("Review a claim, approve the exact public page action, then inspect field comparisons and evidence.")

if section == "Dashboard":
    st.subheader("Verification overview")
    counts = {status: sum(run["state"] == status for run in runs_all) for status in
              ("AWAITING_APPROVAL", "VERIFIED", "MISMATCH", "NOT_FOUND", "NEEDS_REVIEW", "ERROR", "CONSENT_REQUIRED")}
    metrics = st.columns(5)
    metrics[0].metric("Total runs", len(runs_all))
    metrics[1].metric("Awaiting approval", counts["AWAITING_APPROVAL"])
    metrics[2].metric("Verified", counts["VERIFIED"])
    metrics[3].metric("Needs review", counts["NEEDS_REVIEW"])
    metrics[4].metric("Simulated tests", len(simulated_tests_all))
    st.markdown("#### Recent verification runs")
    quick = st.columns([1, 1, 2])
    status_filter = quick[0].multiselect("Status", sorted({r["state"] for r in runs_all}), key="dash_status")
    source_filter = quick[1].selectbox("Source", ["All sources", *sorted({r["source"] for r in runs_all})], key="dash_source")
    search_filter = quick[2].text_input("Find candidate, credential or run ID", key="dash_search")
    visible_runs = filter_runs(runs_all, statuses=status_filter, source=source_filter, search=search_filter)
    st.dataframe(run_table_rows(visible_runs[:25]), width="stretch", hide_index=True)
    if not visible_runs:
        st.info("No runs match these filters. Start with New Verification or clear a filter.")
    with st.expander("Decision status guide"):
        st.dataframe([{"Status": key, "Meaning": STATUS_HELP[key]} for key in
                       ("VERIFIED", "MISMATCH", "NOT_FOUND", "NEEDS_REVIEW", "ERROR", "CONSENT_REQUIRED")],
                     width="stretch", hide_index=True)

elif section == "New Verification":
    st.subheader("Submit a certificate claim")
    st.write("The form prepares a review action only. The destination URL will be shown for approval before any navigation.")
    st.info("Demonstration only: use synthetic candidate details. Do not submit real personal or confidential data.")
    if not PUBLIC_PAGE_ALLOWED_HOSTS:
        st.warning("Live access is disabled until `PUBLIC_PAGE_ALLOWED_HOSTS` is explicitly configured.")
    st.caption("Configured hosts: " + (", ".join(PUBLIC_PAGE_ALLOWED_HOSTS) if PUBLIC_PAGE_ALLOWED_HOSTS else "none"))
    if "submission_attempt_nonce" not in st.session_state:
        st.session_state["submission_attempt_nonce"] = str(uuid.uuid4())
    if st.button("Start a fresh attempt for the same claim", key="fresh_attempt"):
        st.session_state["submission_attempt_nonce"] = str(uuid.uuid4())
        st.info("Fresh attempt key created. Repeated clicks for the same form submission remain idempotent.")
    with st.form("credential_claim_form", clear_on_submit=False):
        left, right = st.columns(2)
        with left:
            credential_id = st.text_input("Credential ID", placeholder="e.g. CERT-2026-001")
            candidate = st.text_input("Claimed candidate name", placeholder="Name shown on the credential")
            course = st.text_input("Course")
        with right:
            issuer = st.text_input("Issuer", placeholder="Issuing organization")
            completion_date = st.text_input("Completion date (YYYY-MM-DD)", placeholder="2026-03-12")
            destination_url = st.text_input("Public verification page URL", placeholder="https://issuer.example/verify")
        consent = st.checkbox("The candidate has explicitly consented to this verification")
        submitted = st.form_submit_button("Prepare verification action", type="primary")
    if submitted:
        claim = {"credential_id": credential_id, "issuer": issuer, "candidate_name": candidate,
                 "course": course, "completion_date": completion_date}
        request_key = hashlib.sha256((st.session_state["submission_attempt_nonce"] + json.dumps(
            {"claim": claim, "consent": consent, "source": "public_page", "url": destination_url},
            sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest()
        try:
            run = get_orchestrator().submit(claim, consent=consent, source="public_page",
                                            destination_url=destination_url, idempotency_key=request_key)
            st.session_state["active_run_id"] = run["run_id"]
            if run["state"] == "CONSENT_REQUIRED":
                st.warning("CONSENT_REQUIRED — no source request or browser navigation was made.")
            elif run["state"] == "ERROR":
                st.error("The claim could not be prepared. Check that all required fields and the HTTPS destination are valid.")
            else:
                st.success("Claim saved. Review the proposed destination in Approval Review; no navigation has occurred.")
        except Exception:
            st.error("The claim could not be saved safely. Please retry or contact the workspace administrator.")
    active_id = st.session_state.get("active_run_id")
    if active_id:
        active = get_orchestrator().get_run(active_id)
        if active:
            st.markdown("#### Latest submission")
            st.write(f"**State:** {active['state']} · **Run ID:** `{active['run_id']}`")
            st.caption(STATUS_HELP.get(active["state"], "Workflow state recorded."))
            if active["state"] == "AWAITING_APPROVAL":
                st.write(f"**Proposed action:** {active['proposed_action']}")
                st.write(f"**Exact destination:** {active['destination_url']}")

elif section == "Approval Review":
    st.subheader("Human approval queue")
    pending = [run for run in runs_all if run["state"] == "AWAITING_APPROVAL"]
    st.metric("Pending actions", len(pending))
    if not pending:
        st.success("No actions are waiting for approval.")
    else:
        choices = {f"{r['claim'].get('candidate_name')} · {r['claim'].get('credential_id')} · {r['run_id'][:8]}": r for r in pending}
        chosen_label = st.selectbox("Select a proposed action", list(choices))
        run = choices[chosen_label]
        st.warning("Review the exact source and URL below. Approving is the only action that can start a lookup.")
        details = st.columns(2)
        details[0].write(f"**Proposed action**\n\n{run['proposed_action']}")
        details[0].write(f"**Selected source:** {run['source']}")
        details[1].write(f"**Exact destination URL**\n\n{run['destination_url']}")
        details[1].write(f"**Candidate consent:** {'Recorded' if run['consent'] else 'Missing'}")
        st.caption(f"Run ID: {run['run_id']} · Action ID: {run['action_id']} · Submitted: {run['created_at']} UTC")
        if run["consent"]:
            approve_col, reject_col = st.columns(2)
            if approve_col.button("Approve exact action and run once", type="primary", key=f"approve_{run['action_id']}"):
                try:
                    updated = get_orchestrator().approve(run["run_id"], approver="app_user", approved=True)
                    st.session_state["review_result"] = updated["run_id"]
                    st.rerun()
                except Exception:
                    st.error("The approved action failed safely. No sensitive error details were exposed.")
            if reject_col.button("Reject action", key=f"reject_{run['action_id']}"):
                try:
                    get_orchestrator().approve(run["run_id"], approver="app_user", approved=False)
                    st.rerun()
                except Exception:
                    st.error("The action could not be rejected safely. Please refresh and try again.")
        else:
            st.error("Consent is missing. This action cannot be approved or navigated.")
    review_id = st.session_state.get("review_result")
    if review_id:
        result_run = get_orchestrator().get_run(review_id)
        if result_run:
            st.markdown("#### Latest review result")
            st.success(f"{result_run['state']} · run `{result_run['run_id']}`")
            st.write(STATUS_HELP.get(result_run["state"], ""))
            if result_run.get("result"):
                st.write("**Decision reason:** " + result_run["result"].get("rationale", ""))

elif section == "Verification History":
    st.subheader("Verification history")
    filters = st.columns([1, 1, 2])
    statuses = filters[0].multiselect("Filter by status", sorted({r["state"] for r in runs_all}), key="history_status")
    source = filters[1].selectbox("Filter by source", ["All sources", *sorted({r["source"] for r in runs_all})], key="history_source")
    search = filters[2].text_input("Search candidate, credential or run ID", key="history_search")
    visible = filter_runs(runs_all, statuses=statuses, source=source, search=search)
    st.dataframe(run_table_rows(visible), width="stretch", hide_index=True)
    if visible:
        selected = st.selectbox("Open run details", visible, format_func=lambda r: f"{r['claim'].get('candidate_name')} · {r['state']} · {r['run_id'][:8]}", key="history_detail")
        st.markdown(f"#### {selected['state']} · {selected['claim'].get('candidate_name')}")
        st.caption(STATUS_HELP.get(selected["state"], "Workflow state recorded."))
        selected_result = selected.get("result") or {}
        st.write(f"**Decision reason:** {selected_result.get('rationale') or selected.get('error_message') or STATUS_HELP.get(selected['state'], '')}")
        st.dataframe(transition_history(str(DATABASE_PATH), selected["run_id"]), width="stretch", hide_index=True)
        st.markdown("##### Field-level comparison")
        st.dataframe(comparison_rows(selected), width="stretch", hide_index=True)
        if selected_result.get("evidence"):
            for evidence in selected_result["evidence"]:
                st.markdown(f"<span class='source-live'>{evidence.get('label', evidence.get('evidence_type', 'LIVE SOURCE'))}</span>", unsafe_allow_html=True)
                st.write(f"**Source URL:** {evidence.get('url', selected['destination_url'])}")
                st.write(f"**Retrieved:** {evidence.get('observed_at', '—')} · **Evidence type:** {evidence.get('evidence_type', '—')}")

elif section == "Evidence":
    st.subheader("Evidence library")
    live_all = list_live_evidence(str(DATABASE_PATH))
    labels = ["All evidence", "LIVE SOURCE"]
    label_filter = st.selectbox("Evidence type", labels, key="evidence_type_filter")
    evidence = filtered_evidence(live_all, label_filter)
    candidate_query = st.text_input("Filter by run ID or source URL", key="evidence_search").casefold().strip()
    if candidate_query:
        evidence = [row for row in evidence if candidate_query in row["run_id"].casefold() or candidate_query in row["source_url"].casefold()]
    if not evidence:
        st.info("No LIVE SOURCE captures match this filter. SIMULATED test records are shown in Test Case Dashboard and are not issuer evidence.")
    else:
        report_rows = evidence_report_rows(runs_all, evidence)
        render_evidence_report(report_rows, "evidence")
        st.dataframe([{ "Label": row["evidence_type"], "Run ID": row["run_id"],
                        "Source URL": row["source_url"], "Retrieved (UTC)": row["captured_at_utc"],
                        "Evidence type": row["evidence_type"], "Reference": row["evidence_id"],
                        "Decision reason": next((r.get("result") or {}).get("rationale", "")
                            for r in runs_all if r["run_id"] == row["run_id"])}
                       for row in evidence], width="stretch", hide_index=True)
        selected_evidence = st.selectbox("Preview capture", evidence,
            format_func=lambda row: f"{row['run_id'][:8]} · {row['captured_at_utc']} · LIVE SOURCE", key="evidence_preview")
        full = list_live_evidence(str(DATABASE_PATH), selected_evidence["run_id"], include_screenshot=True)
        selected_full = next(row for row in full if row["evidence_id"] == selected_evidence["evidence_id"])
        st.caption(f"LIVE SOURCE · Run {selected_full['run_id']} · {selected_full['captured_at_utc']} UTC · {selected_full['evidence_type']}")
        st.write(f"**Source URL:** {selected_full['source_url']}")
        st.write("**Extracted values:**")
        st.json(selected_full["extracted"])
        st.image(selected_full["screenshot"], caption="LIVE SOURCE page capture · not a standalone authenticity guarantee")

elif section == "Test Case Dashboard":
    st.subheader("Synthetic test-case dashboard")
    st.warning("SIMULATED records and outcomes only. They are not issuer evidence; the mock adapter never captures issuer screenshots.")
    st.write(f"**Source:** SIMULATED MOCK RECORDS · **Destination:** {SyntheticMockSourceAdapter.destination_url}")
    if st.button("Run All Test Cases", type="primary", key="run_all_tests"):
        try:
            run_all_test_cases(DATABASE_PATH)
            st.success("Four SIMULATED test cases completed and saved.")
            st.rerun()
        except Exception:
            st.error("The simulated test run could not be saved safely.")
    tests = list_simulated_tests(str(DATABASE_PATH))
    test_filters = st.columns(3)
    case_ids = test_filters[0].multiselect("Case", sorted({row["test_case_id"] for row in tests}), key="tc_case")
    pass_filter = test_filters[1].selectbox("Outcome", ["All", "Passed", "Failed"], key="tc_outcome")
    test_search = test_filters[2].text_input("Search test run or reason", key="tc_search").casefold().strip()
    visible_tests = [row for row in tests if (not case_ids or row["test_case_id"] in case_ids)
                     and (pass_filter == "All" or row["passed"] == (pass_filter == "Passed"))
                     and (not test_search or test_search in row["run_id"].casefold() or test_search in row["reason"].casefold())]
    passed = sum(row["passed"] for row in visible_tests)
    tc_metrics = st.columns(3)
    tc_metrics[0].metric("Runs shown", len(visible_tests))
    tc_metrics[1].metric("Passed", passed)
    tc_metrics[2].metric("Pass rate", f"{(passed / len(visible_tests) * 100):.0f}%" if visible_tests else "—")
    st.dataframe([{ "Case": row["test_case_id"], "Label": row["label"], "Expected": row["expected_result"],
                    "Actual": row["actual_result"], "Passed": "Yes" if row["passed"] else "No",
                    "Run ID": row["run_id"], "Evidence reference": row["evidence_reference"] or "SIMULATED: none",
                    "Reason": row["reason"], "Recorded": row["created_at"]}
                   for row in visible_tests], width="stretch", hide_index=True)
    if visible_tests:
        render_evidence_report([{"label": "SIMULATED", "test_case_id": row["test_case_id"],
            "input": row["input"], "expected_result": row["expected_result"],
            "actual_result": row["actual_result"], "run_id": row["run_id"],
            "reason": row["reason"], "evidence_reference": row["evidence_reference"] or "SIMULATED: none"}
            for row in visible_tests], "tests")

elif section == "Synthetic Demo Records":
    st.subheader("Synthetic demo records")
    st.caption("SIMULATED examples only. These are not verification outcomes or issuer evidence.")
    st.dataframe(get_demo_certificates(DATABASE_PATH), width="stretch", hide_index=True)
