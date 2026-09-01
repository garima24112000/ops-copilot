#!/usr/bin/env bash
# Blocks until Elasticsearch, Kibana, and APM Server are all healthy.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

ELASTIC_PASSWORD="${ELASTIC_PASSWORD:-changeme_local_only}"
ES_URL="${ELASTICSEARCH_URL:-http://localhost:9200}"
KIBANA_URL="${KIBANA_URL:-http://localhost:5601}"
APM_URL="${APM_SERVER_URL:-http://localhost:8200}"

MAX_WAIT=300
INTERVAL=5
elapsed=0

wait_for() {
  local name="$1" check_cmd="$2"
  echo -n "waiting for $name"
  while ! eval "$check_cmd" >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$MAX_WAIT" ]; then
      echo " TIMEOUT after ${MAX_WAIT}s"
      return 1
    fi
    echo -n "."
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
  done
  echo " OK"
}

wait_for "elasticsearch" "curl -sf -u elastic:${ELASTIC_PASSWORD} ${ES_URL}/_cluster/health"
wait_for "kibana" "curl -sf ${KIBANA_URL}/api/status | grep -q '\"level\":\"available\"'"
wait_for "apm-server" "curl -sf ${APM_URL}/"

echo "stack is up:"
echo "  elasticsearch: ${ES_URL}"
echo "  kibana:        ${KIBANA_URL}"
echo "  apm-server:    ${APM_URL}"
