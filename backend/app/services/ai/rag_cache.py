import numpy as np

CACHE = []

SIM_THRESHOLD = 0.90
MAX_CACHE = 1000
MODEL = None


def _get_model():
    global MODEL
    if MODEL is None:
        from sentence_transformers import SentenceTransformer

        MODEL = SentenceTransformer("BAAI/bge-m3")
    return MODEL


def _cosine(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def get_cached_answer(question: str):
    if not CACHE:
        return None

    q_vec = _get_model().encode(question, normalize_embeddings=True)

    best = None
    best_score = 0

    for item in CACHE:
        score = _cosine(q_vec, item["embedding"])

        if score > best_score:
            best_score = score
            best = item

    if best_score >= SIM_THRESHOLD:
        return best["answer"]

    return None


def add_cache(question: str, answer: str):
    emb = _get_model().encode(question, normalize_embeddings=True)

    CACHE.append(
        {
            "question": question,
            "embedding": emb,
            "answer": answer,
        }
    )

    if len(CACHE) > MAX_CACHE:
        CACHE.pop(0)
