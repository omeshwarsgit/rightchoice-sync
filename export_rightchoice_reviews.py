"""
Logs into dashboard.rightchoice.ai, filters Review Management to "yesterday",
makes sure all locations are selected, triggers the Export CSV action, and
saves the downloaded file to a fixed path so a downstream step (n8n, cron,
whatever) can pick it up and push it into the Google Sheet.

Required environment variables:
    RIGHTCHOICE_EMAIL    - login email for dashboard.rightchoice.ai
    RIGHTCHOICE_PASSWORD - login password
Optional:
    OUTPUT_CSV_PATH      - where to save the downloaded CSV
                            (default: ./output/reviews_yesterday.csv)

Requires: pip install playwright && playwright install chromium --with-deps
"""

import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LOGIN_URL = "https://dashboard.rightchoice.ai/login"
DEFAULT_OUTPUT = Path(__file__).parent / "output" / "reviews_yesterday.csv"


def log(msg: str) -> None:
    print(f"[rightchoice-export] {msg}", flush=True)


def target_date() -> date:
    return date.today() - timedelta(days=1)


def date_button_text(d: date) -> str:
    return f"{d.day} {d.strftime('%B')} {d.year}"


def click_with_retry(page, locator, *, timeout: int = 60_000, retries: int = 3):
    """Retry a click when the page is slow or React re-renders menu content."""
    last_error = None
    for attempt in range(retries):
        try:
            locator.first.wait_for(state="visible", timeout=timeout)
            locator.first.click(timeout=timeout)
            return True
        except Exception as exc:  # noqa: BLE001 - keep retry logic simple for flaky UI
            last_error = exc
            log(f"Click attempt {attempt + 1}/{retries} failed for selector {locator}; retrying...")
            page.wait_for_timeout(2000)
    raise last_error


def find_review_management_menu(page):
    """Return the most stable locator for the Review Management navigation item."""
    candidates = [
        page.get_by_role("button", name=re.compile(r"Reviews? Management", re.I)),
        page.get_by_role("link", name=re.compile(r"Reviews? Management", re.I)),
        page.get_by_text(re.compile(r"Reviews? Management", re.I), exact=False),
    ]
    for locator in candidates:
        if locator.count():
            return locator
    return page.locator("button, a, li, div").filter(has_text=re.compile(r"Reviews? Management", re.I))


def click_sidebar_review_management(page):
    """Click the parent/child sidebar items for the duplicated Review Management labels."""
    # First click the parent menu item (the left-side accordion item)
    parent_candidates = [
        page.get_by_role("button", name=re.compile(r"^Reviews? Management$", re.I)),
        page.get_by_role("link", name=re.compile(r"^Reviews? Management$", re.I)),
        page.get_by_text(re.compile(r"^Reviews? Management$", re.I), exact=True),
        page.locator("button, a, div").filter(has_text=re.compile(r"^Reviews? Management$", re.I)),
    ]
    parent = next((loc for loc in parent_candidates if loc.count()), None)
    if parent is None:
        raise RuntimeError("Could not find parent Reviews Management menu item in left sidebar")

    click_with_retry(page, parent)
    page.wait_for_timeout(1500)

    # Then click the child submenu item; if duplicates exist, prefer the second match.
    child_candidates = [
        page.get_by_role("button", name=re.compile(r"^Review Management$", re.I)),
        page.get_by_role("link", name=re.compile(r"^Review Management$", re.I)),
        page.get_by_text(re.compile(r"^Review Management$", re.I), exact=True),
    ]
    child = next((loc for loc in child_candidates if loc.count()), None)
    if child is not None:
        if child.count() > 1:
            child.nth(1).click(timeout=60_000)
        else:
            child.first.click(timeout=60_000)
        return

    # Fallback: click the second text match if the parent and child share the same label.
    matches = page.get_by_text(re.compile(r"Review Management", re.I), exact=False)
    if matches.count() > 1:
        matches.nth(1).click(timeout=60_000)
    else:
        matches.first.click(timeout=60_000)


