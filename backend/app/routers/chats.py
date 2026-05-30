from datetime import datetime
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.db.mongo import drive_files_collection, get_db
from app.modules.users.router import get_current_user
from app.services.company_state import require_company_with_access
from app.services.ai.llm_service import generate_answer, generate_final_answer
from app.services.ai.rag_service import _is_greeting_message, rag_answer, rag_answer_with_full_file_chunks

router = APIRouter(prefix="/chats", tags=["Chats"])
DEFAULT_CHAT_TITLE = "Đoạn chat mới"


def _require_company(user: dict) -> str:
    db = get_db()
    company_id, _company = require_company_with_access(db, user)
    return company_id


def _get_owned_chat_or_404(db, chat_id: str, user: dict):
    try:
        object_id = ObjectId(chat_id)
    except Exception:
        raise HTTPException(status_code=400, detail="chat_id không hợp lệ")

    chat = db.chats.find_one(
        {
            "_id": object_id,
            "user_id": str(user["_id"]),
            "company_id": _require_company(user),
        }
    )
    if not chat:
        raise HTTPException(status_code=404, detail="Không tìm thấy đoạn chat")
    return chat


def _fallback_title(question: str) -> str:
    cleaned = " ".join((question or "").strip().split())
    if not cleaned:
        return "Đoạn chat mới"
    return cleaned[:48]


def _ensure_source_name(company_id: str, source: dict | None) -> dict | None:
    if not source:
        return source
    if source.get("file_name"):
        return source
    file_id = source.get("file_id")
    if not file_id:
        return source

    doc = drive_files_collection.find_one(
        {"company_id": company_id, "file_id": file_id},
        {"file_name": 1},
    )
    file_name = (doc or {}).get("file_name")
    if not file_name:
        return source

    return {**source, "file_name": file_name}


def _generate_chat_title(first_question: str) -> str:
    prompt = f"""
Đặt tên ngắn gọn cho đoạn chat dựa trên câu hỏi đầu tiên.

Yêu cầu:
- tối đa 8 từ
- không dấu ngoặc
- không dấu chấm cuối

Câu hỏi:
{first_question}
"""
    try:
        title = generate_answer(prompt)
        title = title.strip().replace('"', "").replace("'", "")
        title = " ".join(title.split())
        return title[:80] if title else _fallback_title(first_question)
    except Exception:
        return _fallback_title(first_question)


def _should_refresh_chat_title(db, chat: dict, question: str) -> bool:
    current_title = (chat.get("title") or "").strip()
    if current_title == DEFAULT_CHAT_TITLE:
        return True

    if _is_greeting_message(question):
        return _is_greeting_message(current_title)
    return _is_greeting_message(current_title)


def _update_chat_title_if_needed(db, chat: dict, question: str) -> str:
    updated_title = (chat.get("title") or DEFAULT_CHAT_TITLE).strip() or DEFAULT_CHAT_TITLE
    if not _should_refresh_chat_title(db, chat, question):
        return updated_title

    updated_title = _generate_chat_title(question)
    db.chats.update_one({"_id": chat["_id"]}, {"$set": {"title": updated_title}})
    return updated_title


def _run_chat_job(chat_id: str, assistant_message_id: str, question: str, company_id: str, user_id: str):
    db = get_db()
    try:
        rag = rag_answer(company_id, question, user_id)
        if isinstance(rag, dict):
            answer = rag.get("answer") or ""
            source = _ensure_source_name(company_id, rag.get("source"))
        else:
            answer = str(rag or "")
            source = None

        current_message = db.messages.find_one({"_id": ObjectId(assistant_message_id)}, {"status": 1})
        if (current_message or {}).get("status") == "stopped":
            return

        db.messages.update_one(
            {"_id": ObjectId(assistant_message_id), "status": {"$ne": "stopped"}},
            {
                "$set": {
                    "content": answer,
                    "source": source,
                    "status": "completed",
                    "completed_at": datetime.utcnow(),
                }
            },
        )

        chat = db.chats.find_one({"_id": ObjectId(chat_id)})
        if chat:
            _update_chat_title_if_needed(db, chat, question)
    except Exception as exc:
        db.messages.update_one(
            {"_id": ObjectId(assistant_message_id)},
            {
                "$set": {
                    "content": f"Lỗi xử lý: {exc}",
                    "status": "failed",
                    "completed_at": datetime.utcnow(),
                }
            },
        )


@router.post("")
def create_chat(db=Depends(get_db), user=Depends(get_current_user)):
    company_id = _require_company(user)
    existing_chat = db.chats.find_one(
        {
            "user_id": str(user["_id"]),
            "company_id": company_id,
            "title": DEFAULT_CHAT_TITLE,
        },
        sort=[("created_at", -1)],
    )
    if existing_chat:
        existing_chat["_id"] = str(existing_chat["_id"])
        return existing_chat

    chat = {
        "user_id": str(user["_id"]),
        "company_id": company_id,
        "title": DEFAULT_CHAT_TITLE,
        "created_at": datetime.utcnow(),
    }
    res = db.chats.insert_one(chat)
    chat["_id"] = str(res.inserted_id)
    return chat


@router.get("")
def list_chats(db=Depends(get_db), user=Depends(get_current_user)):
    chats = list(
        db.chats.find(
            {"user_id": str(user["_id"]), "company_id": _require_company(user)}
        ).sort("created_at", -1)
    )
    for chat in chats:
        chat["_id"] = str(chat["_id"])
    return chats


