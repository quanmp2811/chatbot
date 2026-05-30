from collections import Counter, defaultdict
from pathlib import Path
import argparse
import json
import sys

from bson import ObjectId

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.mongo import db, drive_files_collection, vector_chunks_collection
from app.services.ai.vector_service import delete_company_vectors, delete_file_vectors


def _existing_company_ids() -> set[str]:
    return {str(doc["_id"]) for doc in db.companies.find({}, {"_id": 1})}


def _existing_drive_file_keys() -> set[tuple[str, str]]:
    cursor = drive_files_collection.find({}, {"company_id": 1, "file_id": 1})
    return {
        (str(doc.get("company_id") or ""), str(doc.get("file_id") or ""))
        for doc in cursor
        if doc.get("company_id") and doc.get("file_id")
    }


def _vector_counts_by_company() -> Counter:
    counts: Counter = Counter()
    for doc in vector_chunks_collection.find({}, {"company_id": 1}):
        counts[str(doc.get("company_id") or "")] += 1
    return counts


def _vector_counts_by_file() -> dict[tuple[str, str], int]:
    counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    cursor = vector_chunks_collection.find({}, {"company_id": 1, "file_id": 1})
    for doc in cursor:
        company_id = str(doc.get("company_id") or "")
        file_id = str(doc.get("file_id") or "")
        if company_id and file_id:
            counts[(company_id, file_id)] += 1
    return dict(counts)


def _build_report() -> dict:
    company_ids = _existing_company_ids()
    drive_file_keys = _existing_drive_file_keys()
    vector_by_company = _vector_counts_by_company()
    vector_by_file = _vector_counts_by_file()

    orphan_companies = [
        {"company_id": company_id, "chunk_count": chunk_count}
        for company_id, chunk_count in sorted(vector_by_company.items())
        if company_id and company_id not in company_ids
    ]

    orphan_files = [
        {"company_id": company_id, "file_id": file_id, "chunk_count": chunk_count}
        for (company_id, file_id), chunk_count in sorted(vector_by_file.items())
        if company_id in company_ids and (company_id, file_id) not in drive_file_keys
    ]

    return {
        "companies_count": len(company_ids),
        "drive_files_count": drive_files_collection.count_documents({}),
        "vector_chunks_count": vector_chunks_collection.count_documents({}),
        "orphan_companies": orphan_companies,
        "orphan_files": orphan_files,
    }


def _apply_cleanup(report: dict) -> None:
    for item in report["orphan_companies"]:
        delete_company_vectors(item["company_id"])

    for item in report["orphan_files"]:
        delete_file_vectors(item["company_id"], item["file_id"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit and optionally clean orphan vector_chunks that no longer map to companies/drive_files."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete orphan vectors. Without this flag the script only reports findings.",
    )
    args = parser.parse_args()

    report = _build_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not args.apply:
        return

    _apply_cleanup(report)
    updated_report = _build_report()
    print(json.dumps(updated_report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
