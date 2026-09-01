"""The free retrieval ablation: BM25 only, dense only (bge-small), ELSER only, hybrid RRF.
Zero LLM calls anywhere in this file (CLAUDE.md rule) -- query encoding uses the retrieval
models themselves (bge-small locally, ELSER server-side), never a chat/completion call.

Ground truth: data/alerts.jsonl, `runbook_id` is the correct answer for `message` as query.

Metrics: recall@5, nDCG@10 (binary relevance, single relevant doc per query), p95 query
latency. Results -> evals/results/ablation.json and indexed into ops-agent-evals with the
git SHA, per CLAUDE.md's "every number must be read from a file under evals/results/" rule.

Run: python -m evals.retrieval.run_ablation
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

# see ingest/load.py for why: avoids an indefinite hang in sentence-transformers' online
# freshness check once the model is already cached locally.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from elasticsearch import Elasticsearch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

from common.es_client import get_es_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
ALERTS_PATH = ROOT / "data" / "alerts.jsonl"
RESULTS_PATH = ROOT / "evals" / "results" / "ablation.json"
INDEX = "ops-runbooks"
K = 10
DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_tasks() -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in ALERTS_PATH.open() if line.strip()]
    return [{"query": r["message"], "runbook_id": r["runbook_id"]} for r in rows]


def _recall_at_5(ranked_ids: list[str], gold: str) -> float:
    return 1.0 if gold in ranked_ids[:5] else 0.0


def _ndcg_at_10(ranked_ids: list[str], gold: str) -> float:
    for i, doc_id in enumerate(ranked_ids[:10]):
        if doc_id == gold:
            return 1.0 / math.log2(i + 2)  # IDCG for a single relevant doc is 1.0
    return 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(round(p / 100 * (len(s) - 1))), len(s) - 1)
    return s[idx]


def run_bm25(es: Elasticsearch, tasks: list[dict[str, Any]]) -> tuple[list[float], list[float], list[float]]:
    recalls, ndcgs, latencies = [], [], []
    for t in tasks:
        t0 = time.perf_counter()
        resp = es.search(
            index=INDEX,
            query={"multi_match": {"query": t["query"], "fields": ["title^2", "body"]}},
            size=K,
            source_includes=["id"],
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        ranked = [h["_source"]["id"] for h in resp["hits"]["hits"]]
        recalls.append(_recall_at_5(ranked, t["runbook_id"]))
        ndcgs.append(_ndcg_at_10(ranked, t["runbook_id"]))
    return recalls, ndcgs, latencies


def run_dense(
    es: Elasticsearch, tasks: list[dict[str, Any]], model: SentenceTransformer
) -> tuple[list[float], list[float], list[float]]:
    recalls, ndcgs, latencies = [], [], []
    queries = [t["query"] for t in tasks]
    vectors = model.encode(queries, normalize_embeddings=True, show_progress_bar=False, batch_size=16)
    for t, vec in zip(tasks, vectors, strict=True):
        t0 = time.perf_counter()
        resp = es.search(
            index=INDEX,
            knn={"field": "body_dense", "query_vector": vec.tolist(), "k": K, "num_candidates": 50},
            source_includes=["id"],
            size=K,
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        ranked = [h["_source"]["id"] for h in resp["hits"]["hits"]]
        recalls.append(_recall_at_5(ranked, t["runbook_id"]))
        ndcgs.append(_ndcg_at_10(ranked, t["runbook_id"]))
    return recalls, ndcgs, latencies


def run_elser(es: Elasticsearch, tasks: list[dict[str, Any]]) -> tuple[list[float], list[float], list[float]]:
    recalls, ndcgs, latencies = [], [], []
    for t in tasks:
        t0 = time.perf_counter()
        resp = es.search(
            index=INDEX,
            query={"semantic": {"field": "body_semantic", "query": t["query"]}},
            size=K,
            source_includes=["id"],
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        ranked = [h["_source"]["id"] for h in resp["hits"]["hits"]]
        recalls.append(_recall_at_5(ranked, t["runbook_id"]))
        ndcgs.append(_ndcg_at_10(ranked, t["runbook_id"]))
    return recalls, ndcgs, latencies


def run_hybrid_rrf(
    es: Elasticsearch, tasks: list[dict[str, Any]]
) -> tuple[list[float], list[float], list[float]]:
    recalls, ndcgs, latencies = [], [], []
    for t in tasks:
        t0 = time.perf_counter()
        resp = es.search(
            index=INDEX,
            body={
                "retriever": {
                    "rrf": {
                        "retrievers": [
                            {
                                "standard": {
                                    "query": {
                                        "multi_match": {"query": t["query"], "fields": ["title^2", "body"]}
                                    }
                                }
                            },
                            {
                                "standard": {
                                    "query": {"semantic": {"field": "body_semantic", "query": t["query"]}}
                                }
                            },
                        ],
                        "rank_window_size": 50,
                        "rank_constant": 60,
                    }
                },
                "size": K,
                "_source": ["id"],
            },
        )
        latencies.append((time.perf_counter() - t0) * 1000)
        ranked = [h["_source"]["id"] for h in resp["hits"]["hits"]]
        recalls.append(_recall_at_5(ranked, t["runbook_id"]))
        ndcgs.append(_ndcg_at_10(ranked, t["runbook_id"]))
    return recalls, ndcgs, latencies


def main() -> int:
    es = get_es_client()
    tasks = _load_tasks()
    print(f"running ablation over {len(tasks)} tasks...")
    model = SentenceTransformer(DENSE_MODEL_NAME)

    strategies = {
        "bm25_only": run_bm25(es, tasks),
        "dense_only": run_dense(es, tasks, model),
        "elser_only": run_elser(es, tasks),
        "hybrid_rrf": run_hybrid_rrf(es, tasks),
    }

    results = {}
    for name, (recalls, ndcgs, latencies) in strategies.items():
        results[name] = {
            "recall@5": sum(recalls) / len(recalls),
            "ndcg@10": sum(ndcgs) / len(ndcgs),
            "p95_latency_ms": _percentile(latencies, 95),
            "n_tasks": len(recalls),
        }

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "git_sha": _git_sha(),
        "n_tasks": len(tasks),
        "strategies": results,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2))
    print(f"wrote {RESULTS_PATH}")

    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    for strategy, metrics in results.items():
        for metric_name in ("recall@5", "ndcg@10", "p95_latency_ms"):
            es.index(
                index="ops-agent-evals",
                document={
                    "run_id": output["generated_at"],
                    "eval_type": "retrieval",
                    "strategy": strategy,
                    "subset": "generated",
                    "metric": metric_name,
                    "score": metrics[metric_name],
                    "git_sha": output["git_sha"],
                    "@timestamp": now,
                },
            )
    es.indices.refresh(index="ops-agent-evals")

    print("\n| strategy    | recall@5 | nDCG@10 | p95 latency (ms) |")
    print("|-------------|----------|---------|-------------------|")
    for name, metrics in results.items():
        print(
            f"| {name:<11} | {metrics['recall@5']:.3f}    | {metrics['ndcg@10']:.3f}   |"
            f" {metrics['p95_latency_ms']:.1f}             |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
