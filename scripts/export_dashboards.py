"""Export the ops-copilot Kibana dashboard (and its alert rule) as saved objects to
dashboards/, via the Kibana saved-objects API -- built by hand in Kibana (session 12 is done
in the Kibana UI, not by Claude Code clicking around), then exported here so a fresh stack can
reproduce it with `make import-dashboards`.

Run: python -m scripts.export_dashboards
"""

from __future__ import annotations

from pathlib import Path

import httpx

from common.config import env

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS_DIR = ROOT / "dashboards"
EXPORT_FILE = DASHBOARDS_DIR / "ops_copilot_dashboard.ndjson"

# The dashboard's saved-object id, set once you've built it in Kibana (Stack Management ->
# Saved Objects -> find "Ops Copilot Overview" -> copy its id from the URL or the object
# inspector). Alert rules are exported alongside via type "alert" / "alerting rule" objects
# that reference the dashboard, if you add one referencing it.
DASHBOARD_TITLE = "Ops Copilot Overview"


def main() -> int:
    kibana_url = (env("KIBANA_URL", "http://localhost:5601") or "http://localhost:5601").rstrip("/")
    password = env("ELASTIC_PASSWORD", "changeme_local_only") or "changeme_local_only"
    auth = ("elastic", password)

    client = httpx.Client(base_url=kibana_url, auth=auth, headers={"kbn-xsrf": "true"})

    resp = client.post(
        "/api/saved_objects/_find",
        params={"type": "dashboard", "search": DASHBOARD_TITLE, "search_fields": "title"},
    )
    resp.raise_for_status()
    hits = resp.json().get("saved_objects", [])
    if not hits:
        print(
            f"no dashboard titled {DASHBOARD_TITLE!r} found in Kibana. Build it first "
            "(see docs/architecture.md for the panel list), then re-run this script."
        )
        return 1

    objects_to_export = [{"type": "dashboard", "id": hits[0]["id"]}]
    export_resp = client.post(
        "/api/saved_objects/_export",
        json={"objects": objects_to_export, "includeReferencesDeep": True},
    )
    export_resp.raise_for_status()

    DASHBOARDS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_FILE.write_bytes(export_resp.content)
    n_objects = len(export_resp.text.strip().splitlines())
    print(f"exported {n_objects} saved objects (dashboard + panels + refs) -> {EXPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
