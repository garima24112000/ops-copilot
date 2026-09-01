"""search_runbooks: hybrid BM25 + ELSER retrieval via the Elasticsearch RRF retriever, over
ops-runbooks. Returns top-3 TRUNCATED snippets (not full documents) -- this is the single
biggest lever on the project's 4-5K-tokens-per-run budget (CLAUDE.md)."""

from __future__ import annotations

from typing import Any

from elasticsearch import Elasticsearch

SNIPPET_CHARS = 600
TOP_K = 3
RANK_WINDOW_SIZE = 50
RANK_CONSTANT = 60


def search_runbooks(es: Elasticsearch, query: str, service: str | None = None) -> list[dict[str, Any]]:
    bm25_query: dict[str, Any] = {"multi_match": {"query": query, "fields": ["title^2", "body"]}}
    semantic_query: dict[str, Any] = {"semantic": {"field": "body_semantic", "query": query}}
    if service:
        bm25_query = {"bool": {"must": [bm25_query], "filter": [{"term": {"service": service}}]}}
        semantic_query = {"bool": {"must": [semantic_query], "filter": [{"term": {"service": service}}]}}

    body = {
        "retriever": {
            "rrf": {
                "retrievers": [
                    {"standard": {"query": bm25_query}},
                    {"standard": {"query": semantic_query}},
                ],
                "rank_window_size": RANK_WINDOW_SIZE,
                "rank_constant": RANK_CONSTANT,
            }
        },
        "size": TOP_K,
        "_source": ["id", "title", "body", "service", "department", "source_url"],
    }
    resp = es.search(index="ops-runbooks", body=body)
    results = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        body_text = src["body"]
        results.append(
            {
                "id": src["id"],
                "title": src["title"],
                "snippet": body_text[:SNIPPET_CHARS] + ("..." if len(body_text) > SNIPPET_CHARS else ""),
                "service": src["service"],
                "department": src["department"],
                "source_url": src["source_url"],
                "score": hit["_score"],
            }
        )
    return results