@router.get("/{chat_id}")
def get_messages(chat_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    _get_owned_chat_or_404(db, chat_id, user)
    msgs = list(
        db.messages.find({"chat_id": chat_id, "company_id": _require_company(user)}).sort("created_at", 1)
    )
    for msg in msgs:
        msg["_id"] = str(msg["_id"])
    return msgs


@router.post("/{chat_id}/messages")
def send_message(chat_id: str, body: dict, db=Depends(get_db), user=Depends(get_current_user)):
    chat = _get_owned_chat_or_404(db, chat_id, user)
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="Nội dung tin nhắn trống")

    company_id = _require_company(user)
    user_msg = {
        "chat_id": chat_id,
        "company_id": company_id,
        "user_id": str(user["_id"]),
        "role": "user",
        "content": content,
        "status": "completed",
        "created_at": datetime.utcnow(),
    }
    user_res = db.messages.insert_one(user_msg)
    user_msg["_id"] = str(user_res.inserted_id)

    rag_result = rag_answer(company_id, content, str(user["_id"]))
    answer = rag_result.get("answer", "") if isinstance(rag_result, dict) else str(rag_result)
    assistant_msg = {
        "chat_id": chat_id,
        "company_id": company_id,
        "user_id": str(user["_id"]),
        "role": "assistant",
        "content": answer,
        "source": rag_result.get("source") if isinstance(rag_result, dict) else None,
        "status": "completed",
        "created_at": datetime.utcnow(),
    }
    assistant_res = db.messages.insert_one(assistant_msg)
    assistant_msg["_id"] = str(assistant_res.inserted_id)

    updated_title = _update_chat_title_if_needed(db, chat, content)

    return {
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        "chat_title": updated_title,
    }


@router.put("/{chat_id}")
def rename_chat(chat_id: str, body: dict, db=Depends(get_db), user=Depends(get_current_user)):
    _get_owned_chat_or_404(db, chat_id, user)
    db.chats.update_one({"_id": ObjectId(chat_id)}, {"$set": {"title": body["title"]}})
    return {"ok": True}


@router.delete("/{chat_id}")
def delete_chat(chat_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    _get_owned_chat_or_404(db, chat_id, user)
    db.chats.delete_one({"_id": ObjectId(chat_id)})
    db.messages.delete_many({"chat_id": chat_id, "company_id": _require_company(user)})
    return {"ok": True}


@router.post("/chat-test")
def chat_test(question: str, user=Depends(get_current_user)):
    result = rag_answer(_require_company(user), question, str(user["_id"]))
    if isinstance(result, dict):
        return result
    return {"answer": result}


@router.post("/chat-full-file-chunks")
def chat_full_file_chunks(question: str, user=Depends(get_current_user)):
    result = rag_answer_with_full_file_chunks(_require_company(user), question, str(user["_id"]))
    if isinstance(result, dict):
        return result
    return {"answer": result}


@router.post("/background")
def chat_background(
    body: dict,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    question = str(body.get("content", "")).strip()
    chat_id = body.get("chat_id")
    if not question:
        raise HTTPException(status_code=400, detail="Nội dung tin nhắn trống")
    if not chat_id:
        raise HTTPException(status_code=400, detail="Thiếu chat_id")

    company_id = _require_company(user)
    chat = _get_owned_chat_or_404(db, chat_id, user)

    user_msg = {
        "chat_id": chat_id,
        "company_id": company_id,
        "user_id": str(user["_id"]),
        "role": "user",
        "content": question,
        "status": "completed",
        "created_at": datetime.utcnow(),
    }
    user_res = db.messages.insert_one(user_msg)
    user_msg["_id"] = str(user_res.inserted_id)

    job_id = str(uuid4())
    assistant_msg = {
        "chat_id": chat_id,
        "company_id": company_id,
        "user_id": str(user["_id"]),
        "role": "assistant",
        "content": "Đang tìm kiếm...",
        "source": None,
        "status": "processing",
        "job_id": job_id,
        "created_at": datetime.utcnow(),
    }
    assistant_res = db.messages.insert_one(assistant_msg)
    assistant_msg["_id"] = str(assistant_res.inserted_id)

    updated_title = _update_chat_title_if_needed(db, chat, question)

    background_tasks.add_task(
        _run_chat_job,
        chat_id,
        assistant_msg["_id"],
        question,
        company_id,
        str(user["_id"]),
    )

    return {
        "job_id": job_id,
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        "chat_title": updated_title,
    }


@router.post("/{message_id}/stop")
def stop_background_message(message_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    company_id = _require_company(user)

    try:
        object_id = ObjectId(message_id)
    except Exception:
        raise HTTPException(status_code=400, detail="message_id không hợp lệ")

    message = db.messages.find_one(
        {
            "_id": object_id,
            "company_id": company_id,
            "user_id": str(user["_id"]),
            "role": "assistant",
        }
    )
    if not message:
        raise HTTPException(status_code=404, detail="Không tìm thấy tin nhắn")

    if message.get("status") != "processing":
        return {"ok": True, "status": message.get("status")}

    db.messages.update_one(
        {"_id": object_id},
        {
            "$set": {
                "content": "Đã dừng tạo câu trả lời.",
                "status": "stopped",
                "completed_at": datetime.utcnow(),
            }
        },
    )
    return {"ok": True, "status": "stopped"}
