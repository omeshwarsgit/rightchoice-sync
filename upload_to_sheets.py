"""
Reads the exported CSV (reviews_yesterday.csv) and appends all rows to the
"Raw Data" tab of the Google Reviews_MIS Dashboard sheet.

Required environment variables:
    GOOGLE_SERVICE_ACCOUNT_JSON  - the full contents of the service account
                                   JSON key file (paste it as a GitHub secret)

Optional:
    INPUT_CSV_PATH   - path to the CSV to upload
                       (default: ./output/reviews_yesterday.csv)
    SHEET_ID         - Google Sheet ID to write to
                       (default: 11b0AP324-Ej2YZXcWqrQQ1vbj8ml1AFVLuGC_QhILmU)
    SHEET_TAB        - tab/worksheet name (default: Raw Data)

Requires: pip install gspread
"""

import csv
import json
import os
import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

DEFAULT_CSV = Path(__file__).parent / "output" / "reviews_yesterday.csv"
DEFAULT_SHEET_ID = "11b0AP324-Ej2YZXcWqrQQ1vbj8ml1AFVLuGC_QhILmU"
DEFAULT_TAB = "Raw Data"


def log(msg: str) -> None:
    print(f"[sheets-upload] {msg}", flush=True)


def main() -> int:
    # --- Load service account credentials from the env var ---
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        log("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON env var is not set.")
        return 1

    try:
        sa_info = json.loads(sa_json)
    except json.JSONDecodeError as e:
        log(f"ERROR: Could not parse GOOGLE_SERVICE_ACCOUNT_JSON as JSON: {e}")
        return 1

    creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    gc = gspread.authorize(creds)

    # --- Open the sheet ---
    sheet_id = os.environ.get("SHEET_ID", DEFAULT_SHEET_ID)
    tab_name = os.environ.get("SHEET_TAB", DEFAULT_TAB)

    log(f"Opening sheet {sheet_id!r}, tab {tab_name!r}...")
    spreadsheet = gc.open_by_key(sheet_id)
    worksheet = spreadsheet.worksheet(tab_name)

    # --- Read the CSV ---
    csv_path = Path(os.environ.get("INPUT_CSV_PATH", str(DEFAULT_CSV)))
    if not csv_path.exists():
        log(f"ERROR: CSV not found at {csv_path}")
        return 1

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        log("CSV is empty — nothing to upload.")
        return 0

    header = rows[0]
    data_rows = rows[1:]  # skip header — sheet already has one
    log(f"Read {len(data_rows)} data rows, {len(header)} columns.")

    if not data_rows:
        log("No data rows after header — nothing to upload.")
        return 0

    # --- Check if sheet has a header; if empty, write header first ---
    existing = worksheet.get_all_values()
    if not existing:
        log("Sheet is empty — writing header row first.")
        worksheet.append_row(header, value_input_option="USER_ENTERED")

    # --- Append data rows ---
    worksheet.append_rows(data_rows, value_input_option="USER_ENTERED")
    log(f"Successfully appended {len(data_rows)} rows to '{tab_name}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
