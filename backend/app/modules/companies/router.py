from datetime import datetime, timedelta, timezone
import hashlib
import os
import secrets
import shutil
from urllib.parse import urlencode
import requests
from bson import ObjectId
from pymongo.errors import ConfigurationError, InvalidOperation, OperationFailure

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.core.drive_log import log_drive_error, log_drive_info
from app.db.mongo import client as mongo_client, get_db
from app.core.config import settings
from app.modules.users.router import get_current_user, reset_user_to_standalone_account
from app.modules.companies.schemas import CreateCompanyRequest
from app.services.ai.conversation_memory import clear_context
from app.services.ai.vector_service import delete_company_vectors
from app.services.company_state import build_company_access_state, require_company_with_access
from app.services.email_service import (
    send_company_action_email,
    send_verification_code_email,
)

router = APIRouter(prefix="/companies", tags=["Companies"])

client_id = settings.GOOGLE_CLIENT_ID
client_secret = settings.GOOGLE_CLIENT_SECRET


class DriveExchangePayload(BaseModel):
    code: str
    redirect_uri: str


class CompanyDeleteConfirmPayload(BaseModel):
    code: str


ALLOWED_DRIVE_REDIRECT_URIS = {
    "http://localhost:5173/drive-callback",
    "http://127.0.0.1:5173/drive-callback",
    "https://app.trolyaodoanhnghiep.io.vn/drive-callback",
}

COMPANY_DELETE_UPLOAD_FOLDER = "uploads"

PLAN_NAME_BY_ID = {
    "0": "G\u00f3i 0",
    "1m": "G\u00f3i 1 th\u00e1ng",
    "6m": "G\u00f3i 6 th\u00e1ng",
    "12m": "G\u00f3i 12 th\u00e1ng",
    "trial_7d": "D\u00f9ng th\u1eed 7 ng\u00e0y",
}

PLAN_NAME_ALIASES = {
    "goi 0": PLAN_NAME_BY_ID["0"],
    "g\u00f3i 0": PLAN_NAME_BY_ID["0"],
    "goi 1 thang": PLAN_NAME_BY_ID["1m"],
    "g\u00f3i 1 th\u00e1ng": PLAN_NAME_BY_ID["1m"],
    "goi 6 thang": PLAN_NAME_BY_ID["6m"],
    "g\u00f3i 6 th\u00e1ng": PLAN_NAME_BY_ID["6m"],
    "goi 12 thang": PLAN_NAME_BY_ID["12m"],
    "g\u00f3i 12 th\u00e1ng": PLAN_NAME_BY_ID["12m"],
    "dung thu 7 ngay": PLAN_NAME_BY_ID["trial_7d"],
    "d\u00f9ng th\u1eed 7 ng\u00e0y": PLAN_NAME_BY_ID["trial_7d"],
}

TRIAL_DURATION_DAYS = 7
TRIAL_STORAGE_LIMIT_BYTES = 1024 * 1024 * 1024
TRIAL_ACCOUNT_LIMIT = 5
TRIAL_ELIGIBILITY_COLLECTION = "trial_eligibility"


def normalize_subscription_plan_name(plan_id: str | None, plan_name: str | None) -> str:
    normalized_id = str(plan_id or "").strip()
    normalized_name = (plan_name or "").strip()
    if normalized_name:
        canonical = PLAN_NAME_ALIASES.get(normalized_name.lower())
        if canonical:
            return canonical
    return PLAN_NAME_BY_ID.get(normalized_id, normalized_name or PLAN_NAME_BY_ID["0"])


def _normalize_super_admin_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _company_has_paid_history(company: dict) -> bool:
    if str(company.get("subscription_plan_id") or "") in {"1m", "6m", "12m"}:
        return True
    if int(company.get("subscription_price") or 0) > 0:
        return True

    for period in company.get("subscription_periods") or []:
        plan_id = str(period.get("plan_id") or "")
        if plan_id in {"1m", "6m", "12m"}:
            return True
        if int(period.get("price") or 0) > 0:
            return True
    return False


def _company_has_trial_history(company: dict) -> bool:
    if bool(company.get("is_trial")):
        return True
    if str(company.get("subscription_plan_id") or "") == "trial_7d":
        return True
    if company.get("trial_started_at") or company.get("trial_ends_at"):
        return True

    for period in company.get("subscription_periods") or []:
        if str(period.get("plan_id") or "") == "trial_7d":
            return True
    return False


