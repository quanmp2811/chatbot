import os
import json
import re
import time
import unicodedata
import mimetypes
import tempfile
from pathlib import Path

import faiss
import numpy as np
import torch
from pymongo import UpdateOne

from app.db.mongo import drive_files_collection, vector_chunks_collection
from app.modules.documents.drive_service import download_drive_file
from app.services.chunking import split_text, split_text_with_headings
from app.services.file_parser import extract_text

MODEL_NAME = "BAAI/bge-m3"
DIMENSION = 1024

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
INDEX_FILE = os.path.join(DATA_DIR, "faiss_index.bin")
META_FILE = os.path.join(DATA_DIR, "vector_meta.json")

os.makedirs(DATA_DIR, exist_ok=True)

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _select_device() -> str:
    override = (os.getenv("RAG_EMBEDDING_DEVICE") or "cpu").strip().lower()
    if override in {"cpu", "cuda", "cuda:0"}:
        return override
    return "cpu"


EMBEDDING_DEVICE = _select_device()
EMBEDDING_BATCH_SIZE = _env_int(
    "RAG_EMBEDDING_BATCH_SIZE",
    64 if EMBEDDING_DEVICE.startswith("cuda") else 32,
)
CPU_THREADS = _env_int("RAG_CPU_THREADS", 0)
if CPU_THREADS > 0:
    torch.set_num_threads(CPU_THREADS)

if EMBEDDING_DEVICE.startswith("cuda"):
    enable_tf32 = (os.getenv("RAG_TF32") or "1").strip().lower() in {"1", "true", "yes", "on"}
    if enable_tf32:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    precision = (os.getenv("RAG_MATMUL_PRECISION") or "high").strip().lower()
    try:
        torch.set_float32_matmul_precision(precision)
    except Exception:
        pass

model = None
CURRENT_DEVICE = "cpu"
CURRENT_BATCH_SIZE = EMBEDDING_BATCH_SIZE


def _get_model():
    global model
    if model is None:
        from sentence_transformers import SentenceTransformer

        print(f"[vector] Loading embedding model: {MODEL_NAME}")
        model = SentenceTransformer(MODEL_NAME, device="cpu")
        model.eval()
    return model


def _set_model_device(device: str) -> None:
    global CURRENT_DEVICE
    current_model = _get_model()
    if device == CURRENT_DEVICE:
        return
    current_model.to(device)
    CURRENT_DEVICE = device


def _is_oom_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "out of memory" in message or "cuda error: out of memory" in message


