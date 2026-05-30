import hashlib
import os
import time
from mimetypes import guess_extension

from fastapi import HTTPException
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.drive_log import log_drive_error, log_drive_info
from app.db.mongo import get_db
from app.modules.companies.router import enforce_company_storage_limit
from app.modules.documents.drive_service import list_drive_files, download_drive_file
from app.db.mongo import drive_files_collection
from app.services.file_parser import extract_text
from app.services.file_parser import extract_google_file
from app.services.chunking import split_text_with_headings
from app.services.ai.vector_service import add_vectors, delete_file_vectors, has_file_vectors


COMPRESSED_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".tbz", ".tbz2", ".txz"
}

COMPRESSED_MIME_PREFIXES = (
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/x-7z-compressed",
    "application/gzip",
    "application/x-gzip",
    "application/x-tar",
    "application/x-bzip",
    "application/x-bzip2",
    "application/x-xz",
)

GOOGLE_NATIVE_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
}

UNSUPPORTED_GOOGLE_MIME_TYPES = {
    "application/vnd.google-apps.audio",
    "application/vnd.google-apps.file",
    "application/vnd.google-apps.folder",
    "application/vnd.google-apps.form",
    "application/vnd.google-apps.fusiontable",
    "application/vnd.google-apps.jam",
    "application/vnd.google-apps.mail-layout",
    "application/vnd.google-apps.map",
    "application/vnd.google-apps.photo",
    "application/vnd.google-apps.script",
    "application/vnd.google-apps.shortcut",
    "application/vnd.google-apps.site",
    "application/vnd.google-apps.unknown",
    "application/vnd.google-apps.video",
}

SYNC_DEBUG = False


def _sync_log(message: str):
    log_drive_info(message)
    if SYNC_DEBUG:
        print(message)


def _coerce_storage_bytes(file: dict) -> int:
    for key in ("size", "quotaBytesUsed", "storage_bytes"):
        raw_value = file.get(key)
        try:
            if raw_value is not None:
                parsed = int(raw_value)
                if parsed >= 0:
                    return parsed
        except (TypeError, ValueError):
            continue
    return 0


def is_compressed_file(file: dict) -> bool:
    mime_type = (file.get("mimeType") or "").lower()
    name = (file.get("name") or "").lower()

    if any(mime_type.startswith(prefix) for prefix in COMPRESSED_MIME_PREFIXES):
        return True

    return any(name.endswith(ext) for ext in COMPRESSED_EXTENSIONS)


def sync_drive(company: dict):

    t0 = time.perf_counter()
    company_id = str(company["_id"])
    if not (company.get("drive_token") or company.get("drive_refresh_token")):
        _sync_log(f"[sync] Skip company={company_id}: no Drive connection")
        return

    try:
        drive_files = list_drive_files(company)
    except HTTPException as exc:
        if exc.status_code == 400 and "ket noi Google Drive" in str(exc.detail):
            _sync_log(f"[sync] Skip company={company_id}: {exc.detail}")
            return
        log_drive_error(f"[sync] Failed company={company_id}: {exc.detail}")
        raise

    _sync_log(f"[sync] Start company={company_id} files={len(drive_files)}")

    for i, file in enumerate(drive_files, start=1):
        _sync_log(f"[sync] ({i}/{len(drive_files)}) {file.get('name')} | {file.get('mimeType')}")
        try:
            if is_compressed_file(file):
                _sync_log(f"[sync] Skip compressed file: {file.get('name')}")
                continue

            existing = drive_files_collection.find_one({
                "company_id": company_id,
                "file_id": file["id"]
            })

            # File mới
            if not existing:
                _sync_log(f"[sync] New file: {file.get('name')}")
                process_new_file(company, file)

            # File sửa
            else:
                if not has_file_vectors(company_id, file["id"]):
                    _sync_log(f"[sync] Missing vectors, re-indexing file: {file.get('name')}")
                    process_new_file(company, file)
                    continue

                # Skip sớm nếu modifiedTime không đổi để tránh tải + đọc + hash lại
                if existing.get("modified_time") == file.get("modifiedTime"):
                    _sync_log(f"[sync] Unchanged by modifiedTime: {file.get('name')}")
                    continue

                try:
                    text = extract_text_from_drive_file(company, file)
                except ValueError as exc:
                    _sync_log(f"[sync] Skip unsupported file: {file.get('name')} ({exc})")
                    continue

                if text is None:
                    _sync_log(f"[sync] Skip file: {file.get('name')}")
                    continue

                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

                # nếu hash khác mới update
                if existing.get("content_hash") != content_hash:
                    _sync_log(f"[sync] Updated content: {file.get('name')}")
                    process_updated_file(company, file, text, content_hash)
                else:
                    # Đồng bộ lại modified_time để lần sync sau skip sớm
                    drive_files_collection.update_one(
                        {"file_id": file["id"], "company_id": company_id},
                        {"$set": {"modified_time": file["modifiedTime"]}}
                    )
                    _sync_log(f"[sync] Unchanged content: {file.get('name')}")
        except Exception as exc:
            log_drive_error(
                f"[sync] File processing failed company={company_id} "
                f"file={file.get('name')} mime={file.get('mimeType')} error={exc}"
            )
            continue

    check_deleted_files(company, drive_files)
    _sync_log(f"[sync] Done in {time.perf_counter() - t0:.2f}s")


