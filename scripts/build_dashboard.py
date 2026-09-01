"""Session 12: build the Ops Copilot Kibana dashboard entirely through the saved-objects API
(no UI clicking), then verify it via GET before exporting (scripts/export_dashboards.py).

Panels: tokens per run over time, p95 latency by span, tool-call frequency, provider mix,
run success rate -- all against the real traces-apm-default data view, built from the actual
OTel GenAI spans this project emits (session 11).

Session-12 postmortem: the first pass at this script was marked DONE on the strength of a
201/200 from every PUT call, which hid five real schema/query bugs that only surfaced when the
dashboard was actually opened in a browser: (1) the script never created the data view itself,
it assumed a hardcoded id created out-of-band by clicking around Kibana still existed; (2) every
panel's `searchSourceJSON` was missing `indexRefName`, so at render time Kibana's SearchSource
couldn't tell which entry in `references` was the index pattern and called `esaggs`'s
`indexPatternLoad` with no id at all; (3) the success-rate panel used `filter_ratio`, which is a
TSVB-only aggregation that was never a registered classic-aggs metric type -- esaggs rejected it
outright; (4) every panel dict in the dashboard's `panelsJSON` was missing `embeddableConfig`,
which crashed the whole dashboard (not just one panel) in Kibana's server-side read transform
with "Cannot read properties of undefined (reading 'enhancements')"; (5) three panels' KQL
queries wildcarded a *quoted* string (`'"chat*"'`), which KQL treats as a literal phrase
containing an asterisk rather than a wildcard, so panels that referenced real data silently
rendered "No results found". An API 200 only proves the object was stored, not that Kibana's
runtime can parse or query with it; this script is now verified by actually opening the
dashboard and confirming every panel renders real data, not just that the writes succeeded.

Run: python -m scripts.build_dashboard
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from common.config import env

DATA_VIEW_TITLE = "traces-apm-default"
RUNTIME_FIELD_NAME = "is_success"


def client() -> httpx.Client:
    kibana_url = (env("KIBANA_URL", "http://localhost:5601") or "http://localhost:5601").rstrip("/")
    password = env("ELASTIC_PASSWORD", "changeme_local_only") or "changeme_local_only"
    return httpx.Client(base_url=kibana_url, auth=("elastic", password), headers={"kbn-xsrf": "true"})


def ensure_data_view(c: httpx.Client) -> str:
    """Find or create the traces-apm-default data view via the Data View API (not a raw
    saved-objects PUT) so Kibana actually populates its field list from the live mapping,
    rather than being handed an empty `fields: []` the way a hand-crafted saved object is."""
    resp = c.get("/api/data_views")
    resp.raise_for_status()
    for dv in resp.json().get("data_view", []):
        if dv["title"] == DATA_VIEW_TITLE:
            print(f"  found existing data view {dv['id']!r}")
            return str(dv["id"])

    resp = c.post(
        "/api/data_views/data_view",
        json={
            "data_view": {
                "title": DATA_VIEW_TITLE,
                "name": "APM Traces (ops-copilot)",
                "timeFieldName": "@timestamp",
            }
        },
    )
    resp.raise_for_status()
    data_view_id = str(resp.json()["data_view"]["id"])
    print(f"  created data view {data_view_id!r}")
    return data_view_id


def ensure_success_runtime_field(c: httpx.Client, data_view_id: str) -> None:
    """Replacement for the removed `filter_ratio` TSVB agg: a plain, currently-supported
    Average metric agg over a runtime field that's 1 for a successful transaction and 0
    otherwise -- averaging 0/1 over the matched transactions is exactly the success rate."""
    script_source = (
        "emit(doc.containsKey('event.outcome') && doc['event.outcome'].size() != 0 "
        "&& doc['event.outcome'].value == 'success' ? 1 : 0)"
    )
    resp = c.post(
        f"/api/data_views/data_view/{data_view_id}/runtime_field",
        json={
            "name": RUNTIME_FIELD_NAME,
            "runtimeField": {"type": "long", "script": {"source": script_source}},
        },
    )
    if resp.status_code == 400 and "already exists" in resp.text:
        print(f"  runtime field {RUNTIME_FIELD_NAME!r} already exists")
        return
    resp.raise_for_status()
    print(f"  created runtime field {RUNTIME_FIELD_NAME!r}")


def put_visualization(
    c: httpx.Client, obj_id: str, title: str, vis_state: dict[str, Any], query: str, data_view_id: str
) -> None:
    body = {
        "attributes": {
            "title": title,
            "visState": json.dumps(vis_state),
            "uiStateJSON": "{}",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(
                    {
                        "query": {"query": query, "language": "kuery"},
                        "filter": [],
                        # Without this, Kibana can't tell which `references` entry is the
                        # index pattern at render time -- esaggs's indexPatternLoad then gets
                        # called with no id, producing "requires the id argument".
                        "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index",
                    }
                )
            },
        },
        "references": [
            {
                "id": data_view_id,
                "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
                "type": "index-pattern",
            }
        ],
    }
    resp = c.post(f"/api/saved_objects/visualization/{obj_id}", json=body, params={"overwrite": "true"})
    resp.raise_for_status()
    print(f"  created visualization {obj_id!r}")


def main() -> int:
    c = client()
    data_view_id = ensure_data_view(c)
    ensure_success_runtime_field(c, data_view_id)

    # tokens per run over time (line chart)
    put_visualization(
        c,
        "ops-copilot-tokens-per-run",
        "Tokens per run over time",
        {
            "title": "Tokens per run over time",
            "type": "line",
            "params": {
                "grid": {"categoryLines": False},
                "categoryAxes": [
                    {
                        "id": "CategoryAxis-1",
                        "type": "category",
                        "position": "bottom",
                        "show": True,
                        "style": {},
                        "scale": {"type": "linear"},
                        "labels": {"show": True, "filter": True, "truncate": 100},
                        "title": {},
                    }
                ],
                "valueAxes": [
                    {
                        "id": "ValueAxis-1",
                        "name": "LeftAxis-1",
                        "type": "value",
                        "position": "left",
                        "show": True,
                        "style": {},
                        "scale": {"type": "linear", "mode": "normal"},
                        "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                        "title": {"text": "tokens"},
                    }
                ],
                "seriesParams": [
                    {
                        "show": True,
                        "type": "line",
                        "mode": "normal",
                        "data": {"label": "input tokens", "id": "1"},
                        "valueAxis": "ValueAxis-1",
                        "drawLinesBetweenPoints": True,
                        "showCircles": True,
                    },
                    {
                        "show": True,
                        "type": "line",
                        "mode": "normal",
                        "data": {"label": "output tokens", "id": "2"},
                        "valueAxis": "ValueAxis-1",
                        "drawLinesBetweenPoints": True,
                        "showCircles": True,
                    },
                ],
                "addTooltip": True,
                "addLegend": True,
                "legendPosition": "right",
                "times": [],
                "addTimeMarker": False,
            },
            "aggs": [
                {
                    "id": "1",
                    "enabled": True,
                    "type": "sum",
                    "schema": "metric",
                    "params": {"field": "numeric_labels.gen_ai_usage_input_tokens"},
                },
                {
                    "id": "2",
                    "enabled": True,
                    "type": "sum",
                    "schema": "metric",
                    "params": {"field": "numeric_labels.gen_ai_usage_output_tokens"},
                },
                {
                    "id": "3",
                    "enabled": True,
                    "type": "date_histogram",
                    "schema": "segment",
                    "params": {
                        "field": "@timestamp",
                        "useNormalizedEsInterval": True,
                        "interval": "auto",
                        "drop_partials": False,
                        "min_doc_count": 1,
                        "extended_bounds": {},
                    },
                },
            ],
        },
        query="span.name: chat*",
        data_view_id=data_view_id,
    )

    # p95 latency by span name (bar chart)
    put_visualization(
        c,
        "ops-copilot-p95-latency",
        "p95 latency by span",
        {
            "title": "p95 latency by span",
            "type": "histogram",
            "params": {
                "grid": {"categoryLines": False},
                "categoryAxes": [
                    {
                        "id": "CategoryAxis-1",
                        "type": "category",
                        "position": "bottom",
                        "show": True,
                        "style": {},
                        "scale": {"type": "linear"},
                        "labels": {"show": True, "filter": True, "truncate": 100},
                        "title": {},
                    }
                ],
                "valueAxes": [
                    {
                        "id": "ValueAxis-1",
                        "name": "LeftAxis-1",
                        "type": "value",
                        "position": "left",
                        "show": True,
                        "style": {},
                        "scale": {"type": "linear", "mode": "normal"},
                        "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                        "title": {"text": "p95 duration (us)"},
                    }
                ],
                "seriesParams": [
                    {
                        "show": True,
                        "type": "histogram",
                        "mode": "normal",
                        "data": {"label": "p95 duration (us)", "id": "1"},
                        "valueAxis": "ValueAxis-1",
                    }
                ],
                "addTooltip": True,
                "addLegend": True,
                "legendPosition": "right",
            },
            "aggs": [
                {
                    "id": "1",
                    "enabled": True,
                    "type": "percentiles",
                    "schema": "metric",
                    "params": {"field": "span.duration.us", "percents": [95]},
                },
                {
                    "id": "2",
                    "enabled": True,
                    "type": "terms",
                    "schema": "segment",
                    # A percentiles metric is multi-value even with one requested percentile --
                    # ordering by it needs "<aggId>.<percentile>", not the bare aggId, or esaggs
                    # rejects it with "Invalid aggregation order path".
                    "params": {"field": "span.name", "size": 10, "order": "desc", "orderBy": "1.95"},
                },
            ],
        },
        query="processor.event: span",
        data_view_id=data_view_id,
    )

    # tool-call frequency (pie)
    put_visualization(
        c,
        "ops-copilot-tool-frequency",
        "Tool-call frequency",
        {
            "title": "Tool-call frequency",
            "type": "pie",
            "params": {"addTooltip": True, "addLegend": True, "legendPosition": "right", "isDonut": True},
            "aggs": [
                {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}},
                {
                    "id": "2",
                    "enabled": True,
                    "type": "terms",
                    "schema": "segment",
                    "params": {"field": "labels.gen_ai_tool_name", "size": 10, "order": "desc", "orderBy": "1"},
                },
            ],
        },
        query="span.name: execute_tool*",
        data_view_id=data_view_id,
    )

    # provider mix (pie)
    put_visualization(
        c,
        "ops-copilot-provider-mix",
        "Provider mix",
        {
            "title": "Provider mix",
            "type": "pie",
            "params": {"addTooltip": True, "addLegend": True, "legendPosition": "right", "isDonut": True},
            "aggs": [
                {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}},
                {
                    "id": "2",
                    "enabled": True,
                    "type": "terms",
                    "schema": "segment",
                    "params": {"field": "labels.gen_ai_provider_name", "size": 10, "order": "desc", "orderBy": "1"},
                },
            ],
        },
        query="span.name: chat*",
        data_view_id=data_view_id,
    )

    # run success rate (metric). `filter_ratio` is a TSVB-only agg, never a registered
    # classic-aggs metric type -- replaced with Average over the `is_success` runtime field
    # (1/0 per matched transaction), which is exactly the same ratio via a supported agg.
    put_visualization(
        c,
        "ops-copilot-success-rate",
        "Run success rate",
        {
            "title": "Run success rate",
            "type": "metric",
            "params": {
                "addTooltip": True,
                "addLegend": False,
                "metric": {
                    "percentageMode": True,
                    "useRanges": False,
                    "colorSchema": "Green to Red",
                    "metricColorMode": "None",
                    "colorsRange": [{"from": 0, "to": 1}],
                    "labels": {"show": True},
                    "invertColors": False,
                    "style": {"bgFill": "#000", "bgColor": False, "labelColor": False, "subText": "", "fontSize": 60},
                },
            },
            "aggs": [
                {
                    "id": "1",
                    "enabled": True,
                    "type": "avg",
                    "schema": "metric",
                    "params": {"field": RUNTIME_FIELD_NAME, "customLabel": "success rate"},
                }
            ],
        },
        query="processor.event: transaction and transaction.name: invoke_agent*",
        data_view_id=data_view_id,
    )

    # dashboard referencing all 5 panels. Every panel needs an explicit `embeddableConfig`
    # (even empty) -- Kibana's server-side read transform destructures `panel.embeddableConfig`
    # in its legacy panelRefName fallback path *before* panelBwc() would otherwise backfill it,
    # so a panel missing this key crashes the whole dashboard load with "Cannot read properties
    # of undefined (reading 'enhancements')", not just that one panel.
    panels = [
        {
            "panelIndex": "1",
            "gridData": {"x": 0, "y": 0, "w": 24, "h": 15, "i": "1"},
            "type": "visualization",
            "embeddableConfig": {},
        },
        {
            "panelIndex": "2",
            "gridData": {"x": 24, "y": 0, "w": 24, "h": 15, "i": "2"},
            "type": "visualization",
            "embeddableConfig": {},
        },
        {
            "panelIndex": "3",
            "gridData": {"x": 0, "y": 15, "w": 16, "h": 15, "i": "3"},
            "type": "visualization",
            "embeddableConfig": {},
        },
        {
            "panelIndex": "4",
            "gridData": {"x": 16, "y": 15, "w": 16, "h": 15, "i": "4"},
            "type": "visualization",
            "embeddableConfig": {},
        },
        {
            "panelIndex": "5",
            "gridData": {"x": 32, "y": 15, "w": 16, "h": 15, "i": "5"},
            "type": "visualization",
            "embeddableConfig": {},
        },
    ]
    panel_refs = [
        ("panel_1", "ops-copilot-tokens-per-run"),
        ("panel_2", "ops-copilot-p95-latency"),
        ("panel_3", "ops-copilot-tool-frequency"),
        ("panel_4", "ops-copilot-provider-mix"),
        ("panel_5", "ops-copilot-success-rate"),
    ]
    for panel, (ref_name, _) in zip(panels, panel_refs, strict=True):
        panel["panelRefName"] = ref_name

    description = (
        "Tokens, latency, tool-call frequency, provider mix, and run success rate for the "
        "ops-copilot agent -- built via the saved-objects API (session 12), sourced from real "
        "OTel GenAI spans (session 11)."
    )
    dashboard_search_source = json.dumps({"query": {"query": "", "language": "kuery"}, "filter": []})
    dashboard_body = {
        "attributes": {
            "title": "Ops Copilot Overview",
            "description": description,
            "panelsJSON": json.dumps(panels),
            # Pinned, not a relative window: this dashboard's real data is a fixed handful of
            # spans from a specific demo run (2026-09-01, ~03:30:33-03:37:16 UTC), not a live
            # stream -- a relative "last 15 minutes" default would show empty panels the moment
            # anyone opens this outside that window.
            "timeRestore": True,
            "timeFrom": "2026-09-01T03:20:00.000Z",
            "timeTo": "2026-09-01T03:45:00.000Z",
            "kibanaSavedObjectMeta": {"searchSourceJSON": dashboard_search_source},
        },
        "references": [
            {"id": obj_id, "name": ref_name, "type": "visualization"} for ref_name, obj_id in panel_refs
        ],
    }
    resp = c.post(
        "/api/saved_objects/dashboard/ops-copilot-overview", json=dashboard_body, params={"overwrite": "true"}
    )
    resp.raise_for_status()
    print("  created dashboard 'ops-copilot-overview'")

    dash_id = resp.json()["id"]
    print(f"\nDashboard URL: {(env('KIBANA_URL', 'http://localhost:5601') or '').rstrip('/')}"
          f"/app/dashboards#/view/{dash_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
