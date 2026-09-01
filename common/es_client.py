from __future__ import annotations

from elasticsearch import Elasticsearch

from common.config import env


def get_es_client() -> Elasticsearch:
    url = env("ELASTICSEARCH_URL", "http://localhost:9200")
    password = env("ELASTIC_PASSWORD", "changeme_local_only")
    assert url is not None and password is not None
    return Elasticsearch(url, basic_auth=("elastic", password), request_timeout=60)
