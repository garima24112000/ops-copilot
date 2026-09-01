# Ops Copilot — Claude Code Execution Runbook

Ordered sessions to go from empty folder to submittable project in ~14 working hours.

**Suggested split:** tonight 5-6h (sessions 0-7), tomorrow morning 4h (sessions 8-11), tomorrow afternoon 4h (sessions 12-17).

---

## Ground rules

**One session per step.** Run `/clear` between them. A single 14-hour context window degrades badly and Claude Code starts contradicting decisions it made four hours earlier.

**Commit at every gate.** `git commit -am "gate N passed"`. When a session goes sideways you want a clean point to reset to, and the commit history is itself a signal to whoever reviews the repo.

**Use plan mode for sessions 9 and 10.** Shift+Tab twice. Those two have real architectural branching and you want to approve the approach before any files get written.

**You run Docker commands, not Claude Code.** Long-running container startup inside a tool call will time out or eat your RAM headroom while the agent keeps working. Run them in your own terminal.

**Gates are yours to judge.** When Claude Code says "retrieval is working," open Kibana and look yourself. It will declare victory on mediocre results.

---

## Session 0 — Scaffold and CLAUDE.md (20 min)

You, in terminal:

```bash
mkdir ops-copilot && cd ops-copilot && git init && code .
```

Drop `ops-copilot-zero-cost-plan.md` into the folder. Then in Claude Code:

> Read ops-copilot-zero-cost-plan.md. Create a CLAUDE.md at the repo root that captures the hard constraints as durable project rules. It must include: zero-cost only (no paid APIs, no cloud deployments, no credit card anywhere); target 4-5K tokens per agent run; free-tier LLM providers are rate-limited on tokens-per-minute so all LLM calls go through a cached router; retrieval evals must never make LLM calls; RAM is the binding local constraint so Ollama and the Elastic ML node never run simultaneously; Python 3.11+, ruff, mypy, pytest; never commit .env or API keys. Also create .gitignore, .env.example, pyproject.toml with the dependency list, and an empty Makefile with targets: setup, up, down, ingest, demo, evals, lint. Do not implement anything yet.

**Gate:** CLAUDE.md exists and reads like rules, not a summary. Edit it by hand if it drifted.

---

## Session 1 — Docker stack (30 min)

> Write docker-compose.yml for a single-node Elasticsearch 9.x with the self-generated trial licence, Kibana, and APM Server with OTLP intake enabled. ES heap 4GB, ML enabled. Security on, with a bootstrap password from .env. Add healthchecks and a `make up` / `make down` target. Include a `scripts/wait_for_stack.sh` that blocks until all three are green.

You, in terminal:

```bash
make up
```

**Gate:** `curl -u elastic:$PASS localhost:9200` returns cluster info, Kibana loads at :5601, APM Server health endpoint responds. Do not proceed until all three are true.

---

## Session 2 — ELSER (20 min, mostly waiting)

This is the highest-risk step in the whole project. Hit it now, not at hour six.

> Write scripts/deploy_elser.py that creates an ELSER inference endpoint via the Elasticsearch inference API, downloads and deploys the model, and polls until it reports fully allocated. Check the current Elasticsearch docs for the correct API shape rather than assuming — this API has changed across versions. Then write a smoke test that indexes one document with a semantic_text field and retrieves it semantically.

**Gate:** the smoke test passes and returns a sparse vector.

**If ELSER will not start** (usually RAM), stop here and tell Claude Code: *"ELSER cannot run on this machine. Switch the plan to the dense-only path: bge-small-en-v1.5 via sentence-transformers, precomputed vectors into a dense_vector field, no ES ML node. Update CLAUDE.md."* Then continue. Your ablation loses one row and gains nothing worse.

---

## Session 3 — Corpus fetch (45 min)

> Write ingest/fetch_corpus.py that pulls three real public sources into clean JSONL under data/: (1) markdown runbooks from the public gitlab-com/runbooks repository, (2) alert runbooks from the Prometheus Operator runbooks site, (3) a subset of Loghub logs, roughly 50k lines, from logpai/loghub. Parse each into a normalised schema: id, title, body, service, source_url. Strip nav chrome and templating. Assign a synthetic `department` field from a fixed list for later document-level security. Cap runbooks at ~120 documents. Print a summary table of what was fetched.

You: run it, then open three of the output records and read them.

**Gate:** the bodies are actual runbook prose, not YAML frontmatter or empty strings. This fails more often than you'd expect and it silently poisons everything downstream.

---

## Session 4 — Alert reverse-generation (45 min, mostly runtime)

You, in terminal, first:

```bash
make down          # free the RAM
ollama serve &
ollama pull qwen3:4b    # or any small instruct model you already have
```

