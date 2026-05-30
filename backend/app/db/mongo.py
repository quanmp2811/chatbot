# -*- coding: utf-8 -*-
from pymongo import ASCENDING, DESCENDING, MongoClient

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB_NAME = "tro_ly_ao_dn"

client = MongoClient(MONGO_URI)
db = client[MONGO_DB_NAME]
drive_files_collection = db["drive_files"]
vector_chunks_collection = db["vector_chunks"]


def ensure_indexes():
    """Create indexes used for tenant isolation and query safety."""
    index_specs = [
        # One drive file record per company/file pair.
        (db["drive_files"], [("company_id", ASCENDING), ("file_id", ASCENDING)], {"unique": True}),
        (db["drive_files"], [("company_id", ASCENDING)], {}),
        # Vector metadata lookup by FAISS position and file ownership.
        (db["vector_chunks"], [("vector_pos", ASCENDING)], {"unique": True}),
        (db["vector_chunks"], [("company_id", ASCENDING), ("file_id", ASCENDING), ("chunk_id", ASCENDING)], {}),
        (db["vector_chunks"], [("company_id", ASCENDING), ("file_id", ASCENDING)], {}),
        (db["vector_chunks"], [("company_id", ASCENDING)], {}),
        # Chat access pattern and ownership filtering.
        (db["chats"], [("user_id", ASCENDING), ("company_id", ASCENDING), ("created_at", DESCENDING)], {}),
        (db["messages"], [("chat_id", ASCENDING), ("created_at", DESCENDING)], {}),
        # Basic account lookup.
        (db["users"], [("email", ASCENDING)], {}),
        (db["companies"], [("approval_status", ASCENDING), ("created_at", DESCENDING)], {}),
        (db["companies"], [("is_blocked", ASCENDING), ("is_expired", ASCENDING)], {}),
        (db["companies"], [("created_by", ASCENDING)], {}),
        (db["company_plans"], [("id", ASCENDING)], {"unique": True}),
        (db["trial_eligibility"], [("email", ASCENDING)], {"unique": True}),
        (db["blocked_accounts"], [("email", ASCENDING)], {}),
        (db["blocked_accounts"], [("google_sub", ASCENDING)], {}),
    ]

    for collection, keys, options in index_specs:
        try:
            collection.create_index(keys, **options)
        except Exception as exc:
            # Do not crash app startup on pre-existing dirty data; keep service available.
            print(f"[mongo] index create failed on {collection.name} {keys}: {exc}")


def get_db():
    return db