def get_super_admin_trial_eligibility(db, email: str) -> dict:
    normalized_email = _normalize_super_admin_email(email)
    if not normalized_email:
        return {"email": "", "trial_used": False, "paid_used": False}

    record = db[TRIAL_ELIGIBILITY_COLLECTION].find_one({"email": normalized_email})
    if record:
        return record

    related_companies = list(
        db.companies.find(
            {
                "$or": [
                    {"created_by": normalized_email},
                    {"contact_email": normalized_email},
                ]
            }
        )
    )
    had_trial = any(_company_has_trial_history(company) for company in related_companies)
    had_paid = any(_company_has_paid_history(company) for company in related_companies)

    if had_trial or had_paid:
        seeded = {
            "email": normalized_email,
            "trial_used": had_trial,
            "paid_used": had_paid,
            "seeded_from_history_at": datetime.utcnow(),
            "last_company_id": str(related_companies[-1].get("_id")) if related_companies else None,
        }
        db[TRIAL_ELIGIBILITY_COLLECTION].update_one(
            {"email": normalized_email},
            {"$set": seeded},
            upsert=True,
        )
        return seeded

    return {"email": normalized_email, "trial_used": False, "paid_used": False}


def mark_super_admin_trial_eligibility(
    db,
    email: str,
    *,
    trial_used: bool = False,
    paid_used: bool = False,
    company_id: str | None = None,
):
    normalized_email = _normalize_super_admin_email(email)
    if not normalized_email:
        return

    update_fields = {
        "email": normalized_email,
        "last_company_id": company_id,
        "last_seen_at": datetime.utcnow(),
    }
    if trial_used:
        update_fields["trial_used"] = True
        update_fields["trial_used_at"] = datetime.utcnow()
    if paid_used:
        update_fields["paid_used"] = True
        update_fields["paid_used_at"] = datetime.utcnow()

    db[TRIAL_ELIGIBILITY_COLLECTION].update_one(
        {"email": normalized_email},
        {"$set": update_fields, "$setOnInsert": {"created_at": datetime.utcnow()}},
        upsert=True,
    )


def _coerce_datetime(value):
    return value if isinstance(value, datetime) else None


def get_company_storage_limit_bytes(company: dict | None) -> int | None:
    if not company:
        return None
    raw_limit = company.get("storage_limit_bytes")
    if raw_limit in (None, "", 0):
        return None
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_company_account_limit(company: dict | None) -> int | None:
    if not company:
        return None
    raw_limit = company.get("account_limit")
    if raw_limit in (None, "", 0):
        return None
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_company_storage_usage_bytes(db, company_id: str) -> int:
    rows = list(
        db["drive_files"].aggregate(
            [
                {"$match": {"company_id": company_id}},
                {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$storage_bytes", 0]}}}},
            ]
        )
    )
    if not rows:
        return 0
    return int(rows[0].get("total") or 0)


def get_company_member_count(db, company_object_id: ObjectId) -> int:
    return db.users.count_documents({"company_id": company_object_id})


def enforce_company_storage_limit(
    db,
    company_id: str,
    company: dict,
    incoming_size_bytes: int,
    replacing_size_bytes: int = 0,
):
    storage_limit = get_company_storage_limit_bytes(company)
    if not storage_limit:
        return

    current_usage = get_company_storage_usage_bytes(db, company_id)
    projected_usage = current_usage - max(0, int(replacing_size_bytes or 0)) + max(0, int(incoming_size_bytes or 0))
    if projected_usage > storage_limit:
        raise HTTPException(
            status_code=400,
            detail="Doanh nghiệp đã dùng hết giới hạn 1GB lưu trữ tài liệu của gói hiện tại",
        )


def enforce_company_account_limit(db, company_id: str, company: dict):
    account_limit = get_company_account_limit(company)
    if not account_limit:
        return

    member_count = get_company_member_count(db, ObjectId(company_id))
    if member_count >= account_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Doanh nghiệp đã đạt giới hạn {account_limit} tài khoản",
        )


