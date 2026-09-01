# Agent Builder comparison

**Correction from an earlier draft of this file:** the build plan assumed Agent Builder would
require spending part of a separate Elastic Cloud trial to access. That assumption was checked
against the actual local stack rather than taken on faith (`GET /api/features` on this
project's own Kibana instance shows `agentBuilder` present) — **Agent Builder is available
locally**, under the same self-generated trial license `docker-compose.yml` already sets, no
separate cloud account needed. The zero-cost constraint holds; this comparison did not need to
be cut after all.

## What was built

Using only `curl` against `/api/agent_builder/tools` and `/api/actions/connector` (no UI
clicking — same "build it against the API" discipline as session 12's dashboard):

1. Created a custom `index_search` tool, `ops_copilot_search_runbooks`, pointed at
   `ops-runbooks` — declarative, four REST calls total (the first three were iterative
   discovery of the required config shape via validation error messages, since no local
   schema/OpenAPI doc was available to read first).
2. Tried to execute it: `"No connector available"`. Agent Builder's `index_search` tool
   translates a natural-language query into a search internally, which needs an LLM connector
   configured *in Kibana's own connector system* — a completely separate credential/config
   surface from this project's `agent/llm_router.py` and `.env`, even though both are ultimately
   pointed at the same free-tier providers.
3. Tried wiring Kibana's native `.gemini` connector to the same Google account this project's
   `GOOGLE_API_KEY` uses: it wants **Vertex AI** credentials (`apiUrl`, `gcpRegion`,
   `gcpProjectID` — a GCP project + service account), not a plain AI Studio API key. Heavier
   setup than this project's zero-cost path, which deliberately uses AI Studio specifically to
   avoid needing a GCP project.
4. Wired Kibana's OpenAI-compatible `.gen-ai` connector to Groq instead (Groq exposes an
   OpenAI-compatible endpoint) using the same `GROQ_API_KEY` already in `.env` — this worked to
   create the connector, but Kibana flags `.gen-ai` as `is_connector_type_deprecated: true` in
   this version, and calling the tool through it failed with `timeout [10s] waiting for
   inference result`. The same Groq key answered a direct SDK call in under a second earlier in
   this session (see `agent/providers.py`'s live verification in `PROGRESS.md`), so this reads
   as friction in the deprecated connector's integration with Agent Builder specifically, not
   Groq being slow.
5. Found the actual modern path: a `.inference` "AI Connector" type that wraps an Elasticsearch
   Inference API endpoint (`provider`/`taskType`/`inferenceId`) — the same `_inference` API
   family `scripts/deploy_elser.py` already uses for ELSER, just for chat completion instead of
   sparse embedding. This is almost certainly the intended, first-class route, but building the
   ES-side inference endpoint it needs is a further step this build's time budget didn't reach.

## What was faster

Declaring a search tool over an existing index (step 1) is genuinely faster than
`mcp_server/tools/runbooks.py`'s ~40 lines of hybrid RRF Python — four `curl` calls versus a
Python module with tests. If the tool's NL-to-query translation just worked out of the box,
that would be a real point in Agent Builder's favor for a simple "search this index" tool.

## What control was lost (or at least, made harder to get)

The credential/connector layer is not the same one this project's own code uses, so "swap the
provider, not the architecture" (this project's actual design property — see
`docs/security.md`) does not carry over: getting Agent Builder onto a genuinely free-tier
provider took discovering three connector types, one dead end (Vertex AI's heavier auth), and
one that connects but doesn't reliably complete a call in this Kibana version. `agent/nodes.py`'s
`approve()` `interrupt()` — the human-in-the-loop gate this project's whole pitch leans on — has
no attempted equivalent here yet; whether Agent Builder's workflow model has one is still an
open question, not one this build reached before time ran out on the rest of the plan (the
ablation table, the working agent, the observability loop, and the README carry the actual
interview value per the plan's own priority ordering).
