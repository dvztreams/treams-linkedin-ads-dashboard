"""
Export the generated CSVs to a fixed Google Drive folder so Cowork can read
them from a stable location.

Auth: same service account as sheet_writer (path resolved via
_resolve_credentials). Target folder: GOOGLE_DRIVE_EXPORT_FOLDER_ID.

Each local CSV is written under a fixed "_latest" name. If a file with that
name already exists in the target folder it is updated in place (the file ID
stays stable), otherwise it is created. This way Cowork always reads the same
files at the same location.

The target folder lives in a Shared Drive, so every Drive call uses
supportsAllDrives=True / includeItemsFromAllDrives=True.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from sheet_writer import _resolve_credentials

load_dotenv()

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Mapping: local csv filename -> fixed name in the Drive folder
CSV_TO_DRIVE_NAME = {
    "analytics_latest.csv": "campaigns_latest.csv",
    "analytics_campaigns_daily.csv": "campaigns_daily_latest.csv",
    "analytics_ads.csv": "ads_latest.csv",
    "analytics_ads_daily.csv": "ads_daily_latest.csv",
    "analytics_companies.csv": "companies_latest.csv",
}


def _find_existing_file(service, folder_id: str, name: str) -> str | None:
    """Return the file ID of an existing (non-trashed) file with this name in
    the folder, or None."""
    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
    )
    resp = (
        service.files()
        .list(
            q=query,
            fields="files(id, name)",
            spaces="drive",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def _upload_one(service, folder_id: str, local_path: Path, drive_name: str) -> None:
    media = MediaFileUpload(str(local_path), mimetype="text/csv", resumable=False)
    existing_id = _find_existing_file(service, folder_id, drive_name)

    if existing_id:
        service.files().update(
            fileId=existing_id,
            media_body=media,
            supportsAllDrives=True,
        ).execute()
        print(f"  ✓ {drive_name} (updated)")
    else:
        service.files().create(
            body={"name": drive_name, "parents": [folder_id]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()
        print(f"  + {drive_name} (created)")


def export_all(base_dir: Path | None = None) -> None:
    base = base_dir or Path(__file__).parent
    folder_id = os.getenv("GOOGLE_DRIVE_EXPORT_FOLDER_ID")
    if not folder_id:
        print("⚠ Skipping Drive export — GOOGLE_DRIVE_EXPORT_FOLDER_ID not set")
        return

    creds_path = _resolve_credentials(base)
    if not creds_path:
        print("⚠ Skipping Drive export — no service account credentials available")
        return

    print(f"\nExporting CSVs to Google Drive folder {folder_id[:8]}...")
    creds = Credentials.from_service_account_file(creds_path, scopes=DRIVE_SCOPES)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    for csv_name, drive_name in CSV_TO_DRIVE_NAME.items():
        local_path = base / csv_name
        if not local_path.exists():
            print(f"  ⚠ {csv_name}: not found, skipping")
            continue
        try:
            _upload_one(service, folder_id, local_path, drive_name)
        except Exception as e:
            print(f"  ✗ {csv_name}: {e}")


if __name__ == "__main__":
    export_all()
