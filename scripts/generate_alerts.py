"""Reverse-generate alert/runbook ground-truth pairs with a local Ollama model.

For each runbook: (1) produce a 2-sentence summary, (2) from the summary ALONE generate a
realistic monitoring alert payload (service, severity, metric, message) that this runbook
would resolve, in operator vocabulary rather than runbook vocabulary (mitigates the
LLM-generated-query bias named in the plan: generating from a summary rather than the full
text, and instructing the model not to reuse distinctive source phrases).

Also emits a lightweight, TEMPLATED (not LLM-generated) "past incident" record per alert into
data/incidents.jsonl, so ops-incidents has real content grounded in the same corpus. Session 3
of the runbook only specifies fetching runbooks + logs, with no separate incidents source, so
this is a deliberate build-time decision (documented in PROGRESS.md) rather than fabricating
incident data from nothing.

Model choice: qwen3:4b (Qwen3, a reasoning model) was tried first and burns 500-2000+ "thinking"
tokens even on trivial prompts -- confirmed empirically (a 3-word reply took ~50s+ with the
Elastic stack down, and much longer with it up). That is not viable for ~200-400 bulk calls.
Switched to llama3.2:1b (plain instruct, no hidden reasoning channel): 0.18s for a comparable
prompt. This script targets llama3.2:1b by default; override with OLLAMA_GEN_MODEL.

Resumable: reads existing data/alerts.jsonl / data/incidents.jsonl first and skips
(runbook_id, variant) pairs already present, so an interrupted run does not start over.

Run: python scripts/generate_alerts.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from common.config import env

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNBOOKS_PATH = DATA_DIR / "runbooks.jsonl"
ALERTS_PATH = DATA_DIR / "alerts.jsonl"
INCIDENTS_PATH = DATA_DIR / "incidents.jsonl"

MODEL = env("OLLAMA_GEN_MODEL", "llama3.2:1b")
OLLAMA_URL = (env("OLLAMA_BASE_URL", "http://localhost:11434") or "http://localhost:11434").rstrip("/")
TARGET_PAIRS = 200
VARIANTS_PER_RUNBOOK = 2
SEVERITIES = ["critical", "warning", "info"]

client = httpx.Client(timeout=60.0)


def ollama_generate(prompt: str, max_tokens: int = 300) -> str:
    resp = client.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.7},
        },
    )
    resp.raise_for_status()
    text: str = resp.json()["response"].strip()
    return text


def summarize(runbook_body: str) -> str:
    prompt = (
        "Summarize the following internal engineering runbook in EXACTLY 2 sentences. "
        "Focus only on: what problem/symptom it addresses, and how it is resolved. "
        "Do not copy distinctive phrases verbatim, do not mention 'runbook' or a title.\n\n"
        f"RUNBOOK:\n{runbook_body[:1500]}\n\nTWO-SENTENCE SUMMARY:"
    )
    return ollama_generate(prompt, max_tokens=120)


ALERT_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


MESSAGE_TEMPLATES = [
    "{symptom}. Threshold breached, escalating to on-call.",
    "ALERT: {symptom}",
    "{symptom} -- SLO at risk.",
    "{symptom}",
    "Paging on-call: {symptom}",
]


def generate_alert(summary: str, variant_seed: int) -> dict[str, Any] | None:
    """A small (1B) local model can't reliably juggle "paraphrase this AND make it terse AND
    make it operator-flavored AND don't just copy an example" all at once -- tried that
    (verified empirically, see PROGRESS.md session 6: it started copying the prompt's own
    example verbatim into unrelated alerts, or returned nothing at all). Split into two
    easier sub-tasks instead: ask the model only to extract a specific, paraphrased symptom
    (one job), then build the operator-flavored message deterministically in Python from a
    small template set (a second, non-LLM job) -- guarantees every alert message actually
    contains the runbook's real symptom instead of risking a content-free generic phrase."""
    severity_hint = SEVERITIES[variant_seed % len(SEVERITIES)]
    prompt = (
        "Extract the SPECIFIC symptom or failure mode from this incident summary, in your own "
        "words, as a short phrase (8-20 words). Name the actual component/metric/behavior "
        "that's wrong -- do not write a generic phrase like 'errors detected' or 'threshold "
        "exceeded' with nothing else. Also give a short lowercase-kebab service name and a "
        "short metric name a monitoring system would emit.\n\n"
        "Respond with ONLY a JSON object: "
        '{"service": "...", "metric": "...", "symptom": "..."}\n\n'
        f"INCIDENT SUMMARY:\n{summary}"
    )
    raw = ollama_generate(prompt, max_tokens=150)
    m = ALERT_JSON_RE.search(raw)
    if not m:
        return None
    try:
        payload: dict[str, Any] = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    required = {"service", "metric", "symptom"}
    if not required.issubset(payload.keys()) or not payload["symptom"].strip():
        return None

    template = MESSAGE_TEMPLATES[variant_seed % len(MESSAGE_TEMPLATES)]
    message = template.format(symptom=payload["symptom"].strip())
    return {
        "service": payload["service"],
        "severity": severity_hint,
        "metric": payload["metric"],
        "message": message[:200],
    }


