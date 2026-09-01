# Ops Copilot — Zero-Cost Build Plan

**An autonomous IT incident-triage agent that uses Elasticsearch as both its knowledge base and its observability backend. Total spend: $0.**

Target role: Agentic AI Engineer, Elastic (Finance & IT Operations), req 8079636.

---

## 1. The pitch

> When a service alert fires at 2am, an on-call engineer spends 20 minutes doing the same four things: working out what broke, finding the right runbook, checking whether this happened before, and filing a ticket. Ops Copilot does those four things autonomously in 40 seconds, grounded in the company's own runbooks and telemetry, and stops to ask a human before it touches anything destructive.

Two design decisions carry the project:

1. **Elasticsearch is the agent's brain and its memory.** Runbooks, past incidents, and live telemetry all live in Elastic. The agent retrieves from all three, then writes its own postmortem back so the next run retrieves it.
2. **Elasticsearch is also the agent's observability backend.** Every LLM call, tool call, and reasoning step lands in Elastic APM as OpenTelemetry GenAI spans. You debug the agent using the same stack the agent runs on.

The second point is the differentiator. Most portfolio RAG projects stop at "chatbot answers question." Almost nobody instruments the agent and ships a Kibana dashboard of token spend and p95 latency, which is a literal bullet in the job description.

### The loop

Not a Q&A bot. An end-to-end business process:

```
alert fires
   │
   ├─ 1. TRIAGE      classify severity + service       (LLM + ES|QL over past incidents)
   ├─ 2. GROUND      retrieve the right runbook        (hybrid BM25 + ELSER, RRF)
   ├─ 3. DIAGNOSE    confirm or refute the hypothesis  (ES|QL over logs)
   ├─ 4. APPROVE     human-in-the-loop gate            (LangGraph interrupt)
   ├─ 5. ACT         create ticket / restart service   (mock internal API via MCP)
   └─ 6. RECORD      write postmortem back to Elastic  (becomes future context)
```

Step 4 is what makes it enterprise-credible. Steps 1 to 3 and 6 run autonomously. Anything with a side effect stops and waits.

---

## 2. What "zero cost" actually changes

Free LLM tiers are not rate-limited versions of paid tiers with the same shape. The binding constraint moves from **dollars per token** to **tokens per minute**, and that is a different engineering problem. Groq's free tier for a 70B model runs around 12K tokens/minute and 100K tokens/day. If each agent run burns 12K tokens, that is eight runs per day. Useless.

So the zero-cost version of this project is not a worse version. It is a version where you were forced to solve context efficiency, caching, and provider failover, all of which are real production problems and all of which are things you can talk about in an interview. Frame it that way in the README.

Four consequences that shape everything below:

**Context efficiency becomes a first-class goal.** Retrieve top-3 with truncated snippets, not top-10 full runbooks. Target 4-5K tokens per run instead of 12K. Measure it and put the number in the README.

**Split your evals in two.** Retrieval quality (recall@5, nDCG@10) is computed purely from Elasticsearch and needs zero LLM calls. That means your headline ablation table is free, deterministic, and runnable in CI on every commit. Only end-to-end task success needs the LLM, and you run that manually with a small N. This split is good practice regardless of budget, and most people never make it.

**Cache and fixture everything.** Hash the prompt, cache the response to disk. During development you re-run the same five alerts constantly, so caching removes most calls. Then freeze a set of recorded responses as test fixtures so CI makes zero API calls. "How do you get deterministic CI for a non-deterministic system" is a real interview question and you will have already answered it.

**Stack providers behind one interface.** Gemini for reasoning, Groq for fast classification, a local Ollama model for bulk generation, with rate-limit-aware failover between them. This maps directly onto the JD's "hands-on experience with LLM providers" and is more interesting than having one API key.

---

## 3. The stack, all free

