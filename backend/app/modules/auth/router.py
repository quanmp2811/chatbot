from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets

from fastapi import APIRouter, HTTPException, Depends
from bson import ObjectId
from google.oauth2 import id_token
from google.auth.transport import requests
from pydantic import BaseModel
from app.core.jwt import create_access_token, create_refresh_token, verify_refresh_token
from app.core.config import settings
from app.db.mongo import get_db
from app.services.account_blocklist import normalize_email
from app.services.account_roles import resolve_user_role
from app.services.email_service import send_password_changed_email, send_verification_code_email

router = APIRouter(prefix="/xac-thuc", tags=["Auth"])


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterSendCodePayload(BaseModel):
    name: str
    phone: str = ""
    email: str
    password: str
    confirm_password: str


class RegisterVerifyPayload(BaseModel):
    email: str
    code: str


class PasswordLoginPayload(BaseModel):
    identifier: str
    password: str
    remember_me: bool = False


class ForgotPasswordSendCodePayload(BaseModel):
    email: str


class ForgotPasswordResetPayload(BaseModel):
    email: str
    code: str
    new_password: str
    confirm_password: str


class RefreshTokenPayload(BaseModel):
    refresh_token: str


@router.get("/oauth-info")
def get_oauth_info():
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Chưa cấu hình GOOGLE_CLIENT_ID / GOOGLE_REDIRECT_URI")
    return {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
    }


@router.post("/dang-nhap-google")
def dang_nhap_google(payload: dict, db=Depends(get_db)):
    token = payload.get("id_token")
    print(f"[auth.google] login attempt has_id_token={bool(token)}")
    if not token:
        print("[auth.google] missing_id_token")
        raise HTTPException(status_code=400, detail="Thiếu id_token")

    try:
        idinfo = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            settings.GOOGLE_CLIENT_ID
        )
    except Exception as e:
        print(f"[auth.google] invalid_token error={str(e)}")
        raise HTTPException(status_code=401, detail=f"Token Google không hợp lệ: {str(e)}")

    email = normalize_email(idinfo.get("email"))
    name = idinfo.get("name")
    picture = idinfo.get("picture")
    google_sub = idinfo.get("sub")

    if not email:
        print("[auth.google] missing_email")
        raise HTTPException(status_code=400, detail="Không lấy được email từ Google")

    # Tìm hoặc tạo user
    blocked_account = None
    if blocked_account:
        print(f"[auth.google] blocked_account email={email} google_sub={google_sub}")
        raise HTTPException(
            status_code=403,
            detail="Tài khoản này đã bị xoá hoặc bị chặn. Vui lòng liên hệ quản trị viên.",
        )

    user = db.users.find_one({"email": email})
    if not user:
        db.users.insert_one({
            "email": email,
            "name": name,
            "picture": picture,
            "google_sub": google_sub,
            "role": "user",
            "company_id": None,
            "created_at": idinfo.get("iat"),
        })
        user = db.users.find_one({"email": email})
    else:
        # Cập nhật thông tin nếu có thay đổi
        db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"name": name, "picture": picture, "google_sub": google_sub}}
        )
        user = db.users.find_one({"_id": user["_id"]})

    # Tạo JWT nội bộ
    token_version = user.get("token_version", 0)
    if "token_version" not in user:
        db.users.update_one({"_id": user["_id"]}, {"$set": {"token_version": 0}})
        token_version = 0

    resolved_role = resolve_user_role(user)
    jwt_token = create_access_token({
        "sub": str(user["_id"]),
        "email": email,
        "role": resolved_role,
        "company_id": str(user.get("company_id")) if user.get("company_id") else None,
        "token_version": token_version,
    })

    return {
        "access_token": jwt_token,
        "user": {
            "email": email,
            "name": name,
            "picture": picture,
            "role": resolved_role,
            "company_id": str(user.get("company_id")) if user.get("company_id") else None
        }
    }


