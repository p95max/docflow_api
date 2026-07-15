import os
import re
import uuid

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")
expect = playwright_sync_api.expect
sync_playwright = playwright_sync_api.sync_playwright


@pytest.mark.e2e
def test_user_can_register_and_open_documents_in_a_browser() -> None:
    base_url = os.environ.get("E2E_BASE_URL", "").rstrip("/")
    if not base_url:
        pytest.skip("Set E2E_BASE_URL to the running DocsFlow application.")

    email = f"e2e-{uuid.uuid4().hex[:12]}@example.test"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"{base_url}/register", wait_until="networkidle")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("e2e-browser-password")
            page.get_by_role("button", name="Create account").click()

            expect(page).to_have_url(f"{base_url}/login?registered=1")
            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("e2e-browser-password")
            page.get_by_role("button", name="Log in").click()

            expect(page).to_have_url(f"{base_url}/documents")
            expect(page.get_by_role("heading", name="Your documents")).to_be_visible()

            page.goto(f"{base_url}/documents/upload", wait_until="networkidle")
            page.get_by_role("button", name="Upload").click()
            expect(page.locator("#file")).to_have_class(re.compile("is-invalid"))
        finally:
            browser.close()