def _encode_texts(texts, batch_size=None):
    global CURRENT_BATCH_SIZE
    current_model = _get_model()
    batch_size = int(batch_size or CURRENT_BATCH_SIZE or 1)
    try:
        if len(texts) > 0:
            batch_size = min(batch_size, len(texts))
    except Exception:
        pass
    while True:
        try:
            embeddings = current_model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=False,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            CURRENT_BATCH_SIZE = batch_size
            return embeddings
        except Exception as exc:
            if not _is_oom_error(exc):
                raise

            if CURRENT_DEVICE.startswith("cuda"):
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

                if batch_size > 1:
                    batch_size = max(1, batch_size // 2)
                    print(f"[vector] CUDA OOM, retrying with batch_size={batch_size}")
                    continue

                print("[vector] CUDA OOM at batch_size=1, falling back to CPU")
                _set_model_device("cpu")
                batch_size = min(32, EMBEDDING_BATCH_SIZE)
                continue

            raise


def _new_index():
    return faiss.IndexFlatIP(DIMENSION)


def _temp_extension_for_mime(mime_type: str, file_name: str | None = None) -> str:
    if file_name:
        _, ext = os.path.splitext(file_name)
        if ext:
            return ext

    export_ext_map = {
        "application/vnd.google-apps.document": ".docx",
        "application/vnd.google-apps.spreadsheet": ".xlsx",
        "application/vnd.google-apps.presentation": ".pptx",
        "application/vnd.google-apps.drawing": ".png",
    }
    if mime_type in export_ext_map:
        return export_ext_map[mime_type]

    return mimetypes.guess_extension(mime_type or "") or ".bin"


file_name_cache = {}

def _metadata_projection():
    return {
        "_id": 1,
        "vector_pos": 1,
        "company_id": 1,
        "file_id": 1,
        "file_name": 1,
        "text": 1,
        "search_text": 1,
        "section_text": 1,
        "chunk_id": 1,
        "position": 1,
        "page": 1,
        "section": 1,
        "heading": 1,
        "heading_path": 1,
        "section_id": 1,
        "chunk_in_section": 1,
    }


def _iter_all_metadata():
    return vector_chunks_collection.find({}, _metadata_projection()).sort("vector_pos", 1)


def _metadata_count() -> int:
    return vector_chunks_collection.count_documents({})


def _legacy_metadata_exists() -> bool:
    return os.path.exists(META_FILE) and os.path.getsize(META_FILE) > 0


def _ensure_metadata_store_ready():
    metadata_count = _metadata_count()
    if metadata_count > 0:
        return metadata_count

    if _legacy_metadata_exists():
        raise RuntimeError(
            "Legacy vector metadata was detected in backend/data/vector_meta.json. "
            "Run `python backend/scripts/migrate_vector_metadata.py` from the backend environment "
            "before using vector search or indexing."
        )

    return 0


def _iter_legacy_metadata():
    decoder = json.JSONDecoder()
    with open(META_FILE, "r", encoding="utf-8") as handle:
        buffer = ""
        eof = False
        started = False
        vector_pos = 0

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
                    raise ValueError(f"Expected JSON array in {META_FILE}")
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

            yield vector_pos, item
            vector_pos += 1
            buffer = buffer[end:]


def _cleanup_legacy_vectors(should_delete, reason: str) -> int:
    global index

    current_index = _get_index()
    if current_index.ntotal == 0:
        print(f"[vector] Legacy cleanup skipped after {reason}: empty index")
        return 0

    temp_meta = f"{META_FILE}.tmp"
    temp_index = f"{INDEX_FILE}.tmp"
    rebuilt = _new_index()
    kept_vectors = []
    deleted_count = 0
    kept_count = 0

    with open(temp_meta, "w", encoding="utf-8") as handle:
        handle.write("[")
        first_item = True

        for old_pos, item in _iter_legacy_metadata():
            if should_delete(item):
                deleted_count += 1
                continue

            vector = np.asarray(current_index.reconstruct(int(old_pos)), dtype="float32")
            kept_vectors.append(vector)
            if len(kept_vectors) >= 1024:
                rebuilt.add(np.vstack(kept_vectors))
                kept_vectors = []

            if not first_item:
                handle.write(",")
            json.dump(item, handle, ensure_ascii=False)
            first_item = False
            kept_count += 1

        handle.write("]")

    if kept_vectors:
        rebuilt.add(np.vstack(kept_vectors))

    faiss.write_index(rebuilt, temp_index)
    os.replace(temp_meta, META_FILE)
    os.replace(temp_index, INDEX_FILE)
    index = rebuilt
    print(f"[vector] Legacy cleanup after {reason}: deleted={deleted_count} kept={kept_count}")
    return deleted_count


def _load_metadata_by_positions(indices) -> dict[int, dict]:
    positions = sorted({int(idx) for idx in indices if idx is not None and int(idx) >= 0})
    if not positions:
        return {}

    docs = vector_chunks_collection.find(
        {"vector_pos": {"$in": positions}},
        _metadata_projection(),
    )
    return {int(doc["vector_pos"]): doc for doc in docs}


def _iter_company_metadata(company_id: str):
    return vector_chunks_collection.find(
        {"company_id": company_id},
        _metadata_projection(),
    ).sort("vector_pos", 1)


def _iter_company_file_metadata(company_id: str, file_id: str):
    return vector_chunks_collection.find(
        {"company_id": company_id, "file_id": file_id},
        _metadata_projection(),
    ).sort("vector_pos", 1)


def _metadata_exists_for_file(company_id: str, file_id: str) -> bool:
    return (
        vector_chunks_collection.find_one(
            {"company_id": company_id, "file_id": file_id},
            {"_id": 1},
        )
        is not None
    )


def _rebuild_index_from_metadata():
    rebuilt = _new_index()
    texts = [(item.get("search_text") or item.get("text") or "").strip() for item in _iter_all_metadata()]
    texts = [text for text in texts if text]

    if not texts:
        return rebuilt

    print(f"[vector] Rebuilding index from metadata with {len(texts)} chunks")
    embeddings = _encode_texts(texts, batch_size=EMBEDDING_BATCH_SIZE)
    vectors = np.array(embeddings).astype("float32")
    rebuilt.add(vectors)
    return rebuilt


index = None


def _get_index():
    global index
    if index is not None:
        return index

    if os.path.exists(INDEX_FILE):
        loaded_index = faiss.read_index(INDEX_FILE)
        if loaded_index.d != DIMENSION:
            print(
                f"[vector] Index dimension mismatch ({loaded_index.d} != {DIMENSION}), rebuilding index"
            )
            loaded_index = _rebuild_index_from_metadata()
            faiss.write_index(loaded_index, INDEX_FILE)
        index = loaded_index
        return index

    index = _rebuild_index_from_metadata() if _metadata_count() else _new_index()
    return index


def save_index():
    current_index = _get_index()
    faiss.write_index(current_index, INDEX_FILE)


def _similarity_score(raw_score: float) -> float:
    return max(0.0, min(1.0, (float(raw_score) + 1.0) / 2.0))


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _prepare_chunk_record(chunk, fallback_position: int, file_name: str | None = None) -> dict | None:
    file_label = _normalize_text(file_name or "")

    def with_file_context(text: str, heading_path: str | None = None) -> str:
        parts = []
        if file_label:
            parts.append(file_label)
        if heading_path:
            normalized_heading = _normalize_text(heading_path)
            if normalized_heading and normalized_heading != file_label:
                parts.append(normalized_heading)
        parts.append(text)
        return "\n".join([part for part in parts if part]).strip()

    if isinstance(chunk, dict):
        text = _normalize_text(chunk.get("text", ""))
        if not text:
            return None
        section_text = _normalize_text(chunk.get("section_text") or chunk.get("text") or "")
        heading_path = chunk.get("heading_path")
        return {
            "text": text,
            "search_text": with_file_context(text, heading_path),
            "section_text": section_text or text,
            "heading": chunk.get("heading"),
            "heading_path": heading_path,
            "section_id": chunk.get("section_id"),
            "chunk_in_section": chunk.get("chunk_in_section"),
            "position": chunk.get("position", fallback_position),
        }

    text = _normalize_text(chunk)
    if not text:
        return None
    return {
        "text": text,
        "search_text": with_file_context(text),
        "section_text": text,
        "heading": None,
        "heading_path": None,
        "section_id": fallback_position,
        "chunk_in_section": 0,
        "position": fallback_position,
    }


def _rebuild_all_vectors(metadata_docs, reason: str) -> None:
    current_index = _get_index()
    current_index.reset()

    metadata_docs = list(metadata_docs)
    if not metadata_docs:
        save_index()
        print(f"[vector] Reset index after {reason}")
        return

    texts = [(doc.get("search_text") or doc.get("text") or "").strip() for doc in metadata_docs]
    texts = [text for text in texts if text]
    print(f"[vector] Rebuilding index with {len(texts)} chunks after {reason}")
    embeddings = _encode_texts(texts, batch_size=EMBEDDING_BATCH_SIZE)
    vectors = np.array(embeddings).astype("float32")
    current_index.add(vectors)

    operations = []
    for new_pos, doc in enumerate(metadata_docs):
        operations.append(
            UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {"vector_pos": new_pos}},
            )
        )

    if operations:
        vector_chunks_collection.bulk_write(operations, ordered=False)

    save_index()


