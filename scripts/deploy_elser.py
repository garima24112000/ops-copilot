"""Deploy (or confirm) the ELSER sparse-embedding inference endpoint and poll until it is
fully allocated, then run a smoke test: index one document with a semantic_text field and
retrieve it semantically.

Verified against a live Elasticsearch 9.5.2 cluster on 2026-08-31 (session 2): current ES
ships a *preconfigured* `.elser-2-elasticsearch` inference endpoint
(service="elasticsearch", model_id=".elser_model_2", adaptive_allocations enabled 0-32) — the
older dedicated PUT .../sparse_embedding/<id> {"service": "elser", ...} shape still works but
is documented as deprecated in favour of the generic endpoint.

This script does NOT use that preconfigured endpoint, though. Its adaptive_allocations budget
was capped at a single allocation by ES's default ML memory sizing (30% of node memory minus
JVM heap), which throughput-starves bulk-loading a whole runbook corpus through it -- confirmed
empirically: bulk-indexing 120+190 documents through 1 allocation queued 250+ pending inference
requests and was still nowhere near done after 15+ minutes. Instead this deploys a dedicated,
explicitly-sized endpoint (`ops-copilot-elser`, default 6 allocations) and REQUIRES
`xpack.ml.use_auto_machine_memory_percent=true` in docker-compose.yml's Elasticsearch service
(already set) so there's enough ML memory headroom for more than one allocation to fit.
"""

from __future__ import annotations

import sys
import time

from elasticsearch import Elasticsearch, NotFoundError

from common.es_client import get_es_client

ELSER_INFERENCE_ID = "ops-copilot-elser"
NUM_ALLOCATIONS = 6
SMOKE_INDEX = "ops-copilot-elser-smoke-test"
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 600


def ensure_endpoint(es: Elasticsearch) -> str:
    try:
        es.inference.get(inference_id=ELSER_INFERENCE_ID, task_type="sparse_embedding")
        print(f"inference endpoint {ELSER_INFERENCE_ID!r} already exists")
        return ELSER_INFERENCE_ID
    except NotFoundError:
        pass

    print(f"creating {ELSER_INFERENCE_ID!r} ({NUM_ALLOCATIONS} allocations)...")
    es.inference.put(
        task_type="sparse_embedding",
        inference_id=ELSER_INFERENCE_ID,
        inference_config={
            "service": "elasticsearch",
            "service_settings": {
                "num_allocations": NUM_ALLOCATIONS,
                "num_threads": 1,
                "model_id": ".elser_model_2",
            },
        },
    )
    return ELSER_INFERENCE_ID


def wait_for_allocation(es: Elasticsearch, inference_id: str) -> None:
    """ELSER on the elasticsearch service deploys lazily on first use. Trigger one inference
    call to force deployment, then poll trained model stats until allocation is ready."""
    print("triggering deployment with a test inference call...")
    deadline = time.time() + POLL_TIMEOUT_S
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            es.inference.inference(
                inference_id=inference_id,
                task_type="sparse_embedding",
                input="deployment warm-up ping",
            )
            print("inference call succeeded, model is allocated")
            return
        except Exception as exc:  # noqa: BLE001 - broad on purpose, we're polling a 5xx-while-loading condition
            last_err = exc
            print(f"  not ready yet ({exc.__class__.__name__}), retrying in {POLL_INTERVAL_S}s...")
            time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"ELSER did not become allocated within {POLL_TIMEOUT_S}s: {last_err}")


def smoke_test(es: Elasticsearch, inference_id: str) -> None:
    print(f"running semantic_text smoke test against index {SMOKE_INDEX!r}...")
    es.indices.delete(index=SMOKE_INDEX, ignore_unavailable=True)
    es.indices.create(
        index=SMOKE_INDEX,
        mappings={
            "properties": {
                "body": {
                    "type": "semantic_text",
                    "inference_id": inference_id,
                }
            }
        },
    )
    es.index(
        index=SMOKE_INDEX,
        id="1",
        document={"body": "Restart the payment gateway service when its health check fails."},
        refresh="wait_for",
    )
    query_text = "how do I recover a crashed payment service"
    resp = es.search(
        index=SMOKE_INDEX,
        query={"semantic": {"field": "body", "query": query_text}},
    )
    hits = resp["hits"]["hits"]
    if not hits:
        raise RuntimeError("semantic_text smoke test returned zero hits")
    top = hits[0]

    # semantic_text does not surface the stored sparse vector in _source in this ES version
    # (it's kept as internal doc values), so confirm sparse-vector generation directly via
    # the inference API against the same query text used above.
    inf_resp = es.inference.inference(
        inference_id=inference_id, task_type="sparse_embedding", input=query_text
    )
    sparse_vec = inf_resp["sparse_embedding"][0]["embedding"]
    n_terms = len(sparse_vec)
    sample_terms = list(sparse_vec.items())[:5]
    print(f"smoke test PASSED: doc {top['_id']!r} matched semantically, score={top['_score']:.3f}")
    print(f"  sparse vector for query has {n_terms} weighted terms, sample: {sample_terms}")
    es.indices.delete(index=SMOKE_INDEX, ignore_unavailable=True)


def main() -> int:
    es = get_es_client()
    try:
        inference_id = ensure_endpoint(es)
        wait_for_allocation(es, inference_id)
        smoke_test(es, inference_id)
    except Exception as exc:  # noqa: BLE001
        print(f"ELSER deployment FAILED: {exc}", file=sys.stderr)
        print(
            "If this is a RAM issue, switch to the dense-only path per CLAUDE.md / plan section 12:"
            " bge-small-en-v1.5 precomputed vectors, no ES ML node.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