| Layer | Choice | Cost | Watch out for |
|---|---|---|---|
| Elasticsearch + Kibana | Self-hosted Docker, single node, `xpack.license.self_generated.type=trial` | $0 | Trial license unlocks ML, ELSER, APM for 30 days, then reverts to Basic |
| Embeddings | ELSER, running on your own cluster | $0 | No per-token charge ever. Needs ~4GB RAM for the ML node |
| Dense baseline | `bge-small-en-v1.5` via sentence-transformers, precomputed | $0 | 130MB model, CPU-fine, no ES ML node needed |
| LLM (reasoning) | Google AI Studio free tier (Gemini Flash) | $0 | No card. Quotas were cut in late 2025, check current limits in AI Studio |
| LLM (fast/short calls) | Groq free tier | $0 | ~30 RPM but TPM is the real cap. Great for classification, bad for long context |
| LLM (bulk generation) | Ollama locally | $0 | Competes with ELSER for RAM. Run them at different times |
| Backup providers | Cerebras, GitHub Models, OpenRouter free slots | $0 | OpenRouter's higher daily cap needs a $10 top-up, so stay on the 50/day tier |
| Observability | APM Server in Docker with OTLP intake, or OTel Collector with the Elasticsearch exporter | $0 | Collector route works under Basic if your trial license lapses |
| IaC | Terraform with the `elastic/elasticstack` provider against your local cluster, plus the Docker provider | $0 | Better fit than the cloud provider anyway, see note below |
| Orchestration | LangGraph | $0 | Open source |
| MCP | FastMCP | $0 | Open source |
| CI | GitHub Actions | $0 | Free for public repos |
| Kubernetes (optional) | kind or k3d | $0 | Stretch goal only |
| Diagram | Excalidraw or Mermaid in-repo | $0 | Commit the source, not just a PNG |
| Demo video | OBS Studio | $0 | Loom's free tier caps length |

**On Terraform.** Using the `elastic/elasticstack` provider to manage index templates, ingest pipelines, roles, and API keys as code against your local cluster is free, and it is a *better* story than provisioning a cloud deployment. It is the part of Elastic IaC an internal IT team actually lives in day to day. Add the Docker provider to bring up the containers themselves and you have a real, applyable, zero-cost Terraform story.

**One deliberate exception.** Agent Builder needs Elastic 9.3+ and may require a licence tier that your local trial licence covers. Check whether it appears in your local Kibana. If it doesn't, spend two hours of the free 14-day Elastic Cloud trial (no credit card) purely to build the same flow declaratively and screenshot it. Do not enter a card at any point, since adding one converts the trial to paid immediately.

---

## 4. Data

The trap: you need three things that link together, an alert, the runbook that resolves it, and similar past incidents. Public datasets give you each piece separately but never the connections, and without connections you cannot build an eval set.

Use real data for the corpus and generate only the links.

### Real, free, worth using

- **Loghub** (`github.com/logpai/loghub`) is the standard academic collection of real system logs: HDFS, Hadoop, Zookeeper, OpenStack, Apache, Linux. This becomes `ops-logs-*`. It is real production noise, not synthetic tidiness, and it is widely cited so referencing it signals you know the log-analysis literature.
- **GitLab's production runbooks** (`gitlab.com/gitlab-com/runbooks`) are genuinely public. Real runbooks by real SREs for a real service, far better than anything an LLM invents.
- **Prometheus Operator runbooks** (`runbooks.prometheus-operator.dev`) give you alert-to-remediation pairs already structured the way you need.
- **`danluu/post-mortems`** is a curated collection of published real postmortems. Good source for the incidents index.
- Kibana ships **built-in sample data sets**. Use one for a five-minute smoke test before touching anything real.

### Reverse-generate the links

Take a real runbook, ask a model to write the alert payload that would trigger it. You now have ground truth for free: you know which runbook is correct for each alert, so recall@5 and nDCG@10 compute automatically across 200 tasks instead of the 30 you would hand-label.