def add_vectors(company_id, file_id, chunks, file_name=None):
    _ensure_metadata_store_ready()
    if not chunks:
        print(f"[vector] Skip add_vectors: no chunks for file_id={file_id}")
        return

    if _metadata_exists_for_file(company_id, file_id):
        print(f"[vector] Existing vectors found for file_id={file_id}, replacing before add")
        delete_file_vectors(company_id, file_id)

    t0 = time.perf_counter()
    print(f"[vector] Encoding {len(chunks)} chunks for file_id={file_id}")
    prepared_chunks = []
    for i, chunk in enumerate(chunks):
        prepared = _prepare_chunk_record(chunk, i, file_name=file_name)
        if prepared:
            prepared_chunks.append(prepared)

    if not prepared_chunks:
        print(f"[vector] Skip add_vectors: empty normalized chunks for file_id={file_id}")
        return

    embeddings = _encode_texts([item["search_text"] for item in prepared_chunks], batch_size=EMBEDDING_BATCH_SIZE)
    vectors = np.array(embeddings).astype("float32")

    current_index = _get_index()
    start_pos = current_index.ntotal
    current_index.add(vectors)

    docs = []
    for i, chunk in enumerate(prepared_chunks):
        docs.append(
            {
                "vector_pos": start_pos + i,
                "company_id": company_id,
                "file_id": file_id,
                "file_name": file_name,
                "text": chunk["text"],
                "search_text": chunk["search_text"],
                "section_text": chunk["section_text"],
                "chunk_id": i,
                "position": chunk["position"],
                "page": None,  # TODO: add page info if available
                "section": chunk.get("heading_path") or chunk.get("heading"),
                "heading": chunk.get("heading"),
                "heading_path": chunk.get("heading_path"),
                "section_id": chunk.get("section_id"),
                "chunk_in_section": chunk.get("chunk_in_section"),
            }
        )

    if docs:
        vector_chunks_collection.insert_many(docs, ordered=True)

    save_index()
    print(f"[vector] Added {len(chunks)} vectors in {time.perf_counter() - t0:.2f}s")


