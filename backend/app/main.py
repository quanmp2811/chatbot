# -*- coding: utf-8 -*-
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.mongo import ensure_indexes, get_db
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.companies.router import router as companies_router
from app.modules.developer.router import router as developer_router
from app.modules.documents.drive_router import router as drive_router
from app.modules.documents.router import router as tai_lieu_router
from app.modules.payments.router import router as payments_router
from app.modules.users.router import router as nguoi_dung_router
from app.routers import upload
from app.routers.chats import router as chats_router
from app.services.subscription_reminder_service import (
    cleanup_expired_trial_companies,
    send_expiring_company_reminders,
    send_trial_expiry_reminders,
)
from app.services.sync_service import sync_drive

load_dotenv()

scheduler = BackgroundScheduler()


def auto_sync():
    db = get_db()
    companies = db["companies"].find(
        {"approval_status": "approved", "is_blocked": {"$ne": True}, "is_expired": {"$ne": True}}
    )
    for company in companies:
        sync_drive(company)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes()
    scheduler.add_job(
        auto_sync,
        "interval",
        minutes=settings.SYNC_INTERVAL_MINUTES,
        max_instances=1,
        coalesce=True,
        id="drive_auto_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        send_expiring_company_reminders,
        "interval",
        hours=6,
        max_instances=1,
        coalesce=True,
        id="company_expiry_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        send_trial_expiry_reminders,
        "interval",
        hours=6,
        max_instances=1,
        coalesce=True,
        id="trial_expiry_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        cleanup_expired_trial_companies,
        "interval",
        hours=1,
        max_instances=1,
        coalesce=True,
        id="trial_expiry_cleanup",
        replace_existing=True,
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Trợ lý ảo doanh nghiệp", lifespan=lifespan)
api_router = APIRouter(prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://app.trolyaodoanhnghiep.io.vn",
    ],
    allow_origin_regex=r"https://([a-zA-Z0-9-]+\.)?trolyaodoanhnghiep\.io\.vn|https://[a-zA-Z0-9-]+\.trycloudflare\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(nguoi_dung_router)
app.include_router(companies_router)
app.include_router(tai_lieu_router)
app.include_router(drive_router)
app.include_router(admin_router)
app.include_router(developer_router)
app.include_router(payments_router)
app.include_router(chats_router)
app.include_router(upload.router)
app.include_router(auth_router)

api_router.include_router(nguoi_dung_router)
api_router.include_router(companies_router)
api_router.include_router(tai_lieu_router)
api_router.include_router(drive_router)
api_router.include_router(admin_router)
api_router.include_router(developer_router)
api_router.include_router(payments_router)
api_router.include_router(chats_router)
api_router.include_router(upload.router)
api_router.include_router(auth_router)

app.include_router(api_router)


@app.get("/", summary="Trang chủ", description="Kiểm tra trạng thái máy chủ")
def trang_chu():
    return {"thong_bao": "Máy chủ đang chạy bình thường"}
