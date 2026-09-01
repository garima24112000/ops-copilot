# Ops Copilot

![CI](https://github.com/garima24112000/ops-copilot/actions/workflows/ci.yml/badge.svg)

When a service alert fires at 2am, an on-call engineer spends 20 minutes doing the same four
things: working out what broke, finding the right runbook, checking whether this happened
before, and filing a ticket. Ops Copilot does those four things autonomously, grounded in
real runbooks and live telemetry, and stops to ask a human before it touches anything
destructive. Total spend: **$0** — every provider and every piece of infrastructure runs on a
genuinely free tier or self-hosted locally, no credit card anywhere.

Elasticsearch is the agent's brain and its memory (runbooks, past incidents, and live
telemetry all live there, and the agent writes its own postmortems back so the next run
retrieves them), and it's also the agent's observability backend (every LLM call and tool call
lands in Elastic APM as OpenTelemetry GenAI spans, queried with the same stack the agent
itself queries).

**3-minute walkthrough:** _(link to be added)_

## Architecture

```mermaid
flowchart TB
    subgraph control["Control plane — LangGraph (agent/)"]
        direction LR
        triage[triage] --> ground[ground] --> diagnose[diagnose] --> approve["approve\n(interrupt)"] --> act[act] --> record[record]
    end

    subgraph data["Data plane — Elasticsearch"]
        direction LR
        runbooks[(ops-runbooks\nBM25 + ELSER + dense)]
        incidents[(ops-incidents)]
        logs[(ops-logs-*\nLoghub, ES|QL)]
        postmortems[(ops-postmortems)]
    end

    subgraph telemetry["Telemetry plane — OTel -> APM Server -> Kibana"]
        direction LR
        invoke_span["invoke_agent span"] --> chat_span["chat spans\n(tokens, provider, model)"]
        invoke_span --> tool_span["execute_tool spans"]
    end

    ground -- "search_runbooks (MCP)" --> runbooks
    diagnose -- "find_similar_incidents (MCP)" --> incidents
    diagnose -- "query_service_health (MCP, ES|QL)" --> logs
    act -- "create_ticket / restart_service\n(after approval only)" --> mock[["mock ITSM API"]]
    record -- writes postmortem --> postmortems
    control -. every LLM + tool call .-> telemetry
```

Full write-up: [`docs/architecture.md`](docs/architecture.md). Security design and data
governance: [`docs/security.md`](docs/security.md).

## Retrieval ablation (zero LLM calls)

