from app.services.ai.vector_service import search_vectors
from app.services.ai.llm_service import generate_answer


def build_context(results):

    if not results:
        return "Không có dữ liệu liên quan."

    context_parts = []

    for r in results:
        if isinstance(r, dict):
            context_parts.append(r.get("content", ""))
        else:
            context_parts.append(str(r))

    return "\n\n".join(context_parts)


def chat_with_ai(company_id: str, question: str):

    print("\n===== USER QUESTION =====")
    print(question)
    print("=========================\n")

    results = search_vectors(company_id, question)

    print("VECTOR RESULTS:", len(results))

    context = build_context(results)

    prompt = f"""
TÀI LIỆU:

{context}

CÂU HỎI:
{question}

Hãy trả lời dựa hoàn toàn trên tài liệu.

Nếu không tìm thấy thông tin thì trả lời đúng câu:
"Không tìm thấy dữ liệu liên quan trong tài liệu."
"""

    answer = generate_answer(prompt)

    return answer