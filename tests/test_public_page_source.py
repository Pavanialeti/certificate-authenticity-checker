import json
import socket
import sqlite3

import pytest

from agent.orchestrator import VerificationOrchestrator
from agent.sources import SourceRegistry
from sources.public_page import PublicPageAdapter, UnsafeDestination, validate_public_url


HOST = "certs.example.org"
URL = f"https://{HOST}/verify?id=abc"
CLAIM = {
    "credential_id": "CERT-91", "issuer": "Example Institute",
    "candidate_name": "Riley Example", "course": "Public Health",
    "completion_date": "2026-03-17",
}


def resolver_public(host, port, type):
    assert host == HOST
    assert port == 443
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def resolver_private(host, port, type):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]


@pytest.mark.parametrize("url", [
    "http://certs.example.org/verify",
    "file:///C:/secret.txt",
    "https://localhost/verify",
    "https://127.0.0.1/verify",
    "https://not-allowlisted.example/verify",
    "https://user:pass@certs.example.org/verify",
    "https://certs.example.org:8443/verify",
])
def test_unsafe_destination_rejected(url):
    with pytest.raises(UnsafeDestination):
        validate_public_url(url, [HOST], resolver_public)


def test_rejects_hostname_resolving_to_private_ip():
    with pytest.raises(UnsafeDestination, match="private or reserved"):
        validate_public_url(URL, [HOST], resolver_private)


class FakeLocator:
    def __init__(self, text):
        self.text = text

    def inner_text(self, timeout):
        return self.text


class FakePage:
    def __init__(self, text):
        self.text = text
        self.navigated_to = None
        self.route_handler = None
        self.closed = False

    def route(self, pattern, handler):
        self.route_handler = handler

    def goto(self, url, wait_until, timeout):
        self.navigated_to = url
        return type("Response", (), {"status": 200})()

    def locator(self, selector):
        assert selector == "body"
        return FakeLocator(self.text)

    def screenshot(self, full_page, type):
        return b"fake-png-bytes"

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


def make_adapter(db_path, page):
    browser = FakeBrowser(page)
    adapter = PublicPageAdapter(
        str(db_path), allowed_hosts=[HOST], browser_factory=lambda: browser,
        resolver=resolver_public, timeout_ms=5,
    )
    return adapter, browser


def run_approved(tmp_path, page):
    db_path = tmp_path / "live-source.sqlite3"
    adapter, browser = make_adapter(db_path, page)
    orchestrator = VerificationOrchestrator(db_path, SourceRegistry({"public_page": adapter}))
    pending = orchestrator.submit(CLAIM, consent=True, source="public_page", destination_url=URL)
    result = orchestrator.approve(pending["run_id"], approver="test-reviewer", approved=True)
    return db_path, result, page, browser


def test_approved_page_stores_live_source_values_and_screenshot(tmp_path):
    text = """Candidate name: Riley Example
Course: Public Health
Completion date: 2026-03-17
Credential ID: CERT-91
Issuer: Example Institute"""
    db, run, page, browser = run_approved(tmp_path, FakePage(text))
    assert run["state"] == "VERIFIED"
    assert page.navigated_to == URL
    assert page.closed and browser.closed
    evidence = run["result"]["evidence"][0]
    assert evidence["label"] == "LIVE SOURCE"
    assert evidence["url"] == URL
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT run_id, source_url, captured_at_utc, extracted_json, evidence_type, screenshot, screenshot_label FROM live_source_evidence"
        ).fetchone()
    assert row[0] == run["run_id"]
    assert row[1] == URL
    assert row[2].endswith("+00:00")
    assert json.loads(row[3])["candidate_name"] == "Riley Example"
    assert row[4] == row[6] == "LIVE SOURCE"
    assert row[5] == b"fake-png-bytes"


def test_http_200_with_missing_fields_is_needs_review(tmp_path):
    db, run, page, _ = run_approved(tmp_path, FakePage("Certificate verification page is available."))
    assert page.navigated_to == URL
    assert run["state"] == "NEEDS_REVIEW"
    assert "missing" in run["result"]["rationale"].casefold()


def test_restricted_page_is_not_bypassed_and_needs_review(tmp_path):
    db, run, page, _ = run_approved(tmp_path, FakePage("Please complete CAPTCHA to continue."))
    assert page.navigated_to == URL
    assert run["state"] == "NEEDS_REVIEW"
    assert "bypass" in run["result"]["source_reason"].casefold()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM live_source_evidence").fetchone()[0] == 0


def test_live_adapter_checks_approval_consent_and_exact_url_before_browser(tmp_path):
    page = FakePage("Candidate name: Riley Example")
    db_path = tmp_path / "unauthorized.sqlite3"
    VerificationOrchestrator(db_path)
    adapter, _ = make_adapter(db_path, page)
    with pytest.raises(PermissionError):
        adapter.lookup(URL, CLAIM, run_id="unapproved-run")
    assert page.navigated_to is None
