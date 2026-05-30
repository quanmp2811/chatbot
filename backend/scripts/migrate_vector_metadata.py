import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.mongo import vector_chunks_collection


BATCH_SIZE = 1000
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
META_FILE = DATA_DIR / "vector_meta.json"


def iter_json_array(path: Path):
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as handle:
        buffer = ""
        eof = False
        started = False

        while True:
            if not eof and len(buffer) < 1_000_000:
                chunk = handle.read(1_000_000)
                if chunk:
                    buffer += chunk
                else:
                    eof = True

            stripped = buffer.lstrip()
            if not stripped:
                if eof:
                    break
                buffer = stripped
                continue

            buffer = stripped
            if not started:
                if not buffer.startswith("["):
                    raise ValueError("Expected JSON array in vector_meta.json")
                buffer = buffer[1:]
                started = True
                continue

            buffer = buffer.lstrip()
            if buffer.startswith("]"):
                break
            if buffer.startswith(","):
                buffer = buffer[1:]
                continue

            try:
                item, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if eof:
                    raise
                continue

            yield item
            buffer = buffer[end:]


def main():
    if not META_FILE.exists():
        raise FileNotFoundError(f"Missing metadata file: {META_FILE}")

    existing = vector_chunks_collection.count_documents({})
    if existing:
        raise RuntimeError(
            "vector_chunks collection is not empty. Clear it first if you want to migrate from legacy JSON."
        )

    batch = []
    inserted = 0

    for vector_pos, item in enumerate(iter_json_array(META_FILE)):
        item["vector_pos"] = vector_pos
        batch.append(item)

        if len(batch) >= BATCH_SIZE:
            vector_chunks_collection.insert_many(batch, ordered=True)
            inserted += len(batch)
            print(f"migrated={inserted}")
            batch = []

    if batch:
        vector_chunks_collection.insert_many(batch, ordered=True)
        inserted += len(batch)

    print(f"done migrated={inserted}")


if __name__ == "__main__":
    main()
