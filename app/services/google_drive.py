from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from app.core.config import settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class DriveUploadResult:
    folder_id: str
    file_id: str
    file_name: str
    web_view_link: str | None


def upload_gzip_backup(
    *,
    filename: str,
    content: bytes,
    folder_name: str | None = None,
) -> DriveUploadResult:
    folder_name = folder_name or settings.google_drive_folder_name

    with httpx.Client(timeout=settings.google_drive_timeout_seconds) as client:
        access_token = _get_access_token(client)
        folder_id = _get_or_create_folder(
            client=client,
            access_token=access_token,
            folder_name=folder_name,
        )
        file_data = _upload_file(
            client=client,
            access_token=access_token,
            folder_id=folder_id,
            filename=filename,
            content=content,
        )

    return DriveUploadResult(
        folder_id=folder_id,
        file_id=str(file_data["id"]),
        file_name=str(file_data.get("name", filename)),
        web_view_link=(
            str(file_data["webViewLink"])
            if file_data.get("webViewLink") is not None
            else None
        ),
    )


def _get_access_token(client: httpx.Client) -> str:
    required_settings = {
        "GOOGLE_DRIVE_CLIENT_ID": settings.google_drive_client_id,
        "GOOGLE_DRIVE_CLIENT_SECRET": settings.google_drive_client_secret,
        "GOOGLE_DRIVE_REFRESH_TOKEN": settings.google_drive_refresh_token,
    }
    missing = [name for name, value in required_settings.items() if not value]

    if missing:
        raise RuntimeError(
            "Google Drive backup is not configured. Missing: " + ", ".join(missing)
        )

    response = client.post(
        TOKEN_URL,
        data={
            "client_id": settings.google_drive_client_id,
            "client_secret": settings.google_drive_client_secret,
            "refresh_token": settings.google_drive_refresh_token,
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()

    access_token = response.json().get("access_token")
    if not access_token:
        raise RuntimeError("Google OAuth token response does not contain access_token")

    return str(access_token)


def _get_or_create_folder(
    *,
    client: httpx.Client,
    access_token: str,
    folder_name: str,
) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}
    query_name = _escape_drive_query_literal(folder_name)
    query = (
        f"name = '{query_name}' and mimeType = '{FOLDER_MIME_TYPE}' "
        "and 'root' in parents and trashed = false"
    )

    response = client.get(
        DRIVE_FILES_URL,
        headers=headers,
        params={
            "q": query,
            "spaces": "drive",
            "fields": "files(id,name)",
            "pageSize": 1,
        },
    )
    response.raise_for_status()

    files = response.json().get("files", [])
    if files:
        return str(files[0]["id"])

    response = client.post(
        DRIVE_FILES_URL,
        headers=headers,
        params={"fields": "id,name"},
        json={
            "name": folder_name,
            "mimeType": FOLDER_MIME_TYPE,
        },
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _upload_file(
    *,
    client: httpx.Client,
    access_token: str,
    folder_id: str,
    filename: str,
    content: bytes,
) -> dict[str, object]:
    metadata = {
        "name": filename,
        "parents": [folder_id],
    }
    response = client.post(
        DRIVE_UPLOAD_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "uploadType": "multipart",
            "fields": "id,name,webViewLink,size,md5Checksum",
        },
        files={
            "metadata": (
                None,
                json.dumps(metadata),
                "application/json; charset=UTF-8",
            ),
            "file": (filename, content, "application/gzip"),
        },
    )
    response.raise_for_status()
    return dict(response.json())


def _escape_drive_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
