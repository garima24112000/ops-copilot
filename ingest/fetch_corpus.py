"""Fetch three real, public sources into clean JSONL under data/:

1. GitLab's production runbooks (gitlab-com/runbooks, docs/**/*.md) via the GitLab API.
2. Prometheus Operator alert runbooks (prometheus-operator/runbooks,
   content/runbooks/**/*.md) via the GitHub API.
3. A subset of Loghub (logpai/loghub) real system log samples (~2k lines per system,
   the freely downloadable benchmark samples — full-size datasets require a request form
   and are out of scope for a zero-cost, no-signup pipeline).

Normalised runbook schema: id, title, body, service, source_url, department.
department is a synthetic field assigned round-robin from a fixed list, for the later
document-level security demo (session 13) -- it is not present in the source data.

Run: python ingest/fetch_corpus.py
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUNBOOKS_CAP = 120
LOG_LINES_TARGET = 50_000

DEPARTMENTS = [
    "platform-engineering",
    "database-reliability",
    "networking",
    "security-compliance",
    "observability",
]

GITLAB_PROJECT = "gitlab-com%2Frunbooks"
GITLAB_API = "https://gitlab.com/api/v4"
GITLAB_REF = "master"

GITHUB_REPO = "prometheus-operator/runbooks"
GITHUB_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"

LOGHUB_SYSTEMS = [
    "HDFS", "Hadoop", "Zookeeper", "OpenStack", "Apache", "Linux",
    "HealthApp", "OpenSSH", "Spark", "Thunderbird", "BGL", "Mac",
    "Android", "HPC", "Proxifier", "Windows",
]
LOGHUB_RAW = "https://raw.githubusercontent.com/logpai/loghub/master"

LEVEL_RE = re.compile(r"\b(FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b", re.IGNORECASE)

client = httpx.Client(timeout=30.0, follow_redirects=True)


@dataclass
class Runbook:
    id: str
    title: str
    body: str
    service: str
    source_url: str
    department: str


def _department(index: int) -> str:
    return DEPARTMENTS[index % len(DEPARTMENTS)]


def _clean_markdown(raw: str) -> str:
    """Strip YAML frontmatter, Liquid/Hugo templating, and image-only lines."""
    text = raw.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2].strip()
    text = re.sub(r"\{\{.*?\}\}", "", text)
    text = re.sub(r"\{%.*?%\}", "", text)
    lines = [ln for ln in text.splitlines() if not re.match(r"^\s*!\[.*\]\(.*\)\s*$", ln)]
    text = "\n".join(lines).strip()
    return text


def fetch_gitlab_runbooks(limit: int) -> list[Runbook]:
    print(f"fetching GitLab runbooks (docs/**/*.md), cap {limit}...")
    md_paths: list[str] = []
    page = 1
    while len(md_paths) < limit * 3 and page <= 20:
        url = (
            f"{GITLAB_API}/projects/{GITLAB_PROJECT}/repository/tree"
            f"?path=docs&recursive=true&per_page=100&page={page}"
        )
        resp = client.get(url)
        if resp.status_code != 200:
            break
        items = resp.json()
        if not items:
            break
        md_paths.extend(
            it["path"] for it in items if it["type"] == "blob" and it["path"].endswith(".md")
        )
        page += 1

    md_paths = sorted(set(md_paths))
    runbooks: list[Runbook] = []
    for i, path in enumerate(md_paths):
        if len(runbooks) >= limit:
            break
        encoded = urllib.parse.quote(path, safe="")
        raw_url = f"{GITLAB_API}/projects/{GITLAB_PROJECT}/repository/files/{encoded}/raw?ref={GITLAB_REF}"
        resp = client.get(raw_url)
        if resp.status_code != 200:
            continue
        body = _clean_markdown(resp.text)
        if len(body) < 200:  # skip stubs / empty pages
            continue
        title = Path(path).stem
        service = path.split("/")[1] if path.count("/") >= 1 else "general"
        runbooks.append(
            Runbook(
                id=f"gitlab-{title}",
                title=title,
                body=body,
                service=service,
                source_url=f"https://gitlab.com/gitlab-com/runbooks/-/blob/{GITLAB_REF}/{path}",
                department=_department(len(runbooks)),
            )
        )
        if len(runbooks) % 20 == 0:
            print(f"  gitlab: {len(runbooks)} runbooks so far...")
    print(f"gitlab runbooks: {len(runbooks)} kept")
    return runbooks


def fetch_prometheus_operator_runbooks(limit: int) -> list[Runbook]:
    print(f"fetching Prometheus Operator runbooks, cap {limit}...")
    tree_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/main?recursive=1"
    resp = client.get(tree_url)
    resp.raise_for_status()
    tree = resp.json()["tree"]
    md_paths = [
        t["path"]
        for t in tree
        if t["path"].startswith("content/runbooks/")
        and t["path"].endswith(".md")
        and not t["path"].endswith("_index.md")
    ]
    md_paths.sort()

    runbooks: list[Runbook] = []
    for path in md_paths:
        if len(runbooks) >= limit:
            break
        raw_url = f"{GITHUB_RAW}/{path}"
        resp = client.get(raw_url)
        if resp.status_code != 200:
            continue
        body = _clean_markdown(resp.text)
        if len(body) < 200:
            continue
        title = Path(path).stem
        service = path.split("/")[2] if path.count("/") >= 2 else "general"
        runbooks.append(
            Runbook(
                id=f"promop-{title}",
                title=title,
                body=body,
                service=service,
                source_url=f"https://github.com/{GITHUB_REPO}/blob/main/{path}",
                department=_department(len(runbooks)),
            )
        )
        if len(runbooks) % 20 == 0:
            print(f"  prometheus-operator: {len(runbooks)} runbooks so far...")
    print(f"prometheus-operator runbooks: {len(runbooks)} kept")
    return runbooks


def fetch_loghub_subset() -> list[dict]:
    print(f"fetching Loghub subset from {len(LOGHUB_SYSTEMS)} systems...")
    docs: list[dict] = []
    for system in LOGHUB_SYSTEMS:
        url = f"{LOGHUB_RAW}/{system}/{system}_2k.log"
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"  skip {system}: HTTP {resp.status_code}")
            continue
        lines = resp.text.splitlines()
        base_ts = time.time() - 3600  # spread the sample over the last hour, most-recent-last
        step = 3600 / max(len(lines), 1)
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            m = LEVEL_RE.search(line)
            level = m.group(1).upper() if m else "INFO"
            if level == "WARNING":
                level = "WARN"
            docs.append(
                {
                    "@timestamp": time.strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base_ts + i * step)
                    ),
                    "service": system,
                    "level": level,
                    "message": line[:2000],
                }
            )
        print(f"  {system}: {len(lines)} lines")
    print(f"loghub subset: {len(docs)} lines total")
    return docs


def write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            obj = asdict(row) if hasattr(row, "__dataclass_fields__") else row
            f.write(json.dumps(obj) + "\n")


def main() -> None:
    gitlab_cap = RUNBOOKS_CAP * 2 // 3
    promop_cap = RUNBOOKS_CAP - gitlab_cap

    gitlab_runbooks = fetch_gitlab_runbooks(gitlab_cap)
    promop_runbooks = fetch_prometheus_operator_runbooks(promop_cap)
    all_runbooks = gitlab_runbooks + promop_runbooks

    log_docs = fetch_loghub_subset()

    write_jsonl(DATA_DIR / "runbooks.jsonl", all_runbooks)
    write_jsonl(DATA_DIR / "logs.jsonl", log_docs)

    print("\n=== fetch summary ===")
    print(f"{'source':<28} {'count':>8}")
    print(f"{'gitlab-com/runbooks':<28} {len(gitlab_runbooks):>8}")
    print(f"{'prometheus-operator/runbooks':<28} {len(promop_runbooks):>8}")
    print(f"{'TOTAL runbooks':<28} {len(all_runbooks):>8}  -> data/runbooks.jsonl")
    print(f"{'loghub log lines':<28} {len(log_docs):>8}  -> data/logs.jsonl")
    services = sorted({r.service for r in all_runbooks})
    print(f"\ndistinct runbook services ({len(services)}): {', '.join(services[:15])}"
          f"{'...' if len(services) > 15 else ''}")


if __name__ == "__main__":
    main()