@router.post("/quen-mat-khau/gui-ma")
def quen_mat_khau_gui_ma(payload: ForgotPasswordSendCodePayload, db=Depends(get_db)):
    email = (payload.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Vui lòng nhập email")
    if not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Email không hợp lệ")

    user = db.users.find_one(
        {
            "$or": [
                {"email": email},
                {"contact_email": email},
            ]
        }
    )
    if not user or not user.get("password_hash"):
        print(f"[auth.password] user_not_found_or_no_password identifier={identifier}")
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản phù hợp")

    delivery_email = (user.get("contact_email") or user.get("email") or "").strip().lower()
    if not delivery_email:
        raise HTTPException(status_code=400, detail="Tài khoản chưa có email nhận mã")

    now = datetime.now(timezone.utc)
    pending = db.pending_password_resets.find_one({"user_id": str(user["_id"])})
    last_sent_at = (pending or {}).get("last_sent_at")
    if isinstance(last_sent_at, datetime):
        if last_sent_at.tzinfo is None:
            last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
        if (now - last_sent_at).total_seconds() < 60:
            raise HTTPException(status_code=429, detail="Vui lòng chờ 60 giây trước khi gửi lại mã")

    code = str(secrets.randbelow(900000) + 100000)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires_at = now + timedelta(minutes=settings.EMAIL_CODE_EXPIRE_MINUTES)

    db.pending_password_resets.update_one(
        {"user_id": str(user["_id"])},
        {
            "$set": {
                "user_id": str(user["_id"]),
                "requested_email": email,
                "delivery_email": delivery_email,
                "code_hash": code_hash,
                "expires_at": expires_at,
                "last_sent_at": now,
            }
        },
        upsert=True,
    )

    try:
        send_verification_code_email(delivery_email, code, purpose="reset_password")
    except Exception as exc:
        if settings.EMAIL_DEBUG_RETURN_CODE:
            return {
                "message": "Không gửi được email. Đã tạo mã xác thực cho môi trường local.",
                "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
                "delivery_email": delivery_email,
            }
        raise HTTPException(status_code=500, detail=f"Gửi email thất bại: {str(exc)}")

    return {
        "message": "Đã gửi mã xác thực",
        "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
        "delivery_email": delivery_email,
    }


@router.post("/quen-mat-khau/dat-lai")
def quen_mat_khau_dat_lai(payload: ForgotPasswordResetPayload, db=Depends(get_db)):
    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    new_password = (payload.new_password or "").strip()
    confirm_password = (payload.confirm_password or "").strip()

    if not email or not code:
        raise HTTPException(status_code=400, detail="Vui lòng nhập email và mã xác thực")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải từ 6 ký tự")
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="Xác nhận mật khẩu không khớp")

    user = db.users.find_one(
        {
            "$or": [
                {"email": email},
                {"contact_email": email},
            ]
        }
    )
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản phù hợp")

    pending = db.pending_password_resets.find_one({"user_id": str(user["_id"])})
    if not pending:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu lấy lại mật khẩu")

    expires_at = pending.get("expires_at")
    code_hash = pending.get("code_hash")
    if not expires_at or not code_hash:
        raise HTTPException(status_code=400, detail="Mã xác thực không hợp lệ")

    now = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="Mã xác thực đã hết hạn")

    incoming_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if incoming_hash != code_hash:
        raise HTTPException(status_code=400, detail="Mã xác thực không đúng")

    new_hash = hashlib.sha256(new_password.encode("utf-8")).hexdigest()
    db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": new_hash}})
    db.pending_password_resets.delete_one({"user_id": str(user["_id"])})

    notify_email = (user.get("contact_email") or user.get("email") or "").strip()
    if notify_email:
        try:
            send_password_changed_email(notify_email)
        except Exception:
            pass

    return {"message": "Đặt lại mật khẩu thành công"}


@router.post("/dang-nhap")
def dang_nhap_bang_mat_khau(payload: PasswordLoginPayload, db=Depends(get_db)):
    identifier = (payload.identifier or "").strip().lower()
    password = (payload.password or "").strip()
    print(f"[auth.password] login attempt identifier={identifier} remember_me={payload.remember_me}")

    if not identifier:
        print("[auth.password] missing_identifier")
        raise HTTPException(status_code=400, detail="Vui lòng nhập email hoặc số điện thoại")
    if not password:
        print(f"[auth.password] missing_password identifier={identifier}")
        raise HTTPException(status_code=400, detail="Vui lòng nhập mật khẩu")

    user = db.users.find_one(
        {
            "$or": [
                {"email": identifier},
                {"contact_email": identifier},
                {"phone": identifier},
            ]
        }
    )
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Tài khoản hoặc mật khẩu không đúng")

    password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    if password_hash != user.get("password_hash"):
        print(f"[auth.password] wrong_password identifier={identifier} user_id={user.get('_id')}")
        raise HTTPException(status_code=401, detail="Tài khoản hoặc mật khẩu không đúng")

    # Ensure token_version exists
    token_version = user.get("token_version", 0)
    if "token_version" not in user:
        db.users.update_one({"_id": user["_id"]}, {"$set": {"token_version": 0}})
        token_version = 0

    resolved_role = resolve_user_role(user)
    jwt_token = create_access_token({
        "sub": str(user["_id"]),
        "email": user.get("email"),
        "role": resolved_role,
        "company_id": str(user.get("company_id")) if user.get("company_id") else None,
        "token_version": token_version
    })

    response = {
        "access_token": jwt_token,
        "user": {
            "_id": str(user["_id"]),
            "email": user.get("email"),
            "name": user.get("name"),
            "picture": user.get("picture"),
            "role": resolved_role,
            "company_id": str(user.get("company_id")) if user.get("company_id") else None
        }
    }

    if payload.remember_me:
        refresh_token = create_refresh_token({
            "sub": str(user["_id"]),
            "token_version": token_version
        })
        response["refresh_token"] = refresh_token

    print(f"[auth.password] success identifier={identifier} user_id={user.get('_id')} role={user.get('role', 'user')}")
    return response


