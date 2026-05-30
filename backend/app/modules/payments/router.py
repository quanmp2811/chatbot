import base64
import hashlib
import hmac
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlencode

import requests
from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.db.mongo import get_db
from app.modules.companies.router import (
    build_subscription_periods,
    get_active_subscription_period,
    mark_super_admin_trial_eligibility,
    normalize_subscription_plan_name,
)
from app.modules.users.router import get_current_user

router = APIRouter(prefix="/payments", tags=["Payments"])


class CreatePaymentPayload(BaseModel):
    amount: int
    order_desc: str
    payment_type: str = "register"
    company_name: str = ""
    plan_id: str = "1m"


DEFAULT_PLAN_CATALOG = [
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


def _get_client_ip(request: Request, x_forwarded_for: Optional[str] = None) -> str:
    forwarded = (x_forwarded_for or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def _build_frontend_payment_url(base_url: str, payload: CreatePaymentPayload) -> str:
    if not base_url:
        login_url = (settings.FRONTEND_LOGIN_URL or "").strip()
        if login_url.endswith("/login"):
            base_url = login_url[: -len("/login")]
        else:
            base_url = login_url
        if base_url:
            base_url = f"{base_url}/company-payment"

    if not base_url:
        raise HTTPException(status_code=500, detail="Chưa cấu hình VNPAY_RETURN_URL")

    separator = "&" if "?" in base_url else "?"
    payment_type = quote(payload.payment_type or "register", safe="")
    company_name = quote(payload.company_name or "", safe="")
    return f"{base_url}{separator}type={payment_type}&company={company_name}"


def _build_vnpay_return_url(request: Request) -> str:
    configured = (settings.VNPAY_RETURN_URL or "").strip()
    if configured:
        return configured
    return str(request.url_for("vnpay_return"))


def _build_momo_return_url(payload: CreatePaymentPayload) -> str:
    return _build_frontend_payment_url((settings.MOMO_RETURN_URL or "").strip(), payload)


def _build_backend_public_url(request: Request, route_name: str) -> str:
    configured_ipn_url = (settings.MOMO_IPN_URL or "").strip()
    if route_name == "momo_ipn" and configured_ipn_url:
        return configured_ipn_url
    return str(request.url_for(route_name))


def _normalize_plan_record(plan: dict) -> dict:
    duration_months = int(plan.get("duration_months") or 0)
    bonus_months = int(plan.get("bonus_months") or 0)
    return {
        "id": str(plan.get("id") or "").strip().lower(),
        "name": normalize_subscription_plan_name(plan.get("id"), plan.get("name") or ""),
        "duration_months": duration_months,
        "bonus_months": bonus_months,
        "total_months": duration_months + bonus_months,
        "price": int(plan.get("price") or 0),
    }


def _get_plan_by_id(db, plan_id: str) -> dict | None:
    normalized_plan_id = str(plan_id or "").strip().lower()
    if not normalized_plan_id:
        return None

    plan = db.company_plans.find_one({"id": normalized_plan_id}, {"_id": 0})
    if plan:
        return _normalize_plan_record(plan)

    for default_plan in DEFAULT_PLAN_CATALOG:
        if default_plan["id"] == normalized_plan_id:
            return _normalize_plan_record(default_plan)
    return None


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


def _build_frontend_base_url() -> str:
    login_url = (settings.FRONTEND_LOGIN_URL or "").strip()
    if login_url.endswith("/login"):
        return login_url[: -len("/login")]
    return login_url


def _build_frontend_payment_status_url(provider: str, status: str, company_name: str) -> str:
    frontend_base_url = _build_frontend_base_url()
    if not frontend_base_url:
        raise HTTPException(status_code=500, detail="Chua cau hinh FRONTEND_LOGIN_URL")
    return (
        f"{frontend_base_url}/company-payment?{provider}_status={quote(status, safe='')}"
        f"&company={quote(company_name or '', safe='')}"
    )


def _encode_mock_token(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    signature = hmac.new(settings.JWT_SECRET_KEY.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return urlsafe_b64encode(raw).decode("utf-8").rstrip("=") + "." + signature


def _decode_mock_token(token: str) -> dict:
    encoded_payload, signature = str(token or "").split(".", 1)
    padding = "=" * (-len(encoded_payload) % 4)
    raw = urlsafe_b64decode((encoded_payload + padding).encode("utf-8"))
    expected = hmac.new(settings.JWT_SECRET_KEY.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="Mock payment token không hợp lệ")
    return json.loads(raw.decode("utf-8"))


def _activate_company_plan_from_payment(db, company: dict, plan: dict):
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

    return {
        "company_id": str(company["_id"]),
        "expires_at": next_expires_at,
        "plan_id": plan["id"],
        "plan_name": normalized_plan_name,
    }


def _encode_extra_data(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def _decode_extra_data(extra_data: str) -> dict:
    if not extra_data:
        return {}
    try:
        raw = base64.b64decode(extra_data)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _build_momo_ipn_signature(payload: dict) -> str:
    raw_signature = (
        f"accessKey={settings.MOMO_ACCESS_KEY}"
        f"&amount={payload.get('amount', '')}"
        f"&extraData={payload.get('extraData', '')}"
        f"&message={payload.get('message', '')}"
        f"&orderId={payload.get('orderId', '')}"
        f"&orderInfo={payload.get('orderInfo', '')}"
        f"&orderType={payload.get('orderType', '')}"
        f"&partnerCode={payload.get('partnerCode', '')}"
        f"&payType={payload.get('payType', '')}"
        f"&requestId={payload.get('requestId', '')}"
        f"&responseTime={payload.get('responseTime', '')}"
        f"&resultCode={payload.get('resultCode', '')}"
        f"&transId={payload.get('transId', '')}"
    )
    return hmac.new(
        settings.MOMO_SECRET_KEY.encode("utf-8"),
        raw_signature.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _verify_vnpay_return_signature(params: dict) -> bool:
    provided_hash = str(params.get("vnp_SecureHash") or "")
    filtered = {
        key: value
        for key, value in params.items()
        if key.startswith("vnp_") and key not in {"vnp_SecureHash", "vnp_SecureHashType"}
    }
    query_string = urlencode(sorted(filtered.items()))
    expected_hash = hmac.new(
        settings.VNPAY_HASH_SECRET.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    return bool(provided_hash) and hmac.compare_digest(provided_hash, expected_hash)


@router.post("/vnpay/create")
def create_vnpay_payment_url(
    payload: CreatePaymentPayload,
    request: Request,
    x_forwarded_for: Optional[str] = Header(default=None),
    _current_user=Depends(get_current_user),
):
    if not settings.VNPAY_TMN_CODE or not settings.VNPAY_HASH_SECRET:
        raise HTTPException(status_code=500, detail="Chưa cấu hình thông tin VNPay")

    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền thanh toán không hợp lệ")

    db = get_db()
    raw_company_id = str(_current_user.get("company_id") or "").strip()
    if not raw_company_id:
        raise HTTPException(status_code=400, detail="Tai khoan chua thuoc doanh nghiep nao")

    try:
        company_object_id = ObjectId(raw_company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id khong hop le") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Khong tim thay doanh nghiep")

    plan = _get_plan_by_id(db, payload.plan_id)
    if not plan or plan["id"] == "0":
        raise HTTPException(status_code=400, detail="Goi cuoc khong hop le")

    txn_ref = datetime.now().strftime("%Y%m%d%H%M%S%f")
    create_date = datetime.now().strftime("%Y%m%d%H%M%S")
    order_desc = (payload.order_desc or "Thanh toán gói dịch vụ doanh nghiệp").strip()[:255]

    params = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": settings.VNPAY_TMN_CODE,
        "vnp_Amount": str(int(payload.amount) * 100),
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": txn_ref,
        "vnp_OrderInfo": order_desc,
        "vnp_OrderType": "other",
        "vnp_Locale": "vn",
        "vnp_ReturnUrl": _build_vnpay_return_url(request),
        "vnp_IpAddr": _get_client_ip(request, x_forwarded_for),
        "vnp_CreateDate": create_date,
    }

    sorted_items = sorted(params.items())
    query_string = urlencode(sorted_items)
    secure_hash = hmac.new(
        settings.VNPAY_HASH_SECRET.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    payment_url = f"{settings.VNPAY_URL}?{query_string}&vnp_SecureHash={secure_hash}"
    db.payment_orders.update_one(
        {"provider": "vnpay", "order_id": txn_ref},
        {
            "$set": {
                "provider": "vnpay",
                "order_id": txn_ref,
                "request_id": txn_ref,
                "company_id": str(company_object_id),
                "company_name": company.get("name") or payload.company_name or "",
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "payment_type": payload.payment_type or "renew",
                "amount": int(payload.amount),
                "order_info": order_desc,
                "status": "pending",
                "created_at": datetime.utcnow(),
                "completed_at": None,
                "gateway_response": {"payment_url": payment_url},
            }
        },
        upsert=True,
    )
    return {"payment_url": payment_url, "txn_ref": txn_ref}


@router.get("/vnpay/return")
def vnpay_return(request: Request):
    if not settings.VNPAY_HASH_SECRET:
        raise HTTPException(status_code=500, detail="Chua cau hinh thong tin VNPay")

    db = get_db()
    params = dict(request.query_params)
    txn_ref = str(params.get("vnp_TxnRef") or "").strip()
    response_code = str(params.get("vnp_ResponseCode") or "")
    transaction_status = str(params.get("vnp_TransactionStatus") or "")

    payment_order = db.payment_orders.find_one({"provider": "vnpay", "order_id": txn_ref})
    company_name = (payment_order or {}).get("company_name") or ""

    if not txn_ref or not payment_order:
        redirect_url = _build_frontend_payment_status_url("vnpay", "failed", company_name)
        return RedirectResponse(url=redirect_url, status_code=303)

    if not _verify_vnpay_return_signature(params):
        db.payment_orders.update_one(
            {"provider": "vnpay", "order_id": txn_ref},
            {"$set": {"status": "invalid_signature", "return_payload": params, "updated_at": datetime.utcnow()}},
        )
        redirect_url = _build_frontend_payment_status_url("vnpay", "failed", payment_order.get("company_name") or "")
        return RedirectResponse(url=redirect_url, status_code=303)

    update_fields = {
        "return_payload": params,
        "gateway_response_code": response_code,
        "gateway_transaction_status": transaction_status,
        "updated_at": datetime.utcnow(),
    }

    if response_code != "00" or transaction_status != "00":
        update_fields["status"] = "failed"
        db.payment_orders.update_one(
            {"provider": "vnpay", "order_id": txn_ref},
            {"$set": update_fields},
        )
        redirect_url = _build_frontend_payment_status_url("vnpay", "failed", payment_order.get("company_name") or "")
        return RedirectResponse(url=redirect_url, status_code=303)

    if payment_order.get("completed_at"):
        redirect_url = _build_frontend_payment_status_url("vnpay", "success", payment_order.get("company_name") or "")
        return RedirectResponse(url=redirect_url, status_code=303)

    company_id = str(payment_order.get("company_id") or "").strip()
    plan_id = str(payment_order.get("plan_id") or "").strip().lower()
    try:
        company_object_id = ObjectId(company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id VNPay khong hop le") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Khong tim thay doanh nghiep")

    plan = _get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail="Khong tim thay goi cuoc thanh toan")

    activation = _activate_company_plan_from_payment(db, company, plan)
    update_fields["status"] = "paid"
    update_fields["completed_at"] = datetime.utcnow()
    update_fields["activation"] = activation
    db.payment_orders.update_one(
        {"provider": "vnpay", "order_id": txn_ref},
        {"$set": update_fields},
    )
    redirect_url = _build_frontend_payment_status_url("vnpay", "success", payment_order.get("company_name") or "")
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/mock/create")
def create_mock_payment_qr(
    payload: CreatePaymentPayload,
    request: Request,
    _current_user=Depends(get_current_user),
):
    if payload.amount != 0:
        raise HTTPException(status_code=400, detail="Mock payment chỉ dùng cho giao dịch 0đ")

    db = get_db()
    raw_company_id = str(_current_user.get("company_id") or "").strip()
    if not raw_company_id:
        raise HTTPException(status_code=400, detail="Tài khoản chưa thuộc doanh nghiệp nào")

    try:
        company_object_id = ObjectId(raw_company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id không hợp lệ") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    plan = _get_plan_by_id(db, payload.plan_id)
    if not plan or plan["id"] == "0":
        raise HTTPException(status_code=400, detail="Gói cước không hợp lệ cho mock payment")

    token_payload = {
        "company_id": str(company_object_id),
        "plan_id": plan["id"],
        "payment_type": payload.payment_type or "renew",
        "created_at": datetime.utcnow().isoformat(),
    }
    token = _encode_mock_token(token_payload)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    created_at = datetime.utcnow()
    db.mock_payments.update_one(
        {"token_hash": token_hash},
        {
            "$set": {
                "token_hash": token_hash,
                "company_id": str(company_object_id),
                "company_name": company.get("name") or payload.company_name or "",
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "payment_type": payload.payment_type or "renew",
                "status": "pending",
                "created_at": created_at,
                "scanned_at": None,
                "completed_at": None,
                "scan_count": 0,
            }
        },
        upsert=True,
    )
    payment_url = str(request.url_for("mock_payment_landing")) + f"?token={quote(token, safe='')}"
    return {
        "payment_url": payment_url,
        "payment_mode": "mock_qr",
        "token": token,
        "company_id": str(company_object_id),
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "status": "pending",
    }


@router.get("/mock/landing")
def mock_payment_landing(token: str):
    db = get_db()
    payload = _decode_mock_token(token)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    company_id = str(payload.get("company_id") or "").strip()
    try:
        company_object_id = ObjectId(company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Mock payment token chứa company_id không hợp lệ") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    plan = _get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail="Không tìm thấy gói cước cho mock payment")

    payment_record = db.mock_payments.find_one({"token_hash": token_hash})
    if not payment_record:
        raise HTTPException(status_code=404, detail="Không tìm thấy mock payment")
    if payment_record.get("completed_at"):
        frontend_base_url = _build_frontend_base_url()
        return RedirectResponse(
            url=f"{frontend_base_url}/company-payment?mock_zero=success&company={quote(company.get('name') or '', safe='')}",
            status_code=303,
        )

    result = _activate_company_plan_from_payment(db, company, plan)
    db.mock_payments.update_one(
        {"token_hash": token_hash},
        {"$set": {"completed_at": datetime.utcnow(), "completed_company_id": result["company_id"]}},
    )
    frontend_base_url = _build_frontend_base_url()
    if not frontend_base_url:
        raise HTTPException(status_code=500, detail="Chưa cấu hình FRONTEND_LOGIN_URL")
    redirect_url = f"{frontend_base_url}/company-payment?mock_zero=success&company={quote(company.get('name') or '', safe='')}"
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.headers["X-Mock-Payment-Plan"] = result["plan_id"]
    return response


@router.get("/mock/status")
def get_mock_payment_status(token: str):
    db = get_db()
    _decode_mock_token(token)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    payment_record = db.mock_payments.find_one({"token_hash": token_hash}, {"_id": 0})
    if not payment_record:
        raise HTTPException(status_code=404, detail="Khong tim thay mock payment")

    return {
        "status": payment_record.get("status") or "pending",
        "company_id": payment_record.get("company_id") or "",
        "company_name": payment_record.get("company_name") or "",
        "plan_id": payment_record.get("plan_id") or "",
        "plan_name": payment_record.get("plan_name") or "",
        "scan_count": int(payment_record.get("scan_count") or 0),
        "created_at": payment_record.get("created_at"),
        "scanned_at": payment_record.get("scanned_at"),
        "completed_at": payment_record.get("completed_at"),
    }


class MockConfirmPayload(BaseModel):
    token: str


@router.post("/mock/confirm")
def confirm_mock_payment(payload: MockConfirmPayload):
    db = get_db()
    decoded = _decode_mock_token(payload.token)
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    company_id = str(decoded.get("company_id") or "").strip()
    plan_id = str(decoded.get("plan_id") or "").strip().lower()

    try:
        company_object_id = ObjectId(company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Mock payment token company_id khong hop le") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Khong tim thay doanh nghiep")

    plan = _get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail="Khong tim thay goi cuoc cho mock payment")

    payment_record = db.mock_payments.find_one({"token_hash": token_hash})
    if not payment_record:
        raise HTTPException(status_code=404, detail="Khong tim thay mock payment")

    if payment_record.get("completed_at"):
        return {
            "status": "paid",
            "message": "Mock payment da hoan tat",
            "company_id": str(company_object_id),
        }

    result = _activate_company_plan_from_payment(db, company, plan)
    db.mock_payments.update_one(
        {"token_hash": token_hash},
        {
            "$set": {
                "status": "paid",
                "completed_at": datetime.utcnow(),
                "completed_company_id": result["company_id"],
            }
        },
    )
    return {
        "status": "paid",
        "message": "Da xac nhan thanh toan demo thanh cong",
        "company_id": result["company_id"],
        "plan_id": result["plan_id"],
    }


@router.post("/atm/create")
def create_atm_payment_link(
    payload: CreatePaymentPayload,
    _current_user=Depends(get_current_user),
):
    db = get_db()
    raw_company_id = str(_current_user.get("company_id") or "").strip()
    if not raw_company_id:
        raise HTTPException(status_code=400, detail="Tai khoan chua thuoc doanh nghiep nao")

    try:
        company_object_id = ObjectId(raw_company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id khong hop le") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Khong tim thay doanh nghiep")

    plan = _get_plan_by_id(db, payload.plan_id)
    if not plan or plan["id"] == "0":
        raise HTTPException(status_code=400, detail="Goi cuoc khong hop le")

    token_payload = {
        "company_id": str(company_object_id),
        "plan_id": plan["id"],
        "payment_type": payload.payment_type or "renew",
        "channel": "atm",
        "created_at": datetime.utcnow().isoformat(),
    }
    token = _encode_mock_token(token_payload)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db.payment_orders.update_one(
        {"provider": "atm", "order_id": token_hash},
        {
            "$set": {
                "provider": "atm",
                "order_id": token_hash,
                "request_id": token_hash,
                "company_id": str(company_object_id),
                "company_name": company.get("name") or payload.company_name or "",
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "payment_type": payload.payment_type or "renew",
                "amount": int(payload.amount or plan["price"]),
                "order_info": payload.order_desc or f"Thanh toan ATM {plan['name']}",
                "status": "pending",
                "created_at": datetime.utcnow(),
                "completed_at": None,
            }
        },
        upsert=True,
    )

    payment_url = (
        f"{_build_frontend_base_url()}/company-payment?atm_status=confirm"
        f"&atm_token={quote(token, safe='')}"
        f"&company={quote(company.get('name') or '', safe='')}"
    )
    return {
        "payment_url": payment_url,
        "payment_mode": "atm_confirm",
        "token": token,
        "plan_id": plan["id"],
        "plan_name": plan["name"],
    }


class AtmConfirmPayload(BaseModel):
    token: str


@router.post("/atm/confirm")
def confirm_atm_payment(payload: AtmConfirmPayload):
    db = get_db()
    decoded = _decode_mock_token(payload.token)
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    company_id = str(decoded.get("company_id") or "").strip()
    plan_id = str(decoded.get("plan_id") or "").strip().lower()

    try:
        company_object_id = ObjectId(company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="ATM payment token company_id khong hop le") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Khong tim thay doanh nghiep")

    plan = _get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail="Khong tim thay goi cuoc thanh toan")

    payment_order = db.payment_orders.find_one({"provider": "atm", "order_id": token_hash})
    if not payment_order:
        raise HTTPException(status_code=404, detail="Khong tim thay giao dich ATM")

    if payment_order.get("completed_at"):
        return {"status": "paid", "message": "Giao dich ATM da hoan tat"}

    activation = _activate_company_plan_from_payment(db, company, plan)
    db.payment_orders.update_one(
        {"provider": "atm", "order_id": token_hash},
        {
            "$set": {
                "status": "paid",
                "completed_at": datetime.utcnow(),
                "activation": activation,
            }
        },
    )
    return {
        "status": "paid",
        "message": "Da xac nhan thanh toan the ATM thanh cong",
        "company_id": activation["company_id"],
        "plan_id": activation["plan_id"],
    }


@router.post("/momo/create")
def create_momo_payment_url(
    payload: CreatePaymentPayload,
    request: Request,
    _current_user=Depends(get_current_user),
):
    if not settings.MOMO_PARTNER_CODE or not settings.MOMO_ACCESS_KEY or not settings.MOMO_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Chưa cấu hình thông tin MoMo")

    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền thanh toán không hợp lệ")

    db = get_db()
    raw_company_id = str(_current_user.get("company_id") or "").strip()
    if not raw_company_id:
        raise HTTPException(status_code=400, detail="Tai khoan chua thuoc doanh nghiep nao")

    try:
        company_object_id = ObjectId(raw_company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id khong hop le") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Khong tim thay doanh nghiep")

    plan = _get_plan_by_id(db, payload.plan_id)
    if not plan or plan["id"] == "0":
        raise HTTPException(status_code=400, detail="Goi cuoc khong hop le")

    order_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    request_id = f"{order_id}-req"
    amount = str(int(payload.amount))
    order_info = (payload.order_desc or "Thanh toán gói dịch vụ doanh nghiệp").strip()[:255]
    redirect_url = _build_momo_return_url(payload)
    ipn_url = _build_backend_public_url(request, "momo_ipn")
    request_type = "captureWallet"
    extra_data = _encode_extra_data(
        {
            "company_id": str(company_object_id),
            "plan_id": plan["id"],
            "payment_type": payload.payment_type or "renew",
            "company_name": company.get("name") or payload.company_name or "",
        }
    )

    raw_signature = (
        f"accessKey={settings.MOMO_ACCESS_KEY}"
        f"&amount={amount}"
        f"&extraData={extra_data}"
        f"&ipnUrl={ipn_url}"
        f"&orderId={order_id}"
        f"&orderInfo={order_info}"
        f"&partnerCode={settings.MOMO_PARTNER_CODE}"
        f"&redirectUrl={redirect_url}"
        f"&requestId={request_id}"
        f"&requestType={request_type}"
    )
    signature = hmac.new(
        settings.MOMO_SECRET_KEY.encode("utf-8"),
        raw_signature.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    request_body = {
        "partnerCode": settings.MOMO_PARTNER_CODE,
        "partnerName": "Trợ lý ảo doanh nghiệp",
        "storeId": "TroLyAoDoanhNghiep",
        "requestId": request_id,
        "amount": amount,
        "orderId": order_id,
        "orderInfo": order_info,
        "redirectUrl": redirect_url,
        "ipnUrl": ipn_url,
        "lang": "vi",
        "extraData": extra_data,
        "requestType": request_type,
        "signature": signature,
    }

    db.payment_orders.update_one(
        {"provider": "momo", "order_id": order_id},
        {
            "$set": {
                "provider": "momo",
                "order_id": order_id,
                "request_id": request_id,
                "company_id": str(company_object_id),
                "company_name": company.get("name") or payload.company_name or "",
                "plan_id": plan["id"],
                "plan_name": plan["name"],
                "payment_type": payload.payment_type or "renew",
                "amount": int(payload.amount),
                "order_info": order_info,
                "status": "pending",
                "created_at": datetime.utcnow(),
                "completed_at": None,
                "momo_request_type": request_type,
            }
        },
        upsert=True,
    )

    try:
        response = requests.post(settings.MOMO_ENDPOINT, json=request_body, timeout=20)
        data = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Không thể kết nối MoMo: {exc}")

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=data.get("message") or "MoMo không phản hồi hợp lệ")

    payment_url = data.get("payUrl") or data.get("deeplink") or data.get("shortLink")
    if not payment_url:
        raise HTTPException(status_code=502, detail=data.get("message") or "MoMo không trả về liên kết thanh toán")

    db.payment_orders.update_one(
        {"provider": "momo", "order_id": order_id},
        {
            "$set": {
                "gateway_response": data,
                "gateway_result_code": data.get("resultCode"),
                "pay_url": data.get("payUrl"),
                "deeplink": data.get("deeplink"),
                "qr_code_url": data.get("qrCodeUrl"),
            }
        },
    )

    return {
        "payment_url": payment_url,
        "order_id": order_id,
        "request_id": request_id,
        "raw_response": data,
    }


@router.post("/momo/ipn")
def momo_ipn(payload: dict = Body(...)):
    if not settings.MOMO_ACCESS_KEY or not settings.MOMO_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Chua cau hinh thong tin MoMo")

    db = get_db()
    order_id = str(payload.get("orderId") or "").strip()
    provided_signature = str(payload.get("signature") or "")
    expected_signature = _build_momo_ipn_signature(payload)

    if not provided_signature or not hmac.compare_digest(provided_signature, expected_signature):
        if order_id:
            db.payment_orders.update_one(
                {"provider": "momo", "order_id": order_id},
                {"$set": {"status": "invalid_signature", "ipn_payload": payload, "updated_at": datetime.utcnow()}},
            )
        raise HTTPException(status_code=400, detail="Chu ky IPN MoMo khong hop le")

    payment_order = db.payment_orders.find_one({"provider": "momo", "order_id": order_id})
    if not payment_order:
        raise HTTPException(status_code=404, detail="Khong tim thay don hang MoMo")

    result_code = int(payload.get("resultCode") or -1)
    update_fields = {
        "ipn_payload": payload,
        "gateway_result_code": result_code,
        "gateway_trans_id": payload.get("transId"),
        "gateway_message": payload.get("message"),
        "pay_type": payload.get("payType"),
        "updated_at": datetime.utcnow(),
    }

    if result_code != 0:
        update_fields["status"] = "failed"
        db.payment_orders.update_one(
            {"provider": "momo", "order_id": order_id},
            {"$set": update_fields},
        )
        return {"resultCode": 0, "message": "IPN failure recorded"}

    if payment_order.get("completed_at"):
        return {"resultCode": 0, "message": "Order already confirmed"}

    extra_data = _decode_extra_data(str(payload.get("extraData") or ""))
    company_id = str(extra_data.get("company_id") or payment_order.get("company_id") or "").strip()
    plan_id = str(extra_data.get("plan_id") or payment_order.get("plan_id") or "").strip().lower()

    try:
        company_object_id = ObjectId(company_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="company_id trong IPN MoMo khong hop le") from exc

    company = db.companies.find_one({"_id": company_object_id})
    if not company:
        raise HTTPException(status_code=404, detail="Khong tim thay doanh nghiep")

    plan = _get_plan_by_id(db, plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail="Khong tim thay goi cuoc thanh toan")

    activation = _activate_company_plan_from_payment(db, company, plan)
    update_fields["status"] = "paid"
    update_fields["completed_at"] = datetime.utcnow()
    update_fields["activation"] = activation
    db.payment_orders.update_one(
        {"provider": "momo", "order_id": order_id},
        {"$set": update_fields},
    )
    return {"resultCode": 0, "message": "Success"}