The single highest-value-per-minute artifact in this project: four retrieval strategies,
computed purely from Elasticsearch (no LLM in this path, per the project's own rule), over
147 real generated alert/runbook pairs. Source: [`evals/results/ablation.json`](evals/results/ablation.json).

| strategy    | recall@5 | nDCG@10 | p95 latency |
|-------------|----------|---------|-------------|
| BM25 only   | 0.837    | 0.744   | 4.2 ms      |
| Dense only  | 0.748    | 0.656   | 5.9 ms      |
| ELSER only  | 0.884    | 0.785   | 663.0 ms    |
| Hybrid RRF  | 0.864    | 0.791   | 19.7 ms     |

**Interpretation:** ELSER alone has the best recall@5, but hybrid RRF has the best nDCG@10
(0.791, edging out ELSER's 0.785) at **34x lower p95 latency** (19.7ms vs 663ms) — RRF only
needs one of its two sub-retrievers to rank the right document highly for the fused rank to
benefit, so it captures most of ELSER's semantic-matching advantage while paying BM25's
latency for the query that usually resolves it. Dense-only (bge-small, no domain adaptation)
trails both sparse approaches on every metric. For a real on-call tool, hybrid RRF is the
right default: ELSER-only's 663ms p95 would be a genuinely bad experience at alert-triage
time.

## End-to-end evals

Generated and human-curated subsets are reported **separately, never merged** — they carry
different provenance and different bias profiles. Source: [`evals/results/e2e_eval.json`](evals/results/e2e_eval.json).

| subset    | n  | task success | tool-selection accuracy | mean tokens/run | p95 latency |
|-----------|----|--------------|--------------------------|------------------|-------------|
| generated | 20 | 0.650        | 1.000                    | 695              | 77.17 s     |
| human     | 10 | 0.300        | 1.000                    | 787              | 48.08 s     |

**Interpretation:** tool-selection accuracy is 1.000 on both subsets — the agent always calls
the right MCP tool sequence for the alert it's given; every failure here is a *retrieval*
failure (wrong runbook id), not a tool-use failure. Two patterns explain most of the gap:

1. **Near-misses within the right service, not random misses.** Several "failures" retrieve a
   more specific doc in the *same* service directory as the expected one — e.g. `gen-16`
   expected `gitlab-blackbox-README` but got `gitlab-blackbox-BlackboxProbeFailures`; `gen-02`
   expected `gitlab-ci-orchestration-README` but got a specific SLO-violation runbook in that
   same `ci-orchestration` directory. Task success here is exact-id match, a strict metric —
   these are the retriever correctly narrowing to the right service and picking a plausibly
   *more* targeted document than the generic README, which the metric still scores as failure.
2. **The human subset's two "INTENDED HARD" tasks failed as their own notes predicted.**
   `human-07` (TargetDown) and `human-08` (ApdexSLOViolation) were deliberately curated as hard
   cases in `golden_set_human.yaml`'s own `notes` field, and both failed. A well-designed
   golden set testing its hard cases and having them fail as anticipated is a sign the set is
   doing its job, not evidence the agent is unusually broken on human-curated alerts — though
   the smaller n=10 means each failure moves the human success rate by 10 points, so this
   number is noisier than the generated subset's.

**A note on the "human-curated" subset's provenance**, stated precisely because it matters:
the 10 tasks in `evals/end_to_end/golden_set_human.yaml` were **not** hand-authored from
scratch. The project's operator curated which runbooks to target and edited the alert text,
but the initial drafts were written by an assistant that had already read three of the
target runbook bodies. This subset is therefore **human-curated, model-drafted,
human-edited** — not human-authored — and residual lexical-overlap bias (the same bias the
20-task *generated* subset carries by construction) cannot be ruled out for those three
specific tasks. Reporting it as "human-authored" would overstate its independence from the
retrieval system being evaluated; this project would rather understate a number than
overstate one.

## Quickstart

```bash
make up       # docker compose up: Elasticsearch, Kibana, APM Server
make ingest   # load the corpus (runbooks, incidents, logs) into Elasticsearch
make demo     # run the agent against a sample alert, pausing for approval
```

Three commands, no signup, no credit card, works on a clean clone. `make deploy-elser` runs
once first if you're starting from a truly fresh Elasticsearch cluster (see
`scripts/deploy_elser.py`).

![CI passing on GitHub Actions](docs/images/ci-green.png)

## Cost and latency

Full write-up: [`docs/cost_and_latency.md`](docs/cost_and_latency.md). Every LLM call goes
through a disk-backed, rate-limit-aware router (`agent/llm_router.py`) across Google AI
Studio (Gemini), Groq, and local Ollama — free-tier providers are rate-limited on
tokens-per-minute, not dollars, so caching and provider failover are load-bearing, not
optional polish. Live-verified: a request that hit a transient Gemini `503` failed over to
Groq and completed correctly, with both the cache-hit and failover behavior covered by unit
tests (`tests/test_llm_router.py`) and observed live in a real trace (see below).

## Observability

Every LLM call and MCP tool call is wrapped in an OpenTelemetry GenAI semantic-convention span
(`agent/telemetry.py`) exported to APM Server: one `invoke_agent` root span per run, `chat`
child spans carrying `gen_ai.usage.input_tokens`/`output_tokens`/`gen_ai.provider.name`, and
`execute_tool` child spans per MCP call. A Kibana dashboard (`dashboards/ops_copilot_dashboard.ndjson`,
built via the saved-objects API — session 12 in `PROGRESS.md`) plots tokens per run, p95
latency by span, tool-call frequency, provider mix, and run success rate, all sourced from
these real spans.

![Kibana dashboard: tokens per run, p95 latency, tool-call frequency, provider mix, run success rate](docs/images/kibana-dashboard.png)

![APM trace showing a chat span and its token/latency attributes](docs/images/apm-trace-tokens.png)

The 460µs `chat` span above is a disk-cache hit from the router (`agent/llm_router.py`), not a
live provider call — the router's cache-hit path is intentionally near-instant, and reading it
as real LLM latency would badly understate the true cost of a cold call. The 47s root span is
dominated by time spent outside its instrumented children, which is a known gap in this
project's current instrumentation (not every intermediate step between child spans has its own
span yet), not evidence of real LLM latency of that magnitude. And the `gen_ai.usage.*`
attributes appear under `numeric_labels.*`/`labels.*` rather than as first-class APM fields
because APM Server has no built-in mapping for the still-maturing OTel GenAI semantic
conventions — it falls back to its generic labels mechanism for any attribute it doesn't
recognize, which is also why the dashboard's aggregations above query `numeric_labels.gen_ai_*`
directly instead of a dedicated field.