def main() -> int:
    email = os.environ.get("RIGHTCHOICE_EMAIL")
    password = os.environ.get("RIGHTCHOICE_PASSWORD")
    if not email or not password:
        log("ERROR: RIGHTCHOICE_EMAIL and RIGHTCHOICE_PASSWORD must be set.")
        return 1

    output_path = Path(os.environ.get("OUTPUT_CSV_PATH", str(DEFAULT_OUTPUT)))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    day = target_date()
    day_label = date_button_text(day)
    log(f"Target date: {day_label}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(60_000)
        page.set_default_navigation_timeout(60_000)

        # --- 1. Login ---
        log("Logging in...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        page.get_by_placeholder("Email Address").fill(email)
        page.get_by_placeholder("Password").fill(password)
        page.get_by_role("button", name="Sign In").click(timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=60_000)
        page.wait_for_timeout(5000)
        page.screenshot(path="output/after_login.png")

        # --- 2. Navigate to Reviews Management -> Review Management ---
        log("Navigating to Review Management...")
        try:
            click_sidebar_review_management(page)
        except Exception:
            page.reload()
            page.wait_for_load_state("networkidle", timeout=60_000)
            click_sidebar_review_management(page)

        page.wait_for_timeout(2000)
        page.screenshot(path="output/after_menu_click.png")

        # Some installs land on a different dashboard tab first; if the Review Management tab exists,
        # make sure it's active before proceeding.
        tab_item = page.get_by_role("tab", name=re.compile(r"Review Management", re.I))
        if tab_item.count():
            tab_item.first.click(timeout=60_000)

        page.wait_for_load_state("networkidle", timeout=60_000)
        page.wait_for_timeout(3000)

        # --- 3. Make sure "All Locations" is selected ---
        log("Confirming all locations are selected...")
        page.get_by_text(re.compile(r"Locations Selected")).first.click(timeout=60_000)
        all_locations_checkbox = page.get_by_role("checkbox", name=re.compile("All Locations", re.I))
        if all_locations_checkbox.count() and not all_locations_checkbox.first.is_checked():
            all_locations_checkbox.first.click(timeout=60_000)
        apply_btn = page.get_by_role("button", name=re.compile(r"^(Select|Apply) ", re.I))
        if apply_btn.count():
            apply_btn.first.click(timeout=60_000)
        else:
            page.keyboard.press("Escape")
        page.wait_for_load_state("networkidle", timeout=60_000)

        # --- 4. Set the date filter to "yesterday only" ---
        log("Setting date filter...")
        page.get_by_text("All Time", exact=True).first.click(timeout=60_000)
        page.get_by_role("radio", name="Pick Date Range").click(timeout=60_000)

        date_btn = page.get_by_role("button", name=day_label, exact=True)
        if not date_btn.count():
            prev_arrow = page.locator("button").filter(has_text=re.compile("^$"))
            if prev_arrow.count():
                prev_arrow.first.click(timeout=60_000)
            date_btn = page.get_by_role("button", name=day_label, exact=True)
        date_btn.first.click(timeout=60_000)
        date_btn.first.click(timeout=60_000)

        page.get_by_role("button", name="Apply Filter").first.click(timeout=60_000)
        page.wait_for_load_state("networkidle", timeout=60_000)

        # --- 5. Export ---
        log("Triggering export...")
        page.get_by_role("button", name="Export CSV").first.click(timeout=60_000)
        export_submit = page.get_by_role("button", name=re.compile(r"^Export CSV For"))
        export_submit.wait_for(state="visible", timeout=60_000)

        with page.expect_download(timeout=60_000) as download_info:
            export_submit.click(timeout=60_000)
        download = download_info.value
        download.save_as(str(output_path))
        log(f"Saved CSV to {output_path}")

        browser.close()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PWTimeout as e:
        log(f"ERROR: timed out waiting for an element: {e}")
        sys.exit(2)