Do this generation with **Ollama locally**, not your free cloud quota. Writing a short alert from a runbook summary is an easy task that a small local model handles fine, and it costs you nothing but wall-clock time. Generate once, commit the output as JSONL, never regenerate.

### The bias problem, and why naming it helps you

LLM-generated queries lexically echo their source document, which inflates retrieval scores, and BM25 benefits most from this. Three mitigations:

1. Generate the alert from a *summary* of the runbook, not the full text.
2. Instruct the model to use operator vocabulary rather than runbook vocabulary.
3. Hand-write 10 of your 30 end-to-end golden tasks yourself and report those numbers separately from the generated ones.

Write this up in the README. Recognising that your own eval set is biased, quantifying the bias, and reporting both numbers is the single clearest "master's student, not tutorial follower" signal in the whole project.

### Indices

| Index | Contents | Notes |
|---|---|---|
| `ops-runbooks` | ~120 real runbooks | `body` (text) + `body_semantic` (semantic_text) + `body_dense` (dense_vector) + `department` |
| `ops-incidents` | ~300 past incidents | semantic + keyword fields |
| `ops-logs-*` | Loghub subset, ~50k lines | data stream, ES\|QL queryable |
| `ops-postmortems` | written by the agent | closes the memory loop |
| `ops-agent-evals` | eval scores per commit | `run_id`, `metric`, `score`, `git_sha`, `@timestamp` |

Note the three parallel body fields. That is deliberate, and it is your ablation.

---

## 5. The free ablation (do not skip this)

Because it needs no LLM, this is the cheapest and highest-value experiment in the project:

| Strategy | recall@5 | nDCG@10 | Latency p95 |
|---|---|---|---|
| BM25 only | | | |
| Dense only (bge-small) | | | |
| ELSER only (sparse) | | | |
| Hybrid, RRF | | | |

Four rows, three columns, one paragraph of interpretation. Costs an hour, reads like research, and demonstrates you understand what ESRE actually is rather than just naming it.

If your trial licence has lapsed and the `rrf` retriever is unavailable under Basic, implement reciprocal rank fusion client-side. It is about fifteen lines and arguably shows more understanding than calling the built-in.

---

## 6. Token budget

Design to this, and measure against it:

| Phase | Runs | Tokens/run | Total | Where |
|---|---|---|---|---|
| Corpus generation | ~250 short calls | ~800 | ~200K | Ollama, local, free |
| Development loop | ~200 | ~5K | ~1M | Cached after first hit, so effectively ~300K |
| Retrieval ablation | 200 tasks × 4 configs | **0** | **0** | Pure Elasticsearch |
| End-to-end evals | 30 tasks × 3 repeats | ~5K | ~450K | Spread across providers over 2 days |
| CI | every commit | **0** | **0** | Fixtures |
| Demo recording | ~10 | ~5K | ~50K | Best available free model |

Roughly 800K to 1M billable-equivalent tokens spread across two days and three providers. Comfortably inside stacked free tiers, but only because retrieval evals and CI cost nothing. That is the whole trick.

---

## 7. Agenda

Ordered by dependency, with a gate at each step. Do not move on until the gate passes.

### Day 0, the evening before (~1 hour)

- `docker compose up` with Elasticsearch, Kibana, APM Server. Set the trial licence.
- Pull ELSER, deploy it, confirm the ML node starts.
- Get free API keys: Google AI Studio, Groq. Install Ollama and pull one small instruct model.
- `git init`, push a public repo with a README stub.
- **Gate:** one document indexed with `semantic_text` and retrieved semantically.

If ELSER will not start, stop and fix it now rather than at hour six of Day 1.

### Day 1 morning (3-4h), data and retrieval

