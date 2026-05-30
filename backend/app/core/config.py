from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str
    JWT_SECRET_KEY: str
    DEVELOPER_EMAILS: str = ""
    FRONTEND_LOGIN_URL: str = "http://localhost:5173/login"
    SYNC_INTERVAL_MINUTES: int = 1
    OLLAMA_URL: str = "http://localhost:11434/api/generate"
    OLLAMA_MODEL: str = "qwen2.5:7b-instruct"
    OLLAMA_TIMEOUT_SECONDS: int = 90
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "Trợ lý ảo doanh nghiệp"
    SMTP_USE_TLS: bool = True
    EMAIL_CODE_EXPIRE_MINUTES: int = 10
    EMAIL_DEBUG_RETURN_CODE: bool = False
    VNPAY_TMN_CODE: str = ""
    VNPAY_HASH_SECRET: str = ""
    VNPAY_URL: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    VNPAY_RETURN_URL: str = ""
    MOMO_PARTNER_CODE: str = "MOMO"
    MOMO_ACCESS_KEY: str = "F8BBA842ECF85"
    MOMO_SECRET_KEY: str = "K951B6PE1waDMi640xX08PD3vg6EkVlz"
    MOMO_ENDPOINT: str = "https://test-payment.momo.vn/v2/gateway/api/create"
    MOMO_RETURN_URL: str = ""
    MOMO_IPN_URL: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
