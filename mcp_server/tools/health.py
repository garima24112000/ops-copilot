"""query_service_health: ES|QL aggregation over ops-logs-* -- counts log lines by level for a
service within a recent time window, used by the agent's diagnose step to confirm or refute
the runbook's hypothesis against live telemetry."""

from __future__ import annotations

import re
from typing import Any

from elasticsearch import Elasticsearch

_WINDOW_RE = re.compile(r"^(\d+)\s*([mhd])$")
_UNIT_WORDS = {"m": "minutes", "h": "hours", "d": "days"}


def _window_to_esql(window: str) -> str:
    m = _WINDOW_RE.match(window.strip())
    if not m:
        raise ValueError(f"window must look like '15m', '1h', '2d', got {window!r}")
    n, unit = m.groups()
    return f"{n} {_UNIT_WORDS[unit]}"


def query_service_health(es: Elasticsearch, service: str, window: str = "1h") -> dict[str, Any]:
    # `window` is validated against a fixed pattern above (no free-text interpolation); `service`
    # is untrusted input from the alert payload, so it goes in as an ES|QL query parameter
    # (`?`) rather than being string-interpolated into the query text.
    esql_window = _window_to_esql(window)
    query = (
        "FROM ops-logs-* "
        f"| WHERE service == ? AND @timestamp > NOW() - {esql_window} "
        "| STATS count = COUNT(*) BY level "
        "| SORT count DESC"
    )
    resp = es.esql.query(query=query, params=[service])
    columns = [c["name"] for c in resp["columns"]]
    rows = [dict(zip(columns, row, strict=True)) for row in resp["values"]]
    by_level = {row["level"]: row["count"] for row in rows}
    total = sum(by_level.values())
    return {
        "service": service,
        "window": window,
        "total_lines": total,
        "by_level": by_level,
        "error_rate": (by_level.get("ERROR", 0) + by_level.get("FATAL", 0)) / total if total else 0.0,
    }
