"""Text -> vector, on this machine, for "find things that mean the same".

Uses bge-small-en-v1.5 through fastembed (ONNX, no GPU). The model is
downloaded once into backend/.models (about 65 MB) and then works offline.

Vectors are normalised, so the cosine similarity of two of them is just their
dot product. They are stored in MySQL as raw float32 bytes (384 * 4 = 1,536
bytes each).
"""

import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("chatbot.embeddings")

DIMENSIONS = 384
MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODELS_DIR = Path(__file__).resolve().parent / ".models"

_model = None
_model_lock = threading.Lock()


def _backend():
    return os.getenv("EMBEDDINGS", "local")


def _load():
    global _model
    with _model_lock:
        if _model is None:
            from fastembed import TextEmbedding

            started = time.monotonic()
            MODELS_DIR.mkdir(exist_ok=True)
            _model = TextEmbedding(MODEL_NAME, cache_dir=str(MODELS_DIR))
            log.info("Embedding model loaded in %.1fs", time.monotonic() - started)
    return _model


def warm_up():
    """Load the model in the background so the first search isn't slow."""
    if _backend() == "local":
        threading.Thread(target=_load, name="embeddings-warm-up", daemon=True).start()


def _normalise(vectors):
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return vectors / norms


def _fake(texts):
    """Tests only: a bag-of-words hash, so shared words mean similar vectors."""
    out = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
    for row, text in enumerate(texts):
        for word in re.findall(r"\w+", text.lower()):
            if len(word) > 2:
                out[row, int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1
    return _normalise(out)


def embed_documents(texts):
    """Vectors for stored text (messages, facts). Shape (len(texts), 384)."""
    if not texts:
        return np.zeros((0, DIMENSIONS), dtype=np.float32)
    if _backend() == "fake":
        return _fake(texts)
    # One text at a time: on this 2-core machine that is about twice as fast as
    # batching, because batches pad every text to the longest one.
    return _normalise(list(_load().embed([t[:4000] for t in texts], batch_size=1)))


def embed_query(text):
    """Vector for a search query. bge uses a slightly different form for queries."""
    if _backend() == "fake":
        return _fake([text])[0]
    return _normalise(list(_load().query_embed([text[:2000]])))[0]


def to_bytes(vector):
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_bytes(blob):
    return np.frombuffer(blob, dtype=np.float32)


def rank(query_vector, blobs):
    """Cosine similarity of the query with each stored vector (None -> -1)."""
    scores = np.full(len(blobs), -1.0, dtype=np.float32)
    rows = [i for i, b in enumerate(blobs) if b]
    if rows:
        matrix = np.stack([from_bytes(blobs[i]) for i in rows])
        scores[rows] = matrix @ np.asarray(query_vector, dtype=np.float32)
    return scores
