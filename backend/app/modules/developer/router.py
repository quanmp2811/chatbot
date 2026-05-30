from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.mongo import get_db
from app.modules.companies.router import (
    build_subscription_periods,
    get_active_subscription_period,
    mark_super_admin_trial_eligibility,
    normalize_subscription_plan_name,
    perform_company_delete,
)
from app.modules.users.router import get_current_user
from app.services.email_service import (
    send_company_action_email,
    send_company_blocked_email,
    send_company_renewed_success_email,
    send_company_unblocked_email,
)

router = APIRouter(prefix="/developer", tags=["Developer"])


DEFAULT_PLAN_CATALOG = [
    {
        "id": "0",
        "name": "Gói 0",
        "duration_months": 0,
        "bonus_months": 0,
        "price": 0,
    },
    {
        "id": "1m",
        "name": "Gói 1 tháng",
        "duration_months": 1,
        "bonus_months": 0,
        "price": 150000,
    },
    {
        "id": "6m",
        "name": "Gói 6 tháng",
        "duration_months": 6,
        "bonus_months": 1,
        "price": 900000,
    },
    {
        "id": "12m",
        "name": "Gói 12 tháng",
        "duration_months": 12,
        "bonus_months": 3,
        "price": 1800000,
    },
]


class RenewCompanyPayload(BaseModel):
    plan_id: str


class PlanPayload(BaseModel):
    id: str
    name: str
    duration_months: int
    bonus_months: int = 0
    price: int


class UpdatePlanPayload(BaseModel):
    name: str
    duration_months: int
    bonus_months: int = 0
    price: int


def _require_developer(user: dict):
    if user.get("role") not in {"developer", "super_admin"}:
        raise HTTPException(status_code=403, detail="Chỉ tài khoản nhà phát triển được phép")
    return user


def _normalize_plan_id(plan_id: str) -> str:
    return str(plan_id or "").strip().lower()


def _normalize_plan_record(plan: dict) -> dict:
    plan_id = _normalize_plan_id(plan.get("id"))
    duration_months = int(plan.get("duration_months") or 0)
    bonus_months = int(plan.get("bonus_months") or 0)
    price = int(plan.get("price") or 0)
    total_months = duration_months + bonus_months
    return {
        "id": plan_id,
        "name": normalize_subscription_plan_name(plan_id, plan.get("name") or ""),
        "duration_months": duration_months,
        "bonus_months": bonus_months,
        "total_months": total_months,
        "price": price,
    }


def _validate_plan_payload(plan_id: str, name: str, duration_months: int, bonus_months: int, price: int):
    if not plan_id:
        raise HTTPException(status_code=400, detail="Mã gói cước không được để trống")
    if " " in plan_id:
        raise HTTPException(status_code=400, detail="Mã gói cước không được chứa khoảng trắng")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Tên gói cước không được để trống")
    if duration_months < 0:
        raise HTTPException(status_code=400, detail="Số tháng chính không hợp lệ")
    if bonus_months < 0:
        raise HTTPException(status_code=400, detail="Số tháng tặng không hợp lệ")
    if price < 0:
        raise HTTPException(status_code=400, detail="Giá gói cước không hợp lệ")


def _plan_sort_key(plan: dict):
    if plan.get("id") == "0":
        return (0, 0, 0, plan.get("name") or "")
    return (
        1,
        int(plan.get("duration_months") or 0),
        int(plan.get("price") or 0),
        plan.get("name") or "",
    )


def _ensure_default_plans(db):
    for plan in DEFAULT_PLAN_CATALOG:
        normalized = _normalize_plan_record(plan)
        db.company_plans.update_one(
            {"id": normalized["id"]},
            {"$setOnInsert": normalized},
            upsert=True,
        )


def _load_plans(db, include_free: bool = True) -> list[dict]:
    _ensure_default_plans(db)
    plans = [_normalize_plan_record(item) for item in db.company_plans.find({}, {"_id": 0})]
    plans.sort(key=_plan_sort_key)
    if include_free:
        return plans
    return [item for item in plans if item.get("id") != "0"]


def _get_plan_by_id(db, plan_id: str) -> dict | None:
    normalized_plan_id = _normalize_plan_id(plan_id)
    if not normalized_plan_id:
        return None
    _ensure_default_plans(db)
    plan = db.company_plans.find_one({"id": normalized_plan_id}, {"_id": 0})
    if not plan:
        return None
    return _normalize_plan_record(plan)