- Fetch and parse GitLab runbooks and Prometheus Operator runbooks into a clean JSONL corpus.
- Load a Loghub subset into a data stream.
- Reverse-generate ~200 alert/runbook pairs with Ollama. Commit the output.
- Index with all three body representations.
- **Gate:** a hybrid RRF query in Dev Tools returns the correct runbook for three alerts you type by hand.

Sanity-check retrieval here, before any agent code exists. Bad retrieval looks identical to a bad agent, and people lose entire days debugging the wrong layer.

### Day 1 afternoon (4-5h), the agent

- MCP server with five tools: `search_runbooks`, `find_similar_incidents`, `query_service_health`, `create_ticket`, `restart_service`. The last two require approval.
- LLM router: provider abstraction with disk cache and rate-limit failover across Gemini, Groq, and Ollama.
- LangGraph graph: triage → ground → diagnose → approve → act → record, with `interrupt()` before `act`.
- CLI runner that takes an alert JSON and prints the trace.
- **Gate:** one alert flows end to end and pauses for approval.

Write the MCP server as a standalone server, not as LangChain tools. It is about forty extra lines and it means you can demo the same tools working in a second MCP client with zero code changes, which is a persuasive thirty seconds of video.

**If you reach this gate by end of Day 1, the project is already submittable.** Everything after is upside.

### Day 2 morning (3-4h), observability and security

- Instrument with OTel GenAI semantic conventions: `invoke_agent` root span, `chat` spans carrying `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.request.model`, and `execute_tool` spans per tool call. Export OTLP to APM Server.
- Kibana dashboard: tokens per run, latency p95 by node, tool-call frequency, failure rate, provider mix. Export as saved objects JSON into `dashboards/`.
- One alert rule that fires when a single run exceeds a token budget.
- Document-level security: mint a per-user API key with a role descriptor restricting `ops-runbooks` by `department`. Demo the same question from two users returning different sources.
- **Gate:** a trace waterfall visible in APM with token counts on the `chat` spans.

The DLS demo is roughly thirty lines and disproportionately impressive. It is the difference between "I built a RAG app" and "I thought about multi-tenant enterprise retrieval."

### Day 2 afternoon (4-5h), rigour and packaging

- Retrieval eval harness. Run the four-row ablation. Write results to `ops-agent-evals`.
- End-to-end eval on 30 tasks, reporting generated and hand-written subsets separately.
- GitHub Actions: ruff, mypy, pytest, and the retrieval eval against an ES service container using precomputed vectors from fixtures. No ML node, no API calls.
- Terraform: index templates, ingest pipelines, roles, API keys as code.
- Agent Builder mirror if available, with 300 words on what was faster and what control you lost.
- README, architecture diagram, three-minute demo video.

---

## 8. Cut list

You will not finish all of this in two days. Cut in this order:

1. Kubernetes (do not even start)
2. Agent Builder mirror
3. Terraform
4. Provider failover, drop to a single provider plus cache

**Never cut:** the working end-to-end demo, the retrieval ablation table, the README, the three-minute video.

If you are behind at noon on Day 2, go straight to observability dashboard → ablation table → README → video. Those four carry ninety percent of the interview value.

---

## 9. Repo structure

```
ops-copilot/
├── README.md                  ← the deliverable that actually gets read
├── docker-compose.yml         ← reviewer runs this and it just works
├── Makefile                   ← make setup / make demo / make evals
├── .env.example               ← never commit real keys
├── agent/
│   ├── graph.py               LangGraph state machine
│   ├── nodes.py
│   ├── state.py
│   ├── llm_router.py          provider failover + disk cache
│   └── telemetry.py           OTel setup
├── mcp_server/
│   ├── server.py              FastMCP
│   └── tools/
├── ingest/
│   ├── mappings/*.json
│   ├── fetch_corpus.py        GitLab + Prometheus runbooks, Loghub
│   └── load.py
├── scripts/generate_alerts.py Ollama reverse-generation
├── data/                      committed JSONL, reproducible
├── evals/
│   ├── retrieval/             zero-LLM, runs in CI
│   ├── end_to_end/            golden_set.yaml, 30 tasks
│   └── results/
├── fixtures/                  recorded LLM responses for CI
├── dashboards/                exported Kibana saved objects
├── infra/                     Terraform
├── .github/workflows/ci.yml
└── docs/
    ├── architecture.md
    ├── security.md            DLS design + free-tier data governance
    ├── cost_and_latency.md
    └── agent_builder_comparison.md
```

