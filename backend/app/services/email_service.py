import yagmail

from app.core.config import settings
from app.services.account_roles import get_developer_emails

PUBLIC_LOGIN_URL = "https://trolyaodoanhnghiep.io.vn"


EMAIL_PURPOSE_MESSAGES = {
    "create_password": {
        "subject": "Mã xác thực tạo mật khẩu - Trợ lý ảo doanh nghiệp",
        "headline": "Chúng tôi đã nhận được yêu cầu tạo mật khẩu cho tài khoản của bạn.",
    },
    "change_email": {
        "subject": "Cảnh báo đổi email - Trợ lý ảo doanh nghiệp",
        "headline": "Chúng tôi đã nhận được yêu cầu đổi email cho tài khoản của bạn.",
    },
    "change_password": {
        "subject": "Cảnh báo đổi mật khẩu - Trợ lý ảo doanh nghiệp",
        "headline": "Chúng tôi đã nhận được yêu cầu đổi mật khẩu cho tài khoản của bạn.",
    },
    "change_email_and_password": {
        "subject": "Cảnh báo đổi email và mật khẩu - Trợ lý ảo doanh nghiệp",
        "headline": "Chúng tôi đã nhận được yêu cầu đổi email và mật khẩu cho tài khoản của bạn.",
    },
    "reset_password": {
        "subject": "Mã xác thực lấy lại mật khẩu - Trợ lý ảo doanh nghiệp",
        "headline": "Chúng tôi đã nhận được yêu cầu lấy lại mật khẩu cho tài khoản của bạn.",
    },
    "generic": {
        "subject": "Mã xác thực - Trợ lý ảo doanh nghiệp",
        "headline": "Chúng tôi đã nhận được yêu cầu gửi mã xác thực cho tài khoản của bạn.",
    },
    "delete_company": {
        "subject": "Mã xác thực xóa doanh nghiệp - Trợ lý ảo doanh nghiệp",
        "headline": "Chúng tôi đã nhận được yêu cầu xóa toàn bộ dữ liệu doanh nghiệp của bạn.",
    },
}


def _build_email_contents(to_email: str, code: str, purpose: str) -> tuple[str, list[str]]:
    message = EMAIL_PURPOSE_MESSAGES.get(purpose, EMAIL_PURPOSE_MESSAGES["generic"])
    subject = message["subject"]
    contents = [
        f"Xin chào {to_email},",
        "",
        message["headline"],
        f"Mã xác thực của bạn là: {code}",
        f"Mã có hiệu lực trong {settings.EMAIL_CODE_EXPIRE_MINUTES} phút.",
        "",
        "Không được chia sẻ mã này cho người khác.",
    ]
    return subject, contents


def _create_smtp_client():
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise RuntimeError("SMTP chưa được cấu hình đầy đủ")

    return yagmail.SMTP(
        user=settings.SMTP_USERNAME,
        password=settings.SMTP_PASSWORD,
        host=settings.SMTP_HOST or "smtp.gmail.com",
        port=settings.SMTP_PORT,
        smtp_starttls=settings.SMTP_USE_TLS,
        smtp_ssl=not settings.SMTP_USE_TLS,
    )


def send_email(to_email: str, subject: str, contents: list[str]):
    smtp_client = _create_smtp_client()
    try:
        smtp_client.send(
            to=to_email,
            subject=subject,
            contents=contents,
            headers={
                "From": f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME}>"
            },
        )
    finally:
        smtp_client.close()


def send_verification_code_email(to_email: str, code: str, purpose: str = "generic"):
    subject, contents = _build_email_contents(to_email, code, purpose)
    send_email(to_email, subject, contents)


def send_password_changed_email(to_email: str):
    send_email(
        to_email,
        "Thông báo đổi mật khẩu thành công - Trợ lý ảo doanh nghiệp",
        [
            f"Xin chào {to_email},",
            "",
            "Mật khẩu tài khoản của bạn vừa được thay đổi thành công.",
            "Nếu bạn không thực hiện hành động này, vui lòng liên hệ quản trị viên hoặc hỗ trợ ngay.",
        ],
    )


