from app.core.config import settings
from app.services.account_blocklist import normalize_email

DEFAULT_DEVELOPER_EMAILS = {
    "phamquan28112004@gmail.com",
    "trolyaodoanhnghiep@gmail.com",
}


def get_developer_emails() -> set[str]:
    raw = settings.DEVELOPER_EMAILS or ""
    values = [normalize_email(item) for item in raw.replace(";", ",").split(",")]
    configured = {item for item in values if item}
    return configured | DEFAULT_DEVELOPER_EMAILS


def is_developer_email(email: str | None) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    return normalized in get_developer_emails()


def resolve_user_role(user: dict | None) -> str:
    if not user:
        return "user"
    if is_developer_email(user.get("email")) or is_developer_email(user.get("contact_email")):
        return "super_admin"
    return user.get("role", "user")
