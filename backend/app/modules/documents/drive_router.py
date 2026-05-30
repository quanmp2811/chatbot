# app/modules/documents/drive_router.py
from fastapi import APIRouter, Depends, HTTPException
from urllib.parse import urlencode
import requests
from datetime import datetime, timedelta
from bson import ObjectId

from app.db.mongo import get_db
from app.modules.users.router import get_current_user
from app.core.config import settings

router = APIRouter(prefix="/drive", tags=["Google Drive"])

# ⚠️ Chú ý: Endpoint /connect và /exchange đã được move tới /companies/router.py
# để tránh trùng lặp và dễ quản lý. Nếu cần update Google Drive flow, hãy
# cập nhật tại companies/router.py thay vì tại đây.