def search_vectors(company_id, query, top_k=8):
    _ensure_metadata_store_ready()
    current_index = _get_index()
    if current_index.ntotal == 0:
        return []

    query = _normalize_text(query)

    query_vector = _encode_texts([f"query: {query}"], batch_size=1)
    query_vector = np.array(query_vector).astype("float32")

    distances, indices = current_index.search(query_vector, top_k * 50)
    metadata_by_pos = _load_metadata_by_positions(indices[0])

    results = []

    for idx in indices[0]:
        meta = metadata_by_pos.get(int(idx))
        if not meta:
            continue

        if meta.get("company_id") != company_id:
            continue

        text = meta.get("text")

        if text:
            results.append(text)

        if len(results) >= top_k:
            break

    return results


def search_vectors_with_sources(company_id, query, top_k=5):
    _ensure_metadata_store_ready()
    current_index = _get_index()
    if current_index.ntotal == 0:
        return []

    query = _normalize_text(query)
    query_vector = _encode_texts([f"query: {query}"], batch_size=1)
    query_vector = np.array(query_vector).astype("float32")

    def collect(search_k):
        distances, indices = current_index.search(query_vector, search_k)
        metadata_by_pos = _load_metadata_by_positions(indices[0])
        results = []

        for distance, idx in zip(distances[0], indices[0]):
            meta = metadata_by_pos.get(int(idx))
            if not meta:
                continue
            if meta.get("company_id") != company_id:
                continue

            chunk_text = meta.get("text")
            section_text = meta.get("section_text") or chunk_text
            if not chunk_text or len(chunk_text) < 10:
                continue

            results.append(
                {
                    "text": chunk_text,
                    "chunk_text": chunk_text,
                    "section_text": section_text,
                    "file_id": meta.get("file_id"),
                    "file_name": _file_label(meta),
                    "chunk_id": meta.get("chunk_id"),
                    "section_id": meta.get("section_id"),
                    "heading": meta.get("heading"),
                    "heading_path": meta.get("heading_path"),
                    "score": _similarity_score(distance),
                }
            )
            if len(results) >= top_k:
                break

        return results

    candidate_k = min(current_index.ntotal, max(top_k * 20, 200))
    results = collect(candidate_k)

    if len(results) < top_k and candidate_k < current_index.ntotal:
        results = collect(current_index.ntotal)

    return results


def _tokenize(text: str) -> set[str]:
    normalized = _strip_accents((text or "").lower())
    return set(re.findall(r"\w+", normalized))


def _keyword_overlap_score(query: str, text: str) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0

    t_tokens = _tokenize(text)
    if not t_tokens:
        return 0.0

    return len(q_tokens & t_tokens) / len(q_tokens)


def _phrase_match_score(query: str, text: str) -> float:
    normalized_query = _normalize_text(_strip_accents(query))
    normalized_text = _normalize_text(_strip_accents(text))
    if not normalized_query or not normalized_text:
        return 0.0

    if normalized_query in normalized_text:
        return 1.0

    query_tokens = normalized_query.split()
    if len(query_tokens) < 2:
        return 0.0

    longest_run = 0
    current_run = 0
    for token in query_tokens:
        if token in normalized_text:
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0

    return longest_run / len(query_tokens)