def build_subscription_periods(company: dict, fallback_now: datetime | None = None) -> list[dict]:
    now = fallback_now or datetime.utcnow()
    periods = []

    for raw_period in company.get("subscription_periods") or []:
        start_at = _coerce_datetime(raw_period.get("start_at"))
        end_at = _coerce_datetime(raw_period.get("end_at"))
        if not end_at:
            continue
        if start_at and end_at <= start_at:
            continue
        periods.append(
            {
                "plan_id": str(raw_period.get("plan_id") or "0"),
                "plan_name": normalize_subscription_plan_name(
                    raw_period.get("plan_id"),
                    raw_period.get("plan_name"),
                ),
                "duration_months": int(raw_period.get("duration_months") or 0),
                "bonus_months": int(raw_period.get("bonus_months") or 0),
                "total_months": int(raw_period.get("total_months") or 0),
                "price": int(raw_period.get("price") or 0),
                "start_at": start_at,
                "end_at": end_at,
            }
        )

    if periods:
        periods.sort(key=lambda item: item.get("end_at") or now)
        return periods

    legacy_plan_id = str(company.get("subscription_plan_id") or "0")
    legacy_expires_at = _coerce_datetime(company.get("expires_at"))
    if legacy_plan_id == "0" or not legacy_expires_at:
        return []

    legacy_start_at = (
        _coerce_datetime(company.get("registered_at"))
        or _coerce_datetime(company.get("created_at"))
        or now
    )
    if legacy_expires_at <= legacy_start_at:
        legacy_start_at = now

    return [
        {
            "plan_id": legacy_plan_id,
            "plan_name": normalize_subscription_plan_name(
                legacy_plan_id,
                company.get("subscription_plan_name"),
            ),
            "duration_months": int(company.get("subscription_duration_months") or 0),
            "bonus_months": int(company.get("subscription_bonus_months") or 0),
            "total_months": int(company.get("subscription_total_months") or 0),
            "price": int(company.get("subscription_price") or 0),
            "start_at": legacy_start_at,
            "end_at": legacy_expires_at,
        }
    ]


def get_active_subscription_period(company: dict, fallback_now: datetime | None = None) -> dict | None:
    now = fallback_now or datetime.utcnow()
    periods = build_subscription_periods(company, fallback_now=now)
    for period in periods:
        start_at = period.get("start_at")
        end_at = period.get("end_at")
        if end_at and end_at > now and (not start_at or start_at <= now):
            return period
    return None