### A real failure trace, and what it taught me

Querying the live trace data directly (rather than eyeballing Kibana) surfaced something the
observability layer was built to catch: a `chat` span for a call that failed over from Gemini
to Groq (session 8's router failover) still carries the span *name* `chat gemini-flash-latest`
— because the span name is set when the call starts, before the provider is known, and OTel
span names aren't meant to be renamed mid-span once a later provider actually serves it. The
span's *attributes* are correct either way (`gen_ai.provider.name: groq`,
`gen_ai.response.model: openai/gpt-oss-20b`), so nothing is silently wrong — but reading only
the span *name* in a dashboard list would tell a misleading story about which provider served
a given call. This is exactly the kind of thing a token/latency dashboard without a way to
drill into individual span attributes would hide, and exactly why this project instruments
`gen_ai.provider.name` as a separate attribute rather than trusting the span name alone.

## Security: per-user document-level security

Retrieval is scoped per user by real Elasticsearch document-level security (DLS), not an
application-layer filter bolted on afterward. Each demo user's API key
(`security/dls.py`, minted via `mint_user_api_key()`) carries a role descriptor that restricts
`ops-runbooks` to their department with a query the *cluster* enforces
(`{"query": {"term": {"department": ...}}}`) — the agent process never sees a runbook outside a
user's department in the first place, rather than seeing it and choosing not to show it.

![Two users, bob and carol, running the same alert through the CLI and grounding in different runbooks](docs/images/dls-two-users.png)

The live result above: the same etcd alert, run through the identical CLI invocation, grounds
in a different runbook depending on who runs it. `bob` (department `database-reliability`)
grounds in `promop-etcdInsufficientMembers`; `carol` (department `networking`) is scoped away
from that runbook entirely and grounds in `promop-etcdMembersDown` instead — a different real
runbook, not an empty result. Because the two runs ground in different source documents, the
two agents produce genuinely different hypotheses about the same alert, not just a redacted
version of the same one. The two runs also took visibly different wall-clock time — 6.13s for
bob's run versus 0.49s for carol's — caused by the router's disk cache (`agent/llm_router.py`)
serving carol's run from a cache entry bob's identical LLM calls had already populated; the
timing difference is a caching artifact of running them back-to-back, not a DLS cost.

## Engineering notes: what actually went wrong, and how it was found

This project's discipline throughout was: investigate suspicious numbers instead of reporting
them, and never weaken a gate to make it pass. Five real incidents, in the order they were hit:

**1. ELSER bulk-indexing throughput collapsed under the default ML memory sizing.**
`semantic_text` fields run ELSER inference synchronously as part of the write path. Bulk
indexing the corpus through Elasticsearch's *preconfigured* ELSER endpoint queued 250+ pending
inference requests and was still nowhere near done after 15+ minutes on a single allocation.
Diagnosed by checking `GET _ml/info` directly rather than guessing: only 3.4GB was available
for ML, most already consumed by that one allocation — Elasticsearch sizes its ML memory
budget as a fixed percentage of node memory by default, and that default left no headroom for
a second allocation even though the container had plenty of spare RAM. Fixed by setting
`xpack.ml.use_auto_machine_memory_percent=true` (7.65GB available afterward, up from 3.4GB)
and deploying a dedicated 6-allocation ELSER endpoint instead of depending on adaptive scaling
for a one-shot batch job. The full corpus (120 runbooks + 147 incidents + 32,000 log lines)
then indexed in 1108s.