def send_company_action_email(
    to_email: str,
    company_name: str,
    action_label: str,
    message_body: str,
):
    developer_emails = sorted(get_developer_emails())
    developer_contact = ", ".join(developer_emails) if developer_emails else "Chưa cập nhật"
    send_email(
        to_email,
        f"{action_label} - Trợ lý ảo doanh nghiệp",
        [
            f"Xin chào {to_email},",
            "",
            f"Doanh nghiệp '{company_name}' {message_body}",
            f"Vui lòng liên hệ nhà phát triển nếu cần hỗ trợ: {developer_contact}",
            f"Trang đăng nhập hệ thống: {PUBLIC_LOGIN_URL}",
        ],
    )


def send_company_renewed_success_email(
    to_email: str,
    company_name: str,
    plan_name: str,
    expires_at_text: str | None = None,
):
    contents = [
        f"Xin chào {to_email},",
        "",
        f"Doanh nghiệp '{company_name}' đã gia hạn thành công với {plan_name}.",
    ]
    if expires_at_text:
        contents.append(f"Thời hạn sử dụng hiện tại: {expires_at_text}.")
    contents.extend(
        [
            f"Bạn có thể đăng nhập và tiếp tục sử dụng dịch vụ tại đây: {PUBLIC_LOGIN_URL}",
        ]
    )
    send_email(
        to_email,
        "Gia hạn doanh nghiệp thành công - Trợ lý ảo doanh nghiệp",
        contents,
    )


def send_company_blocked_email(to_email: str, company_name: str):
    send_email(
        to_email,
        "Doanh nghiệp đã bị khóa - Trợ lý ảo doanh nghiệp",
        [
            f"Xin chào {to_email},",
            "",
            f"Doanh nghiệp '{company_name}' hiện đã bị khóa.",
            "Bạn tạm thời không thể tiếp tục sử dụng hệ thống cho đến khi doanh nghiệp được mở khóa.",
            f"Nếu cần hỗ trợ, vui lòng đăng nhập hoặc liên hệ nhà phát triển tại: {PUBLIC_LOGIN_URL}",
        ],
    )


def send_company_unblocked_email(to_email: str, company_name: str):
    send_email(
        to_email,
        "Doanh nghiệp đã được mở khóa - Trợ lý ảo doanh nghiệp",
        [
            f"Xin chào {to_email},",
            "",
            f"Doanh nghiệp '{company_name}' đã được mở khóa.",
            "Bạn có thể đăng nhập và tiếp tục sử dụng hệ thống nếu gói dịch vụ vẫn còn hiệu lực.",
            f"Trang đăng nhập hệ thống: {PUBLIC_LOGIN_URL}",
        ],
    )


def send_company_expiry_reminder_email(
    to_email: str,
    company_name: str,
    expires_at_text: str,
    days_left: int,
):
    day_label = "1 ngày" if days_left == 1 else f"{days_left} ngày"
    send_email(
        to_email,
        "Thông báo gói cước sắp hết hạn - Trợ lý ảo doanh nghiệp",
        [
            f"Xin chào {to_email},",
            "",
            f"Doanh nghiệp '{company_name}' của bạn sẽ hết hạn sau {day_label}.",
            f"Thời điểm hết hạn dự kiến: {expires_at_text}.",
            "Vui lòng gia hạn sớm để tránh gián đoạn sử dụng hệ thống.",
            f"Bạn có thể đăng nhập và gia hạn tại đây: {PUBLIC_LOGIN_URL}",
        ],
    )


def send_trial_expiry_reminder_email(
    to_email: str,
    company_name: str,
    expires_at_text: str,
):
    send_email(
        to_email,
        "Thông báo dùng thử sắp kết thúc - Trợ lý ảo doanh nghiệp",
        [
            f"Xin chào {to_email},",
            "",
            f"Gói dùng thử 7 ngày của doanh nghiệp '{company_name}' sẽ kết thúc sau 1 ngày.",
            f"Thời điểm kết thúc dự kiến: {expires_at_text}.",
            "Sau khi hết hạn, toàn bộ dữ liệu dùng thử sẽ được xóa tự động.",
            f"Bạn có thể đăng nhập và chuyển sang gói thanh toán tại đây: {PUBLIC_LOGIN_URL}",
        ],
    )
