"""Playwright adapter for explicitly allowlisted, public HTTPS verification pages."""

from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from agent.storage import assert_live_lookup_authorized, save_live_source_evidence


class UnsafeDestination(ValueError):
    """Raised before browser navigation for a disallowed or non-public URL."""


def _normalize_host(host: str) -> str:
    return host.rstrip(".").encode("idna").decode("ascii").lower()


def validate_public_url(url: str, allowed_hosts: Iterable[str],
                        resolver: Callable[..., Any] = socket.getaddrinfo) -> str:
    """Require an exact allowlisted HTTPS host whose resolved addresses are public."""
    allowed = {_normalize_host(host.strip()) for host in allowed_hosts if host.strip()}
    if not allowed:
        raise UnsafeDestination("No public page hosts are configured")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise UnsafeDestination("Invalid destination URL") from exc
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password:
        raise UnsafeDestination("Destination must be a user-supplied HTTPS URL without credentials")
    if port not in (None, 443):
        raise UnsafeDestination("Only the standard HTTPS port is allowed")
    hostname = _normalize_host(host)
    if hostname in {"localhost", "localhost.localdomain"} or hostname not in allowed:
        raise UnsafeDestination("Destination host is not explicitly allowlisted")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if not literal_ip.is_global:
            raise UnsafeDestination("Private or reserved IP destinations are not allowed")
        return hostname
    try:
        resolved = resolver(hostname, 443, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}
    except (OSError, ValueError, IndexError, TypeError) as exc:
        raise UnsafeDestination("Destination host could not be safely resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise UnsafeDestination("Host resolves to a private or reserved IP address")
    return hostname


_FIELD_PATTERNS = {
    "candidate_name": re.compile(r"^\s*(?:candidate\s+name|recipient\s+name|recipient|student\s+name|name)\s*[:#-]\s*(.*?)\s*$", re.I),
    "course": re.compile(r"^\s*(?:course|program|qualification|course\s+name)\s*[:#-]\s*(.*?)\s*$", re.I),
    "completion_date": re.compile(r"^\s*(?:completion\s+date|completed\s+on|date\s+completed|completion)\s*[:#-]\s*(.*?)\s*$", re.I),
    "credential_id": re.compile(r"^\s*(?:credential\s+id|certificate\s+id|certificate\s+number|credential\s+number)\s*[:#-]\s*(.*?)\s*$", re.I),
    "issuer": re.compile(r"^\s*(?:issuer|issuing\s+organization|institution)\s*[:#-]\s*(.*?)\s*$", re.I),
}
_RESTRICTED_MARKERS = ("captcha", "one-time password", "enter otp", "verification code", "sign in to continue",
                       "log in to continue", "access denied", "automated requests")


def extract_visible_fields(visible_text: str) -> dict[str, str]:
    """Extract only explicitly labelled values from visible page text."""
    extracted: dict[str, str] = {}
    for line in visible_text.splitlines():
        for field, pattern in _FIELD_PATTERNS.items():
            match = pattern.match(line)
            value = match.group(1).strip() if match else ""
            if value and field not in extracted:
                extracted[field] = value
    return extracted


def _default_browser_factory():
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    browser_path = shutil.which("chromium")
    launch_options = {"headless": True}
    if browser_path:
        launch_options.update(executable_path=browser_path, args=["--no-sandbox"])
    browser = playwright.chromium.launch(**launch_options)
    return _OwnedBrowser(browser, playwright)


class _OwnedBrowser:
    def __init__(self, browser: Any, playwright: Any):
        self.browser = browser
        self.playwright = playwright

    def new_page(self):
        return self.browser.new_page()

    def close(self):
        self.browser.close()


class PublicPageAdapter:
    """Read visible text from a public page and persist a LIVE SOURCE screenshot/evidence."""

    source_name = "LIVE SOURCE"
    evidence_type = "LIVE SOURCE"

    def __init__(self, db_path: str, *, allowed_hosts: Iterable[str],
                 browser_factory: Callable[[], Any] | None = None,
                 resolver: Callable[..., Any] = socket.getaddrinfo,
                 timeout_ms: int = 20000):
        self.db_path = str(db_path)
        self.allowed_hosts = tuple(allowed_hosts)
        self.browser_factory = browser_factory or _default_browser_factory
        self.resolver = resolver
        self.timeout_ms = timeout_ms

    def lookup(self, destination_url: str, claim: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        """Navigate only to the approved URL, after URL/host/IP checks; never bypass access controls."""
        assert_live_lookup_authorized(self.db_path, run_id, destination_url)
        validate_public_url(destination_url, self.allowed_hosts, self.resolver)
        browser = page = None
        try:
            browser = self.browser_factory()
            page = browser.new_page()
            # Guard redirects and subresources as well as the initial navigation.
            if hasattr(page, "route"):
                def guard(route):
                    try:
                        validate_public_url(route.request.url, self.allowed_hosts, self.resolver)
                        route.continue_()
                    except UnsafeDestination:
                        route.abort()
                page.route("**/*", guard)
            page.goto(destination_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            visible_text = page.locator("body").inner_text(timeout=self.timeout_ms)
            lowered = visible_text.casefold()
            if any(marker in lowered for marker in _RESTRICTED_MARKERS):
                return {"label": "LIVE SOURCE", "evidence": [], "authoritative_search_complete": False,
                        "reason": "The public page indicates restricted access or an anti-bot challenge; no attempt was made to bypass it."}

            extracted = extract_visible_fields(visible_text)
            screenshot = page.screenshot(full_page=True, type="png")
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            evidence_id = str(uuid.uuid4())
            save_live_source_evidence(
                self.db_path, evidence_id=evidence_id, run_id=run_id,
                source_url=destination_url, captured_at_utc=captured_at,
                extracted=extracted, screenshot=screenshot,
            )
            evidence = {
                "label": "LIVE SOURCE", "source": "LIVE SOURCE public page",
                "evidence_type": "LIVE SOURCE", "url": destination_url,
                "observed_at": captured_at, "fields": extracted,
                "evidence_reference": evidence_id,
                "note": "HTTP status and page loading are not proof of authenticity.",
            }
            return {"label": "LIVE SOURCE", "evidence": [evidence],
                    "authoritative_search_complete": False,
                    "reason": "Visible page text was captured. The decision uses extracted fields, never HTTP status alone."}
        except Exception:
            # No exception internals are exposed; timeouts and inaccessible pages are inconclusive errors.
            return {"label": "LIVE SOURCE", "evidence": [], "error": True,
                    "authoritative_search_complete": False,
                    "reason": "The permitted public page timed out or could not be accessed."}
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
                playwright = getattr(browser, "playwright", None)
                if playwright is not None:
                    try:
                        playwright.stop()
                    except Exception:
                        pass