Then:

> Write scripts/generate_alerts.py. For each runbook, first produce a 2-sentence summary, then from the summary alone generate a realistic monitoring alert payload (service, severity, metric, message) that this runbook would resolve. The alert must use operator vocabulary, not runbook vocabulary — instruct the model explicitly to avoid reusing distinctive phrases from the source. Target ~200 alert/runbook pairs. Runs against local Ollama at localhost:11434. Write to data/alerts.jsonl with the source runbook id as ground truth. Make it resumable so an interrupted run doesn't start over.

**Gate:** 200 pairs in `data/alerts.jsonl`. Read five. If the alert text obviously parrots the runbook title, tighten the prompt and rerun — this directly inflates your BM25 numbers later.

Then: `pkill ollama && make up`

---

## Session 5 — Index and ingest (45 min)

> Write ingest/mappings/ for three indices: ops-runbooks with parallel body representations (body as text for BM25, body_semantic as semantic_text with the ELSER endpoint, body_dense as dense_vector from bge-small), ops-incidents, and an ops-logs-* data stream. Write ingest/load.py to bulk-load everything with progress output and idempotent reindexing. Add `make ingest`.

**Gate:** document counts match expectations and a `_search` on each index returns hits.

---

## Session 6 — Human retrieval check (20 min, no Claude Code)

Open Kibana Dev Tools. Write three hybrid RRF queries by hand using alerts you make up yourself, not from the generated set.

**Gate:** the correct runbook is in the top 3 for at least 2 of 3.

If it isn't, the problem is your mappings or your parsing, and no amount of agent code will hide it. Go back to session 3 or 5. **Do not proceed past this gate.** This is the single most common place these projects fail.

---

## Session 7 — The ablation (45 min) ← highest value per minute

> Write evals/retrieval/run_ablation.py. Evaluate four strategies over data/alerts.jsonl using the runbook id as ground truth: BM25 only, dense only, ELSER only, and hybrid RRF. Compute recall@5, nDCG@10, and p95 query latency for each. Zero LLM calls anywhere in this file. Write results to evals/results/ablation.json and index them into ops-agent-evals with the git SHA. Print a markdown table to stdout.

**Gate:** four rows of real numbers.

**Commit this immediately.** If everything after tonight goes wrong, this table plus a README is still a credible submission.

---

*— End of tonight. You should have: running stack, real corpus, working hybrid retrieval, and a results table. —*

---

## Session 8 — LLM router (30 min)

> Write agent/llm_router.py: a provider abstraction over Google AI Studio (Gemini Flash), Groq, and local Ollama, all behind one chat-with-tools interface. Requirements: disk cache keyed on a hash of (model, messages, tools) so repeated dev runs cost nothing; read rate-limit response headers and fail over to the next provider on 429 rather than hardcoding limits; record per-call input and output token counts; a record/replay mode that writes fixtures to fixtures/ for use in CI. Unit tests with a fake provider.

**Gate:** call the same prompt twice, second one is a cache hit, and killing your Gemini key still works via Groq.

---

## Session 9 — MCP server (45 min) — *plan mode*

> Plan then implement mcp_server/server.py using FastMCP, exposing five tools: search_runbooks(query, service?) doing hybrid RRF retrieval; find_similar_incidents(summary); query_service_health(service, window) running an ES|QL aggregation over ops-logs-*; create_ticket(title, body, severity) and restart_service(service) hitting a mock internal API in mcp_server/mock_api.py. The last two must be flagged as requiring approval. Standalone MCP server, not LangChain tools. Include a README section on pointing another MCP client at it.

**Gate:** MCP Inspector lists all five tools and `search_runbooks` returns real results.

Point Claude Code itself at your MCP server and ask it to search a runbook. That is thirty seconds of extremely good demo video.

---

## Session 10 — The agent (1.5h) — *plan mode*

> Plan then implement the LangGraph agent in agent/. Nodes: triage (classify severity and service), ground (retrieve runbook via MCP), diagnose (query telemetry via MCP), approve (interrupt before any side-effecting tool), act (execute approved tool), record (write a postmortem into ops-postmortems). State in agent/state.py. All LLM calls go through llm_router. Keep total context per run under 5K tokens — truncate retrieved snippets rather than passing full runbooks. Add cli.py taking an alert JSON path and printing each node transition.

**Gate:** `python cli.py data/sample_alert.json` runs end to end, pauses for approval, and writes a postmortem.

### ← Minimum viable submission line

If you're out of time, stop here, jump to session 17, and submit. This is already better than most portfolio projects.

---

## Session 11 — OTel instrumentation (1h)

