from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # heavy imports stay lazy so the cloud job installs without torch
    from sentence_transformers import SentenceTransformer


@dataclass(frozen=True)
class EmbeddingResult:
    """
    Result of embedding one or more texts.
    """

    vectors: list[list[float]]
    model: str
    dimension: int


class Embedder:
    """
    Local text embedder.

    The rest of the application should depend on this class rather than
    importing SentenceTransformer directly.
    """

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        *,
        dimension: int = 384,
        normalize: bool = True,
    ) -> None:
        self.model_name = model_name
        self.expected_dimension = dimension
        self.normalize = normalize
        self._model: SentenceTransformer | None = None
        self._model_lock = threading.Lock()
    def _get_model(self) -> SentenceTransformer:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._model = _load_model(self.model_name)
        return self._model
    def warm(self) -> None:
        self._get_model()
    @property
    def dimension(self) -> int:
        dimension = self._get_model().get_embedding_dimension()

        if dimension != self.expected_dimension:
            raise ValueError(
                f"embedding dimension mismatch: "
                f"configured={self.expected_dimension}, model={dimension}"
            )

        return dimension

    def embed(self, text: str) -> list[float]:
        """
        Embed a single text.
        """

        if not isinstance(text, str):
            raise TypeError("text must be a string")

        if not text.strip():
            raise ValueError("text must not be empty")

        import numpy as np

        vector = self._get_model().encode(
            text,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
        )

        return vector.astype(np.float32).tolist()

    def embed_many(self, texts: list[str],*, batch_size: int = 8) -> EmbeddingResult:
        """
        Embed multiple texts in one batch.
        """

        if not texts:
            return EmbeddingResult(
                vectors=[],
                model=self.model_name,
                dimension=self.dimension,
            )

        if any(not isinstance(text, str) for text in texts):
            raise TypeError("all texts must be strings")

        if any(not text.strip() for text in texts):
            raise ValueError("texts must not contain empty strings")

        import numpy as np

        vectors = self._get_model().encode(
            texts,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
            batch_size=batch_size,
        )

        vectors = np.asarray(vectors, dtype=np.float32)

        return EmbeddingResult(
            vectors=vectors.tolist(),
            model=self.model_name,
            dimension=vectors.shape[1],
        )


@lru_cache(maxsize=4)
def _load_model(model_name: str) -> SentenceTransformer:
    """
    Load each configured model once per process.

    Cached separately so experiments with another model name don't
    require changing the Embedder implementation.
    """

    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)