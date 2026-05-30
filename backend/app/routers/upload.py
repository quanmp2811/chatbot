import os
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.db.mongo import drive_files_collection, get_db
from app.modules.companies.router import enforce_company_storage_limit
from app.modules.users.router import get_current_user
from app.services.company_state import require_company_with_access
from app.services.ai.vector_service import add_vectors
from app.services.chunking import split_text_with_headings
from app.services.file_reader import read_file_content

router = APIRouter()

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    file_id = str(uuid4())
    db = get_db()
    company_id, company = require_company_with_access(db, user)

    safe_name = os.path.basename(file.filename or "upload.bin")
    company_upload_dir = os.path.join(UPLOAD_FOLDER, company_id)
    os.makedirs(company_upload_dir, exist_ok=True)
    file_path = os.path.join(company_upload_dir, f"{file_id}_{safe_name}")

    file_bytes = await file.read()
    enforce_company_storage_limit(db, company_id, company, len(file_bytes))

    with open(file_path, "wb") as f:
        f.write(file_bytes)

    try:
        text = read_file_content(file_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Không thể đọc file: {exc}") from exc

    if not text.strip():
        raise HTTPException(status_code=400, detail="Không trích xuất được nội dung từ file")

    chunks = split_text_with_headings(text)

    add_vectors(
        company_id=company_id,
        file_id=file_id,
        chunks=chunks,
        file_name=safe_name,
    )

    drive_files_collection.update_one(
        {"company_id": company_id, "file_id": file_id},
        {
            "$set": {
                "company_id": company_id,
                "file_id": file_id,
                "file_name": safe_name,
                "file_path": file_path,
                "mime_type": file.content_type,
                "indexed": True,
                "source": "local_upload",
                "modified_time": datetime.utcnow().isoformat(),
                "storage_bytes": len(file_bytes),
            }
        },
        upsert=True,
    )

    return {
        "filename": safe_name,
        "chunks_indexed": len(chunks),
        "message": "File đã được index tự động",
    }