**2. The retrieval gate failed its first real run — 40%, against a 60% threshold — and the
fix required two separate root causes, not one.** Per this project's own rule, that gate is
load-bearing: a failure there means stop and diagnose, not proceed or lower the threshold.
Splitting the 20 holdout failures apart: 3 traced to an id-collision bug in corpus generation
(`gitlab-com/runbooks` has one `README.md` per service directory; an id scheme keyed only on
filename collapsed 21 distinct documents into one, silently overwriting the correct content
for several eval queries). Fixing that alone only moved the number to 47% — the *dominant*
cause was the alert-generation prompt's instruction to avoid the source runbook's own wording,
which over-corrected a small (1B-parameter) local model into producing content-free generic
phrases like `"error_counts < 10"` that carry no retrievable signal. The fix that worked was
splitting one hard LLM task into two easy ones: ask the model only to extract a specific,
paraphrased symptom, then build the operator-flavored alert message deterministically in
Python from a template — guaranteeing every message contains real content instead of risking
a generic one. Re-run: **90% (18/20)**.

**3. `query_service_health`'s ES|QL query was string-interpolating untrusted input.** The
`service` value from an alert payload was concatenated directly into an ES|QL query string.
Fixed to use ES|QL's parameterized query support (`WHERE service == ?`, `params=[service]`)
instead — the kind of fix that's easy to miss because the vulnerable version works fine on
every well-behaved input.

