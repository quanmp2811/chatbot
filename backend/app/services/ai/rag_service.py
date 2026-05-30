import json
import os
import re
import tempfile
import mimetypes
from glob import glob

from app.services.ai.vector_service import (
    get_all_chunks_in_file,
    get_indexed_files,
    rank_files_from_chunks,
    search_and_chunk_entire_files,
    search_global_chunks,
    search_vectors_with_sources,
    search_within_file,
    search_within_file_with_sources,
)
from app.db.mongo import drive_files_collection
from app.modules.documents.drive_service import download_drive_file
from app.services.file_parser import extract_text
from app.services.ai.llm_service import (
    _is_ollama_error,
    auto_markdown,
    clean_text,
    generate_answer,
    generate_edit_markdown,
    generate_final_answer,
    generate_preserve_markdown,
    _preserve_grounded_answer,
    keep_verbatim,
    remove_non_vietnamese,
    remove_summary,
)
from app.services.ai.conversation_memory import (
    get_context,
    get_current_file,
    get_current_topic,
    get_last_intent,
    get_last_question,
    next_file,
    save_context,
    update_context,
)
from app.services.ai.rag_cache import add_cache, get_cached_answer
from app.services.chunking import split_text, split_text_with_headings
from app.services.context_file_detector import get_file_from_last_answer

NEGATIVE_FEEDBACK = {
    "không phải",
    "khong phai",
    "ko phải",
    "ko phai",
    "sai rồi",
    "sai roi",
    "không đúng",
    "khong dung",
}

SWITCH_FILE_KEYWORDS = {
    "đổi file",
    "doi file",
    "file khác",
    "file khac",
    "chuyển file",
    "chuyen file",
    "sang file khác",
    "sang file khac",
    "tài liệu khác",
    "tai lieu khac",
    "tìm tất cả file",
    "tim tat ca file",
    "tất cả file",
    "tat ca file",
}

FOLLOW_UP_KEYWORDS = {
    "còn nữa",
    "con nua",
    "tiếp",
    "tiep",
    "tiếp đi",
    "tiep di",
    "nói rõ hơn",
    "noi ro hon",
    "chi tiết hơn",
    "chi tiet hon",
    "ý đầu",
    "y dau",
    "phần trên",
    "phan tren",
    "file đó",
    "file do",
    "tài liệu đó",
    "tai lieu do",
}

FILE_LOOKUP_PREFIXES = (
    "tim file",
    "tìm file",
    "mo file",
    "mở file",
    "tim tai lieu",
    "tìm tài liệu",
    "mo tai lieu",
    "mở tài liệu",
)

USE_OLLAMA_ANALYSIS = True
USE_OLLAMA_FORMAT = False
OLLAMA_ANALYSIS_MIN_QUESTION_LEN = 24
OLLAMA_ANALYSIS_LONG_QUESTION_LEN = 120

USE_RAG_CACHE = (os.getenv("RAG_USE_CACHE") or "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

GREETING_EXACT_MATCHES = {
    "hi",
    "hello",
    "helo",
    "hey",
    "alo",
    "xin chao",
    "xin chào",
    "chao",
    "chào",
    "chao ban",
    "chào bạn",
    "ban oi",
    "bạn ơi",
    "ban la ai",
    "bạn là ai",
    "gioi thieu ban than",
    "giới thiệu bản thân",
}

GREETING_PREFIXES = (
    "xin chao",
    "xin chào",
    "chao ",
    "chào ",
    "hello ",
    "hi ",
    "hey ",
    "alo ",
)

GREETING_AUTO_REPLY = (
    "Chào bạn tôi là trợ lý ảo doanh nghiệp, bạn cần tìm dữ liệu gì hãy đặt câu hỏi cho tôi."
)



def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _strip_accents(text: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _normalize_search_text(text: str) -> str:
    cleaned = _strip_accents((text or "").lower())
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return " ".join(cleaned.split())


def _compact_search_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _strip_accents((text or "").lower()))


def _is_greeting_message(question: str) -> bool:
    normalized = _normalize_search_text(question)
    if not normalized:
        return False

    if normalized in GREETING_EXACT_MATCHES:
        return True

    return any(normalized.startswith(prefix) for prefix in GREETING_PREFIXES)


def _is_verbatim_request(question: str) -> bool:
    normalized = _normalize_search_text(question)
    if not normalized:
        return False

    keywords = [
        "noi dung",
        "nguyen van",
        "trich nguyen van",
        "trich van",
        "y nguyen",
        "y het",
        "dung y",
        "giu nguyen",
        "khong duoc sua",
        "khong duoc chinh sua",
        "khong sua",
    ]

    return any(keyword in normalized for keyword in keywords)


def _is_document_title_lookup(question: str, file_name: str | None = None, full_text: str | None = None) -> bool:
    normalized_question = _normalize_search_text(question)
    if not normalized_question:
        return False

    if any(marker in normalized_question for marker in ("?", "la gi", "nghia la", "bao nhieu", "khi nao", "the nao")):
        return False

    candidates = []
    if file_name:
        candidates.append(_normalize_search_text(file_name))
    if full_text:
        head = " ".join((full_text or "").splitlines()[:4])
        candidates.append(_normalize_search_text(head))

    compact_question = _compact_search_text(normalized_question)
    for candidate in candidates:
        if not candidate:
            continue
        compact_candidate = _compact_search_text(candidate)
        if not compact_candidate:
            continue

        if compact_question in compact_candidate or compact_candidate in compact_question:
            return True

        overlap = _keyword_overlap_score(normalized_question, candidate)
        phrase = _phrase_match_score(normalized_question, candidate)
        if overlap >= 0.8 or phrase >= 0.8:
            return True

    return False


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


def _load_verbatim_chunks_from_drive(
    company: dict,
    company_id: str,
    file_ref: str,
    best_hit: dict | None = None,
) -> tuple[list[str], str | None, str | None]:
    if not company:
        return [], None, None

    file_doc = drive_files_collection.find_one(
        {"company_id": company_id, "$or": [{"file_id": file_ref}, {"file_name": file_ref}]},
        {"file_id": 1, "file_name": 1, "mime_type": 1},
    )
    if not file_doc and best_hit:
        hit_file_id = best_hit.get("file_id")
        if hit_file_id:
            file_doc = drive_files_collection.find_one(
                {"company_id": company_id, "file_id": hit_file_id},
                {"file_id": 1, "file_name": 1, "mime_type": 1},
            )

    if not file_doc:
        return [], None, None

    file_id = file_doc.get("file_id") or file_ref
    file_name = file_doc.get("file_name") or file_ref
    mime_type = file_doc.get("mime_type")
    if not mime_type:
        return [], file_id, file_name

    content = download_drive_file(company, file_id, mime_type)
    if not content:
        return [], file_id, file_name

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
        return [], file_id, file_name

    sections = split_text_with_headings(text_content)
    if not sections:
        return [], file_id, file_name

    seen = set()
    chunks = []
    for item in sections:
        section_text = (item.get("section_text") or item.get("text") or "").strip()
        key = _normalize(section_text)
        if not section_text or key in seen:
            continue
        seen.add(key)
        chunks.append(section_text)

    return chunks, file_id, file_name

def _has_multiple_question_intents(question: str) -> bool:
    normalized = _normalize_search_text(question)
    if not normalized:
        return False

    multi_intent_markers = (
        " va ",
        " hoac ",
        " dong thoi ",
        " kem theo ",
        " bao gom ",
        " gom ",
        " thu nhat ",
        " thu hai ",
        " tiep theo ",
    )

    padded = f" {normalized} "
    if any(marker in padded for marker in multi_intent_markers):
        return True

    if (question or "").count("?") >= 2:
        return True

    lines = [line.strip() for line in (question or "").splitlines() if line.strip()]
    if len(lines) >= 2:
        return True

    comma_parts = [part.strip() for part in re.split(r"[;,]", question or "") if part.strip()]
    return len(comma_parts) >= 3


def _should_use_ollama_analysis(question: str) -> bool:
    question = (question or "").strip()
    if not question:
        return False

    if len(question) < OLLAMA_ANALYSIS_MIN_QUESTION_LEN:
        return False

    if "\n" in question:
        return True

    if len(question) >= OLLAMA_ANALYSIS_LONG_QUESTION_LEN:
        return True

    return _has_multiple_question_intents(question)


def _extract_explicit_file_ref(question: str) -> tuple[str | None, str]:
    raw = (question or "").strip()
    if not raw:
        return None, raw

    lines = raw.splitlines()
    file_ref = None
    kept_lines = []

    pattern = re.compile(r"^\s*mo\s+tai\s+lieu\s*:\s*(.+)$", re.IGNORECASE)
    pattern2 = re.compile(r"^\s*open\s+file\s*:\s*(.+)$", re.IGNORECASE)

    for line in lines:
        stripped = _strip_accents(line)
        m = pattern.match(stripped)
        if not m:
            m = pattern2.match(stripped)
        if m:
            file_ref = m.group(1).strip()
            continue
        kept_lines.append(line)

    cleaned = "\n".join(kept_lines).strip()
    return file_ref, cleaned


def _get_cache_answer(question: str):
    if not USE_RAG_CACHE:
        return None
    return get_cached_answer(question)



def _add_cache(question: str, answer: str) -> None:
    if not USE_RAG_CACHE:
        return
    add_cache(question, answer)


def _try_parse_json_payload(text: str):
    if not text:
        return None

    cleaned = re.sub(r"```(?:json)?", "", str(text), flags=re.IGNORECASE).replace("```", "").strip()
    if not cleaned:
        return None

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _ollama_extract_queries(question: str) -> list[str]:
    prompt = f"""
Bạn là hệ thống phân tích câu hỏi để tạo truy vấn tìm kiếm trong tài liệu.

YÊU CẦU:
- Trả về JSON duy nhất, không giải thích.
- JSON có khóa "queries": danh sách truy vấn cần tìm.
- Không cố định số lượng. Chỉ tách thành nhiều truy vấn khi câu hỏi có nhiều ý rõ ràng.
- Nếu không chắc tách, trả về 1 truy vấn duy nhất, giữ nguyên câu hỏi gốc.
- Mỗi mục là câu ngắn, rõ nghĩa, giữ nguyên tiếng Việt.

Ví dụ:
{{"queries": ["điều kiện cấp lại giấy phép", "thời hạn xử lý hồ sơ"]}}

CÂU HỎI:
{question}
"""

    raw = generate_answer(prompt)
    data = _try_parse_json_payload(raw)

    queries: list[str] = []
    if isinstance(data, dict):
        payload = data.get("queries") or data.get("questions") or data.get("items")
        if isinstance(payload, list):
            queries = payload
    elif isinstance(data, list):
        queries = data

    cleaned = []
    for item in queries:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if len(_normalize_search_text(text)) < 3:
            continue
        cleaned.append(text)

    if not cleaned:
        cleaned = _split_multi_questions(question) or [question]

    seen = set()
    deduped = []
    for q in cleaned:
        key = _normalize_search_text(q)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(q)

    return deduped or [question]


def _collect_related_chunks_from_file(company_id: str, file_ref: str | None, query: str) -> list[str]:
    if not file_ref:
        return []

    chunks = _rechunk_file_content(company_id, file_ref)
    if not chunks:
        return []

    ranked = []
    for position, text in enumerate(chunks):
        lexical_score = _keyword_overlap_score(query, text)
        phrase_score = _phrase_match_score(query, text)
        score = (lexical_score * 0.65) + (phrase_score * 0.35)
        if score <= 0:
            continue
        ranked.append((score, position, text))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked]


