from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from app.db.mongo import get_db
from app.modules.companies.router import enforce_company_account_limit
from app.modules.users.router import get_current_user, purge_user_data_in_company
from app.services.company_state import require_company_with_access
from bson import ObjectId


class AddAdminPayload(BaseModel):
    email: str
    position: str = ""  # Tên vị trí làm việc (tùy chọn)


class UpdateAdminPayload(BaseModel):
    name: str
    position: str = ""

router = APIRouter(prefix="/admin", tags=["Admin"])

# Lấy danh sách admin trong doanh nghiệp
@router.get("/admins")
def list_admins(user=Depends(get_current_user)):
    if user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    db = get_db()
    company_id, _company = require_company_with_access(db, user)
    company_id = ObjectId(company_id)
    
    admins = list(db.users.find({
        "company_id": company_id,
        "role": {"$in": ["admin", "super_admin"]}
    }, {"password": 0}))

    # Convert ObjectId -> string
    for u in admins:
        u["_id"] = str(u["_id"])
        u["company_id"] = str(u["company_id"])

    return admins


# Thêm admin
@router.post("/admins/add")
def add_admin(payload: AddAdminPayload, user=Depends(get_current_user)):
    if user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    email = payload.email.lower().strip()
    position = payload.position.strip() if payload.position else ""
    
    if not email:
        raise HTTPException(status_code=400, detail="Thiếu email")

    db = get_db()
    company_id, comp = require_company_with_access(db, user)
    existing_admin_count = db.users.count_documents({
        "company_id": ObjectId(company_id),
        "role": {"$in": ["admin", "super_admin"]},
    })
    if existing_admin_count >= 1:
        raise HTTPException(status_code=400, detail="Mỗi doanh nghiệp chỉ được có 1 admin")

    # Tìm user theo email (case-insensitive)
    target = db.users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not target or str(target.get("company_id") or "") != str(company_id):
        enforce_company_account_limit(db, company_id, comp)
    if not target:
        # Nếu user chưa từng đăng nhập, tạo user mới
        target = {
            "email": email,
            "name": email.split("@")[0],
            "position": position,
            "role": "admin",
            "company_id": ObjectId(company_id)
        }
        db.users.insert_one(target)
        return {"message": "Đã tạo và thêm admin thành công"}

    # User đã tồn tại, cập nhật thành admin
    update_fields = {"role": "admin", "company_id": ObjectId(company_id)}
    if position:
        update_fields["position"] = position
    
    # Nếu công ty có drive_token thì thêm vào user luôn
    if comp and comp.get("drive_token"):
        update_fields["drive_token"] = comp.get("drive_token")

    db.users.update_one(
        {"_id": target["_id"]},
        {"$set": update_fields}
    )
    # Trả về object user mới/cập nhật để frontend có thể hiển thị ngay
    updated = db.users.find_one({"_id": target["_id"]})
    updated["_id"] = str(updated["_id"])
    updated["company_id"] = str(updated["company_id"]) if updated.get("company_id") else None
    return {"message": "Đã thêm admin thành công", "user": {
        "_id": updated["_id"],
        "email": updated.get("email"),
        "name": updated.get("name"),
        "position": updated.get("position"),
        "role": updated.get("role")
    }}


# Sửa thông tin admin
@router.put("/admins/{user_id}")
def update_admin(user_id: str, payload: UpdateAdminPayload, user=Depends(get_current_user)):
    if user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    name = payload.name.strip() if payload.name else ""
    position = payload.position.strip() if payload.position else ""

    if not name:
        raise HTTPException(status_code=400, detail="Tên không được để trống")

    db = get_db()
    require_company_with_access(db, user)
    result = db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"name": name, "position": position}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Không tìm thấy admin")

    return {"message": "Đã cập nhật thông tin admin"}


# Xóa admin, hạ quyền về user
@router.delete("/admins/{user_id}")
def remove_admin(user_id: str, user=Depends(get_current_user)):
    if user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    db = get_db()
    company_id, _company = require_company_with_access(db, user)
    target = db.users.find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status_code=404, detail="Không tìm thấy admin")

    if str(target.get("company_id")) != company_id:
        raise HTTPException(status_code=403, detail="Admin không thuộc công ty của bạn")

    purge_user_data_in_company(db, str(target["_id"]), company_id)
    return {"message": "Đã xóa admin và toàn bộ dữ liệu liên quan"}
