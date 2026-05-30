from datetime import datetime, timedelta

from app.core.config import settings
from app.db.mongo import get_db
from app.modules.companies.router import get_active_subscription_period, perform_company_delete
from app.services.email_service import (
    send_company_expiry_reminder_email,
    send_trial_expiry_reminder_email,
)


REMINDER_DAYS_BEFORE_EXPIRY = 5
TRIAL_REMINDER_DAYS_BEFORE_EXPIRY = 1


def _normalize_delivery_email(company: dict) -> str:
    return (company.get("contact_email") or company.get("created_by") or "").strip().lower()


def _format_expiry_time(expires_at: datetime) -> str:
    return expires_at.strftime("%H:%M %d/%m/%Y UTC")


def send_expiring_company_reminders():
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        return

    db = get_db()
    now = datetime.utcnow()
    reminder_deadline = now + timedelta(days=REMINDER_DAYS_BEFORE_EXPIRY)

    companies = db.companies.find(
        {
            "approval_status": "approved",
            "is_trial": {"$ne": True},
            "is_blocked": {"$ne": True},
            "is_expired": {"$ne": True},
            "expires_at": {"$gt": now, "$lte": reminder_deadline},
        }
    )

    for company in companies:
        company_id = str(company.get("_id"))
        expires_at = company.get("expires_at")
        if not isinstance(expires_at, datetime):
            continue

        delivery_email = _normalize_delivery_email(company)
        if not delivery_email:
            continue

        reminder_marker = expires_at.isoformat()
        if company.get("expiry_reminder_sent_for") == reminder_marker:
            continue

        active_period = get_active_subscription_period(company, fallback_now=now)
        if not active_period:
            continue

        time_left = expires_at - now
        days_left = max(1, (time_left.days + (1 if time_left.seconds > 0 else 0)))

        try:
            send_company_expiry_reminder_email(
                delivery_email,
                company.get("name") or "Doanh nghiệp",
                _format_expiry_time(expires_at),
                days_left,
            )
            db.companies.update_one(
                {"_id": company["_id"]},
                {
                    "$set": {
                        "expiry_reminder_sent_for": reminder_marker,
                        "expiry_reminder_sent_at": now,
                    }
                },
            )
        except Exception as exc:
            print(f"[company.expiry_reminder] failed company_id={company_id} error={exc}")


def send_trial_expiry_reminders():
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        return

    db = get_db()
    now = datetime.utcnow()
    reminder_start = now
    reminder_end = now + timedelta(days=TRIAL_REMINDER_DAYS_BEFORE_EXPIRY)

    companies = db.companies.find(
        {
            "approval_status": "approved",
            "is_trial": True,
            "is_blocked": {"$ne": True},
            "is_expired": {"$ne": True},
            "expires_at": {"$gt": reminder_start, "$lte": reminder_end},
        }
    )

    for company in companies:
        company_id = str(company.get("_id"))
        expires_at = company.get("expires_at")
        if not isinstance(expires_at, datetime):
            continue

        delivery_email = _normalize_delivery_email(company)
        if not delivery_email:
            continue

        reminder_marker = expires_at.isoformat()
        if company.get("trial_expiry_reminder_sent_for") == reminder_marker:
            continue

        try:
            send_trial_expiry_reminder_email(
                delivery_email,
                company.get("name") or "Doanh nghiệp",
                _format_expiry_time(expires_at),
            )
            db.companies.update_one(
                {"_id": company["_id"]},
                {
                    "$set": {
                        "trial_expiry_reminder_sent_for": reminder_marker,
                        "trial_expiry_reminder_sent_at": now,
                    }
                },
            )
        except Exception as exc:
            print(f"[company.trial_reminder] failed company_id={company_id} error={exc}")


def cleanup_expired_trial_companies():
    db = get_db()
    now = datetime.utcnow()

    companies = db.companies.find(
        {
            "is_trial": True,
            "auto_delete_after_expiry": True,
            "expires_at": {"$lte": now},
        }
    )

    for company in companies:
        company_id = str(company.get("_id"))
        try:
            perform_company_delete(db, company["_id"], company_id)
        except Exception as exc:
            print(f"[company.trial_cleanup] failed company_id={company_id} error={exc}")
