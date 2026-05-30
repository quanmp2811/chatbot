import re

def get_file_from_last_answer(last_answer: str):

    if not last_answer:
        return None

    match = re.search(r"Mở tài liệu:\s*(.+)", last_answer)

    if match:
        return match.group(1).strip()

    return None