**4. The `apm-server` Docker healthcheck never actually worked.** The original healthcheck
piped `curl` through `grep` — but the `apm-server` image ships neither binary. An initial fix
attempt used a bash loop variable named `$l` directly inside the Docker Compose YAML, which
got silently consumed by Compose's own `${...}` interpolation before it ever reached the
container (Compose doesn't know or care that `$l` was meant for the container's shell). Fixed
with a bash-builtins-only check (`/dev/tcp` for the raw socket, `[[ == ]]` for the string
match, `$$l` to escape the variable past Compose's interpolation layer). Confirmed live:
`docker ps` now reports `ops-copilot-apm ... (healthy)`.

**5. The Kibana dashboard was marked done on API 200s that hid five real rendering bugs.**
`scripts/build_dashboard.py` builds the dashboard and its 5 panels entirely through the
saved-objects API. The first pass was verified by re-running each panel's aggregation directly
against Elasticsearch and confirming sane numbers — a real check, but the wrong one: it proves
the aggregation logic is sane, not that Kibana's own runtime can load and render the saved
objects. Opening it in a browser found the gap immediately: every panel failed. Fixing it
surfaced five distinct bugs, three visible from the first error message and two more that only
appeared once the earlier ones were cleared: (1) the script never created its data view, it
assumed a hardcoded id that happened to exist locally; (2) every panel's `searchSourceJSON` was
missing `indexRefName`, so Kibana couldn't resolve which saved-object reference was the index
pattern (`indexPatternLoad requires the "id" argument`); (3) the success-rate panel used
`filter_ratio`, a TSVB-only aggregation never registered for classic-aggs visualizations —
replaced with a plain `avg` over a runtime field that's 1 for a successful run and 0 otherwise;
(4) every panel's `embeddableConfig` key was missing, which crashed the *entire dashboard*
(not just one panel) in Kibana's server-side read transform, root-caused by reading the
transform source directly out of the Kibana image rather than guessing; (5) three panels
wildcarded a *quoted* KQL string (`'span.name: "chat*"'`), which KQL parses as a literal phrase
containing an asterisk rather than a wildcard — those panels silently rendered "No results
found" against data that was actually there, and a terms bucket ordered by a percentiles metric
using a bare aggregation id, which Kibana rejects for a multi-value metric. Verified this time
by the method that should have been used the first time: delete everything, rebuild from a
clean stack, open it in a real browser with the time picker pinned to the eval data's actual
window, confirm all 5 panels render real data — then delete again and confirm the exported
`dashboards/ops_copilot_dashboard.ndjson` reproduces the same working dashboard via the actual
import path, not just the live API objects. The lesson: an API 200 proves a write succeeded,
never that a UI-rendering system can parse, resolve, or query with what was written.

## Honest limitations

- **The Prometheus Operator half of the corpus is alphabetically truncated at 40 documents,
  not sampled.** `ingest/fetch_corpus.py` sorts the available runbook paths and takes the
  first 40; it does not randomly or representatively sample the full set. This biases the
  corpus toward alert names that sort early in the alphabet and is a real gap, not a stylistic
  choice.
- **`query_service_health`'s confirm path is untested against this corpus, structurally.**
  The Loghub log systems (HDFS, Zookeeper, OpenStack, Apache, Linux, and others) and the
  runbook systems (etcd, Kubernetes, GitLab's own services) are **disjoint sets** — no runbook
  in this corpus has matching telemetry in `ops-logs-*`. Every live diagnose step this project
  ran ended up with `diagnosis_confirmed: false` for exactly this reason (zero matching log
  lines, not a wrong diagnosis) — the code path that would set `diagnosis_confirmed: true` off
  of real telemetry has not been exercised by this corpus at all.
- **One malformed id survives the session-6 collision fix**: `gitlab-README.md-README`, from
  the single top-level `docs/README.md` file that has no service subdirectory to derive a
  `service` component from. Harmless (it's the generic project-level README, not an
  actionable runbook, and nothing depends on its id format) but not clean.
- **The generated eval subset carries known lexical-overlap bias.** LLM-generated queries
  lexically echo their source document, which inflates retrieval scores, and BM25 benefits
  most from this. Mitigated by generating from a 2-sentence summary rather than the full
  runbook text and instructing the model toward operator vocabulary rather than the runbook's
  own wording — but not eliminated, which is exactly why the human-curated subset (itself
  imperfect — see the provenance note above) is reported as a separate row rather than folded
  into one number.
- **Public corpus, not proprietary.** Runbooks are GitLab's and the Prometheus Operator
  project's own published documentation; incidents are templated from the same public
  runbooks, not real ticket data.
- **No real ITSM integration.** `create_ticket`/`restart_service` hit a mock internal API
  (`mcp_server/mock_api.py`), not a real ticketing system.
- **Single-tenant.** Document-level security (`security/dls.py`) restricts retrieval by a
  synthetic `department` field for 5 fixed demo users; there is no real IdP/SSO integration.
- **Document-level security is enforced on `ops-runbooks` only.** `ops-incidents` is granted
  unrestricted read for every user's API key, so `find_similar_incidents` can surface incident
  summaries derived from runbooks outside a user's own department — directly visible in the
  two-user demo above, where `carol` (scoped away from `promop-etcdInsufficientMembers`) still
  receives `incident-promop-etcdInsufficientMembers-1` from `find_similar_incidents`. This is a
  real multi-tenant isolation gap, not a hypothetical one: a complete implementation would apply
  the same department-based DLS query to `ops-incidents` and `ops-postmortems` that
  `ops-runbooks` already has.
- **Free-tier models are weaker at tool selection and instruction-following than frontier
  models.** The alert-generation prompt redesign (engineering note #2) exists specifically
  because a 1B-parameter local model could not reliably juggle multiple simultaneous
  instructions — a real, measured constraint of the zero-cost design, not a hypothetical one.
- **Terraform is written and `plan`-clean but not `apply`d.** `infra/` manages index
  templates, a Terraform-owned ELSER endpoint, and DLS roles/keys as code; `terraform plan`
  runs clean against the live local cluster, but `apply` was deliberately skipped to avoid
  resource contention with the ML-memory-constrained corpus ingest running at the same time.

## Full session log

Every session's gate, its real numbers, and every bug found along the way (with the actual
diagnosis, not just the fix) is in [`PROGRESS.md`](PROGRESS.md) — including a `Context-loss
note` documenting a gap where this file went stale relative to the actual git history, and how
it was reconciled. `docs/agent_builder_comparison.md` documents a genuine finding: the build
plan assumed Elastic's Agent Builder would need a separate Cloud trial, which turned out to be
wrong — it's available locally under the same trial license this project already runs.

## Demo

Shot-by-shot script for a 3-minute screen recording: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

**3-minute walkthrough:** _(link to be added)_
