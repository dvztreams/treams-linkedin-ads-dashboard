"""
Push local CSVs to specific tabs in the configured Google Sheet.

Auth: service account JSON file (path in GOOGLE_SERVICE_ACCOUNT_JSON).
Target Sheet: GOOGLE_SHEET_ID.

Reads each CSV, opens (or creates) the matching worksheet, clears it,
and writes the rows in one batch update.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

import gspread
from dotenv import load_dotenv

load_dotenv()

# Mapping: csv_filename -> worksheet tab name in the Sheet
CSV_TO_TAB = {
    "analytics_latest.csv": "campaigns",
    "analytics_ads.csv": "ads",
    "analytics_campaigns_daily.csv": "campaigns_daily",
    "analytics_ads_daily.csv": "ads_daily",
    "analytics_companies.csv": "companies",
}


def _coerce_value(v: str):
    """Convert CSV string cells into proper types so Sheets gets numbers, not text."""
    if v == "":
        return ""
    # Try int first, then float, otherwise leave as string
    try:
        if "." not in v and "e" not in v.lower():
            return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _read_csv(path: Path) -> list[list]:
    with path.open(newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    data = [[_coerce_value(c) for c in row] for row in rows[1:]]
    return [header] + data


def push_csv_to_tab(sh, csv_path: Path, tab_name: str) -> None:
    rows = _read_csv(csv_path)
    if not rows:
        print(f"  ⚠ {csv_path.name}: empty, skipping")
        return

    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        # Create with enough rows/cols for the data
        n_rows = max(len(rows) + 100, 1000)
        n_cols = max(len(rows[0]) + 5, 26)
        ws = sh.add_worksheet(title=tab_name, rows=n_rows, cols=n_cols)
        print(f"  + Created new tab '{tab_name}'")

    ws.clear()
    ws.update(range_name="A1", values=rows)
    print(f"  ✓ {csv_path.name} → tab '{tab_name}' ({len(rows) - 1} rows)")


def _resolve_credentials(base: Path) -> str | None:
    """Return a filesystem path to the service account JSON, writing a temp file
    if the credentials come from an env var (GitHub Actions case)."""
    # Option 1: full JSON content in env var (used by GitHub Actions)
    json_content = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT")
    if json_content:
        try:
            json.loads(json_content)  # sanity check
        except json.JSONDecodeError as e:
            print(f"⚠ GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT is not valid JSON: {e}")
            return None
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="sa_"
        )
        tmp.write(json_content)
        tmp.close()
        return tmp.name

    # Option 2: file path on disk (used locally)
    sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_path:
        return None
    sa_full = (base / sa_path) if not os.path.isabs(sa_path) else Path(sa_path)
    if not sa_full.exists():
        print(f"⚠ Service account JSON not found at {sa_full}")
        return None
    return str(sa_full)


def push_all(base_dir: Path | None = None) -> None:
    base = base_dir or Path(__file__).parent
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("⚠ Skipping Sheet upload — GOOGLE_SHEET_ID not set")
        return

    creds_path = _resolve_credentials(base)
    if not creds_path:
        print("⚠ Skipping Sheet upload — no service account credentials available")
        return

    print(f"\nPushing CSVs to Google Sheet {sheet_id[:8]}...")
    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_key(sheet_id)

    for csv_name, tab in CSV_TO_TAB.items():
        csv_path = base / csv_name
        if not csv_path.exists():
            print(f"  ⚠ {csv_name}: not found, skipping")
            continue
        try:
            push_csv_to_tab(sh, csv_path, tab)
        except Exception as e:
            print(f"  ✗ {csv_name}: {e}")


if __name__ == "__main__":
    push_all()
