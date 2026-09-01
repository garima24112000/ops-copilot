from __future__ import annotations

import contextvars

from elastic_transport import Urllib3HttpNode
from elasticsearch import Elasticsearch

from common.config import env

# semantic_text fields run ELSER inference synchronously as part of the write path. Bulk
# indexing ~120 runbook bodies through it (cold-start adaptive_allocations, CPU-only) takes
# minutes, not seconds -- confirmed by isolating it: bulk-indexing the same docs with the
# semantic_text field removed took 0.02s, with it included it blew past even a 60s timeout.
# This is expected ELSER throughput, not a bug; give it real headroom rather than fail loudly.
DEFAULT_REQUEST_TIMEOUT_S = 300

# Set by cli.py's --user flag (security/dls.py) so a whole agent run -- including the MCP
# tool calls and the record() postmortem write -- authenticates as that user's DLS-scoped API
# key instead of the elastic superuser, without threading an es client through every call site.
_current_api_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_api_key", default=None
)


def set_current_api_key(encoded_api_key: str | None) -> None:
    _current_api_key.set(encoded_api_key)


def get_es_client(
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT_S, api_key: str | None = None
) -> Elasticsearch:
    url = env("ELASTICSEARCH_URL", "http://localhost:9200")
    assert url is not None
    key = api_key or _current_api_key.get()
    # Explicit urllib3-based node: this venv also has httpx installed for other deps and
    # elastic-transport's auto-selected node picked something less battle-tested for this
    # environment during debugging; pinning urllib3 removes that variable.
    if key:
        return Elasticsearch(
            url,
            api_key=key,
            request_timeout=request_timeout,
            node_class=Urllib3HttpNode,
            http_compress=False,
        )
    password = env("ELASTIC_PASSWORD", "changeme_local_only")
    assert password is not None
    return Elasticsearch(
        url,
        basic_auth=("elastic", password),
        request_timeout=request_timeout,
        node_class=Urllib3HttpNode,
        http_compress=False,
    )
