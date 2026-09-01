"""Import dashboards/ops_copilot_dashboard.ndjson into a fresh Kibana via the saved-objects
import API, recreating the hand-built dashboard from session 12.

Run: python -m scripts.import_dashboards
"""

from __future__ import annotations

from pathlib import Path

import httpx

from common.config import env

ROOT = Path(__file__).resolve().parent.parent
EXPORT_FILE = ROOT / "dashboards" / "ops_copilot_dashboard.ndjson"


def main() -> int:
    if not EXPORT_FILE.exists():
        print(f"{EXPORT_FILE} not found. Run `make export-dashboards` against a stack that has it first.")
        return 1

    kibana_url = (env("KIBANA_URL", "http://localhost:5601") or "http://localhost:5601").rstrip("/")
    password = env("ELASTIC_PASSWORD", "changeme_local_only") or "changeme_local_only"
    auth = ("elastic", password)

    client = httpx.Client(base_url=kibana_url, auth=auth, headers={"kbn-xsrf": "true"})
    with EXPORT_FILE.open("rb") as f:
        resp = client.post(
            "/api/saved_objects/_import",
            params={"overwrite": "true"},
            files={"file": (EXPORT_FILE.name, f, "application/ndjson")},
        )
    resp.raise_for_status()
    result = resp.json()
    print(f"import success={result.get('success')}, imported {result.get('successCount')} objects")
    if result.get("errors"):
        print("errors:", result["errors"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
