import json
import re

import requests
from requests.exceptions import RequestException, Timeout

from app.core.config import settings

LARGE_CONTEXT_THRESHOLD = 12000
CONTEXT_WINDOW_CHARS = 6000
MAX_CONTEXT_PARTS = 12

SYSTEM_PROMPT = """
Bạn là hệ thống chỉnh sửa văn bản.

NHIỆM VỤ:

- Giữ nguyên nội dung gốc.
- Loại bỏ các phần thừa, lặp lại hoặc không cần thiết.
- Chỉnh lại câu chữ và định dạng để dễ đọc hơn.

QUY TẮC BẮT BUỘC:

1. Không được thêm bất kỳ thông tin mới nào.
2. Không suy luận.
3. Không thay đổi ý nghĩa của văn bản.
4. Không trả lời câu hỏi.
5. Không tóm tắt nội dung.
6. Chỉ sắp xếp, chưa dùng Markdown.

Chỉ chỉnh sửa lại văn bản để:
- gọn hơn
- rõ ràng hơn
- dễ đọc hơn.
"""


def build_edit_prompt(text: str) -> str:
    return f"""
{SYSTEM_PROMPT}

Văn bản gốc:

{text}

Hãy chỉnh lại văn bản.
"""


def build_markdown_preserve_prompt(text: str) -> str:
    return f"""
Bạn là hệ thống định dạng Markdown.

NHIỆM VỤ:
- Chỉ làm đẹp Markdown cho dễ đọc hơn.
- Giữ nguyên toàn bộ nội dung, câu chữ và thứ tự ý.
- Chỉ tô đậm những từ khóa chính.
- Đồng nhất cách hiển thị tiêu đề, nội dung và danh sách ý con.
- Xuống dòng rõ ràng cho các ý con, tiểu mục, gạch đầu dòng.

QUY TẮC BẮT BUỘC:
1. Không thêm thông tin mới.
2. Không bỏ nội dung.
3. Không đổi ý nghĩa.
4. Không viết lại câu theo cách khác.
5. Chỉ được thêm ký hiệu Markdown như tiêu đề, danh sách, xuống dòng, in đậm.
6. Không được thay đổi bất kỳ từ nào trong nội dung gốc, ngoại trừ việc bao quanh bằng ký hiệu Markdown.
7. Giữ cách trình bày đồng nhất toàn văn bản: cùng cấp tiêu đề thì cùng kiểu Markdown, các ý con phải được tách dòng dễ đọc.
8. Nếu gặp chuỗi liệt kê dài trên một dòng, hãy tách thành các ý con bằng Markdown nhưng không đổi chữ.
9. Chỉ dùng in đậm cho từ khóa ngắn hoặc cụm từ khóa quan trọng.
10. Không in đậm cả câu, cả đoạn, hoặc toàn bộ tiêu đề nếu không thật sự là từ khóa.

Văn bản đầu vào:

{text}

Hãy làm đẹp Markdown, đồng nhất định dạng, chỉ tô đậm từ khóa chính và xuống dòng các ý con, nhưng giữ nguyên nội dung.
"""


