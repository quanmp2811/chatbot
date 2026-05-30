# app/modules/users/router.py
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
import requests
import hashlib
import secrets
import re
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from app.db.mongo import get_db
from app.core.config import settings
from app.core.jwt import verify_access_token
from app.services.account_blocklist import normalize_email
from app.services.account_roles import resolve_user_role
from app.services.company_state import build_company_access_state, require_company_with_access
from app.services.email_service import send_password_changed_email, send_verification_code_email

router = APIRouter(prefix="/nguoi-dung", tags=["Người dùng"])


class AddUserPayload(BaseModel):
    email: str
    name: str = ""
    position: str = ""

def reset_user_to_standalone_account(db, user_id: str, session=None):
    target_user = db.users.find_one({"_id": ObjectId(user_id)}, session=session)
    if not target_user:
        return

    current_token_version = target_user.get("token_version", 0)
    db.users.update_one(
        {"_id": target_user["_id"]},
        {
            "$set": {
                "company_id": None,
                "role": "user",
                "token_version": current_token_version + 1,
            },
            "$unset": {
                "position": "",
                "drive_token": "",
            },
        },
        session=session,
    )


def purge_user_data_in_company(db, user_id: str, company_id: str):
    chat_docs = list(
        db.chats.find(
            {"user_id": user_id, "company_id": company_id},
            {"_id": 1}
        )
    )
    chat_ids = [str(c["_id"]) for c in chat_docs]

    if chat_ids:
        db.messages.delete_many({"chat_id": {"$in": chat_ids}})

    db.messages.delete_many({"user_id": user_id, "company_id": company_id})
    db.chats.delete_many({"user_id": user_id, "company_id": company_id})
    reset_user_to_standalone_account(db, user_id)

def serialize_user(user: dict):
    db = get_db()
    email_verified = user.get("email_verified")
    if email_verified is None:
        email_verified = bool(user.get("contact_email"))

    company = None
    company_id = str(user["company_id"]) if user.get("company_id") else None
    if company_id:
        try:
            company = db.companies.find_one({"_id": ObjectId(company_id)})
        except Exception:
            company = None

    resolved_role = resolve_user_role(user)
    company_access_state = build_company_access_state(company)
    trial_eligibility = {"trial_used": False, "paid_used": False}
    try:
        from app.modules.companies.router import get_super_admin_trial_eligibility

        trial_eligibility = get_super_admin_trial_eligibility(
            db,
            user.get("contact_email") or user.get("email"),
        )
    except Exception:
        trial_eligibility = {"trial_used": False, "paid_used": False}

    s = {
        "_id": str(user["_id"]) if user.get("_id") else None,
        "email": user["email"],
        "contact_email": user.get("contact_email"),
        "email_verified": bool(email_verified),
        "phone": user.get("phone"),
        "has_password": bool(user.get("password_hash")),
        "name": user.get("name"),
        "picture": user.get("picture"),
        "role": resolved_role,
        "company_id": company_id,
        "company_name": company.get("name") if company else None,
        "company_approval_status": (company or {}).get("approval_status"),
        "company_is_blocked": bool((company or {}).get("is_blocked")),
        "company_is_expired": bool((company or {}).get("is_expired")),
        "company_access_state": company_access_state,
        "can_use_trial": not bool(trial_eligibility.get("trial_used") or trial_eligibility.get("paid_used")),
    }
    # drive_token có thể được thêm vào trước khi gọi hàm này
    if user.get("drive_token"):
        s["drive_token"] = user["drive_token"]
    return s

