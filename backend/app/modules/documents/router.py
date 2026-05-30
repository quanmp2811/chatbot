import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from bson import ObjectId

from app.db.mongo import drive_files_collection, get_db
from app.modules.users.router import get_current_user
from app.modules.documents.drive_service import list_drive_files
from app.services.company_state import require_company_with_access

router = APIRouter(prefix="/tai-lieu", tags=["Tài liệu"])


@router.get("/danh-sach")
def lay_danh_sach_file(
    current_user=Depends(get_current_user),
    db=Depends(get_db)
):
    from app.modules.companies.router import maybe_refresh_drive_token

    company_id, company = require_company_with_access(db, current_user)

    # ❌ Không chặn theo refresh_token nữa
    if not company.get("drive_token"):
        raise HTTPException(status_code=400, detail="Doanh nghiệp chưa kết nối Google Drive")

    # ✅ Lấy token hợp lệ (tự refresh nếu cần)
    token = maybe_refresh_drive_token(company)

    if not token:
        raise HTTPException(status_code=400, detail="Không lấy được token Google Drive, vui lòng kết nối lại")

    try:
        files = list_drive_files(company)  # bên trong nhớ dùng token đã refresh
    except HTTPException as e:
        # ❌ Không được throw 401 ở đây
        if e.status_code == 401:
            raise HTTPException(status_code=400, detail="Token Google Drive hết hạn hoặc bị thu hồi, vui lòng kết nối lại")
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi lấy tài liệu từ Google Drive: {e}")

    return {
        "thanh_cong": True,
        "so_luong": len(files),
        "danh_sach": files
    }


@router.get("/tai-ve/{file_id}")
def tai_ve_file_noi_bo(file_id: str, current_user=Depends(get_current_user)):
    company_id, _company = require_company_with_access(get_db(), current_user)

    doc = drive_files_collection.find_one(
        {"company_id": company_id, "file_id": file_id},
        {"file_path": 1, "file_name": 1, "source": 1},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")

    if doc.get("source") != "local_upload":
        raise HTTPException(status_code=400, detail="Tài liệu này không phải file upload nội bộ")

    file_path = doc.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File không còn tồn tại trên máy chủ")

    return FileResponse(
        path=file_path,
        filename=doc.get("file_name") or os.path.basename(file_path),
    )
