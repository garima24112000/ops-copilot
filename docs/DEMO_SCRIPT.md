# Demo script (3 minutes)

Shot-by-shot script for a screen recording (OBS Studio or similar). No slides, no talking
head — terminal and Kibana only, per the plan. Run everything from the repo root with the
stack already up (`make up`, healthy) and the corpus already ingested (`make ingest`).

Every command below is a real, working command in this repo — nothing here is aspirational.

---

## Shot 1 — the alert comes in (0:00–0:30)

**Say:** "An alert fires. Ops Copilot is going to triage it, find the right runbook, check
live telemetry, and stop to ask a human before it touches anything."

**Show:** the sample alert file.

```bash
cat data/sample_alert.json
```

**On screen:** a realistic alert JSON — `service`, `severity`, `metric`, `message`.

## Shot 2 — the agent runs (0:30–1:30)

**Run:**

```bash
python cli.py data/sample_alert.json
```

**On screen, as it streams:** each node printing its own name and output —
`triage` (normalized service/severity), `ground` (the retrieved runbook id, title, and a
truncated snippet — call out that it's a snippet, not the whole document, and say why: token
budget), `diagnose` (the hypothesis and whether telemetry confirmed or refuted it).

**Say while `ground` prints:** "That's hybrid retrieval — BM25 and ELSER combined with
reciprocal rank fusion — pulling the top match out of the real GitLab and Prometheus Operator
runbooks this project indexed, not a synthetic corpus."

## Shot 3 — the approval gate (1:30–2:00)

**On screen:** the `== APPROVAL REQUIRED ==` block — the proposed action (`create_ticket` or
`restart_service`), the rationale, the hypothesis.

**Say:** "This is the part that makes it enterprise-credible, not a toy. Nothing with a side
effect happens without a human in the loop."

**Type `y` and hit enter** (or re-run with `--approve` beforehand if recording unattended is
easier — either is a real code path, not a mocked one).

**On screen:** the `act` node's result (a real mock ticket id / restart confirmation), then
`record` writing the postmortem id.

## Shot 4 — cut to the APM trace (2:00–2:30)

**Switch to browser, Kibana APM (`http://localhost:5601` → Observability → APM → Traces).**

**On screen:** the trace for this exact run — the `invoke_agent` root span, `chat` child spans
(with token counts on them — point the cursor at `gen_ai.usage.input_tokens` /
`gen_ai.usage.output_tokens`), `execute_tool` child spans for each MCP tool call.

**Say:** "Every LLM call and every tool call in that run just showed up here, with real token
counts, in the same Elasticsearch cluster the agent itself queries. You debug the agent with
the same stack the agent runs on."

## Shot 5 — the dashboard (2:30–2:50)

**Switch to the Ops Copilot Overview dashboard** (Kibana → Dashboards).

**On screen:** tokens-per-run over time, p95 latency, tool-call frequency, provider mix panels
— built via the saved-objects API (session 12), not clicked together live.

## Shot 6 — the numbers (2:50–3:00)

**Switch back to terminal:**

```bash
cat evals/results/ablation.json | python3 -m json.tool | head -20
```

**Say (reading the real committed number, whatever it is at record time — never a rehearsed
figure):** "This is the retrieval ablation — BM25, dense, ELSER, and hybrid RRF, computed with
zero LLM calls, straight from Elasticsearch. [state the actual hybrid RRF recall@5 from the
file on screen]. That's the whole pitch: Elasticsearch as the agent's brain, its memory, and
its observability backend, for zero dollars."

---

## Notes for whoever records this

- Every number spoken on camera must be read live from a file under `evals/results/` at
  record time — this project's own rule (CLAUDE.md) is "never fabricate a number," and that
  applies to the video too.
- If a run picks a `create_ticket` action instead of `restart_service` (or vice versa), that's
  fine — say whichever the run actually proposed. Don't re-run hoping for a more dramatic
  action; a `create_ticket` run is just as real a demonstration of the approval gate.
- Total runtime should land close to 3:00. If short on time, cut shot 5 (the dashboard) before
  cutting shot 4 (the trace) — the trace is closer to the project's actual differentiator.
