import re

MAX_CHUNK_SIZE = 900
OVERLAP = 150

_HEADING_PATTERNS = (
    re.compile(r"^(phần|phan)\s+[ivxlcdm0-9]+[\.:]?\s+.+$", re.IGNORECASE),
    re.compile(r"^(chương|chuong)\s+[ivxlcdm0-9]+[\.:]?\s*.*$", re.IGNORECASE),
    re.compile(r"^(mục|muc)\s+[ivxlcdm0-9]+[\.:]?\s*.*$", re.IGNORECASE),
    re.compile(r"^(điều|dieu)\s+\d+[a-zA-Z\-]*[\.:]?\s*.*$", re.IGNORECASE),
    re.compile(r"^\d+(?:\.\d+){1,}[\)\.]?\s+.+$"),
    re.compile(r"^\d+[\)\.]\s+.+$"),
    re.compile(r"^[IVXLCDM]+[\)\.]\s+.+$"),
    re.compile(r"^[A-ZĐ][A-ZĐ0-9\s\-/,:]{3,}$"),
)


def _normalize_block(block: str) -> str:
    lines = [line.rstrip() for line in str(block or "").splitlines()]
    return "\n".join(lines).strip()


def _is_heading_line(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    return any(pattern.match(stripped) for pattern in _HEADING_PATTERNS)


def _heading_level(line: str) -> int:
    stripped = (line or "").strip()
    if not stripped:
        return 1

    if re.match(r"^(phần|phan)\s+", stripped, re.IGNORECASE):
        return 1
    if re.match(r"^(chương|chuong)\s+", stripped, re.IGNORECASE):
        return 2
    if re.match(r"^(mục|muc)\s+", stripped, re.IGNORECASE):
        return 3
    if re.match(r"^(điều|dieu)\s+", stripped, re.IGNORECASE):
        return 4

    numeric = re.match(r"^(\d+(?:\.\d+)*)([\)\.]?)\s+", stripped)
    if numeric:
        return max(1, numeric.group(1).count(".") + 2)

    if re.match(r"^[IVXLCDM]+[\)\.]\s+", stripped):
        return 2

    return 1


def split_legal_sections(text: str):
    normalized = _normalize_block(text)
    if not normalized:
        return []

    lines = normalized.splitlines()
    sections = []
    heading_stack = []
    current_heading = None
    current_lines = []

    def flush_section():
        nonlocal current_heading, current_lines
        body = "\n".join(current_lines).strip()
        if current_heading:
            section_text = f"{current_heading}\n{body}".strip() if body else current_heading
            heading_path = " > ".join(heading_stack) if heading_stack else current_heading
        else:
            section_text = body
            heading_path = None

        if section_text and section_text.strip():
            sections.append(
                {
                    "heading": current_heading,
                    "heading_path": heading_path,
                    "text": _normalize_block(section_text),
                }
            )

        current_heading = None
        current_lines = []

    for raw_line in lines:
        line = raw_line.strip()
        if _is_heading_line(line):
            flush_section()
            level = _heading_level(line)
            heading_stack[:] = heading_stack[: max(0, level - 1)]
            heading_stack.append(line)
            current_heading = line
            continue

        if not line and not current_lines:
            continue
        current_lines.append(raw_line.rstrip())

    flush_section()

    if len(sections) <= 1:
        paragraphs = re.split(r"\n{2,}", normalized)
        return [
            {
                "heading": None,
                "heading_path": None,
                "text": _normalize_block(paragraph),
            }
            for paragraph in paragraphs
            if paragraph and paragraph.strip()
        ]

    return sections


def split_text_with_headings(text: str):
    text = _normalize_block(text)
    if not text:
        return []

    sections = split_legal_sections(text)
    chunks = []
    position = 0

    for section_id, section in enumerate(sections):
        section_text = _normalize_block(section.get("text", ""))
        if not section_text:
            continue

        heading = section.get("heading")
        heading_path = section.get("heading_path")
        start = 0
        length = len(section_text)
        chunk_index = 0

        while start < length:
            end = start + MAX_CHUNK_SIZE
            chunk_text = section_text[start:end].strip()
            if chunk_text:
                chunks.append(
                    {
                        "text": chunk_text,
                        "section_text": section_text,
                        "heading": heading,
                        "heading_path": heading_path,
                        "section_id": section_id,
                        "chunk_in_section": chunk_index,
                        "position": position,
                    }
                )
                position += 1
                chunk_index += 1

            if length <= MAX_CHUNK_SIZE:
                break
            start += MAX_CHUNK_SIZE - OVERLAP

    return chunks


def split_text(text: str):
    return [item["text"] for item in split_text_with_headings(text)]
