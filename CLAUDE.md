# CLAUDE.md

Durable project rules for Ops Copilot. These are hard constraints, not preferences — do not propose or implement anything that violates them. See `ops-copilot-zero-cost-plan.md` for the full build plan and rationale.

## Zero cost, no exceptions

- **No paid APIs, no cloud deployments, no credit card anywhere.** Every provider, tool, and service used must have a genuinely free tier that requires no payment method on file. If a feature requires adding a card (e.g. Elastic Cloud trial beyond the no-card window, OpenRouter's paid daily-cap top-up), do not add it — cut the feature or find a free path instead.
- All infrastructure runs self-hosted locally (Docker Compose: Elasticsearch, Kibana, APM Server). No managed cloud services.

## Token budget

- Target **4-5K tokens per agent run**, end to end. This is a design constraint, not an afterthought: retrieve top-3 truncated snippets, not top-10 full documents. Measure actual token usage per run and treat regressions above this budget as bugs.

## LLM calls go through the cached router

- Free-tier LLM providers (Gemini, Groq, Ollama, backups) are rate-limited on **tokens-per-minute**, not just requests-per-day. TPM is the binding constraint.
- **Every LLM call must go through the router** (`agent/llm_router.py`): disk-backed cache keyed on the prompt hash, plus rate-limit-aware failover across providers. Never call a provider SDK directly from agent/eval code.
- Cache aggressively during development. Freeze recorded responses as fixtures so CI makes zero live API calls.

## Retrieval evals never call the LLM

- Retrieval quality (recall@5, nDCG@10) is computed purely from Elasticsearch queries. **Retrieval eval code must never make an LLM call** — no embeddings-on-the-fly via a hosted API, no LLM-as-judge in this path. This is what keeps the ablation table free, deterministic, and safe to run in CI on every commit.
- Only end-to-end task success evals may call an LLM, and those are run manually / deliberately, not in CI.

## RAM is the binding local constraint

- **Ollama and the Elastic ML node (ELSER) must never run simultaneously.** Elasticsearch + ML node + Kibana + APM Server already uses ~8-10GB; Ollama wants another 4-8GB on top.
- Sequence work instead of running both: generate/bulk-process with Ollama first, stop it, then bring up the full Elastic stack.
- On low-RAM machines, prefer the dense-only path with precomputed `bge-small` vectors over ELSER rather than trying to run both ML workloads at once.

## Toolchain

- **Python 3.11+**, managed via `pyproject.toml`.
- Lint/format: **ruff**. Type check: **mypy**. Test: **pytest**. All three must pass before considering work done; `make lint` runs ruff and mypy.

## Secrets

- **Never commit `.env` or any file containing API keys.** `.env` is gitignored — keep it that way. Use `.env.example` to document required variables with placeholder values only.
- Never paste real API keys, tokens, or credentials into code, comments, commit messages, or fixtures.

## Do not implement ahead of plan

- Follow the agenda and cut list in `ops-copilot-zero-cost-plan.md`. Do not build features from later phases before their gate is reached, and do not skip the retrieval sanity-check gate before writing agent code.
