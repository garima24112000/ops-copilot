"""Build a small, committed CI fixture corpus for the retrieval eval that runs in GitHub
Actions: fixtures/ci_corpus.jsonl (runbooks WITH precomputed body_dense vectors baked in) and
fixtures/ci_alerts.jsonl (alert queries + ground-truth runbook_id). CI loads these into a bare
Elasticsearch service container -- no ML node, no trial license, no sentence-transformers call
at CI time -- and runs BM25-only / dense-only recall@5 (ELSER and hybrid RRF need the ML node,
so they're the local/full ablation's job, not CI's; see evals/retrieval/run_ablation.py).

Run: python -m scripts.build_ci_fixtures
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from sentence_transformers import SentenceTransformer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FIXTURES_DIR = ROOT / "fixtures"
N_ALERTS = 30
DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def main() -> int:
    runbooks = {r["id"]: r for r in (json.loads(line) for line in (DATA_DIR / "runbooks.jsonl").open())}
    alerts = [json.loads(line) for line in (DATA_DIR / "alerts.jsonl").open()]

    # variant 0 first (one alert per runbook) so the fixture covers as many distinct runbooks
    # as possible within N_ALERTS, rather than two variants of the same handful.
    alerts.sort(key=lambda a: a["variant"])
    selected_alerts = alerts[:N_ALERTS]
    selected_runbook_ids = {a["runbook_id"] for a in selected_alerts}
    selected_runbooks = [runbooks[rid] for rid in selected_runbook_ids if rid in runbooks]

    print(f"selected {len(selected_alerts)} alerts covering {len(selected_runbooks)} distinct runbooks")

    model = SentenceTransformer(DENSE_MODEL_NAME)
    bodies = [r["body"] for r in selected_runbooks]
    body_vectors = model.encode(bodies, normalize_embeddings=True, show_progress_bar=False, batch_size=16)

    # Query vectors are ALSO precomputed and committed here (not encoded at CI time) so the
    # dense-only CI eval is a real recall@5 signal, not just round-tripping a doc's own vector.
    queries = [a["message"] for a in selected_alerts]
    query_vectors = model.encode(queries, normalize_embeddings=True, show_progress_bar=False, batch_size=16)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with (FIXTURES_DIR / "ci_corpus.jsonl").open("w") as f:
        for runbook, vec in zip(selected_runbooks, body_vectors, strict=True):
            row = {**runbook, "body_dense": vec.tolist()}
            f.write(json.dumps(row) + "\n")

    with (FIXTURES_DIR / "ci_alerts.jsonl").open("w") as f:
        for alert, vec in zip(selected_alerts, query_vectors, strict=True):
            f.write(
                json.dumps(
                    {
                        "query": alert["message"],
                        "runbook_id": alert["runbook_id"],
                        "query_dense": vec.tolist(),
                    }
                )
                + "\n"
            )

    print(f"wrote fixtures/ci_corpus.jsonl ({len(selected_runbooks)} docs) and "
          f"fixtures/ci_alerts.jsonl ({len(selected_alerts)} queries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