def get_current_user(authorization: str = Header(None)):
    if not authorization:
        print("[users.me] missing_authorization")
        raise HTTPException(status_code=401, detail="Thiếu Authorization header")

    token = authorization.replace("Bearer ", "")
    db = get_db()

    jwt_payload = verify_access_token(token)
    if jwt_payload:
        user_id = jwt_payload.get("sub")
        token_version = jwt_payload.get("token_version", 0)
        if user_id:
            try:
                user = db.users.find_one({"_id": ObjectId(user_id)})
            except Exception:
                user = None
            if user:
                current_token_version = user.get("token_version", 0)
                if token_version == current_token_version:
                    print(f"[users.me] jwt_success user_id={user_id} role={user.get('role', 'user')}")
                    return serialize_user(user)
                print(f"[users.me] jwt_token_version_mismatch user_id={user_id} token_version={token_version} current={current_token_version}")
            else:
                print(f"[users.me] jwt_user_not_found user_id={user_id}")
        else:
            print("[users.me] jwt_missing_sub")

    # Internal JWTs should never fall back to Google userinfo.
    if token.count(".") == 2:
        print("[users.me] invalid_internal_jwt")
        raise HTTPException(status_code=401, detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn")

    print("[users.me] fallback_google_userinfo")
    res = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )

    if res.status_code != 200:
        print(f"[users.me] google_userinfo_failed status={res.status_code}")
        raise HTTPException(
            status_code=401, 
            detail="Token Google không hợp lệ hoặc quyền bị thu hồi"
        )

    google_user = res.json()
    email = normalize_email(google_user["email"])
    name = google_user.get("name")
    picture = google_user.get("picture")
    google_sub = google_user.get("sub")

    user = db.users.find_one({"email": email})

    if not user:
        print(f"[users.me] user_not_found_after_google email={email}")
        raise HTTPException(
            status_code=401,
            detail="Tài khoản không còn tồn tại trong hệ thống. Vui lòng đăng nhập lại.",
        )
    else:
        # Chỉ đồng bộ tên từ Google khi user chưa có tên trong hệ thống
        update_fields = {"picture": picture, "google_sub": google_sub}
        if not user.get("name") and name:
            update_fields["name"] = name
        db.users.update_one({"email": email}, {"$set": update_fields})
        user.update(update_fields)
        # nếu user đã thuộc doanh nghiệp thì lấy token drive từ document công ty
        if user.get("company_id") and resolve_user_role(user) != "developer":
            try:
                comp = db.companies.find_one({"_id": ObjectId(user["company_id"])})
                if comp and comp.get("drive_token"):
                    # cố gắng làm mới nếu có refresh token
                    from app.modules.companies.router import maybe_refresh_drive_token
                    token = maybe_refresh_drive_token(comp)
                    user["drive_token"] = token
                    # lưu lại vào user để lần sau không phải truy vấn công ty nữa
                    db.users.update_one(
                        {"email": email},
                        {"$set": {"drive_token": token}}
                    )
            except Exception:
                pass

    print(f"[users.me] google_success email={email} role={user.get('role', 'user')}")
    return serialize_user(user)

@router.get("/me")
def lay_thong_tin_user(authorization: str = Header(None)):
    return get_current_user(authorization)


class UpdateProfilePayload(BaseModel):
    name: str = ""
    phone: str = ""


class ChangePasswordPayload(BaseModel):
    current_password: str = ""
    new_password: str
    confirm_password: str


class CheckPasswordPayload(BaseModel):
    current_password: str = ""


class SendEmailCodePayload(BaseModel):
    email: str
    change_password: bool = False


class VerifyEmailCodePayload(BaseModel):
    email: str
    code: str


@router.put("/me")
def cap_nhat_thong_tin_ca_nhan(payload: UpdateProfilePayload, current_user=Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Không xác định được user")

    existing_user = db.users.find_one({"_id": ObjectId(user_id)})
    if not existing_user:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")
    if not (existing_user.get("contact_email") and existing_user.get("email_verified")):
        raise HTTPException(status_code=400, detail="Vui lòng xác thực email trước khi lưu thông tin")

    name = (payload.name or "").strip()
    phone = (payload.phone or "").strip()
    db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"name": name, "phone": phone}}
    )

    updated = db.users.find_one({"_id": ObjectId(user_id)})
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    return {"message": "Đã cập nhật thông tin", "user": serialize_user(updated)}