def validate_drive_redirect_uri(redirect_uri: str) -> str:
    normalized = (redirect_uri or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Thiếu redirect_uri")
    if normalized not in ALLOWED_DRIVE_REDIRECT_URIS:
        raise HTTPException(status_code=400, detail="redirect_uri không được phép")
    return normalized

def _require_super_admin_company(user: dict):
    if user.get("role") not in {"admin", "super_admin"}:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    db = get_db()
    company_id, _company = require_company_with_access(db, user)

    try:
        return ObjectId(company_id), str(company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id không hợp lệ") from exc


def _require_super_admin_company_any_state(user: dict):
    if user.get("role") not in {"admin", "super_admin"}:
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    raw_company_id = str(user.get("company_id") or "").strip()
    if not raw_company_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    try:
        company_object_id = ObjectId(raw_company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id không hợp lệ") from exc

    db = get_db()
    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    return company_object_id, company


def _get_company_delete_delivery_email(db, user: dict) -> str:
    user_doc = db.users.find_one({"_id": ObjectId(user["_id"])})
    if not user_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")

    delivery_email = (user_doc.get("contact_email") or user_doc.get("email") or "").strip().lower()
    if not delivery_email:
        raise HTTPException(status_code=400, detail="Tài khoản chưa có email nhận mã")

    return delivery_email


def _cleanup_company_uploads(company_id: str):
    company_upload_dir = os.path.join(COMPANY_DELETE_UPLOAD_FOLDER, company_id)
    if os.path.isdir(company_upload_dir):
        shutil.rmtree(company_upload_dir, ignore_errors=True)


def _normalize_company_references_for_delete(db, company_object_id: ObjectId, company_id: str, session=None):
    collections = (
        db.messages,
        db.chats,
        db.documents,
        db["drive_files"],
    )
    for collection in collections:
        collection.update_many(
            {"company_id": company_id},
            {"$set": {"company_id": company_object_id}},
            session=session,
        )


def _purge_company_related_data(db, company_object_id: ObjectId, session=None):
    company_id = str(company_object_id)
    print(f"[company.delete] purge_start company_id={company_id}")
    _normalize_company_references_for_delete(db, company_object_id, company_id, session=session)

    company_users = list(
        db.users.find(
            {"company_id": company_object_id},
            {"_id": 1},
            session=session,
        )
    )
    user_ids = [str(item["_id"]) for item in company_users]
    for account in company_users:
        reset_user_to_standalone_account(db, str(account["_id"]), session=session)

    db.messages.delete_many({"company_id": company_object_id}, session=session)
    db.chats.delete_many({"company_id": company_object_id}, session=session)
    db.documents.delete_many({"company_id": company_object_id}, session=session)
    db["drive_files"].delete_many({"company_id": company_object_id}, session=session)
    db.companies.delete_one({"_id": company_object_id}, session=session)
    print(f"[company.delete] purge_done company_id={company_id}")
    return user_ids


def _supports_transactions(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "replica set" in message
        or "mongos" in message
        or "transaction numbers are only allowed" in message
        or "does not support transactions" in message
    )


def _delete_company_with_transaction(db, company_object_id: ObjectId) -> list[str]:
    try:
        with mongo_client.start_session() as session:
            with session.start_transaction():
                return _purge_company_related_data(db, company_object_id, session=session)
    except (ConfigurationError, InvalidOperation, OperationFailure) as exc:
        if not _supports_transactions(exc):
            raise
        print(f"[company.delete] transaction_unavailable company_id={company_object_id} error={exc}")
        return _purge_company_related_data(db, company_object_id)


def perform_company_delete(db, company_object_id: ObjectId, company_id: str | None = None) -> dict:
    normalized_company_id = company_id or str(company_object_id)
    try:
        user_ids = _delete_company_with_transaction(db, company_object_id)
    except Exception as exc:
        print(f"[company.delete] confirm_failed company_id={normalized_company_id} error={exc}")
        raise HTTPException(status_code=500, detail=f"Không thể xóa doanh nghiệp: {str(exc)}") from exc

    try:
        delete_company_vectors(normalized_company_id)
    except Exception as exc:
        print(f"[company.delete] vector_cleanup_failed company_id={normalized_company_id} error={exc}")

    try:
        _cleanup_company_uploads(normalized_company_id)
    except Exception as exc:
        print(f"[company.delete] upload_cleanup_failed company_id={normalized_company_id} error={exc}")

    for user_id in user_ids:
        try:
            clear_context(user_id)
        except Exception as exc:
            print(f"[company.delete] context_cleanup_failed company_id={normalized_company_id} user_id={user_id} error={exc}")

    print(f"[company.delete] confirm_success company_id={normalized_company_id}")
    return {
        "message": "Đã xóa doanh nghiệp và toàn bộ dữ liệu liên quan",
        "company_deleted": True,
    }


@router.post("")
def create_company(payload: CreateCompanyRequest, user=Depends(get_current_user)):
    """Create a company and attach current user as admin."""
    db = get_db()

    if user.get("company_id"):
        raise HTTPException(status_code=400, detail="Bạn đã thuộc một doanh nghiệp")

    creator = db.users.find_one({"email": user.get("email")})
    if not creator:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản người dùng")

    now = datetime.utcnow()
    creation_mode = (payload.creation_mode or "paid").strip().lower()
    is_trial = creation_mode == "trial"
    super_admin_email = _normalize_super_admin_email(creator.get("contact_email") or creator.get("email") or user.get("email"))
    if is_trial:
        eligibility = get_super_admin_trial_eligibility(db, super_admin_email)
        if eligibility.get("trial_used") or eligibility.get("paid_used"):
            raise HTTPException(
                status_code=400,
                detail="Tài khoản này không còn đủ điều kiện dùng thử 7 ngày",
            )
    expires_at = now + timedelta(days=TRIAL_DURATION_DAYS) if is_trial else None
    company_doc = {
        "name": payload.name.strip(),
        "owner_name": payload.owner_name.strip(),
        "shared_drive_id": payload.shared_drive_id,
        "created_by": user.get("email"),
        "created_by_user_id": str(creator["_id"]),
        "contact_email": (creator.get("contact_email") or creator.get("email") or "").strip().lower(),
        "approval_status": "approved",
        "is_blocked": False,
        "is_expired": False if is_trial else True,
        "created_at": now,
        "registered_at": now,
        "expires_at": expires_at,
        "subscription_plan_id": "trial_7d" if is_trial else "0",
        "subscription_plan_name": PLAN_NAME_BY_ID["trial_7d"] if is_trial else PLAN_NAME_BY_ID["0"],
        "subscription_duration_months": 0,
        "subscription_bonus_months": 0,
        "subscription_total_months": 0,
        "subscription_price": 0,
        "subscription_periods": (
            [
                {
                    "plan_id": "trial_7d",
                    "plan_name": PLAN_NAME_BY_ID["trial_7d"],
                    "duration_months": 0,
                    "bonus_months": 0,
                    "total_months": 0,
                    "price": 0,
                    "start_at": now,
                    "end_at": expires_at,
                }
            ]
            if is_trial
            else []
        ),
        "is_trial": is_trial,
        "trial_started_at": now if is_trial else None,
        "trial_ends_at": expires_at,
        "storage_limit_bytes": TRIAL_STORAGE_LIMIT_BYTES if is_trial else None,
        "account_limit": TRIAL_ACCOUNT_LIMIT if is_trial else None,
        "auto_delete_after_expiry": bool(is_trial),
    }

    if not company_doc["name"] or not company_doc["owner_name"]:
        raise HTTPException(status_code=400, detail="Thiếu thông tin doanh nghiệp")

    insert_res = db.companies.insert_one(company_doc)
    company_id = insert_res.inserted_id

    db.users.update_one(
        {"_id": creator["_id"]},
        {"$set": {"company_id": company_id, "role": "admin"}}
    )

    if is_trial:
        mark_super_admin_trial_eligibility(
            db,
            super_admin_email,
            trial_used=True,
            company_id=str(company_id),
        )

    return {
        "message": (
            "Doanh nghiệp dùng thử đã được kích hoạt trong 7 ngày"
            if is_trial
            else "Doanh nghiệp đã được tạo thành công. Vui lòng thanh toán để kích hoạt sử dụng"
        ),
        "company": {
            "_id": str(company_id),
            "name": company_doc["name"],
            "owner_name": company_doc["owner_name"],
            "shared_drive_id": company_doc["shared_drive_id"],
            "approval_status": company_doc["approval_status"],
            "is_trial": is_trial,
            "is_expired": bool(company_doc["is_expired"]),
        }
    }


@router.post("/activate-trial")
def activate_company_trial(user=Depends(get_current_user)):
    db = get_db()
    company_object_id, company = _require_super_admin_company_any_state(user)

    if company.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Doanh nghiệp đang bị khóa")
    if bool(company.get("is_trial")) and not bool(company.get("is_expired")):
        raise HTTPException(status_code=400, detail="Doanh nghiệp đang ở gói dùng thử")

    super_admin_email = _normalize_super_admin_email(
        company.get("contact_email") or user.get("contact_email") or user.get("email")
    )
    eligibility = get_super_admin_trial_eligibility(db, super_admin_email)
    if eligibility.get("trial_used") or eligibility.get("paid_used"):
        raise HTTPException(
            status_code=400,
            detail="Tài khoản này không còn đủ điều kiện dùng thử 7 ngày",
        )

    now = datetime.utcnow()
    expires_at = now + timedelta(days=TRIAL_DURATION_DAYS)
    trial_period = {
        "plan_id": "trial_7d",
        "plan_name": PLAN_NAME_BY_ID["trial_7d"],
        "duration_months": 0,
        "bonus_months": 0,
        "total_months": 0,
        "price": 0,
        "start_at": now,
        "end_at": expires_at,
    }

    db.companies.update_one(
        {"_id": company_object_id},
        {
            "$set": {
                "is_expired": False,
                "expires_at": expires_at,
                "subscription_plan_id": "trial_7d",
                "subscription_plan_name": PLAN_NAME_BY_ID["trial_7d"],
                "subscription_duration_months": 0,
                "subscription_bonus_months": 0,
                "subscription_total_months": 0,
                "subscription_price": 0,
                "subscription_periods": [trial_period],
                "is_trial": True,
                "trial_started_at": now,
                "trial_ends_at": expires_at,
                "storage_limit_bytes": TRIAL_STORAGE_LIMIT_BYTES,
                "account_limit": TRIAL_ACCOUNT_LIMIT,
                "auto_delete_after_expiry": True,
            }
        },
    )

    mark_super_admin_trial_eligibility(
        db,
        super_admin_email,
        trial_used=True,
        company_id=str(company_object_id),
    )

    return {
        "message": "Doanh nghiệp dùng thử đã được kích hoạt trong 7 ngày",
        "company_id": str(company_object_id),
        "expires_at": expires_at,
    }


def maybe_refresh_drive_token(company):
    """Return a valid access token for the company, refreshing if needed."""
    if not company:
        return None
    if build_company_access_state(company) != "active":
        return None

    access_token = company.get("drive_token")
    refresh_token = company.get("drive_refresh_token")
    expires = company.get("drive_token_expires")
    now = datetime.utcnow()

    # Nếu chưa có expires → coi như đã hết hạn (trả token cũ để FE yêu cầu kết nối lại)
    if not expires:
        return access_token

    # Token còn hạn (> 5 phút) → dùng luôn
    if expires > now + timedelta(minutes=5):
        return access_token

    # Không có refresh_token → không refresh được
    if not refresh_token:
        return access_token

    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )

        data = resp.json()
        if resp.status_code == 200 and data.get("access_token"):
            new_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)

            db = get_db()
            db.companies.update_one(
                {"_id": company["_id"]},
                {"$set": {
                    "drive_token": new_token,
                    "drive_token_expires": now + timedelta(seconds=int(expires_in))
                }}
            )

            return new_token

        print("[drive.refresh] failed:", data)
        log_drive_error(f"[drive.refresh] failed company_id={company.get('_id')} data={data}")
    except Exception as e:
        print("[drive.refresh] exception:", e)
        log_drive_error(f"[drive.refresh] exception company_id={company.get('_id')} error={e}")

    return access_token


@router.get("/drive/connect")
def drive_connect(redirect_uri: str, user=Depends(get_current_user)):
    """Trả về OAuth URL để frontend redirect user sang Google xin quyền Drive"""
    print(f"[drive.connect] called role={user.get('role')}")
    log_drive_info(f"[drive.connect] called role={user.get('role')} user={user.get('email')}")
    
    if user.get("role") not in {"admin", "super_admin"}:
        print(f"[drive.connect] forbidden role={user.get('role')}")
        log_drive_error(f"[drive.connect] forbidden role={user.get('role')} user={user.get('email')}")
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    redirect_uri = validate_drive_redirect_uri(redirect_uri)

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive",
        "access_type": "offline",
        "prompt": "consent",
    }

    oauth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    
    print(f"[drive.connect] oauth_url={oauth_url}")
    log_drive_info(f"[drive.connect] oauth_url_ready redirect_uri={redirect_uri}")

    return {"url": oauth_url}


