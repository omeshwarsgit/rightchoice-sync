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


def navigable_review_links(page):
    return [
        page.get_by_role("link", name=re.compile(r"Review Management|Reviews Management", re.I)),
        page.get_by_role("button", name=re.compile(r"Review Management|Reviews Management", re.I)),
        page.get_by_text(re.compile(r"Review Management|Reviews Management", re.I), exact=False),
        page.locator("a, button, [role='button'], [role='menuitem'], [role='tab']").filter(
            has_text=re.compile(r"Review Management|Reviews Management", re.I)
        ),
    ]


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

        # --- 1. Login ---
        log("Logging in...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.get_by_placeholder("Email Address").fill(email)
        page.get_by_placeholder("Password").fill(password)
        page.get_by_role("button", name="Sign In").click()
        page.wait_for_load_state("networkidle", timeout=60_000)
        page.wait_for_timeout(5000)
        page.screenshot(path="output/after_login.png")

        # --- 2. Navigate to Review Management (left-nav menu item in the current UI) ---
        log("Navigating to Review Management...")
        page.wait_for_load_state("networkidle", timeout=60_000)
        page.wait_for_timeout(3000)

        clicked = False
        for locator in navigable_review_links(page):
            try:
                if locator.count():
                    locator.first.click(timeout=60_000)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            log("Review Management menu item not found; reloading and retrying once...")
            page.reload()
            page.wait_for_load_state("networkidle", timeout=60_000)
            page.wait_for_timeout(3000)
            for locator in navigable_review_links(page):
                try:
                    if locator.count():
                        locator.first.click(timeout=60_000)
                        clicked = True
                        break
                except Exception:
                    continue

        if not clicked:
            raise PWTimeout("Could not find the Review Management navigation item in the dashboard.")

        page.wait_for_load_state("networkidle", timeout=60_000)
        page.wait_for_timeout(3000)
        page.screenshot(path="output/after_menu_click.png")

        # --- 3. Make sure "All Locations" is selected ---
        log("Confirming all locations are selected...")
        page.get_by_text(re.compile(r"Locations Selected")).first.click(timeout=60_000)
        all_locations_checkbox = page.get_by_role("checkbox", name=re.compile("All Locations"))
        if all_locations_checkbox.count() and not all_locations_checkbox.first.is_checked():
            all_locations_checkbox.first.click(timeout=60_000)
        apply_btn = page.get_by_role("button", name=re.compile(r"^(Select|Apply) "))
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
            prev_arrow = page.locator("button").filter(has_text=re.compile("^$")).first
            prev_arrow.click(timeout=60_000)
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