def _add_months(base_date: datetime, months: int) -> datetime:
    if months <= 0:
        return base_date

    year = base_date.year + (base_date.month - 1 + months) // 12
    month = (base_date.month - 1 + months) % 12 + 1
    day = base_date.day

    while True:
        try:
            return base_date.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1


def _company_plan_snapshot(company: dict) -> dict:
    db = get_db()
    active_period = get_active_subscription_period(company)
    plan_id = (active_period or {}).get("plan_id") or str(company.get("subscription_plan_id") or "0")
    fallback_plan = _get_plan_by_id(db, "0") or _normalize_plan_record(DEFAULT_PLAN_CATALOG[0])
    plan = _get_plan_by_id(db, plan_id) or fallback_plan
    return {
        "current_plan_id": plan_id,
        "current_plan_name": normalize_subscription_plan_name(
            plan_id,
            (active_period or {}).get("plan_name") or company.get("subscription_plan_name") or plan["name"],
        ),
        "current_plan_duration_months": int(
            (active_period or {}).get("duration_months")
            or company.get("subscription_duration_months")
            or plan["duration_months"]
        ),
        "current_plan_bonus_months": int(
            (active_period or {}).get("bonus_months")
            or company.get("subscription_bonus_months")
            or plan["bonus_months"]
        ),
        "current_plan_total_months": int(
            (active_period or {}).get("total_months")
            or company.get("subscription_total_months")
            or plan["total_months"]
        ),
        "current_plan_price": int(
            (active_period or {}).get("price") or company.get("subscription_price") or plan["price"]
        ),
    }


def _serialize_company(company: dict) -> dict:
    registered_at = company.get("registered_at") or company.get("created_at")
    payload = {
        "_id": str(company["_id"]),
        "name": company.get("name"),
        "owner_name": company.get("owner_name"),
        "contact_email": company.get("contact_email") or company.get("created_by"),
        "approval_status": company.get("approval_status") or "approved",
        "is_blocked": bool(company.get("is_blocked")),
        "is_expired": bool(company.get("is_expired")),
        "created_at": company.get("created_at"),
        "registered_at": registered_at,
        "expires_at": company.get("expires_at"),
    }
    payload.update(_company_plan_snapshot(company))
    return payload


