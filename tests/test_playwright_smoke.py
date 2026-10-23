import os

import pytest
from playwright.sync_api import expect, sync_playwright


APP_URL = os.environ.get("APP_URL")


@pytest.mark.skipif(not APP_URL, reason="Set APP_URL to run the browser smoke test")
def test_app_displays_review_dashboard_and_approval_gate():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(APP_URL, wait_until="domcontentloaded", timeout=15_000)
            expect(page.get_by_role("heading", name="Certificate Authenticity Checker")).to_be_visible(timeout=15_000)
            expect(page.get_by_text("Verification overview", exact=True)).to_be_visible(timeout=15_000)
            expect(page.get_by_text("human approval required for live source access", exact=False)).to_be_visible(timeout=15_000)
        finally:
            browser.close()
