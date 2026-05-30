# app/modules/documents/drive_service.py
import requests
from datetime import datetime, timedelta
from fastapi import HTTPException

from app.core.config import settings
from app.db.mongo import get_db

GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


def list_drive_files(company: dict):
    """
    Lấy danh sách file Google Drive của doanh nghiệp.
    Tự động refresh token nếu sắp hết hạn.
    Không throw 401 để tránh làm logout user hệ thống.
    """

    from app.modules.companies.router import maybe_refresh_drive_token

    # Lấy token hợp lệ, tự refresh nếu cần
    access_token = maybe_refresh_drive_token(company)

    if not access_token:
        raise HTTPException(status_code=400, detail="Chưa kết nối Google Drive")

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    files = []
    page_token = None
    shared_drive_id = str(company.get("shared_drive_id") or "").strip()

    while True:
        params = {
            "pageSize": 1000,
            "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size, quotaBytesUsed, webViewLink, owners(displayName, emailAddress))",
            "q": "trashed=false and mimeType!='application/vnd.google-apps.folder'",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if shared_drive_id:
            params["corpora"] = "drive"
            params["driveId"] = shared_drive_id
        else:
            params["corpora"] = "allDrives"
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(GOOGLE_DRIVE_FILES_URL, headers=headers, params=params, timeout=20)

        if resp.status_code == 401:
            # Không throw 401 để tránh logout user
            raise HTTPException(status_code=400, detail="Token Google Drive hết hạn hoặc bị thu hồi, vui lòng kết nối lại Drive")

        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Lỗi Google Drive: {resp.text}")

        data = resp.json()
        files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return files

# Download file content
def download_drive_file(company: dict, file_id: str, mime_type: str):
    from app.modules.companies.router import maybe_refresh_drive_token

    access_token = maybe_refresh_drive_token(company)

    if not access_token:
        raise HTTPException(status_code=400, detail="Chưa kết nối Google Drive")

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # Nếu là Google Docs / Sheets / Slides thì dùng export
    if mime_type.startswith("application/vnd.google-apps"):

        export_map = {
            "application/vnd.google-apps.document":
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.google-apps.spreadsheet":
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.google-apps.presentation":
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.google-apps.drawing":
                "image/png",
        }

        url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
        export_mime = export_map.get(mime_type)

        if export_mime:
            params = {"mimeType": export_mime, "supportsAllDrives": True}
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        else:
            # Fallback cho một số Google file đặc thù.
            resp = None
            for candidate in ("application/pdf", "text/plain"):
                params = {"mimeType": candidate, "supportsAllDrives": True}
                trial = requests.get(url, headers=headers, params=params, timeout=30)
                if trial.status_code == 200:
                    resp = trial
                    break

            if resp is None:
                raise HTTPException(status_code=400, detail="Unsupported Google file type")

    else:
        # File nhị phân như PDF, DOCX, TXT
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        params = {"alt": "media", "supportsAllDrives": True}

        resp = requests.get(url, headers=headers, params=params, timeout=30)

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Không thể tải file: {resp.text}")

    return resp.content

