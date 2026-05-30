from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.mongo import drive_files_collection, vector_chunks_collection


def main():
    total_chunks = vector_chunks_collection.count_documents({})
    total_files = drive_files_collection.count_documents({"indexed": True})

    by_chunk_key = defaultdict(int)
    chunk_totals_by_file = defaultdict(int)

    cursor = vector_chunks_collection.find(
        {},
        {"company_id": 1, "file_id": 1, "chunk_id": 1},
    )
    for doc in cursor:
        company_id = str(doc.get("company_id") or "")
        file_id = str(doc.get("file_id") or "")
        chunk_id = int(doc.get("chunk_id") or 0)
        by_chunk_key[(company_id, file_id, chunk_id)] += 1
        chunk_totals_by_file[(company_id, file_id)] += 1

    duplicate_chunk_keys = [
        {
            "company_id": company_id,
            "file_id": file_id,
            "chunk_id": chunk_id,
            "copies": copies,
        }
        for (company_id, file_id, chunk_id), copies in by_chunk_key.items()
        if copies > 1
    ]
    duplicate_chunk_keys.sort(key=lambda item: item["copies"], reverse=True)

    heaviest_files = [
        {
            "company_id": company_id,
            "file_id": file_id,
            "chunk_count": chunk_count,
        }
        for (company_id, file_id), chunk_count in chunk_totals_by_file.items()
    ]
    heaviest_files.sort(key=lambda item: item["chunk_count"], reverse=True)

    print(f"indexed_files={total_files}")
    print(f"vector_chunks={total_chunks}")
    print(f"duplicate_chunk_keys={len(duplicate_chunk_keys)}")
    print("top_duplicate_chunk_keys:")
    for item in duplicate_chunk_keys[:20]:
        print(
            f"  company_id={item['company_id']} file_id={item['file_id']} "
            f"chunk_id={item['chunk_id']} copies={item['copies']}"
        )

    print("top_heaviest_files:")
    for item in heaviest_files[:20]:
        print(
            f"  company_id={item['company_id']} file_id={item['file_id']} "
            f"chunk_count={item['chunk_count']}"
        )


if __name__ == "__main__":
    main()
