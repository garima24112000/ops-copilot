.PHONY: setup up down ingest demo evals lint retrieval-eval e2e-eval import-dashboards export-dashboards deploy-elser

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -e ".[dev]"

up:
	docker compose up -d
	bash scripts/wait_for_stack.sh

down:
	docker compose down

deploy-elser:
	.venv/bin/python scripts/deploy_elser.py

ingest:
	.venv/bin/python ingest/load.py

demo:
	.venv/bin/python cli.py data/sample_alert.json

evals: retrieval-eval e2e-eval

retrieval-eval:
	.venv/bin/python evals/retrieval/run_ablation.py

e2e-eval:
	.venv/bin/python evals/end_to_end/run_eval.py

export-dashboards:
	.venv/bin/python scripts/export_dashboards.py

import-dashboards:
	.venv/bin/python scripts/import_dashboards.py

lint:
	.venv/bin/ruff check .
	.venv/bin/mypy .