@router.post("/drive-exchange")
def exchange_drive_code(payload: DriveExchangePayload, user=Depends(get_current_user)):
    """Đổi authorization code → access_token + refresh_token"""
    print(f"[drive.exchange] called user={user.get('email')}")
    log_drive_info(f"[drive.exchange] called user={user.get('email')}")
    
    if user.get("role") not in {"admin", "super_admin"}:
        print(f"[drive.exchange] forbidden")
        log_drive_error(f"[drive.exchange] forbidden user={user.get('email')} role={user.get('role')}")
        raise HTTPException(status_code=403, detail="Chỉ admin được phép")

    code = payload.code
    print(f"[drive.exchange] code_present={bool(code)}")
    log_drive_info(f"[drive.exchange] code_present={bool(code)} user={user.get('email')}")
    
    if not code:
        raise HTTPException(status_code=400, detail="Thiếu code")

    db = get_db()

    # Lấy company_id từ user
    try:
        comp_id = ObjectId(user["company_id"])
        print(f"[drive.exchange] company_id={comp_id}")
    except Exception:
        print(f"[drive.exchange] invalid_company_id")
        log_drive_error(f"[drive.exchange] invalid_company_id user={user.get('email')}")
        raise HTTPException(status_code=400, detail="company_id không hợp lệ")

    redirect_uri = validate_drive_redirect_uri(payload.redirect_uri)

    print(f"[drive.exchange] exchanging_with_google")
    log_drive_info(f"[drive.exchange] exchanging_with_google company_id={comp_id}")
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )

    print(f"[drive.exchange] google_status={resp.status_code}")
    print(f"[drive.exchange] google_data={resp.json()}")
    log_drive_info(f"[drive.exchange] google_status={resp.status_code} company_id={comp_id}")

    if resp.status_code != 200:
        print(f"[drive.exchange] google_error={resp.json()}")
        log_drive_error(f"[drive.exchange] google_error company_id={comp_id} data={resp.json()}")
        raise HTTPException(status_code=400, detail=resp.json())

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 3600)

    print(f"[drive.exchange] access_token_present={bool(access_token)}")
    print(f"[drive.exchange] refresh_token_present={bool(refresh_token)}")
    log_drive_info(
        f"[drive.exchange] tokens_received company_id={comp_id} "
        f"access_token_present={bool(access_token)} refresh_token_present={bool(refresh_token)}"
    )

    if not access_token:
        print(f"[drive.exchange] missing_access_token")
        log_drive_error(f"[drive.exchange] missing_access_token company_id={comp_id}")
        raise HTTPException(status_code=400, detail="Google không trả access_token")

    update = {
        "drive_token": access_token,
        "drive_refresh_token": refresh_token,
        "drive_token_expires": datetime.utcnow() + timedelta(seconds=int(expires_in)),
    }

    db.companies.update_one({"_id": comp_id}, {"$set": update})
    print(f"[drive.exchange] tokens_saved")
    log_drive_info(f"[drive.exchange] tokens_saved company_id={comp_id}")

    company = db.companies.find_one({"_id": comp_id})
    if company:
        try:
            from app.services.sync_service import sync_drive

            sync_drive(company)
            print(f"[drive.exchange] initial_sync_done company_id={comp_id}")
            log_drive_info(f"[drive.exchange] initial_sync_done company_id={comp_id}")
        except Exception as exc:
            print(f"[drive.exchange] initial_sync_failed company_id={comp_id} error={exc}")
            log_drive_error(f"[drive.exchange] initial_sync_failed company_id={comp_id} error={exc}")

    return {
        "ok": True,
        "message": "✅ Đã kết nối Google Drive thành công",
        "has_refresh_token": bool(refresh_token),
    }


