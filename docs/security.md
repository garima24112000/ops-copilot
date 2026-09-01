# Security

## Document-level security (DLS)

`security/dls.py` mints per-user Elasticsearch API keys whose role descriptor restricts
`ops-runbooks` to a single `department`, using Elasticsearch's native query-based DLS
(`{"query": {"term": {"department": "..."}}}` on the role's index privilege) — the same
filtering the cluster itself enforces on every search, not an application-layer filter the
agent code could accidentally bypass.

Five fixed demo users map to the five synthetic departments assigned during corpus ingestion
(`ingest/fetch_corpus.py`):

| user  | department              |
|-------|--------------------------|
| alice | platform-engineering     |
| bob   | database-reliability     |
| carol | networking               |
| dave  | security-compliance      |
| erin  | observability            |

Run the same alert as two different users to see it:

```bash
python cli.py data/sample_alert.json --user alice --approve
python cli.py data/sample_alert.json --user bob --approve
```

`alice`'s run can only ground itself in `platform-engineering` runbooks; `bob`'s can only see
`database-reliability` ones. If the alert's best-matching runbook happens to live in a
department neither user has, the agent's `ground()` node retries once without the service
filter but still can't cross the department boundary — it will ground in whatever it *can* see,
or find nothing, which is the DLS working correctly, not a bug.

**What this is not**: real per-user identity. There is no IdP/SSO integration here — swapping
the fixed `DEMO_USERS` dict in `security/dls.py` for a call to a real identity provider's
department/team claim is the actual production path, and it's a small, isolated change because
the DLS mechanism itself (role descriptor -> API key -> scoped search) doesn't change at all.

API keys, once minted, cannot have their secret read back from Elasticsearch — only their
existence/metadata. `security/dls.py` caches each minted key locally
(`security/.user_api_keys.json`, gitignored) the first time it's needed; there's no way to
"recover" a lost cache entry short of minting a fresh key for that user.

## Data governance: free-tier LLM providers and prompt data

The agent's reasoning calls go through `agent/llm_router.py` to Google AI Studio (Gemini) and
Groq's free tiers, with local Ollama as a no-network fallback. **Free-tier terms for hosted LLM
providers generally permit the provider to retain and, in some cases, train on submitted
prompts and completions** — this is standard for no-card free tiers, in exchange for the
provider not charging for inference. That is a real constraint on what can go into a prompt.

Two design choices in this project exist specifically because of that constraint:

1. **The corpus is entirely public data.** `ops-runbooks` is GitLab's own published SRE
   runbooks and the Prometheus Operator project's published alert runbooks; `ops-logs-*` is the
   standard Loghub academic benchmark; `ops-incidents` is templated from the same public
   runbooks, not customer data. Nothing that flows into a Gemini/Groq prompt in this repo is
   anything other than already-public engineering documentation. In a real deployment with
   proprietary runbooks, this becomes the actual question to answer before wiring in a
   free-tier provider — not an afterthought.
2. **The provider layer is one file, deliberately.** `agent/providers.py` defines the
   `Provider` protocol (`chat(model, messages, tools) -> ChatResult`) that
   `agent/llm_router.py` calls against. Swapping Gemini/Groq for a private, contractually
   no-training endpoint (an enterprise API tier, a self-hosted model behind a VPC, Azure
   OpenAI with a no-retention agreement) is adding one more class implementing that protocol
   and reordering `default_providers()` — no change to `agent/nodes.py`, the graph, or any
   caching/failover logic. That boundary is what makes "swap the provider, not the
   architecture" a true statement rather than a slide.

## Known limitations (see also the README's honest-limitations section)

- Kibana's own login in this local stack uses the `elastic` superuser for `docker-compose`'s
  bootstrap convenience — not the DLS-scoped keys. DLS here governs what the *agent* can
  retrieve on a user's behalf, not who can log into Kibana itself.
- The 5-department, 5-user mapping is a fixed demo set, not a real directory.
- `.env` holds real (free-tier) API keys locally; it is gitignored and was never read directly
  by the agent that built this repo (`os.environ` via `python-dotenv`, per this project's own
  rules) — but it is still a plaintext secret on disk, as any local `.env` is.