def _retrieve_expanded_chunks_for_query(
    company_id: str,
    query: str,
    top_k: int,
) -> tuple[list[str], str | None, list[dict]]:
    fetch_k = max(top_k * 5, 20)
    hits = search_global_chunks(company_id, query, top_k=fetch_k)
    if not hits:
        hits = search_vectors_with_sources(company_id, query, top_k=fetch_k)

    if hits:
        hits = _rerank_hits(query, hits, top_k=fetch_k)
    else:
        hits = []

    normalized_query = _normalize(_strip_accents(query))
    best_hit = None
    if normalized_query and len(normalized_query) >= 12:
        for hit in hits:
            text = hit.get("text") or ""
            normalized_text = _normalize(_strip_accents(text))
            if normalized_query in normalized_text:
                best_hit = hit
                break

    if best_hit is None and hits:
        best_hit = hits[0]

    file_ref = None
    if best_hit:
        file_ref = best_hit.get("file_id") or best_hit.get("file_name")

    expanded_chunks = _collect_related_chunks_from_file(company_id, file_ref, query)
    if not expanded_chunks and best_hit:
        text = best_hit.get("text")
        if text:
            expanded_chunks = [text]

    return _dedupe_chunks(expanded_chunks), file_ref, hits




def _format_with_ollama(question: str, context: str) -> str:
    prompt = f"""
Bạn là hệ thống định dạng kết quả trích dẫn từ tài liệu.

YÊU CẦU:
- Chỉ sắp xếp, xuống dòng, thêm tiêu đề hoặc đánh số nếu cần.
- Tuyệt đối không viết lại nội dung.
- Không thêm, không bớt, không suy luận.
- Giữ nguyên câu chữ trong nội dung đã cho.
- Trả lời bằng tiếng Việt.

NỘI DUNG:
{context}

Hãy định dạng lại cho dễ đọc.
"""

    raw = generate_answer(prompt)
    cleaned = clean_text(raw)
    cleaned = remove_non_vietnamese(cleaned)
    cleaned = remove_summary(cleaned)

    if not cleaned.strip():
        return context.strip()

    return cleaned