def process_new_file(company, file):

    t0 = time.perf_counter()
    company_id = str(company["_id"])
    storage_bytes = _coerce_storage_bytes(file)
    db = get_db()

    try:
        enforce_company_storage_limit(db, company_id, company, storage_bytes)
    except HTTPException as exc:
        _sync_log(f"[sync] Skip over quota: {file.get('name')} ({exc.detail})")
        return

    try:
        text = extract_text_from_drive_file(company, file)
    except ValueError as exc:
        _sync_log(f"[sync] Skip unsupported file: {file.get('name')} ({exc})")
        return

    if text is None:
        return

    # Tính hash nội dung
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    chunks = split_text_with_headings(text)
    _sync_log(f"[sync] Chunked {file.get('name')} -> {len(chunks)} chunks")

    add_vectors(company_id, file["id"], chunks, file_name=file.get("name"))

    drive_files_collection.update_one(
        {"company_id": company_id, "file_id": file["id"]},
        {
            "$set": {
                "file_name": file["name"],
                "modified_time": file["modifiedTime"],
                "content_hash": content_hash,
                "indexed": True,
                "storage_bytes": storage_bytes,
            }
        },
        upsert=True,
    )
    _sync_log(f"[sync] Indexed new file {file.get('name')} in {time.perf_counter() - t0:.2f}s")


def _temp_extension_for_mime(mime_type: str) -> str:
    export_ext_map = {
        "application/vnd.google-apps.document": ".docx",
        "application/vnd.google-apps.spreadsheet": ".xlsx",
        "application/vnd.google-apps.presentation": ".pptx",
        "application/vnd.google-apps.drawing": ".png",
    }

    if mime_type in export_ext_map:
        return export_ext_map[mime_type]

    if mime_type.startswith("application/vnd.google-apps"):
        return ".pdf"

    return guess_extension(mime_type) or ".bin"


def _build_drive_service(company: dict):
    from app.modules.companies.router import maybe_refresh_drive_token

    access_token = maybe_refresh_drive_token(company)
    if not access_token:
        raise HTTPException(status_code=400, detail="Chưa kết nối Google Drive")

    credentials = Credentials(token=access_token)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def extract_text_from_drive_file(company, file):
    mime_type = file.get("mimeType", "")
    t0 = time.perf_counter()

    if mime_type in UNSUPPORTED_GOOGLE_MIME_TYPES:
        _sync_log(f"[sync] Skip unsupported Google MIME: {mime_type} ({file.get('name')})")
        return None

    if mime_type in GOOGLE_NATIVE_MIME_TYPES:
        try:
            service = _build_drive_service(company)
            text = extract_google_file(service, file["id"], mime_type)
            _sync_log(
                f"[sync] Read native Google file {file.get('name')} text={len(text)} chars "
                f"in {time.perf_counter() - t0:.2f}s"
            )
            if not text or len(text) < 20:
                _sync_log(f"[sync] Skip empty text: {file.get('name')}")
                return None
            return text
        except Exception as exc:
            log_drive_error(f"[sync] Failed native Google file {file.get('name')}: {exc}")
            raise HTTPException(status_code=400, detail=f"Không thể đọc file Google Drive: {exc}") from exc

    try:
        content_bytes = download_drive_file(company, file["id"], mime_type)
    except HTTPException as e:
        if e.status_code == 400 and "Unsupported Google file type" in str(e.detail):
            _sync_log(f"Skip unsupported Drive file type: {mime_type} ({file.get('name')})")
            return None
        log_drive_error(f"[sync] Download failed file={file.get('name')} error={e.detail}")
        raise

    ext = _temp_extension_for_mime(mime_type)
    temp_path = f"temp_{file['id']}{ext}"

    try:
        with open(temp_path, "wb") as f:
            f.write(content_bytes)
        text = extract_text(temp_path)
        _sync_log(f"[sync] Extracted text length: {len(text)}")
        _sync_log(
            f"[sync] Read {file.get('name')} size={len(content_bytes)}B text={len(text)} chars "
            f"in {time.perf_counter() - t0:.2f}s"
        )
        if not text or len(text) < 20:
            _sync_log(f"[sync] Skip empty text: {file.get('name')}")
            return None
        return text
    except ValueError:
        raise
    except Exception as exc:
        log_drive_error(f"[sync] Extract failed file={file.get('name')} error={exc}")
        raise
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def process_updated_file(company, file, text, content_hash):

    t0 = time.perf_counter()
    company_id = str(company["_id"])
    storage_bytes = _coerce_storage_bytes(file)
    db = get_db()
    existing = drive_files_collection.find_one({"file_id": file["id"], "company_id": company_id}) or {}

    try:
        enforce_company_storage_limit(
            db,
            company_id,
            company,
            storage_bytes,
            replacing_size_bytes=int(existing.get("storage_bytes") or 0),
        )
    except HTTPException as exc:
        _sync_log(f"[sync] Skip updated over quota: {file.get('name')} ({exc.detail})")
        return

    # xoá vector cũ
    delete_file_vectors(company_id, file["id"])

    chunks = split_text_with_headings(text)

    add_vectors(company_id, file["id"], chunks, file_name=file.get("name"))

    drive_files_collection.update_one(
        {"file_id": file["id"], "company_id": company_id},
        {"$set": {
            "modified_time": file["modifiedTime"],
            "content_hash": content_hash,
            "indexed": True,
            "storage_bytes": storage_bytes,
        }}
    )
    _sync_log(f"[sync] Re-indexed updated file {file.get('name')} in {time.perf_counter() - t0:.2f}s")


def check_deleted_files(company, drive_files):

    company_id = str(company["_id"])
    drive_file_ids = {file["id"] for file in drive_files}

    stored_files = drive_files_collection.find({"company_id": company_id})
    deleted_count = 0

    for stored_file in stored_files:

        if stored_file["file_id"] not in drive_file_ids:

            # Xoá vector luôn
            delete_file_vectors(company_id, stored_file["file_id"])

            drive_files_collection.delete_one({"_id": stored_file["_id"]})
            deleted_count += 1

    if deleted_count:
        _sync_log(f"[sync] Deleted {deleted_count} missing files from index")
