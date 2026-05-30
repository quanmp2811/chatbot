from datetime import datetime, timezone


BLOCKED_ACCOUNTS_COLLECTION = "blocked_accounts"


def normalize_email(email: str | None) -> str | None:
    value = (email or "").strip().lower()
    return value or None


def _build_match_conditions(email: str | None = None, google_sub: str | None = None):
    conditions = []
    normalized_email = normalize_email(email)
    if normalized_email:
        conditions.append({"email": normalized_email})
    if google_sub:
        conditions.append({"google_sub": google_sub})
    return conditions


def find_blocked_account(db, email: str | None = None, google_sub: str | None = None):
    return None


def block_account(
    db,
    email: str | None = None,
    google_sub: str | None = None,
    *,
    reason: str,
    company_id: str | None = None,
    session=None,
):
    return


def unblock_account(db, email: str | None = None, google_sub: str | None = None, session=None):
    return