---

## 10. Why zero cost makes this project better

Say this out loud in the README, because it is true and most people miss it.

A project built on a 14-day cloud trial is dead by the time a hiring manager opens it. Yours is not. A reviewer clones the repo, runs `docker compose up`, runs `make demo`, and watches it work on their own machine. Full reproducibility, no signup, no credit card, no expired cluster. That is a genuine competitive advantage that happens to fall out of the constraint.

Add a short **data governance** note to `docs/security.md`: free LLM tiers generally train on submitted prompts, which is why the corpus is public runbooks and synthetic incidents, and why the architecture routes through a provider abstraction that could be swapped for a private endpoint in one file. That paragraph hits the JD's compliance and ethical-AI bullets and takes ten minutes to write.

---

## 11. Details that raise the grade cheaply

- **A cost model.** "Median task: 4.8K tokens, p95 latency 6.1s, $0.00 at current free tiers, $0.014 at Gemini Flash paid rates." Nobody does this and the JD explicitly asks for cost-optimised solutions.
- **One failure trace in the README.** Screenshot a run where the agent picked the wrong tool. Explain what the trace told you and what you changed. This proves the observability layer is load-bearing rather than decorative.
- **An honest limitations section.** Public corpus rather than proprietary, no real ITSM integration, single-tenant, self-authored eval set, free-tier models weaker at tool selection than frontier models. Naming your own weaknesses reads as seniority.
- **A three-swim-lane diagram:** control plane (LangGraph), data plane (Elasticsearch), telemetry plane (OTel → APM). Makes the "brain and memory" framing legible in five seconds.

---

## 12. Practical gotchas

**RAM is your real budget, not money.** Elasticsearch with a 4GB heap, plus an ML node for ELSER, plus Kibana, plus APM Server runs about 8-10GB. Ollama wants another 4-8GB. Do not run them simultaneously. Sequence it: generate the corpus with Ollama first, stop Ollama, then bring up the full Elastic stack and use hosted free tiers for the agent. On a machine with 8GB total, skip ELSER entirely and run the dense-only path with precomputed `bge-small` vectors.

**Free-tier rate limits rot fast.** Published numbers were revised repeatedly through 2026. Check the provider console for your actual project limits rather than trusting any blog post, including this one. Build the router to read rate-limit headers and back off rather than hardcoding numbers.

**TPM bites before RPD.** A provider advertising 1,000 requests/day but 12K tokens/minute will throttle you at about two long calls per minute regardless of the daily figure. This is exactly why context efficiency is worth engineering.

**The local trial licence is 30 days.** Start it when you start building, not while exploring. Everything important should be exported into the repo before it lapses.

**Do not add a credit card to Elastic Cloud** at any point during the 14-day trial. Doing so converts it to a paid subscription immediately.

---

## 13. On the application itself

- Lead the README with the business problem, not the stack. The team is internal IT and they buy outcomes.
- The **three-minute demo video** is the highest-ROI artifact in the whole project. Most applicants send a repo link nobody clones.
- In your cover note, name the specific things you used: ESRE hybrid retrieval, `semantic_text`, ES|QL, MCP, OTel GenAI conventions, document-level security, Agent Builder. Recruiters keyword-match; hiring managers recognise that you read the platform docs.
- Bring one opinion to the interview. "RRF beat pure ELSER on short alert strings because service names are exact-match tokens and the sparse model under-weighted them" is worth more than any amount of polish.