def _dedupe_chunks(chunks: list[str]) -> list[str]:
    unique = []
    seen = set()
    for chunk in chunks:
        text = (chunk or "").strip()
        if not text:
            continue
        key = _normalize(text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def _normalize_for_overlap(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _merge_overlap_text(left: str, right: str) -> str:
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right
    if not right:
        return left

    left_norm = _normalize_for_overlap(left)
    right_norm = _normalize_for_overlap(right)

    max_overlap = min(len(left_norm), len(right_norm), 240)
    best_overlap = 0
    for size in range(max_overlap, 24, -1):
        if left_norm[-size:] == right_norm[:size]:
            best_overlap = size
            break

    if best_overlap:
        right_index = 0
        normalized_count = 0
        while right_index < len(right):
            if not right[right_index].isspace():
                normalized_count += 1
            if normalized_count >= best_overlap:
                break
            right_index += 1
        return f"{left} {right[right_index + 1:].lstrip()}".strip()

    return f"{left}\n\n{right}"


def _is_continuation_line(previous: str, current: str) -> bool:
    if not previous or not current:
        return False

    if _is_structured_line(previous) or _is_structured_line(current):
        return False

    if previous.endswith((".", ":", ";", "?", "!")):
        return False

    if current[:1].islower():
        return True

    continuation_prefixes = (
        "và",
        "hoặc",
        "như",
        "của",
        "cho",
        "với",
        "trong",
        "theo",
        "do",
        "khi",
        "để",
        "là",
        "thì",
    )
    lowered = current.lower()
    return any(lowered.startswith(prefix + " ") for prefix in continuation_prefixes)


def _reconstruct_full_text(chunks: list[str]) -> str:
    items = list(chunks or [])
    if not items:
        return ""

    if isinstance(items[0], dict):
        items = sorted(items, key=lambda x: x.get("position", 0))
        items = [item.get("text", "") for item in items]

    merged = ""
    for chunk in _dedupe_chunks(items):
        merged = _merge_overlap_text(merged, chunk) if merged else chunk.strip()

    raw_lines = [line.strip() for line in merged.splitlines()]
    rebuilt_lines = []

    for line in raw_lines:
        if not line:
            if rebuilt_lines and rebuilt_lines[-1] != "":
                rebuilt_lines.append("")
            continue

        if rebuilt_lines and rebuilt_lines[-1] and _is_continuation_line(rebuilt_lines[-1], line):
            rebuilt_lines[-1] = f"{rebuilt_lines[-1]} {line}"
            continue

        if rebuilt_lines and rebuilt_lines[-1] and line in rebuilt_lines[-1]:
            continue

        rebuilt_lines.append(line)

    while rebuilt_lines and rebuilt_lines[0] == "":
        rebuilt_lines.pop(0)
    while rebuilt_lines and rebuilt_lines[-1] == "":
        rebuilt_lines.pop()

    return "\n".join(rebuilt_lines).strip()


def _tokenize_for_overlap(text: str) -> set[str]:
    return set(re.findall(r"\w+", _normalize(_strip_accents(text))))


def _keyword_overlap_score(query: str, text: str) -> float:
    q_tokens = _tokenize_for_overlap(query)
    if not q_tokens:
        return 0.0

    t_tokens = _tokenize_for_overlap(text)
    if not t_tokens:
        return 0.0

    return len(q_tokens & t_tokens) / len(q_tokens)


def _phrase_match_score(query: str, text: str) -> float:
    normalized_query = _normalize(_strip_accents(query))
    normalized_text = _normalize(_strip_accents(text))
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


def _collapse_redundant_blocks(lines: list[str]) -> list[str]:
    collapsed = []
    previous_tokens = set()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if collapsed and collapsed[-1] != "":
                collapsed.append("")
            previous_tokens = set()
            continue

        current_tokens = _tokenize_for_overlap(stripped)
        if collapsed and previous_tokens and current_tokens:
            overlap = len(previous_tokens & current_tokens) / max(1, len(current_tokens))
            if overlap >= 0.8:
                continue

        collapsed.append(stripped)
        previous_tokens = current_tokens

    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()

    return collapsed


def _is_structured_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False

    return bool(
        re.match(r"^\d+[\.\)]\s*", stripped)
        or re.match(r"^[a-zA-ZđĐ][\)\.]\s*", stripped)
        or re.match(r"^[-*•]\s+", stripped)
        or (":" in stripped and len(stripped.split(":", 1)[0].strip()) <= 80)
    )


def _split_inline_structured_segments(text: str) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []

    pattern = r"(?=(?:^|\s)(?:\d+[\.\)]|[a-zA-ZđĐ][\)\.])\s+)"
    parts = re.split(pattern, stripped)
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return cleaned or [stripped]


def _split_multi_questions(question: str) -> list[str]:
    raw = (question or "").strip()
    if not raw:
        return []

    connector_pattern = r"\s+(và|va|hoặc|hoac|với|voi)\s+"
    normalized = re.sub(connector_pattern, ", ", raw, flags=re.IGNORECASE)

    parts = []
    for part in normalized.split(","):
        cleaned = part.strip().strip(".;:!?")
        if not cleaned:
            continue
        parts.append(cleaned)

    parts = [p for p in parts if len(_normalize_search_text(p)) >= 3]
    return parts if len(parts) >= 2 else []


def _heading_level(line: str) -> int:
    stripped = (line or "").strip()
    if not stripped:
        return 0

    numeric = re.match(r"^(\d+(?:\.\d+)*)([\)\.]?)\s+", stripped)
    if numeric:
        return max(1, numeric.group(1).count(".") + 1)

    alpha = re.match(r"^[a-zA-Z][\)\.]\s+", stripped)
    if alpha:
        return 2

    if stripped.endswith(":") and len(stripped.split(":", 1)[0].strip()) <= 80:
        return 2

    if stripped.isupper() and len(stripped) <= 80:
        return 1

    return 1


def _heading_match_score(heading: str, query: str) -> float:
    if not heading or not query:
        return 0.0

    heading_norm = _normalize_search_text(heading)
    query_norm = _normalize_search_text(query)
    if not heading_norm or not query_norm:
        return 0.0

    if heading_norm == query_norm:
        return 1.0
    if heading_norm in query_norm or query_norm in heading_norm:
        return 0.95

    heading_tokens = set(heading_norm.split())
    query_tokens = set(query_norm.split())
    if not heading_tokens or not query_tokens:
        return 0.0

    overlap = len(heading_tokens & query_tokens) / max(1, len(query_tokens))
    return overlap


def _extract_section_by_heading(text: str, question: str) -> str | None:
    if not text or not question:
        return None

    lines = []
    for raw_line in text.splitlines():
        split_lines = _split_inline_structured_segments(raw_line)
        lines.extend(split_lines if split_lines else [raw_line])

    title_line = ""
    for line in lines:
        if line.strip():
            title_line = line.strip()
            break

    if title_line:
        title_score = _heading_match_score(title_line, question)
        question_norm = _normalize_search_text(question)
        title_norm = _normalize_search_text(title_line)
        if (
            title_score >= 0.6
            or (title_norm and question_norm and title_norm in question_norm)
            or (title_norm and question_norm and question_norm in title_norm)
        ):
            return _reconstruct_full_text([text])

    headings = []
    for idx, line in enumerate(lines):
        if _is_structured_line(line):
            headings.append((idx, line.strip(), _heading_level(line)))

    if not headings:
        return None

    best = None
    for idx, heading, level in headings:
        score = _heading_match_score(heading, question)
        if score < 0.6:
            continue
        if not best or score > best["score"]:
            best = {"index": idx, "heading": heading, "level": level, "score": score}

    if not best:
        return None

    target_index = best["index"]
    target_level = best["level"]

    start = target_index
    end = len(lines)
    for idx, _, level in headings:
        if idx <= target_index:
            continue
        if level <= target_level:
            end = idx
            break

    parent_headings = []
    current_level = target_level
    for idx, heading, level in reversed(headings):
        if idx >= target_index:
            continue
        if level < current_level:
            parent_headings.append(heading)
            current_level = level

    parent_headings.reverse()
    section_lines = []
    if parent_headings:
        section_lines.extend(parent_headings)
        section_lines.append("")
    section_lines.extend(lines[start:end])

    cleaned = [line.rstrip() for line in section_lines]
    return "\n".join([line for line in cleaned if line.strip() or line == ""]).strip() or None


def _detect_section_query(question: str):
    q = (question or "").lower()

    dieu = re.search(r"điều\s+(\d+)", q)
    if dieu:
        return f"Điều {dieu.group(1)}"

    khoan = re.search(r"khoản\s+(\d+)", q)
    if khoan:
        return f"Khoản {khoan.group(1)}"

    chuong = re.search(r"chương\s+([ivx]+|\d+)", q)
    if chuong:
        return f"Chương {chuong.group(1)}"

    return None

def _format_listing_from_context(chunks: list[str]) -> str:
    lines = []
    for chunk in _dedupe_chunks(chunks):
        for raw_line in str(chunk).splitlines():
            split_lines = _split_inline_structured_segments(raw_line)
            lines.extend(split_lines if split_lines else [raw_line])

    formatted = []
    current_item = None

    def flush_current():
        nonlocal current_item
        if current_item:
            formatted.append(current_item.strip())
            current_item = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            flush_current()
            continue

        if _is_structured_line(stripped):
            flush_current()
            current_item = stripped
            continue

        if current_item:
            current_item = f"{current_item} {stripped}"
            continue

        if stripped.isupper() or stripped.endswith(":"):
            formatted.append(stripped)

    flush_current()
    formatted = _collapse_redundant_blocks(formatted)
    return "\n".join(formatted).strip()


def _clean_llm_output(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""

    raw = raw.replace("Kết quả đã lọc:", "").replace("Trả lời:", "").strip()
    lines = raw.splitlines()

    cleaned = []
    seen = set()
    negative_seen = False
    previous_blank = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue

        previous_blank = False
        normalized = _normalize(stripped)
        if not normalized:
            continue

        if normalized in {
            "không tìm thấy thông tin phù hợp.",
            "khong tim thay thong tin phu hop.",
            "không tìm thấy thông tin phù hợp trong tài liệu hiện tại.",
            "khong tim thay thong tin phu hop trong tai lieu hien tai.",
        }:
            if negative_seen or cleaned:
                continue
            negative_seen = True
            cleaned.append("Không tìm thấy thông tin phù hợp trong tài liệu hiện tại.")
            continue

        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(stripped)

    cleaned = _collapse_redundant_blocks(cleaned)
    return "\n".join(cleaned).strip()


def _is_llm_error(text: str) -> bool:
    normalized = _normalize(text)
    return normalized.startswith("lỗi ollama:")


def _is_negative_answer(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {
        "không tìm thấy thông tin phù hợp.",
        "khong tim thay thong tin phu hop.",
        "không tìm thấy thông tin phù hợp trong tài liệu hiện tại.",
        "khong tim thay thong tin phu hop trong tai lieu hien tai.",
        "không tìm thấy dữ liệu liên quan trong tài liệu.",
        "khong tim thay du lieu lien quan trong tai lieu.",
    }


def _mentions_file_switch(normalized_question: str) -> bool:
    return any(keyword in normalized_question for keyword in SWITCH_FILE_KEYWORDS)


def _is_file_lookup_request(question: str) -> bool:
    normalized_question = _normalize_search_text(question)
    return any(normalized_question.startswith(prefix) for prefix in FILE_LOOKUP_PREFIXES)


def _extract_file_lookup_query(question: str) -> str:
    raw_question = ((question or "").strip().splitlines() or [""])[0].strip()
    normalized_question = _normalize_search_text(raw_question)

    for prefix in FILE_LOOKUP_PREFIXES:
        if normalized_question.startswith(prefix):
            raw_normalized = _normalize_search_text(raw_question)
            if raw_normalized == normalized_question:
                trimmed = normalized_question[len(prefix):].strip()
                return trimmed

            pattern = re.compile(rf"^\s*{re.escape(prefix)}[\s:,-]*", re.IGNORECASE)
            stripped = pattern.sub("", _strip_accents(raw_question)).strip()
            return _normalize_search_text(stripped)

    return normalized_question


def _list_known_files(company_id: str) -> list[dict]:
    files_by_id = {}

    for meta in get_indexed_files(company_id):
        file_id = meta.get("file_id")
        if not file_id:
            continue
        files_by_id[file_id] = {
            "file_id": file_id,
            "file_name": (meta.get("file_name") or file_id).strip(),
            "is_drive_file": False,
        }

    for meta in drive_files_collection.find({"company_id": company_id}, {"file_id": 1, "file_name": 1}):
        file_id = meta.get("file_id")
        if not file_id:
            continue

        file_name = (meta.get("file_name") or files_by_id.get(file_id, {}).get("file_name") or file_id).strip()
        existing = files_by_id.get(file_id)
        if existing:
            existing["file_name"] = file_name or existing["file_name"]
            existing["is_drive_file"] = True
            continue

        files_by_id[file_id] = {
            "file_id": file_id,
            "file_name": file_name,
            "is_drive_file": True,
        }

    return list(files_by_id.values())


def _find_files_by_name(company_id: str, query: str, top_k: int = 5) -> list[dict]:
    normalized_query = _normalize_search_text(query)
    compact_query = _compact_search_text(query)
    if not normalized_query:
        return []

    query_tokens = set(normalized_query.split())
    candidates = []

    for meta in _list_known_files(company_id):
        file_id = meta.get("file_id")
        file_name = (meta.get("file_name") or "").strip()
        if not file_id or not file_name:
            continue

        normalized_name = _normalize_search_text(file_name)
        compact_name = _compact_search_text(file_name)
        if not normalized_name:
            continue

        name_tokens = set(normalized_name.split())
        token_overlap = (len(query_tokens & name_tokens) / len(query_tokens)) if query_tokens else 0.0
        score = 0.0

        if normalized_name == normalized_query:
            score = 1.0
        elif compact_query and compact_name == compact_query:
            score = 0.98
        elif compact_query and compact_query in compact_name:
            score = 0.95
        elif normalized_query in normalized_name:
            score = 0.92
        elif normalized_name.startswith(normalized_query):
            score = 0.88
        elif query_tokens and token_overlap > 0:
            score = 0.45 + (token_overlap * 0.4)

        if score <= 0:
            continue

        candidates.append(
            {
                "file_id": file_id,
                "file_name": file_name,
                "is_drive_file": bool(meta.get("is_drive_file")),
                "score": score,
                "token_overlap": token_overlap,
                "name_length": len(normalized_name),
            }
        )

    candidates.sort(key=lambda item: (-item["score"], -item["token_overlap"], item["name_length"], item["file_name"]))
    return candidates[:top_k]


def _find_mentioned_file(company_id: str, question: str) -> dict | None:
    normalized_question = _normalize_search_text(question)
    compact_question = _compact_search_text(question)
    if not normalized_question:
        return None

    fallback_matches = {
        item["file_id"]: item
        for item in _find_files_by_name(company_id, question, top_k=5)
    }
    candidates = []
    for meta in _list_known_files(company_id):
        file_id = meta.get("file_id")
        file_name = (meta.get("file_name") or "").strip()
        if not file_id or not file_name:
            continue

        normalized_name = _normalize_search_text(file_name)
        compact_name = _compact_search_text(file_name)
        if not normalized_name:
            continue

        score = 0.0
        if normalized_name and normalized_name in normalized_question:
            score = 1.0
        elif compact_name and compact_name in compact_question:
            score = 0.98
        elif normalized_question in normalized_name and len(normalized_question) >= 8:
            score = 0.9
        else:
            fallback = fallback_matches.get(file_id)
            if fallback:
                score = float(fallback.get("score") or 0.0)

        if score <= 0:
            continue

        candidates.append(
            {
                "file_id": file_id,
                "file_name": file_name,
                "score": score,
            }
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item["score"], len(item["file_name"]), item["file_name"]))
    return candidates[0]


def _is_follow_up(normalized_question: str) -> bool:
    return any(keyword in normalized_question for keyword in FOLLOW_UP_KEYWORDS)


def _is_full_current_file_request(normalized_question: str) -> bool:
    full_keywords = (
        "toàn bộ nội dung",
        "toan bo noi dung",
        "toàn bộ file",
        "toan bo file",
        "toàn bộ trong file",
        "toan bo trong file",
        "toàn bộ tài liệu",
        "toan bo tai lieu",
    )
    return any(keyword in normalized_question for keyword in full_keywords)


def _heuristic_is_listing(normalized_question: str) -> bool:
    listing_keywords = (
        "liệt kê",
        "liet ke",
        "mục lục",
        "muc luc",
        "chương",
        "chuong",
        "danh sách",
        "danh sach",
        "những gì",
        "nhung gi",
        "các",
        "cac",
        "ghi đầy đủ",
        "ghi day du",
        "toàn bộ",
        "toan bo",
    )
    return any(keyword in normalized_question for keyword in listing_keywords)


def _safe_int(raw: str, default: int, min_value: int = 3, max_value: int = 20) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return max(min_value, min(max_value, value))


def _classify_intent(
    question: str,
    normalized_question: str,
    current_file: str | None,
    current_topic: str | None,
) -> str:
    if _is_file_lookup_request(question):
        return "find_file"
    if _mentions_file_switch(normalized_question):
        return "switch_file"
    if _is_full_current_file_request(normalized_question):
        return "open_full_file"
    if current_file and _is_follow_up(normalized_question):
        return "follow_up"
    if current_file and len(normalized_question) < 60:
        return "follow_up"

    question_normalized = _normalize(question)
    if question_normalized and question_normalized in {
        _normalize(current_file or ""),
        _normalize(current_topic or ""),
    }:
        return "open_full_file"

    return "listing" if _heuristic_is_listing(normalized_question) else "fact"


def _analyze_question(question: str) -> dict:
    normalized = _normalize(question)

    listing_keywords = [
        "liệt kê",
        "liet ke",
        "danh sách",
        "danh sach",
        "mục",
        "muc",
        "các",
        "cac",
    ]

    mode = "fact"

    for k in listing_keywords:
        if k in normalized:
            mode = "listing"
            break

    top_k = 12 if mode == "listing" else 6

    return {
        "mode": mode,
        "search_query": question,
        "top_k": top_k,
    }


def _fallback_answer_from_chunks(chunks: list[str], mode: str) -> str:
    deduped = _dedupe_chunks(chunks)
    if not deduped:
        return "Không tìm thấy thông tin phù hợp trong tài liệu hiện tại."

    if mode == "listing":
        structured = _format_listing_from_context(deduped)
        if structured:
            return structured

    return "\n\n".join(deduped[: min(3, len(deduped))]).strip()


def _build_draft_answer(question: str, chunks: list[str], mode: str) -> str:
    chunks = _dedupe_chunks(chunks)
    if not chunks:
        return "Không tìm thấy thông tin phù hợp."

    context = _reconstruct_full_text(chunks)
    return context


def _format_full_file_content(chunks: list[str]) -> str:
    raw_chunks = _dedupe_chunks(chunks)
    if not raw_chunks:
        return "Không tìm thấy nội dung trong tài liệu hiện tại."
    return "\n\n".join(raw_chunks).strip()


def _try_heading_answer(
    company_id: str,
    file_ref: str | None,
    question: str,
    fallback_chunks: list[str],
) -> str | None:
    full_text = ""
    if file_ref:
        chunks = _rechunk_file_content(company_id, file_ref)
        full_text = _reconstruct_full_text(chunks)
    if not full_text and fallback_chunks:
        full_text = _reconstruct_full_text(fallback_chunks)
    return _extract_section_by_heading(full_text, question)


def _try_heading_answer_with_chunks(
    question: str,
    chunks: list[str],
) -> str | None:
    full_text = _reconstruct_full_text(chunks)
    return _extract_section_by_heading(full_text, question)


def _grounding_filter(question: str, draft_answer: str, chunks: list[str]) -> str:
    return draft_answer


def _build_context_from_chunks(chunks: list[str], max_length: int = 8000) -> str:
    context = "\n\n".join(_dedupe_chunks(chunks))
    return context[:max_length]


def _load_full_file_text(
    company_id: str,
    file_ref: str | None,
    best_hit: dict | None = None,
) -> tuple[str, str | None, str | None]:
    if not file_ref:
        return "", None, None

    lookup_refs = [file_ref]
    if best_hit:
        for candidate in (best_hit.get("file_id"), best_hit.get("file_name")):
            if candidate and candidate not in lookup_refs:
                lookup_refs.append(candidate)

    file_doc = None
    for ref in lookup_refs:
        file_doc = drive_files_collection.find_one(
            {"company_id": company_id, "$or": [{"file_id": ref}, {"file_name": ref}]},
            {"file_id": 1, "file_name": 1, "mime_type": 1, "source": 1, "file_path": 1},
        )
        if file_doc:
            break

    if not file_doc:
        return "", None, None

    file_id = file_doc.get("file_id") or file_ref
    file_name = file_doc.get("file_name") or file_ref
    source = file_doc.get("source")

    if source == "local_upload":
        file_path = file_doc.get("file_path")
        if not file_path:
            upload_pattern = os.path.join("uploads", company_id, f"{file_id}_*")
            matches = glob(upload_pattern)
            file_path = matches[0] if matches else None

        if not file_path or not os.path.exists(file_path):
            return "", file_id, file_name

        try:
            return extract_text(file_path), file_id, file_name
        except Exception as exc:
            print(f"[rag] Failed to reopen local file {file_id}: {exc}")
            return "", file_id, file_name

    mime_type = file_doc.get("mime_type")
    if not mime_type:
        return "", file_id, file_name

    try:
        from app.db.mongo import get_db
        from bson import ObjectId

        companies_collection = get_db()["companies"]
        try:
            company = companies_collection.find_one({"_id": ObjectId(company_id)})
        except Exception:
            company = None
        if not company:
            return "", file_id, file_name

        content = download_drive_file(company, file_id, mime_type)
        if not content:
            return "", file_id, file_name

        suffix = _temp_extension_for_mime(mime_type, file_name)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name

            return extract_text(temp_path), file_id, file_name
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception as exc:
        print(f"[rag] Failed to reopen drive file {file_id}: {exc}")
        return "", file_id, file_name


def _build_full_file_context(company_id: str, file_ref: str | None) -> str:
    if not file_ref:
        return ""

    raw_chunks = get_all_chunks_in_file(company_id, file_ref)
    if not raw_chunks:
        return ""

    if isinstance(raw_chunks[0], dict):
        raw_chunks = [item.get("text", "") for item in raw_chunks]

    return _reconstruct_full_text(raw_chunks)


def _heading_path_prefixes(heading_path: str | None) -> list[str]:
    if not heading_path:
        return []
    parts = [part.strip() for part in str(heading_path).split(" > ") if part.strip()]
    prefixes = []
    for i in range(1, len(parts) + 1):
        prefixes.append(" > ".join(parts[:i]))
    return prefixes


def _select_heading_subtree_context(
    full_text: str,
    query: str,
    hits: list[dict] | None = None,
    max_sections: int = 12,
) -> tuple[str, str | None]:
    if not full_text or not query:
        return "", None

    sections = split_text_with_headings(full_text)
    if not sections:
        return "", None

    scored_prefixes = {}
    section_order = {}
    normalized_hits = []
    for hit in hits or []:
        normalized_hits.append(
            {
                "heading_path": (hit.get("heading_path") or "").strip(),
                "heading": (hit.get("heading") or "").strip(),
                "score": float(
                    hit.get("rerank_score")
                    or hit.get("final_score")
                    or hit.get("score")
                    or 0.0
                ),
            }
        )

    for position, item in enumerate(sections):
        section_text = (item.get("section_text") or item.get("text") or "").strip()
        heading = (item.get("heading") or "").strip()
        heading_path = (item.get("heading_path") or heading).strip()
        if not section_text or not heading_path:
            continue

        lexical_score = _keyword_overlap_score(query, section_text)
        phrase_score = _phrase_match_score(query, section_text)
        heading_score = max(
            _heading_match_score(heading, query),
            _heading_match_score(heading_path, query),
        )

        hit_score = 0.0
        for hit in normalized_hits:
            if hit["heading_path"] and hit["heading_path"] == heading_path:
                hit_score = max(hit_score, hit["score"])
            elif hit["heading"] and hit["heading"] == heading:
                hit_score = max(hit_score, hit["score"] * 0.95)
            elif hit["heading_path"] and heading_path.startswith(hit["heading_path"]):
                hit_score = max(hit_score, hit["score"] * 0.85)

        section_score = (
            (lexical_score * 0.35)
            + (phrase_score * 0.25)
            + (heading_score * 0.30)
            + (hit_score * 0.30)
        )

        if section_score <= 0:
            continue

        section_order[heading_path] = min(position, section_order.get(heading_path, position))

        for prefix in _heading_path_prefixes(heading_path):
            entry = scored_prefixes.setdefault(
                prefix,
                {
                    "best_score": 0.0,
                    "sum_score": 0.0,
                    "sections": [],
                    "first_position": position,
                },
            )
            entry["best_score"] = max(entry["best_score"], section_score)
            entry["sum_score"] += section_score
            entry["first_position"] = min(entry["first_position"], position)
            entry["sections"].append((position, section_text))

    if not scored_prefixes:
        return "", None

    ranked = []
    for prefix, entry in scored_prefixes.items():
        unique_sections = []
        seen = set()
        for position, section_text in sorted(entry["sections"], key=lambda x: x[0]):
            key = _normalize(section_text)
            if key in seen:
                continue
            seen.add(key)
            unique_sections.append((position, section_text))

        score = (
            (entry["best_score"] * 0.55)
            + (entry["sum_score"] * 0.20)
            + (_heading_match_score(prefix, query) * 0.25)
        )
        ranked.append(
            {
                "prefix": prefix,
                "score": score,
                "sections": unique_sections[:max_sections],
                "first_position": entry["first_position"],
            }
        )

    ranked.sort(key=lambda item: (-item["score"], item["first_position"], len(item["prefix"])))
    best = ranked[0]
    context = "\n\n".join([text for _, text in best["sections"]]).strip()
    return context, best["prefix"]


def _score_context_match(query: str, context: str) -> float:
    if not query or not context:
        return 0.0

    lexical_score = _keyword_overlap_score(query, context)
    phrase_score = _phrase_match_score(query, context)

    normalized_query = _normalize(_strip_accents(query))
    normalized_context = _normalize(_strip_accents(context))
    query_len = max(1, len(normalized_query))
    coverage_score = min(1.0, len(normalized_context) / query_len)

    return (
        (lexical_score * 0.45)
        + (phrase_score * 0.40)
        + (coverage_score * 0.15)
    )


def _select_best_context_for_query(
    query: str,
    full_file_context: str,
    heading_context: str = "",
    heading_subtree_context: str = "",
) -> tuple[str, str, dict[str, float]]:
    candidates = []
    if heading_subtree_context:
        candidates.append(("heading_subtree", heading_subtree_context))
    if heading_context:
        candidates.append(("heading_context", heading_context))
    if full_file_context:
        candidates.append(("full_file", full_file_context))

    if not candidates:
        return "", "empty", {}

    scores: dict[str, float] = {}
    best_label = candidates[0][0]
    best_context = candidates[0][1]
    best_score = -1.0

    for label, candidate_context in candidates:
        score = _score_context_match(query, candidate_context)
        scores[label] = score
        if score > best_score:
            best_label = label
            best_context = candidate_context
            best_score = score

    return best_context, best_label, scores


def _collect_heading_subtree_chunks(
    company_id: str,
    file_ref: str | None,
    heading_prefix: str | None,
    full_text: str | None = None,
) -> list[str]:
    if not file_ref or not heading_prefix:
        return []

    matched = []
    raw_chunks = get_all_chunks_in_file(company_id, file_ref)

    if raw_chunks and isinstance(raw_chunks[0], dict):
        for item in raw_chunks:
            heading_path = (item.get("heading_path") or item.get("heading") or "").strip()
            if not heading_path:
                continue
            if heading_path == heading_prefix or heading_path.startswith(f"{heading_prefix} > "):
                matched.append(item)

        matched.sort(
            key=lambda item: (
                item.get("position", 10**9),
                item.get("section_id", 10**9),
                item.get("chunk_in_section", 10**9),
            )
        )

        texts = []
        seen = set()
        for item in matched:
            text = (item.get("text") or "").strip()
            key = _normalize(text)
            if not text or key in seen:
                continue
            seen.add(key)
            texts.append(text)

        if texts:
            return texts

    if not full_text:
        return []

    sections = split_text_with_headings(full_text)
    if not sections:
        return []

    texts = []
    seen = set()
    for item in sections:
        heading_path = (item.get("heading_path") or item.get("heading") or "").strip()
        section_text = (item.get("section_text") or item.get("text") or "").strip()
        key = _normalize(section_text)
        if not heading_path or not section_text or key in seen:
            continue
        if heading_path == heading_prefix or heading_path.startswith(f"{heading_prefix} > "):
            seen.add(key)
            texts.append(section_text)

    return texts


def _expand_sections_from_hits(
    hits: list[dict],
    file_ref: str | None,
    top_sections: int = 3,
) -> list[str]:
    if not hits:
        return []

    section_candidates = []
    seen = set()

    for hit in hits:
        hit_file = hit.get("file_id") or hit.get("file_name")
        if file_ref and hit_file != file_ref:
            continue

        section_text = (hit.get("section_text") or "").strip()
        if not section_text:
            continue

        dedupe_key = _normalize(section_text)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        rank_score = float(
            hit.get("rerank_score")
            or hit.get("final_score")
            or hit.get("score")
            or 0.0
        )
        section_candidates.append((rank_score, section_text))

    section_candidates.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in section_candidates[: max(1, top_sections)]]


def _fallback_chunks_from_file(company_id: str, file_ref: str | None, limit: int = 20) -> list[str]:
    if not file_ref:
        return []

    chunks = get_all_chunks_in_file(company_id, file_ref)
    if not chunks:
        return []

    if isinstance(chunks[0], dict):
        chunks = [item.get("text", "") for item in chunks]

    deduped = _dedupe_chunks(chunks)
    if not deduped:
        return []

    return deduped[: max(1, limit)]


def _build_source_override(
    company_id: str,
    file_ref: str | None,
    chunks: list[str],
    hits: list[dict] | None = None,
) -> dict | None:
    base_source = _build_source_payload(company_id, file_ref) or {}

    if hits:
        hit = hits[0]
        return {
            "file_name": hit.get("file_name") or base_source.get("file_name") or file_ref,
            "file_id": hit.get("file_id") or base_source.get("file_id") or file_ref,
            "chunk_id": hit.get("chunk_id"),
            "text": hit.get("text"),
            "url": base_source.get("url"),
            "download_api": base_source.get("download_api"),
            "source_type": base_source.get("source_type"),
        }
    if chunks:
        return {
            "file_name": base_source.get("file_name") or file_ref,
            "file_id": base_source.get("file_id") or file_ref,
            "chunk_id": None,
            "text": chunks[0],
            "url": base_source.get("url"),
            "download_api": base_source.get("download_api"),
            "source_type": base_source.get("source_type"),
        }
    return base_source or None


def _final_answer_from_chunks(question: str, chunks: list[str]) -> str:
    """
    Trả lời trực tiếp từ tài liệu, không dùng LLM.
    """

    if not chunks:
        return "Không tìm thấy thông tin trong tài liệu."

    raw_chunks = _dedupe_chunks(chunks)
    if not raw_chunks:
        return "Không tìm thấy thông tin trong tài liệu."

    lines = []
    for chunk in raw_chunks:
        for raw_line in str(chunk).splitlines():
            line = raw_line.strip()
            if not line:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            lines.append(line)

    collapsed = _collapse_redundant_blocks(lines)
    if not collapsed:
        return "Không tìm thấy thông tin trong tài liệu."

    return "\n".join(collapsed).strip()


def _answer_supported_by_context(answer: str, context: str) -> bool:
    """
    Kiểm tra LLM có thêm nội dung ngoài context không
    """
    context_lower = context.lower()

    sentences = re.split(r"[.!?\n]", answer)

    for s in sentences:
        sentence = s.strip().lower()

        if not sentence:
            continue

        if len(sentence) < 20:
            continue

        if sentence not in context_lower:
            return False

    return True


def _final_format_answer(question: str, answer: str) -> str:
    cleaned_answer = (answer or "").strip()
    if not cleaned_answer:
        return "Không tìm thấy thông tin phù hợp trong tài liệu hiện tại."

    if USE_OLLAMA_FORMAT:
        formatted = _format_with_ollama(question, cleaned_answer)
        return formatted or cleaned_answer

    return cleaned_answer


def _build_source_payload(company_id: str, file_ref: str | None) -> dict | None:
    if not file_ref:
        return None

    doc = drive_files_collection.find_one(
        {
            "company_id": company_id,
            "$or": [
                {"file_id": file_ref},
                {"file_name": file_ref},
            ],
        },
        {"file_id": 1, "file_name": 1, "source": 1},
    )

    if doc:
        file_id = doc.get("file_id")
        file_name = doc.get("file_name") or file_ref
        source_type = doc.get("source") or "drive"
        return {
            "file_id": file_id,
            "file_name": file_name,
            "url": f"https://drive.google.com/open?id={file_id}" if file_id and source_type != "local_upload" else None,
            "download_api": f"/tai-lieu/tai-ve/{file_id}" if file_id and source_type == "local_upload" else None,
            "source_type": source_type,
        }

    for meta in get_indexed_files(company_id):
        file_id = meta.get("file_id")
        file_name = meta.get("file_name") or file_id
        if file_ref not in {file_id, file_name}:
            continue
        return {
            "file_id": file_id,
            "file_name": file_name,
            "url": None,
            "download_api": None,
            "source_type": None,
        }

    file_id = file_ref if len(file_ref) > 10 else None
    return {
        "file_id": file_id,
        "file_name": file_ref,
        "url": None,
        "download_api": None,
        "source_type": None,
    }


def _response_payload(
    company_id: str,
    answer: str,
    file_ref: str | None = None,
    context: str | None = None,
    source_override: dict | None = None,
) -> dict:
    return {
        "answer": answer,
        "source": source_override or _build_source_payload(company_id, file_ref),
        "context": context,
    }


def _rank_files_for_memory(hits: list[dict]) -> list[str]:
    file_counts = {}
    ordered_files = []

    for hit in hits:
        file_ref = hit.get("file_id") or hit.get("file_name")
        if not file_ref or file_ref == "Tài liệu không tên":
            continue
        if file_ref not in file_counts:
            file_counts[file_ref] = 0
            ordered_files.append(file_ref)
        file_counts[file_ref] += 1

    if not file_counts:
        return []

    file_positions = {name: i for i, name in enumerate(ordered_files)}
    return sorted(
        ordered_files,
        key=lambda name: (-file_counts[name], file_positions[name]),
    )


def _rechunk_file_content(company_id: str, file_ref: str | None) -> list[str]:
    if not file_ref:
        return []

    original_chunks = get_all_chunks_in_file(company_id, file_ref)
    if not original_chunks:
        return []

    full_text = _reconstruct_full_text(original_chunks)
    if not full_text:
        return []

    section_chunks = split_text_with_headings(full_text)
    if not section_chunks:
        rechunked = split_text(full_text)
        return _dedupe_chunks(rechunked)

    sections = []
    seen = set()
    for item in section_chunks:
        section_text = (item.get("section_text") or item.get("text") or "").strip()
        key = _normalize(section_text)
        if not section_text or key in seen:
            continue
        seen.add(key)
        sections.append(section_text)

    return sections


def _rank_rechunked_file_chunks(
    company_id: str,
    file_ref: str | None,
    query: str,
    top_k: int,
) -> list[dict]:
    chunks = _rechunk_file_content(company_id, file_ref)
    if not chunks:
        return []

    ranked = []
    for position, text in enumerate(chunks):
        lexical_score = _keyword_overlap_score(query, text)
        phrase_score = _phrase_match_score(query, text)
        score = (lexical_score * 0.65) + (phrase_score * 0.35)
        if score <= 0:
            continue

        ranked.append(
            {
                "text": text,
                "file_id": file_ref,
                "file_name": file_ref,
                "score": score,
                "semantic_score": score,
                "lexical_score": lexical_score,
                "phrase_score": phrase_score,
                "final_score": score,
                "position": position,
            }
        )

    ranked.sort(key=lambda item: (-item["final_score"], item["position"]))
    return ranked[:top_k]


def _rank_chunks_from_list(
    chunks: list[str],
    file_ref: str | None,
    query: str,
    top_k: int,
) -> list[dict]:
    if not chunks:
        return []

    ranked = []
    for position, text in enumerate(chunks):
        lexical_score = _keyword_overlap_score(query, text)
        phrase_score = _phrase_match_score(query, text)
        score = (lexical_score * 0.65) + (phrase_score * 0.35)
        if score <= 0:
            continue

        ranked.append(
            {
                "text": text,
                "file_id": file_ref,
                "file_name": file_ref,
                "score": score,
                "semantic_score": score,
                "lexical_score": lexical_score,
                "phrase_score": phrase_score,
                "final_score": score,
                "position": position,
            }
        )

    ranked.sort(key=lambda item: (-item["final_score"], item["position"]))
    return ranked[:top_k]


def _select_file_by_exact_content(query: str, hits: list[dict]) -> str | None:
    normalized_query = _normalize(_strip_accents(query))
    if not normalized_query or len(normalized_query) < 8:
        return None

    exact_candidates = {}
    for position, hit in enumerate(hits):
        file_ref = hit.get("file_id") or hit.get("file_name")
        text = hit.get("text") or ""
        if not file_ref or not text:
            continue

        normalized_text = _normalize(_strip_accents(text))
        phrase_score = float(hit.get("phrase_score") or _phrase_match_score(query, text))
        lexical_score = float(hit.get("lexical_score") or _keyword_overlap_score(query, text))
        contains_exact = normalized_query in normalized_text

        if not contains_exact and phrase_score < 0.99:
            continue

        item = exact_candidates.setdefault(
            file_ref,
            {
                "file_ref": file_ref,
                "best_phrase_score": 0.0,
                "best_lexical_score": 0.0,
                "hit_count": 0,
                "best_position": position,
            },
        )
        item["best_phrase_score"] = max(item["best_phrase_score"], phrase_score)
        item["best_lexical_score"] = max(item["best_lexical_score"], lexical_score)
        item["hit_count"] += 1
        item["best_position"] = min(item["best_position"], position)

    if not exact_candidates:
        return None

    ranked = sorted(
        exact_candidates.values(),
        key=lambda item: (
            -item["best_phrase_score"],
            -item["best_lexical_score"],
            -item["hit_count"],
            item["best_position"],
        ),
    )
    return ranked[0]["file_ref"]


def _select_file_by_exact_query_across_files(company_id: str, query: str) -> str | None:
    normalized_query = _normalize(_strip_accents(query))
    if not normalized_query or len(normalized_query) < 20:
        return None

    for meta in _list_known_files(company_id):
        file_ref = meta.get("file_id") or meta.get("file_name")
        if not file_ref:
            continue

        chunks = get_all_chunks_in_file(company_id, file_ref)
        if not chunks:
            continue

        if isinstance(chunks[0], dict):
            chunks = [item.get("text", "") for item in chunks]

        for chunk in chunks:
            normalized_chunk = _normalize(_strip_accents(chunk))
            if normalized_query in normalized_chunk:
                return file_ref

    return None


def _select_file_by_global_content_scan(company_id: str, query: str) -> str | None:
    normalized_query = _normalize(_strip_accents(query))
    if not normalized_query or len(normalized_query) < 12:
        return None

    query_tokens = _tokenize_for_overlap(query)
    if len(query_tokens) < 4:
        return None

    best_match = None

    for meta in _list_known_files(company_id):
        file_ref = meta.get("file_id") or meta.get("file_name")
        if not file_ref:
            continue

        chunks = get_all_chunks_in_file(company_id, file_ref)
        if not chunks:
            continue

        best_phrase = 0.0
        best_lexical = 0.0
        exact_count = 0

        for chunk in chunks:
            normalized_chunk = _normalize(_strip_accents(chunk))
            phrase_score = _phrase_match_score(query, chunk)
            lexical_score = _keyword_overlap_score(query, chunk)

            if normalized_query in normalized_chunk:
                exact_count += 1

            best_phrase = max(best_phrase, phrase_score)
            best_lexical = max(best_lexical, lexical_score)

        if exact_count == 0 and best_phrase < 0.92 and best_lexical < 0.85:
            continue

        candidate = {
            "file_ref": file_ref,
            "exact_count": exact_count,
            "best_phrase": best_phrase,
            "best_lexical": best_lexical,
        }

        if not best_match:
            best_match = candidate
            continue

        if (
            candidate["exact_count"],
            candidate["best_phrase"],
            candidate["best_lexical"],
        ) > (
            best_match["exact_count"],
            best_match["best_phrase"],
            best_match["best_lexical"],
        ):
            best_match = candidate

    return best_match["file_ref"] if best_match else None


def _choose_file_from_hits(
    company_id: str,
    query: str,
    hits: list[dict],
    top_k: int,
) -> tuple[list[dict], str | None, list[str]]:
    global_scan_file = _select_file_by_global_content_scan(company_id, query)
    if global_scan_file:
        file_hits = _rank_rechunked_file_chunks(company_id, global_scan_file, query, top_k=top_k)
        if file_hits:
            return file_hits, global_scan_file, [global_scan_file]

        fallback_global_hits = search_within_file_with_sources(company_id, global_scan_file, query, top_k=top_k)
        if fallback_global_hits:
            return fallback_global_hits, global_scan_file, [global_scan_file]

    exact_content_file = _select_file_by_exact_content(query, hits)
    if exact_content_file:
        file_hits = _rank_rechunked_file_chunks(company_id, exact_content_file, query, top_k=top_k)
        if file_hits:
            return file_hits, exact_content_file, [exact_content_file]

        fallback_exact_hits = search_within_file_with_sources(company_id, exact_content_file, query, top_k=top_k)
        if fallback_exact_hits:
            return fallback_exact_hits, exact_content_file, [exact_content_file]

    file_candidates = rank_files_from_chunks(company_id, query, hits, top_k=5)
    ranked_files = [item["file_id"] for item in file_candidates if item.get("file_id")]
    if not file_candidates:
        return hits[:top_k], None, ranked_files

    best_file = file_candidates[0].get("file_id")
    if not best_file:
        return hits[:top_k], None, ranked_files
    file_hits = _rank_rechunked_file_chunks(company_id, best_file, query, top_k=top_k)
    if file_hits:
        return file_hits, best_file, ranked_files

    fallback_file_hits = search_within_file_with_sources(company_id, best_file, query, top_k=top_k)
    if fallback_file_hits:
        return fallback_file_hits, best_file, ranked_files
    return hits[:top_k], best_file, ranked_files


def _filter_chunks(chunks, query):
    query_words = set(query.lower().split())
    filtered = []

    for c in chunks:
        text = str(c).lower()
        overlap = sum(1 for w in query_words if w in text)

        if overlap >= 1:   # chỉ cần 1 từ trùng
            filtered.append(c)

    return filtered or chunks

def _multi_query_retrieval(company_id: str, query: str, top_k: int = 5):
    """Multi query retrieval for better results."""
    queries = _generate_queries(query)

    results = []
    for q in queries:
        hits = search_global_chunks(company_id, q, top_k=max(top_k * 2, 12))
        results.extend(hits)

    ranked_by_text = {}
    for item in results:
        text = item.get("text", "").strip()
        if not text:
            continue

        key = _normalize_search_text(text)
        existing = ranked_by_text.get(key)
        item_score = float(
            item.get("final_score")
            or item.get("rerank_score")
            or item.get("score")
            or 0.0
        )
        if existing is None:
            ranked_by_text[key] = item
            continue

        existing_score = float(
            existing.get("final_score")
            or existing.get("rerank_score")
            or existing.get("score")
            or 0.0
        )
        if item_score > existing_score:
            ranked_by_text[key] = item

    unique_results = list(ranked_by_text.values())
    unique_results.sort(
        key=lambda item: float(
            item.get("final_score")
            or item.get("rerank_score")
            or item.get("score")
            or 0.0
        ),
        reverse=True,
    )
    return unique_results[: max(top_k * 3, 15)]


def _has_strong_file_hits(hits: list[dict]) -> bool:
    if not hits:
        return False

    best = hits[0]
    final_score = float(best.get("final_score") or 0.0)
    lexical_score = float(best.get("lexical_score") or 0.0)
    phrase_score = float(best.get("phrase_score") or 0.0)
    semantic_score = float(best.get("semantic_score") or best.get("score") or 0.0)

    if phrase_score >= 0.6:
        return True
    if lexical_score >= 0.5:
        return True
    if final_score >= 0.45 and (lexical_score >= 0.2 or semantic_score >= 0.55):
        return True
    return False

def _generate_queries(question: str) -> list[str]:
    queries = [question]

    q = (question or "").lower()

    if "là gì" in q:
        queries.append(q.replace("là gì", "").strip())

    if "quy định" in q:
        queries.append(q.replace("quy định", "").strip())

    return list(dict.fromkeys(queries))


_RERANKER = None


def _rerank_hits(question: str, hits: list[dict], top_k: int) -> list[dict]:
    if not hits:
        return []

    global _RERANKER
    try:
        if _RERANKER is None:
            from sentence_transformers import CrossEncoder
            _RERANKER = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    except Exception:
        return hits[:top_k]

    pairs = [[question, hit.get("text", "")] for hit in hits]
    try:
        scores = _RERANKER.predict(pairs)
    except Exception:
        return hits[:top_k]

    ranked = []
    for score, hit in zip(scores, hits):
        item = dict(hit)
        lexical_score = float(item.get("lexical_score") or _keyword_overlap_score(question, item.get("text", "")))
        phrase_score = float(item.get("phrase_score") or _phrase_match_score(question, item.get("text", "")))
        base_score = float(item.get("final_score") or item.get("score") or 0.0)
        rerank_score = float(score)
        blended_score = (rerank_score * 0.45) + (base_score * 0.35) + (lexical_score * 0.1) + (phrase_score * 0.1)
        item["rerank_score"] = rerank_score
        item["final_score"] = max(base_score, blended_score)
        ranked.append(item)

    ranked.sort(key=lambda item: float(item.get("final_score") or 0.0), reverse=True)
    reranked_hits = []
    for item in ranked:
        reranked_hits.append(item)

    return reranked_hits[:top_k]


def _retrieve_ranked_chunks(
    company_id: str,
    query: str,
    top_k: int,
    user_id: str | None = None,
) -> tuple[list[dict], str | None, list[str]]:
    fetch_k = max(top_k * 3, 12)

    current_file = get_current_file(user_id) if user_id else None
    if current_file:
        hits = search_within_file_with_sources(
            company_id,
            current_file,
            query,
            top_k=top_k * 3,
        )
        if hits:
            hits = _rerank_hits(query, hits, top_k=top_k * 3)
            if _has_strong_file_hits(hits):
                return hits[:top_k], current_file, [current_file]

    hits = _multi_query_retrieval(company_id, query, top_k=fetch_k)
    if hits:
        hits = _rerank_hits(query, hits, top_k=fetch_k)
    if hits:
        return _choose_file_from_hits(company_id, query, hits, top_k)

    fallback_hits = search_vectors_with_sources(company_id, query, top_k=fetch_k)
    if fallback_hits:
        fallback_hits = _rerank_hits(query, fallback_hits, top_k=fetch_k)
    if not fallback_hits:
        return [], None, []
    return _choose_file_from_hits(company_id, query, fallback_hits, top_k)


def _expand_listing_chunks(
    company_id: str,
    selected_file: str | None,
    fallback_hits: list[dict],
) -> list[str]:
    if selected_file:
        chunks = _rechunk_file_content(company_id, selected_file)
        if chunks:
            return _dedupe_chunks(chunks)
    return _dedupe_chunks([hit["text"] for hit in fallback_hits])


def rag_answer_with_full_file_chunks(company_id: str, question: str, user_id: str):
    """
    Tim kiem theo nguyen tac: tim chunk toan bo tai lieu -> tim va chon file chua noi dung
    va chunk lai toan bo file do de liet ke day du du lieu lien quan den cau hoi.
    """
    from app.db.mongo import get_db
    from bson import ObjectId

    db = get_db()
    try:
        company = db.companies.find_one({"_id": ObjectId(company_id)})
    except Exception:
        company = None
    if not company:
        return _response_payload(company_id, "Công ty không tồn tại.")

    all_chunks = search_and_chunk_entire_files(company, question, top_k=5)
    if not all_chunks:
        return _response_payload(company_id, "Không tìm thấy dữ liệu liên quan.")

    response_text = f"Tìm thấy {len(all_chunks)} chunks từ các file liên quan:\n\n"
    for i, chunk in enumerate(all_chunks[:20], 1):  # Limit to 20 for display
        file_name = chunk.get("file_name", "Unknown")
        response_text += f"{i}. **{file_name}**: {chunk['text'][:200]}...\n\n"

    if len(all_chunks) > 20:
        response_text += f"... và {len(all_chunks) - 20} chunks khác."

    return _response_payload(company_id, response_text)


def rag_answer(company_id: str, question: str, user_id: str):
    question = (question or "").strip()
    explicit_file_ref, cleaned_question = _extract_explicit_file_ref(question)
    if explicit_file_ref and cleaned_question:
        question = cleaned_question

    if not question:
        return _response_payload(company_id, "Vui lòng nhập câu hỏi.")

    if _is_greeting_message(question):
        if user_id:
            update_context(
                user_id,
                current_file=None,
                last_question=question,
            )
        return _response_payload(company_id, GREETING_AUTO_REPLY)

    normalized_question = _normalize_search_text(question)
    if _is_full_current_file_request(normalized_question):
        return rag_answer_with_full_file_chunks(company_id, question, user_id)

    verbatim_mode = _is_verbatim_request(question)
    current_file = get_current_file(user_id) if user_id else None

    queries = [question]
    if not verbatim_mode and USE_OLLAMA_ANALYSIS and _should_use_ollama_analysis(question):
        queries = _ollama_extract_queries(question) or [question]

    answers = []
    first_context = None
    first_source = None
    selected_file_for_context = explicit_file_ref or current_file

    for idx, query in enumerate(queries, start=1):
        file_ref = None
        best_hit = None
        hits = []
        matched_file = None
        matched_file_score = 0.0
        used_current_file_priority = False

        if explicit_file_ref:
            file_ref = explicit_file_ref
        else:
            matched_file = _find_mentioned_file(company_id, query)
            matched_file_score = float((matched_file or {}).get("score") or 0.0)

            if matched_file and matched_file_score >= 0.9:
                file_ref = matched_file.get("file_id") or matched_file.get("file_name")
            else:
                if current_file:
                    hits = search_within_file_with_sources(company_id, current_file, query, top_k=20)
                    if hits:
                        hits = _rerank_hits(query, hits, top_k=20)
                        if _has_strong_file_hits(hits):
                            best_hit = hits[0]
                            file_ref = current_file
                            used_current_file_priority = True

                if not file_ref:
                    hits = search_global_chunks(company_id, query, top_k=20)
                    if not hits:
                        hits = search_vectors_with_sources(company_id, query, top_k=20)
                if hits:
                    if not best_hit:
                        hits = _rerank_hits(query, hits, top_k=20)
                        best_hit = hits[0]
                    file_ref = file_ref or best_hit.get("file_id") or best_hit.get("file_name")

        if not file_ref:
            answers.append(f"{idx}. Không tìm thấy tài liệu phù hợp.")
            continue

        print("===== RAG QUERY DEBUG =====")
        print(f"QUERY_INDEX: {idx}")
        print(f"QUERY_TEXT: {query}")
        print(f"FILE_REF: {file_ref}")
        print(f"CURRENT_FILE: {current_file}")
        print(f"USED_CURRENT_FILE_PRIORITY: {used_current_file_priority}")
        if matched_file and matched_file_score > 0:
            print(f"MATCHED_FILE_NAME: {matched_file.get('file_name')}")
            print(f"MATCHED_FILE_SCORE: {matched_file_score}")
        if best_hit:
            print(f"BEST_HIT_HEADING: {best_hit.get('heading_path') or best_hit.get('heading')}")
            print(f"BEST_HIT_SCORE: {best_hit.get('rerank_score') or best_hit.get('final_score') or best_hit.get('score')}")
        print("===========================")

        if verbatim_mode:
            from app.db.mongo import get_db
            from bson import ObjectId

            db = get_db()
            try:
                company = db.companies.find_one({"_id": ObjectId(company_id)})
            except Exception:
                company = None
            if not company:
                answers.append(f"{idx}. Không tìm thấy thông tin trong tài liệu.")
                continue

            chunks, resolved_file_id, resolved_file_name = _load_verbatim_chunks_from_drive(
                company,
                company_id,
                file_ref,
                best_hit,
            )
            if not chunks:
                answers.append(f"{idx}. Không tìm thấy nội dung nguyên văn trong tài liệu.")
                continue

            answer_text = "\n\n".join([c for c in chunks if c and str(c).strip()]).strip()
            if not answer_text:
                answers.append(f"{idx}. Không tìm thấy nội dung nguyên văn trong tài liệu.")
                continue

            if first_context is None:
                first_context = _build_context_from_chunks(chunks)
                source_ref = resolved_file_id or resolved_file_name or file_ref
                first_source = _build_source_override(company_id, source_ref, chunks, hits if hits else None)
            selected_file_for_context = resolved_file_id or resolved_file_name or file_ref

            answers.append(f"{idx}. {answer_text}" if len(queries) > 1 else answer_text)
            continue

        full_file_context, resolved_file_id, resolved_file_name = _load_full_file_text(
            company_id,
            file_ref,
            best_hit,
        )
        if not full_file_context:
            full_file_context = _build_full_file_context(company_id, file_ref)

        if full_file_context:
            if matched_file and matched_file_score >= 0.9:
                if first_context is None:
                    first_context = full_file_context
                    source_ref = resolved_file_id or resolved_file_name or file_ref
                    first_source = _build_source_override(company_id, source_ref, [full_file_context], None)
                selected_file_for_context = resolved_file_id or resolved_file_name or file_ref

                answer_text = generate_preserve_markdown(full_file_context)
                answers.append(f"{idx}. {answer_text}" if len(queries) > 1 else answer_text)
                continue

            heading_context, selected_heading = _select_heading_subtree_context(
                full_file_context,
                query,
                hits,
                max_sections=12,
            )
            heading_subtree_chunks = _collect_heading_subtree_chunks(
                company_id,
                resolved_file_id or file_ref,
                selected_heading,
                full_file_context,
            )
            heading_subtree_context = _reconstruct_full_text(heading_subtree_chunks)
            if heading_subtree_context:
                heading_subtree_context = "\n".join(
                    _collapse_redundant_blocks(heading_subtree_context.splitlines())
                ).strip()
            context_for_ollama, selected_context_type, context_scores = _select_best_context_for_query(
                query,
                full_file_context,
                heading_context,
                heading_subtree_context,
            )

            print("===== HEADING DEBUG =====")
            print(f"SELECTED_HEADING: {selected_heading}")
            print(f"SELECTED_CONTEXT_TYPE: {selected_context_type}")
            print(f"FULL_FILE_CONTEXT_LEN: {len(full_file_context or '')}")
            print(f"HEADING_CONTEXT_LEN: {len(heading_context or '')}")
            print(f"SUBTREE_CHUNKS: {len(heading_subtree_chunks)}")
            print(f"SUBTREE_CONTEXT_LEN: {len(heading_subtree_context or '')}")
            print(f"CONTEXT_SCORES: {context_scores}")
            print("SUBTREE_CONTEXT_PREVIEW:")
            print((context_for_ollama or "")[:4000])
            print("=========================")

            if first_context is None:
                first_context = context_for_ollama
                source_ref = resolved_file_id or resolved_file_name or file_ref
                first_source = _build_source_override(company_id, source_ref, [context_for_ollama], hits if hits else None)
            selected_file_for_context = resolved_file_id or resolved_file_name or file_ref

            if selected_context_type == "full_file" and _is_document_title_lookup(
                query,
                resolved_file_name or file_ref,
                full_file_context,
            ):
                answer_text = generate_preserve_markdown(full_file_context)
                answers.append(f"{idx}. {answer_text}" if len(queries) > 1 else answer_text)
                continue

            answer_text = generate_final_answer(query, context_for_ollama)
            if not _is_ollama_error(answer_text):
                answers.append(f"{idx}. {answer_text}" if len(queries) > 1 else answer_text)
                continue
            print(
                f"[rag] Ollama failed on heading context for file={file_ref}, heading={selected_heading}, fallback to section mode"
            )

        related = _expand_sections_from_hits(hits, file_ref, top_sections=3)

        if not related:
            full_chunks = _rechunk_file_content(company_id, file_ref)
            ranked = _rank_chunks_from_list(full_chunks, file_ref, query, top_k=max(12, len(full_chunks)))
            related = [item.get("text", "") for item in ranked if item.get("text")]
            related = _dedupe_chunks(related)

        if not related and best_hit:
            text = best_hit.get("section_text") or best_hit.get("text")
            if text:
                related = [text]

        if not related:
            answers.append(f"{idx}. Không tìm thấy thông tin trong tài liệu.")
            continue

        if first_context is None:
            first_context = _build_context_from_chunks(related)
            first_source = _build_source_override(company_id, file_ref, related, hits if hits else None)
        selected_file_for_context = file_ref

        answer_text = _final_answer_from_chunks(query, related)
        answers.append(f"{idx}. {answer_text}" if len(queries) > 1 else answer_text)

    final_answer = "\n\n".join(answers).strip() if answers else "Không tìm thấy thông tin trong tài liệu."

    if verbatim_mode:
        final_answer = _format_with_ollama(question, final_answer)

    if user_id:
        update_context(
            user_id,
            current_file=selected_file_for_context,
            last_question=question,
        )

    return _response_payload(
        company_id,
        final_answer,
        explicit_file_ref,
        context=first_context,
        source_override=first_source,
    )