def load_existing(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen = set()
    with path.open() as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                seen.add(f"{row['runbook_id']}::{row['variant']}")
    return seen


def main() -> int:
    if not RUNBOOKS_PATH.exists():
        print(f"missing {RUNBOOKS_PATH}, run ingest/fetch_corpus.py first", file=sys.stderr)
        return 1
    runbooks = [json.loads(line) for line in RUNBOOKS_PATH.open() if line.strip()]

    done = load_existing(ALERTS_PATH)
    print(f"resuming: {len(done)} alert/runbook pairs already generated")

    alerts_f = ALERTS_PATH.open("a")
    incidents_f = INCIDENTS_PATH.open("a")
    generated = len(done)
    t0 = time.time()

    try:
        for variant in range(VARIANTS_PER_RUNBOOK):
            for runbook in runbooks:
                if generated >= TARGET_PAIRS:
                    break
                key = f"{runbook['id']}::{variant}"
                if key in done:
                    continue
                try:
                    summary = summarize(runbook["body"])
                    alert = generate_alert(summary, variant_seed=variant * 7 + hash(runbook["id"]) % 3)
                except httpx.HTTPError as exc:
                    print(f"  ollama HTTP error on {runbook['id']}: {exc}, skipping", file=sys.stderr)
                    continue
                if alert is None:
                    print(f"  could not parse alert JSON for {runbook['id']} variant {variant}, skipping")
                    continue

                alert_row = {
                    "runbook_id": runbook["id"],
                    "variant": variant,
                    "summary": summary,
                    **alert,
                }
                alerts_f.write(json.dumps(alert_row) + "\n")
                alerts_f.flush()

                incident_row = {
                    "id": f"incident-{runbook['id']}-{variant}",
                    "title": f"{alert['service']} {alert['severity']} — {alert['metric']}",
                    "summary": f"{alert['message']} Root cause matched the '{runbook['title']}' runbook.",
                    "resolution": (
                        f"On-call followed the '{runbook['title']}' runbook and confirmed the "
                        f"{alert['metric']} metric returned to baseline after mitigation."
                    ),
                    "service": alert["service"],
                    "department": runbook["department"],
                    "severity": alert["severity"],
                    "related_runbook_id": runbook["id"],
                    "created_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z",
                        time.gmtime(time.time() - (generated + 1) * 3600 * 6),
                    ),
                }
                incidents_f.write(json.dumps(incident_row) + "\n")
                incidents_f.flush()

                generated += 1
                if generated % 10 == 0:
                    elapsed = time.time() - t0
                    print(f"  {generated}/{TARGET_PAIRS} pairs, {elapsed:.0f}s elapsed")
            if generated >= TARGET_PAIRS:
                break
    finally:
        alerts_f.close()
        incidents_f.close()

    print(f"\ndone: {generated} alert/incident pairs -> {ALERTS_PATH}, {INCIDENTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