def clean_text(text: str) -> str:
    text = (text or "").replace("\r", "")
    text = re.sub(r"```markdown", "", text)
    text = re.sub(r"```", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_dedupe_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _dedupe_text_blocks(text: str) -> str:
    blocks = re.split(r"\n{2,}", (text or "").strip())
    kept = []
    seen = set()

    for block in blocks:
        cleaned_block = block.strip()
        if not cleaned_block:
            continue
        key = _normalize_dedupe_key(cleaned_block)
        if key in seen:
            continue
        seen.add(key)
        kept.append(cleaned_block)

    return "\n\n".join(kept).strip()


def _remove_trailing_meta_sections(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned

    trailing_markers = [
        "\nKhông tìm thấy thông tin trong tài liệu về:",
        "\nMở tài liệu:",
    ]

    cut_positions = [cleaned.find(marker) for marker in trailing_markers if cleaned.find(marker) > 0]
    if cut_positions:
        cleaned = cleaned[: min(cut_positions)].rstrip()

    return cleaned


def auto_markdown(text: str) -> str:
    text = re.sub(r"\n(I|II|III|IV|V|VI|VII|VIII|IX|X)\.", r"\n## \1.", text)
    text = re.sub(r"\n(\d+)\.", r"\n### \1.", text)
    return text


def _strip_markdown_tokens(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"```[\s\S]*?```", " ", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"(\*\*|__)(.*?)\1", r"\2", cleaned)
    cleaned = re.sub(r"(\*|_)(.*?)\1", r"\2", cleaned)
    cleaned = re.sub(r"^[#>\-\*\+\d\.\)\s]+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _markdown_preserves_content(formatted: str, original: str) -> bool:
    normalized_formatted = _normalize_dedupe_key(_strip_markdown_tokens(formatted))
    normalized_original = _normalize_dedupe_key(_strip_markdown_tokens(original))
    if not normalized_formatted or not normalized_original:
        return False

    return (
        normalized_formatted == normalized_original
        or normalized_formatted in normalized_original
        or normalized_original in normalized_formatted
    )


def remove_summary(text: str) -> str:
    banned = [
        "dưới đây là",
        "sau đây là",
        "tóm tắt nội dung",
        "tài liệu cho biết rằng",
    ]
    lowered = (text or "").lower()
    for item in banned:
        if item in lowered:
            return ""
    return text


def remove_non_vietnamese(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text or ""):
        return ""
    return text


def _is_ollama_error(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith("lỗi ollama:")


def keep_verbatim(answer: str, context: str) -> str:
    if not answer or not context:
        return "Không tìm thấy thông tin trong tài liệu."

    def norm(value: str) -> str:
        return re.sub(r"\s+", " ", value.lower()).strip()

    ctx_lines = [norm(line) for line in context.splitlines() if line.strip()]
    kept = []

    for line in answer.splitlines():
        stripped = line.strip()
        if not stripped:
            if kept and kept[-1] != "":
                kept.append("")
            continue

        plain = re.sub(r"^[-*\d\.\)\s#]+", "", stripped)
        normalized_line = norm(plain)
        if normalized_line and any(normalized_line in ctx for ctx in ctx_lines):
            kept.append(stripped)

    result = "\n".join(kept).strip()
    if not result:
        return "Không tìm thấy thông tin trong tài liệu."
    return result


def _preserve_grounded_answer(answer: str, context: str) -> str:
    filtered = keep_verbatim(answer, context)
    if not filtered or filtered == "Không tìm thấy thông tin trong tài liệu.":
        return answer

    filtered_lines = [line.strip() for line in filtered.splitlines() if line.strip()]
    answer_lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if len(filtered_lines) >= max(1, len(answer_lines) // 2):
        return filtered
    return answer


def call_ollama(prompt: str, timeout_seconds: int, context_tokens: list[int] | None = None):
    payload = {
        "model": settings.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "top_p": 0.2,
            "num_predict": 2048,
        },
    }
    if context_tokens:
        payload["context"] = context_tokens

    return requests.post(
        settings.OLLAMA_URL,
        json=payload,
        timeout=timeout_seconds,
    )


def _split_long_context(context: str, max_chars: int = CONTEXT_WINDOW_CHARS) -> list[str]:
    normalized = (context or "").strip()
    if not normalized:
        return []

    paragraphs = re.split(r"\n{2,}", normalized)
    chunks = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)

        if len(paragraph) <= max_chars:
            current = paragraph
            continue

        start = 0
        while start < len(paragraph):
            part = paragraph[start:start + max_chars].strip()
            if part:
                chunks.append(part)
            start += max_chars
        current = ""

    if current:
        chunks.append(current)

    return chunks


def _finalize_grounded_answer(answer: str, context: str) -> str:
    raw_answer = answer
    answer = clean_text(answer)
    answer = remove_non_vietnamese(answer)
    answer = remove_summary(answer)
    answer = _remove_trailing_meta_sections(answer)
    answer = _dedupe_text_blocks(answer)
    preserved_answer = answer
    print("===== FINALIZE DEBUG =====")
    print(f"RAW_ANSWER_LEN: {len(raw_answer or '')}")
    print(f"CLEANED_ANSWER_LEN: {len(answer or '')}")
    print(f"PRESERVED_ANSWER_LEN: {len(preserved_answer or '')}")
    print("PRESERVED_ANSWER_PREVIEW:")
    print((preserved_answer or "")[:2000])
    print("==========================")
    answer = preserved_answer
    return answer


def _build_document_qa_prompt(question: str, context: str) -> str:
    return f"""
CÂU HỎI:
{question}

Bạn đang nhận TOÀN BỘ nội dung text của một file theo ĐÚNG THỨ TỰ GỐC trong tài liệu.
Hãy đọc nội dung file và tìm dữ liệu để trả lời câu hỏi.

NỘI DUNG FILE:

{context}

YÊU CẦU:
- Chỉ được dựa trên nội dung của file ở trên
- Ưu tiên tìm đúng đoạn dữ liệu liên quan trong file
- Nếu có thông tin thì trích ra đúng nội dung phù hợp trong file
- Không suy luận thêm ngoài file
- Không tự viết thêm kiến thức bên ngoài
- Nếu không tìm thấy thông tin thì trả lời đúng:
"Không tìm thấy thông tin trong tài liệu."
"""


def _build_final_polish_prompt(question: str, context: str) -> str:
    context = _dedupe_text_blocks(context)
    return f"""
CÂU HỎI:
{question}

Bạn đang ở bước cuối để biên tập nội dung đã được trích sẵn từ tài liệu.

NỘI DUNG ĐÃ TRÍCH TỪ TÀI LIỆU:

{context}

YÊU CẦU BẮT BUỘC:
- Chỉ được dùng nội dung đã có trong phần "NỘI DUNG ĐÃ TRÍCH TỪ TÀI LIỆU".
- Nhiệm vụ của bạn chỉ là lọc bỏ đoạn lặp, câu lặp, ý lặp và sắp xếp lại cho dễ đọc.
- Không được bổ sung thông tin mới.
- Không suy luận.
- Không đưa kiến thức bên ngoài vào câu trả lời.
- Nếu có nhiều ý liên quan, trình bày gọn gàng bằng tiếng Việt, có thể xuống dòng hoặc đánh số.
- Nếu không có thông tin phù hợp thì trả lời đúng:
"Không tìm thấy thông tin trong tài liệu."
"""


def _generate_final_answer_single_call(question: str, context: str) -> str:
    prompt = _build_final_polish_prompt(question, context)

    try:
        response = call_ollama(prompt, settings.OLLAMA_TIMEOUT_SECONDS)
    except Timeout:
        return "Lỗi Ollama: request timeout."
    except RequestException as exc:
        return f"Lỗi Ollama: không kết nối được ({exc})"

    if response.status_code != 200:
        return f"Lỗi Ollama: {response.text}"

    data = response.json()
    answer = str(data.get("response", "")).strip()

    print("===== CONTEXT =====")
    print(f"CONTEXT_LEN: {len(context or '')}")
    print("CONTEXT_HEAD:")
    print((context or "")[:2000])
    if len(context or "") > 2200:
        print("----- CONTEXT_TAIL -----")
        print((context or "")[-1200:])
    print("===================")
    print("===== OLLAMA RAW OUTPUT =====")
    print(answer)
    print("==============================")

    return _finalize_grounded_answer(answer, context)


def generate_final_answer_large_context(question: str, context: str) -> str:
    context = (context or "").strip()
    if not context:
        return "Không tìm thấy thông tin trong tài liệu."

    parts = _split_long_context(context)
    if len(parts) <= 1:
        return _generate_final_answer_single_call(question, context)

    if len(parts) > MAX_CONTEXT_PARTS:
        merged_parts = []
        chunk_size = max(1, (len(parts) + MAX_CONTEXT_PARTS - 1) // MAX_CONTEXT_PARTS)
        for start in range(0, len(parts), chunk_size):
            merged = "\n\n".join(parts[start:start + chunk_size]).strip()
            if merged:
                merged_parts.append(merged)
        parts = merged_parts

    session_context = None
    try:
        intro_prompt = """
Bạn sẽ nhận lần lượt nhiều phần của cùng một tài liệu.
Hãy ghi nhớ nội dung đã nhận.
Chưa trả lời câu hỏi.
Sau mỗi phần, chỉ trả lời đúng: ĐÃ_NHẬN
"""
        response = call_ollama(intro_prompt, settings.OLLAMA_TIMEOUT_SECONDS)
        if response.status_code != 200:
            return f"Lỗi Ollama: {response.text}"

        data = response.json()
        session_context = data.get("context")

        for index, part in enumerate(parts, start=1):
            ingest_prompt = f"""
Đây là phần {index}/{len(parts)} của tài liệu.
Hãy ghi nhớ nội dung này để trả lời ở bước cuối.
Không tóm tắt, không giải thích.
Chỉ trả lời đúng: ĐÃ_NHẬN

NỘI DUNG:
{part}
"""
            response = call_ollama(
                ingest_prompt,
                settings.OLLAMA_TIMEOUT_SECONDS,
                context_tokens=session_context,
            )
            if response.status_code != 200:
                return f"Lỗi Ollama: {response.text}"

            data = response.json()
            session_context = data.get("context")

        final_prompt = f"""
CÂU HỎI:
{question}

Bạn đã nhận đầy đủ các phần của cùng một tài liệu.
Đây là bước cuối để lọc nội dung trùng lặp và trình bày lại phần thông tin liên quan đến câu hỏi.

YÊU CẦU BẮT BUỘC:
- Chỉ được dựa trên nội dung tài liệu đã nhận.
- Chỉ trích các ý trực tiếp liên quan đến câu hỏi.
- Loại bỏ đoạn lặp, câu lặp, ý lặp.
- Sắp xếp lại cho gọn, rõ, dễ đọc.
- Không thêm thông tin mới.
- Không suy luận.
- Nếu không có thông tin thì trả lời đúng:
"Không tìm thấy thông tin trong tài liệu."
"""
        response = call_ollama(
            final_prompt,
            settings.OLLAMA_TIMEOUT_SECONDS,
            context_tokens=session_context,
        )
        if response.status_code != 200:
            return f"Lỗi Ollama: {response.text}"

        data = response.json()
        answer = str(data.get("response", "")).strip()
        return _finalize_grounded_answer(answer, context)
    except Timeout:
        return "Lỗi Ollama: request timeout."
    except RequestException as exc:
        return f"Lỗi Ollama: không kết nối được ({exc})"


def stream_ollama(prompt: str):
    response = requests.post(
        settings.OLLAMA_URL,
        json={
            "model": settings.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": True,
        },
        stream=True,
    )

    for line in response.iter_lines():
        if not line:
            continue
        try:
            data = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        token = data.get("response", "")
        if token:
            yield token


def generate_final_answer(question: str, context: str):
    context = (context or "").strip()
    if len(context) > LARGE_CONTEXT_THRESHOLD:
        return generate_final_answer_large_context(question, context)
    return _generate_final_answer_single_call(question, context)


def generate_edit_markdown(text: str):
    prompt = build_edit_prompt(text)
    try:
        response = call_ollama(prompt, settings.OLLAMA_TIMEOUT_SECONDS)
    except Timeout:
        return "Lỗi Ollama: request timeout."
    except RequestException as exc:
        return f"Lỗi Ollama: không kết nối được ({exc})"

    if response.status_code != 200:
        return f"Lỗi Ollama: {response.text}"

    data = response.json()
    answer = str(data.get("response", "")).strip()
    answer = clean_text(answer)
    answer = auto_markdown(answer)
    return answer


def generate_preserve_markdown(text: str):
    original = clean_text(text)
    base_markdown = auto_markdown(original)
    prompt = build_markdown_preserve_prompt(base_markdown)
    try:
        response = call_ollama(prompt, settings.OLLAMA_TIMEOUT_SECONDS)
    except Timeout:
        return base_markdown
    except RequestException:
        return base_markdown

    if response.status_code != 200:
        return base_markdown

    data = response.json()
    answer = str(data.get("response", "")).strip()
    answer = clean_text(answer)
    if not answer:
        return base_markdown

    if not _markdown_preserves_content(answer, base_markdown):
        return base_markdown

    return answer


def generate_answer(prompt: str):
    try:
        response = call_ollama(prompt, settings.OLLAMA_TIMEOUT_SECONDS)
    except Timeout:
        return "Lỗi Ollama: request timeout."
    except RequestException as exc:
        return f"Lỗi Ollama: không kết nối được ({exc})"

    if response.status_code != 200:
        return f"Lỗi Ollama: {response.text}"

    data = response.json()
    answer = str(data.get("response", "")).strip()
    answer = clean_text(answer)
    return answer