@router.post("/delete/send-code")
def send_delete_company_code(user=Depends(get_current_user)):
    db = get_db()
    company_object_id, _ = _require_super_admin_company(user)
    delivery_email = _get_company_delete_delivery_email(db, user)
    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    verification = company.get("delete_verification") or {}
    now = datetime.now(timezone.utc)
    last_sent_at = verification.get("last_sent_at")
    if isinstance(last_sent_at, datetime):
        if last_sent_at.tzinfo is None:
            last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
        if (now - last_sent_at).total_seconds() < 60:
            raise HTTPException(status_code=429, detail="Vui lòng chờ 60 giây trước khi gửi lại mã")

    code = str(secrets.randbelow(900000) + 100000)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires_at = now + timedelta(minutes=settings.EMAIL_CODE_EXPIRE_MINUTES)

    db.companies.update_one(
        {"_id": company_object_id},
        {
            "$set": {
                "delete_verification": {
                    "code_hash": code_hash,
                    "expires_at": expires_at,
                    "last_sent_at": now,
                    "delivery_email": delivery_email,
                    "requested_by_user_id": user["_id"],
                }
            }
        },
    )

    try:
        send_verification_code_email(delivery_email, code, purpose="delete_company")
    except Exception as exc:
        if settings.EMAIL_DEBUG_RETURN_CODE:
            return {
                "message": "Không gửi được email qua SMTP. Đã tạo mã xác thực cho môi trường local.",
                "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
                "delivery_email": delivery_email,
            }
        raise HTTPException(status_code=500, detail=f"Gửi email thất bại: {str(exc)}")

    return {
        "message": "Đã gửi mã xác thực xóa doanh nghiệp",
        "expires_in_seconds": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
        "delivery_email": delivery_email,
    }


