import faiss
import numpy as np

model = None
dimension = 1024  # bge-m3
index = faiss.IndexFlatL2(dimension)
metadata_store = []


def _get_model():
    global model
    if model is None:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("BAAI/bge-m3")
    return model


def embed_and_store(company_id, file_id, chunks):
    embeddings = _get_model().encode(chunks)

    index.add(np.array(embeddings).astype("float32"))

    for i, chunk in enumerate(chunks):
        metadata_store.append(
            {
                "company_id": company_id,
                "file_id": file_id,
                "chunk_text": chunk,
            }
        )
