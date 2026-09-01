"""Session 6 adaptation (unattended run): the runbook calls for a human to eyeball three
hand-typed queries in Kibana Dev Tools. Automated equivalent per the operator's instructions:
hold out 20 alerts, measure whether the correct runbook lands in the top 3 via hybrid RRF,
and require >=60% before proceeding to session 7. This is a load-bearing gate -- if it fails,
STOP and diagnose (parsing, mappings, or generated alerts) rather than proceeding.

Run: python -m evals.retrieval.session6_check
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from elasticsearch import Elasticsearch

from common.es_client import get_es_client

ROOT = Path(__file__).resolve().parent.parent.parent
ALERTS_PATH = ROOT / "data" / "alerts.jsonl"
RESULTS_PATH = ROOT / "evals" / "results" / "session6_check.json"
HOLDOUT_N = 20
THRESHOLD = 0.60
TOP_K = 3
SEED = 42


def hybrid_search(es: Elasticsearch, query: str, k: int = TOP_K) -> list[dict[str, Any]]:
    resp = es.search(
        index="ops-runbooks",
        body={
            "retriever": {
                "rrf": {
                    "retrievers": [
                        {
                            "standard": {
                                "query": {"multi_match": {"query": query, "fields": ["title^2", "body"]}}
                            }
                        },
                        {"standard": {"query": {"semantic": {"field": "body_semantic", "query": query}}}},
                    ],
                    "rank_window_size": 50,
                    "rank_constant": 60,
                }
            },
            "size": k,
            "_source": ["id", "title"],
        },
    )
    return [{"id": h["_source"]["id"], "title": h["_source"]["title"]} for h in resp["hits"]["hits"]]


def main() -> int:
    es = get_es_client()
    rows = [json.loads(line) for line in ALERTS_PATH.open() if line.strip()]
    rng = random.Random(SEED)
    holdout = rng.sample(rows, min(HOLDOUT_N, len(rows)))

    hits = 0
    examples = []
    for row in holdout:
        top3 = hybrid_search(es, row["message"])
        top3_ids = [h["id"] for h in top3]
        hit = row["runbook_id"] in top3_ids
        hits += int(hit)
        examples.append(
            {
                "query": row["message"],
                "gold_runbook_id": row["runbook_id"],
                "top3": top3,
                "hit": hit,
            }
        )

    hit_rate = hits / len(holdout)
    passed = hit_rate >= THRESHOLD

    output = {
        "n_holdout": len(holdout),
        "top3_hit_rate": hit_rate,
        "threshold": THRESHOLD,
        "passed": passed,
        "worked_examples": examples[:3],
        "all_examples": examples,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2))

    print(f"session 6 retrieval check: {hits}/{len(holdout)} = {hit_rate:.2%} top-3 hit rate "
          f"(threshold {THRESHOLD:.0%}) -> {'PASS' if passed else 'FAIL'}")
    print(f"wrote {RESULTS_PATH}")
    for ex in examples[:3]:
        print(f"\nquery: {ex['query']}")
        print(f"gold:  {ex['gold_runbook_id']} (hit={ex['hit']})")
        for i, t in enumerate(ex["top3"]):
            print(f"  #{i+1}: {t['id']} — {t['title']}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
