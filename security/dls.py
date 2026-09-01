"""Document-level security: mint per-user Elasticsearch API keys whose role descriptor
restricts ops-runbooks to a single `department`, demonstrating that the same alert run as two
different users retrieves (and therefore cites) different runbooks.

Real per-user auth/identity is out of scope for this project (see docs/security.md); this maps
a fixed, small set of demo usernames to departments, which is enough to prove the DLS mechanism
end to end -- the enterprise story is "swap this map for your IdP," not "build an IdP."
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from elasticsearch import Elasticsearch

from common.es_client import get_es_client

ROOT = Path(__file__).resolve().parent.parent
KEYS_CACHE_PATH = ROOT / "security" / ".user_api_keys.json"

# Demo user -> department. In a real deployment this would come from the IdP/SSO claims.
DEMO_USERS: dict[str, str] = {
    "alice": "platform-engineering",
    "bob": "database-reliability",
    "carol": "networking",
    "dave": "security-compliance",
    "erin": "observability",
}


def _role_descriptor(department: str) -> dict[str, Any]:
    return {
        "ops-copilot-user-role": {
            "indices": [
                {
                    "names": ["ops-runbooks"],
                    "privileges": ["read"],
                    "query": {"term": {"department": department}},
                },
                {"names": ["ops-incidents"], "privileges": ["read"]},
                {"names": ["ops-logs-*"], "privileges": ["read"]},
                {"names": ["ops-postmortems"], "privileges": ["read", "write", "create_index"]},
            ]
        }
    }


def mint_user_api_key(es: Elasticsearch, username: str, department: str) -> str:
    """Creates (or re-mints, since API keys can't be read back) an API key scoped to
    `department` via DLS on ops-runbooks. Returns the base64 `encoded` key for Elasticsearch(api_key=...)."""
    resp = es.security.create_api_key(
        name=f"ops-copilot-{username}",
        role_descriptors=_role_descriptor(department),
    )
    return str(resp["encoded"])


def _load_cache() -> dict[str, str]:
    if KEYS_CACHE_PATH.exists():
        cache: dict[str, str] = json.loads(KEYS_CACHE_PATH.read_text())
        return cache
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    KEYS_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def get_or_mint_user_api_key(username: str) -> str:
    if username not in DEMO_USERS:
        raise ValueError(f"unknown demo user {username!r}, choose from {sorted(DEMO_USERS)}")
    cache = _load_cache()
    if username in cache:
        return cache[username]

    es = get_es_client()
    key = mint_user_api_key(es, username, DEMO_USERS[username])
    cache[username] = key
    _save_cache(cache)
    return key


def main() -> int:
    es = get_es_client()
    print(f"minting API keys for {len(DEMO_USERS)} demo users...")
    cache: dict[str, str] = {}
    for username, department in DEMO_USERS.items():
        key = mint_user_api_key(es, username, department)
        cache[username] = key
        print(f"  {username} (department={department}): key minted")
    _save_cache(cache)
    print(f"wrote {KEYS_CACHE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