def _lexical_hits(company_id, query, top_k=8):
    _ensure_metadata_store_ready()
    hits = []
    query_plain = _normalize_text(_strip_accents(query))

    for meta in _iter_company_metadata(company_id):
        chunk_text = (meta.get("text") or "").strip()
        section_text = (meta.get("section_text") or chunk_text).strip()
        if len(chunk_text) < 10:
            continue

        lexical_score = _keyword_overlap_score(query, chunk_text)
        phrase_score = _phrase_match_score(query, chunk_text)
        if lexical_score <= 0 and phrase_score <= 0:
            continue

        text_plain = _normalize_text(_strip_accents(chunk_text))
        final_score = (lexical_score * 0.55) + (phrase_score * 0.45)
        if query_plain and query_plain in text_plain:
            final_score += 0.15

        hits.append(
            {
                "text": chunk_text,
                "chunk_text": chunk_text,
                "section_text": section_text,
                "file_id": meta.get("file_id"),
                "file_name": _file_label(meta),
                "chunk_id": meta.get("chunk_id"),
                "section_id": meta.get("section_id"),
                "heading": meta.get("heading"),
                "heading_path": meta.get("heading_path"),
                "score": max(lexical_score, phrase_score),
                "semantic_score": phrase_score * 0.35,
                "lexical_score": lexical_score,
                "phrase_score": phrase_score,
                "final_score": final_score,
            }
        )

    hits.sort(key=lambda x: x["final_score"], reverse=True)

    dedup = []
    seen = set()
    for item in hits:
        dedupe_key = _normalize_text(item.get("text", ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        dedup.append(item)
        if len(dedup) >= top_k:
            break

    return dedup


def search_vectors_hybrid(company_id, query, top_k=8, overfetch_factor=10):
    fetch_k = max(top_k, top_k * max(1, overfetch_factor))
    semantic_hits = search_vectors_with_sources(company_id, query, top_k=fetch_k)
    lexical_hits = _lexical_hits(company_id, query, top_k=fetch_k)
    if not semantic_hits and not lexical_hits:
        return []

    ranked_by_text = {}

    for hit in semantic_hits:
        semantic_score = float(hit.get("score") or 0.0)
        lexical_score = _keyword_overlap_score(query, hit.get("text", ""))
        phrase_score = _phrase_match_score(query, hit.get("text", ""))
        final_score = (semantic_score * 0.7) + (lexical_score * 0.3) + (phrase_score * 0.15)
        ranked_by_text[_normalize_text(hit.get("text", ""))] = {
            **hit,
            "semantic_score": semantic_score,
            "lexical_score": lexical_score,
            "phrase_score": phrase_score,
            "final_score": final_score,
        }

    for hit in lexical_hits:
        key = _normalize_text(hit.get("text", ""))
        existing = ranked_by_text.get(key)
        if not existing:
            ranked_by_text[key] = hit
            continue

        existing["score"] = max(float(existing.get("score") or 0.0), float(hit.get("score") or 0.0))
        existing["semantic_score"] = max(
            float(existing.get("semantic_score") or 0.0),
            float(hit.get("semantic_score") or 0.0),
        )
        existing["lexical_score"] = max(
            float(existing.get("lexical_score") or 0.0),
            float(hit.get("lexical_score") or 0.0),
        )
        existing["phrase_score"] = max(
            float(existing.get("phrase_score") or 0.0),
            float(hit.get("phrase_score") or 0.0),
        )
        existing["final_score"] = max(
            float(existing.get("final_score") or 0.0),
            float(hit.get("final_score") or 0.0),
        )

    ranked = list(ranked_by_text.values())

    for item in ranked:
        text = item.get("text", "")
        coverage = _keyword_overlap_score(query, text)
        phrase = _phrase_match_score(query, text)
        item["final_score"] += coverage * 0.1
        item["final_score"] += phrase * 0.2

    ranked.sort(key=lambda x: x["final_score"], reverse=True)

    dedup = []
    seen = set()
    for item in ranked:
        text = item.get("text", "")
        if text in seen:
            continue
        seen.add(text)
        dedup.append(item)
        if len(dedup) >= top_k:
            break

    return dedup


def search_global_chunks(company_id, query, top_k=12, overfetch_factor=10):
    fetch_k = max(top_k, top_k * max(1, overfetch_factor))
    return search_vectors_hybrid(company_id, query, top_k=fetch_k, overfetch_factor=overfetch_factor)


def rank_files_from_chunks(company_id, query, hits, top_k=5):
    if not hits:
        return []

    query_normalized = _normalize_text(query)
    query_tokens = _tokenize(query)
    ranked = {}

    for position, hit in enumerate(hits):
        file_id = hit.get("file_id")
        if not file_id:
            continue

        file_name = hit.get("file_name") or file_id
        file_name_normalized = _normalize_text(file_name)
        semantic_score = float(hit.get("semantic_score") or hit.get("score") or 0.0)
        lexical_score = float(hit.get("lexical_score") or _keyword_overlap_score(query, hit.get("text", "")))
        phrase_score = float(hit.get("phrase_score") or _phrase_match_score(query, hit.get("text", "")))
        final_score = float(
            hit.get("final_score")
            or ((semantic_score * 0.65) + (lexical_score * 0.2) + (phrase_score * 0.15))
        )

        item = ranked.setdefault(
            file_id,
            {
                "file_id": file_id,
                "file_name": file_name,
                "chunk_hits": 0,
                "best_chunk_score": 0.0,
                "total_chunk_score": 0.0,
                "best_position": position,
                "name_score": 0.0,
                "query_coverage": 0.0,
                "phrase_score": 0.0,
            },
        )

        item["chunk_hits"] += 1
        item["best_chunk_score"] = max(item["best_chunk_score"], final_score)
        item["total_chunk_score"] += final_score
        item["best_position"] = min(item["best_position"], position)
        item["query_coverage"] = max(item["query_coverage"], lexical_score)
        item["phrase_score"] = max(item["phrase_score"], phrase_score)

        if file_name_normalized == query_normalized:
            item["name_score"] = max(item["name_score"], 1.0)
        elif query_normalized and query_normalized in file_name_normalized:
            item["name_score"] = max(item["name_score"], 0.8)
        elif query_tokens:
            overlap = len(query_tokens & _tokenize(file_name))
            item["name_score"] = max(item["name_score"], overlap / len(query_tokens))

    candidates = []
    for item in ranked.values():
        file_score = (
            (item["best_chunk_score"] * 0.48)
            + (min(item["chunk_hits"], 5) * 0.08)
            + (item["query_coverage"] * 0.16)
            + (item["phrase_score"] * 0.12)
            + (item["name_score"] * 0.16)
        )
        candidates.append(
            {
                **item,
                "file_score": file_score,
            }
        )

    candidates.sort(key=lambda x: (-x["file_score"], x["best_position"], x["file_name"]))
    return candidates[:top_k]


def _file_label(meta):
    file_name = meta.get("file_name")
    if file_name:
        return file_name

    company_id = meta.get("company_id")
    file_id = meta.get("file_id")

    if not company_id or not file_id:
        return "Tài liệu không tên"

    cache = file_name_cache.setdefault(company_id, {})
    if file_id in cache:
        resolved = cache[file_id]
    else:
        doc = drive_files_collection.find_one(
            {"company_id": company_id, "file_id": file_id},
            {"file_name": 1},
        )
        resolved = (doc or {}).get("file_name")
        cache[file_id] = resolved

    if resolved:
        meta["file_name"] = resolved
        return resolved

    return "Tài liệu không tên"


def get_indexed_files(company_id: str) -> list[dict]:
    _ensure_metadata_store_ready()
    files = []
    seen = set()
    cursor = drive_files_collection.find(
        {"company_id": company_id, "indexed": True},
        {"file_id": 1, "file_name": 1},
    )
    for doc in cursor:
        file_id = doc.get("file_id")
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        files.append(
            {
                "file_id": file_id,
                "file_name": doc.get("file_name") or file_id,
            }
        )
    return files


def has_file_vectors(company_id: str, file_id: str) -> bool:
    _ensure_metadata_store_ready()
    return _metadata_exists_for_file(company_id, file_id)


def search_and_chunk_entire_files(company, query, top_k=5):
    """
    Tìm kiếm theo nguyên tắc: tìm chunk toàn bộ tài liệu -> tìm và chọn file chứa nội dung 
    và chunk lại toàn bộ file đấy và liệt kê đầy đủ dữ liệu liên quan đến câu hỏi.
    
    1. Tìm chunks liên quan đến query.
    2. Lấy chunk phù hợp nhất -> mở file chứa chunk đó.
    3. Chunk lại toàn bộ file và tìm tất cả chunks liên quan đến query.
    4. Trả về danh sách chunks (sorted theo relevance).
    """
    company_id = str(company.get("_id") or company.get("company_id") or "")
    if not company_id:
        return []
    
    # Bước 1: Tìm chunks liên quan
    hits = search_global_chunks(company_id, query, top_k=20)
    if not hits:
        return []
    
    # Bước 2: Lấy chunk phù hợp nhất và file tương ứng
    best_hit = hits[0]
    file_id = best_hit.get("file_id")
    if not file_id:
        return []

    file_doc = drive_files_collection.find_one(
        {"company_id": company_id, "file_id": file_id},
        {"file_name": 1, "mime_type": 1},
    )
    if not file_doc:
        return []

    mime_type = file_doc.get("mime_type")
    file_name = file_doc.get("file_name") or file_id

    # Bước 3: Download, chunk lại toàn bộ file, rồi tìm chunks liên quan
    try:
        content = download_drive_file(company, file_id, mime_type)
        if not content:
            return []

        suffix = _temp_extension_for_mime(mime_type, file_name)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name

            text_content = extract_text(temp_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

        if not text_content or not text_content.strip():
            return []

        chunks = split_text_with_headings(text_content)
        if not chunks:
            return []

        query_norm = _normalize_text(query)
        query_vector = _encode_texts([f"query: {query_norm}"], batch_size=1)
        query_vector = np.array(query_vector).astype("float32")[0]

        chunk_texts = [c for c in chunks if c and str(c.get("text", "")).strip()]
        if not chunk_texts:
            return []

        embedded = _encode_texts(
            [f"passage: {_normalize_text(c.get('text', ''))}" for c in chunk_texts],
            batch_size=EMBEDDING_BATCH_SIZE,
        )
        embedded = np.array(embedded).astype("float32")
        semantic_scores = embedded @ query_vector

        ranked_chunks = []
        for chunk, semantic_score in zip(chunk_texts, semantic_scores):
            section_text = chunk.get("section_text") or chunk.get("text") or ""
            lexical_score = _keyword_overlap_score(query, section_text)
            phrase_score = _phrase_match_score(query, section_text)
            final_score = (semantic_score * 0.65) + (lexical_score * 0.2) + (phrase_score * 0.15)

            ranked_chunks.append(
                {
                    "text": section_text,
                    "file_id": file_id,
                    "file_name": file_name,
                    "company_id": company_id,
                    "mime_type": mime_type,
                    "source": "entire_file_chunk",
                    "heading": chunk.get("heading"),
                    "heading_path": chunk.get("heading_path"),
                    "section_id": chunk.get("section_id"),
                    "semantic_score": float(semantic_score),
                    "lexical_score": float(lexical_score),
                    "phrase_score": float(phrase_score),
                    "final_score": float(final_score),
                }
            )

        # Giữ nguyên thứ tự trong file (theo thứ tự chunk được tạo)
        return ranked_chunks

    except Exception as e:
        print(f"[vector] Error processing file {file_id}: {e}")
        return []


def get_file_scores(company_id, query, top_k=5):
    _ensure_metadata_store_ready()
    current_index = _get_index()
    if current_index.ntotal == 0:
        return []

    query = _normalize_text(query)
    query_vector = _encode_texts([f"query: {query}"], batch_size=1)
    query_vector = np.array(query_vector).astype("float32")

    distances, indices = current_index.search(query_vector, current_index.ntotal)
    metadata_by_pos = _load_metadata_by_positions(indices[0])

    scores_by_file = {}
    labels_by_file = {}

    for distance, idx in zip(distances[0], indices[0]):
        meta = metadata_by_pos.get(int(idx))
        if not meta:
            continue
        if meta.get("company_id") != company_id:
            continue

        file_id = meta.get("file_id")
        if not file_id:
            continue

        label = _file_label(meta)
        score = _similarity_score(distance)

        if file_id not in scores_by_file or score > scores_by_file[file_id]:
            scores_by_file[file_id] = score
            labels_by_file[file_id] = label

    ranked_ids = sorted(scores_by_file.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [(labels_by_file[file_id], score) for file_id, score in ranked_ids]


def search_within_file(company_id, file_name, query, top_k=5):
    _ensure_metadata_store_ready()
    current_index = _get_index()
    if current_index.ntotal == 0:
        return []

    query = _normalize_text(query)
    query_vector = _encode_texts([f"query: {query}"], batch_size=1)
    query_vector = np.array(query_vector).astype("float32")

    distances, indices = current_index.search(query_vector, current_index.ntotal)
    metadata_by_pos = _load_metadata_by_positions(indices[0])

    chunks = []
    for idx in indices[0]:
        meta = metadata_by_pos.get(int(idx))
        if not meta:
            continue
        if meta.get("company_id") != company_id:
            continue

        label = _file_label(meta)
        if file_name not in (label, meta.get("file_id")):
            continue

        text = meta.get("text")
        if text:
            chunks.append(text)
            if len(chunks) >= top_k:
                break

    return chunks


def search_within_file_with_sources(company_id, file_name, query, top_k=5):
    _ensure_metadata_store_ready()
    current_index = _get_index()
    if current_index.ntotal == 0:
        return []

    query = _normalize_text(query)
    query_vector = _encode_texts([f"query: {query}"], batch_size=1)
    query_vector = np.array(query_vector).astype("float32")

    distances, indices = current_index.search(query_vector, current_index.ntotal)
    metadata_by_pos = _load_metadata_by_positions(indices[0])

    results = []
    seen = set()

    for distance, idx in zip(distances[0], indices[0]):
        meta = metadata_by_pos.get(int(idx))
        if not meta:
            continue
        if meta.get("company_id") != company_id:
            continue

        label = _file_label(meta)
        if file_name not in (label, meta.get("file_id")):
            continue

        chunk_text = (meta.get("text") or "").strip()
        section_text = (meta.get("section_text") or chunk_text).strip()
        if not chunk_text:
            continue

        dedupe_key = _normalize_text(chunk_text)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        semantic_score = _similarity_score(distance)
        lexical_score = _keyword_overlap_score(query, chunk_text)
        phrase_score = _phrase_match_score(query, chunk_text)
        results.append(
            {
                "text": chunk_text,
                "chunk_text": chunk_text,
                "section_text": section_text,
                "file_id": meta.get("file_id"),
                "file_name": label,
                "chunk_id": meta.get("chunk_id"),
                "section_id": meta.get("section_id"),
                "heading": meta.get("heading"),
                "heading_path": meta.get("heading_path"),
                "score": semantic_score,
                "semantic_score": semantic_score,
                "lexical_score": lexical_score,
                "phrase_score": phrase_score,
                "final_score": (semantic_score * 0.65) + (lexical_score * 0.2) + (phrase_score * 0.15),
            }
        )

    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results[:top_k]


def get_all_chunks_in_file(company_id, file_name):
    _ensure_metadata_store_ready()
    chunks = []

    for meta in _iter_company_metadata(company_id):
        label = _file_label(meta)
        if file_name not in (label, meta.get("file_id")):
            continue

        text = meta.get("text")
        if text:
            chunks.append(text)

    return chunks


def delete_file_vectors(company_id, file_id):
    t0 = time.perf_counter()
    if _metadata_count() == 0 and _legacy_metadata_exists():
        deleted_count = _cleanup_legacy_vectors(
            lambda meta: meta.get("company_id") == company_id and meta.get("file_id") == file_id,
            f"deleting file_id={file_id}",
        )
        if deleted_count == 0:
            print(f"[vector] No legacy vectors found for file_id={file_id}")
            return
        print(f"[vector] Legacy file delete done in {time.perf_counter() - t0:.2f}s")
        return

    _ensure_metadata_store_ready()
    has_target = _metadata_exists_for_file(company_id, file_id)
    if not has_target:
        print(f"[vector] No vectors found for file_id={file_id}")
        return

    vector_chunks_collection.delete_many({"company_id": company_id, "file_id": file_id})
    remaining_metadata = list(_iter_all_metadata())
    _rebuild_all_vectors(remaining_metadata, f"deleting file_id={file_id}")
    print(f"[vector] Rebuild done in {time.perf_counter() - t0:.2f}s")


def delete_company_vectors(company_id):
    t0 = time.perf_counter()
    if _metadata_count() == 0 and _legacy_metadata_exists():
        deleted_count = _cleanup_legacy_vectors(
            lambda meta: meta.get("company_id") == company_id,
            f"deleting company_id={company_id}",
        )
        if deleted_count == 0:
            print(f"[vector] No legacy vectors found for company_id={company_id}")
            return
        file_name_cache.pop(company_id, None)
        print(f"[vector] Legacy company delete done in {time.perf_counter() - t0:.2f}s")
        return

    _ensure_metadata_store_ready()
    has_target = vector_chunks_collection.find_one({"company_id": company_id}, {"_id": 1})
    if not has_target:
        print(f"[vector] No vectors found for company_id={company_id}")
        return

    vector_chunks_collection.delete_many({"company_id": company_id})
    remaining_metadata = list(_iter_all_metadata())
    _rebuild_all_vectors(remaining_metadata, f"deleting company_id={company_id}")
    file_name_cache.pop(company_id, None)
    print(f"[vector] Company delete rebuild done in {time.perf_counter() - t0:.2f}s")
