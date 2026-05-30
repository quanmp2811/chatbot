from bson import ObjectId
from fastapi import HTTPException


def build_company_access_state(company: dict | None) -> str | None:
    if not company:
        return None
    if company.get("is_blocked"):
        return "blocked"
    if company.get("is_expired"):
        return "expired"
    return "active"


def get_company_by_id(db, company_id: str | None) -> dict | None:
    if not company_id:
        return None
    try:
        return db.companies.find_one({"_id": ObjectId(company_id)})
    except Exception:
        return None


def require_company_with_access(db, user: dict, allowed_states: set[str] | None = None) -> tuple[str, dict]:
    company_id = str(user.get("company_id") or "")
    if not company_id:
        raise HTTPException(status_code=400, detail="Bạn chưa thuộc doanh nghiệp nào")

    company = get_company_by_id(db, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Không tìm thấy doanh nghiệp")

    access_state = build_company_access_state(company)
    if allowed_states and access_state in allowed_states:
        return company_id, company

    if access_state == "blocked":
        raise HTTPException(status_code=403, detail="Doanh nghiệp đang bị khóa")
    if access_state == "expired":
        raise HTTPException(status_code=403, detail="Doanh nghiệp đã hết hạn")

    return company_id, company
