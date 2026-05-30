from typing import Optional

chat_memory = {}


def save_context(
    user_id: str,
    file_list: list,
    current_index: int = 0,
    current_file: Optional[str] = None,
    last_question: Optional[str] = None,
    current_topic: Optional[str] = None,
    last_intent: Optional[str] = None,
):
    if current_file is None and file_list and 0 <= current_index < len(file_list):
        current_file = file_list[current_index]

    chat_memory[user_id] = {
        "files": file_list,
        "index": current_index,
        "current_file": current_file,
        "last_question": last_question,
        "current_topic": current_topic,
        "last_intent": last_intent,
    }


def get_context(user_id: str):
    return chat_memory.get(user_id)


def get_current_file(user_id: str):
    context = chat_memory.get(user_id)
    if not context:
        return None
    return context.get("current_file")


def get_current_topic(user_id: str):
    context = chat_memory.get(user_id)
    if not context:
        return None
    return context.get("current_topic")


def get_last_intent(user_id: str):
    context = chat_memory.get(user_id)
    if not context:
        return None
    return context.get("last_intent")


def set_last_question(user_id: str, question: str):
    context = chat_memory.setdefault(user_id, {})
    context["last_question"] = question


def update_context(user_id: str, **fields):
    context = chat_memory.setdefault(user_id, {})
    context.update({k: v for k, v in fields.items() if v is not None})


def get_last_question(user_id: str):
    context = chat_memory.get(user_id)
    if not context:
        return None
    return context.get("last_question")


def next_file(user_id: str):
    context = chat_memory.get(user_id)

    if not context:
        return None

    context["index"] += 1

    if context["index"] >= len(context["files"]):
        return None

    context["current_file"] = context["files"][context["index"]]
    return context["current_file"]


def clear_context(user_id: str):
    chat_memory.pop(user_id, None)
