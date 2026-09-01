"""CI-safe retrieval eval: BM25-only and dense-only (bge-small, precomputed vectors baked into
the fixture -- no sentence-transformers call needed here) over the small committed fixture
corpus in fixtures/ci_corpus.jsonl / fixtures/ci_alerts.jsonl. Runs against a bare
Elasticsearch service container: no ML node, no trial license, no ELSER, no hybrid RRF (those
need the ML node and are the local ablation's job -- evals/retrieval/run_ablation.py). Zero LLM
calls, per CLAUDE.md.

Fails the build if recall@5 for either strategy drops below the committed baseline in
evals/results/ci_baseline.json. Pass --write-baseline to (re)write that baseline from the
current run instead of comparing against it -- an explicit, deliberate action, never automatic.

Run: python -m evals.retrieval.run_ci_ablation
     python -m evals.retrieval.run_ci_ablation --write-baseline
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from elasticsearch import Elasticsearch

from common.es_client import get_es_client

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = ROOT / "fixtures"
RESULTS_PATH = ROOT / "evals" / "results" / "ci_ablation.json"
BASELINE_PATH = ROOT / "evals" / "results" / "ci_baseline.json"
INDEX = "ci-fixture-runbooks"
K = 5


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open() if line.strip()]


def _recall_at_5(ranked_ids: list[str], gold: str) -> float:
    return 1.0 if gold in ranked_ids[:5] else 0.0


def _ndcg_at_10(ranked_ids: list[str], gold: str) -> float:
    for i, doc_id in enumerate(ranked_ids[:10]):
        if doc_id == gold:
            return 1.0 / math.log2(i + 2)
    return 0.0


def load_fixture_index(es: Elasticsearch) -> None:
    corpus = _load_jsonl(FIXTURES_DIR / "ci_corpus.jsonl")
    es.indices.delete(index=INDEX, ignore_unavailable=True)
    es.indices.create(
        index=INDEX,
        settings={"number_of_shards": 1, "number_of_replicas": 0},
        mappings={
            "properties": {
                "id": {"type": "keyword"},
                "title": {"type": "text"},
                "body": {"type": "text"},
                "body_dense": {"type": "dense_vector", "dims": 384, "index": True, "similarity": "cosine"},
            }
        },
    )
    for row in corpus:
        es.index(index=INDEX, id=row["id"], document=row, refresh=False)
    es.indices.refresh(index=INDEX)


def run_bm25(es: Elasticsearch, tasks: list[dict[str, Any]]) -> tuple[float, float]:
    recalls, ndcgs = [], []
    for t in tasks:
        resp = es.search(
            index=INDEX,
            query={"multi_match": {"query": t["query"], "fields": ["title^2", "body"]}},
            size=10,
            source_includes=["id"],
        )
        ranked = [h["_source"]["id"] for h in resp["hits"]["hits"]]
        recalls.append(_recall_at_5(ranked, t["runbook_id"]))
        ndcgs.append(_ndcg_at_10(ranked, t["runbook_id"]))
    return sum(recalls) / len(recalls), sum(ndcgs) / len(ndcgs)


def run_dense(es: Elasticsearch, tasks: list[dict[str, Any]]) -> tuple[float, float]:
    """Query vectors come from the fixture itself (query_dense, precomputed and committed by
    scripts/build_ci_fixtures.py) -- CI never runs the embedding model, but this is still a
    real recall@5 signal since the vectors are genuine bge-small encodings of the alert text,
    not a round-trip of the gold document's own vector."""
    recalls, ndcgs = [], []
    for t in tasks:
        resp = es.search(
            index=INDEX,
            knn={
                "field": "body_dense",
                "query_vector": t["query_dense"],
                "k": 10,
                "num_candidates": 50,
            },
            source_includes=["id"],
            size=10,
        )
        ranked = [h["_source"]["id"] for h in resp["hits"]["hits"]]
        recalls.append(_recall_at_5(ranked, t["runbook_id"]))
        ndcgs.append(_ndcg_at_10(ranked, t["runbook_id"]))
    return sum(recalls) / len(recalls), sum(ndcgs) / len(ndcgs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()

    es = get_es_client()
    tasks = _load_jsonl(FIXTURES_DIR / "ci_alerts.jsonl")
    load_fixture_index(es)

    bm25_recall, bm25_ndcg = run_bm25(es, tasks)
    dense_recall, dense_ndcg = run_dense(es, tasks)

    strategies: dict[str, dict[str, float]] = {
        "bm25_only": {"recall@5": bm25_recall, "ndcg@10": bm25_ndcg},
        "dense_only": {"recall@5": dense_recall, "ndcg@10": dense_ndcg},
    }
    results: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "n_tasks": len(tasks),
        "strategies": strategies,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))

    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(results["strategies"], indent=2))
        print(f"wrote baseline -> {BASELINE_PATH}")
        return 0

    if not BASELINE_PATH.exists():
        print(f"no baseline at {BASELINE_PATH}; run with --write-baseline first", flush=True)
        return 1

    baseline = json.loads(BASELINE_PATH.read_text())
    failed = False
    for strategy, metrics in results["strategies"].items():
        base_recall = baseline.get(strategy, {}).get("recall@5", 0.0)
        if metrics["recall@5"] < base_recall:
            print(
                f"REGRESSION: {strategy} recall@5 {metrics['recall@5']:.3f} < "
                f"baseline {base_recall:.3f}"
            )
            failed = True
        else:
            print(f"OK: {strategy} recall@5 {metrics['recall@5']:.3f} >= baseline {base_recall:.3f}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
