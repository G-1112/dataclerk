"""retrieve_policy 工具（FR-A05）：政策文档 RAG 检索。

默认纯 Python 字符级 bigram TF-IDF（零依赖、确定性、离线可用），
可选 ChromaDB + bge-small-zh 升级。可测失效模式：召回不准、忠实度低。
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

POLICIES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "policies"


class _Snippet:
    __slots__ = ("doc_id", "title", "section", "text", "vector")

    def __init__(self, doc_id: str, title: str, section: str, text: str) -> None:
        self.doc_id = doc_id
        self.title = title
        self.section = section
        self.text = text
        self.vector = _bigrams(text)


def _bigrams(text: str) -> Counter:
    chars = [c for c in text if not c.isspace()]
    return Counter("".join(chars[i : i + 2]) for i in range(len(chars) - 1))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(v * big.get(k, 0) for k, v in small.items())
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


class PolicyRetriever:
    def __init__(self, policies_dir: str | Path | None = None) -> None:
        self.dir = Path(policies_dir or POLICIES_DIR)
        self._snippets: list[_Snippet] | None = None

    def load(self) -> list[_Snippet]:
        if self._snippets is not None:
            return self._snippets
        snippets: list[_Snippet] = []
        for path in sorted(self.dir.glob("*.txt")):
            title = path.stem
            text = path.read_text(encoding="utf-8")
            # 按段落切片，每片带 section 元数据
            for i, para in enumerate(text.split("\n")):
                para = para.strip()
                if not para:
                    continue
                snippets.append(
                    _Snippet(f"{title}#{i}", title, f"第{i + 1}段", para)
                )
        self._snippets = snippets
        return snippets

    def retrieve(self, query: str, top_k: int = 3) -> list[str]:
        snippets = self.load()
        if not snippets:
            return []
        qvec = _query_vector(query)
        scored = sorted(((_cosine(qvec, s.vector), s) for s in snippets), key=lambda x: -x[0])
        return [f"[{s.title} · {s.section}] {s.text}" for score, s in scored[:top_k] if score > 0.02]

    def retrieve_with_source(self, query: str, top_k: int = 3) -> list[dict]:
        snippets = self.load()
        if not snippets:
            return []
        qvec = _query_vector(query)
        scored = sorted(((_cosine(qvec, s.vector), s) for s in snippets), key=lambda x: -x[0])
        return [
            {"doc_id": s.doc_id, "title": s.title, "section": s.section, "text": s.text, "score": round(score, 4)}
            for score, s in scored[:top_k] if score > 0.02
        ]


retriever = PolicyRetriever()

# 查询向量缓存：同一 query 重复出现时免去 bigram 重建（LRU 上限 256）。
_QUERY_VEC_CACHE: dict[str, Counter] = {}
_QUERY_VEC_CACHE_MAX = 256


def _query_vector(query: str) -> Counter:
    v = _QUERY_VEC_CACHE.get(query)
    if v is None:
        v = _bigrams(query)
        if len(_QUERY_VEC_CACHE) >= _QUERY_VEC_CACHE_MAX:
            _QUERY_VEC_CACHE.pop(next(iter(_QUERY_VEC_CACHE)))
        _QUERY_VEC_CACHE[query] = v
    return v


def retrieve_policy(query: str, top_k: int = 3) -> list[str]:
    """便捷函数：返回 Top-K 政策片段文本。"""
    return retriever.retrieve(query, top_k)
