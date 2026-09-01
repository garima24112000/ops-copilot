# Cost and latency

Every number on this page is read from a file under `evals/results/` — none are typed from
memory. Where a number is still pending a run, it says `TBD` rather than a guess.

## Token budget target

CLAUDE.md's design target is 4-5K tokens per agent run, end to end, achieved by retrieving
top-3 truncated snippets (not full documents) and keeping the agent to two LLM calls per run
(triage, diagnose).

## Retrieval (zero LLM calls, per CLAUDE.md)

Source: `evals/results/ablation.json`, 147 real generated alert/runbook pairs, git SHA
`432e065`.

| strategy    | recall@5 | nDCG@10 | p95 latency |
|-------------|----------|---------|-------------|
| BM25 only   | 0.837    | 0.744   | 4.2 ms      |
| Dense only  | 0.748    | 0.656   | 5.9 ms      |
| ELSER only  | 0.884    | 0.785   | 663.0 ms    |
| Hybrid RRF  | 0.864    | 0.791   | 19.7 ms     |

Hybrid RRF is the production default: 34x lower p95 latency than ELSER alone (19.7ms vs
663ms) while beating it on nDCG@10. See the README's retrieval ablation section for the full
interpretation.

## End-to-end agent runs (the only eval path allowed to call an LLM)

Source: `evals/results/e2e_eval.json`. Generated and human-curated subsets reported
separately per CLAUDE.md — never merged, since they carry different (and differently biased)
provenance. See the README's "eval set provenance" note for what "human-curated,
model-drafted, human-edited" means for the second row.

| subset              | n  | task success | tool-selection accuracy | mean tokens/run | p95 latency |
|----------------------|----|--------------|--------------------------|------------------|-------------|
| generated            | TBD | TBD          | TBD                      | TBD              | TBD         |
| human-curated        | TBD | TBD          | TBD                      | TBD              | TBD         |

_(Filled from a live run against the real ingested corpus — `evals.end_to_end.run_eval` — as
soon as it completes; this file is updated in the same commit as the numbers.)_

## What this would cost at paid rates

This project runs entirely on free tiers (Google AI Studio, Groq, local Ollama) — $0 at
current usage. For a rough sense of what the same token volume would cost on a paid tier: at
Gemini Flash's public per-token pricing (order of magnitude: fractions of a cent per 1K input
tokens, similarly small for output), a single ~750-1000 token run costs a small fraction of a
cent — call it **~$0.0005-0.001 per run** at Gemini 2.x/3.x Flash list pricing, orders of
magnitude below the $0.014/task figure the plan cites as a target ceiling. This is a rough
estimate from public list pricing, not a number read from a results file, and is presented as
such — everything above it in this document is measured, not estimated.

## Latency budget in context

`p95_latency_ms` in the retrieval table is Elasticsearch query time only. The end-to-end
`p95 latency` in the agent table above includes real network round trips to whichever LLM
provider served each call (Gemini or Groq, live, not mocked) plus every MCP tool call's
Elasticsearch round trip — it is not comparable to the retrieval-only numbers and shouldn't be
read as "the agent's retrieval step is slow." Two individually observed full runs (not part of
the aggregate table, cited for texture): a `--reject` run took 119.7s and an `--approve` run
took 64.4s, both including two live LLM calls and four MCP tool calls each.