def _parse_company_id(company_id: str) -> ObjectId:
    try:
        return ObjectId(company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id không hợp lệ") from exc


def _notify_company(company: dict, action_label: str, message_body: str):
    to_email = (company.get("contact_email") or company.get("created_by") or "").strip().lower()
    if not to_email:
        return
    try:
        send_company_action_email(to_email, company.get("name") or "Doanh nghiệp", action_label, message_body)
    except Exception as exc:
        print(f"[developer.company] email_failed company_id={company.get('_id')} error={exc}")


def _send_company_block_status_email(company: dict, blocked: bool):
    to_email = (company.get("contact_email") or company.get("created_by") or "").strip().lower()
    if not to_email:
        return

    company_name = company.get("name") or "Doanh nghiệp"
    try:
        if blocked:
            send_company_blocked_email(to_email, company_name)
        else:
            send_company_unblocked_email(to_email, company_name)
    except Exception as exc:
        print(f"[developer.company] status_email_failed company_id={company.get('_id')} error={exc}")


def _send_company_renew_success_email(company: dict, plan_name: str, expires_at: datetime | None):
    to_email = (company.get("contact_email") or company.get("created_by") or "").strip().lower()
    if not to_email:
        return

    expires_at_text = None
    if expires_at:
        try:
            expires_at_text = expires_at.strftime("%d/%m/%Y %H:%M")
        except Exception:
            expires_at_text = str(expires_at)

    try:
        send_company_renewed_success_email(
            to_email,
            company.get("name") or "Doanh nghiệp",
            plan_name,
            expires_at_text,
        )
    except Exception as exc:
        print(f"[developer.company] renew_email_failed company_id={company.get('_id')} error={exc}")


@router.get("/companies/overview")
def get_company_overview(user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()

    managed = list(db.companies.find({}).sort("created_at", -1))

    return {
        "managed_companies": [_serialize_company(item) for item in managed],
        "plan_options": _load_plans(db, include_free=True),
    }


@router.get("/plans")
def get_company_plans(user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()
    return {"plans": _load_plans(db, include_free=True)}


@router.post("/plans")
def create_company_plan(payload: PlanPayload, user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()

    plan_id = _normalize_plan_id(payload.id)
    name = (payload.name or "").strip()
    duration_months = int(payload.duration_months or 0)
    bonus_months = int(payload.bonus_months or 0)
    price = int(payload.price or 0)
    _validate_plan_payload(plan_id, name, duration_months, bonus_months, price)

    if db.company_plans.find_one({"id": plan_id}):
        raise HTTPException(status_code=400, detail="Mã gói cước đã tồn tại")

    plan = _normalize_plan_record(
        {
            "id": plan_id,
            "name": name,
            "duration_months": duration_months,
            "bonus_months": bonus_months,
            "price": price,
        }
    )
    db.company_plans.insert_one(plan)
    return {"message": f"Đã thêm gói cước {plan['name']}", "plan": plan}


@router.put("/plans/{plan_id}")
def update_company_plan(plan_id: str, payload: UpdatePlanPayload, user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()

    normalized_plan_id = _normalize_plan_id(plan_id)
    if normalized_plan_id == "0":
        raise HTTPException(status_code=400, detail="Không thể sửa Gói 0 mặc định")

    existing = db.company_plans.find_one({"id": normalized_plan_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Không tìm thấy gói cước")

    name = (payload.name or "").strip()
    duration_months = int(payload.duration_months or 0)
    bonus_months = int(payload.bonus_months or 0)
    price = int(payload.price or 0)
    _validate_plan_payload(normalized_plan_id, name, duration_months, bonus_months, price)

    plan = _normalize_plan_record(
        {
            "id": normalized_plan_id,
            "name": name,
            "duration_months": duration_months,
            "bonus_months": bonus_months,
            "price": price,
        }
    )
    db.company_plans.update_one({"id": normalized_plan_id}, {"$set": plan})
    return {"message": f"Đã cập nhật gói cước {plan['name']}", "plan": plan}


@router.delete("/plans/{plan_id}")
def delete_company_plan(plan_id: str, user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()

    normalized_plan_id = _normalize_plan_id(plan_id)
    if normalized_plan_id == "0":
        raise HTTPException(status_code=400, detail="Không thể xóa Gói 0 mặc định")

    existing = db.company_plans.find_one({"id": normalized_plan_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Không tìm thấy gói cước")

    in_use = db.companies.find_one(
        {
            "$or": [
                {"subscription_plan_id": normalized_plan_id},
                {"subscription_periods.plan_id": normalized_plan_id},
            ]
        },
        {"_id": 1},
    )
    if in_use:
        raise HTTPException(status_code=400, detail="Gói cước đang được doanh nghiệp sử dụng, không thể xóa")

    db.company_plans.delete_one({"id": normalized_plan_id})
    plan_name = normalize_subscription_plan_name(normalized_plan_id, existing.get("name") or "")
    return {"message": f"Đã xóa gói cước {plan_name}"}


@router.post("/companies/{company_id}/toggle-block")
def toggle_company_block(company_id: str, user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()
    company = db.companies.find_one({"_id": _parse_company_id(company_id)})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    next_value = not bool(company.get("is_blocked"))
    db.companies.update_one({"_id": company["_id"]}, {"$set": {"is_blocked": next_value}})

    if next_value:
        _send_company_block_status_email(company, True)
        _notify_company(company, "Khóa doanh nghiệp", "đã bị khóa. Bạn tạm thời không thể sử dụng hệ thống.")
        return {"message": "Đã khóa doanh nghiệp"}

    _send_company_block_status_email(company, False)
    _notify_company(company, "Mở khóa doanh nghiệp", "đã được mở khóa và có thể sử dụng lại hệ thống.")
    return {"message": "Đã mở khóa doanh nghiệp"}


@router.post("/companies/{company_id}/toggle-expiry")
def expire_company(company_id: str, user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()
    company = db.companies.find_one({"_id": _parse_company_id(company_id)})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")
    if company.get("is_expired"):
        raise HTTPException(status_code=400, detail="Doanh nghiệp đã ở trạng thái hết hạn")

    free_plan = _get_plan_by_id(db, "0") or _normalize_plan_record(DEFAULT_PLAN_CATALOG[0])
    db.companies.update_one(
        {"_id": company["_id"]},
        {
            "$set": {
                "is_expired": True,
                "subscription_plan_id": "0",
                "subscription_plan_name": free_plan["name"],
                "subscription_duration_months": 0,
                "subscription_bonus_months": 0,
                "subscription_total_months": 0,
                "subscription_price": 0,
                "subscription_periods": [],
                "expires_at": None,
            }
        },
    )

    _notify_company(
        company,
        "Hết hạn doanh nghiệp",
        "đã hết hạn. Để tiếp tục sử dụng dịch vụ, vui lòng gia hạn hoặc liên hệ nhà phát triển.",
    )
    return {"message": "Đã chuyển doanh nghiệp sang trạng thái hết hạn và reset gói cước"}


@router.post("/companies/{company_id}/renew")
def renew_company(company_id: str, payload: RenewCompanyPayload, user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()
    company = db.companies.find_one({"_id": _parse_company_id(company_id)})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    plan = _get_plan_by_id(db, payload.plan_id)
    if not plan or plan["id"] == "0":
        raise HTTPException(status_code=400, detail="Gói cước không hợp lệ")

    now = datetime.utcnow()
    existing_periods = build_subscription_periods(company, fallback_now=now)
    last_period_end = existing_periods[-1]["end_at"] if existing_periods else None
    base_date = last_period_end if last_period_end and last_period_end > now else now
    next_expires_at = _add_months(base_date, int(plan["total_months"]))
    registered_at = company.get("registered_at") or company.get("created_at") or now
    normalized_plan_name = normalize_subscription_plan_name(plan["id"], plan["name"])

    next_period = {
        "plan_id": plan["id"],
        "plan_name": normalized_plan_name,
        "duration_months": int(plan["duration_months"]),
        "bonus_months": int(plan["bonus_months"]),
        "total_months": int(plan["total_months"]),
        "price": int(plan["price"]),
        "start_at": base_date,
        "end_at": next_expires_at,
    }
    updated_periods = [*existing_periods, next_period]
    active_period = get_active_subscription_period({"subscription_periods": updated_periods}, fallback_now=now)
    active_plan_id = (active_period or {}).get("plan_id") or company.get("subscription_plan_id") or "0"
    active_plan_name = normalize_subscription_plan_name(
        active_plan_id,
        (active_period or {}).get("plan_name") or company.get("subscription_plan_name"),
    )

    db.companies.update_one(
        {"_id": company["_id"]},
        {
            "$set": {
                "is_expired": False,
                "subscription_plan_id": active_plan_id,
                "subscription_plan_name": active_plan_name,
                "subscription_duration_months": int((active_period or {}).get("duration_months") or 0),
                "subscription_bonus_months": int((active_period or {}).get("bonus_months") or 0),
                "subscription_total_months": int((active_period or {}).get("total_months") or 0),
                "subscription_price": int((active_period or {}).get("price") or 0),
                "subscription_periods": updated_periods,
                "registered_at": registered_at,
                "expires_at": next_expires_at,
            }
        },
    )

    owner_email = (company.get("contact_email") or company.get("created_by") or "").strip().lower()
    mark_super_admin_trial_eligibility(
        db,
        owner_email,
        paid_used=True,
        company_id=str(company["_id"]),
    )

    _notify_company(
        company,
        "Gia hạn doanh nghiệp",
        f"đã được gia hạn thành công với {normalized_plan_name}. Hãy truy cập https://trolyaodoanhnghiep.io.vn để tiếp tục sử dụng.",
    )
    _send_company_renew_success_email(company, normalized_plan_name, next_expires_at)
    return {
        "message": f"Đã gia hạn doanh nghiệp với {normalized_plan_name}",
        "plan": plan,
        "expires_at": next_expires_at,
    }


@router.delete("/companies/{company_id}")
def delete_company(company_id: str, user=Depends(get_current_user)):
    _require_developer(user)
    db = get_db()
    company = db.companies.find_one({"_id": _parse_company_id(company_id)})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    _notify_company(company, "Xóa doanh nghiệp", "đã bị xóa khỏi hệ thống.")
    return perform_company_delete(db, company["_id"], str(company["_id"]))
