"""Bulk-load ops-runbooks, ops-incidents, and the ops-logs-* data stream from the committed
JSONL under data/. Idempotent: each index/data stream is dropped and recreated from its
mapping in ingest/mappings/ every run, so `make ingest` is safe to re-run.

Also creates the empty ops-postmortems and ops-agent-evals indices (written to later by the
agent and the eval harnesses respectively).

Precomputes body_dense / summary_semantic dense vectors with bge-small-en-v1.5
(sentence-transformers, CPU, no ES ML node needed for this one) so ops-runbooks and
ops-incidents carry all three retrieval representations (BM25 text, ELSER semantic_text,
dense_vector) side by side for the session 7 ablation.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

# sentence-transformers/huggingface_hub does an online freshness check on every model load
# by default, which can hang for a long time on a flaky/local-only network path (observed:
# indefinite hang with no error, vs. ~0.4s once cached). The model is a fixed pinned name
# (BAAI/bge-small-en-v1.5) committed nowhere else, so once it's been downloaded once this is
# safe; a genuinely fresh clone needs one online run first (see README).
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from elasticsearch import Elasticsearch, NotFoundError  # noqa: E402
from elasticsearch.helpers import bulk  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

from common.es_client import get_es_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MAPPINGS_DIR = ROOT / "ingest" / "mappings"

DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
LOGS_DATA_STREAM = "ops-logs-loghub"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _bulk_index(es: Elasticsearch, actions: Iterator[dict[str, Any]], chunk_size: int = 10) -> int:
    # Small chunks: semantic_text fields run ELSER inference synchronously per bulk request,
    # so keep each individual HTTP request's worth of inference bounded (see common/es_client.py).
    ok, errors = bulk(es, actions, stats_only=False, raise_on_error=False, chunk_size=chunk_size)
    error_list = cast(list[Any], errors)
    if error_list:
        print(f"  {len(error_list)} bulk errors, first: {error_list[0]}", file=sys.stderr)
    return ok


def _recreate_index(es: Elasticsearch, name: str, mapping_file: str) -> None:
    mapping = json.loads((MAPPINGS_DIR / mapping_file).read_text())
    es.indices.delete(index=name, ignore_unavailable=True)
    es.indices.create(index=name, **mapping)
    print(f"  recreated index {name!r}")


def _recreate_data_stream(es: Elasticsearch, template_name: str, mapping_file: str, ds_name: str) -> None:
    template = json.loads((MAPPINGS_DIR / mapping_file).read_text())
    template.pop("_comment", None)
    try:
        es.indices.delete_data_stream(name=ds_name)
    except NotFoundError:
        pass
    es.indices.put_index_template(name=template_name, **template)
    print(f"  put index template {template_name!r} for pattern {template['index_patterns']}")


def load_runbooks(es: Elasticsearch, model: SentenceTransformer) -> int:
    print("loading ops-runbooks...")
    rows = _load_jsonl(DATA_DIR / "runbooks.jsonl")
    if not rows:
        print("  data/runbooks.jsonl is empty or missing, skipping (run ingest/fetch_corpus.py first)")
        return 0
    _recreate_index(es, "ops-runbooks", "ops-runbooks.json")

    bodies = [r["body"] for r in rows]
    vectors = model.encode(bodies, normalize_embeddings=True, show_progress_bar=False, batch_size=16)

    def actions() -> Iterator[dict[str, Any]]:
        for row, vec in zip(rows, vectors, strict=True):
            yield {
                "_index": "ops-runbooks",
                "_id": row["id"],
                "_source": {
                    "id": row["id"],
                    "title": row["title"],
                    "body": row["body"],
                    "body_semantic": row["body"],
                    "body_dense": vec.tolist(),
                    "service": row["service"],
                    "department": row["department"],
                    "source_url": row["source_url"],
                },
            }

    ok = _bulk_index(es, actions())
    es.indices.refresh(index="ops-runbooks")
    count: int = es.count(index="ops-runbooks")["count"]
    print(f"  indexed {ok} docs, index count={count}")
    return count


def load_incidents(es: Elasticsearch, model: SentenceTransformer) -> int:
    print("loading ops-incidents...")
    rows = _load_jsonl(DATA_DIR / "incidents.jsonl")
    if not rows:
        print("  data/incidents.jsonl is empty or missing, skipping (run scripts/generate_alerts.py first)")
        return 0
    _recreate_index(es, "ops-incidents", "ops-incidents.json")

    summaries = [r["summary"] for r in rows]
    vectors = model.encode(summaries, normalize_embeddings=True, show_progress_bar=False, batch_size=16)

    def actions() -> Iterator[dict[str, Any]]:
        for row, vec in zip(rows, vectors, strict=True):
            yield {
                "_index": "ops-incidents",
                "_id": row["id"],
                "_source": {**row, "summary_semantic": row["summary"], "summary_dense": vec.tolist()},
            }

    ok = _bulk_index(es, actions())
    es.indices.refresh(index="ops-incidents")
    count: int = es.count(index="ops-incidents")["count"]
    print(f"  indexed {ok} docs, index count={count}")
    return count


def load_logs(es: Elasticsearch) -> int:
    print("loading ops-logs-* data stream...")
    rows = _load_jsonl(DATA_DIR / "logs.jsonl")
    if not rows:
        print("  data/logs.jsonl is empty or missing, skipping (run ingest/fetch_corpus.py first)")
        return 0
    _recreate_data_stream(es, "ops-logs-template", "ops-logs.json", LOGS_DATA_STREAM)

    def actions() -> Iterator[dict[str, Any]]:
        for row in rows:
            yield {"_index": LOGS_DATA_STREAM, "_op_type": "create", "_source": row}

    ok = _bulk_index(es, actions())
    es.indices.refresh(index=LOGS_DATA_STREAM)
    count: int = es.count(index=LOGS_DATA_STREAM)["count"]
    print(f"  indexed {ok} docs, data stream count={count}")
    return count


def ensure_empty_indices(es: Elasticsearch) -> None:
    for name, mapping_file in [
        ("ops-postmortems", "ops-postmortems.json"),
        ("ops-agent-evals", "ops-agent-evals.json"),
    ]:
        if not es.indices.exists(index=name):
            mapping = json.loads((MAPPINGS_DIR / mapping_file).read_text())
            es.indices.create(index=name, **mapping)
            print(f"  created empty index {name!r}")
        else:
            print(f"  index {name!r} already exists, leaving its data alone")


def main() -> int:
    es = get_es_client()
    t0 = time.time()
    print(f"loading dense embedding model {DENSE_MODEL_NAME}...")
    model = SentenceTransformer(DENSE_MODEL_NAME)

    counts = {
        "ops-runbooks": load_runbooks(es, model),
        "ops-incidents": load_incidents(es, model),
        LOGS_DATA_STREAM: load_logs(es),
    }
    print("ensuring ops-postmortems / ops-agent-evals exist...")
    ensure_empty_indices(es)

    print(f"\n=== ingest summary (took {time.time() - t0:.1f}s) ===")
    for name, count in counts.items():
        print(f"{name:<20} {count:>8} docs")

    for name in counts:
        resp = es.search(index=name, size=1, query={"match_all": {}})
        hits = resp["hits"]["total"]["value"]
        print(f"_search sanity check on {name!r}: {hits} total hits")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
