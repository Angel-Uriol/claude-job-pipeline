# Convenience targets. Each one shells to the CLI so behavior matches
# whatever `job-pipeline <cmd>` does — no duplicated logic.
#
# Needs uv (https://docs.astral.sh/uv/) and Node 20+. Without uv, activate
# a venv, `pip install -e ".[dev]"`, and call `python -m job_pipeline.cli`.

PY := uv run python
CLI := $(PY) -m job_pipeline.cli

.PHONY: help install dev auth setup doctor scrape jsearch pre-filter deep-fit \
        digest tracker handoff daily backend frontend test lint type check clean

help:
	@echo "First run:"
	@echo "  install     Python + Node dependencies"
	@echo "  dev         start the web UI (backend :8765 + frontend :8766)"
	@echo "  auth        connect Claude from the terminal (API key or Claude Code login)"
	@echo "  setup       questionnaire from the terminal (same as the web Setup page)"
	@echo "  doctor      check that everything is wired up"
	@echo ""
	@echo "Pipeline:"
	@echo "  daily       scrape → pre-filter → deep-fit → digest → tracker"
	@echo "  scrape | jsearch | pre-filter | deep-fit | digest | tracker | handoff"
	@echo ""
	@echo "Dev:"
	@echo "  check       lint + type + test"

install:
	uv sync --all-extras
	cd job_pipeline/resume/node && npm ci
	cd ui && npm ci

# Runs both servers; Ctrl-C stops both.
dev:
	@echo "Backend  → http://127.0.0.1:8765"
	@echo "Frontend → http://localhost:8766   (open this one)"
	@trap 'kill 0' INT TERM; \
	  $(CLI) serve & \
	  (cd ui && npm run dev) & \
	  wait

backend:
	$(CLI) serve

frontend:
	cd ui && npm run dev

auth:
	$(CLI) auth

setup:
	$(CLI) setup

doctor:
	$(CLI) doctor

scrape jsearch pre-filter deep-fit digest tracker handoff daily:
	$(CLI) $@

test:
	uv run pytest -q

lint:
	uv run ruff check .

type:
	uv run mypy

check: lint type test

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