@router.post("/email/send-code")
def gui_ma_xac_thuc_email(payload: SendEmailCodePayload, current_user=Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Không xác định được user")

    email = (payload.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Vui lòng nhập email")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=400, detail="Email không hợp lệ")

    user_doc = db.users.find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    current_contact_email = (user_doc.get("contact_email") or "").strip().lower()
    current_verified = bool(user_doc.get("email_verified"))
    delivery_email = email
    is_change_email = bool(current_contact_email and current_verified and email != current_contact_email)
    if is_change_email:
        delivery_email = current_contact_email
    is_change_password = bool(payload.change_password)
    has_existing_password = bool(user_doc.get("password_hash"))

    if is_change_email and is_change_password:
        email_purpose = "change_email_and_password"
    elif is_change_email:
        email_purpose = "change_email"
    elif is_change_password and has_existing_password:
        email_purpose = "change_password"
    elif is_change_password and not has_existing_password:
        email_purpose = "create_password"
    else:
        email_purpose = "generic"

    now = datetime.now(timezone.utc)
    last_sent_at = user_doc.get("email_verification", {}).get("last_sent_at")
    if isinstance(last_sent_at, datetime):
        if last_sent_at.tzinfo is None:
            last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
        seconds = (now - last_sent_at).total_seconds()
        if seconds < 60:
            raise HTTPException(status_code=429, detail="Vui lòng chờ 60 giây trước khi gửi lại mã")

    code = str(secrets.randbelow(900000) + 100000)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires_at = now + timedelta(minutes=settings.EMAIL_CODE_EXPIRE_MINUTES)

    db.users.update_one(
        {"_id": ObjectId(user_id)},
        {
            "$set": {
                "email_verification": {
                    "current_email": current_contact_email or None,
                    "delivery_email": delivery_email,
                    "target_email": email,
                    "code_hash": code_hash,
                    "expires_at": expires_at,
                    "last_sent_at": now
                },
                "email_verified": False
            }
        }
    )

    try:
        send_verification_code_email(delivery_email, code, purpose=email_purpose)
    except Exception as e:
        if settings.EMAIL_DEBUG_RETURN_CODE:
            return {
                "message": "Không gửi được email qua SMTP. Đã tạo mã xác thực cho môi trường local.",
                "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60
            }
        raise HTTPException(status_code=500, detail=f"Gửi email thất bại: {str(e)}")

    response = {
        "message": "Đã gửi mã xác thực qua email",
        "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
        "delivery_email": delivery_email,
        "target_email": email
    }
    return response


@router.post("/email/verify-code")
def xac_thuc_ma_email(payload: VerifyEmailCodePayload, current_user=Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Không xác định được user")

    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    if not email or not code:
        raise HTTPException(status_code=400, detail="Thiếu email hoặc mã xác thực")

    user_doc = db.users.find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    verification = user_doc.get("email_verification") or {}
    target_email = (verification.get("target_email") or "").strip().lower()
    delivery_email = (verification.get("delivery_email") or "").strip().lower()
    code_hash = verification.get("code_hash")
    expires_at = verification.get("expires_at")

    if email != target_email or not code_hash or not expires_at:
        raise HTTPException(status_code=400, detail="Mã xác thực không hợp lệ hoặc đã hết hạn")

    now = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="Mã xác thực đã hết hạn")

    incoming_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if incoming_hash != code_hash:
        raise HTTPException(status_code=400, detail="Mã xác thực không đúng")

    db.users.update_one(
        {"_id": ObjectId(user_id)},
        {
            "$set": {
                "contact_email": target_email,
                "email_verified": True
            },
            "$unset": {"email_verification": ""}
        }
    )

    updated = db.users.find_one({"_id": ObjectId(user_id)})
    return {
        "message": "Xác thực email thành công",
        "delivery_email": delivery_email or None,
        "target_email": target_email,
        "user": serialize_user(updated),
    }


@router.post("/kiem-tra-mat-khau")
def kiem_tra_mat_khau_hien_tai(payload: CheckPasswordPayload, current_user=Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Không xác định được user")

    user_doc = db.users.find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    existing_hash = user_doc.get("password_hash")
    if not existing_hash:
        return {"valid": True}

    current_password = (payload.current_password or "").strip()
    if not current_password:
        raise HTTPException(status_code=400, detail="Vui lòng nhập mật khẩu hiện tại")

    current_hash = hashlib.sha256(current_password.encode("utf-8")).hexdigest()
    if current_hash != existing_hash:
        raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng")

    return {"valid": True}


@router.put("/doi-mat-khau")
def doi_mat_khau(payload: ChangePasswordPayload, current_user=Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Không xác định được user")

    new_password = (payload.new_password or "").strip()
    confirm_password = (payload.confirm_password or "").strip()
    current_password = (payload.current_password or "").strip()

    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải từ 6 ký tự")
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="Xác nhận mật khẩu không khớp")

    user_doc = db.users.find_one({"_id": ObjectId(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    existing_hash = user_doc.get("password_hash")
    if existing_hash:
        if not current_password:
            raise HTTPException(status_code=400, detail="Vui lòng nhập mật khẩu hiện tại")
        current_hash = hashlib.sha256(current_password.encode("utf-8")).hexdigest()
        if current_hash != existing_hash:
            raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng")

    new_hash = hashlib.sha256(new_password.encode("utf-8")).hexdigest()
    current_token_version = user_doc.get("token_version", 0)
    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"password_hash": new_hash, "token_version": current_token_version + 1}})

    notify_email = (user_doc.get("contact_email") or user_doc.get("email") or "").strip()
    if notify_email:
        try:
            send_password_changed_email(notify_email)
        except Exception:
            pass

    return {"message": "Đã cập nhật mật khẩu"}

# Danh sách user trong công ty
@router.get("/danh-sach")
def list_users(user=Depends(get_current_user)):
    if user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    db = get_db()
    company_id, _company = require_company_with_access(db, user)
    company_id = ObjectId(company_id)
    
    users = list(db.users.find(
        {"company_id": company_id, "role": "user"},
        {"password": 0}
    ))

    result = []
    for u in users:
        result.append({
            "_id": str(u["_id"]),
            "email": u["email"],
            "name": u.get("name"),
            "position": u.get("position"),
            "role": u.get("role", "user"),
            "picture": u.get("picture")
        })

    return result


# Thêm user vào công ty
@router.post("/them")
def add_user(payload: AddUserPayload, user=Depends(get_current_user)):
    if user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    email = normalize_email(payload.email)
    if not email:
        raise HTTPException(status_code=400, detail="Thiếu email")
    name = payload.name.strip() if payload.name else email.split("@")[0]
    position = payload.position.strip() if getattr(payload, 'position', None) else ""

    db = get_db()
    company_id, _company = require_company_with_access(db, user)

    # Tìm hoặc tạo user
    existing = db.users.find_one({"email": {"$regex": f"^{email}$", "$options": "i"}})
    if not existing or str(existing.get("company_id") or "") != str(company_id):
        from app.modules.companies.router import enforce_company_account_limit

        enforce_company_account_limit(db, company_id, _company)
    company_id = ObjectId(company_id)
    
    if existing:
        # Nếu user đã tồn tại, thêm vào công ty và đảm bảo role là "user"
        update_fields = {"company_id": company_id, "name": name, "role": "user"}
        if position:
            update_fields["position"] = position
        db.users.update_one(
            {"_id": existing["_id"]},
            {"$set": update_fields}
        )
        user_doc = db.users.find_one({"_id": existing["_id"]})
    else:
        # Tạo user mới
        new_user = {
            "email": email,
            "name": name,
            "position": position,
            "role": "user",
            "company_id": company_id
        }
        res = db.users.insert_one(new_user)
        user_doc = db.users.find_one({"_id": res.inserted_id})

    # Trả về user để frontend có thể hiển thị ngay
    user_doc["_id"] = str(user_doc["_id"])
    user_doc["company_id"] = str(user_doc["company_id"]) if user_doc.get("company_id") else None
    return {"message": "Đã thêm user thành công", "user": {
        "_id": user_doc["_id"],
        "email": user_doc.get("email"),
        "name": user_doc.get("name"),
        "position": user_doc.get("position"),
        "role": user_doc.get("role")
    }}


# Xóa user khỏi công ty
@router.delete("/{user_id}")
def remove_user(user_id: str, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    db = get_db()
    company_id, _company = require_company_with_access(db, current_user)
    target = db.users.find_one({"_id": ObjectId(user_id)})

    if not target:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    if str(target.get("company_id")) != company_id:
        raise HTTPException(status_code=403, detail="User không thuộc công ty của bạn")

    purge_user_data_in_company(db, user_id, company_id)
    return {"message": "Đã xóa user và toàn bộ dữ liệu liên quan"}


class UpdateUserPayload(BaseModel):
    name: str
    position: str = ""


@router.put("/{user_id}")
def update_user(user_id: str, payload: UpdateUserPayload, current_user=Depends(get_current_user)):
    if current_user["role"] not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    db = get_db()
    company_id, _company = require_company_with_access(db, current_user)
    target = db.users.find_one({"_id": ObjectId(user_id)})
    if not target:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    # Kiểm tra quyền: user phải thuộc cùng công ty
    if str(target.get("company_id")) != company_id:
        raise HTTPException(status_code=403, detail="User không thuộc công ty của bạn")

    name = payload.name.strip()
    position = payload.position.strip() if payload.position else ""

    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"name": name, "position": position}})

    updated = db.users.find_one({"_id": ObjectId(user_id)})
    updated["_id"] = str(updated["_id"])
    updated["company_id"] = str(updated["company_id"]) if updated.get("company_id") else None

    return {"message": "Đã cập nhật user", "user": {"_id": updated["_id"], "email": updated.get("email"), "name": updated.get("name"), "position": updated.get("position"), "role": updated.get("role")}}
