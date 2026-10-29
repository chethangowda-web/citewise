"""Cross-encoder reranking (ONNX via fastembed, no PyTorch). Loaded lazily so tests and
deployments that leave reranking off never import or download anything."""
from functools import lru_cache

from app.config import get_settings


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name=model_name)

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, passages)]


@lru_cache
def get_reranker() -> CrossEncoderReranker:
    return CrossEncoderReranker(get_settings().rerank_model)
