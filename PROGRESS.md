# Ops Copilot — Progress Tracker

Autonomous build run. Sessions 0-17 per `claude-code-runbook.md`. Updated after every session.
If context is lost, re-read this file and resume from the first non-DONE session.

Environment at start: Docker daemon running, Ollama installed with `qwen3:4b` pulled,
16GB RAM / 8 CPU / 237GB free disk, Python 3.13.4, git remote set to
`github.com/garima24112000/ops-copilot`. `.env` present but not read directly (rule 8) —
router code reads `os.environ` via `python-dotenv` at runtime.

## Checklist

- [x] Session 0 — Scaffold and CLAUDE.md
- [x] Session 1 — Docker stack
- [ ] Session 2 — ELSER
- [ ] Session 3 — Corpus fetch
- [ ] Session 4 — Alert reverse-generation
- [ ] Session 5 — Index and ingest
- [ ] Session 6 — Retrieval check (automated, per adaptation)
- [ ] Session 7 — Retrieval ablation
- [ ] Session 8 — LLM router
- [ ] Session 9 — MCP server
- [ ] Session 10 — The agent (LangGraph)
- [ ] Session 11 — OTel instrumentation
- [ ] Session 12 — Kibana dashboard (saved objects)
- [ ] Session 13 — Document-level security
- [ ] Session 14 — End-to-end evals
- [ ] Session 15 — CI
- [ ] Session 16 — Terraform
- [ ] Session 17 — Packaging (README, docs, demo script)

---

## Session log

### Session 0 — Scaffold and CLAUDE.md — DONE
CLAUDE.md, .gitignore, .env.example, pyproject.toml, Makefile were already present at repo root
(pre-existing scaffold). Added repo directory skeleton (agent/, mcp_server/, ingest/, scripts/,
evals/{retrieval,end_to_end,results}, fixtures/, dashboards/, infra/, docs/, security/, tests/)
and a README.md stub (needed so `pip install -e .` could resolve `readme = "README.md"`).
Fixed `.gitignore` to stop excluding `data/*.jsonl` — the plan requires committing the corpus
for reproducibility. Added `[tool.hatch.build.targets.wheel] packages = ["agent","mcp_server","security"]`
so the editable install has something to package. Bumped `elasticsearch` client pin from `>=8.15,<9`
to `>=9.0,<10` to match the ES 9.x server this project runs against.
`make setup` runs clean: venv created, all deps installed (elasticsearch, langgraph, fastmcp,
sentence-transformers, google-genai, groq, ollama, opentelemetry-*, etc.)

### Session 1 — Docker stack — DONE
Wrote `docker-compose.yml`: Elasticsearch 9.5.2 (single-node, 4GB heap, security on,
`xpack.license.self_generated.type=trial`, ML enabled), Kibana 9.5.2, APM Server 9.5.2 with
OTLP-capable intake (anonymous auth enabled for otlp agent), all pinned to the current latest
9.x release (verified via GitHub tags before writing the compose file, per rule 10's spirit).

Two real bugs caught and fixed during the actual `docker compose up`, not assumed:
1. APM Server's image entrypoint already invokes the `apm-server` binary, so a `command:`
   starting with `apm-server ...` caused "unknown command apm-server for apm-server". Fixed by
   dropping the redundant binary name from `command:`.
2. Kibana cannot authenticate as the `elastic` superuser ("this is a superuser account that
   cannot write to system indices"). Added a one-shot `setup-kibana-user` init service
   (curlimages/curl) that sets the `kibana_system` user's password via the ES security API
   once Elasticsearch is healthy; Kibana now depends on that service completing successfully
   and authenticates as `kibana_system`. Password defaults inline in compose
   (`${KIBANA_SYSTEM_PASSWORD:-kibana_system_local_dev_pw}`) so nothing needed touching `.env`.

**Gate (session 1, run for real):**
```
curl -u elastic:$PASS localhost:9200      -> 200, cluster_name "ops-copilot", version 9.5.2
curl localhost:5601/api/status            -> level "available"
curl localhost:8200/                      -> {"publish_ready": true, "version": "9.5.2"}
```
All three true. Trial license active, `type: trial`, expires 2026-10-01 (30 days from issue) —
noted so nothing important is left un-exported when it lapses (see plan §12).

