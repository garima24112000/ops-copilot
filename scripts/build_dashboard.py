"""Session 12: build the Ops Copilot Kibana dashboard entirely through the saved-objects API
(no UI clicking), then verify it via GET before exporting (scripts/export_dashboards.py).

Panels: tokens per run over time, p95 latency by span, tool-call frequency, provider mix,
run success rate -- all against the real traces-apm-default data view, built from the actual
OTel GenAI spans this project emits (session 11).

Run: python -m scripts.build_dashboard
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from common.config import env

DATA_VIEW_ID = "1a0e73f9-7552-4c7e-a2d7-ce93c12b92b6"  # traces-apm-default, created earlier


def client() -> httpx.Client:
    kibana_url = (env("KIBANA_URL", "http://localhost:5601") or "http://localhost:5601").rstrip("/")
    password = env("ELASTIC_PASSWORD", "changeme_local_only") or "changeme_local_only"
    return httpx.Client(base_url=kibana_url, auth=("elastic", password), headers={"kbn-xsrf": "true"})


def put_visualization(c: httpx.Client, obj_id: str, title: str, vis_state: dict[str, Any], query: str) -> None:
    body = {
        "attributes": {
            "title": title,
            "visState": json.dumps(vis_state),
            "uiStateJSON": "{}",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({"query": {"query": query, "language": "kuery"}, "filter": []})
            },
        },
        "references": [
            {
                "id": DATA_VIEW_ID,
                "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
                "type": "index-pattern",
            }
        ],
    }
    resp = c.post(f"/api/saved_objects/visualization/{obj_id}", json=body)
    resp.raise_for_status()
    print(f"  created visualization {obj_id!r}")


def main() -> int:
    c = client()

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
        query='span.name: "chat*"',
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
                    "params": {"field": "span.name", "size": 10, "order": "desc", "orderBy": "1"},
                },
            ],
        },
        query="processor.event: span",
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
        query='span.name: "execute_tool*"',
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
        query='span.name: "chat*"',
    )

    # run success rate (metric, filter-ratio)
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
                    "colorsRange": [{"from": 0, "to": 10000}],
                    "labels": {"show": True},
                    "invertColors": False,
                    "style": {"bgFill": "#000", "bgColor": False, "labelColor": False, "subText": "", "fontSize": 60},
                },
            },
            "aggs": [
                {
                    "id": "1",
                    "enabled": True,
                    "type": "filter_ratio",
                    "schema": "metric",
                    "params": {
                        "numeratorLabel": "successful runs",
                        "denominatorLabel": "all runs",
                        "numerator": {"query": "event.outcome: success", "language": "kuery"},
                        "denominator": {"query": "*", "language": "kuery"},
                    },
                }
            ],
        },
        query="processor.event: transaction and transaction.name: invoke_agent*",
    )

    # dashboard referencing all 5 panels
    panels = [
        {"panelIndex": "1", "gridData": {"x": 0, "y": 0, "w": 24, "h": 15, "i": "1"}, "type": "visualization"},
        {"panelIndex": "2", "gridData": {"x": 24, "y": 0, "w": 24, "h": 15, "i": "2"}, "type": "visualization"},
        {"panelIndex": "3", "gridData": {"x": 0, "y": 15, "w": 16, "h": 15, "i": "3"}, "type": "visualization"},
        {"panelIndex": "4", "gridData": {"x": 16, "y": 15, "w": 16, "h": 15, "i": "4"}, "type": "visualization"},
        {"panelIndex": "5", "gridData": {"x": 32, "y": 15, "w": 16, "h": 15, "i": "5"}, "type": "visualization"},
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
            "timeRestore": False,
            "kibanaSavedObjectMeta": {"searchSourceJSON": dashboard_search_source},
        },
        "references": [
            {"id": obj_id, "name": ref_name, "type": "visualization"} for ref_name, obj_id in panel_refs
        ],
    }
    resp = c.post("/api/saved_objects/dashboard/ops-copilot-overview", json=dashboard_body)
    resp.raise_for_status()
    print("  created dashboard 'ops-copilot-overview'")

    dash_id = resp.json()["id"]
    print(f"\nDashboard URL: {(env('KIBANA_URL', 'http://localhost:5601') or '').rstrip('/')}"
          f"/app/dashboards#/view/{dash_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