@router.post("/refresh")
def refresh_token(payload: RefreshTokenPayload, db=Depends(get_db)):
    refresh_payload = verify_refresh_token(payload.refresh_token)
    if not refresh_payload:
        raise HTTPException(status_code=401, detail="Refresh token không hợp lệ")

    user_id = refresh_payload.get("sub")
    token_version = refresh_payload.get("token_version", 0)

    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="User không tồn tại")

    current_token_version = user.get("token_version", 0)
    if token_version != current_token_version:
        raise HTTPException(status_code=401, detail="Token đã bị thu hồi")

    resolved_role = resolve_user_role(user)
    new_access_token = create_access_token({
        "sub": str(user["_id"]),
        "email": user.get("email"),
        "role": resolved_role,
        "company_id": str(user.get("company_id")) if user.get("company_id") else None,
        "token_version": current_token_version
    })

    return {"access_token": new_access_token}


@router.post("/dang-ky/gui-ma")
def dang_ky_gui_ma(payload: RegisterSendCodePayload, db=Depends(get_db)):
    name = (payload.name or "").strip()
    phone = (payload.phone or "").strip()
    email = (payload.email or "").strip().lower()
    password = payload.password or ""
    confirm_password = payload.confirm_password or ""

    if not name:
        raise HTTPException(status_code=400, detail="Họ tên là bắt buộc")
    if not email:
        raise HTTPException(status_code=400, detail="Email là bắt buộc")
    if not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Email không hợp lệ")
    if len(password.strip()) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu phải từ 6 ký tự")
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="Xác nhận mật khẩu không khớp")

    existing_user = db.users.find_one({"email": email})
    if existing_user and existing_user.get("password_hash"):
        raise HTTPException(status_code=400, detail="Email đã tồn tại")

    now = datetime.now(timezone.utc)
    pending = db.pending_registrations.find_one({"email": email})
    last_sent_at = (pending or {}).get("last_sent_at")
    if isinstance(last_sent_at, datetime):
        if last_sent_at.tzinfo is None:
            last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
        if (now - last_sent_at).total_seconds() < 60:
            raise HTTPException(status_code=429, detail="Vui lòng chờ 60 giây trước khi gửi lại mã")

    code = str(secrets.randbelow(900000) + 100000)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires_at = now + timedelta(minutes=settings.EMAIL_CODE_EXPIRE_MINUTES)
    password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()

    db.pending_registrations.update_one(
        {"email": email},
        {
            "$set": {
                "name": name,
                "phone": phone,
                "email": email,
                "password_hash": password_hash,
                "code_hash": code_hash,
                "expires_at": expires_at,
                "last_sent_at": now,
            }
        },
        upsert=True,
    )

    try:
        send_verification_code_email(email, code, purpose="create_password")
    except Exception as exc:
        if settings.EMAIL_DEBUG_RETURN_CODE:
            return {
                "message": "Không gửi được email. Trả mã debug để test local.",
                "debug_code": code,
                "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
            }
        raise HTTPException(status_code=500, detail=f"Gửi email thất bại: {str(exc)}")

    response = {
        "message": "Đã gửi mã xác thực qua email",
        "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
    }
    if settings.EMAIL_DEBUG_RETURN_CODE:
        response["debug_code"] = code
    return response


@router.post("/dang-ky/xac-thuc")
def dang_ky_xac_thuc(payload: RegisterVerifyPayload, db=Depends(get_db)):
    email = (payload.email or "").strip().lower()
    code = (payload.code or "").strip()
    if not email or not code:
        raise HTTPException(status_code=400, detail="Thiếu email hoặc mã xác thực")

    pending = db.pending_registrations.find_one({"email": email})
    if not pending:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu đăng ký")

    expires_at = pending.get("expires_at")
    code_hash = pending.get("code_hash")
    if not expires_at or not code_hash:
        raise HTTPException(status_code=400, detail="Mã xác thực không hợp lệ")

    now = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="Mã xác thực đã hết hạn")

    incoming_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if incoming_hash != code_hash:
        raise HTTPException(status_code=400, detail="Mã xác thực không đúng")

    existing_user = db.users.find_one({"email": email})
    base_updates = {
        "name": pending.get("name", ""),
        "phone": pending.get("phone", ""),
        "contact_email": email,
        "email_verified": True,
        "password_hash": pending.get("password_hash"),
    }

    if existing_user:
        db.users.update_one({"_id": existing_user["_id"]}, {"$set": base_updates})
        user_id = existing_user["_id"]
    else:
        created = {
            "email": email,
            "name": pending.get("name", ""),
            "phone": pending.get("phone", ""),
            "contact_email": email,
            "email_verified": True,
            "password_hash": pending.get("password_hash"),
            "role": "user",
            "company_id": None,
        }
        user_id = db.users.insert_one(created).inserted_id

    db.pending_registrations.delete_one({"email": email})

    return {
        "message": "Đăng ký tài khoản thành công",
        "user_id": str(user_id),
        "email": email,
    }