@router.post("/delete/confirm")
def confirm_delete_company(payload: CompanyDeleteConfirmPayload, user=Depends(get_current_user)):
    db = get_db()
    company_object_id, company_id = _require_super_admin_company(user)
    print(f"[company.delete] confirm_start company_id={company_id} user_id={user.get('_id')}")
    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    verification = company.get("delete_verification") or {}
    code_hash = verification.get("code_hash")
    expires_at = verification.get("expires_at")
    incoming_code = (payload.code or "").strip()

    if not incoming_code or not code_hash or not expires_at:
        raise HTTPException(status_code=400, detail="Mã xác thực xóa doanh nghiệp không hợp lệ")

    now = datetime.now(timezone.utc)
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="Mã xác thực đã hết hạn")

    incoming_hash = hashlib.sha256(incoming_code.encode("utf-8")).hexdigest()
    if incoming_hash != code_hash:
        raise HTTPException(status_code=400, detail="Mã xác thực không đúng")

    return perform_company_delete(db, company_object_id, company_id)


@router.get("/me")
def get_company_info(user=Depends(get_current_user)):
    """Trả về thông tin cơ bản của company cho frontend (status kết nối Drive)."""
    db = get_db()
    comp_id = user.get("company_id")
    if not comp_id:
        # Frontend xử lý case này (user chưa thuộc doanh nghiệp)
        raise HTTPException(status_code=404, detail="User chưa thuộc doanh nghiệp")

    try:
        comp = db.companies.find_one({"_id": ObjectId(comp_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    if not comp:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    access_state = build_company_access_state(comp)
    has_drive = bool(comp.get("drive_token") or comp.get("drive_refresh_token"))
    has_refresh = bool(comp.get("drive_refresh_token"))
    expires = comp.get("drive_token_expires")
    company_id = str(comp.get("_id"))
    total_documents = db["drive_files"].count_documents({"company_id": company_id})
    indexed_documents = db["drive_files"].count_documents({"company_id": company_id, "indexed": True})

    if has_drive and access_state == "active":
        try:
            from app.modules.documents.drive_service import list_drive_files

            total_documents = len(list_drive_files(comp))
        except Exception as exc:
            print(f"[companies.me] live_drive_count_failed company_id={company_id} error={exc}")

    # Ensure the expiry is returned as an explicit UTC timestamp (ends with Z)
    expires_iso = None
    if expires:
        try:
            expires_iso = expires.isoformat() + "Z"
        except Exception:
            expires_iso = expires.strftime("%Y-%m-%dT%H:%M:%SZ")

    active_period = get_active_subscription_period(comp)
    active_plan_id = (active_period or {}).get("plan_id") or comp.get("subscription_plan_id") or "0"
    active_plan_name = normalize_subscription_plan_name(
        active_plan_id,
        (active_period or {}).get("plan_name") or comp.get("subscription_plan_name"),
    )
    active_duration_months = int((active_period or {}).get("duration_months") or comp.get("subscription_duration_months") or 0)
    active_bonus_months = int((active_period or {}).get("bonus_months") or comp.get("subscription_bonus_months") or 0)
    active_total_months = int((active_period or {}).get("total_months") or comp.get("subscription_total_months") or 0)
    active_price = int((active_period or {}).get("price") or comp.get("subscription_price") or 0)

    return {
        "_id": str(comp.get("_id")),
        "name": comp.get("name"),
        "owner_name": comp.get("owner_name"),
        "contact_email": comp.get("contact_email"),
        "approval_status": comp.get("approval_status", "approved"),
        "is_blocked": bool(comp.get("is_blocked")),
        "is_expired": bool(comp.get("is_expired")),
        "access_state": access_state,
        "has_drive_connected": has_drive,
        "has_refresh_token": has_refresh,
        "drive_token_expires": expires_iso,
        "total_documents": total_documents,
        "indexed_documents": indexed_documents,
        "registered_at": comp.get("registered_at") or comp.get("created_at"),
        "expires_at": comp.get("expires_at"),
        "current_plan_id": active_plan_id,
        "current_plan_name": active_plan_name,
        "current_plan_duration_months": active_duration_months,
        "current_plan_bonus_months": active_bonus_months,
        "current_plan_total_months": active_total_months,
        "current_plan_price": active_price,
        "storage_limit_bytes": get_company_storage_limit_bytes(comp),
        "storage_used_bytes": get_company_storage_usage_bytes(db, company_id),
        "account_limit": get_company_account_limit(comp),
        "member_count": get_company_member_count(db, ObjectId(company_id)),
        "is_trial": bool(comp.get("is_trial")),
        "trial_ends_at": comp.get("trial_ends_at"),
    }
