"""find_similar_incidents: semantic search over ops-incidents, grounding the agent in past
resolutions (Elasticsearch as memory -- the postmortem loop writes back into this same shape
of index via ops-postmortems / record())."""

from __future__ import annotations

from typing import Any

from elasticsearch import Elasticsearch

TOP_K = 3
SUMMARY_CHARS = 400


def find_similar_incidents(
    es: Elasticsearch, summary: str, service: str | None = None
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"semantic": {"field": "summary_semantic", "query": summary}}
    if service:
        query = {"bool": {"must": [query], "filter": [{"term": {"service": service}}]}}

    resp = es.search(
        index="ops-incidents",
        query=query,
        size=TOP_K,
        source_includes=["id", "title", "summary", "resolution", "service", "severity", "related_runbook_id"],
    )
    results = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        results.append(
            {
                "id": src["id"],
                "title": src["title"],
                "summary": src["summary"][:SUMMARY_CHARS],
                "resolution": src["resolution"][:SUMMARY_CHARS],
                "service": src["service"],
                "severity": src["severity"],
                "related_runbook_id": src.get("related_runbook_id"),
                "score": hit["_score"],
            }
        )
    return results