> Add agent/telemetry.py using OpenTelemetry GenAI semantic conventions. Root span invoke_agent per run. A chat span per LLM call carrying gen_ai.provider.name, gen_ai.request.model, gen_ai.usage.input_tokens, gen_ai.usage.output_tokens. An execute_tool span per tool call with tool name and duration. Export OTLP to the local APM Server. These attributes are pre-1.0 and have changed before, so check the current OTel semantic conventions rather than assuming.

**Gate:** open APM in Kibana, find your trace, see the waterfall with token counts on the chat spans.

---

## Session 12 — Dashboard (45 min, mostly you)

Build it in Kibana yourself. Claude Code cannot click a UI, and this is faster by hand.

Panels: tokens per run over time, p95 latency by node, tool-call frequency, run success rate, provider mix. Then add one alert rule firing above a token threshold per run.

> Write scripts/export_dashboards.py that exports the Kibana saved objects for my dashboard and alert rule to dashboards/, and a matching import script. Add make targets for both.

**Gate:** `make import-dashboards` recreates it on a fresh stack.

---

## Session 13 — Document-level security (30 min)

> Add security/dls.py that mints per-user Elasticsearch API keys with role descriptors restricting ops-runbooks by the department field. Add a --user flag to cli.py that runs the agent under that user's key. Write docs/security.md covering the DLS design and a data-governance note on free-tier LLM providers training on submitted prompts, and why the corpus is public and the router is swappable.

**Gate:** the same alert run as two different users cites different sources. Screenshot this.

---

## Session 14 — End-to-end evals (1h)

**You write ten golden tasks by hand first.** Do not skip this and do not let Claude Code write them — it will write tasks your retrieval already answers, and your numbers become meaningless.

> Write evals/end_to_end/. Load golden_set.yaml with expected runbook id and expected tool sequence per task. Metrics: task success rate, tool-selection accuracy, mean tokens per run, p95 latency. Report the hand-written and generated subsets as separate rows — never merged. Write results to evals/results/ and index into ops-agent-evals.

**Gate:** a results table with the two subsets reported separately.

---

## Session 15 — CI (45 min)

> Write .github/workflows/ci.yml: ruff, mypy, pytest, and the retrieval ablation against an Elasticsearch service container using precomputed vectors loaded from fixtures — no ML node and no API calls in CI. Agent tests use replayed fixtures from the router. Fail the build if recall@5 drops below the current committed baseline.

**Gate:** green run on GitHub with zero API calls.

---

## Session 16 — Terraform (45 min) — *first cut candidate*

> Write infra/ using the elastic/elasticstack Terraform provider against the local cluster, managing index templates, ingest pipelines, the ELSER inference endpoint, roles, and API keys as code. Add a docker provider config for the containers. Include a README on apply against a local stack.

**Gate:** `terraform plan` is clean against your running cluster.

---

## Session 17 — Packaging (1.5h) — *never cut*

> Write the README. Open with the business problem in two sentences, not the stack. Then: architecture with a three-swim-lane Mermaid diagram (control plane LangGraph, data plane Elasticsearch, telemetry plane OTel to APM); the ablation table pulled from evals/results/ablation.json; the end-to-end eval table with subsets separate; a cost and latency section with median tokens per run, p95, and what it would cost at paid rates; a quickstart that is genuinely three commands; and an honest limitations section. Pull every number from files in evals/results/ — do not write any figure you cannot trace to an artifact. Also write docs/architecture.md and docs/cost_and_latency.md.

Then, yourself: record three minutes with OBS. Alert in, agent reasoning, approval pause, ticket out, then cut to the APM trace and the dashboard. No slides, no talking head, just the terminal and Kibana.

**Add one failure trace to the README.** Find a run where the agent picked the wrong tool, screenshot the trace, and write two sentences on what the trace told you and what you changed. This proves the observability layer is load-bearing rather than decorative, and it is the most convincing paragraph in the whole document.

---

## Where Claude Code will steer you wrong

**It will declare retrieval "working" on bad results.** Session 6 is yours. Look at the top 3 yourself.

**It will write your eval set to pass.** Ten hand-written tasks, minimum, written before it sees the retriever.

**It will put plausible numbers in the README.** Instruct it explicitly that every figure must come from a file in `evals/results/`. Check three of them against the JSON.

**It will guess at the ELSER and OTel GenAI APIs.** Both have changed recently. Tell it to check current docs in those two sessions specifically.

**It will over-retrieve.** Left alone it passes top-10 full documents into context and your token budget triples. Restate the 5K target when you see context growing.

**It will want to build a web UI.** Say no. A CLI plus a Kibana dashboard is the right surface for this project and the UI would eat four hours you don't have.
