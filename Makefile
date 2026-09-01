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
	.venv/bin/python -m scripts.deploy_elser

ingest:
	.venv/bin/python -m ingest.load

demo:
	.venv/bin/python cli.py data/sample_alert.json

evals: retrieval-eval e2e-eval

retrieval-eval:
	.venv/bin/python -m evals.retrieval.run_ablation

e2e-eval:
	.venv/bin/python -m evals.end_to_end.run_eval

export-dashboards:
	.venv/bin/python -m scripts.export_dashboards

import-dashboards:
	.venv/bin/python -m scripts.import_dashboards

lint:
	.venv/bin/ruff check .
	.venv/bin/mypy .
